"""7-stage stateless WaveRunner orchestrating sequential barriers and parallel units."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from gh_migration_tool.contracts import (
    CheckResult,
    GitHubSetup,
    InventoryClient,
    MigrationStageResult,
    MigrationUnit,
    Migrator,
    OriginLock,
    OriginLockResult,
    PipelineMigrationResult,
    PipelineMigrator,
    PostCheck,
    PreCheck,
    TokenProvider,
    UnitExecutionRecord,
    UnitStatus,
    WaveContext,
    WaveReport,
)
from gh_migration_tool.destinations.github import GitHubApiSetup
from gh_migration_tool.engine._concurrency import run_parallel_by_unit
from gh_migration_tool.engine.git_sync import GitSyncMigrator, SourceDriftPostCheck
from gh_migration_tool.engine.inventory import update_inventory
from gh_migration_tool.engine.prepare import prepare_candidates
from gh_migration_tool.engine.report import build_wave_report
from gh_migration_tool.origins.jenkins import JenkinsJobPreCheck
from gh_migration_tool.settings import MigrationSettings

logger = logging.getLogger(__name__)


class WaveRunner:
    """Orchestrates a Migration Wave across all 7 stages."""

    def __init__(
        self,
        settings: MigrationSettings,
        inventory_client: InventoryClient,
        pre_checks: list[PreCheck] | None = None,
        github_setup: GitHubSetup | None = None,
        migrator: Migrator | None = None,
        post_checks: list[PostCheck] | None = None,
        token_provider: TokenProvider | None = None,
        pipeline_migrators: Sequence[PipelineMigrator] | None = None,
        origin_lock: OriginLock | None = None,
    ) -> None:
        if inventory_client is None:
            raise ValueError(
                "inventory_client must be provided via dependency injection."
            )
        self.settings = settings
        self.pre_checks: list[PreCheck] = (
            pre_checks if pre_checks is not None else [JenkinsJobPreCheck()]
        )
        self.github_setup: GitHubSetup = github_setup or GitHubApiSetup()
        self.migrator: Migrator = migrator or GitSyncMigrator()
        self.post_checks: list[PostCheck] = (
            post_checks if post_checks is not None else [SourceDriftPostCheck()]
        )
        self.pipeline_migrators: list[PipelineMigrator] = (
            list(pipeline_migrators) if pipeline_migrators is not None else []
        )
        self.origin_lock: OriginLock | None = origin_lock
        self._token_provider: TokenProvider | None = token_provider
        self._inventory_client: InventoryClient = inventory_client

    def run(self, units: list[MigrationUnit]) -> WaveReport:
        """Execute the 7-stage pipeline for the provided Migration Units."""
        started_at = datetime.now(timezone.utc)
        context = WaveContext(
            settings=self.settings, github_token_provider=self._token_provider
        )
        max_workers = max(1, self.settings.max_workers)

        logger.info("Starting Migration Wave with %d units", len(units))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Step 1: Pre-checks (parallel per unit & check)
            pre_results = self._run_pre_checks(units, context, executor)

            # Step 2: Prepare (core stage)
            candidates, records = prepare_candidates(units, pre_results)

            # Step 3: GitHub Setup (wave/per-candidate)
            setup_results = self.github_setup.setup_wave(candidates, context)
            setup_passed_units: list[MigrationUnit] = []
            for unit in candidates:
                s_res = setup_results.get(unit.name)
                records[unit.name].setup_result = s_res
                if s_res is not None and s_res.passed:
                    setup_passed_units.append(unit)
                else:
                    records[unit.name].status = UnitStatus.GITHUB_SETUP_FAILED
                    records[unit.name].error_summary = (
                        s_res.message if s_res else "GitHub setup returned no result."
                    )

            # Step 4: Migration (parallel per unit)
            mig_results = self._run_migrations(
                setup_passed_units, context, max_workers, executor
            )
            migrated_units: list[MigrationUnit] = []
            for unit in setup_passed_units:
                m_res = mig_results[unit.name]
                records[unit.name].migration_result = m_res
                if m_res.passed:
                    migrated_units.append(unit)
                else:
                    records[unit.name].status = UnitStatus.MIGRATION_FAILED
                    records[unit.name].error_summary = (
                        "; ".join(m_res.errors) or "Migration failed."
                    )

            # Step 4.5: Optional PipelineMigrator hooks (sequential per unit)
            pipeline_ready_units = migrated_units
            if self.pipeline_migrators and migrated_units:
                hook_results = self._run_pipeline_migrators(
                    migrated_units, mig_results, context, max_workers, executor
                )
                pipeline_ready_units = []
                for unit in migrated_units:
                    unit_hooks = hook_results.get(unit.name, [])
                    records[unit.name].pipeline_migrations = unit_hooks
                    if all(h.passed for h in unit_hooks):
                        pipeline_ready_units.append(unit)
                    else:
                        failed_msgs = [
                            h.message for h in unit_hooks if not h.passed and h.message
                        ]
                        records[unit.name].status = UnitStatus.PIPELINE_MIGRATION_FAILED
                        records[unit.name].error_summary = (
                            "; ".join(failed_msgs) or "Pipeline migration failed."
                        )

            # Step 5: Post-checks (parallel per unit & check)
            post_results = self._run_post_checks(
                pipeline_ready_units, mig_results, context, executor
            )
            verified_units: list[MigrationUnit] = []
            for unit in pipeline_ready_units:
                checks = post_results.get(unit.name, [])
                records[unit.name].post_checks = checks
                if all(c.passed for c in checks):
                    verified_units.append(unit)
                else:
                    failed_msgs = [
                        c.message for c in checks if not c.passed and c.message
                    ]
                    records[unit.name].status = UnitStatus.POST_CHECK_FAILED
                    records[unit.name].error_summary = (
                        "; ".join(failed_msgs) or "Post-check failed."
                    )

            # Step 5.5: Optional Origin Lock (parallel per unit)
            inventory_units = verified_units
            newly_locked_unit_names: set[str] = set()
            if self.origin_lock is not None and verified_units:
                to_lock = [u for u in verified_units if u.lock_origin]
                lock_results = self._run_origin_lock(
                    to_lock, context, max_workers, executor
                )
                inventory_units = []
                for unit in verified_units:
                    if not unit.lock_origin:
                        inventory_units.append(unit)
                        continue
                    l_res = lock_results.get(unit.name)
                    records[unit.name].origin_lock_result = l_res
                    if l_res is not None and l_res.passed:
                        inventory_units.append(unit)
                        if not l_res.already_in_target_state:
                            newly_locked_unit_names.add(unit.name)
                    else:
                        records[unit.name].status = UnitStatus.ORIGIN_LOCK_FAILED
                        records[unit.name].error_summary = (
                            l_res.message
                            if l_res and l_res.message
                            else "Origin lock failed."
                        )

        # Step 6: Inventory Update (core stage)
        inv_results = update_inventory(
            inventory_units, self.settings, inventory_client=self._inventory_client
        )
        rollback_units: list[MigrationUnit] = []
        for unit in inventory_units:
            i_res = inv_results.get(unit.name)
            records[unit.name].inventory_result = i_res
            if i_res is None or not i_res.passed:
                records[unit.name].status = UnitStatus.INVENTORY_UPDATE_FAILED
                records[unit.name].error_summary = (
                    i_res.message if i_res else "Inventory update returned no result."
                )
                if unit.name in newly_locked_unit_names:
                    rollback_units.append(unit)

        if self.origin_lock is not None and rollback_units:
            logger.info(
                "Rolling back origin lock for %d unit(s) after inventory update "
                "failure",
                len(rollback_units),
            )
            unlock_results = self._run_origin_unlock(
                rollback_units, context, max_workers
            )
            for unit in rollback_units:
                u_res = unlock_results.get(unit.name)
                records[unit.name].origin_unlock_result = u_res
                if u_res is None or not u_res.passed:
                    rollback_err = (
                        u_res.message
                        if u_res and u_res.message
                        else "Origin unlock rollback failed."
                    )
                    logger.error(
                        "Origin lock rollback failed for unit '%s': %s",
                        unit.name,
                        rollback_err,
                    )
                    records[unit.name].error_summary = (
                        f"{records[unit.name].error_summary}; "
                        f"Rollback failed: {rollback_err}"
                    )

        # Step 7: Migration Report (core stage)
        ordered_records: list[UnitExecutionRecord] = [records[u.name] for u in units]
        finished_at = datetime.now(timezone.utc)
        return build_wave_report(started_at, finished_at, ordered_records)

    @staticmethod
    def _run_unit_checks(
        units: list[MigrationUnit],
        checks: Sequence[PreCheck | PostCheck],
        invoke: Callable[[Any, MigrationUnit], CheckResult],
        executor: ThreadPoolExecutor,
    ) -> dict[str, list[CheckResult]]:
        results: dict[str, list[CheckResult]] = {u.name: [] for u in units}
        if not units or not checks:
            return results

        ordered: dict[str, list[CheckResult | None]] = {
            u.name: [None] * len(checks) for u in units
        }

        def _safe_execute(check: Any, unit: MigrationUnit) -> CheckResult:
            try:
                return invoke(check, unit)
            except Exception as exc:
                return CheckResult(
                    check_name=check.name,
                    passed=False,
                    message=str(exc),
                )

        future_to_key = {
            executor.submit(_safe_execute, check, unit): (unit.name, idx)
            for unit in units
            for idx, check in enumerate(checks)
        }
        for future in as_completed(future_to_key):
            unit_name, idx = future_to_key[future]
            ordered[unit_name][idx] = future.result()

        for unit_name, unit_checks in ordered.items():
            results[unit_name] = [c for c in unit_checks if c is not None]
        return results

    def _run_pre_checks(
        self,
        units: list[MigrationUnit],
        context: WaveContext,
        executor: ThreadPoolExecutor,
    ) -> dict[str, list[CheckResult]]:
        return self._run_unit_checks(
            units,
            self.pre_checks,
            lambda check, unit: check.execute(unit, context),
            executor,
        )

    def _run_migrations(
        self,
        units: list[MigrationUnit],
        context: WaveContext,
        max_workers: int,
        executor: ThreadPoolExecutor,
    ) -> dict[str, MigrationStageResult]:
        def _migrate_unit(unit: MigrationUnit) -> MigrationStageResult:
            try:
                return self.migrator.migrate(unit, context)
            except Exception as exc:
                return MigrationStageResult(
                    unit_name=unit.name,
                    passed=False,
                    errors=[str(exc)],
                )

        return run_parallel_by_unit(
            units, _migrate_unit, max_workers, executor=executor
        )

    def _run_pipeline_migrators(
        self,
        units: list[MigrationUnit],
        mig_results: dict[str, MigrationStageResult],
        context: WaveContext,
        max_workers: int,
        executor: ThreadPoolExecutor,
    ) -> dict[str, list[PipelineMigrationResult]]:
        def _run_unit_hooks(unit: MigrationUnit) -> list[PipelineMigrationResult]:
            results: list[PipelineMigrationResult] = []
            for hook in self.pipeline_migrators:
                try:
                    res = hook.execute(unit, mig_results[unit.name], context)
                except Exception as exc:
                    logger.error(
                        "PipelineMigrator '%s' failed for unit '%s': %s",
                        hook.name,
                        unit.name,
                        exc,
                    )
                    res = PipelineMigrationResult(
                        migrator_name=hook.name,
                        passed=False,
                        message=str(exc),
                    )
                results.append(res)
                if not res.passed:
                    break
            return results

        return run_parallel_by_unit(
            units, _run_unit_hooks, max_workers, executor=executor
        )

    def _run_post_checks(
        self,
        units: list[MigrationUnit],
        mig_results: dict[str, MigrationStageResult],
        context: WaveContext,
        executor: ThreadPoolExecutor,
    ) -> dict[str, list[CheckResult]]:
        return self._run_unit_checks(
            units,
            self.post_checks,
            lambda check, unit: check.execute(unit, mig_results[unit.name], context),
            executor,
        )

    def _run_origin_lock(
        self,
        units: list[MigrationUnit],
        context: WaveContext,
        max_workers: int,
        executor: ThreadPoolExecutor,
    ) -> dict[str, OriginLockResult]:
        origin_lock = self.origin_lock
        if origin_lock is None or not units:
            return {}

        def _lock_unit(unit: MigrationUnit) -> OriginLockResult:
            try:
                return origin_lock.lock(unit, context)
            except Exception as exc:
                logger.error(
                    "OriginLock '%s' lock failed for unit '%s': %s",
                    origin_lock.name,
                    unit.name,
                    exc,
                )
                return OriginLockResult(
                    unit_name=unit.name,
                    passed=False,
                    lock_name=origin_lock.name,
                    message=str(exc),
                )

        return run_parallel_by_unit(units, _lock_unit, max_workers, executor=executor)

    def _run_origin_unlock(
        self,
        units: list[MigrationUnit],
        context: WaveContext,
        max_workers: int,
    ) -> dict[str, OriginLockResult]:
        origin_lock = self.origin_lock
        if origin_lock is None or not units:
            return {}

        def _unlock_unit(unit: MigrationUnit) -> OriginLockResult:
            try:
                return origin_lock.unlock(unit, context)
            except Exception as exc:
                logger.error(
                    "OriginLock '%s' unlock failed for unit '%s': %s",
                    origin_lock.name,
                    unit.name,
                    exc,
                )
                return OriginLockResult(
                    unit_name=unit.name,
                    passed=False,
                    lock_name=origin_lock.name,
                    message=str(exc),
                )

        return run_parallel_by_unit(units, _unlock_unit, max_workers)
