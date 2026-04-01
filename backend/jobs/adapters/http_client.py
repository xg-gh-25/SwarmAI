"""
Shared HTTP Client Factory

Creates httpx clients that ignore all env proxy vars (trust_env=False).
This avoids both SOCKS proxies (which httpx can't handle without socksio)
and Claude Code's sandbox proxy (which only works inside the sandbox).

Includes retry-with-backoff for transient DNS and connection errors, which are
common in launchd environments (sleep/wake, VPN disconnect).
"""

from __future__ import annotations

import logging
import socket
import time
from contextlib import contextmanager
from typing import Iterator

import httpx

logger = logging.getLogger(__name__)

# Transient errors that justify a retry (DNS, connection reset, timeout)
_RETRYABLE = (
    socket.gaierror,           # DNS resolution failure
    ConnectionResetError,
    ConnectionRefusedError,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    OSError,                   # Catches [Errno 8] nodename nor servname
)

# Retry config
_MAX_RETRIES = 2
_RETRY_DELAY_SECS = 2.0


@contextmanager
def safe_client(timeout: int = 15, **kwargs) -> Iterator[httpx.Client]:
    """Context manager that yields an httpx.Client ignoring env proxy vars.

    The yielded client is a RetryClient wrapper that automatically retries
    transient errors (DNS, connection reset) with exponential backoff.

    Thread-safe: uses ``trust_env=False`` so httpx never reads proxy env vars.

    Usage:
        with safe_client(timeout=15) as client:
            resp = client.get("https://example.com")
    """
    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        trust_env=False,      # ignore ALL env proxy vars — thread-safe
        **kwargs,
    ) as client:
        yield _RetryClient(client)


class _RetryClient:
    """Thin wrapper around httpx.Client that retries transient errors.

    Delegates all attribute access to the underlying client, but wraps
    .get(), .post(), .request() with retry logic for DNS/connection errors.
    Used internally by safe_client() — never instantiated directly.
    """

    def __init__(self, client: httpx.Client, retries: int = _MAX_RETRIES):
        self._client = client
        self._retries = retries

    def __getattr__(self, name: str):
        attr = getattr(self._client, name)
        if name in ("get", "post", "put", "patch", "delete", "head", "options", "request"):
            return self._wrap_with_retry(attr, name)
        return attr

    def _wrap_with_retry(self, method, method_name: str):
        def wrapper(*args, **kwargs):
            last_err: Exception | None = None
            for attempt in range(1 + self._retries):
                try:
                    return method(*args, **kwargs)
                except _RETRYABLE as e:
                    last_err = e
                    if attempt < self._retries:
                        delay = _RETRY_DELAY_SECS * (2 ** attempt)
                        url_hint = args[0] if args else kwargs.get("url", "?")
                        logger.debug(
                            "Transient %s %s (attempt %d/%d): %s — retrying in %.1fs",
                            method_name.upper(), url_hint,
                            attempt + 1, 1 + self._retries, e, delay,
                        )
                        time.sleep(delay)
                    else:
                        url_hint = args[0] if args else kwargs.get("url", "?")
                        logger.warning(
                            "Failed %s %s after %d attempts: %s",
                            method_name.upper(), url_hint, 1 + self._retries, e,
                        )
            raise last_err  # type: ignore[misc]
        return wrapper


def safe_get(url: str, timeout: int = 15, **kwargs) -> httpx.Response:
    """One-shot GET with proxy bypass and retry for transient errors.

    Convenience wrapper — creates a safe_client, makes one GET.
    For multiple requests, use safe_client() context manager directly.
    """
    with safe_client(timeout=timeout) as client:
        return client.get(url, **kwargs)
