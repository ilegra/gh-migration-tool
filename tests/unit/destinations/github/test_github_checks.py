"""Unit tests for GitHub Actions trigger Post-check."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import requests
import responses

from gh_migration_tool.contracts import (
    MigrationStageResult,
    MigrationUnit,
    OriginSnapshot,
    WaveContext,
)
from gh_migration_tool.destinations.github.checks import GitHubActionsTriggerPostCheck
from gh_migration_tool.settings import MigrationSettings


def _unit(github_workflow: str | None = "ci.yml") -> MigrationUnit:
    return MigrationUnit(
        name="service-a",
        origin_url="https://dev.azure.com/org/p/_git/service-a",
        destination_url="https://github.com/org/service-a.git",
        github_workflow=github_workflow,
    )


def _mig_result() -> MigrationStageResult:
    return MigrationStageResult(
        unit_name="service-a",
        passed=True,
        origin_snapshot=OriginSnapshot(
            captured_at=datetime.now(timezone.utc),
            branch_heads={"main": "sha1"},
        ),
    )


def _context(has_token_provider: bool = True) -> WaveContext:
    provider = MagicMock()
    provider.get_token.return_value = "ghs_mock_token"
    return WaveContext(
        settings=MigrationSettings(
            CI_POLL_INTERVAL_SECONDS=0.0,
            CI_POLL_TIMEOUT_SECONDS=0.1,
        ),
        github_token_provider=provider if has_token_provider else None,
    )


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
