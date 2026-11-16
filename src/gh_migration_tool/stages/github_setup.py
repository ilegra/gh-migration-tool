"""Step 3: Built-in GitHub Setup implementations."""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any

import requests

from gh_migration_tool.interfaces import GitHubSetup, WaveContext
from gh_migration_tool.models import MigrationUnit, SetupResult

logger = logging.getLogger(__name__)


def parse_github_owner_repo(destination_url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub HTTPS or SSH repository URL."""
    clean = destination_url.strip().rstrip("/")
    if clean.endswith(".git"):
        clean = clean[:-4]

    if clean.startswith("git@"):
        # git@github.com:org/repo
        path_part = clean.split(":", 1)[1]
    else:
        parsed = urllib.parse.urlparse(clean)
        path_part = parsed.path.lstrip("/")

    parts = [p for p in path_part.split("/") if p]
    if len(parts) < 2:
        raise ValueError(
            f"Cannot parse GitHub owner/repo from destination_url '{destination_url}'."
        )
    return parts[-2], parts[-1]


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


class VerifyRepoExistsGitHubSetup(GitHubSetup):
    """Verifies that the target GitHub repository already exists (e.g. via IaC)."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()

    def setup_unit(self, unit: MigrationUnit, context: WaveContext) -> SetupResult:
        """Verify destination repository existence on GitHub."""
        if context.github_token_provider is None:
            return SetupResult(
                unit_name=unit.name,
                passed=False,
                message="GitHubAppTokenProvider is not configured.",
            )

        owner, repo = parse_github_owner_repo(unit.destination_url)
        token = context.github_token_provider.get_token()
        api_url = context.settings.github_api_url.rstrip("/")
        url = f"{api_url}/repos/{owner}/{repo}"

        response = self._session.get(url, headers=_github_headers(token), timeout=30)
        if response.status_code == 200:
            return SetupResult(
                unit_name=unit.name,
                passed=True,
                message=f"Verified repository '{owner}/{repo}' exists.",
                created=False,
            )

        return SetupResult(
            unit_name=unit.name,
            passed=False,
            message=(
                f"Repository '{owner}/{repo}' verification failed: "
                f"HTTP {response.status_code}."
            ),
        )


class GitHubApiSetup(GitHubSetup):
    """Idempotently creates/reuses a GitHub repo and reconciles teams and rulesets."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()

    def setup_unit(self, unit: MigrationUnit, context: WaveContext) -> SetupResult:
        """Ensure GitHub repository exists, then apply teams and rulesets."""
        if context.github_token_provider is None:
            return SetupResult(
                unit_name=unit.name,
                passed=False,
                message="GitHubAppTokenProvider is not configured.",
            )

        owner, repo = parse_github_owner_repo(unit.destination_url)
        token = context.github_token_provider.get_token()
        api_url = context.settings.github_api_url.rstrip("/")
        headers = _github_headers(token)

        created = False
        repo_url = f"{api_url}/repos/{owner}/{repo}"
        get_resp = self._session.get(repo_url, headers=headers, timeout=30)

        if get_resp.status_code == 404:
            create_url = f"{api_url}/orgs/{owner}/repos"
            payload: dict[str, Any] = {
                "name": repo,
                "visibility": unit.visibility,
                "private": unit.visibility != "public",
            }
            create_resp = self._session.post(
                create_url, headers=headers, json=payload, timeout=30
            )
            if create_resp.status_code in (200, 201):
                created = True
            elif create_resp.status_code == 422:
                # Race condition: repository was created concurrently
                created = False
            else:
                return SetupResult(
                    unit_name=unit.name,
                    passed=False,
                    message=(
                        f"Failed to create repository '{owner}/{repo}': "
                        f"HTTP {create_resp.status_code}."
                    ),
                )
        elif get_resp.status_code != 200:
            return SetupResult(
                unit_name=unit.name,
                passed=False,
                message=(
                    f"Failed to inspect repository '{owner}/{repo}': "
                    f"HTTP {get_resp.status_code}."
                ),
            )

        for team_slug, permission in unit.teams.items():
            team_url = f"{api_url}/orgs/{owner}/teams/{team_slug}/repos/{owner}/{repo}"
            team_resp = self._session.put(
                team_url,
                headers=headers,
                json={"permission": permission},
                timeout=30,
            )
            if team_resp.status_code not in (200, 201, 204):
                return SetupResult(
                    unit_name=unit.name,
                    passed=False,
                    created=created,
                    message=(
                        f"Failed to assign team '{team_slug}' to '{owner}/{repo}': "
                        f"HTTP {team_resp.status_code}."
                    ),
                )

        if unit.rulesets:
            existing_rulesets = self._list_rulesets(api_url, owner, repo, headers)
            for ruleset in unit.rulesets:
                ok, err = self._reconcile_ruleset(
                    api_url, owner, repo, headers, ruleset, existing_rulesets
                )
                if not ok:
                    return SetupResult(
                        unit_name=unit.name,
                        passed=False,
                        created=created,
                        message=err,
                    )

        return SetupResult(
            unit_name=unit.name,
            passed=True,
            created=created,
            message=f"GitHub setup completed for '{owner}/{repo}'.",
        )

    def _list_rulesets(
        self, api_url: str, owner: str, repo: str, headers: dict[str, str]
    ) -> dict[str, int]:
        url = f"{api_url}/repos/{owner}/{repo}/rulesets"
        resp = self._session.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return {}
        items = resp.json()
        if not isinstance(items, list):
            return {}
        return {
            str(item["name"]): int(item["id"])
            for item in items
            if isinstance(item, dict) and "name" in item and "id" in item
        }

    def _reconcile_ruleset(
        self,
        api_url: str,
        owner: str,
        repo: str,
        headers: dict[str, str],
        ruleset: dict[str, Any],
        existing: dict[str, int],
    ) -> tuple[bool, str]:
        name = str(ruleset.get("name", "")).strip()
        if name and name in existing:
            ruleset_id = existing[name]
            url = f"{api_url}/repos/{owner}/{repo}/rulesets/{ruleset_id}"
            resp = self._session.put(url, headers=headers, json=ruleset, timeout=30)
        else:
            url = f"{api_url}/repos/{owner}/{repo}/rulesets"
            resp = self._session.post(url, headers=headers, json=ruleset, timeout=30)

        if resp.status_code in (200, 201):
            return True, ""
        return (
            False,
            f"Failed to reconcile ruleset '{name}' on '{owner}/{repo}': "
            f"HTTP {resp.status_code}.",
        )
