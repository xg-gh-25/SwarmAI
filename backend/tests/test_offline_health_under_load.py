"""DoD test for the backend-offline root-cause fix (run_b36c7880).

The offline symptom is: under a burst of slow blocking work, the readiness
sampler (and request endpoints) cannot get a worker on the SINGLE default
ThreadPoolExecutor, so the sampler tick stalls → readiness goes stale →
the frontend declares the alive daemon offline.

Empirically verified mechanism (not the event loop — that stays responsive):
a NEW `asyncio.to_thread(...)` TIMES OUT when all default-pool workers are busy.
So the DoD is: liveness-critical blocking work (the readiness sampler's STS
check, and any dedicated-pool work) must complete promptly EVEN WHEN the default
pool is fully saturated by bulk work.

These tests reproduce saturation and assert the insulated paths stay responsive.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time

import pytest


def _default_pool_workers() -> int:
    return min(32, (os.cpu_count() or 1) + 4)


@pytest.mark.asyncio
async def test_default_pool_saturation_starves_a_new_to_thread():
    """CHARACTERIZATION (the bug): with the default pool saturated, a new
    asyncio.to_thread cannot get a worker. This documents WHY liveness work must
    not live on the default pool. (If this ever fails, the default pool grew
    unbounded and the premise changed.)"""
    workers = _default_pool_workers()
    ev = threading.Event()
    blockers = [asyncio.create_task(asyncio.to_thread(ev.wait)) for _ in range(workers)]
    await asyncio.sleep(0.2)
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.to_thread(lambda: "x"), timeout=1.0)
    finally:
        ev.set()
        await asyncio.gather(*blockers)


@pytest.mark.asyncio
async def test_dedicated_pool_work_survives_default_pool_saturation():
    """DoD CORE: work routed to a dedicated pool via executors.run_in completes
    promptly even when the DEFAULT pool is fully saturated by bulk blocking work.
    This is what keeps the readiness sampler (once its STS leg is on a dedicated
    pool) alive under load → no false offline."""
    from core import executors

    workers = _default_pool_workers()
    ev = threading.Event()
    # Saturate the DEFAULT pool.
    blockers = [asyncio.create_task(asyncio.to_thread(ev.wait)) for _ in range(workers)]
    await asyncio.sleep(0.2)
    try:
        t0 = time.perf_counter()
        result = await asyncio.wait_for(
            executors.run_in("subprocess", lambda: "sts-ok"), timeout=2.0
        )
        elapsed = time.perf_counter() - t0
        assert result == "sts-ok"
        assert elapsed < 1.0, (
            f"dedicated-pool work took {elapsed:.2f}s under default-pool saturation "
            "— liveness path not insulated"
        )
    finally:
        ev.set()
        await asyncio.gather(*blockers)


@pytest.mark.asyncio
async def test_readiness_sampler_sts_leg_uses_dedicated_pool():
    """DoD: the readiness sampler's credential/STS check must NOT run on the
    default pool (the one thing on its path that still did). We assert the
    credential validator's STS call is dispatched to a dedicated executor, so a
    saturated default pool cannot stall the sampler."""
    from core import session_registry
    validator = session_registry.get_credential_validator()

    # The STS call must be routed off the default pool. We detect this by
    # checking the thread name the sync STS helper runs on.
    seen = {}
    # check() dispatches _call_sts_typed (is_valid() uses _call_sts) — spy the one
    # check() actually calls, or the spy never fires and we test nothing.
    orig = validator._call_sts_typed

    def _spy(region):
        seen["thread"] = threading.current_thread().name
        # Don't actually hit AWS — raise so check() collapses to "unknown".
        raise RuntimeError("stub — no network in test")

    validator._call_sts_typed = _spy
    try:
        # Saturate default pool so, if check() used it, this would stall/timeout.
        workers = _default_pool_workers()
        ev = threading.Event()
        blockers = [asyncio.create_task(asyncio.to_thread(ev.wait)) for _ in range(workers)]
        await asyncio.sleep(0.2)
        try:
            validator._check_cache_status = None  # force a real check
            validator._check_cache_time = 0
            await asyncio.wait_for(validator.check("us-east-1"), timeout=2.0)
        except asyncio.TimeoutError:
            pytest.fail("credential.check() stalled under default-pool saturation "
                        "— STS leg still on the default pool (sampler would go stale)")
        except Exception:
            pass  # the stub RuntimeError is fine — we only care WHICH thread ran it
        finally:
            ev.set()
            await asyncio.gather(*blockers)
        # The STS leg was moved from the shared 'subprocess' writer pool to the
        # dedicated latency-sensitive 'spawn' READER pool (run_e76b3ea5): STS is both
        # the cold-spawn preflight AND the readiness sampler's auth leg, so it must not
        # queue behind slow git writers. Either dedicated pool satisfies this test's
        # real contract (STS is OFF the default pool); the current architecture routes
        # it to 'spawn' (thread prefix swarm-spawn), so assert that.
        assert seen.get("thread", "").lower().startswith("swarm-spawn"), (
            f"STS ran on {seen.get('thread')!r}, not the dedicated 'spawn' pool"
        )
    finally:
        validator._call_sts_typed = orig
