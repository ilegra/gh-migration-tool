"""Integration test for FileWaveLoader + WaveRunner + ReportFormatter."""

from __future__ import annotations

import subprocess
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
    PipelineMigrationResult,
    PipelineMigrator,
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


def test_given_yaml_manifest_and_pipeline_migrator_when_running_wave_then_commits_change_to_destination_repo(
    tmp_path: Path,
) -> None:
    dest_repo = tmp_path / "dest.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(dest_repo)],
        check=True,
        capture_output=True,
        text=True,
    )

    manifest = tmp_path / "wave.yaml"
    manifest.write_text(
        f"""
units:
  - name: demo-repo
    origin_url: https://dev.azure.com/org/proj/_git/demo-repo
    destination_url: {dest_repo.as_uri()}
    project: proj
""",
        encoding="utf-8",
    )

    def _commit_and_push_file(
        repo_url: str,
        work_dir: Path,
        rel_path: str,
        content: str,
        commit_message: str,
        push_refspec: str,
        branch: str | None = None,
    ) -> None:
        clone_cmd = ["git", "clone"]
        if branch is not None:
            clone_cmd.extend(["--branch", branch])
        clone_cmd.extend([repo_url, str(work_dir)])
        subprocess.run(clone_cmd, check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "config", "user.email", "ci@example.com"],
            cwd=work_dir,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Migration Bot"],
            cwd=work_dir,
            check=True,
        )
        target = work_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", rel_path], cwd=work_dir, check=True)
        subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=work_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "push", "origin", push_refspec],
            cwd=work_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    class _GitMirrorMigrator(Migrator):
        def migrate(
            self, unit: MigrationUnit, context: WaveContext
        ) -> MigrationStageResult:
            _commit_and_push_file(
                repo_url=unit.destination_url,
                work_dir=tmp_path / "mirror-work",
                rel_path="README.md",
                content="# Demo Repo\n",
                commit_message="chore: initial mirror",
                push_refspec="HEAD:main",
            )
            return MigrationStageResult(
                unit_name=unit.name,
                passed=True,
                origin_snapshot=OriginSnapshot(
                    datetime.now(timezone.utc), {"main": "abc"}
                ),
                evaluated_branches=["main"],
                updated_branches=["main"],
            )

    class _WorkflowCommitPipelineMigrator(PipelineMigrator):
        @property
        def name(self) -> str:
            return "workflow_commit_migrator"

        def execute(
            self,
            unit: MigrationUnit,
            migration_result: MigrationStageResult,
            context: WaveContext,
        ) -> PipelineMigrationResult:
            _commit_and_push_file(
                repo_url=unit.destination_url,
                work_dir=tmp_path / "pipeline-work",
                rel_path=".github/workflows/ci.yml",
                content="name: CI\non: [push]\n",
                commit_message="ci: add migrated GitHub Actions workflow",
                push_refspec="main",
                branch="main",
            )
            return PipelineMigrationResult(
                migrator_name=self.name,
                passed=True,
                message="Committed .github/workflows/ci.yml",
            )

    class _VerifyWorkflowPostCheck(PostCheck):
        @property
        def name(self) -> str:
            return "verify_workflow_post"

        def execute(
            self,
            unit: MigrationUnit,
            migration_result: MigrationStageResult,
            context: WaveContext,
        ) -> CheckResult:
            out = subprocess.run(
                [
                    "git",
                    "--git-dir",
                    str(dest_repo),
                    "show",
                    "main:.github/workflows/ci.yml",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            passed = out.returncode == 0 and "name: CI" in out.stdout
            return CheckResult(self.name, passed, "ok" if passed else out.stderr)

    units = FileWaveLoader().load(manifest)
    inv_client = MagicMock()
    runner = WaveRunner(
        settings=MigrationSettings(),
        pre_checks=[_PassPre()],
        github_setup=_PassSetup(),
        migrator=_GitMirrorMigrator(),
        post_checks=[_VerifyWorkflowPostCheck()],
        inventory_client=inv_client,
        pipeline_migrators=[_WorkflowCommitPipelineMigrator()],
    )

    report = runner.run(units)

    assert report.status_counts[UnitStatus.SUCCEEDED] == 1
    assert len(report.records[0].pipeline_migrations) == 1
    assert report.records[0].pipeline_migrations[0].passed is True

    committed = subprocess.run(
        ["git", "--git-dir", str(dest_repo), "show", "main:.github/workflows/ci.yml"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "name: CI" in committed.stdout
    inv_client.update_progress.assert_called_once()
