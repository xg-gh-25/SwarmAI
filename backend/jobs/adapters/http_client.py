"""
Shared HTTP Client Factory

Creates httpx clients that ignore all env proxy vars (trust_env=False).
This avoids both SOCKS proxies (which httpx can't handle without socksio)
and Claude Code's sandbox proxy (which only works inside the sandbox).

Includes retry-with-backoff for transient DNS and connection errors, which are
common in launchd environments (sleep/wake, VPN disconnect).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import time
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

# ── SSRF egress guard (BSC-A2, run_cd11637a) ────────────────────────────────
# The shared outbound-fetch chokepoint is reused by the signal-fetch adapters
# (rss/github_trending/github_releases/weibo_trending/hacker_news/trending/
# eastmoney_market). External feed content can drive fetches + redirects toward
# internal/metadata endpoints. This guard validates the RESOLVED IP at connect
# time (rebind-safe — see _ValidatingTransport) and rejects non-https schemes.
#
# SCOPE: only clients built via safe_client()/safe_get() below. Intentionally
# NOT covering: s_notify (user webhooks may be http/LAN), executor Slack senders
# (own client), and the localhost daemon probes in eval_scheduled.py /
# session_health_probe.py (urllib to 127.0.0.1:18321 — a private IP the guard
# would correctly reject, so they must stay off this path). Those are separate
# policies, documented out of scope in run_cd11637a's plan.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")   # RFC 6598 — not is_private
_ZERO_8 = ipaddress.ip_network("0.0.0.0/8")


class SSRFBlocked(Exception):
    """Raised when an outbound request targets a disallowed scheme or IP.

    Deliberately a bare Exception (NOT in _RETRYABLE) — a blocked egress is a
    security decision, never a transient error to retry.
    """


def _classify_ip(ip_str: str) -> bool:
    """True if this IP MUST be blocked (non-routable / internal / metadata)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable → fail closed
    # Normalize IPv4-mapped IPv6 (::ffff:127.0.0.1 → 127.0.0.1) before classifying.
    mapped = getattr(ip, "ipv4_mapped", None)
    eff = mapped if mapped is not None else ip
    if (eff.is_private or eff.is_loopback or eff.is_link_local or eff.is_reserved
            or eff.is_multicast or eff.is_unspecified):
        return True
    if eff.version == 4 and (eff in _CGNAT or eff in _ZERO_8):
        return True
    return False


def is_blocked_ip_literal(host: str | None) -> bool:
    """PUBLIC SSRF helper for write-time member validation (community_api).

    True IFF `host` is an IP-LITERAL that must be blocked (private / loopback /
    link-local / reserved / metadata / CGNAT). A DNS HOSTNAME (not an IP literal)
    returns False — it is NOT judged here; the actual outbound fetch re-resolves
    and egress-guards it via ``_validate_egress`` (this is the documented
    write-time hygiene vs. connect-time-guard split). Pass ``urlparse(url).hostname``
    (already strips :port, [ipv6] brackets, and user:pw@ — do NOT pass ``.netloc``,
    which keeps them and makes every port/creds/ipv6 form fail the IP parse and
    slip through). None/empty → False (a URL with no host fails other checks first).
    """
    if not host:
        return False
    try:
        ipaddress.ip_address(host)   # only classify when host IS an IP literal
    except ValueError:
        return False                 # DNS hostname → not judged here (egress-guard owns it)
    return _classify_ip(host)


def _resolve_ips(host: str) -> list[str]:
    """Resolve a host to ALL its IPs (v4 + v6). Isolated for test injection."""
    infos = socket.getaddrinfo(host, None)
    return [info[4][0] for info in infos]


def _validate_egress(url: str) -> str:
    """Validate an outbound URL. Returns the validated first IP, or raises.

    Rejects non-https schemes and any host where ANY resolved IP is disallowed
    (checks all A/AAAA records — a dual-stack one-bad-IP host is rejected).

    ⚠️ RESIDUAL TOCTOU (honest scope — do NOT claim rebind-safe):
    this validates the IP that getaddrinfo returns HERE, but the connection made
    by super().handle_request() re-resolves the hostname at socket-connect time.
    A DNS-rebinding resolver (public on this call, private on the connect call)
    is therefore NARROWED but NOT eliminated. This is accepted for the current
    threat model — the feed hosts are config-pinned first-party domains and the
    external-content URLs (RSS link/href) are display-only, never re-fetched — so
    the guard is defense-in-depth against static-internal-IP + redirect + alternate
    -encoding SSRF, not a hard rebind defense. TRUE pinning (connect to the
    returned IP with Host/SNI preserved) is a documented follow-up if a live
    attacker-controlled-host fetch path is ever added. The returned IP is currently
    advisory (logged), not used to pin the socket.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        logger.warning("SSRF egress blocked (scheme=%s): %s", parts.scheme, url)
        raise SSRFBlocked(f"scheme not allowed: {parts.scheme!r} (https only)")
    host = parts.hostname
    if not host:
        raise SSRFBlocked(f"no host in url: {url!r}")
    try:
        ips = _resolve_ips(host)
    except socket.gaierror as e:
        raise SSRFBlocked(f"DNS resolution failed for {host!r}: {e}") from e
    if not ips:
        raise SSRFBlocked(f"no IPs resolved for {host!r}")
    for ip in ips:
        if _classify_ip(ip):
            logger.warning("SSRF egress blocked (ip=%s): %s", ip, url)
            raise SSRFBlocked(f"host {host!r} resolves to disallowed IP {ip}")
    return ips[0]


class _ValidatingTransport(httpx.HTTPTransport):
    """httpx transport that validates egress at CONNECT time (per hop).

    Because httpx drives redirects (follow_redirects stays True on the client),
    handle_request fires once PER hop — so every redirect target is re-validated
    here (verified: httpx 0.28.1 calls handle_request per redirect hop), closing
    the manual-redirect-loop and blind-follow gaps. See _validate_egress for the
    residual DNS-rebind TOCTOU caveat — this NARROWS but does not eliminate it,
    which is accepted for the current (config-pinned feed) threat model.
    """

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        _validate_egress(str(request.url))
        return super().handle_request(request)

# Transient errors that justify a retry (DNS, connection reset, timeout).
# Note: OSError was intentionally removed — it's overly broad (catches file
# permission errors, etc). socket.gaierror (a subclass of OSError) covers DNS
# failures including [Errno 8] "nodename nor servname". ConnectionError covers
# all connection-level failures (Reset, Refused, BrokenPipe, Aborted).
_RETRYABLE = (
    socket.gaierror,           # DNS resolution failure (subclass of OSError)
    ConnectionError,           # ConnectionReset, ConnectionRefused, BrokenPipe, ConnectionAborted
    httpx.ConnectError,
    httpx.ConnectTimeout,
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
        follow_redirects=True,   # httpx drives redirects; the transport re-validates each hop
        trust_env=False,         # ignore ALL env proxy vars — thread-safe
        transport=_ValidatingTransport(),  # SSRF egress guard (BSC-A2) — validates connect IP
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
