"""Unit tests for shared HTTP session, timeout adapter, and retry configuration."""

from __future__ import annotations

from unittest.mock import MagicMock

import requests
import responses

from gh_migration_tool.engine._http import (
    DEFAULT_HTTP_BACKOFF_FACTOR,
    DEFAULT_HTTP_MAX_RETRIES,
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    RETRYABLE_STATUS_CODES,
    TimeoutHTTPAdapter,
    build_http_session,
)


def test_given_default_args_when_building_http_session_then_mounts_timeout_and_retry_adapters() -> (
    None
):
    session = build_http_session()

    for scheme in ("https://", "http://"):
        adapter = session.get_adapter(f"{scheme}example.com")
        assert isinstance(adapter, TimeoutHTTPAdapter)
        assert adapter.timeout == DEFAULT_HTTP_TIMEOUT_SECONDS
        assert adapter.max_retries.total == DEFAULT_HTTP_MAX_RETRIES
        assert adapter.max_retries.backoff_factor == DEFAULT_HTTP_BACKOFF_FACTOR
        assert (
            tuple(adapter.max_retries.status_forcelist or ()) == RETRYABLE_STATUS_CODES
        )
        assert adapter.max_retries.respect_retry_after_header is True
        assert "POST" in (adapter.max_retries.allowed_methods or ())


def test_given_session_without_explicit_timeout_when_sending_request_then_applies_default_timeout() -> (
    None
):
    adapter = TimeoutHTTPAdapter(timeout=12.5)
    prepared = requests.Request("GET", "https://example.com").prepare()
    captured: dict[str, object] = {}

    def fake_urlopen(*_args: object, **kwargs: object) -> MagicMock:
        captured["timeout"] = kwargs.get("timeout")
        resp = MagicMock()
        resp.status = 200
        resp.headers = {}
        resp.reason = "OK"
        resp.read = MagicMock(return_value=b"")
        return resp

    mock_conn = MagicMock()
    mock_conn.urlopen.side_effect = fake_urlopen
    adapter.get_connection_with_tls_context = MagicMock(return_value=mock_conn)  # type: ignore[method-assign]

    adapter.send(prepared)
    default_timeout = captured["timeout"]
    assert getattr(default_timeout, "connect_timeout", None) == 12.5
    assert getattr(default_timeout, "read_timeout", None) == 12.5

    adapter.send(prepared, timeout=5.0)
    explicit_timeout = captured["timeout"]
    assert getattr(explicit_timeout, "connect_timeout", None) == 5.0
    assert getattr(explicit_timeout, "read_timeout", None) == 5.0


@responses.activate
def test_given_transient_503_then_200_when_calling_session_then_retries_and_succeeds() -> (
    None
):
    responses.add(responses.POST, "https://example.com/resource", status=503)
    responses.add(
        responses.POST,
        "https://example.com/resource",
        json={"ok": True},
        status=200,
    )

    session = build_http_session(timeout=10.0, max_retries=2, backoff_factor=0.01)
    resp = session.post("https://example.com/resource")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert len(responses.calls) == 2
