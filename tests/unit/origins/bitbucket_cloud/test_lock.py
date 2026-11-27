"""Unit tests for OriginLock contract and BitbucketCloudOriginLock."""

from __future__ import annotations

import pytest
import requests
import responses

from gh_migration_tool.contracts import (
    MigrationUnit,
    OriginLock,
    OriginLockResult,
    WaveContext,
)
from gh_migration_tool.origins.bitbucket_cloud.lock import BitbucketCloudOriginLock
from gh_migration_tool.settings import MigrationSettings

RESTRICTIONS_URL = (
    "https://api.bitbucket.org/2.0/repositories/acme-ws/payments-api/"
    "branch-restrictions"
)


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


# --- OriginLock interface enforcement ---


def test_given_subclass_missing_unlock_when_instantiating_origin_lock_then_raises_type_error() -> (
    None
):
    class _LockOnly(OriginLock):
        @property
        def name(self) -> str:
            return "lock_only"

        def lock(self, unit: MigrationUnit, context: WaveContext) -> OriginLockResult:
            return OriginLockResult(unit_name=unit.name, passed=True)

    with pytest.raises(TypeError, match="unlock"):
        _LockOnly()  # type: ignore[abstract]


def test_given_subclass_missing_lock_when_instantiating_origin_lock_then_raises_type_error() -> (
    None
):
    class _UnlockOnly(OriginLock):
        @property
        def name(self) -> str:
            return "unlock_only"

        def unlock(self, unit: MigrationUnit, context: WaveContext) -> OriginLockResult:
            return OriginLockResult(unit_name=unit.name, passed=True)

    with pytest.raises(TypeError, match="lock"):
        _UnlockOnly()  # type: ignore[abstract]


# --- BitbucketCloudOriginLock.lock() tests ---


@responses.activate
def test_given_valid_bitbucket_repo_when_locking_then_creates_push_branch_restriction() -> (
    None
):
    responses.add(
        responses.GET,
        RESTRICTIONS_URL,
        json={"values": []},
        status=200,
    )
    responses.add(
        responses.POST,
        RESTRICTIONS_URL,
        json={"id": 42, "kind": "push", "pattern": "*"},
        status=201,
    )

    origin_lock = BitbucketCloudOriginLock()
    result = origin_lock.lock(_unit(), _context())

    assert result.unit_name == "payments-api"
    assert result.passed is True
    assert result.lock_name == "bitbucket_cloud_origin_lock"
    assert result.already_in_target_state is False
    assert "locked" in result.message
    assert result.details == {
        "workspace": "acme-ws",
        "repo_slug": "payments-api",
        "push_restriction_added": True,
    }
    assert (
        responses.calls[0].request.headers["Authorization"] == "Bearer bb-admin-token"
    )


@responses.activate
def test_given_origin_username_when_locking_then_uses_basic_auth() -> None:
    responses.add(
        responses.GET,
        RESTRICTIONS_URL,
        json={"values": []},
        status=200,
    )
    responses.add(
        responses.POST,
        RESTRICTIONS_URL,
        json={"id": 42, "kind": "push", "pattern": "*"},
        status=201,
    )

    origin_lock = BitbucketCloudOriginLock()
    result = origin_lock.lock(
        _unit(), _context(origin_token="app-pass", origin_username="bb-user")
    )

    assert result.passed is True
    assert result.already_in_target_state is False
    assert responses.calls[0].request.headers["Authorization"].startswith("Basic ")


@responses.activate
def test_given_existing_push_restriction_when_locking_then_is_idempotent() -> None:
    responses.add(
        responses.GET,
        RESTRICTIONS_URL,
        json={
            "values": [
                {"id": 10, "kind": "push", "pattern": "*", "users": [], "groups": []}
            ]
        },
        status=200,
    )

    origin_lock = BitbucketCloudOriginLock()
    result = origin_lock.lock(_unit(), _context())

    assert result.unit_name == "payments-api"
    assert result.passed is True
    assert result.lock_name == "bitbucket_cloud_origin_lock"
    assert result.already_in_target_state is True
    assert "already locked" in result.message
    assert result.details == {
        "workspace": "acme-ws",
        "repo_slug": "payments-api",
        "push_restriction_added": False,
    }
    assert len(responses.calls) == 1


