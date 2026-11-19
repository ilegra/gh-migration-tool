"""Unit tests for Step 5 Post-checks."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import requests
import responses

from gh_migration_tool.interfaces import WaveContext
from gh_migration_tool.models import (
    MigrationStageResult,
    MigrationUnit,
    OriginSnapshot,
)
from gh_migration_tool.settings import MigrationSettings
from gh_migration_tool.stages.post_checks import (
    GitHubActionsTriggerPostCheck,
    JenkinsTriggerPostCheck,
    SourceDriftPostCheck,
)


def _unit(
    jenkins_job: str | None = "team/service-a",
    github_workflow: str | None = "ci.yml",
) -> MigrationUnit:
    return MigrationUnit(
        name="service-a",
        origin_url="https://dev.azure.com/org/p/_git/service-a",
        destination_url="https://github.com/org/service-a.git",
        jenkins_job=jenkins_job,
        github_workflow=github_workflow,
    )


def _mig_result(
    branch_heads: dict[str, str] | None = None,
    with_snapshot: bool = True,
) -> MigrationStageResult:
    snapshot = (
        OriginSnapshot(
            captured_at=datetime.now(timezone.utc),
            branch_heads=branch_heads or {"main": "sha1"},
        )
        if with_snapshot
        else None
    )
    return MigrationStageResult(
        unit_name="service-a",
        passed=True,
        origin_snapshot=snapshot,
    )


def _context(
    jenkins_url: str = "https://jenkins.example.com",
    has_token_provider: bool = True,
) -> WaveContext:
    provider = MagicMock()
    provider.get_token.return_value = "ghs_mock_token"
    return WaveContext(
        settings=MigrationSettings(
            JENKINS_URL=jenkins_url,
            JENKINS_USERNAME="bot",
            JENKINS_TOKEN="tok",
            CI_POLL_INTERVAL_SECONDS=0.0,
            CI_POLL_TIMEOUT_SECONDS=0.1,
        ),
        github_token_provider=provider if has_token_provider else None,
    )


def test_given_unchanged_origin_refs_when_checking_drift_then_passes() -> None:
    check = SourceDriftPostCheck(ls_remote_fn=lambda *_: {"main": "sha1"})
    res = check.execute(_unit(), _mig_result({"main": "sha1"}), _context())

    assert res.check_name == "source_drift_post_check"
    assert res.passed is True
    assert "No source drift" in res.message
    assert res.details == {}


def test_given_advanced_commit_and_new_branch_when_checking_drift_then_fails() -> None:
    check = SourceDriftPostCheck(
        ls_remote_fn=lambda *_: {"main": "sha2", "feat/new": "sha3"}
    )
    res = check.execute(_unit(), _mig_result({"main": "sha1"}), _context())

    assert res.check_name == "source_drift_post_check"
    assert res.passed is False
    assert "Source drift detected" in res.message
    assert res.details == {
        "advanced_branches": ["main"],
        "new_branches": ["feat/new"],
    }


def test_given_missing_origin_snapshot_when_checking_drift_then_fails() -> None:
    check = SourceDriftPostCheck(ls_remote_fn=lambda *_: {"main": "sha1"})
    res = check.execute(_unit(), _mig_result(with_snapshot=False), _context())

    assert res.check_name == "source_drift_post_check"
    assert res.passed is False
    assert "OriginSnapshot is missing" in res.message
    assert res.details == {}


def test_given_ls_remote_error_when_checking_drift_then_fails() -> None:
    def _boom(*_: object) -> dict[str, str]:
        raise RuntimeError("git ls-remote timeout")

    check = SourceDriftPostCheck(ls_remote_fn=_boom)
    res = check.execute(_unit(), _mig_result({"main": "sha1"}), _context())

    assert res.check_name == "source_drift_post_check"
    assert res.passed is False
    assert "git ls-remote timeout" in res.message
    assert res.details == {}


def test_given_bitbucket_unit_when_checking_drift_then_forwards_origin_provider_to_ls_remote() -> (
    None
):
    captured_args: list[tuple[object, ...]] = []

    def _fake_ls_remote(*args: object) -> dict[str, str]:
        captured_args.append(args)
        return {"main": "sha1"}

    unit = MigrationUnit(
        name="bb-service",
        origin_url="https://bitbucket.org/ws/bb-service.git",
        destination_url="https://github.com/org/bb-service.git",
        origin_provider="bitbucket",
    )
    check = SourceDriftPostCheck(ls_remote_fn=_fake_ls_remote)
    res = check.execute(unit, _mig_result({"main": "sha1"}), _context())

    assert res.passed is True
    assert captured_args == [
        ("https://bitbucket.org/ws/bb-service.git", "", None, "bitbucket")
    ]


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


@responses.activate
def test_given_successful_gh_workflow_when_executing_post_check_then_passes() -> None:
    responses.add(
        responses.POST,
        "https://api.github.com/repos/org/service-a/actions/workflows/ci.yml/dispatches",
        status=204,
    )
    responses.add(
        responses.GET,
        "https://api.github.com/repos/org/service-a/actions/workflows/ci.yml/runs",
        json={"workflow_runs": [{"status": "completed", "conclusion": "success"}]},
        status=200,
    )

    res = GitHubActionsTriggerPostCheck(sleep_fn=lambda _: None).execute(
        _unit(), _mig_result(), _context()
    )

    assert res.check_name == "github_actions_trigger_post_check"
    assert res.passed is True
    assert "success" in res.message
    assert res.details == {}


@responses.activate
def test_given_failed_gh_workflow_when_executing_post_check_then_fails() -> None:
    responses.add(
        responses.POST,
        "https://api.github.com/repos/org/service-a/actions/workflows/ci.yml/dispatches",
        status=204,
    )
    responses.add(
        responses.GET,
        "https://api.github.com/repos/org/service-a/actions/workflows/ci.yml/runs",
        json={"workflow_runs": [{"status": "completed", "conclusion": "failure"}]},
        status=200,
    )

    res = GitHubActionsTriggerPostCheck(sleep_fn=lambda _: None).execute(
        _unit(), _mig_result(), _context()
    )

    assert res.check_name == "github_actions_trigger_post_check"
    assert res.passed is False
    assert "failure" in res.message
    assert res.details == {"conclusion": "failure"}


def test_given_missing_token_provider_when_executing_gh_post_check_then_fails() -> None:
    res = GitHubActionsTriggerPostCheck().execute(
        _unit(), _mig_result(), _context(has_token_provider=False)
    )

    assert res.check_name == "github_actions_trigger_post_check"
    assert res.passed is False
    assert "GitHubAppTokenProvider" in res.message
    assert res.details == {}


def test_given_network_error_on_gh_dispatch_when_executing_post_check_then_fails() -> (
    None
):
    session = MagicMock()
    session.post.side_effect = requests.ConnectionError("connection reset")

    res = GitHubActionsTriggerPostCheck(
        session=session, sleep_fn=lambda _: None
    ).execute(_unit(), _mig_result(), _context())

    assert res.check_name == "github_actions_trigger_post_check"
    assert res.passed is False
    assert "connection reset" in res.message
    assert res.details == {}
