"""Hive app-layer authentication middleware (pure ASGI).

WHY THIS EXISTS
---------------
The backend's privileged route groups (``/api/workspace/*``, ``/api/jobs/*``,
``/api/autonomous-jobs/*`` — and in fact every ``/api/*`` route) historically had
ZERO application-layer authentication. In a Hive (EC2) deployment the ONLY thing
protecting them was a single network-edge gate: ``basic_auth @protected`` in
``hive/Caddyfile``. That is one layer of defense — if Caddy is bypassed
(origin-direct reach to 127.0.0.1:18321, a same-VPC path, an SSRF), or is ever
misconfigured, the routes become unauthenticated-RCE-grade (``/api/jobs`` schedules
execution; ``/api/workspace`` reads/writes arbitrary files). CloudSec flagged this.

This middleware adds the INNER layer of defense-in-depth: the application validates
the SAME credential Caddy uses (``HIVE_USER`` / ``HIVE_PASS_HASH``), so a request that
reaches the app without that credential is rejected regardless of how it got past the
edge. Caddy stays as the OUTER layer.

DESIGN NOTES (each earned — do not "simplify" away)
---------------------------------------------------
* **Pure ASGI, NOT ``BaseHTTPMiddleware``.** ``BaseHTTPMiddleware`` wraps the response
  ``send`` and buffers ``StreamingResponse`` — it would silently break the SSE
  endpoints (``/api/chat/stream`` etc. in ``routers/chat.py``), turning token-by-token
  streaming into one buffered burst with NO error. A pure ASGI middleware only inspects
  the request ``scope`` headers and either sends a 401 itself or calls the inner app
  unchanged, so streaming responses pass through untouched. (Gate-1 finding, verified.)
* **Mode-gated.** Enforcement is active ONLY when ``SWARMAI_MODE=hive``. Desktop
  (daemon/subprocess) binds 127.0.0.1 (``config.host``) and has no login flow — forcing
  auth there would lock out the single local user. Off-hive, this middleware is a
  pure pass-through.
* **Fail-closed.** Missing header, malformed base64, non-Basic scheme, wrong user,
  unset ``hive_pass_hash``, or ANY exception during verification → 401. It never
  fails open to "no auth".
* **Public allow-list is tiny + explicit.** ``/health`` (monitors, matches Caddy's
  ``@protected not path /health``) and ``OPTIONS`` preflight (so browser CORS is not
  broken — the CORS middleware must still see the preflight).
* **Reuses ``core.auth.verify_password``** (bcrypt) — no new hash comparison; the same
  primitive the JWT auth path uses. ``HIVE_PASS_HASH`` is a bcrypt hash (as produced by
  ``caddy hash-password``), so ``bcrypt.checkpw`` validates it directly.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from config import settings
from core.auth import verify_password

logger = logging.getLogger(__name__)

# Public allow-list: paths reachable WITHOUT a credential even in hive mode.
# Keep this tiny and explicit — every entry is an unauthenticated attack surface.
# /health: liveness/readiness monitors (mirrors hive/Caddyfile "@protected not path /health").
_PUBLIC_PATHS: frozenset[str] = frozenset({"/health"})


def _hive_enforcement_active() -> bool:
    """True only in hive mode. Read the env var directly (not main._detect_run_mode)
    to avoid a circular import; main.py reads the same SWARMAI_MODE var."""
    return os.environ.get("SWARMAI_MODE", "daemon") == "hive"


def _extract_basic_credential(scope: Scope) -> tuple[str, str] | None:
    """Parse ``Authorization: Basic <base64(user:pass)>`` from the ASGI scope headers.

    Returns ``(user, password)`` or ``None`` if the header is absent/malformed/not-Basic.
    Fail-closed: any parse problem returns None (→ the caller denies)."""
    # scope["headers"] is a list of (name, value) byte-tuples, names lower-cased.
    raw = None
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            raw = value
            break
    if raw is None:
        return None
    try:
        decoded = raw.decode("latin-1").strip()
    except (UnicodeDecodeError, AttributeError):
        return None
    scheme, _, param = decoded.partition(" ")
    if scheme.lower() != "basic" or not param:
        return None
    try:
        userpass = base64.b64decode(param, validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    user, sep, password = userpass.partition(":")
    if not sep:
        return None
    return user, password


class HiveAuthMiddleware:
    """Pure-ASGI defense-in-depth auth layer for hive deployments.

    Off-hive: transparent pass-through. In hive: HTTP Basic auth on every request
    except the public allow-list + OPTIONS preflight, validated against
    ``settings.hive_user`` / ``settings.hive_pass_hash`` (bcrypt). Fail-closed.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only guard HTTP requests. Websockets/lifespan pass straight through
        # (there are no unauthenticated ws routes in the privileged groups today;
        # guarding lifespan would break startup).
        if scope["type"] != "http" or not _hive_enforcement_active():
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        # Normalize a trailing slash so "/health/" == "/health" for the allow-list.
        # (Starlette's router would 404 "/health/" anyway, so this is consistency, not
        # a bypass fix — a non-allow-listed path can never be widened by rstrip.)
        path = scope.get("path", "").rstrip("/") or "/"

        # OPTIONS (CORS preflight, carries no credential by design) and the public
        # allow-list bypass auth so CORS + health monitoring keep working.
        if method == "OPTIONS" or path in _PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        if self._is_authorized(scope):
            await self.app(scope, receive, send)
            return

        await self._send_401(send)

    def _is_authorized(self, scope: Scope) -> bool:
        """Fail-closed credential check. Returns True ONLY on a verified match."""
        # Unset credential in hive mode → deny everything (never fail open to no-auth).
        expected_user = settings.hive_user
        expected_hash = settings.hive_pass_hash
        if not expected_hash:
            logger.error(
                "HiveAuthMiddleware: SWARMAI_MODE=hive but hive_pass_hash is empty — "
                "denying all non-public requests (fail-closed). Set HIVE_PASS_HASH."
            )
            return False
        cred = _extract_basic_credential(scope)
        if cred is None:
            return False
        user, password = cred
        try:
            # Compare user first (cheap), then bcrypt-verify the password. Any
            # exception in verify_password (malformed hash, encoding) → deny.
            # NOTE: the username compare is non-constant-time, which is acceptable —
            # in HTTP Basic auth the username ("admin") is NOT a secret; only the
            # password is, and bcrypt.checkpw (verify_password) is constant-time.
            if user != expected_user:
                return False
            return verify_password(password, expected_hash)
        except Exception:  # noqa: BLE001 — fail-closed on ANY verification error
            logger.exception("HiveAuthMiddleware: credential verification raised — denying")
            return False

    async def _send_401(self, send: Send) -> None:
        """Emit a 401 ASGI response with a WWW-Authenticate challenge, without
        touching the inner app (so streaming routes are never wrapped)."""
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b'Basic realm="SwarmAI Hive"'),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"detail":"Unauthorized"}',
            }
        )
