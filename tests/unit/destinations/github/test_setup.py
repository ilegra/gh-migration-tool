"""Unit tests for Step 3 GitHub Setup."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests
import responses

from gh_migration_tool.contracts import MigrationUnit, WaveContext
from gh_migration_tool.destinations.github.setup import (
    GitHubApiSetup,
    VerifyRepoExistsGitHubSetup,
    parse_github_owner_repo,
)
from gh_migration_tool.settings import MigrationSettings


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


@responses.activate
def test_given_transient_503_when_verifying_repo_exists_then_retries_and_passes() -> (
    None
):
    responses.add(
        responses.GET,
        "https://api.github.com/repos/my-org/service-a",
        status=503,
    )
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
    assert len(responses.calls) == 2


def test_given_network_error_when_verifying_repo_exists_then_returns_failed() -> None:
    session = MagicMock()
    session.get.side_effect = requests.Timeout("read timed out")

    res = VerifyRepoExistsGitHubSetup(session=session).setup_unit(_unit(), _context())

    assert res.unit_name == "service-a"
    assert res.passed is False
    assert res.created is False
    assert "read timed out" in res.message


def test_given_network_error_when_running_github_api_setup_then_returns_failed() -> (
    None
):
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("connection refused")

    res = GitHubApiSetup(session=session).setup_unit(_unit(), _context())

    assert res.unit_name == "service-a"
    assert res.passed is False
    assert res.created is False
    assert "connection refused" in res.message


def test_given_ssh_url_when_parsing_owner_repo_then_extracts_owner_and_repo() -> None:
    owner, repo = parse_github_owner_repo("git@github.com:my-org/service-a.git")

    assert owner == "my-org"
    assert repo == "service-a"


def test_given_invalid_destination_url_when_parsing_owner_repo_then_raises_value_error() -> (
    None
):
    with pytest.raises(ValueError, match="Cannot parse GitHub owner/repo"):
        parse_github_owner_repo("invalid-url")


@responses.activate
def test_given_repository_settings_when_running_github_api_setup_then_patches_repo_settings() -> (
    None
):
    unit = MigrationUnit(
        name="service-a",
        origin_url="https://bitbucket.org/org/service-a.git",
        destination_url="https://github.com/my-org/service-a.git",
        repository_settings={
            "allow_auto_merge": True,
            "delete_branch_on_merge": True,
        },
    )
    responses.add(
        responses.GET,
        "https://api.github.com/repos/my-org/service-a",
        json={"name": "service-a"},
        status=200,
    )
    responses.add(
        responses.PATCH,
        "https://api.github.com/repos/my-org/service-a",
        json={
            "name": "service-a",
            "allow_auto_merge": True,
            "delete_branch_on_merge": True,
        },
        status=200,
    )

    res = GitHubApiSetup().setup_unit(unit, _context())

    assert res.unit_name == "service-a"
    assert res.passed is True
    assert res.created is False
    assert "my-org/service-a" in res.message
    patch_calls = [c for c in responses.calls if c.request.method == "PATCH"]
    assert len(patch_calls) == 1
    assert patch_calls[0].request.url == "https://api.github.com/repos/my-org/service-a"
    assert patch_calls[0].request.body is not None
    assert b'"allow_auto_merge": true' in patch_calls[0].request.body
    assert b'"delete_branch_on_merge": true' in patch_calls[0].request.body


@responses.activate
def test_given_empty_repository_settings_when_running_github_api_setup_then_skips_patch_request() -> (
    None
):
    unit = MigrationUnit(
        name="service-a",
        origin_url="https://bitbucket.org/org/service-a.git",
        destination_url="https://github.com/my-org/service-a.git",
    )
    responses.add(
        responses.GET,
        "https://api.github.com/repos/my-org/service-a",
        json={"name": "service-a"},
        status=200,
    )

    res = GitHubApiSetup().setup_unit(unit, _context())

    assert res.unit_name == "service-a"
    assert res.passed is True
    assert res.created is False
    assert all(c.request.method != "PATCH" for c in responses.calls)


@responses.activate
def test_given_patch_repo_settings_http_422_when_running_github_api_setup_then_returns_failed() -> (
    None
):
    unit = MigrationUnit(
        name="service-a",
        origin_url="https://bitbucket.org/org/service-a.git",
        destination_url="https://github.com/my-org/service-a.git",
        repository_settings={"allow_auto_merge": True},
    )
    responses.add(
        responses.GET,
        "https://api.github.com/repos/my-org/service-a",
        json={"name": "service-a"},
        status=200,
    )
    responses.add(
        responses.PATCH,
        "https://api.github.com/repos/my-org/service-a",
        json={"message": "Validation Failed"},
        status=422,
    )

    res = GitHubApiSetup().setup_unit(unit, _context())

    assert res.unit_name == "service-a"
    assert res.passed is False
    assert res.created is False
    assert "HTTP 422" in res.message


def test_given_patch_repo_settings_network_error_when_running_github_api_setup_then_returns_failed() -> (
    None
):
    unit = MigrationUnit(
        name="service-a",
        origin_url="https://bitbucket.org/org/service-a.git",
        destination_url="https://github.com/my-org/service-a.git",
        repository_settings={"allow_auto_merge": True},
    )
    session = MagicMock()
    get_resp = MagicMock()
    get_resp.status_code = 200
    session.get.return_value = get_resp
    session.patch.side_effect = requests.Timeout("patch timed out")

    res = GitHubApiSetup(session=session).setup_unit(unit, _context())

    assert res.unit_name == "service-a"
    assert res.passed is False
    assert res.created is False
    assert "patch timed out" in res.message
