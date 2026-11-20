"""Jenkins legacy CI origin integration."""

from gh_migration_tool.origins.jenkins.checks import (
    JenkinsJobPreCheck,
    JenkinsTriggerPostCheck,
)

__all__ = [
    "JenkinsJobPreCheck",
    "JenkinsTriggerPostCheck",
]
