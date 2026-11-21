"""Public SDK contracts: consumer-facing interfaces, context, and domain models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from gh_migration_tool.settings import MigrationSettings

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
    ORIGIN_DEACTIVATION_FAILED = "ORIGIN_DEACTIVATION_FAILED"
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
    deactivate_origin: bool = True
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
class OriginDeactivationResult:
    """Outcome of the Origin Deactivation stage for a single Migration Unit."""

    unit_name: str
    passed: bool
    deactivator_name: str = ""
    already_disabled: bool = False
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
    origin_deactivation_result: OriginDeactivationResult | None = None
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


class WaveContext:
    """Shared runtime context passed to extensible stage implementations."""

    def __init__(
        self,
        settings: MigrationSettings,
        github_token_provider: TokenProvider | None = None,
    ) -> None:
        self.settings = settings
        self.github_token_provider = github_token_provider


class WaveLoader(ABC):
    """Abstract interface for loading Migration Units from a source."""

    @abstractmethod
    def load(self, source: Path) -> list[MigrationUnit]:
        """Load and validate Migration Units from the given source."""


class PreCheck(ABC):
    """Extensible interface for Step 1 (Pre-checks)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable identifier for this Pre-check."""

    @abstractmethod
    def execute(self, unit: MigrationUnit, context: WaveContext) -> CheckResult:
        """Execute the Pre-check for a single Migration Unit."""


class GitHubSetup(ABC):
    """Extensible interface for Step 3 (GitHub Setup)."""

    def setup_wave(
        self, candidates: list[MigrationUnit], context: WaveContext
    ) -> dict[str, SetupResult]:
        """Prepare GitHub for all Candidates, defaulting to parallel per-unit setup.

        Subclasses performing bulk IaC provisioning may override this method
        to operate on the full batch of Candidates at once.
        """
        from gh_migration_tool.engine._concurrency import run_parallel_by_unit

        def _run_unit(unit: MigrationUnit) -> SetupResult:
            try:
                return self.setup_unit(unit, context)
            except Exception as exc:
                return SetupResult(
                    unit_name=unit.name,
                    passed=False,
                    message=str(exc),
                )

        return run_parallel_by_unit(candidates, _run_unit, context.settings.max_workers)

    @abstractmethod
    def setup_unit(self, unit: MigrationUnit, context: WaveContext) -> SetupResult:
        """Prepare GitHub for a single Candidate."""


class Migrator(ABC):
    """Extensible interface for Step 4 (Migration)."""

    @abstractmethod
    def migrate(
        self, unit: MigrationUnit, context: WaveContext
    ) -> MigrationStageResult:
        """Synchronize the Migration Unit from Origin to GitHub."""


class PipelineMigrator(ABC):
    """Extensible interface for optional post-migration hooks before Post-checks."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable identifier for this PipelineMigrator."""

    @abstractmethod
    def execute(
        self,
        unit: MigrationUnit,
        migration_result: MigrationStageResult,
        context: WaveContext,
    ) -> PipelineMigrationResult:
        """Execute a mutating post-migration step for a single Migration Unit."""


class PostCheck(ABC):
    """Extensible interface for Step 5 (Post-checks)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable identifier for this Post-check."""

    @abstractmethod
    def execute(
        self,
        unit: MigrationUnit,
        migration_result: MigrationStageResult,
        context: WaveContext,
    ) -> CheckResult:
        """Execute the Post-check for a single migrated Migration Unit."""


class OriginDeactivator(ABC):
    """Extensible interface for optional Step 5.5 (Origin Deactivation)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable identifier for this OriginDeactivator."""

    @abstractmethod
    def deactivate(
        self, unit: MigrationUnit, context: WaveContext
    ) -> OriginDeactivationResult:
        """Deactivate or freeze the Origin repository for a verified unit."""


@runtime_checkable
class InventoryClient(Protocol):
    """Structural contract for Stage 6 inventory persistence clients."""

    def update_progress(
        self,
        repository_name: str,
        status: str,
        project: str | None = None,
    ) -> Any:
        """Update the migration status for a repository in the inventory."""


@runtime_checkable
class TokenProvider(Protocol):
    """Structural contract for GitHub authentication token providers."""

    def get_token(self) -> str:
        """Return a valid GitHub access token."""
