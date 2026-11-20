"""Internal thread-pool execution helpers for per-unit parallel stages."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypeVar

from gh_migration_tool.contracts import MigrationUnit

_T = TypeVar("_T")


def run_parallel_by_unit(
    units: list[MigrationUnit],
    fn: Callable[[MigrationUnit], _T],
    max_workers: int,
    *,
    executor: ThreadPoolExecutor | None = None,
) -> dict[str, _T]:
    """Execute fn concurrently for each MigrationUnit and map results by unit name."""
    if not units:
        return {}
    if executor is None:
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as own_executor:
            return run_parallel_by_unit(units, fn, max_workers, executor=own_executor)
    future_to_name = {executor.submit(fn, unit): unit.name for unit in units}
    return {
        future_to_name[future]: future.result()
        for future in as_completed(future_to_name)
    }
