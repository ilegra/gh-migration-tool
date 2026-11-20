"""Destination-platform integrations (GitHub)."""

from gh_migration_tool.destinations.github import (
    GitHubActionsTriggerPostCheck,
    GitHubApiSetup,
    GitHubAppTokenProvider,
    VerifyRepoExistsGitHubSetup,
)

__all__ = [
    "GitHubActionsTriggerPostCheck",
    "GitHubApiSetup",
    "GitHubAppTokenProvider",
    "VerifyRepoExistsGitHubSetup",
]
