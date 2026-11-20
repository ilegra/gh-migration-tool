"""Step 5.5: Built-in Origin Deactivation stage implementations."""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any

import requests

from gh_migration_tool._http import build_http_session
from gh_migration_tool.interfaces import OriginDeactivator, WaveContext
from gh_migration_tool.models import MigrationUnit, OriginDeactivationResult

logger = logging.getLogger(__name__)

_BITBUCKET_API_BASE = "https://api.bitbucket.org/2.0"


def _parse_bitbucket_cloud_repo(origin_url: str) -> tuple[str, str]:
    """Extract (workspace, repo_slug) from a Bitbucket Cloud repository URL."""
    parsed = urllib.parse.urlparse(origin_url.strip())
    hostname = (parsed.hostname or "").lower()
    if hostname != "bitbucket.org" and not hostname.endswith(".bitbucket.org"):
        raise ValueError(
            f"Origin URL '{origin_url}' is not a Bitbucket Cloud repository URL."
        )
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise ValueError(
            f"Bitbucket Cloud URL '{origin_url}' must include workspace and repo slug."
        )
    workspace = parts[0]
    repo_slug = parts[1]
    if repo_slug.endswith(".git"):
        repo_slug = repo_slug[:-4]
    if not workspace or not repo_slug:
        raise ValueError(
            f"Bitbucket Cloud URL '{origin_url}' has empty workspace or repo slug."
        )
    return workspace, repo_slug


