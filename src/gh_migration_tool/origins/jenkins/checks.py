"""Jenkins Pre-check and Post-check implementations."""

from __future__ import annotations

import base64
import logging
import time
import urllib.parse
from collections.abc import Callable

import requests

from gh_migration_tool.contracts import (
    CheckResult,
    MigrationStageResult,
    MigrationUnit,
    PostCheck,
    PreCheck,
    WaveContext,
)
from gh_migration_tool.engine._http import build_http_session

logger = logging.getLogger(__name__)


def build_jenkins_headers(username: str | None, token: str) -> dict[str, str]:
    """Build Jenkins Authorization headers using Basic or Bearer credentials."""
    if username and token:
        encoded = base64.b64encode(f"{username}:{token}".encode()).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def format_jenkins_job_path(job_name: str) -> str:
    """Convert a slash-delimited Jenkins job name into a Jenkins API job path."""
    parts = [p.strip() for p in job_name.strip("/").split("/") if p.strip()]
    return "/".join(f"job/{urllib.parse.quote(part)}" for part in parts)


class JenkinsJobPreCheck(PreCheck):
    """Verifies that a Migration Unit's latest Jenkins build succeeded."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or build_http_session(allowed_methods={"GET"})

    @property
    def name(self) -> str:
        """Human-readable identifier for this Pre-check."""
        return "jenkins_job_pre_check"

    def execute(self, unit: MigrationUnit, context: WaveContext) -> CheckResult:
        """Check the latest Jenkins build status for unit.jenkins_job."""
        if not unit.jenkins_job or not unit.jenkins_job.strip():
            return CheckResult(
                check_name=self.name,
                passed=True,
                message="No jenkins_job configured; skipping Jenkins pre-check.",
            )

        jenkins_url = context.settings.jenkins_url.strip().rstrip("/")
        if not jenkins_url:
            return CheckResult(
                check_name=self.name,
                passed=False,
                message="JENKINS_URL is not configured.",
            )

        job_path = format_jenkins_job_path(unit.jenkins_job)
        api_url = f"{jenkins_url}/{job_path}/lastBuild/api/json"
        headers = build_jenkins_headers(
            context.settings.jenkins_username, context.settings.jenkins_token
        )

        try:
            response = self._session.get(
                api_url,
                headers=headers,
                timeout=context.settings.http_timeout_seconds,
            )
        except requests.RequestException as exc:
            logger.error(
                "Jenkins pre-check request failed for unit '%s': %s", unit.name, exc
            )
            return CheckResult(
                check_name=self.name,
                passed=False,
                message=f"Jenkins request failed: {exc}",
            )

        if response.status_code != 200:
            return CheckResult(
                check_name=self.name,
                passed=False,
                message=f"Jenkins API returned HTTP {response.status_code}.",
                details={"status_code": response.status_code},
            )

        data = response.json()
        building = bool(data.get("building", False))
        result = data.get("result")

        if building:
            return CheckResult(
                check_name=self.name,
                passed=False,
                message=f"Jenkins job '{unit.jenkins_job}' is currently building.",
                details={"building": True, "result": result},
            )

        if result == "SUCCESS":
            return CheckResult(
                check_name=self.name,
                passed=True,
                message=f"Latest Jenkins build for '{unit.jenkins_job}' succeeded.",
                details={"result": result},
            )

        return CheckResult(
            check_name=self.name,
            passed=False,
            message=(
                f"Latest Jenkins build for '{unit.jenkins_job}' has status '{result}'."
            ),
            details={"result": result},
        )


class JenkinsTriggerPostCheck(PostCheck):
    """Triggers a Jenkins job and polls until completion."""

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

        http_timeout = context.settings.http_timeout_seconds
        headers = build_jenkins_headers(
            context.settings.jenkins_username, context.settings.jenkins_token
        )
        job_path = format_jenkins_job_path(unit.jenkins_job)
        trigger_url = f"{jenkins_url}/{job_path}/build"
        try:
            crumb_resp = self._session.get(
                f"{jenkins_url}/crumbIssuer/api/json",
                headers=headers,
                timeout=min(15.0, http_timeout),
            )
            if crumb_resp.status_code == 200:
                crumb_data = crumb_resp.json()
                field = crumb_data.get("crumbRequestField")
                crumb = crumb_data.get("crumb")
                if field and crumb:
                    headers[str(field)] = str(crumb)

            trigger_resp = self._session.post(
                trigger_url, headers=headers, timeout=http_timeout
            )
        except requests.RequestException as exc:
            logger.error(
                "Jenkins trigger request failed for unit '%s': %s", unit.name, exc
            )
            return CheckResult(
                check_name=self.name,
                passed=False,
                message=f"Failed to trigger Jenkins job '{unit.jenkins_job}': {exc}",
            )

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
            try:
                resp = self._session.get(
                    status_url, headers=headers, timeout=http_timeout
                )
            except requests.RequestException as exc:
                logger.warning(
                    "Jenkins poll request failed for unit '%s': %s", unit.name, exc
                )
                continue
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