@responses.activate
def test_given_paginated_restrictions_with_existing_lock_when_locking_then_detects_lock_across_pages() -> (
    None
):
    page_2_url = f"{RESTRICTIONS_URL}?page=2"
    responses.add(
        responses.GET,
        RESTRICTIONS_URL,
        json={
            "values": [{"id": 1, "kind": "force", "pattern": "main"}],
            "next": page_2_url,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        page_2_url,
        json={
            "values": [
                {"id": 2, "kind": "push", "pattern": "*", "users": [], "groups": []}
            ]
        },
        status=200,
    )

    origin_lock = BitbucketCloudOriginLock()
    result = origin_lock.lock(_unit(), _context())

    assert result.passed is True
    assert result.already_in_target_state is True
    assert result.details["push_restriction_added"] is False
    assert len(responses.calls) == 2


@responses.activate
def test_given_http_403_on_get_when_locking_then_returns_failed_result() -> None:
    responses.add(
        responses.GET,
        RESTRICTIONS_URL,
        json={"error": {"message": "Forbidden"}},
        status=403,
    )

    result = BitbucketCloudOriginLock().lock(_unit(), _context())

    assert result.unit_name == "payments-api"
    assert result.passed is False
    assert result.lock_name == "bitbucket_cloud_origin_lock"
    assert result.already_in_target_state is False
    assert "403" in result.message


@responses.activate
def test_given_http_500_on_post_when_locking_then_returns_failed_result() -> None:
    responses.add(
        responses.GET,
        RESTRICTIONS_URL,
        json={"values": []},
        status=200,
    )
    responses.add(
        responses.POST,
        RESTRICTIONS_URL,
        json={"error": {"message": "Internal error"}},
        status=500,
    )

    result = BitbucketCloudOriginLock().lock(_unit(), _context())

    assert result.passed is False
    assert "500" in result.message
    assert result.details["push_restriction_added"] is False


@responses.activate
def test_given_network_error_when_locking_then_returns_failed_result() -> None:
    responses.add(
        responses.GET,
        RESTRICTIONS_URL,
        body=requests.ConnectionError("connection timed out"),
    )

    result = BitbucketCloudOriginLock().lock(_unit(), _context())

    assert result.passed is False
    assert "connection timed out" in result.message


def test_given_missing_origin_token_when_locking_then_returns_failed_result() -> None:
    result = BitbucketCloudOriginLock().lock(_unit(), _context(origin_token=""))

    assert result.passed is False
    assert "ORIGIN_TOKEN" in result.message


def test_given_non_bitbucket_url_when_locking_then_returns_failed_result() -> None:
    result = BitbucketCloudOriginLock().lock(
        _unit(origin_url="https://dev.azure.com/org/proj/_git/repo"), _context()
    )

    assert result.passed is False
    assert "Bitbucket Cloud" in result.message


# --- BitbucketCloudOriginLock.unlock() tests ---


@responses.activate
def test_given_locked_bitbucket_repo_when_unlocking_then_deletes_push_branch_restriction() -> (
    None
):
    responses.add(
        responses.GET,
        RESTRICTIONS_URL,
        json={
            "values": [
                {"id": 42, "kind": "push", "pattern": "*", "users": [], "groups": []}
            ]
        },
        status=200,
    )
    responses.add(
        responses.DELETE,
        f"{RESTRICTIONS_URL}/42",
        status=204,
    )

    origin_lock = BitbucketCloudOriginLock()
    result = origin_lock.unlock(_unit(), _context())

    assert result.unit_name == "payments-api"
    assert result.passed is True
    assert result.lock_name == "bitbucket_cloud_origin_lock"
    assert result.already_in_target_state is False
    assert "unlocked" in result.message
    assert result.details == {
        "workspace": "acme-ws",
        "repo_slug": "payments-api",
        "push_restrictions_removed": 1,
    }
    assert (
        responses.calls[0].request.headers["Authorization"] == "Bearer bb-admin-token"
    )
    assert responses.calls[1].request.method == "DELETE"


@responses.activate
def test_given_origin_username_when_unlocking_then_uses_basic_auth() -> None:
    responses.add(
        responses.GET,
        RESTRICTIONS_URL,
        json={
            "values": [
                {"id": 42, "kind": "push", "pattern": "*", "users": [], "groups": []}
            ]
        },
        status=200,
    )
    responses.add(
        responses.DELETE,
        f"{RESTRICTIONS_URL}/42",
        status=204,
    )

    result = BitbucketCloudOriginLock().unlock(
        _unit(), _context(origin_token="app-pass", origin_username="bb-user")
    )

    assert result.passed is True
    assert result.already_in_target_state is False
    assert result.details["push_restrictions_removed"] == 1
    assert responses.calls[0].request.headers["Authorization"].startswith("Basic ")
    assert responses.calls[1].request.headers["Authorization"].startswith("Basic ")


@responses.activate
def test_given_mixed_branch_restrictions_when_unlocking_then_deletes_only_global_push_rule() -> (
    None
):
    responses.add(
        responses.GET,
        RESTRICTIONS_URL,
        json={
            "values": [
                {"id": 1, "kind": "push", "pattern": "main", "users": [], "groups": []},
                {"id": 2, "kind": "force", "pattern": "*", "users": [], "groups": []},
                {
                    "id": 3,
                    "kind": "push",
                    "pattern": "*",
                    "users": [{"uuid": "{user-1}"}],
                    "groups": [],
                },
                {
                    "id": 4,
                    "kind": "push",
                    "pattern": "*",
                    "users": [],
                    "groups": [{"slug": "admins"}],
                },
                {"id": 99, "kind": "push", "pattern": "*", "users": [], "groups": []},
            ]
        },
        status=200,
    )
    responses.add(
        responses.DELETE,
        f"{RESTRICTIONS_URL}/99",
        status=204,
    )

    result = BitbucketCloudOriginLock().unlock(_unit(), _context())

    assert result.passed is True
    assert result.already_in_target_state is False
    assert result.details["push_restrictions_removed"] == 1
    delete_calls = [c for c in responses.calls if c.request.method == "DELETE"]
    assert len(delete_calls) == 1
    assert delete_calls[0].request.url == f"{RESTRICTIONS_URL}/99"


@responses.activate
def test_given_paginated_matching_restrictions_when_unlocking_then_deletes_all_across_pages() -> (
    None
):
    page_2_url = f"{RESTRICTIONS_URL}?page=2"
    responses.add(
        responses.GET,
        RESTRICTIONS_URL,
        json={
            "values": [
                {"id": 10, "kind": "push", "pattern": "*", "users": [], "groups": []}
            ],
            "next": page_2_url,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        page_2_url,
        json={
            "values": [
                {"id": 20, "kind": "push", "pattern": "*", "users": [], "groups": []}
            ]
        },
        status=200,
    )
    responses.add(responses.DELETE, f"{RESTRICTIONS_URL}/10", status=204)
    responses.add(responses.DELETE, f"{RESTRICTIONS_URL}/20", status=204)

    result = BitbucketCloudOriginLock().unlock(_unit(), _context())

    assert result.passed is True
    assert result.already_in_target_state is False
    assert result.details["push_restrictions_removed"] == 2


@responses.activate
def test_given_unlocked_bitbucket_repo_when_unlocking_then_is_idempotent() -> None:
    responses.add(
        responses.GET,
        RESTRICTIONS_URL,
        json={
            "values": [
                {"id": 1, "kind": "push", "pattern": "main", "users": [], "groups": []}
            ]
        },
        status=200,
    )

    result = BitbucketCloudOriginLock().unlock(_unit(), _context())

    assert result.unit_name == "payments-api"
    assert result.passed is True
    assert result.lock_name == "bitbucket_cloud_origin_lock"
    assert result.already_in_target_state is True
    assert "already unlocked" in result.message
    assert result.details == {
        "workspace": "acme-ws",
        "repo_slug": "payments-api",
        "push_restrictions_removed": 0,
    }
    assert len(responses.calls) == 1


@responses.activate
def test_given_http_403_on_get_when_unlocking_then_returns_failed_result() -> None:
    responses.add(
        responses.GET,
        RESTRICTIONS_URL,
        json={"error": {"message": "Forbidden"}},
        status=403,
    )

    result = BitbucketCloudOriginLock().unlock(_unit(), _context())

    assert result.unit_name == "payments-api"
    assert result.passed is False
    assert result.lock_name == "bitbucket_cloud_origin_lock"
    assert result.already_in_target_state is False
    assert "403" in result.message


@responses.activate
def test_given_http_500_on_delete_when_unlocking_then_returns_failed_result() -> None:
    responses.add(
        responses.GET,
        RESTRICTIONS_URL,
        json={
            "values": [
                {"id": 42, "kind": "push", "pattern": "*", "users": [], "groups": []}
            ]
        },
        status=200,
    )
    responses.add(
        responses.DELETE,
        f"{RESTRICTIONS_URL}/42",
        json={"error": {"message": "Server error"}},
        status=500,
    )

    result = BitbucketCloudOriginLock().unlock(_unit(), _context())

    assert result.passed is False
    assert "500" in result.message
    assert result.details["push_restrictions_removed"] == 0


@responses.activate
def test_given_network_error_when_unlocking_then_returns_failed_result() -> None:
    responses.add(
        responses.GET,
        RESTRICTIONS_URL,
        body=requests.ConnectionError("connection reset"),
    )

    result = BitbucketCloudOriginLock().unlock(_unit(), _context())

    assert result.passed is False
    assert "connection reset" in result.message


def test_given_missing_origin_token_when_unlocking_then_returns_failed_result() -> None:
    result = BitbucketCloudOriginLock().unlock(_unit(), _context(origin_token=""))

    assert result.passed is False
    assert "ORIGIN_TOKEN" in result.message


def test_given_non_bitbucket_url_when_unlocking_then_returns_failed_result() -> None:
    result = BitbucketCloudOriginLock().unlock(
        _unit(origin_url="https://dev.azure.com/org/proj/_git/repo"), _context()
    )

    assert result.passed is False
    assert "Bitbucket Cloud" in result.message
