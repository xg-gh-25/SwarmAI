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
* **Public allow-list is tiny + explicit.** ``/health`` (monitors, matches the
  Caddyfile's /health exemption) and ``OPTIONS`` preflight (so browser CORS is not
  broken — the CORS middleware must still see the preflight).
* **Reuses ``core.auth.verify_password``** (bcrypt) — no new hash comparison; the same
  primitive the JWT auth path uses. ``HIVE_PASS_HASH`` is a bcrypt hash (as produced by
  ``caddy hash-password``), so ``bcrypt.checkpw`` validates it directly.
* **bcrypt runs once per credential, off the event loop.** The hash has cost 14 (about
  1 s per check), and the cost stays: the agent on the Hive can read the hash, so the cost
  is what protects the password. Run on the event loop for every request, that check
  stalled the whole backend (a 3.2 s stall was logged during a dispatch). Now the first
  check of a credential runs on the dedicated ``hive_auth`` pool (``core.executors``),
  never on the loop or the default executor, and identical concurrent first checks share
  one run. Only a successful check is cached (``_VerifiedCache``): at most
  ``_CACHE_MAX_ENTRIES`` entries, each accepted for ``_CACHE_TTL_S`` after its
  verification. The key is an HMAC with a per-process random key over the expected hash,
  the user and the password, so the cache never holds a fast hash of the password, and a
  changed ``HIVE_PASS_HASH`` clears the cache and can never match an old entry. A wrong
  password is never cached and runs bcrypt every time, as before. (Caddy, the outer
  layer, also caches its successful checks.)