class BitbucketCloudOriginDeactivator(OriginDeactivator):
    """Freezes a Bitbucket Cloud repository after migration.

    By default, adds a ``push`` branch restriction (``pattern: "*"``) so
    developers cannot push to any branch while preserving original user and
    group permissions for later analysis and easy rollback. Optionally,
    explicit repository permissions can also be downgraded to ``"read"``.
    """

    def __init__(
        self,
        *,
        add_push_restriction: bool = True,
        downgrade_permissions: bool = False,
    ) -> None:
        self._add_push_restriction = add_push_restriction
        self._downgrade_permissions = downgrade_permissions

    @property
    def name(self) -> str:
        """Return the identifier for this deactivator."""
        return "bitbucket_cloud_origin_deactivator"

    def deactivate(
        self, unit: MigrationUnit, context: WaveContext
    ) -> OriginDeactivationResult:
        """Freeze the Bitbucket Cloud origin repository for a verified unit."""
        token = context.settings.origin_token.strip()
        if not token:
            return OriginDeactivationResult(
                unit_name=unit.name,
                passed=False,
                deactivator_name=self.name,
                message="ORIGIN_TOKEN is required for Bitbucket Cloud deactivation.",
            )

        try:
            workspace, repo_slug = _parse_bitbucket_cloud_repo(unit.origin_url)
        except ValueError as exc:
            return OriginDeactivationResult(
                unit_name=unit.name,
                passed=False,
                deactivator_name=self.name,
                message=str(exc),
            )

        session = build_http_session(
            timeout=context.settings.http_timeout_seconds,
            max_retries=context.settings.http_max_retries,
            backoff_factor=context.settings.http_backoff_factor,
        )
        headers: dict[str, str] = {"Accept": "application/json"}
        auth: tuple[str, str] | None = None
        if context.settings.origin_username:
            auth = (context.settings.origin_username, token)
        else:
            headers["Authorization"] = f"Bearer {token}"

        repo_api = (
            f"{_BITBUCKET_API_BASE}/repositories/"
            f"{urllib.parse.quote(workspace, safe='')}/"
            f"{urllib.parse.quote(repo_slug, safe='')}"
        )
        details: dict[str, Any] = {
            "workspace": workspace,
            "repo_slug": repo_slug,
            "push_restriction_added": False,
            "downgraded_groups": 0,
            "downgraded_users": 0,
        }
        already_disabled = False

        try:
            if self._add_push_restriction:
                added, existed = self._ensure_push_restriction(
                    session, repo_api, headers, auth
                )
                details["push_restriction_added"] = added
                already_disabled = existed

            if self._downgrade_permissions:
                details["downgraded_groups"] = self._downgrade_group_permissions(
                    session, repo_api, headers, auth
                )
                details["downgraded_users"] = self._downgrade_user_permissions(
                    session, repo_api, headers, auth
                )
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else "?"
            logger.error(
                "Bitbucket Cloud deactivation failed for '%s' (HTTP %s)",
                unit.name,
                status_code,
            )
            return OriginDeactivationResult(
                unit_name=unit.name,
                passed=False,
                deactivator_name=self.name,
                message=f"Bitbucket Cloud API returned HTTP {status_code}.",
                details=details,
            )
        except requests.RequestException as exc:
            logger.error(
                "Bitbucket Cloud deactivation request failed for '%s': %s",
                unit.name,
                exc,
            )
            return OriginDeactivationResult(
                unit_name=unit.name,
                passed=False,
                deactivator_name=self.name,
                message=f"Bitbucket Cloud API request error: {exc}",
                details=details,
            )

        logger.info(
            "Deactivated Bitbucket Cloud origin '%s/%s' for unit '%s'",
            workspace,
            repo_slug,
            unit.name,
        )
        return OriginDeactivationResult(
            unit_name=unit.name,
            passed=True,
            deactivator_name=self.name,
            already_disabled=already_disabled,
            message="Bitbucket Cloud origin repository frozen.",
            details=details,
        )

    @staticmethod
    def _paginate(
        session: requests.Session,
        url: str,
        headers: dict[str, str],
        auth: tuple[str, str] | None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_url: str | None = url
        while next_url:
            resp = session.get(next_url, headers=headers, auth=auth)
            resp.raise_for_status()
            payload = resp.json()
            items.extend(payload.get("values", []))
            next_url = payload.get("next")
        return items

    def _ensure_push_restriction(
        self,
        session: requests.Session,
        repo_api: str,
        headers: dict[str, str],
        auth: tuple[str, str] | None,
    ) -> tuple[bool, bool]:
        restrictions_url = f"{repo_api}/branch-restrictions"
        existing = self._paginate(session, restrictions_url, headers, auth)
        for rule in existing:
            if (
                rule.get("kind") == "push"
                and rule.get("pattern") == "*"
                and not rule.get("users")
                and not rule.get("groups")
            ):
                return False, True

        resp = session.post(
            restrictions_url,
            headers={**headers, "Content-Type": "application/json"},
            auth=auth,
            json={"kind": "push", "branch_match_kind": "glob", "pattern": "*"},
        )
        resp.raise_for_status()
        return True, False

    def _downgrade_group_permissions(
        self,
        session: requests.Session,
        repo_api: str,
        headers: dict[str, str],
        auth: tuple[str, str] | None,
    ) -> int:
        groups_url = f"{repo_api}/permissions-config/groups"
        entries = self._paginate(session, groups_url, headers, auth)
        count = 0
        for entry in entries:
            if entry.get("permission") == "read":
                continue
            group_slug = entry.get("group", {}).get("slug", "")
            if not group_slug:
                continue
            put_url = f"{groups_url}/{urllib.parse.quote(group_slug, safe='')}"
            resp = session.put(
                put_url,
                headers={**headers, "Content-Type": "application/json"},
                auth=auth,
                json={"permission": "read"},
            )
            resp.raise_for_status()
            count += 1
        return count

    def _downgrade_user_permissions(
        self,
        session: requests.Session,
        repo_api: str,
        headers: dict[str, str],
        auth: tuple[str, str] | None,
    ) -> int:
        users_url = f"{repo_api}/permissions-config/users"
        entries = self._paginate(session, users_url, headers, auth)
        count = 0
        for entry in entries:
            if entry.get("permission") == "read":
                continue
            user_uuid = entry.get("user", {}).get("uuid", "")
            if not user_uuid:
                continue
            put_url = f"{users_url}/{urllib.parse.quote(user_uuid, safe='')}"
            resp = session.put(
                put_url,
                headers={**headers, "Content-Type": "application/json"},
                auth=auth,
                json={"permission": "read"},
            )
            resp.raise_for_status()
            count += 1
        return count
