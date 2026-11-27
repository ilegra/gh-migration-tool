"""Integration test for FileWaveLoader + WaveRunner + ReportFormatter."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import responses

from gh_migration_tool import (
    BitbucketCloudOriginLock,
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


def test_given_bitbucket_origin_lock_when_running_wave_then_locks_succeeded_rolls_back_on_inventory_failure_and_skips_on_lock_failure(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "wave_lock.yaml"
    manifest.write_text(
        """
units:
  - name: repo-lock-ok
    origin_url: https://bitbucket.org/acme-ws/repo-lock-ok.git
    destination_url: https://github.com/acme/repo-lock-ok.git
  - name: repo-opt-out
    origin_url: https://bitbucket.org/acme-ws/repo-opt-out.git
    destination_url: https://github.com/acme/repo-opt-out.git
    lock_origin: false
  - name: repo-lock-fail
    origin_url: https://bitbucket.org/acme-ws/repo-lock-fail.git
    destination_url: https://github.com/acme/repo-lock-fail.git
  - name: repo-inv-fail-rollback
    origin_url: https://bitbucket.org/acme-ws/repo-inv-fail-rollback.git
    destination_url: https://github.com/acme/repo-inv-fail-rollback.git
""",
        encoding="utf-8",
    )

    units = FileWaveLoader().load(manifest)
    inv_client = MagicMock()

    def _update_progress(
        *, repository_name: str, status: str, project: str | None = None
    ) -> None:
        if repository_name == "repo-inv-fail-rollback":
            raise RuntimeError("sheet write error")

    inv_client.update_progress.side_effect = _update_progress
    settings = MigrationSettings(origin_token="bb-admin-token", http_max_retries=0)

    with responses.RequestsMock() as rsps:
        # repo-lock-ok: lock succeeds
        rsps.add(
            responses.GET,
            "https://api.bitbucket.org/2.0/repositories/acme-ws/repo-lock-ok/branch-restrictions",
            json={"values": []},
            status=200,
        )
        rsps.add(
            responses.POST,
            "https://api.bitbucket.org/2.0/repositories/acme-ws/repo-lock-ok/branch-restrictions",
            json={"id": 1, "kind": "push", "pattern": "*"},
            status=201,
        )
        # repo-lock-fail: lock returns HTTP 403
        rsps.add(
            responses.GET,
            "https://api.bitbucket.org/2.0/repositories/acme-ws/repo-lock-fail/branch-restrictions",
            json={"error": {"message": "Forbidden"}},
            status=403,
        )
        # repo-inv-fail-rollback: lock succeeds, then inventory fails -> automatic unlock
        rsps.add(
            responses.GET,
            "https://api.bitbucket.org/2.0/repositories/acme-ws/repo-inv-fail-rollback/branch-restrictions",
            json={"values": []},
            status=200,
        )
        rsps.add(
            responses.POST,
            "https://api.bitbucket.org/2.0/repositories/acme-ws/repo-inv-fail-rollback/branch-restrictions",
            json={"id": 77, "kind": "push", "pattern": "*"},
            status=201,
        )
        rsps.add(
            responses.GET,
            "https://api.bitbucket.org/2.0/repositories/acme-ws/repo-inv-fail-rollback/branch-restrictions",
            json={
                "values": [
                    {
                        "id": 77,
                        "kind": "push",
                        "pattern": "*",
                        "users": [],
                        "groups": [],
                    }
                ]
            },
            status=200,
        )
        rsps.add(
            responses.DELETE,
            "https://api.bitbucket.org/2.0/repositories/acme-ws/repo-inv-fail-rollback/branch-restrictions/77",
            status=204,
        )

        runner = WaveRunner(
            settings=settings,
            pre_checks=[],
            github_setup=_PassSetup(),
            migrator=_PassMigrator(),
            post_checks=[],
            inventory_client=inv_client,
            origin_lock=BitbucketCloudOriginLock(),
        )
        report = runner.run(units)

    by_name = {r.unit.name: r for r in report.records}
    assert by_name["repo-lock-ok"].status == UnitStatus.SUCCEEDED
    assert by_name["repo-lock-ok"].origin_lock_result is not None
    assert by_name["repo-lock-ok"].origin_lock_result.passed is True
    assert by_name["repo-lock-ok"].origin_unlock_result is None
    assert by_name["repo-opt-out"].status == UnitStatus.SUCCEEDED
    assert by_name["repo-opt-out"].origin_lock_result is None
    assert by_name["repo-lock-fail"].status == UnitStatus.ORIGIN_LOCK_FAILED
    assert by_name["repo-lock-fail"].inventory_result is None
    assert (
        by_name["repo-inv-fail-rollback"].status == UnitStatus.INVENTORY_UPDATE_FAILED
    )
    assert by_name["repo-inv-fail-rollback"].origin_lock_result is not None
    assert by_name["repo-inv-fail-rollback"].origin_lock_result.passed is True
    assert by_name["repo-inv-fail-rollback"].origin_unlock_result is not None
    assert by_name["repo-inv-fail-rollback"].origin_unlock_result.passed is True
    assert (
        by_name["repo-inv-fail-rollback"].origin_unlock_result.details[
            "push_restrictions_removed"
        ]
        == 1
    )

    updated_repos = {
        call.kwargs["repository_name"]
        for call in inv_client.update_progress.call_args_list
    }
    assert updated_repos == {"repo-lock-ok", "repo-opt-out", "repo-inv-fail-rollback"}


def test_given_no_origin_lock_when_running_wave_then_completes_without_locking(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "wave_no_lock.yaml"
    manifest.write_text(
        """
units:
  - name: repo-a
    origin_url: https://bitbucket.org/acme-ws/repo-a.git
    destination_url: https://github.com/acme/repo-a.git
""",
        encoding="utf-8",
    )

    units = FileWaveLoader().load(manifest)
    inv_client = MagicMock()
    runner = WaveRunner(
        settings=MigrationSettings(),
        pre_checks=[],
        github_setup=_PassSetup(),
        migrator=_PassMigrator(),
        post_checks=[],
        inventory_client=inv_client,
    )
    report = runner.run(units)

    assert report.status_counts[UnitStatus.SUCCEEDED] == 1
    assert report.records[0].origin_lock_result is None
    assert report.records[0].origin_unlock_result is None
    inv_client.update_progress.assert_called_once()
