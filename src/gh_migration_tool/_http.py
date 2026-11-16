"""Internal HTTP header and URL formatting helpers shared across modules."""

from __future__ import annotations

import base64
import urllib.parse


def github_headers(token: str) -> dict[str, str]:
    """Build standard GitHub REST API request headers."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def build_jenkins_headers(username: str | None, token: str) -> dict[str, str]:
    """Build Jenkins Authorization headers using Basic or Bearer credentials."""
    if username and token:
        encoded = base64.b64encode(f"{username}:{token}".encode()).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def format_jenkins_job_path(job_name: str) -> str:
    """Convert a slash-delimited Jenkins job name into a Jenkins API job path."""
    parts = [p.strip() for p in job_name.strip("/").split("/") if p.strip()]
    return "/".join(f"job/{urllib.parse.quote(part)}" for part in parts)
