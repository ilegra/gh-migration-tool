"""Step 7: Core Migration Report builder and formatters."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime

from gh_migration_tool.models import (
    UnitExecutionRecord,
    UnitStatus,
    WaveReport,
)


def build_wave_report(
    started_at: datetime,
    finished_at: datetime,
    records: list[UnitExecutionRecord],
) -> WaveReport:
    """Aggregate execution records into a WaveReport."""
    counts: dict[UnitStatus, int] = {status: 0 for status in UnitStatus}
    for record in records:
        counts[record.status] = counts.get(record.status, 0) + 1

    return WaveReport(
        started_at=started_at,
        finished_at=finished_at,
        total_units=len(records),
        status_counts=counts,
        records=records,
    )


class ReportFormatter:
    """Formats a WaveReport into Markdown, JSON, or Console text."""

    @staticmethod
    def to_markdown(report: WaveReport) -> str:
        """Render WaveReport as a Markdown summary."""
        lines = [
            "# Migration Wave Report",
            "",
            f"- **Started**: {report.started_at.isoformat()}",
            f"- **Finished**: {report.finished_at.isoformat()}",
            f"- **Total Units**: {report.total_units}",
            "",
            "## Status Summary",
            "",
        ]
        for status in UnitStatus:
            lines.append(f"- `{status.value}`: {report.status_counts.get(status, 0)}")

        lines.extend(
            [
                "",
                "## Unit Details",
                "",
                "| Unit | Status | Error Summary |",
                "| :--- | :--- | :--- |",
            ]
        )
        for rec in report.records:
            err = rec.error_summary.replace("\n", " ").replace("|", "\\|")
            lines.append(f"| {rec.unit.name} | `{rec.status.value}` | {err or '—'} |")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def to_json(report: WaveReport) -> str:
        """Render WaveReport as a JSON string."""
        payload = asdict(report)
        payload["started_at"] = report.started_at.isoformat()
        payload["finished_at"] = report.finished_at.isoformat()
        payload["status_counts"] = {k.value: v for k, v in report.status_counts.items()}
        for rec in payload["records"]:
            rec["status"] = rec["status"].value
            mig = rec.get("migration_result")
            if mig and mig.get("origin_snapshot"):
                mig["origin_snapshot"]["captured_at"] = mig["origin_snapshot"][
                    "captured_at"
                ].isoformat()
        return json.dumps(payload, indent=2)

    @staticmethod
    def to_console(report: WaveReport) -> str:
        """Render WaveReport as a concise console summary."""
        lines = [
            f"Migration Wave Complete ({report.total_units} units)",
            "-" * 50,
        ]
        for status in UnitStatus:
            count = report.status_counts.get(status, 0)
            if count > 0:
                lines.append(f"  {status.value}: {count}")
        lines.append("-" * 50)
        for rec in report.records:
            suffix = f" - {rec.error_summary}" if rec.error_summary else ""
            lines.append(f"  [{rec.status.value}] {rec.unit.name}{suffix}")
        return "\n".join(lines)
