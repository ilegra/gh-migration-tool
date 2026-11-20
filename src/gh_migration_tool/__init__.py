"""gh-migration-tool: Extensible Python SDK for SCM-to-GitHub migrations."""

from gh_migration_tool.contracts import (
    CheckResult,
    GitHubSetup,
    InventoryUpdateResult,
    MigrationStageResult,
    MigrationUnit,
    Migrator,
    OriginDeactivationResult,
    OriginDeactivator,
    OriginSnapshot,
    PipelineMigrationResult,
    PipelineMigrator,
    PostCheck,
    PreCheck,
    SetupResult,
    UnitExecutionRecord,
    UnitStatus,
    WaveContext,
    WaveLoader,
    WaveReport,
)
from gh_migration_tool.destinations.github import (
    GitHubActionsTriggerPostCheck,
    GitHubApiSetup,
    GitHubAppTokenProvider,
    VerifyRepoExistsGitHubSetup,
)
from gh_migration_tool.engine import (
    FileWaveLoader,
    GitSyncMigrator,
    ReportFormatter,
    SourceDriftPostCheck,
)
from gh_migration_tool.origins import (
    BitbucketCloudOriginDeactivator,
    JenkinsJobPreCheck,
    JenkinsTriggerPostCheck,
)
from gh_migration_tool.runner import WaveRunner
from gh_migration_tool.settings import MigrationSettings

__version__ = "0.1.0"

__all__ = [
    "BitbucketCloudOriginDeactivator",
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
    "OriginDeactivationResult",
    "OriginDeactivator",
    "OriginSnapshot",
    "PipelineMigrationResult",
    "PipelineMigrator",
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
