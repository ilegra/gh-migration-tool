"""Built-in stage implementations for gh-migration-tool."""

from gh_migration_tool.stages.github_setup import (
    GitHubApiSetup,
    VerifyRepoExistsGitHubSetup,
)
from gh_migration_tool.stages.inventory import update_inventory
from gh_migration_tool.stages.migration import GitSyncMigrator
from gh_migration_tool.stages.origin_deactivation import BitbucketCloudOriginDeactivator
from gh_migration_tool.stages.post_checks import (
    GitHubActionsTriggerPostCheck,
    JenkinsTriggerPostCheck,
    SourceDriftPostCheck,
)
from gh_migration_tool.stages.pre_checks import JenkinsJobPreCheck
from gh_migration_tool.stages.prepare import prepare_candidates
from gh_migration_tool.stages.report import ReportFormatter, build_wave_report

__all__ = [
    "BitbucketCloudOriginDeactivator",
    "GitHubActionsTriggerPostCheck",
    "GitHubApiSetup",
    "GitSyncMigrator",
    "JenkinsJobPreCheck",
    "JenkinsTriggerPostCheck",
    "ReportFormatter",
    "SourceDriftPostCheck",
    "VerifyRepoExistsGitHubSetup",
    "build_wave_report",
    "prepare_candidates",
    "update_inventory",
]
