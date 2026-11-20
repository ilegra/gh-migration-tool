"""GitHub App JWT signing, installation access token provider, and API headers."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import jwt
import requests

from gh_migration_tool.engine._http import (
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    build_http_session,
)

logger = logging.getLogger(__name__)


def github_headers(token: str) -> dict[str, str]:
    """Build standard GitHub REST API request headers."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


class GitHubAppTokenProvider:
    """Mints and caches GitHub App Installation Access Tokens."""

    def __init__(
        self,
        app_id: str,
        installation_id: str,
        private_key: str = "",
        private_key_path: str | None = None,
        api_url: str = "https://api.github.com",
        session: requests.Session | None = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    ) -> None:
        if not app_id or not str(app_id).strip():
            raise ValueError("GitHub App ID cannot be empty.")
        if not installation_id or not str(installation_id).strip():
            raise ValueError("GitHub App Installation ID cannot be empty.")

        self.app_id = str(app_id).strip()
        self.installation_id = str(installation_id).strip()
        self.api_url = api_url.rstrip("/")
        self._private_key = self._resolve_private_key(private_key, private_key_path)
        self._timeout = timeout
        self._session = session or build_http_session(timeout=timeout)
        self._lock = threading.Lock()
        self._cached_token: str | None = None
        self._expires_at_epoch: float = 0.0

    @staticmethod
    def _resolve_private_key(private_key: str, private_key_path: str | None) -> str:
        if private_key and private_key.strip():
            return private_key.strip()
        if private_key_path and str(private_key_path).strip():
            path = Path(str(private_key_path).strip())
            try:
                content = path.read_text(encoding="utf-8").strip()
            except (FileNotFoundError, IsADirectoryError) as exc:
                raise FileNotFoundError(
                    f"GitHub App private key file not found: '{path}'."
                ) from exc
            if not content:
                raise ValueError(f"GitHub App private key file is empty: '{path}'.")
            return content
        raise ValueError(
            "GitHub App private key must be provided via "
            "private_key or private_key_path."
        )

    def _create_jwt(self) -> str:
        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + 540,
            "iss": self.app_id,
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    def get_token(self) -> str:
        """Return a valid Installation Access Token, refreshing if expired."""
        with self._lock:
            now = time.time()
            if self._cached_token and now < (self._expires_at_epoch - 60):
                return self._cached_token

            app_jwt = self._create_jwt()
            url = (
                f"{self.api_url}/app/installations/{self.installation_id}/access_tokens"
            )
            headers = github_headers(app_jwt)
            logger.debug(
                "Requesting GitHub App installation token for installation %s",
                self.installation_id,
            )
            try:
                response = self._session.post(
                    url, headers=headers, timeout=self._timeout
                )
            except requests.RequestException as exc:
                raise RuntimeError(
                    f"Failed to obtain GitHub App installation token: {exc}"
                ) from exc
            if response.status_code not in (200, 201):
                raise RuntimeError(
                    f"Failed to obtain GitHub App installation token: "
                    f"HTTP {response.status_code} - {response.text[:300]}"
                )

            data = response.json()
            token = str(data.get("token", "")).strip()
            if not token:
                raise RuntimeError("GitHub App token response did not contain 'token'.")

            expires_at_raw = data.get("expires_at")
            if isinstance(expires_at_raw, str) and expires_at_raw:
                dt = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
                self._expires_at_epoch = dt.astimezone(timezone.utc).timestamp()
            else:
                self._expires_at_epoch = now + 3600

            self._cached_token = token
            return token
