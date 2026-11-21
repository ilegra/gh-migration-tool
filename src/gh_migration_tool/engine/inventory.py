"""Step 6: Core Inventory Update stage using an injected InventoryClient."""

from __future__ import annotations

import logging

from gh_migration_tool.contracts import (
    InventoryClient,
    InventoryUpdateResult,
    MigrationUnit,
)
from gh_migration_tool.settings import MigrationSettings

logger = logging.getLogger(__name__)


def update_inventory(
    verified_units: list[MigrationUnit],
    settings: MigrationSettings,
    inventory_client: InventoryClient,
) -> dict[str, InventoryUpdateResult]:
    """Update inventory status for all verified Migration Units.

    Args:
        verified_units: Migration Units that passed all prior stages.
        settings: Runtime migration settings providing the target inventory status.
        inventory_client: Injected inventory client implementing ``update_progress``.

    Returns:
        Mapping of unit name to its ``InventoryUpdateResult``.

    Raises:
        ValueError: If ``inventory_client`` is ``None``.
    """
    if inventory_client is None:
        raise ValueError("inventory_client must be provided via dependency injection.")

    results: dict[str, InventoryUpdateResult] = {}
    if not verified_units:
        return results

    target_status = settings.inventory_target_status
    for unit in verified_units:
        try:
            inventory_client.update_progress(
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
