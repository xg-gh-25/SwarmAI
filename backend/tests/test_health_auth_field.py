"""GET /health exposes an `auth` field (valid|expired|unknown).

Tests call health_check() directly (not via TestClient) to avoid the full
lifespan startup.

⚠️ LIVENESS/READINESS SPLIT (run_7e8a2030): health_check() no longer runs the
STS auth check on the request path. It reads a snapshot the background
readiness sampler maintains off-path (`core.readiness_sampler.readiness_cache`).
So these tests SEED the readiness cache (not patch the validator) — that is the
source health_check() actually reads now. A stale/never-sampled cache → auth
snapshot is "unknown" (fail-open), liveness stays healthy.
"""
from __future__ import annotations

import pytest

from core.readiness_sampler import readiness_cache


def _seed_auth(status: str, db_healthy: bool | None = True):
    """Seed the readiness cache the way the background sampler would."""
    readiness_cache.update(db_healthy=db_healthy, auth=status)


@pytest.mark.asyncio
async def test_health_auth_valid():
    import main
    orig = main._startup_complete
    main._startup_complete = True
    try:
        _seed_auth("valid")
        data = await main.health_check()
    finally:
        main._startup_complete = orig
    assert data.get("auth") == "valid", data


@pytest.mark.asyncio
async def test_health_auth_expired():
    import main
    orig = main._startup_complete
    main._startup_complete = True
    try:
        _seed_auth("expired")
        data = await main.health_check()
    finally:
        main._startup_complete = orig
    assert data.get("auth") == "expired"


@pytest.mark.asyncio
async def test_health_auth_unknown_maps_unknown():
    """A sampler value of 'unknown' (timeout/error sampled off-path) → auth='unknown',
    health unaffected (liveness never gates on auth)."""
    import main
    orig = main._startup_complete
    main._startup_complete = True
    try:
        _seed_auth("unknown")
        data = await main.health_check()
    finally:
        main._startup_complete = orig
    assert data.get("auth") == "unknown", data
    assert data.get("status") in ("healthy", "degraded")


@pytest.mark.asyncio
async def test_health_auth_unexpected_value_maps_unknown():
    """An out-of-domain sampler value → coerced to 'unknown' (fail-open guard in
    health_check: only valid|expired|unknown pass through)."""
    import main
    orig = main._startup_complete
    main._startup_complete = True
    try:
        _seed_auth("garbage-not-a-status")
        data = await main.health_check()
    finally:
        main._startup_complete = orig
    assert data.get("auth") == "unknown"


@pytest.mark.asyncio
async def test_health_initializing_branch_auth_unknown():
    """Before startup completes → no STS, auth=unknown."""
    import main
    orig = main._startup_complete
    main._startup_complete = False
    try:
        data = await main.health_check()
    finally:
        main._startup_complete = orig
    assert data.get("status") == "initializing"
    assert data.get("auth") == "unknown"
