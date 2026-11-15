"""Unit tests for Step 1 Pre-checks."""

from __future__ import annotations

from unittest.mock import MagicMock

import requests
import responses

from gh_migration_tool.interfaces import WaveContext
from gh_migration_tool.models import MigrationUnit
from gh_migration_tool.settings import MigrationSettings
from gh_migration_tool.stages.pre_checks import JenkinsJobPreCheck


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

    assert res.passed is True


@responses.activate
def test_given_failed_jenkins_build_when_executing_pre_check_then_fails() -> None:
    responses.add(
        responses.GET,
        "https://jenkins.example.com/job/team/job/service-a/lastBuild/api/json",
        json={"building": False, "result": "FAILURE"},
        status=200,
    )

    res = JenkinsJobPreCheck().execute(_unit(), _context())

    assert res.passed is False
    assert "FAILURE" in res.message


def test_given_network_error_when_executing_pre_check_then_returns_failed_result() -> (
    None
):
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("timeout")

    res = JenkinsJobPreCheck(session=session).execute(_unit(), _context())

    assert res.passed is False
    assert "timeout" in res.message


def test_given_no_jenkins_job_configured_when_executing_pre_check_then_passes() -> None:
    res = JenkinsJobPreCheck().execute(_unit(jenkins_job=None), _context())

    assert res.passed is True
