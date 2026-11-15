"""Unit tests for gh_migration_tool.cli."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from gh_migration_tool.cli import main
from gh_migration_tool.models import (
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
) -> None:
    manifest = _write_manifest(tmp_path)
    out_file = tmp_path / "report.json"
    mock_runner = MagicMock()
    mock_runner.run.return_value = _report(UnitStatus.SUCCEEDED)

    with patch("gh_migration_tool.cli.WaveRunner", return_value=mock_runner):
        exit_code = main(
            ["run", str(manifest), "--format", "json", "--report-out", str(out_file)]
        )

    assert exit_code == 0
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["total_units"] == 1
    assert payload["status_counts"]["SUCCEEDED"] == 1


def test_given_failed_unit_in_wave_when_running_cli_then_returns_1(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(tmp_path)
    mock_runner = MagicMock()
    mock_runner.run.return_value = _report(UnitStatus.MIGRATION_FAILED)

    with patch("gh_migration_tool.cli.WaveRunner", return_value=mock_runner):
        exit_code = main(["run", str(manifest), "--format", "markdown"])

    assert exit_code == 1
