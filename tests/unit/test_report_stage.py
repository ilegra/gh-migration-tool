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
    unit_ok = MigrationUnit(
        name="service-a",
        origin_url="https://dev.azure.com/org/p/_git/service-a",
        destination_url="https://github.com/org/service-a.git",
    )
    unit_fail = MigrationUnit(
        name="service-b",
        origin_url="https://dev.azure.com/org/p/_git/service-b",
        destination_url="https://github.com/org/service-b.git",
    )
    started = datetime(2026, 11, 15, 10, 0, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 11, 15, 10, 5, 0, tzinfo=timezone.utc)
    records = [
        UnitExecutionRecord(unit=unit_ok, status=UnitStatus.SUCCEEDED),
        UnitExecutionRecord(
            unit=unit_fail,
            status=UnitStatus.MIGRATION_FAILED,
            error_summary="sync error",
        ),
    ]

    report = build_wave_report(
        started_at=started, finished_at=finished, records=records
    )

    assert report.started_at == started
    assert report.finished_at == finished
    assert report.total_units == 2
    assert report.records == records
    assert report.status_counts == {
        UnitStatus.SUCCEEDED: 1,
        UnitStatus.PRE_CHECK_FAILED: 0,
        UnitStatus.BLOCKED_BY_GROUP: 0,
        UnitStatus.GITHUB_SETUP_FAILED: 0,
        UnitStatus.MIGRATION_FAILED: 1,
        UnitStatus.PIPELINE_MIGRATION_FAILED: 0,
        UnitStatus.POST_CHECK_FAILED: 0,
        UnitStatus.INVENTORY_UPDATE_FAILED: 0,
    }

    md = ReportFormatter.to_markdown(report)
    js = json.loads(ReportFormatter.to_json(report))
    console = ReportFormatter.to_console(report)

    assert "service-a" in md
    assert "`SUCCEEDED`: 1" in md
    assert "`MIGRATION_FAILED`: 1" in md
    assert js["total_units"] == 2
    assert js["status_counts"]["SUCCEEDED"] == 1
    assert js["status_counts"]["MIGRATION_FAILED"] == 1
    assert "[SUCCEEDED] service-a" in console
    assert "[MIGRATION_FAILED] service-b - sync error" in console
