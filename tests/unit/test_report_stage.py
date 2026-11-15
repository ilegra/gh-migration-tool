"""Unit tests for Step 7 Migration Report stage."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from gh_migration_tool.models import (
    MigrationUnit,
    UnitExecutionRecord,
    UnitStatus,
)
from gh_migration_tool.stages.report import ReportFormatter, build_wave_report


def test_given_execution_records_when_building_and_formatting_report_then_renders_all_formats() -> (
    None
):
    unit = MigrationUnit(
        name="service-a",
        origin_url="https://dev.azure.com/org/p/_git/service-a",
        destination_url="https://github.com/org/service-a.git",
    )
    now = datetime.now(timezone.utc)
    report = build_wave_report(
        started_at=now,
        finished_at=now,
        records=[UnitExecutionRecord(unit=unit, status=UnitStatus.SUCCEEDED)],
    )

    md = ReportFormatter.to_markdown(report)
    js = json.loads(ReportFormatter.to_json(report))
    console = ReportFormatter.to_console(report)

    assert "service-a" in md
    assert "`SUCCEEDED`: 1" in md
    assert js["status_counts"]["SUCCEEDED"] == 1
    assert "[SUCCEEDED] service-a" in console
