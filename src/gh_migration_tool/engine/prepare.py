"""Step 2: Core Prepare stage for Candidate filtering and Atomic Group gating."""

from __future__ import annotations

import logging

from gh_migration_tool.contracts import (
    CheckResult,
    MigrationUnit,
    UnitExecutionRecord,
    UnitStatus,
)

logger = logging.getLogger(__name__)


def prepare_candidates(
    units: list[MigrationUnit],
    pre_check_results: dict[str, list[CheckResult]],
) -> tuple[list[MigrationUnit], dict[str, UnitExecutionRecord]]:
    """Filter Pre-check outcomes into approved Candidates and blocked records.

    Units that fail any Pre-check are marked PRE_CHECK_FAILED.
    Healthy units sharing an atomic_group with a failed unit are marked
    BLOCKED_BY_GROUP.
    """
    failed_unit_names: set[str] = set()
    failed_groups: set[str] = set()

    for unit in units:
        checks = pre_check_results.get(unit.name, [])
        if any(not check.passed for check in checks):
            failed_unit_names.add(unit.name)
            if unit.atomic_group:
                failed_groups.add(unit.atomic_group)

    candidates: list[MigrationUnit] = []
    records: dict[str, UnitExecutionRecord] = {}

    for unit in units:
        checks = pre_check_results.get(unit.name, [])
        if unit.name in failed_unit_names:
            failed_msgs = [c.message for c in checks if not c.passed and c.message]
            records[unit.name] = UnitExecutionRecord(
                unit=unit,
                status=UnitStatus.PRE_CHECK_FAILED,
                pre_checks=checks,
                error_summary="; ".join(failed_msgs) or "Failed pre-checks.",
            )
        elif unit.atomic_group and unit.atomic_group in failed_groups:
            records[unit.name] = UnitExecutionRecord(
                unit=unit,
                status=UnitStatus.BLOCKED_BY_GROUP,
                pre_checks=checks,
                error_summary=(
                    f"Blocked because atomic_group '{unit.atomic_group}' "
                    "had pre-check failures."
                ),
            )
        else:
            candidates.append(unit)
            records[unit.name] = UnitExecutionRecord(
                unit=unit,
                status=UnitStatus.SUCCEEDED,
                pre_checks=checks,
            )

    logger.info(
        "Prepare stage complete: %d/%d units approved as Candidates",
        len(candidates),
        len(units),
    )
    return candidates, records
