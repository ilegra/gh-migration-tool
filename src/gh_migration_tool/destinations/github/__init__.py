"""GitHub destination integration."""

from gh_migration_tool.destinations.github.auth import GitHubAppTokenProvider
from gh_migration_tool.destinations.github.checks import GitHubActionsTriggerPostCheck
from gh_migration_tool.destinations.github.setup import (
    GitHubApiSetup,
    VerifyRepoExistsGitHubSetup,
)

__all__ = [
    "GitHubActionsTriggerPostCheck",
    "GitHubApiSetup",
    "GitHubAppTokenProvider",
    "VerifyRepoExistsGitHubSetup",
]
