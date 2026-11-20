"""Wave Loader boundary for reading and validating Wave Manifest files."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, cast, get_args

import yaml

from gh_migration_tool.models import MigrationUnit, RepoVisibility

logger = logging.getLogger(__name__)

_VALID_VISIBILITIES = get_args(RepoVisibility)


class WaveLoader(ABC):
    """Abstract interface for loading Migration Units from a source."""

    @abstractmethod
    def load(self, source: Path) -> list[MigrationUnit]:
        """Load and validate Migration Units from the given source."""


class FileWaveLoader(WaveLoader):
    """Loads Migration Units from a YAML or JSON Wave Manifest file."""

    def load(self, source: Path) -> list[MigrationUnit]:
        """Parse a YAML or JSON Wave Manifest into a list of MigrationUnit objects.

        Args:
            source: Path to the Wave Manifest file (.yaml, .yml, or .json).

        Returns:
            List of validated MigrationUnit objects.

        Raises:
            FileNotFoundError: If the manifest file does not exist.
            ValueError: If the file format is unsupported or schema validation fails.
        """
        if not isinstance(source, Path):
            raise TypeError("source must be a pathlib.Path instance.")

        suffix = source.suffix.lower()
        try:
            raw_text = source.read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError) as exc:
            raise FileNotFoundError(
                f"Wave manifest file not found: '{source}'."
            ) from exc

        if suffix in (".yaml", ".yml"):
            parsed = yaml.safe_load(raw_text)
        elif suffix == ".json":
            parsed = json.loads(raw_text)
        else:
            raise ValueError(
                f"Unsupported wave manifest format '{suffix}'. "
                "Expected .yaml, .yml, or .json."
            )

        if isinstance(parsed, dict):
            raw_units = parsed.get("units")
            if raw_units is None:
                raise ValueError("Wave manifest object must contain a 'units' list.")
        elif isinstance(parsed, list):
            raw_units = parsed
        else:
            raise ValueError(
                "Wave manifest must be a YAML/JSON list or an object "
                "with a 'units' key."
            )

        if not isinstance(raw_units, list):
            raise ValueError("'units' in wave manifest must be a list.")

        units = [self._parse_unit(item, idx) for idx, item in enumerate(raw_units)]
        logger.info("Loaded %d migration units from '%s'", len(units), source)
        return units

    @staticmethod
    def _parse_unit(raw: Any, index: int) -> MigrationUnit:
        if not isinstance(raw, dict):
            raise ValueError(f"Unit at index {index} must be a mapping/object.")

        name = str(raw.get("name", "")).strip()
        origin_url = str(raw.get("origin_url", "")).strip()
        destination_url = str(raw.get("destination_url", "")).strip()

        if not name:
            raise ValueError(f"Unit at index {index} is missing required field 'name'.")
        if not origin_url:
            raise ValueError(
                f"Unit '{name}' at index {index} is missing "
                "required field 'origin_url'."
            )
        if not destination_url:
            raise ValueError(
                f"Unit '{name}' at index {index} is missing "
                "required field 'destination_url'."
            )

        visibility = raw.get("visibility", "private")
        if visibility not in _VALID_VISIBILITIES:
            raise ValueError(
                f"Unit '{name}' has invalid visibility '{visibility}'. "
                f"Expected one of {_VALID_VISIBILITIES}."
            )

        raw_deactivate = raw.get("deactivate_origin", True)
        if raw_deactivate is None:
            deactivate_origin = True
        elif isinstance(raw_deactivate, bool):
            deactivate_origin = raw_deactivate
        else:
            raise ValueError(
                f"Unit '{name}' has invalid deactivate_origin '{raw_deactivate}'. "
                "Expected a boolean."
            )

        return MigrationUnit(
            name=name,
            origin_url=origin_url,
            destination_url=destination_url,
            project=raw.get("project"),
            atomic_group=raw.get("atomic_group"),
            origin_provider=raw.get("origin_provider"),
            branch_mapping=dict(raw.get("branch_mapping") or {}),
            origin_exclusive_paths=list(raw.get("origin_exclusive_paths") or []),
            destination_exclusive_paths=list(
                raw.get("destination_exclusive_paths") or []
            ),
            visibility=cast(RepoVisibility, visibility),
            teams=dict(raw.get("teams") or {}),
            rulesets=list(raw.get("rulesets") or []),
            jenkins_job=raw.get("jenkins_job"),
            github_workflow=raw.get("github_workflow"),
            deactivate_origin=deactivate_origin,
            metadata=dict(raw.get("metadata") or {}),
        )
