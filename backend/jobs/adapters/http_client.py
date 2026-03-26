"""
Shared HTTP Client Factory

Creates httpx clients that work behind corporate proxies.
Strips SOCKS proxy env vars (ALL_PROXY) since httpx requires socksio for SOCKS,
but the HTTP proxy (HTTP_PROXY/HTTPS_PROXY) works natively.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import httpx

# SOCKS env vars that httpx can't handle without socksio
_SOCKS_VARS = ("ALL_PROXY", "all_proxy", "FTP_PROXY", "ftp_proxy",
               "GRPC_PROXY", "grpc_proxy", "RSYNC_PROXY")


@contextmanager
def safe_client(timeout: int = 15, **kwargs) -> Iterator[httpx.Client]:
    """Context manager that yields an httpx.Client with SOCKS proxy stripped.

    Usage:
        with safe_client(timeout=15) as client:
            resp = client.get("https://example.com")
    """
    saved = {}
    for var in _SOCKS_VARS:
        if var in os.environ:
            saved[var] = os.environ.pop(var)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, **kwargs) as client:
            yield client
    finally:
        os.environ.update(saved)


def safe_get(url: str, timeout: int = 15, **kwargs) -> httpx.Response:
    """One-shot GET that strips SOCKS proxy."""
    with safe_client(timeout=timeout) as client:
        return client.get(url, **kwargs)
