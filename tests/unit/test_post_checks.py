"""Unit tests for Step 5 Post-checks."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

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


def _unit() -> MigrationUnit:
    return MigrationUnit(
        name="service-a",
        origin_url="https://dev.azure.com/org/p/_git/service-a",
        destination_url="https://github.com/org/service-a.git",
        jenkins_job="team/service-a",
        github_workflow="ci.yml",
    )


def _mig_result(branch_heads: dict[str, str] | None = None) -> MigrationStageResult:
    return MigrationStageResult(
        unit_name="service-a",
        passed=True,
        origin_snapshot=OriginSnapshot(
            captured_at=datetime.now(timezone.utc),
            branch_heads=branch_heads or {"main": "sha1"},
        ),
    )


def _context() -> WaveContext:
    provider = MagicMock()
    provider.get_token.return_value = "ghs_mock_token"
    return WaveContext(
        settings=MigrationSettings(
            JENKINS_URL="https://jenkins.example.com",
            JENKINS_USERNAME="bot",
            JENKINS_TOKEN="tok",
            CI_POLL_INTERVAL_SECONDS=0.0,
            CI_POLL_TIMEOUT_SECONDS=5.0,
        ),
        github_token_provider=provider,
    )


def test_given_unchanged_origin_refs_when_checking_drift_then_passes() -> None:
    check = SourceDriftPostCheck(ls_remote_fn=lambda *_: {"main": "sha1"})
    res = check.execute(_unit(), _mig_result({"main": "sha1"}), _context())

    assert res.passed is True


def test_given_advanced_commit_and_new_branch_when_checking_drift_then_fails() -> None:
    check = SourceDriftPostCheck(
        ls_remote_fn=lambda *_: {"main": "sha2", "feat/new": "sha3"}
    )
    res = check.execute(_unit(), _mig_result({"main": "sha1"}), _context())

    assert res.passed is False
    assert "main" in res.details["advanced_branches"]
    assert "feat/new" in res.details["new_branches"]


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

    assert res.passed is True


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

    assert res.passed is True
