"""Unit tests for Step 6 Inventory Update stage."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from gh_migration_tool.contracts import MigrationUnit
from gh_migration_tool.engine.inventory import update_inventory
from gh_migration_tool.settings import MigrationSettings


def _unit(name: str = "service-a") -> MigrationUnit:
    return MigrationUnit(
        name=name,
        project="payments",
        origin_url="https://dev.azure.com/org/p/_git/service-a",
        destination_url="https://github.com/org/service-a.git",
    )


def test_given_valid_inventory_client_when_updating_inventory_then_succeeds() -> None:
    client = MagicMock()
    settings = MigrationSettings(INVENTORY_TARGET_STATUS="migrated")

    results = update_inventory([_unit()], settings, inventory_client=client)

    res = results["service-a"]
    assert res.unit_name == "service-a"
    assert res.passed is True
    assert res.target_status == "migrated"
    assert "migrated" in res.message
    client.update_progress.assert_called_once_with(
        repository_name="service-a",
        status="migrated",
        project="payments",
    )


def test_given_failing_inventory_client_when_updating_inventory_then_marks_failed() -> (
    None
):
    client = MagicMock()
    client.update_progress.side_effect = RuntimeError("Sheets API 503")
    settings = MigrationSettings(INVENTORY_TARGET_STATUS="migrated")

    results = update_inventory([_unit()], settings, inventory_client=client)

    res = results["service-a"]
    assert res.unit_name == "service-a"
    assert res.passed is False
    assert res.target_status == "migrated"
    assert "Sheets API 503" in res.message


def test_given_none_inventory_client_when_updating_inventory_then_raises_value_error() -> (
    None
):
    settings = MigrationSettings(GOOGLE_SPREADSHEET_ID="spreadsheet-id")

    with pytest.raises(ValueError, match="inventory_client"):
        update_inventory([_unit()], settings, inventory_client=cast(Any, None))


def test_given_empty_verified_units_when_updating_inventory_then_returns_empty_dict() -> (
    None
):
    client = MagicMock()

    results = update_inventory([], MigrationSettings(), inventory_client=client)

    assert results == {}
    client.update_progress.assert_not_called()
