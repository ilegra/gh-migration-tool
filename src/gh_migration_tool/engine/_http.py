"""Internal HTTP session, timeout adapter, and retry helpers."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

DEFAULT_HTTP_TIMEOUT_SECONDS: float = 30.0
DEFAULT_HTTP_MAX_RETRIES: int = 3
DEFAULT_HTTP_BACKOFF_FACTOR: float = 0.5
RETRYABLE_STATUS_CODES: tuple[int, ...] = (429, 500, 502, 503, 504)
DEFAULT_RETRY_METHODS: frozenset[str] = frozenset(
    {"GET", "HEAD", "OPTIONS", "PUT", "DELETE", "POST"}
)


class TimeoutHTTPAdapter(HTTPAdapter):
    """HTTPAdapter that enforces a non-None default timeout on every request."""

    def __init__(
        self,
        *args: Any,
        timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        **kwargs: Any,
    ) -> None:
        self.timeout = timeout
        super().__init__(*args, **kwargs)

    def send(
        self,
        request: requests.PreparedRequest,
        stream: bool = False,
        timeout: float | tuple[float, float] | tuple[float, None] | None = None,
        verify: bool | str = True,
        cert: bytes | str | tuple[bytes | str, bytes | str] | None = None,
        proxies: Any = None,
    ) -> requests.Response:
        """Send request with a default timeout when none is provided."""
        if timeout is None:
            timeout = self.timeout
        return super().send(
            request,
            stream=stream,
            timeout=timeout,
            verify=verify,
            cert=cert,
            proxies=proxies,
        )


def build_http_session(
    timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_HTTP_MAX_RETRIES,
    backoff_factor: float = DEFAULT_HTTP_BACKOFF_FACTOR,
    allowed_methods: Collection[str] = DEFAULT_RETRY_METHODS,
) -> requests.Session:
    """Build a requests.Session with timeout and exponential backoff retries."""
    session = requests.Session()
    retry = Retry(
        total=max_retries,
        backoff_factor=backoff_factor,
        status_forcelist=RETRYABLE_STATUS_CODES,
        allowed_methods=frozenset(m.upper() for m in allowed_methods),
        respect_retry_after_header=True,
    )
    adapter = TimeoutHTTPAdapter(timeout=timeout, max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
