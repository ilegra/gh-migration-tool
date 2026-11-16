"""gh-migration-tool: Extensible Python SDK for SCM-to-GitHub migrations."""

from gh_migration_tool.auth.github_app import GitHubAppTokenProvider
from gh_migration_tool.interfaces import (
    GitHubSetup,
    Migrator,
    PostCheck,
    PreCheck,
    WaveContext,
)
from gh_migration_tool.loader import FileWaveLoader, WaveLoader
from gh_migration_tool.models import (
    CheckResult,
    InventoryUpdateResult,
    MigrationStageResult,
    MigrationUnit,
    OriginSnapshot,
    SetupResult,
    UnitExecutionRecord,
    UnitStatus,
    WaveReport,
)
from gh_migration_tool.runner import WaveRunner
from gh_migration_tool.settings import MigrationSettings
from gh_migration_tool.stages import (
    GitHubActionsTriggerPostCheck,
    GitHubApiSetup,
    GitSyncMigrator,
    JenkinsJobPreCheck,
    JenkinsTriggerPostCheck,
    ReportFormatter,
    SourceDriftPostCheck,
    VerifyRepoExistsGitHubSetup,
)

__version__ = "0.1.0"

__all__ = [
    "CheckResult",
    "FileWaveLoader",
    "GitHubActionsTriggerPostCheck",
    "GitHubApiSetup",
    "GitHubAppTokenProvider",
    "GitHubSetup",
    "GitSyncMigrator",
    "InventoryUpdateResult",
    "JenkinsJobPreCheck",
    "JenkinsTriggerPostCheck",
    "MigrationSettings",
    "MigrationStageResult",
    "MigrationUnit",
    "Migrator",
    "OriginSnapshot",
    "PostCheck",
    "PreCheck",
    "ReportFormatter",
    "SetupResult",
    "SourceDriftPostCheck",
    "UnitExecutionRecord",
    "UnitStatus",
    "VerifyRepoExistsGitHubSetup",
    "WaveContext",
    "WaveLoader",
    "WaveReport",
    "WaveRunner",
    "__version__",
]
