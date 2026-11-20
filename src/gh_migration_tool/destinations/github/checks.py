"""GitHub Actions workflow dispatch Post-check."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import requests

from gh_migration_tool.contracts import (
    CheckResult,
    MigrationStageResult,
    MigrationUnit,
    PostCheck,
    WaveContext,
)
from gh_migration_tool.destinations.github.setup import resolve_github_target
from gh_migration_tool.engine._http import build_http_session

logger = logging.getLogger(__name__)


class GitHubActionsTriggerPostCheck(PostCheck):
    """Dispatches a GitHub Actions workflow and polls until completion."""

    def __init__(
        self,
        session: requests.Session | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._session = session or build_http_session()
        self._sleep = sleep_fn

    @property
    def name(self) -> str:
        """Human-readable identifier for this Post-check."""
        return "github_actions_trigger_post_check"

    def execute(
        self,
        unit: MigrationUnit,
        migration_result: MigrationStageResult,
        context: WaveContext,
    ) -> CheckResult:
        """Dispatch unit.github_workflow via GitHub API and poll for success."""
        if not unit.github_workflow or not unit.github_workflow.strip():
            return CheckResult(
                check_name=self.name,
                passed=True,
                message=(
                    "No github_workflow configured; skipping GitHub Actions post-check."
                ),
            )

        target = resolve_github_target(unit, context)
        if target is None:
            return CheckResult(
                check_name=self.name,
                passed=False,
                message="GitHubAppTokenProvider is not configured.",
            )

        owner, repo, api_url, headers = target
        http_timeout = context.settings.http_timeout_seconds

        ref = unit.branch_mapping.get("master", unit.branch_mapping.get("main", "main"))
        dispatch_url = (
            f"{api_url}/repos/{owner}/{repo}/actions/workflows/"
            f"{unit.github_workflow}/dispatches"
        )
        try:
            disp_resp = self._session.post(
                dispatch_url, headers=headers, json={"ref": ref}, timeout=http_timeout
            )
        except requests.RequestException as exc:
            logger.error(
                "GitHub workflow dispatch failed for unit '%s': %s", unit.name, exc
            )
            return CheckResult(
                check_name=self.name,
                passed=False,
                message=(
                    f"Failed to dispatch GitHub workflow "
                    f"'{unit.github_workflow}': {exc}"
                ),
            )

        if disp_resp.status_code not in (200, 201, 204):
            return CheckResult(
                check_name=self.name,
                passed=False,
                message=(
                    f"Failed to dispatch GitHub workflow '{unit.github_workflow}': "
                    f"HTTP {disp_resp.status_code}."
                ),
            )

        runs_url = (
            f"{api_url}/repos/{owner}/{repo}/actions/workflows/"
            f"{unit.github_workflow}/runs"
        )
        deadline = time.monotonic() + context.settings.ci_poll_timeout_seconds
        poll_interval = context.settings.ci_poll_interval_seconds

        while time.monotonic() < deadline:
            self._sleep(poll_interval)
            try:
                runs_resp = self._session.get(
                    runs_url,
                    headers=headers,
                    params={"per_page": 1},
                    timeout=http_timeout,
                )
            except requests.RequestException as exc:
                logger.warning(
                    "GitHub workflow poll failed for unit '%s': %s", unit.name, exc
                )
                continue
            if runs_resp.status_code != 200:
                continue
            runs = runs_resp.json().get("workflow_runs", [])
            if not runs:
                continue
            latest = runs[0]
            if latest.get("status") != "completed":
                continue
            conclusion = latest.get("conclusion")
            if conclusion == "success":
                return CheckResult(
                    check_name=self.name,
                    passed=True,
                    message=(
                        f"GitHub workflow '{unit.github_workflow}' "
                        "completed with success."
                    ),
                )
            return CheckResult(
                check_name=self.name,
                passed=False,
                message=(
                    f"GitHub workflow '{unit.github_workflow}' "
                    f"concluded with '{conclusion}'."
                ),
                details={"conclusion": conclusion},
            )

        return CheckResult(
            check_name=self.name,
            passed=False,
            message=f"Timed out waiting for GitHub workflow '{unit.github_workflow}'.",
        )
