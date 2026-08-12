"""Tests for the central dedicated-executor registry (core/executors.py).

Root-cause fix for backend-offline (run_b36c7880): ~50 asyncio.to_thread sites
all shared the SINGLE default ThreadPoolExecutor (16 workers), so a burst of slow
blocking work (STS / LLM / git-subprocess / briefing glob) saturated it and the
event loop could not even schedule the zero-IO /health cache-read handler, nor the
readiness sampler tick (→ stale readiness → false offline).

The registry gives slow/blocking work CLASS-SCOPED bounded pools that are DISTINCT
from the default pool, so the default pool (and thus /health scheduling) is never
starved by them.

Methodology: TDD RED→GREEN.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest


def test_registry_exposes_named_bounded_pools():
    from core import executors
    # Each class of slow work has its own pool.
    for name in ("io", "subprocess", "llm", "briefing"):
        pool = executors.get_pool(name)
        assert pool is not None, f"missing dedicated pool: {name}"
        # bounded (not unbounded / not the default)
        assert pool._max_workers >= 1


def test_pools_are_distinct_from_each_other_and_default():
    from core import executors
    io = executors.get_pool("io")
    llm = executors.get_pool("llm")
    sub = executors.get_pool("subprocess")
    assert io is not llm and io is not sub and llm is not sub, "pools must be distinct instances"
    # And distinct from asyncio's default executor (which is None until first to_thread).
    # Use a throwaway loop, NOT get_event_loop(): the latter raises "no current event
    # loop" on Py3.12 once a prior test cleared the thread-default (set_event_loop(None)).
    _probe_loop = asyncio.new_event_loop()
    try:
        loop_default = getattr(_probe_loop, "_default_executor", None)
    finally:
        _probe_loop.close()
    assert io is not loop_default


def test_unknown_pool_name_raises():
    from core import executors
    with pytest.raises((KeyError, ValueError)):
        executors.get_pool("does-not-exist")


def test_thread_name_prefix_is_set_for_observability():
    from core import executors
    pool = executors.get_pool("llm")
    # thread_name_prefix lets us see WHICH pool a stuck thread belongs to in a dump.
    assert "llm" in (pool._thread_name_prefix or "").lower()


@pytest.mark.asyncio
async def test_run_in_offloads_to_the_named_pool_not_default():
    """run_in(pool_name, fn) must execute fn on the named pool's threads."""
    from core import executors
    seen = {}

    def _work():
        seen["thread"] = threading.current_thread().name
        return 42

    result = await executors.run_in("io", _work)
    assert result == 42
    assert "io" in seen["thread"].lower(), (
        f"work ran on {seen['thread']!r}, not the 'io' pool — routing broken"
    )


@pytest.mark.asyncio
async def test_saturating_one_pool_does_not_block_another():
    """The whole point: a saturated slow pool must NOT delay work on a different
    pool (this is what protects /health scheduling from a briefing/LLM burst)."""
    from core import executors

    llm = executors.get_pool("llm")
    # Fill the llm pool with blockers so it is fully saturated.
    release = threading.Event()
    n = llm._max_workers
    futs = [llm.submit(release.wait) for _ in range(n)]
    try:
        # A task on a DIFFERENT pool must still complete promptly.
        t0 = time.perf_counter()
        result = await asyncio.wait_for(executors.run_in("io", lambda: "ok"), timeout=2.0)
        elapsed = time.perf_counter() - t0
        assert result == "ok"
        assert elapsed < 1.0, (
            f"io-pool work took {elapsed:.2f}s while llm pool saturated — pools not isolated"
        )
    finally:
        release.set()
        for f in futs:
            f.result(timeout=5)
