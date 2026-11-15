"""Unit tests for FileWaveLoader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gh_migration_tool.loader import FileWaveLoader


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
    visibility: private
    teams:
      platform: push
    jenkins_job: folder/service-a-build
""",
        encoding="utf-8",
    )

    units = FileWaveLoader().load(manifest)

    assert len(units) == 1
    assert units[0].name == "service-a"
    assert units[0].atomic_group == "core-app"
    assert units[0].teams == {"platform": "push"}


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
    assert units[0].name == "service-b"


def test_given_missing_manifest_file_when_loading_then_raises_file_not_found(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError):
        FileWaveLoader().load(tmp_path / "missing.yaml")


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
