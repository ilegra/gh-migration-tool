"""Unit tests for gh_migration_tool.cli."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gh_migration_tool.cli import main
from gh_migration_tool.contracts import (
    MigrationUnit,
    UnitExecutionRecord,
    UnitStatus,
    WaveReport,
)


def _write_manifest(tmp_path: Path) -> Path:
    manifest = tmp_path / "wave.yaml"
    manifest.write_text(
        """
units:
  - name: repo-a
    origin_url: https://dev.azure.com/org/p/_git/repo-a
    destination_url: https://github.com/org/repo-a.git
""",
        encoding="utf-8",
    )
    return manifest


def _report(status: UnitStatus) -> WaveReport:
    now = datetime.now(timezone.utc)
    unit = MigrationUnit(
        name="repo-a",
        origin_url="https://dev.azure.com/org/p/_git/repo-a",
        destination_url="https://github.com/org/repo-a.git",
    )
    return WaveReport(
        started_at=now,
        finished_at=now,
        total_units=1,
        status_counts={s: (1 if s == status else 0) for s in UnitStatus},
        records=[UnitExecutionRecord(unit=unit, status=status)],
    )


def test_given_all_succeeded_wave_when_running_cli_with_json_out_then_writes_report_and_returns_0(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _write_manifest(tmp_path)
    out_file = tmp_path / "report.json"
    monkeypatch.setenv("GOOGLE_SPREADSHEET_ID", "sheet-123")
    mock_runner = MagicMock()
    mock_runner.run.return_value = _report(UnitStatus.SUCCEEDED)
    mock_inventory = MagicMock()

    with (
        patch("gh_migration_tool.cli.Inventory", return_value=mock_inventory),
        patch("gh_migration_tool.cli.WaveRunner", return_value=mock_runner),
    ):
        exit_code = main(
            ["run", str(manifest), "--format", "json", "--report-out", str(out_file)]
        )

    assert exit_code == 0
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["total_units"] == 1
    assert payload["status_counts"]["SUCCEEDED"] == 1


def test_given_failed_unit_in_wave_when_running_cli_then_returns_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _write_manifest(tmp_path)
    monkeypatch.setenv("GOOGLE_SPREADSHEET_ID", "sheet-123")
    mock_runner = MagicMock()
    mock_runner.run.return_value = _report(UnitStatus.MIGRATION_FAILED)

    with (
        patch("gh_migration_tool.cli.Inventory", return_value=MagicMock()),
        patch("gh_migration_tool.cli.WaveRunner", return_value=mock_runner),
    ):
        exit_code = main(["run", str(manifest), "--format", "markdown"])

    assert exit_code == 1


def test_given_origin_deactivator_flag_when_running_cli_then_wires_bitbucket_deactivator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _write_manifest(tmp_path)
    monkeypatch.setenv("GOOGLE_SPREADSHEET_ID", "sheet-123")
    mock_runner = MagicMock()
    mock_runner.run.return_value = _report(UnitStatus.SUCCEEDED)

    with (
        patch("gh_migration_tool.cli.Inventory", return_value=MagicMock()),
        patch(
            "gh_migration_tool.cli.WaveRunner", return_value=mock_runner
        ) as runner_cls,
    ):
        exit_code = main(
            ["run", str(manifest), "--origin-deactivator", "bitbucket-cloud"]
        )

    assert exit_code == 0
    deactivator = runner_cls.call_args.kwargs["origin_deactivator"]
    assert deactivator is not None
    assert deactivator.name == "bitbucket_cloud_origin_deactivator"


def test_given_configured_spreadsheet_id_when_running_cli_then_injects_inventory_client_into_wave_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _write_manifest(tmp_path)
    monkeypatch.setenv("GOOGLE_SPREADSHEET_ID", "sheet-123")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", "/path/to/creds.json")
    mock_inventory = MagicMock()
    mock_runner = MagicMock()
    mock_runner.run.return_value = _report(UnitStatus.SUCCEEDED)

    with (
        patch(
            "gh_migration_tool.cli.Inventory", return_value=mock_inventory
        ) as inventory_cls,
        patch(
            "gh_migration_tool.cli.WaveRunner", return_value=mock_runner
        ) as runner_cls,
    ):
        exit_code = main(["run", str(manifest)])

    assert exit_code == 0
    inventory_cls.assert_called_once_with(
        spreadsheet_id="sheet-123",
        credentials_path="/path/to/creds.json",
    )
    assert runner_cls.call_args.kwargs["inventory_client"] is mock_inventory


def test_given_missing_spreadsheet_id_when_running_cli_then_returns_1_without_running_wave(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _write_manifest(tmp_path)
    monkeypatch.setenv("GOOGLE_SPREADSHEET_ID", "")

    with (
        patch("gh_migration_tool.cli.Inventory") as inventory_cls,
        patch("gh_migration_tool.cli.WaveRunner") as runner_cls,
    ):
        exit_code = main(["run", str(manifest)])

    assert exit_code == 1
    inventory_cls.assert_not_called()
    runner_cls.assert_not_called()


def test_given_configured_github_app_when_running_cli_then_injects_token_provider_into_wave_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _write_manifest(tmp_path)
    monkeypatch.setenv("GOOGLE_SPREADSHEET_ID", "sheet-123")
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "67890")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "pem-key-content")
    monkeypatch.setenv("GITHUB_API_URL", "https://api.github.com")
    mock_token_provider = MagicMock()
    mock_runner = MagicMock()
    mock_runner.run.return_value = _report(UnitStatus.SUCCEEDED)

    with (
        patch("gh_migration_tool.cli.Inventory", return_value=MagicMock()),
        patch(
            "gh_migration_tool.cli.GitHubAppTokenProvider",
            return_value=mock_token_provider,
        ) as provider_cls,
        patch(
            "gh_migration_tool.cli.WaveRunner", return_value=mock_runner
        ) as runner_cls,
    ):
        exit_code = main(["run", str(manifest)])

    assert exit_code == 0
    provider_cls.assert_called_once_with(
        app_id="12345",
        installation_id="67890",
        private_key="pem-key-content",
        private_key_path=None,
        api_url="https://api.github.com",
        timeout=30.0,
    )
    assert runner_cls.call_args.kwargs["token_provider"] is mock_token_provider


def test_given_unconfigured_github_app_when_running_cli_then_passes_none_token_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _write_manifest(tmp_path)
    monkeypatch.setenv("GOOGLE_SPREADSHEET_ID", "sheet-123")
    monkeypatch.setenv("GITHUB_APP_ID", "")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "")
    mock_runner = MagicMock()
    mock_runner.run.return_value = _report(UnitStatus.SUCCEEDED)

    with (
        patch("gh_migration_tool.cli.Inventory", return_value=MagicMock()),
        patch("gh_migration_tool.cli.GitHubAppTokenProvider") as provider_cls,
        patch(
            "gh_migration_tool.cli.WaveRunner", return_value=mock_runner
        ) as runner_cls,
    ):
        exit_code = main(["run", str(manifest)])

    assert exit_code == 0
    provider_cls.assert_not_called()
    assert runner_cls.call_args.kwargs["token_provider"] is None
