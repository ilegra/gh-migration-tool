"""Step 5: Built-in Post-check implementations."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import requests

from gh_migration_tool._http import build_jenkins_headers, format_jenkins_job_path
from gh_migration_tool.interfaces import PostCheck, WaveContext
from gh_migration_tool.models import CheckResult, MigrationStageResult, MigrationUnit
from gh_migration_tool.stages.github_setup import resolve_github_target
from gh_migration_tool.stages.migration import capture_origin_branch_heads

logger = logging.getLogger(__name__)


class SourceDriftPostCheck(PostCheck):
    """Detects Source Drift by comparing live Origin heads against OriginSnapshot."""

    def __init__(
        self,
        ls_remote_fn: Callable[[str, str, str | None], dict[str, str]] | None = None,
    ) -> None:
        self._ls_remote = ls_remote_fn or capture_origin_branch_heads

    @property
    def name(self) -> str:
        """Human-readable identifier for this Post-check."""
        return "source_drift_post_check"

    def execute(
        self,
        unit: MigrationUnit,
        migration_result: MigrationStageResult,
        context: WaveContext,
    ) -> CheckResult:
        """Verify no migrated branch advanced and no new branch appeared on Origin."""
        snapshot = migration_result.origin_snapshot
        if snapshot is None:
            return CheckResult(
                check_name=self.name,
                passed=False,
                message="OriginSnapshot is missing; cannot verify source drift.",
            )

        try:
            live_heads = self._ls_remote(
                unit.origin_url,
                context.settings.origin_token,
                context.settings.origin_username,
            )
        except Exception as exc:
            return CheckResult(
                check_name=self.name,
                passed=False,
                message=f"Failed to query live origin refs: {exc}",
            )

        advanced_branches: list[str] = []
        for branch, snap_sha in snapshot.branch_heads.items():
            live_sha = live_heads.get(branch)
            if live_sha is not None and live_sha != snap_sha:
                advanced_branches.append(branch)

        new_branches = sorted(b for b in live_heads if b not in snapshot.branch_heads)

        if advanced_branches or new_branches:
            return CheckResult(
                check_name=self.name,
                passed=False,
                message=(
                    f"Source drift detected: advanced_branches={advanced_branches}, "
                    f"new_branches={new_branches}."
                ),
                details={
                    "advanced_branches": advanced_branches,
                    "new_branches": new_branches,
                },
            )

        return CheckResult(
            check_name=self.name,
            passed=True,
            message="No source drift detected on Origin.",
        )


class JenkinsTriggerPostCheck(PostCheck):
    """Triggers a Jenkins job and polls until completion."""

    def __init__(
        self,
        session: requests.Session | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._session = session or requests.Session()
        self._sleep = sleep_fn

    @property
    def name(self) -> str:
        """Human-readable identifier for this Post-check."""
        return "jenkins_trigger_post_check"

    def execute(
        self,
        unit: MigrationUnit,
        migration_result: MigrationStageResult,
        context: WaveContext,
    ) -> CheckResult:
        """Trigger unit.jenkins_job on Jenkins and poll for SUCCESS."""
        if not unit.jenkins_job or not unit.jenkins_job.strip():
            return CheckResult(
                check_name=self.name,
                passed=True,
                message="No jenkins_job configured; skipping Jenkins post-check.",
            )

        jenkins_url = context.settings.jenkins_url.strip().rstrip("/")
        if not jenkins_url:
            return CheckResult(
                check_name=self.name,
                passed=False,
                message="JENKINS_URL is not configured.",
            )

        headers = build_jenkins_headers(
            context.settings.jenkins_username, context.settings.jenkins_token
        )
        crumb_resp = self._session.get(
            f"{jenkins_url}/crumbIssuer/api/json", headers=headers, timeout=15
        )
        if crumb_resp.status_code == 200:
            crumb_data = crumb_resp.json()
            field = crumb_data.get("crumbRequestField")
            crumb = crumb_data.get("crumb")
            if field and crumb:
                headers[str(field)] = str(crumb)

        job_path = format_jenkins_job_path(unit.jenkins_job)
        trigger_url = f"{jenkins_url}/{job_path}/build"
        trigger_resp = self._session.post(trigger_url, headers=headers, timeout=30)
        if trigger_resp.status_code not in (200, 201, 202):
            return CheckResult(
                check_name=self.name,
                passed=False,
                message=(
                    f"Failed to trigger Jenkins job '{unit.jenkins_job}': "
                    f"HTTP {trigger_resp.status_code}."
                ),
            )

        deadline = time.monotonic() + context.settings.ci_poll_timeout_seconds
        poll_interval = context.settings.ci_poll_interval_seconds
        status_url = f"{jenkins_url}/{job_path}/lastBuild/api/json"

        while time.monotonic() < deadline:
            self._sleep(poll_interval)
            resp = self._session.get(status_url, headers=headers, timeout=30)
            if resp.status_code != 200:
                continue
            data = resp.json()
            if data.get("building"):
                continue
            result = data.get("result")
            if result == "SUCCESS":
                return CheckResult(
                    check_name=self.name,
                    passed=True,
                    message=f"Triggered Jenkins job '{unit.jenkins_job}' succeeded.",
                )
            return CheckResult(
                check_name=self.name,
                passed=False,
                message=(
                    f"Triggered Jenkins job '{unit.jenkins_job}' "
                    f"finished with '{result}'."
                ),
                details={"result": result},
            )

        return CheckResult(
            check_name=self.name,
            passed=False,
            message=f"Timed out waiting for Jenkins job '{unit.jenkins_job}'.",
        )


class GitHubActionsTriggerPostCheck(PostCheck):
    """Dispatches a GitHub Actions workflow and polls until completion."""

    def __init__(
        self,
        session: requests.Session | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._session = session or requests.Session()
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

        ref = unit.branch_mapping.get("master", unit.branch_mapping.get("main", "main"))
        dispatch_url = (
            f"{api_url}/repos/{owner}/{repo}/actions/workflows/"
            f"{unit.github_workflow}/dispatches"
        )
        disp_resp = self._session.post(
            dispatch_url, headers=headers, json={"ref": ref}, timeout=30
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
            runs_resp = self._session.get(
                runs_url, headers=headers, params={"per_page": 1}, timeout=30
            )
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
