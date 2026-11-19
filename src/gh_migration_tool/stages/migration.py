"""Step 4: Built-in Migration stage wrapping gh-repo-sync v1.1.1."""

from __future__ import annotations

import functools
import logging
import shutil
import subprocess  # nosec B404
import tempfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from src.config import SyncConfig
from src.core.sync import SyncManager
from src.git.client import Git
from src.providers.factory import get_origin_provider
from src.providers.github import GitHubProvider

from gh_migration_tool.interfaces import Migrator, WaveContext
from gh_migration_tool.models import (
    MigrationStageResult,
    MigrationUnit,
    OriginSnapshot,
)

logger = logging.getLogger(__name__)


_BITBUCKET_PROVIDERS = frozenset({"bitbucket", "bitbucket_cloud", "bitbucket-cloud"})


def _default_origin_username(
    parsed_url: urllib.parse.ParseResult, origin_provider: str | None = None
) -> str:
    explicit = (origin_provider or "").strip().lower()
    if explicit:
        return "x-token-auth" if explicit in _BITBUCKET_PROVIDERS else "x-access-token"

    hostname = (parsed_url.hostname or "").lower()
    if hostname == "bitbucket.org" or hostname.endswith(".bitbucket.org"):
        return "x-token-auth"
    return "x-access-token"


def build_authenticated_origin_url(
    origin_url: str,
    token: str,
    username: str | None = None,
    origin_provider: str | None = None,
) -> str:
    """Inject authentication credentials into an HTTPS origin URL."""
    if not token:
        return origin_url
    parsed = urllib.parse.urlparse(origin_url)
    if parsed.scheme not in ("http", "https"):
        return origin_url
    effective_user = username or _default_origin_username(parsed, origin_provider)
    user = urllib.parse.quote(effective_user, safe="")
    secret = urllib.parse.quote(token, safe="")
    netloc = f"{user}:{secret}@{parsed.hostname}"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urllib.parse.urlunparse(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


@functools.lru_cache(maxsize=1)
def _resolve_git_bin() -> str:
    return shutil.which("git") or "/usr/bin/git"


def capture_origin_branch_heads(
    origin_url: str,
    token: str,
    username: str | None = None,
    origin_provider: str | None = None,
) -> dict[str, str]:
    """Run git ls-remote --heads against Origin and return {branch: sha}."""
    git_bin = _resolve_git_bin()
    auth_url = build_authenticated_origin_url(
        origin_url, token, username, origin_provider
    )
    proc = subprocess.run(  # nosec B603
        [git_bin, "ls-remote", "--heads", auth_url],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.replace(token, "***") if token else proc.stderr
        raise RuntimeError(f"git ls-remote failed: {stderr.strip()}")

    heads: dict[str, str] = {}
    prefix = "refs/heads/"
    for line in proc.stdout.splitlines():
        parts = line.strip().split("\t", 1)
        if len(parts) == 2 and parts[1].startswith(prefix):
            branch_name = parts[1][len(prefix) :]
            heads[branch_name] = parts[0]
    return heads


class GitSyncMigrator(Migrator):
    """Migrates a repository using gh-repo-sync while capturing an Origin Snapshot."""

    def migrate(
        self, unit: MigrationUnit, context: WaveContext
    ) -> MigrationStageResult:
        """Capture Origin Snapshot and execute gh-repo-sync SyncManager."""
        if context.github_token_provider is None:
            return MigrationStageResult(
                unit_name=unit.name,
                passed=False,
                errors=["GitHubAppTokenProvider is not configured."],
            )

        try:
            branch_heads = capture_origin_branch_heads(
                unit.origin_url,
                context.settings.origin_token,
                context.settings.origin_username,
                unit.origin_provider,
            )
            snapshot = OriginSnapshot(
                captured_at=datetime.now(timezone.utc),
                branch_heads=branch_heads,
            )
        except Exception as exc:
            logger.error(
                "Failed to capture origin snapshot for '%s': %s", unit.name, exc
            )
            return MigrationStageResult(
                unit_name=unit.name,
                passed=False,
                errors=[f"Origin snapshot failed: {exc}"],
            )

        dest_token = context.github_token_provider.get_token()
        branch_mapping_raw = ",".join(
            f"{src}:{dst}" for src, dst in unit.branch_mapping.items()
        )
        sync_config = SyncConfig(
            ORIGIN_URL=unit.origin_url,
            ORIGIN_TOKEN=context.settings.origin_token,
            ORIGIN_USERNAME=context.settings.origin_username,
            ORIGIN_PROVIDER=unit.origin_provider,
            DESTINATION_URL=unit.destination_url,
            DESTINATION_TOKEN=dest_token,
            ORIGIN_EXCLUSIVE_PATHS=",".join(unit.origin_exclusive_paths),
            DESTINATION_EXCLUSIVE_PATHS=",".join(unit.destination_exclusive_paths),
            BRANCH_MAPPING=branch_mapping_raw,
        )

        temp_dir = tempfile.mkdtemp(prefix=f"gh_mig_{unit.name}_")
        repo_dir = str(Path(temp_dir) / "repo")
        Path(repo_dir).mkdir(parents=True, exist_ok=True)

        try:
            mask_patterns = [
                p for p in (context.settings.origin_token, dest_token) if p
            ]
            git_client = Git(repo_dir, mask_patterns=mask_patterns)
            origin_provider = get_origin_provider(
                config=sync_config,
                git_client=git_client,
                remote_name="origin_remote",
            )
            destination_provider = GitHubProvider(
                repo_url=sync_config.destination_url,
                token=sync_config.destination_token,
                git=git_client,
                remote_name="destination_remote",
            )
            manager = SyncManager(
                config=sync_config,
                origin_provider=origin_provider,
                destination_provider=destination_provider,
                git_client=git_client,
            )
            sync_result = manager.execute()
        except Exception as exc:
            logger.error("Migration sync failed for '%s': %s", unit.name, exc)
            return MigrationStageResult(
                unit_name=unit.name,
                passed=False,
                origin_snapshot=snapshot,
                errors=[str(exc)],
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        passed = (not sync_result.already_disabled) and (len(sync_result.errors) == 0)
        errors = list(sync_result.errors)
        if sync_result.already_disabled:
            errors.append("Origin repository is disabled or archived.")

        return MigrationStageResult(
            unit_name=unit.name,
            passed=passed,
            origin_snapshot=snapshot,
            evaluated_branches=list(sync_result.evaluated_branches),
            updated_branches=list(sync_result.updated_branches),
            removed_branches=list(sync_result.removed_branches),
            synced_tags=list(sync_result.synced_tags),
            already_disabled=sync_result.already_disabled,
            errors=errors,
        )
