"""Unit tests for WaveRunner 7-stage orchestration."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from gh_migration_tool.contracts import (
    CheckResult,
    GitHubSetup,
    MigrationStageResult,
    MigrationUnit,
    Migrator,
    OriginLock,
    OriginLockResult,
    OriginSnapshot,
    PipelineMigrationResult,
    PipelineMigrator,
    PostCheck,
    PreCheck,
    SetupResult,
    UnitStatus,
    WaveContext,
)
from gh_migration_tool.runner import WaveRunner
from gh_migration_tool.settings import MigrationSettings


def _unit(name: str, atomic_group: str | None = None) -> MigrationUnit:
    return MigrationUnit(
        name=name,
        origin_url=f"https://dev.azure.com/org/p/_git/{name}",
        destination_url=f"https://github.com/org/{name}.git",
        atomic_group=atomic_group,
    )


class _StubPreCheck(PreCheck):
    def __init__(self, failing_units: set[str]) -> None:
        self._failing = failing_units

    @property
    def name(self) -> str:
        return "stub_pre"

    def execute(self, unit: MigrationUnit, context: WaveContext) -> CheckResult:
        ok = unit.name not in self._failing
        return CheckResult(self.name, ok, "" if ok else "pre-check failed")


class _StubSetup(GitHubSetup):
    def __init__(self, failing_units: set[str]) -> None:
        self._failing = failing_units

    def setup_unit(self, unit: MigrationUnit, context: WaveContext) -> SetupResult:
        ok = unit.name not in self._failing
        return SetupResult(unit.name, ok, "" if ok else "setup failed")


class _StubMigrator(Migrator):
    def __init__(self, failing_units: set[str]) -> None:
        self._failing = failing_units

    def migrate(
        self, unit: MigrationUnit, context: WaveContext
    ) -> MigrationStageResult:
        ok = unit.name not in self._failing
        return MigrationStageResult(
            unit_name=unit.name,
            passed=ok,
            origin_snapshot=OriginSnapshot(datetime.now(timezone.utc), {"main": "s1"}),
            errors=[] if ok else ["sync error"],
        )


class _StubPostCheck(PostCheck):
    def __init__(self, failing_units: set[str]) -> None:
        self._failing = failing_units

    @property
    def name(self) -> str:
        return "stub_post"

    def execute(
        self,
        unit: MigrationUnit,
        migration_result: MigrationStageResult,
        context: WaveContext,
    ) -> CheckResult:
        ok = unit.name not in self._failing
        return CheckResult(self.name, ok, "" if ok else "drift detected")


def test_given_mixed_stage_failures_when_running_wave_then_assigns_exact_terminal_statuses() -> (
    None
):
    units = [
        _unit("ok-repo"),
        _unit("pre-fail", atomic_group="grp"),
        _unit("grp-blocked", atomic_group="grp"),
        _unit("setup-fail"),
        _unit("mig-fail"),
        _unit("post-fail"),
        _unit("inv-fail"),
    ]
    inv_client = MagicMock()

    def _update_side_effect(repository_name: str, **_: object) -> None:
        if repository_name == "inv-fail":
            raise RuntimeError("sheet unavailable")

    inv_client.update_progress.side_effect = _update_side_effect

    runner = WaveRunner(
        settings=MigrationSettings(MAX_WORKERS=2),
        pre_checks=[_StubPreCheck({"pre-fail"})],
        github_setup=_StubSetup({"setup-fail"}),
        migrator=_StubMigrator({"mig-fail"}),
        post_checks=[_StubPostCheck({"post-fail"})],
        inventory_client=inv_client,
    )

    report = runner.run(units)
    by_name = {r.unit.name: r for r in report.records}

    assert report.total_units == 7
    assert report.status_counts[UnitStatus.SUCCEEDED] == 1
    assert report.status_counts[UnitStatus.PRE_CHECK_FAILED] == 1
    assert report.status_counts[UnitStatus.BLOCKED_BY_GROUP] == 1
    assert report.status_counts[UnitStatus.GITHUB_SETUP_FAILED] == 1
    assert report.status_counts[UnitStatus.MIGRATION_FAILED] == 1
    assert report.status_counts[UnitStatus.POST_CHECK_FAILED] == 1
    assert report.status_counts[UnitStatus.INVENTORY_UPDATE_FAILED] == 1

    assert by_name["ok-repo"].status == UnitStatus.SUCCEEDED
    assert by_name["ok-repo"].inventory_result is not None
    assert by_name["ok-repo"].inventory_result.passed is True
    assert by_name["pre-fail"].status == UnitStatus.PRE_CHECK_FAILED
    assert by_name["grp-blocked"].status == UnitStatus.BLOCKED_BY_GROUP
    assert by_name["setup-fail"].status == UnitStatus.GITHUB_SETUP_FAILED
    assert by_name["mig-fail"].status == UnitStatus.MIGRATION_FAILED
    assert by_name["post-fail"].status == UnitStatus.POST_CHECK_FAILED
    assert by_name["inv-fail"].status == UnitStatus.INVENTORY_UPDATE_FAILED
    assert "sheet unavailable" in by_name["inv-fail"].error_summary


def test_given_exception_raising_plugins_when_running_wave_then_isolates_failures() -> (
    None
):
    class _ExplodingPreCheck(PreCheck):
        @property
        def name(self) -> str:
            return "exploding_pre"

        def execute(self, unit: MigrationUnit, context: WaveContext) -> CheckResult:
            raise RuntimeError("boom pre")

    runner = WaveRunner(
        settings=MigrationSettings(MAX_WORKERS=1),
        pre_checks=[_ExplodingPreCheck()],
        github_setup=_StubSetup(set()),
        migrator=_StubMigrator(set()),
        post_checks=[_StubPostCheck(set())],
        inventory_client=MagicMock(),
    )

    report = runner.run([_unit("repo-a")])

    assert report.total_units == 1
    assert report.records[0].status == UnitStatus.PRE_CHECK_FAILED
    assert "boom pre" in report.records[0].error_summary


def test_given_empty_units_when_running_wave_then_returns_empty_report() -> None:
    runner = WaveRunner(
        settings=MigrationSettings(),
        pre_checks=[],
        github_setup=_StubSetup(set()),
        migrator=_StubMigrator(set()),
        post_checks=[],
        inventory_client=MagicMock(),
    )

    report = runner.run([])

    assert report.total_units == 0
    assert report.records == []
    assert all(count == 0 for count in report.status_counts.values())


def test_given_no_pipeline_migrators_when_running_wave_then_executes_normally_without_errors() -> (
    None
):
    inv_client = MagicMock()
    runner = WaveRunner(
        settings=MigrationSettings(MAX_WORKERS=2),
        pre_checks=[_StubPreCheck(set())],
        github_setup=_StubSetup(set()),
        migrator=_StubMigrator(set()),
        post_checks=[_StubPostCheck(set())],
        inventory_client=inv_client,
    )

    report = runner.run([_unit("repo-a"), _unit("repo-b")])

    assert runner.pipeline_migrators == []
    assert report.total_units == 2
    assert report.status_counts[UnitStatus.SUCCEEDED] == 2
    for record in report.records:
        assert record.status == UnitStatus.SUCCEEDED
        assert record.pipeline_migrations == []
        assert record.error_summary == ""


def test_given_pipeline_migrators_when_running_wave_then_executes_sequentially_per_unit_before_post_checks() -> (
    None
):
    events: list[str] = []

    class _OrderedHook(PipelineMigrator):
        def __init__(self, hook_name: str) -> None:
            self._name = hook_name

        @property
        def name(self) -> str:
            return self._name

        def execute(
            self,
            unit: MigrationUnit,
            migration_result: MigrationStageResult,
            context: WaveContext,
        ) -> PipelineMigrationResult:
            assert migration_result.passed is True
            assert context.settings is not None
            events.append(f"{unit.name}:{self._name}")
            return PipelineMigrationResult(
                migrator_name=self._name, passed=True, message="done"
            )

    class _RecordingPostCheck(PostCheck):
        @property
        def name(self) -> str:
            return "recording_post"

        def execute(
            self,
            unit: MigrationUnit,
            migration_result: MigrationStageResult,
            context: WaveContext,
        ) -> CheckResult:
            events.append(f"{unit.name}:post_check")
            return CheckResult(self.name, True, "ok")

    runner = WaveRunner(
        settings=MigrationSettings(MAX_WORKERS=1),
        pre_checks=[],
        github_setup=_StubSetup(set()),
        migrator=_StubMigrator(set()),
        post_checks=[_RecordingPostCheck()],
        inventory_client=MagicMock(),
        pipeline_migrators=[_OrderedHook("hook_1"), _OrderedHook("hook_2")],
    )

    report = runner.run([_unit("repo-a")])

    assert report.status_counts[UnitStatus.SUCCEEDED] == 1
    assert events == ["repo-a:hook_1", "repo-a:hook_2", "repo-a:post_check"]
    assert [r.migrator_name for r in report.records[0].pipeline_migrations] == [
        "hook_1",
        "hook_2",
    ]


def test_given_failing_pipeline_migrator_when_running_wave_then_short_circuits_subsequent_hooks_and_skips_post_checks() -> (
    None
):
    hook_calls: list[tuple[str, str]] = []
    post_check_units: list[str] = []
    inv_client = MagicMock()

    class _FailingHook(PipelineMigrator):
        @property
        def name(self) -> str:
            return "first_hook"

        def execute(
            self,
            unit: MigrationUnit,
            migration_result: MigrationStageResult,
            context: WaveContext,
        ) -> PipelineMigrationResult:
            hook_calls.append((unit.name, self.name))
            if unit.name == "fail-repo":
                return PipelineMigrationResult(
                    migrator_name=self.name, passed=False, message="conversion error"
                )
            return PipelineMigrationResult(migrator_name=self.name, passed=True)

    class _SecondHook(PipelineMigrator):
        @property
        def name(self) -> str:
            return "second_hook"

        def execute(
            self,
            unit: MigrationUnit,
            migration_result: MigrationStageResult,
            context: WaveContext,
        ) -> PipelineMigrationResult:
            hook_calls.append((unit.name, self.name))
            return PipelineMigrationResult(migrator_name=self.name, passed=True)

    class _SpyPostCheck(PostCheck):
        @property
        def name(self) -> str:
            return "spy_post"

        def execute(
            self,
            unit: MigrationUnit,
            migration_result: MigrationStageResult,
            context: WaveContext,
        ) -> CheckResult:
            post_check_units.append(unit.name)
            return CheckResult(self.name, True)

    runner = WaveRunner(
        settings=MigrationSettings(MAX_WORKERS=2),
        pre_checks=[],
        github_setup=_StubSetup(set()),
        migrator=_StubMigrator(set()),
        post_checks=[_SpyPostCheck()],
        inventory_client=inv_client,
        pipeline_migrators=[_FailingHook(), _SecondHook()],
    )

    report = runner.run([_unit("ok-repo"), _unit("fail-repo")])
    by_name = {r.unit.name: r for r in report.records}

    assert report.status_counts[UnitStatus.SUCCEEDED] == 1
    assert report.status_counts[UnitStatus.PIPELINE_MIGRATION_FAILED] == 1
    assert by_name["fail-repo"].status == UnitStatus.PIPELINE_MIGRATION_FAILED
    assert "conversion error" in by_name["fail-repo"].error_summary
    assert [r.migrator_name for r in by_name["fail-repo"].pipeline_migrations] == [
        "first_hook"
    ]
    assert ("fail-repo", "second_hook") not in hook_calls
    assert post_check_units == ["ok-repo"]
    inv_client.update_progress.assert_called_once()


def test_given_exception_raising_pipeline_migrator_when_running_wave_then_isolates_failure() -> (
    None
):
    class _ExplodingHook(PipelineMigrator):
        @property
        def name(self) -> str:
            return "exploding_hook"

        def execute(
            self,
            unit: MigrationUnit,
            migration_result: MigrationStageResult,
            context: WaveContext,
        ) -> PipelineMigrationResult:
            raise RuntimeError("boom pipeline")

    runner = WaveRunner(
        settings=MigrationSettings(MAX_WORKERS=1),
        pre_checks=[],
        github_setup=_StubSetup(set()),
        migrator=_StubMigrator(set()),
        post_checks=[_StubPostCheck(set())],
        inventory_client=MagicMock(),
        pipeline_migrators=[_ExplodingHook()],
    )

    report = runner.run([_unit("repo-a")])

    assert report.total_units == 1
    assert report.records[0].status == UnitStatus.PIPELINE_MIGRATION_FAILED
    assert "boom pipeline" in report.records[0].error_summary
    assert report.records[0].pipeline_migrations[0].passed is False
    assert report.records[0].pipeline_migrations[0].migrator_name == "exploding_hook"


def test_given_multiple_units_with_pipeline_migrators_when_running_wave_then_executes_across_units_in_parallel() -> (
    None
):
    barrier = threading.Barrier(2, timeout=2.0)
    threads: set[int] = set()
    lock = threading.Lock()

    class _ParallelHook(PipelineMigrator):
        @property
        def name(self) -> str:
            return "parallel_hook"

        def execute(
            self,
            unit: MigrationUnit,
            migration_result: MigrationStageResult,
            context: WaveContext,
        ) -> PipelineMigrationResult:
            with lock:
                threads.add(threading.get_ident())
            barrier.wait()
            time.sleep(0.01)
            return PipelineMigrationResult(migrator_name=self.name, passed=True)

    runner = WaveRunner(
        settings=MigrationSettings(MAX_WORKERS=2),
        pre_checks=[],
        github_setup=_StubSetup(set()),
        migrator=_StubMigrator(set()),
        post_checks=[],
        inventory_client=MagicMock(),
        pipeline_migrators=[_ParallelHook()],
    )

    report = runner.run([_unit("repo-a"), _unit("repo-b")])

    assert report.status_counts[UnitStatus.SUCCEEDED] == 2
    assert len(threads) == 2


def test_given_origin_lock_when_running_wave_then_runs_before_inventory_and_isolates_failures() -> (
    None
):
    locked_units: list[str] = []

    class _StubOriginLock(OriginLock):
        @property
        def name(self) -> str:
            return "stub_origin_lock"

        def lock(self, unit: MigrationUnit, context: WaveContext) -> OriginLockResult:
            locked_units.append(unit.name)
            if unit.name == "lock-fail":
                return OriginLockResult(
                    unit_name=unit.name,
                    passed=False,
                    lock_name=self.name,
                    message="lock error",
                )
            if unit.name == "lock-boom":
                raise RuntimeError("unexpected crash")
            return OriginLockResult(
                unit_name=unit.name,
                passed=True,
                lock_name=self.name,
                message="locked",
            )

        def unlock(self, unit: MigrationUnit, context: WaveContext) -> OriginLockResult:
            return OriginLockResult(
                unit_name=unit.name,
                passed=True,
                lock_name=self.name,
                message="unlocked",
            )

    inv_client = MagicMock()
    units = [
        _unit("ok-repo"),
        MigrationUnit(
            name="opt-out-repo",
            origin_url="https://dev.azure.com/org/p/_git/opt-out-repo",
            destination_url="https://github.com/org/opt-out-repo.git",
            lock_origin=False,
        ),
        _unit("lock-fail"),
        _unit("lock-boom"),
        _unit("post-fail"),
    ]
    runner = WaveRunner(
        settings=MigrationSettings(),
        pre_checks=[],
        github_setup=_StubSetup(set()),
        migrator=_StubMigrator(set()),
        post_checks=[_StubPostCheck({"post-fail"})],
        inventory_client=inv_client,
        origin_lock=_StubOriginLock(),
    )

    report = runner.run(units)
    by_name = {r.unit.name: r for r in report.records}

    assert set(locked_units) == {"ok-repo", "lock-fail", "lock-boom"}
    assert by_name["ok-repo"].status == UnitStatus.SUCCEEDED
    assert by_name["ok-repo"].origin_lock_result is not None
    assert by_name["ok-repo"].origin_lock_result.passed is True
    assert by_name["ok-repo"].origin_unlock_result is None
    assert by_name["opt-out-repo"].status == UnitStatus.SUCCEEDED
    assert by_name["opt-out-repo"].origin_lock_result is None
    assert by_name["lock-fail"].status == UnitStatus.ORIGIN_LOCK_FAILED
    assert by_name["lock-fail"].inventory_result is None
    assert by_name["lock-boom"].status == UnitStatus.ORIGIN_LOCK_FAILED
    assert by_name["lock-boom"].inventory_result is None
    assert by_name["post-fail"].status == UnitStatus.POST_CHECK_FAILED

    updated_repos = {
        call.kwargs["repository_name"]
        for call in inv_client.update_progress.call_args_list
    }
    assert updated_repos == {"ok-repo", "opt-out-repo"}


def test_given_inventory_update_failure_after_lock_when_running_wave_then_rolls_back_only_newly_locked_units() -> (
    None
):
    unlocked_units: list[str] = []

    class _RollbackOriginLock(OriginLock):
        @property
        def name(self) -> str:
            return "rollback_origin_lock"

        def lock(self, unit: MigrationUnit, context: WaveContext) -> OriginLockResult:
            return OriginLockResult(
                unit_name=unit.name,
                passed=True,
                lock_name=self.name,
                already_in_target_state=(unit.name == "pre-locked-repo"),
                message="locked",
            )

        def unlock(self, unit: MigrationUnit, context: WaveContext) -> OriginLockResult:
            unlocked_units.append(unit.name)
            return OriginLockResult(
                unit_name=unit.name,
                passed=True,
                lock_name=self.name,
                message="unlocked",
            )

    inv_client = MagicMock()

    def _update_progress(
        *, repository_name: str, status: str, project: str | None = None
    ) -> None:
        if repository_name in {"inv-fail-repo", "pre-locked-repo"}:
            raise RuntimeError("sheet unavailable")

    inv_client.update_progress.side_effect = _update_progress

    units = [
        _unit("ok-repo"),
        _unit("inv-fail-repo"),
        _unit("pre-locked-repo"),
    ]
    runner = WaveRunner(
        settings=MigrationSettings(),
        pre_checks=[],
        github_setup=_StubSetup(set()),
        migrator=_StubMigrator(set()),
        post_checks=[],
        inventory_client=inv_client,
        origin_lock=_RollbackOriginLock(),
    )

    report = runner.run(units)
    by_name = {r.unit.name: r for r in report.records}

    assert unlocked_units == ["inv-fail-repo"]
    assert by_name["ok-repo"].status == UnitStatus.SUCCEEDED
    assert by_name["ok-repo"].origin_unlock_result is None
    assert by_name["inv-fail-repo"].status == UnitStatus.INVENTORY_UPDATE_FAILED
    assert by_name["inv-fail-repo"].origin_unlock_result is not None
    assert by_name["inv-fail-repo"].origin_unlock_result.passed is True
    assert by_name["pre-locked-repo"].status == UnitStatus.INVENTORY_UPDATE_FAILED
    assert by_name["pre-locked-repo"].origin_unlock_result is None


def test_given_failing_rollback_unlock_when_inventory_fails_then_records_rollback_error() -> (
    None
):
    class _FailingUnlockOriginLock(OriginLock):
        @property
        def name(self) -> str:
            return "failing_unlock_origin_lock"

        def lock(self, unit: MigrationUnit, context: WaveContext) -> OriginLockResult:
            return OriginLockResult(
                unit_name=unit.name,
                passed=True,
                lock_name=self.name,
                message="locked",
            )

        def unlock(self, unit: MigrationUnit, context: WaveContext) -> OriginLockResult:
            if unit.name == "unlock-boom":
                raise RuntimeError("unlock crashed")
            return OriginLockResult(
                unit_name=unit.name,
                passed=False,
                lock_name=self.name,
                message="unlock denied",
            )

    inv_client = MagicMock()
    inv_client.update_progress.side_effect = RuntimeError("inventory write failed")

    runner = WaveRunner(
        settings=MigrationSettings(),
        pre_checks=[],
        github_setup=_StubSetup(set()),
        migrator=_StubMigrator(set()),
        post_checks=[],
        inventory_client=inv_client,
        origin_lock=_FailingUnlockOriginLock(),
    )

    report = runner.run([_unit("unlock-fail"), _unit("unlock-boom")])
    by_name = {r.unit.name: r for r in report.records}

    assert by_name["unlock-fail"].status == UnitStatus.INVENTORY_UPDATE_FAILED
    assert by_name["unlock-fail"].origin_unlock_result is not None
    assert by_name["unlock-fail"].origin_unlock_result.passed is False
    assert "Rollback failed: unlock denied" in by_name["unlock-fail"].error_summary

    assert by_name["unlock-boom"].status == UnitStatus.INVENTORY_UPDATE_FAILED
    assert by_name["unlock-boom"].origin_unlock_result is not None
    assert by_name["unlock-boom"].origin_unlock_result.passed is False
    assert "Rollback failed: unlock crashed" in by_name["unlock-boom"].error_summary


def test_given_none_inventory_client_when_initializing_wave_runner_then_raises_value_error() -> (
    None
):
    with pytest.raises(ValueError, match="inventory_client"):
        WaveRunner(
            settings=MigrationSettings(),
            inventory_client=cast(Any, None),
        )


def test_given_no_token_provider_when_running_wave_then_does_not_instantiate_token_provider_internally() -> (
    None
):
    observed_providers: list[object] = []

    class _RecordingPreCheck(PreCheck):
        @property
        def name(self) -> str:
            return "recording_pre"

        def execute(self, unit: MigrationUnit, context: WaveContext) -> CheckResult:
            observed_providers.append(context.github_token_provider)
            return CheckResult(self.name, True, "ok")

    runner = WaveRunner(
        settings=MigrationSettings(
            GITHUB_APP_ID="12345",
            GITHUB_APP_INSTALLATION_ID="67890",
            GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\nfake",
        ),
        inventory_client=MagicMock(),
        pre_checks=[_RecordingPreCheck()],
        github_setup=_StubSetup(set()),
        migrator=_StubMigrator(set()),
        post_checks=[],
    )

    report = runner.run([_unit("repo-a")])

    assert report.status_counts[UnitStatus.SUCCEEDED] == 1
    assert observed_providers == [None]


def test_given_injected_token_provider_when_running_wave_then_passes_it_in_wave_context() -> (
    None
):
    class _StubTokenProvider:
        def get_token(self) -> str:
            return "injected-token"

    observed_tokens: list[str | None] = []
    provider = _StubTokenProvider()

    class _TokenPreCheck(PreCheck):
        @property
        def name(self) -> str:
            return "token_pre"

        def execute(self, unit: MigrationUnit, context: WaveContext) -> CheckResult:
            token = (
                context.github_token_provider.get_token()
                if context.github_token_provider is not None
                else None
            )
            observed_tokens.append(token)
            return CheckResult(self.name, True, "ok")

    runner = WaveRunner(
        settings=MigrationSettings(),
        inventory_client=MagicMock(),
        token_provider=provider,
        pre_checks=[_TokenPreCheck()],
        github_setup=_StubSetup(set()),
        migrator=_StubMigrator(set()),
        post_checks=[],
    )

    report = runner.run([_unit("repo-a")])

    assert report.status_counts[UnitStatus.SUCCEEDED] == 1
    assert observed_tokens == ["injected-token"]
