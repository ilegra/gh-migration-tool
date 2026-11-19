"""Unit tests for WaveRunner 7-stage orchestration."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

from gh_migration_tool.interfaces import (
    GitHubSetup,
    Migrator,
    PipelineMigrator,
    PostCheck,
    PreCheck,
    WaveContext,
)
from gh_migration_tool.models import (
    CheckResult,
    MigrationStageResult,
    MigrationUnit,
    OriginSnapshot,
    PipelineMigrationResult,
    SetupResult,
    UnitStatus,
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
