"""Step 6: Core Inventory Update stage using gh-migration-inventory-manager."""

from __future__ import annotations

import logging
from typing import Any

from gh_migration_inventory_manager import Inventory

from gh_migration_tool.contracts import InventoryUpdateResult, MigrationUnit
from gh_migration_tool.settings import MigrationSettings

logger = logging.getLogger(__name__)


def update_inventory(
    verified_units: list[MigrationUnit],
    settings: MigrationSettings,
    inventory_client: Any = None,
) -> dict[str, InventoryUpdateResult]:
    """Update Google Sheets inventory status for all verified Migration Units."""
    results: dict[str, InventoryUpdateResult] = {}
    if not verified_units:
        return results

    client = inventory_client
    if client is None:
        if not settings.google_spreadsheet_id.strip():
            for unit in verified_units:
                results[unit.name] = InventoryUpdateResult(
                    unit_name=unit.name,
                    passed=False,
                    target_status=settings.inventory_target_status,
                    message="GOOGLE_SPREADSHEET_ID is not configured.",
                )
            return results

        client = Inventory(
            spreadsheet_id=settings.google_spreadsheet_id,
            credentials_path=settings.google_credentials_path,
        )

    target_status = settings.inventory_target_status
    for unit in verified_units:
        try:
            client.update_progress(
                repository_name=unit.name,
                status=target_status,
                project=unit.project,
            )
            results[unit.name] = InventoryUpdateResult(
                unit_name=unit.name,
                passed=True,
                target_status=target_status,
                message=f"Updated inventory status to '{target_status}'.",
            )
        except Exception as exc:
            logger.error("Failed to update inventory for unit '%s': %s", unit.name, exc)
            results[unit.name] = InventoryUpdateResult(
                unit_name=unit.name,
                passed=False,
                target_status=target_status,
                message=str(exc),
            )

    return results
