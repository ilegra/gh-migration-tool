"""Unit tests for FileWaveLoader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gh_migration_tool.engine.loader import FileWaveLoader


def test_given_valid_yaml_manifest_when_loading_then_returns_migration_units(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "wave.yaml"
    manifest.write_text(
        """
units:
  - name: service-a
    origin_url: https://dev.azure.com/org/proj/_git/service-a
    destination_url: https://github.com/org/service-a.git
    project: proj
    atomic_group: core-app
    origin_provider: ado
    branch_mapping:
      master: main
    origin_exclusive_paths:
      - azure-pipelines.yml
    destination_exclusive_paths:
      - .github
    visibility: internal
    repository_settings:
      allow_auto_merge: true
      delete_branch_on_merge: true
    teams:
      platform: push
    rulesets:
      - name: protect-main
        enforcement: active
    jenkins_job: folder/service-a-build
    github_workflow: ci.yml
    metadata:
      owner: team-a
""",
        encoding="utf-8",
    )

    units = FileWaveLoader().load(manifest)

    assert len(units) == 1
    unit = units[0]
    assert unit.name == "service-a"
    assert unit.origin_url == "https://dev.azure.com/org/proj/_git/service-a"
    assert unit.destination_url == "https://github.com/org/service-a.git"
    assert unit.project == "proj"
    assert unit.atomic_group == "core-app"
    assert unit.origin_provider == "ado"
    assert unit.branch_mapping == {"master": "main"}
    assert unit.origin_exclusive_paths == ["azure-pipelines.yml"]
    assert unit.destination_exclusive_paths == [".github"]
    assert unit.visibility == "internal"
    assert unit.repository_settings == {
        "allow_auto_merge": True,
        "delete_branch_on_merge": True,
    }
    assert unit.teams == {"platform": "push"}
    assert unit.rulesets == [{"name": "protect-main", "enforcement": "active"}]
    assert unit.jenkins_job == "folder/service-a-build"
    assert unit.github_workflow == "ci.yml"
    assert unit.metadata == {"owner": "team-a"}


def test_given_valid_json_list_when_loading_then_returns_migration_units(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "wave.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "name": "service-b",
                    "origin_url": "https://bitbucket.org/ws/service-b.git",
                    "destination_url": "https://github.com/org/service-b.git",
                }
            ]
        ),
        encoding="utf-8",
    )

    units = FileWaveLoader().load(manifest)

    assert len(units) == 1
    unit = units[0]
    assert unit.name == "service-b"
    assert unit.origin_url == "https://bitbucket.org/ws/service-b.git"
    assert unit.destination_url == "https://github.com/org/service-b.git"
    assert unit.project is None
    assert unit.atomic_group is None
    assert unit.origin_provider is None
    assert unit.branch_mapping == {}
    assert unit.origin_exclusive_paths == []
    assert unit.destination_exclusive_paths == []
    assert unit.visibility == "private"
    assert unit.repository_settings == {}
    assert unit.teams == {}
    assert unit.rulesets == []
    assert unit.jenkins_job is None
    assert unit.github_workflow is None
    assert unit.metadata == {}


def test_given_missing_manifest_file_when_loading_then_raises_file_not_found(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError):
        FileWaveLoader().load(tmp_path / "missing.yaml")


def test_given_non_path_input_when_loading_then_raises_type_error() -> None:
    with pytest.raises(TypeError, match="pathlib.Path"):
        FileWaveLoader().load("wave.yaml")  # type: ignore[arg-type]


def test_given_unsupported_extension_when_loading_then_raises_value_error(
    tmp_path: Path,
) -> None:
    bad_file = tmp_path / "wave.txt"
    bad_file.write_text("name: a", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported wave manifest format"):
        FileWaveLoader().load(bad_file)


def test_given_missing_required_field_when_loading_then_raises_value_error(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "invalid.yaml"
    manifest.write_text("units:\n  - name: only-name\n", encoding="utf-8")

    with pytest.raises(ValueError, match="origin_url"):
        FileWaveLoader().load(manifest)


def test_given_invalid_visibility_when_loading_then_raises_value_error(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "bad_vis.yaml"
    manifest.write_text(
        """
units:
  - name: service-c
    origin_url: https://dev.azure.com/org/proj/_git/service-c
    destination_url: https://github.com/org/service-c.git
    visibility: secret
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid visibility"):
        FileWaveLoader().load(manifest)


def test_given_deactivate_origin_in_manifest_when_loading_then_parses_boolean_and_defaults_true(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "deact.yaml"
    manifest.write_text(
        """
units:
  - name: repo-default
    origin_url: https://bitbucket.org/ws/repo-default
    destination_url: https://github.com/org/repo-default.git
  - name: repo-opt-out
    origin_url: https://bitbucket.org/ws/repo-opt-out
    destination_url: https://github.com/org/repo-opt-out.git
    deactivate_origin: false
""",
        encoding="utf-8",
    )

    units = FileWaveLoader().load(manifest)
    assert units[0].deactivate_origin is True
    assert units[1].deactivate_origin is False


def test_given_non_boolean_deactivate_origin_when_loading_then_raises_value_error(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "bad_deact.yaml"
    manifest.write_text(
        """
units:
  - name: repo-bad
    origin_url: https://bitbucket.org/ws/repo-bad
    destination_url: https://github.com/org/repo-bad.git
    deactivate_origin: "false"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid deactivate_origin"):
        FileWaveLoader().load(manifest)
