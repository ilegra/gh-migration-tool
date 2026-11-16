"""Integration test for FileWaveLoader + WaveRunner + ReportFormatter."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from gh_migration_tool import (
    CheckResult,
    FileWaveLoader,
    GitHubSetup,
    MigrationSettings,
    MigrationStageResult,
    MigrationUnit,
    Migrator,
    OriginSnapshot,
    PostCheck,
    PreCheck,
    ReportFormatter,
    SetupResult,
    UnitStatus,
    WaveContext,
    WaveRunner,
)


class _PassPre(PreCheck):
    @property
    def name(self) -> str:
        return "pass_pre"

    def execute(self, unit: MigrationUnit, context: WaveContext) -> CheckResult:
        return CheckResult(self.name, True, "ok")


class _PassSetup(GitHubSetup):
    def setup_unit(self, unit: MigrationUnit, context: WaveContext) -> SetupResult:
        return SetupResult(unit.name, True, "ok", created=True)


class _PassMigrator(Migrator):
    def migrate(
        self, unit: MigrationUnit, context: WaveContext
    ) -> MigrationStageResult:
        return MigrationStageResult(
            unit_name=unit.name,
            passed=True,
            origin_snapshot=OriginSnapshot(datetime.now(timezone.utc), {"main": "abc"}),
            evaluated_branches=["main"],
            updated_branches=["main"],
        )


class _PassPost(PostCheck):
    @property
    def name(self) -> str:
        return "pass_post"

    def execute(
        self,
        unit: MigrationUnit,
        migration_result: MigrationStageResult,
        context: WaveContext,
    ) -> CheckResult:
        return CheckResult(self.name, True, "ok")


def test_given_yaml_manifest_when_running_end_to_end_wave_then_produces_succeeded_report(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "wave.yaml"
    manifest.write_text(
        """
units:
  - name: demo-repo
    origin_url: https://dev.azure.com/org/proj/_git/demo-repo
    destination_url: https://github.com/org/demo-repo.git
    project: proj
""",
        encoding="utf-8",
    )

    units = FileWaveLoader().load(manifest)
    inv_client = MagicMock()
    runner = WaveRunner(
        settings=MigrationSettings(),
        pre_checks=[_PassPre()],
        github_setup=_PassSetup(),
        migrator=_PassMigrator(),
        post_checks=[_PassPost()],
        inventory_client=inv_client,
    )

    report = runner.run(units)
    rendered = ReportFormatter.to_markdown(report)

    assert report.status_counts[UnitStatus.SUCCEEDED] == 1
    assert "demo-repo" in rendered
    inv_client.update_progress.assert_called_once()
