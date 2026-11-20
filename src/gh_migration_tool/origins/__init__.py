"""Origin-platform integrations (Bitbucket Cloud, Jenkins).

Git repository mirroring for Azure DevOps and Bitbucket Cloud is handled
by ``gh_migration_tool.engine.git_sync.GitSyncMigrator`` via ``gh-repo-sync``.
"""

from gh_migration_tool.origins.bitbucket_cloud import BitbucketCloudOriginDeactivator
from gh_migration_tool.origins.jenkins import (
    JenkinsJobPreCheck,
    JenkinsTriggerPostCheck,
)

__all__ = [
    "BitbucketCloudOriginDeactivator",
    "JenkinsJobPreCheck",
    "JenkinsTriggerPostCheck",
]
