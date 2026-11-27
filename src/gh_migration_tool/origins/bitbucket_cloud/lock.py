"""Bitbucket Cloud reversible origin repository lock."""

from __future__ import annotations

import logging
import urllib.parse
from collections.abc import Iterator
from typing import Any

import requests

from gh_migration_tool.contracts import (
    MigrationUnit,
    OriginLock,
    OriginLockResult,
    WaveContext,
)
from gh_migration_tool.engine._http import build_http_session

logger = logging.getLogger(__name__)

BITBUCKET_API_BASE = "https://api.bitbucket.org/2.0"
PUSH_RESTRICTION_KIND = "push"
PUSH_RESTRICTION_BRANCH_MATCH_KIND = "glob"
PUSH_RESTRICTION_PATTERN = "*"


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
    repo_slug = parts[1].removesuffix(".git")
    if not workspace or not repo_slug:
        raise ValueError(
            f"Bitbucket Cloud URL '{origin_url}' has empty workspace or repo slug."
        )
    return workspace, repo_slug


class BitbucketCloudOriginLock(OriginLock):
    """Reversibly locks and unlocks a Bitbucket Cloud repository via branch rules.

    Locking adds a global ``push`` branch restriction (``pattern: "*"`` with no
    exempted users or groups) so developers cannot push while preserving all
    original user and group permissions. Unlocking deletes that matching global
    ``push`` restriction so teams can resume working in Bitbucket Cloud.
    """

    @property
    def name(self) -> str:
        """Return the identifier for this origin lock."""
        return "bitbucket_cloud_origin_lock"

    def lock(self, unit: MigrationUnit, context: WaveContext) -> OriginLockResult:
        """Lock the Bitbucket Cloud origin repository against pushes."""
        prepared = self._prepare_request(unit, context, operation="lock")
        if isinstance(prepared, OriginLockResult):
            return prepared
        session, repo_api, workspace, repo_slug = prepared

        details: dict[str, Any] = {
            "workspace": workspace,
            "repo_slug": repo_slug,
            "push_restriction_added": False,
        }
        try:
            with session:
                added, already_locked = self._ensure_push_restriction(session, repo_api)
            details["push_restriction_added"] = added
        except requests.HTTPError as exc:
            return self._handle_http_error(unit, exc, details, operation="lock")
        except requests.RequestException as exc:
            return self._handle_request_error(unit, exc, details, operation="lock")

        logger.info(
            "Locked Bitbucket Cloud origin '%s/%s' for unit '%s' (already_locked=%s)",
            workspace,
            repo_slug,
            unit.name,
            already_locked,
        )
        return OriginLockResult(
            unit_name=unit.name,
            passed=True,
            lock_name=self.name,
            already_in_target_state=already_locked,
            message=(
                "Bitbucket Cloud origin repository was already locked."
                if already_locked
                else "Bitbucket Cloud origin repository locked."
            ),
            details=details,
        )

    def unlock(self, unit: MigrationUnit, context: WaveContext) -> OriginLockResult:
        """Unlock the Bitbucket Cloud origin repository by removing the push lock."""
        prepared = self._prepare_request(unit, context, operation="unlock")
        if isinstance(prepared, OriginLockResult):
            return prepared
        session, repo_api, workspace, repo_slug = prepared

        details: dict[str, Any] = {
            "workspace": workspace,
            "repo_slug": repo_slug,
            "push_restrictions_removed": 0,
        }
        try:
            with session:
                removed_count, already_unlocked = self._remove_push_restrictions(
                    session, repo_api
                )
            details["push_restrictions_removed"] = removed_count
        except requests.HTTPError as exc:
            return self._handle_http_error(unit, exc, details, operation="unlock")
        except requests.RequestException as exc:
            return self._handle_request_error(unit, exc, details, operation="unlock")

        logger.info(
            "Unlocked Bitbucket Cloud origin '%s/%s' for unit '%s' "
            "(removed=%d, already_unlocked=%s)",
            workspace,
            repo_slug,
            unit.name,
            removed_count,
            already_unlocked,
        )
        return OriginLockResult(
            unit_name=unit.name,
            passed=True,
            lock_name=self.name,
            already_in_target_state=already_unlocked,
            message=(
                "Bitbucket Cloud origin repository was already unlocked."
                if already_unlocked
                else "Bitbucket Cloud origin repository unlocked."
            ),
            details=details,
        )

    def _prepare_request(
        self,
        unit: MigrationUnit,
        context: WaveContext,
        *,
        operation: str,
    ) -> tuple[requests.Session, str, str, str] | OriginLockResult:
        token = context.settings.origin_token.strip()
        if not token:
            return OriginLockResult(
                unit_name=unit.name,
                passed=False,
                lock_name=self.name,
                message=f"ORIGIN_TOKEN is required for Bitbucket Cloud {operation}.",
            )

        try:
            workspace, repo_slug = _parse_bitbucket_cloud_repo(unit.origin_url)
        except ValueError as exc:
            return OriginLockResult(
                unit_name=unit.name,
                passed=False,
                lock_name=self.name,
                message=str(exc),
            )

        session = build_http_session(
            timeout=context.settings.http_timeout_seconds,
            max_retries=context.settings.http_max_retries,
            backoff_factor=context.settings.http_backoff_factor,
        )
        session.headers["Accept"] = "application/json"
        if context.settings.origin_username:
            session.auth = (context.settings.origin_username, token)
        else:
            session.headers["Authorization"] = f"Bearer {token}"

        repo_api = (
            f"{BITBUCKET_API_BASE}/repositories/"
            f"{urllib.parse.quote(workspace, safe='')}/"
            f"{urllib.parse.quote(repo_slug, safe='')}"
        )
        return session, repo_api, workspace, repo_slug

    def _handle_http_error(
        self,
        unit: MigrationUnit,
        exc: requests.HTTPError,
        details: dict[str, Any],
        *,
        operation: str,
    ) -> OriginLockResult:
        status_code = exc.response.status_code if exc.response is not None else "?"
        logger.error(
            "Bitbucket Cloud %s failed for '%s' (HTTP %s)",
            operation,
            unit.name,
            status_code,
        )
        return OriginLockResult(
            unit_name=unit.name,
            passed=False,
            lock_name=self.name,
            message=f"Bitbucket Cloud API returned HTTP {status_code}.",
            details=details,
        )

    def _handle_request_error(
        self,
        unit: MigrationUnit,
        exc: requests.RequestException,
        details: dict[str, Any],
        *,
        operation: str,
    ) -> OriginLockResult:
        logger.error(
            "Bitbucket Cloud %s request failed for '%s': %s",
            operation,
            unit.name,
            exc,
        )
        return OriginLockResult(
            unit_name=unit.name,
            passed=False,
            lock_name=self.name,
            message=f"Bitbucket Cloud API request error: {exc}",
            details=details,
        )

    @staticmethod
    def _is_global_push_restriction(rule: dict[str, Any]) -> bool:
        return (
            rule.get("kind") == PUSH_RESTRICTION_KIND
            and rule.get("pattern") == PUSH_RESTRICTION_PATTERN
            and not rule.get("users")
            and not rule.get("groups")
        )

    @staticmethod
    def _iter_paginated(
        session: requests.Session,
        url: str,
    ) -> Iterator[dict[str, Any]]:
        next_url: str | None = url
        while next_url:
            resp = session.get(next_url)
            resp.raise_for_status()
            payload = resp.json()
            yield from payload.get("values", [])
            next_url = payload.get("next")

    def _ensure_push_restriction(
        self,
        session: requests.Session,
        repo_api: str,
    ) -> tuple[bool, bool]:
        restrictions_url = f"{repo_api}/branch-restrictions"
        existing = self._iter_paginated(session, restrictions_url)
        if any(self._is_global_push_restriction(rule) for rule in existing):
            return False, True

        resp = session.post(
            restrictions_url,
            json={
                "kind": PUSH_RESTRICTION_KIND,
                "branch_match_kind": PUSH_RESTRICTION_BRANCH_MATCH_KIND,
                "pattern": PUSH_RESTRICTION_PATTERN,
            },
        )
        resp.raise_for_status()
        return True, False

    def _remove_push_restrictions(
        self,
        session: requests.Session,
        repo_api: str,
    ) -> tuple[int, bool]:
        restrictions_url = f"{repo_api}/branch-restrictions"
        matching_ids = [
            rule["id"]
            for rule in self._iter_paginated(session, restrictions_url)
            if self._is_global_push_restriction(rule) and rule.get("id") is not None
        ]
        if not matching_ids:
            return 0, True

        for rule_id in matching_ids:
            delete_url = (
                f"{restrictions_url}/{urllib.parse.quote(str(rule_id), safe='')}"
            )
            resp = session.delete(delete_url)
            resp.raise_for_status()

        return len(matching_ids), False
