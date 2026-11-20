"""CLI entrypoint for gh-migration-tool."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from gh_migration_tool.interfaces import OriginDeactivator
from gh_migration_tool.loader import FileWaveLoader
from gh_migration_tool.models import UnitStatus
from gh_migration_tool.runner import WaveRunner
from gh_migration_tool.settings import MigrationSettings
from gh_migration_tool.stages.origin_deactivation import BitbucketCloudOriginDeactivator
from gh_migration_tool.stages.report import ReportFormatter


def _configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for executing a Migration Wave from a manifest file."""
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
        "--origin-deactivator",
        choices=["none", "bitbucket-cloud"],
        default="none",
        help=(
            "Optional origin repository deactivator for verified units (default: none)"
        ),
    )

    args = parser.parse_args(argv)
    _configure_logging()

    if args.command == "run":
        loader = FileWaveLoader()
        units = loader.load(args.manifest)
        settings = MigrationSettings()
        origin_deactivator: OriginDeactivator | None = None
        if args.origin_deactivator == "bitbucket-cloud":
            origin_deactivator = BitbucketCloudOriginDeactivator()
        runner = WaveRunner(
            settings=settings, origin_deactivator=origin_deactivator
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
