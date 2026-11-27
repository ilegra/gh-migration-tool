"""Jenkins Pre-check and Post-check implementations."""

from __future__ import annotations

import base64
import logging
import time
import urllib.parse
from collections.abc import Callable
from http import HTTPStatus
from typing import Any

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

JENKINS_COLOR_GREEN = "blue"
JENKINS_COLOR_DISABLED_PREFIX = "disabled"
JENKINS_COLOR_RUNNING_SUFFIX = "_anime"
JENKINS_BUILD_RESULT_SUCCESS = "SUCCESS"
JENKINS_TRIGGER_SUCCESS_STATUSES = (
    HTTPStatus.OK,
    HTTPStatus.CREATED,
    HTTPStatus.ACCEPTED,
)


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
    """Verifies that a Migration Unit's Jenkins job is enabled, idle, and green."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or build_http_session(allowed_methods={"GET"})

    @property
    def name(self) -> str:
        """Human-readable identifier for this Pre-check."""
        return "jenkins_job_pre_check"

    def _fetch_json(
        self,
        url: str,
        headers: dict[str, str],
        timeout: float,
        unit_name: str,
    ) -> tuple[dict[str, Any] | None, CheckResult | None]:
        try:
            response = self._session.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            logger.error(
                "Jenkins pre-check request failed for unit '%s': %s", unit_name, exc
            )
            return None, CheckResult(
                check_name=self.name,
                passed=False,
                message=f"Jenkins request failed: {exc}",
            )

        if response.status_code != HTTPStatus.OK:
            return None, CheckResult(
                check_name=self.name,
                passed=False,
                message=f"Jenkins API returned HTTP {response.status_code}.",
                details={"status_code": response.status_code},
            )

        payload: dict[str, Any] = response.json()
        return payload, None

    def execute(self, unit: MigrationUnit, context: WaveContext) -> CheckResult:
        """Check that unit.jenkins_job is enabled, not running/queued, and green."""
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
        headers = build_jenkins_headers(
            context.settings.jenkins_username, context.settings.jenkins_token
        )
        timeout = context.settings.http_timeout_seconds

        job_api_url = f"{jenkins_url}/{job_path}/api/json"
        job_data, error_result = self._fetch_json(
            job_api_url, headers, timeout, unit.name
        )
        if error_result is not None or job_data is None:
            return error_result or CheckResult(
                check_name=self.name,
                passed=False,
                message="Jenkins job metadata response was empty.",
            )

        buildable = job_data.get("buildable")
        in_queue = bool(job_data.get("inQueue", False))
        raw_color = job_data.get("color")
        color = raw_color if isinstance(raw_color, str) else ""

        if buildable is False or color.startswith(JENKINS_COLOR_DISABLED_PREFIX):
            return CheckResult(
                check_name=self.name,
                passed=False,
                message=f"Jenkins job '{unit.jenkins_job}' is disabled.",
                details={"buildable": buildable, "color": raw_color},
            )

        is_animating = color.endswith(JENKINS_COLOR_RUNNING_SUFFIX)
        if in_queue or is_animating:
            return CheckResult(
                check_name=self.name,
                passed=False,
                message=(
                    f"Jenkins job '{unit.jenkins_job}' is currently queued or running."
                ),
                details={
                    "in_queue": in_queue,
                    "building": is_animating,
                    "color": raw_color,
                },
            )

        if color != JENKINS_COLOR_GREEN:
            return CheckResult(
                check_name=self.name,
                passed=False,
                message=(
                    f"Jenkins job '{unit.jenkins_job}' is not green "
                    f"(color='{raw_color}')."
                ),
                details={"color": raw_color, "result": None},
            )

        last_build_url = f"{jenkins_url}/{job_path}/lastBuild/api/json"
        build_data, error_result = self._fetch_json(
            last_build_url, headers, timeout, unit.name
        )
        if error_result is not None or build_data is None:
            return error_result or CheckResult(
                check_name=self.name,
                passed=False,
                message="Jenkins lastBuild response was empty.",
            )

        building = bool(build_data.get("building", False))
        result = build_data.get("result")

        if building:
            return CheckResult(
                check_name=self.name,
                passed=False,
                message=f"Jenkins job '{unit.jenkins_job}' is currently building.",
                details={
                    "in_queue": in_queue,
                    "building": True,
                    "color": raw_color,
                    "result": result,
                },
            )

        if result == JENKINS_BUILD_RESULT_SUCCESS:
            return CheckResult(
                check_name=self.name,
                passed=True,
                message=f"Latest Jenkins build for '{unit.jenkins_job}' succeeded.",
                details={"color": raw_color, "result": result},
            )

        return CheckResult(
            check_name=self.name,
            passed=False,
            message=(
                f"Latest Jenkins build for '{unit.jenkins_job}' has status '{result}'."
            ),
            details={"color": raw_color, "result": result},
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
            crumb_timeout = min(
                context.settings.jenkins_crumb_timeout_seconds, http_timeout
            )
            crumb_resp = self._session.get(
                f"{jenkins_url}/crumbIssuer/api/json",
                headers=headers,
                timeout=crumb_timeout,
            )
            if crumb_resp.status_code == HTTPStatus.OK:
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

        if trigger_resp.status_code not in JENKINS_TRIGGER_SUCCESS_STATUSES:
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
            if resp.status_code != HTTPStatus.OK:
                continue
            data = resp.json()
            if data.get("building"):
                continue
            result = data.get("result")
            if result == JENKINS_BUILD_RESULT_SUCCESS:
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
