"""Extensible SDK interfaces for variable workflow stages."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, TypeVar

from gh_migration_tool.models import (
    CheckResult,
    MigrationStageResult,
    MigrationUnit,
    OriginDeactivationResult,
    PipelineMigrationResult,
    SetupResult,
)

if TYPE_CHECKING:
    from gh_migration_tool.auth.github_app import GitHubAppTokenProvider
    from gh_migration_tool.settings import MigrationSettings

_T = TypeVar("_T")


def run_parallel_by_unit(
    units: list[MigrationUnit],
    fn: Callable[[MigrationUnit], _T],
    max_workers: int,
    *,
    executor: ThreadPoolExecutor | None = None,
) -> dict[str, _T]:
    """Execute fn concurrently for each MigrationUnit and map results by unit name."""
    if not units:
        return {}
    if executor is None:
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as own_executor:
            return run_parallel_by_unit(units, fn, max_workers, executor=own_executor)
    future_to_name = {executor.submit(fn, unit): unit.name for unit in units}
    return {
        future_to_name[future]: future.result()
        for future in as_completed(future_to_name)
    }


class WaveContext:
    """Shared runtime context passed to extensible stage implementations."""

    def __init__(
        self,
        settings: MigrationSettings,
        github_token_provider: GitHubAppTokenProvider | None = None,
    ) -> None:
        self.settings = settings
        self.github_token_provider = github_token_provider


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
