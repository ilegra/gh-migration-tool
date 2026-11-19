"""Unit tests for Step 4 GitSyncMigrator and origin snapshot helpers."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from gh_migration_tool.interfaces import WaveContext
from gh_migration_tool.models import MigrationUnit
from gh_migration_tool.settings import MigrationSettings
from gh_migration_tool.stages.migration import (
    GitSyncMigrator,
    build_authenticated_origin_url,
    capture_origin_branch_heads,
)


def _unit() -> MigrationUnit:
    return MigrationUnit(
        name="service-a",
        origin_url="https://dev.azure.com/org/proj/_git/service-a",
        destination_url="https://github.com/org/service-a.git",
        branch_mapping={"master": "main"},
        origin_exclusive_paths=["azure-pipelines.yml"],
        destination_exclusive_paths=[".github"],
    )


def _context(has_token_provider: bool = True) -> WaveContext:
    provider = MagicMock()
    provider.get_token.return_value = "ghs_mock_token"
    return WaveContext(
        settings=MigrationSettings(ORIGIN_TOKEN="ado_tok"),
        github_token_provider=provider if has_token_provider else None,
    )


def test_given_https_origin_url_when_building_authenticated_url_then_embeds_credentials() -> (
    None
):
    url = build_authenticated_origin_url(
        "https://dev.azure.com/org/proj/_git/repo",
        token="secret",
        username="user",
    )

    assert url == "https://user:secret@dev.azure.com/org/proj/_git/repo"


def test_given_bitbucket_provider_and_no_username_when_building_authenticated_url_then_uses_x_token_auth() -> (
    None
):
    for provider in ("bitbucket", "bitbucket_cloud", "bitbucket-cloud", " Bitbucket "):
        url = build_authenticated_origin_url(
            "https://git.internal.example/ws/repo.git",
            token="bb_tok",
            username=None,
            origin_provider=provider,
        )
        assert url == "https://x-token-auth:bb_tok@git.internal.example/ws/repo.git"


def test_given_bitbucket_url_and_no_provider_or_username_when_building_authenticated_url_then_uses_x_token_auth() -> (
    None
):
    url = build_authenticated_origin_url(
        "https://bitbucket.org/ws/repo.git",
        token="bb_tok",
    )

    assert url == "https://x-token-auth:bb_tok@bitbucket.org/ws/repo.git"


def test_given_bitbucket_provider_and_explicit_username_when_building_authenticated_url_then_uses_explicit_username() -> (
    None
):
    url = build_authenticated_origin_url(
        "https://bitbucket.org/ws/repo.git",
        token="bb_tok",
        username="bb_user",
        origin_provider="bitbucket",
    )

    assert url == "https://bb_user:bb_tok@bitbucket.org/ws/repo.git"


def test_given_non_bitbucket_provider_and_bitbucket_url_when_building_authenticated_url_then_uses_x_access_token() -> (
    None
):
    url = build_authenticated_origin_url(
        "https://bitbucket.org/ws/repo.git",
        token="ado_tok",
        username=None,
        origin_provider="ado",
    )

    assert url == "https://x-access-token:ado_tok@bitbucket.org/ws/repo.git"


def test_given_bitbucket_provider_when_capturing_heads_then_passes_x_token_auth_url_to_git() -> (
    None
):
    fake_proc = SimpleNamespace(
        returncode=0,
        stdout="abc123\trefs/heads/main\n",
        stderr="",
    )
    with patch(
        "gh_migration_tool.stages.migration.subprocess.run", return_value=fake_proc
    ) as mock_run:
        heads = capture_origin_branch_heads(
            "https://bitbucket.org/ws/repo.git",
            "bb_tok",
            username=None,
            origin_provider="bitbucket",
        )

    assert heads == {"main": "abc123"}
    cmd = mock_run.call_args[0][0]
    assert cmd[1:] == [
        "ls-remote",
        "--heads",
        "https://x-token-auth:bb_tok@bitbucket.org/ws/repo.git",
    ]


def test_given_git_ls_remote_output_when_capturing_heads_then_returns_branch_sha_map() -> (
    None
):
    fake_proc = SimpleNamespace(
        returncode=0,
        stdout="abc123\trefs/heads/main\ndef456\trefs/heads/feat/x\n",
        stderr="",
    )
    with patch(
        "gh_migration_tool.stages.migration.subprocess.run", return_value=fake_proc
    ):
        heads = capture_origin_branch_heads("https://example.com/repo.git", "tok")

    assert heads == {"main": "abc123", "feat/x": "def456"}


def test_given_failing_git_ls_remote_when_capturing_heads_then_masks_token_and_raises() -> (
    None
):
    fake_proc = SimpleNamespace(
        returncode=128,
        stdout="",
        stderr="fatal: auth failed for secret_tok",
    )
    with patch(
        "gh_migration_tool.stages.migration.subprocess.run", return_value=fake_proc
    ):
        try:
            capture_origin_branch_heads("https://example.com/repo.git", "secret_tok")
            raised = False
        except RuntimeError as exc:
            raised = True
            assert "secret_tok" not in str(exc)
            assert "***" in str(exc)
        assert raised is True


def test_given_missing_token_provider_when_migrating_then_returns_failed_result() -> (
    None
):
    res = GitSyncMigrator().migrate(_unit(), _context(has_token_provider=False))

    assert res.unit_name == "service-a"
    assert res.passed is False
    assert res.origin_snapshot is None
    assert res.errors == ["GitHubAppTokenProvider is not configured."]


def test_given_successful_sync_manager_when_migrating_then_returns_snapshot_and_metrics() -> (
    None
):
    fake_sync_result = SimpleNamespace(
        already_disabled=False,
        errors=[],
        evaluated_branches=["main"],
        updated_branches=["main"],
        removed_branches=[],
        synced_tags=["v1.0.0"],
    )
    mock_manager = MagicMock()
    mock_manager.execute.return_value = fake_sync_result

    with (
        patch(
            "gh_migration_tool.stages.migration.capture_origin_branch_heads",
            return_value={"main": "sha1"},
        ) as mock_capture,
        patch("gh_migration_tool.stages.migration.Git"),
        patch("gh_migration_tool.stages.migration.get_origin_provider"),
        patch("gh_migration_tool.stages.migration.GitHubProvider"),
        patch(
            "gh_migration_tool.stages.migration.SyncManager",
            return_value=mock_manager,
        ),
    ):
        unit = _unit()
        res = GitSyncMigrator().migrate(unit, _context())

    mock_capture.assert_called_once_with(
        unit.origin_url,
        "ado_tok",
        None,
        unit.origin_provider,
    )

    assert res.unit_name == "service-a"
    assert res.passed is True
    assert res.origin_snapshot is not None
    assert res.origin_snapshot.branch_heads == {"main": "sha1"}
    assert res.evaluated_branches == ["main"]
    assert res.updated_branches == ["main"]
    assert res.removed_branches == []
    assert res.synced_tags == ["v1.0.0"]
    assert res.already_disabled is False
    assert res.errors == []


def test_given_disabled_origin_repo_when_migrating_then_returns_failed_result() -> None:
    fake_sync_result = SimpleNamespace(
        already_disabled=True,
        errors=[],
        evaluated_branches=[],
        updated_branches=[],
        removed_branches=[],
        synced_tags=[],
    )
    mock_manager = MagicMock()
    mock_manager.execute.return_value = fake_sync_result

    with (
        patch(
            "gh_migration_tool.stages.migration.capture_origin_branch_heads",
            return_value={"main": "sha1"},
        ) as mock_capture,
        patch("gh_migration_tool.stages.migration.Git"),
        patch("gh_migration_tool.stages.migration.get_origin_provider"),
        patch("gh_migration_tool.stages.migration.GitHubProvider"),
        patch(
            "gh_migration_tool.stages.migration.SyncManager",
            return_value=mock_manager,
        ),
    ):
        unit = _unit()
        res = GitSyncMigrator().migrate(unit, _context())

    mock_capture.assert_called_once_with(
        unit.origin_url,
        "ado_tok",
        None,
        unit.origin_provider,
    )

    assert res.unit_name == "service-a"
    assert res.passed is False
    assert res.already_disabled is True
    assert "Origin repository is disabled or archived." in res.errors


def test_given_snapshot_subprocess_error_when_migrating_then_returns_failed_result() -> (
    None
):
    with patch(
        "gh_migration_tool.stages.migration.capture_origin_branch_heads",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=60),
    ):
        res = GitSyncMigrator().migrate(_unit(), _context())

    assert res.unit_name == "service-a"
    assert res.passed is False
    assert res.origin_snapshot is None
    assert len(res.errors) == 1
    assert "Origin snapshot failed" in res.errors[0]
