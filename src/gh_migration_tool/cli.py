"""CLI entrypoint for gh-migration-tool."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import cast

from gh_migration_inventory_manager import Inventory

from gh_migration_tool.contracts import (
    InventoryClient,
    MigrationUnit,
    OriginLock,
    OriginLockResult,
    TokenProvider,
    UnitStatus,
    WaveContext,
)
from gh_migration_tool.destinations.github import GitHubAppTokenProvider
from gh_migration_tool.engine._concurrency import run_parallel_by_unit
from gh_migration_tool.engine.loader import FileWaveLoader
from gh_migration_tool.engine.report import ReportFormatter
from gh_migration_tool.origins.bitbucket_cloud import BitbucketCloudOriginLock
from gh_migration_tool.runner import WaveRunner
from gh_migration_tool.settings import MigrationSettings

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _build_inventory_client(settings: MigrationSettings) -> InventoryClient:
    if not settings.google_spreadsheet_id.strip():
        raise ValueError("GOOGLE_SPREADSHEET_ID is not configured.")
    return cast(
        InventoryClient,
        Inventory(
            spreadsheet_id=settings.google_spreadsheet_id,
            credentials_path=settings.google_credentials_path,
        ),
    )


def _build_token_provider(settings: MigrationSettings) -> TokenProvider | None:
    if not settings.github_app_id or not settings.github_app_installation_id:
        return None
    if not settings.github_app_private_key and not settings.github_app_private_key_path:
        return None
    return GitHubAppTokenProvider(
        app_id=settings.github_app_id,
        installation_id=settings.github_app_installation_id,
        private_key=settings.github_app_private_key,
        private_key_path=settings.github_app_private_key_path,
        api_url=settings.github_api_url,
        timeout=settings.http_timeout_seconds,
    )


def _build_origin_lock(provider: str) -> OriginLock | None:
    if provider == "bitbucket-cloud":
        return BitbucketCloudOriginLock()
    return None


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for executing or unlocking a Migration Wave from a manifest."""
    parser = argparse.ArgumentParser(
        prog="gh-migration-tool",
        description="Execute a 7-stage SCM-to-GitHub Migration Wave.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a Migration Wave manifest")
    run_parser.add_argument("manifest", type=Path, help="Path to YAML/JSON manifest")
    run_parser.add_argument(
        "--format",
        choices=["console", "markdown", "json"],
        default="console",
        help="Report output format (default: console)",
    )
    run_parser.add_argument(
        "--report-out",
        type=Path,
        default=None,
        help="Optional path to write the rendered report",
    )
    run_parser.add_argument(
        "--origin-lock",
        choices=["none", "bitbucket-cloud"],
        default="none",
        help="Optional origin repository lock for verified units (default: none)",
    )

    unlock_parser = subparsers.add_parser(
        "unlock",
        help="Unlock origin repositories for units in a Wave Manifest",
    )
    unlock_parser.add_argument("manifest", type=Path, help="Path to YAML/JSON manifest")
    unlock_parser.add_argument(
        "--origin-lock",
        choices=["bitbucket-cloud"],
        required=True,
        help="Origin repository lock provider to unlock",
    )

    args = parser.parse_args(argv)
    _configure_logging()

    if args.command == "run":
        loader = FileWaveLoader()
        units = loader.load(args.manifest)
        settings = MigrationSettings()
        try:
            inventory_client = _build_inventory_client(settings)
        except ValueError as exc:
            logger.error("Failed to initialize inventory client: %s", exc)
            return 1
        token_provider = _build_token_provider(settings)
        origin_lock = _build_origin_lock(args.origin_lock)
        runner = WaveRunner(
            settings=settings,
            inventory_client=inventory_client,
            token_provider=token_provider,
            origin_lock=origin_lock,
        )
        report = runner.run(units)

        if args.format == "markdown":
            rendered = ReportFormatter.to_markdown(report)
        elif args.format == "json":
            rendered = ReportFormatter.to_json(report)
        else:
            rendered = ReportFormatter.to_console(report)

        if args.report_out is not None:
            args.report_out.write_text(rendered, encoding="utf-8")
        else:
            print(rendered)

        has_failures = any(rec.status != UnitStatus.SUCCEEDED for rec in report.records)
        return 1 if has_failures else 0

    if args.command == "unlock":
        loader = FileWaveLoader()
        units = loader.load(args.manifest)
        settings = MigrationSettings()
        origin_lock = _build_origin_lock(args.origin_lock)
        if origin_lock is None:
            logger.error("Unsupported origin lock provider: %s", args.origin_lock)
            return 1

        context = WaveContext(settings=settings)
        target_units = [u for u in units if u.lock_origin]

        def _unlock_unit(unit: MigrationUnit) -> OriginLockResult:
            try:
                return origin_lock.unlock(unit, context)
            except Exception as exc:
                logger.error(
                    "OriginLock '%s' unlock failed for unit '%s': %s",
                    origin_lock.name,
                    unit.name,
                    exc,
                )
                return OriginLockResult(
                    unit_name=unit.name,
                    passed=False,
                    lock_name=origin_lock.name,
                    message=str(exc),
                )

        results = run_parallel_by_unit(
            target_units, _unlock_unit, max(1, settings.max_workers)
        )
        has_failures = False
        for unit in target_units:
            res = results[unit.name]
            status_label = "UNLOCKED" if res.passed else "FAILED"
            print(f"  [{status_label}] {unit.name} - {res.message}")
            if not res.passed:
                has_failures = True
        return 1 if has_failures else 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