* **Bounded admission to bcrypt.** At most ``_MAX_PENDING_CHECKS`` distinct credentials
  are checked or queued at once; past that a new uncached credential is refused at once
  with 503 + ``Retry-After`` and no bcrypt (still a denial — fail-closed — but "busy", not
  401, so a browser does not drop the owner's correct stored password). A flood of
  distinct wrong passwords (from a caller that reached the app without passing Caddy)
  therefore holds bounded memory and never builds a queue that a real check waits behind
  for minutes. During such a flood a credential already cached keeps working; a new or
  expired one gets 503 until the flood ends.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import logging
import os
import secrets
import time
from collections import OrderedDict
from typing import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from config import settings
from core import executors
from core.auth import verify_password

logger = logging.getLogger(__name__)

# Public allow-list: paths reachable WITHOUT a credential even in hive mode.
# Keep this tiny and explicit — every entry is an unauthenticated attack surface.
# /health: liveness/readiness monitors (mirrors the Caddyfile's /health exemption).
_PUBLIC_PATHS: frozenset[str] = frozenset({"/health"})

# Success cache bounds. A Hive is single-tenant with one credential, so 32 entries is far
# above real use and only bounds memory. An entry is accepted for 10 minutes after its
# bcrypt check (not refreshed on use), which bounds how long the process keeps accepting a
# credential without re-checking it.
_CACHE_TTL_S = 600.0
_CACHE_MAX_ENTRIES = 32

# core.executors pool the bcrypt check runs on (never the event loop, never the default pool).
_BCRYPT_POOL = "hive_auth"

# Distinct credentials being checked or queued at once. With 2 pool threads at about 1 s a
# check, 8 is a few seconds of queue; a single-tenant Hive has one credential, so the cap
# is only reached by a flood. A request joining a check already in flight is not counted.
_MAX_PENDING_CHECKS = 8


class _VerifiedCache:
    """Successful credential checks only: key -> monotonic expiry, oldest first.

    Keys come from :meth:`key`, an HMAC under a random per-process secret, so the cache
    holds no value an attacker could test password guesses against offline. The expected
    hash is part of the key, and :meth:`bind` clears everything when it changes."""

    def __init__(
        self,
        ttl_s: float = _CACHE_TTL_S,
        max_entries: int = _CACHE_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._clock = clock
        self._secret = secrets.token_bytes(32)
        self._entries: OrderedDict[bytes, float] = OrderedDict()
        self._bound_hash: str | None = None

    def __len__(self) -> int:
        return len(self._entries)

    def bind(self, expected_hash: str) -> None:
        """Drop every entry when the expected hash differs from the one they were made for."""
        if expected_hash != self._bound_hash:
            self._entries.clear()
            self._bound_hash = expected_hash

    def key(self, expected_hash: str, user: str, password: str) -> bytes:
        mac = hmac.new(self._secret, digestmod=hashlib.sha256)
        for part in (expected_hash, user, password):
            raw = part.encode("utf-8")
            # Length-prefixed, so no two different triples produce the same message.
            mac.update(len(raw).to_bytes(8, "big"))
            mac.update(raw)
        return mac.digest()

    def hit(self, key: bytes) -> bool:
        expiry = self._entries.get(key)
        if expiry is None:
            return False
        if self._clock() >= expiry:
            del self._entries[key]
            return False
        return True

    def add(self, key: bytes) -> None:
        self._entries[key] = self._clock() + self._ttl_s
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


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


class _ChecksBusy(Exception):
    """``_MAX_PENDING_CHECKS`` credential checks are pending: refuse with 503, not 401."""


class HiveAuthMiddleware:
    """Pure-ASGI defense-in-depth auth layer for hive deployments.

    Off-hive: transparent pass-through. In hive: HTTP Basic auth on every request
    except the public allow-list + OPTIONS preflight, validated against
    ``settings.hive_user`` / ``settings.hive_pass_hash`` (bcrypt once per credential,
    on the ``hive_auth`` pool, then a bounded success cache). Fail-closed.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._cache = _VerifiedCache()
        # Cache key -> the bcrypt check in progress for it (single-flight).
        self._inflight: dict[bytes, asyncio.Task[bool]] = {}

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

        try:
            authorized = await self._is_authorized(scope)
        except _ChecksBusy:
            await self._send_503(send)
            return
        if authorized:
            await self.app(scope, receive, send)
            return

        await self._send_401(send)

    async def _is_authorized(self, scope: Scope) -> bool:
        """Fail-closed credential check. Returns True ONLY on a verified match.

        Raises ``_ChecksBusy`` (also a denial; the caller answers 503) when the
        pending-check cap is full and the credential is neither cached nor in flight."""
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
            # Compare user first (cheap), then verify the password (cache, else bcrypt).
            # Any exception (malformed hash, encoding, the pool) → deny.
            # NOTE: the username compare is non-constant-time, which is acceptable —
            # in HTTP Basic auth the username ("admin") is NOT a secret; only the
            # password is, and bcrypt.checkpw (verify_password) is constant-time.
            # For the same reason it is acceptable that only the right username can
            # reach the pending-check cap (503); the bcrypt time already showed it.
            if user != expected_user:
                return False
            return await self._verify(expected_hash, user, password)
        except _ChecksBusy:
            raise  # a denial too, answered with 503 by __call__
        except Exception:  # noqa: BLE001 — fail-closed on ANY verification error
            logger.exception("HiveAuthMiddleware: credential verification raised — denying")
            return False

    async def _verify(self, expected_hash: str, user: str, password: str) -> bool:
        """True from the success cache, else from one bcrypt check shared by every
        concurrent request with the same credential."""
        self._cache.bind(expected_hash)
        key = self._cache.key(expected_hash, user, password)
        if self._cache.hit(key):
            return True
        loop = asyncio.get_running_loop()
        task = self._inflight.get(key)
        if task is None or task.get_loop() is not loop:
            # A task of another (closed) loop can never be awaited here: start afresh.
            if len(self._inflight) >= _MAX_PENDING_CHECKS:
                self._drop_foreign_tasks(loop)
            if len(self._inflight) >= _MAX_PENDING_CHECKS:
                logger.warning(
                    "HiveAuthMiddleware: %d credential checks already pending — "
                    "refusing a new one with 503, without bcrypt", len(self._inflight)
                )
                raise _ChecksBusy()
            task = loop.create_task(self._check_and_cache(key, expected_hash, password))
            self._inflight[key] = task
            task.add_done_callback(lambda done, k=key: self._forget(k, done))
        # Shielded: a request that disconnects does not cancel the check others await.
        return await asyncio.shield(task)

    def _drop_foreign_tasks(self, loop: asyncio.AbstractEventLoop) -> None:
        """Forget checks of another (closed) loop: their done callback never runs, so
        left in place they would hold the cap full and refuse every new credential."""
        for key, task in list(self._inflight.items()):
            if task.get_loop() is not loop:
                del self._inflight[key]

    def _forget(self, key: bytes, done: asyncio.Task[bool]) -> None:
        if self._inflight.get(key) is done:
            del self._inflight[key]

    async def _check_and_cache(self, key: bytes, expected_hash: str, password: str) -> bool:
        """Run bcrypt on the dedicated pool; cache the key only on an exact True."""
        try:
            ok = await executors.run_in(_BCRYPT_POOL, verify_password, password, expected_hash)
        except Exception:  # noqa: BLE001 — fail-closed, and never cached
            logger.exception("HiveAuthMiddleware: credential verification raised — denying")
            return False
        if ok is True:
            self._cache.add(key)
            return True
        return False

    async def _send_503(self, send: Send) -> None:
        """Too many pending checks: deny without a WWW-Authenticate challenge, so a
        client keeps its stored credential and retries instead of re-prompting."""
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", b"5"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"detail":"Authentication busy, retry shortly"}',
            }
        )

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
