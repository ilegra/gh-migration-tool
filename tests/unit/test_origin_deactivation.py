"""Unit tests for BitbucketCloudOriginDeactivator."""

from __future__ import annotations

import requests
import responses

from gh_migration_tool.interfaces import WaveContext
from gh_migration_tool.models import MigrationUnit
from gh_migration_tool.settings import MigrationSettings
from gh_migration_tool.stages.origin_deactivation import BitbucketCloudOriginDeactivator


def _unit(
    origin_url: str = "https://bitbucket.org/acme-ws/payments-api.git",
) -> MigrationUnit:
    return MigrationUnit(
        name="payments-api",
        origin_url=origin_url,
        destination_url="https://github.com/acme/payments-api.git",
    )


def _context(
    origin_token: str = "bb-admin-token",
    origin_username: str | None = None,
) -> WaveContext:
    settings = MigrationSettings(
        origin_token=origin_token,
        origin_username=origin_username,
        http_max_retries=0,
    )
    return WaveContext(settings=settings)


@responses.activate
def test_given_valid_bitbucket_repo_when_deactivating_then_creates_push_branch_restriction() -> (
    None
):
    responses.add(
        responses.GET,
        "https://api.bitbucket.org/2.0/repositories/acme-ws/payments-api/branch-restrictions",
        json={"values": []},
        status=200,
    )
    responses.add(
        responses.POST,
        "https://api.bitbucket.org/2.0/repositories/acme-ws/payments-api/branch-restrictions",
        json={"id": 42, "kind": "push", "pattern": "*"},
        status=201,
    )

    deactivator = BitbucketCloudOriginDeactivator()
    result = deactivator.deactivate(_unit(), _context())

    assert result.passed is True
    assert result.unit_name == "payments-api"
    assert result.deactivator_name == "bitbucket_cloud_origin_deactivator"
    assert result.already_disabled is False
    assert result.details["workspace"] == "acme-ws"
    assert result.details["repo_slug"] == "payments-api"
    assert result.details["push_restriction_added"] is True
    assert responses.calls[0].request.headers["Authorization"] == "Bearer bb-admin-token"


@responses.activate
def test_given_existing_push_restriction_when_deactivating_then_is_idempotent() -> None:
    responses.add(
        responses.GET,
        "https://api.bitbucket.org/2.0/repositories/acme-ws/payments-api/branch-restrictions",
        json={
            "values": [
                {"id": 10, "kind": "push", "pattern": "*", "users": [], "groups": []}
            ]
        },
        status=200,
    )

    deactivator = BitbucketCloudOriginDeactivator()
    result = deactivator.deactivate(_unit(), _context())

    assert result.passed is True
    assert result.already_disabled is True
    assert result.details["push_restriction_added"] is False
    assert len(responses.calls) == 1


@responses.activate
def test_given_downgrade_permissions_enabled_when_deactivating_then_updates_non_read_entries() -> (
    None
):
    responses.add(
        responses.GET,
        "https://api.bitbucket.org/2.0/repositories/acme-ws/payments-api/branch-restrictions",
        json={"values": []},
        status=200,
    )
    responses.add(
        responses.POST,
        "https://api.bitbucket.org/2.0/repositories/acme-ws/payments-api/branch-restrictions",
        json={"id": 42, "kind": "push", "pattern": "*"},
        status=201,
    )
    responses.add(
        responses.GET,
        "https://api.bitbucket.org/2.0/repositories/acme-ws/payments-api/permissions-config/groups",
        json={
            "values": [
                {"group": {"slug": "devs"}, "permission": "write"},
                {"group": {"slug": "readers"}, "permission": "read"},
            ]
        },
        status=200,
    )
    responses.add(
        responses.PUT,
        "https://api.bitbucket.org/2.0/repositories/acme-ws/payments-api/permissions-config/groups/devs",
        json={"permission": "read"},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.bitbucket.org/2.0/repositories/acme-ws/payments-api/permissions-config/users",
        json={
            "values": [
                {"user": {"uuid": "{user-1}"}, "permission": "admin"},
            ]
        },
        status=200,
    )
    responses.add(
        responses.PUT,
        "https://api.bitbucket.org/2.0/repositories/acme-ws/payments-api/permissions-config/users/%7Buser-1%7D",
        json={"permission": "read"},
        status=200,
    )

    deactivator = BitbucketCloudOriginDeactivator(downgrade_permissions=True)
    result = deactivator.deactivate(
        _unit(), _context(origin_token="app-pass", origin_username="bb-user")
    )

    assert result.passed is True
    assert result.details["push_restriction_added"] is True
    assert result.details["downgraded_groups"] == 1
    assert result.details["downgraded_users"] == 1
    assert responses.calls[0].request.headers["Authorization"].startswith("Basic ")


@responses.activate
def test_given_http_403_from_bitbucket_when_deactivating_then_returns_failed_result() -> (
    None
):
    responses.add(
        responses.GET,
        "https://api.bitbucket.org/2.0/repositories/acme-ws/payments-api/branch-restrictions",
        json={"error": {"message": "Forbidden"}},
        status=403,
    )

    deactivator = BitbucketCloudOriginDeactivator()
    result = deactivator.deactivate(_unit(), _context())

    assert result.passed is False
    assert "403" in result.message


@responses.activate
def test_given_network_error_when_deactivating_then_returns_failed_result() -> None:
    responses.add(
        responses.GET,
        "https://api.bitbucket.org/2.0/repositories/acme-ws/payments-api/branch-restrictions",
        body=requests.ConnectionError("connection timed out"),
    )

    deactivator = BitbucketCloudOriginDeactivator()
    result = deactivator.deactivate(_unit(), _context())

    assert result.passed is False
    assert "connection timed out" in result.message


def test_given_missing_origin_token_when_deactivating_then_returns_failed_result() -> (
    None
):
    deactivator = BitbucketCloudOriginDeactivator()
    result = deactivator.deactivate(_unit(), _context(origin_token=""))

    assert result.passed is False
    assert "ORIGIN_TOKEN" in result.message


def test_given_non_bitbucket_url_when_deactivating_then_returns_failed_result() -> None:
    deactivator = BitbucketCloudOriginDeactivator()
    result = deactivator.deactivate(
        _unit(origin_url="https://dev.azure.com/org/proj/_git/repo"), _context()
    )

    assert result.passed is False
    assert "Bitbucket Cloud" in result.message
