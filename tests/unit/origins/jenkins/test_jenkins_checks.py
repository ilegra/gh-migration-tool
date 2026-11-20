"""Unit tests for Step 1 Pre-checks."""

from __future__ import annotations

from unittest.mock import MagicMock

import requests
import responses

from gh_migration_tool.contracts import MigrationUnit, WaveContext
from gh_migration_tool.origins.jenkins.checks import JenkinsJobPreCheck
from gh_migration_tool.settings import MigrationSettings


def _unit(jenkins_job: str | None = "team/service-a") -> MigrationUnit:
    return MigrationUnit(
        name="service-a",
        origin_url="https://dev.azure.com/org/p/_git/service-a",
        destination_url="https://github.com/org/service-a.git",
        jenkins_job=jenkins_job,
    )


def _context(jenkins_url: str = "https://jenkins.example.com") -> WaveContext:
    settings = MigrationSettings(
        JENKINS_URL=jenkins_url,
        JENKINS_USERNAME="bot",
        JENKINS_TOKEN="tok",
    )
    return WaveContext(settings=settings)


@responses.activate
def test_given_successful_jenkins_build_when_executing_pre_check_then_passes() -> None:
    responses.add(
        responses.GET,
        "https://jenkins.example.com/job/team/job/service-a/lastBuild/api/json",
        json={"building": False, "result": "SUCCESS"},
        status=200,
    )

    res = JenkinsJobPreCheck().execute(_unit(), _context())

    assert res.check_name == "jenkins_job_pre_check"
    assert res.passed is True
    assert "succeeded" in res.message
    assert res.details == {"result": "SUCCESS"}


@responses.activate
def test_given_failed_jenkins_build_when_executing_pre_check_then_fails() -> None:
    responses.add(
        responses.GET,
        "https://jenkins.example.com/job/team/job/service-a/lastBuild/api/json",
        json={"building": False, "result": "FAILURE"},
        status=200,
    )

    res = JenkinsJobPreCheck().execute(_unit(), _context())

    assert res.check_name == "jenkins_job_pre_check"
    assert res.passed is False
    assert "FAILURE" in res.message
    assert res.details == {"result": "FAILURE"}


@responses.activate
def test_given_in_progress_jenkins_build_when_executing_pre_check_then_fails() -> None:
    responses.add(
        responses.GET,
        "https://jenkins.example.com/job/team/job/service-a/lastBuild/api/json",
        json={"building": True, "result": None},
        status=200,
    )

    res = JenkinsJobPreCheck().execute(_unit(), _context())

    assert res.check_name == "jenkins_job_pre_check"
    assert res.passed is False
    assert "currently building" in res.message
    assert res.details == {"building": True, "result": None}


@responses.activate
def test_given_http_404_from_jenkins_when_executing_pre_check_then_fails() -> None:
    responses.add(
        responses.GET,
        "https://jenkins.example.com/job/team/job/service-a/lastBuild/api/json",
        status=404,
    )

    res = JenkinsJobPreCheck().execute(_unit(), _context())

    assert res.check_name == "jenkins_job_pre_check"
    assert res.passed is False
    assert "HTTP 404" in res.message
    assert res.details == {"status_code": 404}


@responses.activate
def test_given_http_500_from_jenkins_when_executing_pre_check_then_retries_and_fails() -> (
    None
):
    responses.add(
        responses.GET,
        "https://jenkins.example.com/job/team/job/service-a/lastBuild/api/json",
        status=500,
    )

    res = JenkinsJobPreCheck().execute(_unit(), _context())

    assert res.check_name == "jenkins_job_pre_check"
    assert res.passed is False
    assert "500 error responses" in res.message
    assert res.details == {}


def test_given_network_error_when_executing_pre_check_then_returns_failed_result() -> (
    None
):
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("timeout")

    res = JenkinsJobPreCheck(session=session).execute(_unit(), _context())

    assert res.check_name == "jenkins_job_pre_check"
    assert res.passed is False
    assert "timeout" in res.message
    assert res.details == {}


def test_given_missing_jenkins_url_when_executing_pre_check_then_returns_failed_result() -> (
    None
):
    res = JenkinsJobPreCheck().execute(_unit(), _context(jenkins_url=""))

    assert res.check_name == "jenkins_job_pre_check"
    assert res.passed is False
    assert "JENKINS_URL" in res.message
    assert res.details == {}


def test_given_no_jenkins_job_configured_when_executing_pre_check_then_passes() -> None:
    res = JenkinsJobPreCheck().execute(_unit(jenkins_job=None), _context())

    assert res.check_name == "jenkins_job_pre_check"
    assert res.passed is True
    assert "skipping" in res.message
    assert res.details == {}


