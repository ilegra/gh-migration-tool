"""Extensible SDK interfaces for variable workflow stages."""

from __future__ import annotations

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from gh_migration_tool.models import (
    CheckResult,
    MigrationStageResult,
    MigrationUnit,
    SetupResult,
)

if TYPE_CHECKING:
    from gh_migration_tool.auth.github_app import GitHubAppTokenProvider
    from gh_migration_tool.settings import MigrationSettings


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
        results: dict[str, SetupResult] = {}
        if not candidates:
            return results

        max_workers = max(1, context.settings.max_workers)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_name = {
                executor.submit(self.setup_unit, unit, context): unit.name
                for unit in candidates
            }
            for future in as_completed(future_to_name):
                unit_name = future_to_name[future]
                try:
                    results[unit_name] = future.result()
                except Exception as exc:
                    results[unit_name] = SetupResult(
                        unit_name=unit_name,
                        passed=False,
                        message=str(exc),
                    )
        return results

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
