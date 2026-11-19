"""Pure domain data containers for gh-migration-tool."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal

RepoVisibility = Literal["private", "internal", "public"]


class UnitStatus(str, Enum):
    """Canonical terminal statuses for a Migration Unit in a Migration Wave."""

    SUCCEEDED = "SUCCEEDED"
    PRE_CHECK_FAILED = "PRE_CHECK_FAILED"
    BLOCKED_BY_GROUP = "BLOCKED_BY_GROUP"
    GITHUB_SETUP_FAILED = "GITHUB_SETUP_FAILED"
    MIGRATION_FAILED = "MIGRATION_FAILED"
    PIPELINE_MIGRATION_FAILED = "PIPELINE_MIGRATION_FAILED"
    POST_CHECK_FAILED = "POST_CHECK_FAILED"
    INVENTORY_UPDATE_FAILED = "INVENTORY_UPDATE_FAILED"


@dataclass
class MigrationUnit:
    """Single source-to-GitHub migration mapping."""

    name: str
    origin_url: str
    destination_url: str
    project: str | None = None
    atomic_group: str | None = None
    origin_provider: str | None = None
    branch_mapping: dict[str, str] = field(default_factory=dict)
    origin_exclusive_paths: list[str] = field(default_factory=list)
    destination_exclusive_paths: list[str] = field(default_factory=list)
    visibility: RepoVisibility = "private"
    teams: dict[str, str] = field(default_factory=dict)
    rulesets: list[dict[str, Any]] = field(default_factory=list)
    jenkins_job: str | None = None
    github_workflow: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OriginSnapshot:
    """Baseline record of origin branch names and tip commit SHAs before Migration."""

    captured_at: datetime
    branch_heads: dict[str, str] = field(default_factory=dict)


@dataclass
class CheckResult:
    """Outcome of a single PreCheck or PostCheck execution."""

    check_name: str
    passed: bool
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SetupResult:
    """Outcome of GitHub Setup for a single Candidate."""

    unit_name: str
    passed: bool
    message: str = ""
    created: bool = False


@dataclass
class MigrationStageResult:
    """Outcome of the Migration stage for a single Candidate."""

    unit_name: str
    passed: bool
    origin_snapshot: OriginSnapshot | None = None
    evaluated_branches: list[str] = field(default_factory=list)
    updated_branches: list[str] = field(default_factory=list)
    removed_branches: list[str] = field(default_factory=list)
    synced_tags: list[str] = field(default_factory=list)
    already_disabled: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass
class PipelineMigrationResult:
    """Outcome of a single PipelineMigrator execution."""

    migrator_name: str
    passed: bool
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class InventoryUpdateResult:
    """Outcome of the Inventory Update stage for a single verified Migration Unit."""

    unit_name: str
    passed: bool
    target_status: str = ""
    message: str = ""


@dataclass
class UnitExecutionRecord:
    """Complete stage-by-stage execution record for one Migration Unit."""

    unit: MigrationUnit
    status: UnitStatus
    pre_checks: list[CheckResult] = field(default_factory=list)
    setup_result: SetupResult | None = None
    migration_result: MigrationStageResult | None = None
    pipeline_migrations: list[PipelineMigrationResult] = field(default_factory=list)
    post_checks: list[CheckResult] = field(default_factory=list)
    inventory_result: InventoryUpdateResult | None = None
    error_summary: str = ""


@dataclass
class WaveReport:
    """Consolidated Migration Report for a Migration Wave."""

    started_at: datetime
    finished_at: datetime
    total_units: int
    status_counts: dict[UnitStatus, int] = field(default_factory=dict)
    records: list[UnitExecutionRecord] = field(default_factory=list)