# --- JenkinsTriggerPostCheck tests ---

from datetime import datetime, timezone  # noqa: E402

from gh_migration_tool.contracts import MigrationStageResult, OriginSnapshot  # noqa: E402
from gh_migration_tool.origins.jenkins.checks import JenkinsTriggerPostCheck  # noqa: E402


def _mig_result() -> MigrationStageResult:
    return MigrationStageResult(
        unit_name="service-a",
        passed=True,
        origin_snapshot=OriginSnapshot(
            captured_at=datetime.now(timezone.utc),
            branch_heads={"main": "sha1"},
        ),
    )


@responses.activate
def test_given_successful_jenkins_trigger_when_executing_post_check_then_passes() -> (
    None
):
    responses.add(
        responses.GET,
        "https://jenkins.example.com/crumbIssuer/api/json",
        status=404,
    )
    responses.add(
        responses.POST,
        "https://jenkins.example.com/job/team/job/service-a/build",
        status=201,
    )
    responses.add(
        responses.GET,
        "https://jenkins.example.com/job/team/job/service-a/lastBuild/api/json",
        json={"building": False, "result": "SUCCESS"},
        status=200,
    )

    res = JenkinsTriggerPostCheck(sleep_fn=lambda _: None).execute(
        _unit(), _mig_result(), _context()
    )

    assert res.check_name == "jenkins_trigger_post_check"
    assert res.passed is True
    assert "succeeded" in res.message
    assert res.details == {}


@responses.activate
def test_given_failed_jenkins_trigger_build_when_executing_post_check_then_fails() -> (
    None
):
    responses.add(
        responses.GET,
        "https://jenkins.example.com/crumbIssuer/api/json",
        status=404,
    )
    responses.add(
        responses.POST,
        "https://jenkins.example.com/job/team/job/service-a/build",
        status=201,
    )
    responses.add(
        responses.GET,
        "https://jenkins.example.com/job/team/job/service-a/lastBuild/api/json",
        json={"building": False, "result": "FAILURE"},
        status=200,
    )

    res = JenkinsTriggerPostCheck(sleep_fn=lambda _: None).execute(
        _unit(), _mig_result(), _context()
    )

    assert res.check_name == "jenkins_trigger_post_check"
    assert res.passed is False
    assert "FAILURE" in res.message
    assert res.details == {"result": "FAILURE"}


@responses.activate
def test_given_jenkins_trigger_http_500_when_executing_post_check_then_fails() -> None:
    responses.add(
        responses.GET,
        "https://jenkins.example.com/crumbIssuer/api/json",
        status=404,
    )
    responses.add(
        responses.POST,
        "https://jenkins.example.com/job/team/job/service-a/build",
        status=500,
    )

    res = JenkinsTriggerPostCheck(sleep_fn=lambda _: None).execute(
        _unit(), _mig_result(), _context()
    )

    assert res.check_name == "jenkins_trigger_post_check"
    assert res.passed is False
    assert "500 error responses" in res.message
    assert res.details == {}


@responses.activate
def test_given_transient_503_on_jenkins_trigger_when_executing_post_check_then_retries_and_passes() -> (
    None
):
    responses.add(
        responses.GET,
        "https://jenkins.example.com/crumbIssuer/api/json",
        status=404,
    )
    responses.add(
        responses.POST,
        "https://jenkins.example.com/job/team/job/service-a/build",
        status=503,
    )
    responses.add(
        responses.POST,
        "https://jenkins.example.com/job/team/job/service-a/build",
        status=201,
    )
    responses.add(
        responses.GET,
        "https://jenkins.example.com/job/team/job/service-a/lastBuild/api/json",
        json={"building": False, "result": "SUCCESS"},
        status=200,
    )

    res = JenkinsTriggerPostCheck(sleep_fn=lambda _: None).execute(
        _unit(), _mig_result(), _context()
    )

    assert res.check_name == "jenkins_trigger_post_check"
    assert res.passed is True
    assert "succeeded" in res.message


def test_given_network_error_on_jenkins_trigger_when_executing_post_check_then_fails() -> (
    None
):
    session = MagicMock()
    session.get.side_effect = requests.Timeout("read timed out")

    res = JenkinsTriggerPostCheck(session=session, sleep_fn=lambda _: None).execute(
        _unit(), _mig_result(), _context()
    )

    assert res.check_name == "jenkins_trigger_post_check"
    assert res.passed is False
    assert "read timed out" in res.message
    assert res.details == {}
