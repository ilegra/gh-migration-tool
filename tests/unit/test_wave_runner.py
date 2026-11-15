"""Unit tests for WaveRunner 7-stage orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from gh_migration_tool.interfaces import (
    GitHubSetup,
    Migrator,
    PostCheck,
    PreCheck,
    WaveContext,
)
from gh_migration_tool.models import (
    CheckResult,
    MigrationStageResult,
    MigrationUnit,
    OriginSnapshot,
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
