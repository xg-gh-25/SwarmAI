"""Background readiness sampler — decouples dependency-health from liveness.

ROOT-CAUSE FIX (run_7e8a2030): the ``/health`` endpoint used to run the DB check
+ STS auth check ON its request critical path. Under executor-thread-pool
starvation (unpooled aiosqlite spawned 20+ threads) even a 2s-capped ``wait_for``
could not get SCHEDULED, so the whole ``/health`` round-trip exceeded the Rust
watchdog's 3s budget → watchdog emitted ``backend-terminated-restarting`` → the
frontend showed "Backend is unavailable" and disabled all chat inputs, even
though the daemon was alive the whole time.

The fix separates two DIFFERENT questions (k8s liveness vs readiness):
  - **Liveness** ("is the process alive + event loop responsive?") — answered by
    ``/health`` with ZERO awaited I/O, so it can never be dragged past 3s by a slow
    dependency. This is what the watchdog consumes.
  - **Readiness** ("is the DB reachable? are creds valid?") — sampled HERE, off the
    request path, on a timer. ``/health`` reads the cached snapshot (a plain dict),
    so the dependency signal still reaches the frontend banner WITHOUT gating
    liveness or adding latency.

Hardening (Gate-1 skeptic R4): each sample iteration is individually timeout-bounded
and wrapped so an exception can NEVER kill the loop; the snapshot carries
``sampled_at`` so a consumer can treat a stale cache (sampler wedged) as "unknown"
rather than trusting a frozen last-known-good value.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Sampling cadence + per-sample bound. The interval is well under the frontend's
# 30s poll so the banner sees fresh readiness; the per-sample timeouts match the
# old inline caps (DB 2s, auth 1s) but now run OFF the request path.
SAMPLE_INTERVAL_S: float = 10.0
DB_SAMPLE_TIMEOUT_S: float = 2.0
AUTH_SAMPLE_TIMEOUT_S: float = 1.0
# A snapshot older than this ⇒ the sampler is wedged ⇒ report "unknown", never a
# frozen last-known-good value. 3× interval tolerates one missed tick + jitter.
STALE_AFTER_S: float = SAMPLE_INTERVAL_S * 3


class ReadinessCache:
    """Holds the latest sampled dependency-health snapshot (read by /health)."""

    def __init__(self) -> None:
        # Before the first sample completes, db_healthy is None ("unknown") and
        # auth is "unknown" — liveness never blocks on this, it just reports it.
        self._db_healthy: bool | None = None
        self._auth: str = "unknown"
        self._sampled_at: float = 0.0

    def update(self, db_healthy: bool | None, auth: str) -> None:
        self._db_healthy = db_healthy
        self._auth = auth
        self._sampled_at = time.time()

    def snapshot(self) -> dict[str, Any]:
        """Return the current readiness view. Stale snapshot → 'unknown' (never
        a frozen value): if the sampler wedged, we must not assert health we
        can't currently confirm."""
        age = time.time() - self._sampled_at if self._sampled_at else None
        if age is None or age > STALE_AFTER_S:
            return {"db_healthy": None, "auth": "unknown", "stale": True, "age_s": age}
        return {
            "db_healthy": self._db_healthy,
            "auth": self._auth,
            "stale": False,
            "age_s": age,
        }


# Module-level singleton cache — /health reads it, the sampler writes it.
readiness_cache = ReadinessCache()


async def _sample_once() -> None:
    """One readiness sample: DB reachability + auth, each individually bounded.

    Every failure mode collapses to a safe value (db_healthy=False / auth=unknown)
    and is swallowed — this coroutine must never raise into the loop.
    """
    db_healthy: bool | None
    try:
        from database import db
        db_healthy = await asyncio.wait_for(db.health_check(), timeout=DB_SAMPLE_TIMEOUT_S)
    except Exception:
        db_healthy = False

    auth = "unknown"
    try:
        from core import session_registry
        from core.app_config_manager import AppConfigManager
        _cfg = AppConfigManager.instance()
        if not _cfg.get("use_bedrock", True) or _cfg.get("auth_method") == "bedrock_api_key":
            auth = "valid"
        else:
            region = _cfg.get("aws_region", "us-east-1")
            result = await asyncio.wait_for(
                session_registry.get_credential_validator().check(region),
                timeout=AUTH_SAMPLE_TIMEOUT_S,
            )
            auth = result if result in ("valid", "expired", "unknown") else "unknown"
    except Exception:
        auth = "unknown"

    readiness_cache.update(db_healthy, auth)


async def readiness_sampler_loop() -> None:
    """Lifespan task: sample readiness every SAMPLE_INTERVAL_S, forever.

    A single hung/failed sample can NEVER freeze future samples (each is
    wait_for-bounded inside _sample_once) nor kill the loop (broad except here).
    """
    logger.info("readiness sampler started (interval=%.0fs)", SAMPLE_INTERVAL_S)
    while True:
        try:
            await _sample_once()
        except Exception as exc:  # pragma: no cover - loop must never die
            logger.debug("readiness sample iteration failed (non-fatal): %s", exc)
        await asyncio.sleep(SAMPLE_INTERVAL_S)
