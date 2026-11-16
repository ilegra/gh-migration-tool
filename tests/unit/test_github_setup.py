"""Unit tests for Step 3 GitHub Setup."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import responses

from gh_migration_tool.interfaces import WaveContext
from gh_migration_tool.models import MigrationUnit
from gh_migration_tool.settings import MigrationSettings
from gh_migration_tool.stages.github_setup import (
    GitHubApiSetup,
    VerifyRepoExistsGitHubSetup,
    parse_github_owner_repo,
)


def _context(has_token_provider: bool = True) -> WaveContext:
    provider = MagicMock()
    provider.get_token.return_value = "ghs_mock_token"
    return WaveContext(
        settings=MigrationSettings(),
        github_token_provider=provider if has_token_provider else None,
    )


def _unit() -> MigrationUnit:
    return MigrationUnit(
        name="service-a",
        origin_url="https://dev.azure.com/org/p/_git/service-a",
        destination_url="https://github.com/my-org/service-a.git",
        teams={"devs": "push"},
        rulesets=[{"name": "protect-main", "enforcement": "active"}],
    )


@responses.activate
def test_given_missing_repo_when_running_github_api_setup_then_creates_repo_teams_and_rulesets() -> (
    None
):
    responses.add(
        responses.GET,
        "https://api.github.com/repos/my-org/service-a",
        status=404,
    )
    responses.add(
        responses.POST,
        "https://api.github.com/orgs/my-org/repos",
        json={"name": "service-a"},
        status=201,
    )
    responses.add(
        responses.PUT,
        "https://api.github.com/orgs/my-org/teams/devs/repos/my-org/service-a",
        status=204,
    )
    responses.add(
        responses.GET,
        "https://api.github.com/repos/my-org/service-a/rulesets",
        json=[],
        status=200,
    )
    responses.add(
        responses.POST,
        "https://api.github.com/repos/my-org/service-a/rulesets",
        json={"id": 1, "name": "protect-main"},
        status=201,
    )

    res = GitHubApiSetup().setup_unit(_unit(), _context())

    assert res.unit_name == "service-a"
    assert res.passed is True
    assert res.created is True
    assert "my-org/service-a" in res.message


@responses.activate
def test_given_existing_repo_and_ruleset_when_running_github_api_setup_then_updates_idempotently() -> (
    None
):
    responses.add(
        responses.GET,
        "https://api.github.com/repos/my-org/service-a",
        json={"name": "service-a"},
        status=200,
    )
    responses.add(
        responses.PUT,
        "https://api.github.com/orgs/my-org/teams/devs/repos/my-org/service-a",
        status=204,
    )
    responses.add(
        responses.GET,
        "https://api.github.com/repos/my-org/service-a/rulesets",
        json=[{"id": 42, "name": "protect-main"}],
        status=200,
    )
    responses.add(
        responses.PUT,
        "https://api.github.com/repos/my-org/service-a/rulesets/42",
        json={"id": 42, "name": "protect-main"},
        status=200,
    )

    res = GitHubApiSetup().setup_unit(_unit(), _context())

    assert res.unit_name == "service-a"
    assert res.passed is True
    assert res.created is False
    assert "my-org/service-a" in res.message


@responses.activate
def test_given_team_assignment_http_403_when_running_github_api_setup_then_returns_failed() -> (
    None
):
    responses.add(
        responses.GET,
        "https://api.github.com/repos/my-org/service-a",
        json={"name": "service-a"},
        status=200,
    )
    responses.add(
        responses.PUT,
        "https://api.github.com/orgs/my-org/teams/devs/repos/my-org/service-a",
        status=403,
    )

    res = GitHubApiSetup().setup_unit(_unit(), _context())

    assert res.unit_name == "service-a"
    assert res.passed is False
    assert res.created is False
    assert "HTTP 403" in res.message


@responses.activate
def test_given_existing_repo_when_verifying_setup_then_passes() -> None:
    responses.add(
        responses.GET,
        "https://api.github.com/repos/my-org/service-a",
        json={"name": "service-a"},
        status=200,
    )

    res = VerifyRepoExistsGitHubSetup().setup_unit(_unit(), _context())

    assert res.unit_name == "service-a"
    assert res.passed is True
    assert res.created is False
    assert "Verified" in res.message


@responses.activate
def test_given_missing_repo_when_verifying_setup_then_returns_failed() -> None:
    responses.add(
        responses.GET,
        "https://api.github.com/repos/my-org/service-a",
        status=404,
    )

    res = VerifyRepoExistsGitHubSetup().setup_unit(_unit(), _context())

    assert res.unit_name == "service-a"
    assert res.passed is False
    assert res.created is False
    assert "HTTP 404" in res.message


def test_given_missing_token_provider_when_running_setup_then_returns_failed() -> None:
    res = GitHubApiSetup().setup_unit(_unit(), _context(has_token_provider=False))

    assert res.unit_name == "service-a"
    assert res.passed is False
    assert res.created is False
    assert "GitHubAppTokenProvider" in res.message


def test_given_ssh_url_when_parsing_owner_repo_then_extracts_owner_and_repo() -> None:
    owner, repo = parse_github_owner_repo("git@github.com:my-org/service-a.git")

    assert owner == "my-org"
    assert repo == "service-a"


def test_given_invalid_destination_url_when_parsing_owner_repo_then_raises_value_error() -> (
    None
):
    with pytest.raises(ValueError, match="Cannot parse GitHub owner/repo"):
        parse_github_owner_repo("invalid-url")
