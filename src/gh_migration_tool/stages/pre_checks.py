"""Step 1: Built-in Pre-check implementations."""

from __future__ import annotations

import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from gh_migration_tool._http import build_jenkins_headers, format_jenkins_job_path
from gh_migration_tool.interfaces import PreCheck, WaveContext
from gh_migration_tool.models import CheckResult, MigrationUnit

logger = logging.getLogger(__name__)


def _build_jenkins_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class JenkinsJobPreCheck(PreCheck):
    """Verifies that a Migration Unit's latest Jenkins build succeeded."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or _build_jenkins_session()

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
            response = self._session.get(api_url, headers=headers, timeout=30)
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
