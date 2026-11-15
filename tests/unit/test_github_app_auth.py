"""Unit tests for GitHubAppTokenProvider."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import responses
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from gh_migration_tool.auth.github_app import GitHubAppTokenProvider


def _generate_test_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem_bytes.decode("utf-8")


@responses.activate
def test_given_valid_app_credentials_when_getting_token_then_returns_and_caches_token() -> (
    None
):
    pem = _generate_test_pem()
    responses.add(
        responses.POST,
        "https://api.github.com/app/installations/999/access_tokens",
        json={"token": "ghs_mock_token_123", "expires_at": "2099-01-01T00:00:00Z"},
        status=201,
    )

    provider = GitHubAppTokenProvider(
        app_id="12345",
        installation_id="999",
        private_key=pem,
    )

    first = provider.get_token()
    second = provider.get_token()

    assert first == "ghs_mock_token_123"
    assert second == "ghs_mock_token_123"
    assert len(responses.calls) == 1


def test_given_api_error_when_getting_token_then_raises_runtime_error() -> None:
    pem = _generate_test_pem()
    session = MagicMock()
    session.post.return_value = MagicMock(status_code=401, text="Unauthorized")

    provider = GitHubAppTokenProvider(
        app_id="12345",
        installation_id="999",
        private_key=pem,
        session=session,
    )

    with pytest.raises(RuntimeError, match="HTTP 401"):
        provider.get_token()


def test_given_missing_app_id_when_initializing_then_raises_value_error() -> None:
    with pytest.raises(ValueError, match="App ID"):
        GitHubAppTokenProvider(app_id="", installation_id="999", private_key="key")


def test_given_missing_private_key_when_initializing_then_raises_value_error() -> None:
    with pytest.raises(ValueError, match="private key"):
        GitHubAppTokenProvider(app_id="123", installation_id="999")
