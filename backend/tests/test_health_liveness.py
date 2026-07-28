"""Tests for /health liveness/readiness separation (offline-root-fix run_7e8a2030).

The liveness path (health_check) must do ZERO awaited DB/auth I/O — it reads the
readiness cache the background sampler maintains. This is what makes /health
immune to the executor-starvation that caused the false "Backend offline".
"""
from __future__ import annotations

import time

import pytest

from core.readiness_sampler import ReadinessCache, readiness_cache


def test_readiness_cache_unknown_before_first_sample():
    """Fresh cache → db_healthy None (unknown), never a fabricated True/False."""
    c = ReadinessCache()
    snap = c.snapshot()
    assert snap["db_healthy"] is None
    assert snap["auth"] == "unknown"
    assert snap["stale"] is True


def test_readiness_cache_fresh_snapshot():
    c = ReadinessCache()
    c.update(db_healthy=True, auth="valid")
    snap = c.snapshot()
    assert snap["db_healthy"] is True
    assert snap["auth"] == "valid"
    assert snap["stale"] is False
    assert snap["age_s"] < 1.0


def test_readiness_cache_stale_reports_unknown(monkeypatch):
    """A snapshot older than STALE_AFTER_S → 'unknown' (sampler wedged), NOT a
    frozen last-known-good value (Gate-1 R4)."""
    import core.readiness_sampler as rs
    c = ReadinessCache()
    c.update(db_healthy=True, auth="valid")
    # Force the sample timestamp far into the past.
    c._sampled_at = time.time() - (rs.STALE_AFTER_S + 5)
    snap = c.snapshot()
    assert snap["db_healthy"] is None
    assert snap["auth"] == "unknown"
    assert snap["stale"] is True


@pytest.mark.asyncio
async def test_health_endpoint_does_no_awaited_db_io(async_client, monkeypatch):
    """AC1/AC4: /health must NOT call db.health_check() on the request path.

    We make db.health_check RAISE if called; a correct liveness path (reading the
    cache) never touches it, so /health still returns 200 healthy. If the endpoint
    regressed to awaiting the DB, this test would surface the call.
    """
    import main
    from database import db

    # /health short-circuits to "initializing" until lifespan startup completes;
    # in-process tests don't run lifespan, so force the post-startup path.
    monkeypatch.setattr(main, "_startup_complete", True)

    called = {"n": 0}

    async def _boom():
        called["n"] += 1
        raise AssertionError("liveness path must not call db.health_check()")

    monkeypatch.setattr(db, "health_check", _boom)
    # Seed the readiness cache so the endpoint has a snapshot to read.
    readiness_cache.update(db_healthy=True, auth="valid")

    resp = await async_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert called["n"] == 0, "liveness path awaited the DB — regression"


@pytest.mark.asyncio
async def test_health_reports_degraded_when_readiness_db_down(async_client, monkeypatch):
    """AC2: a real DB-down (sampled) surfaces as degraded to the banner — the
    readiness signal still reaches the frontend, it just doesn't gate liveness."""
    import main
    monkeypatch.setattr(main, "_startup_complete", True)
    readiness_cache.update(db_healthy=False, auth="valid")
    resp = await async_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["db_healthy"] is False
    assert body["status"] == "degraded"


@pytest.mark.asyncio
async def test_sample_once_never_raises(monkeypatch):
    """The sampler's single-iteration must swallow ALL failures (loop must never die)."""
    import core.readiness_sampler as rs
    from database import db

    async def _boom():
        raise RuntimeError("db exploded")

    monkeypatch.setattr(db, "health_check", _boom)
    # Should NOT raise despite the DB call blowing up.
    await rs._sample_once()
    snap = rs.readiness_cache.snapshot()
    # DB failure → db_healthy False (safe value), not an exception.
    assert snap["db_healthy"] is False
