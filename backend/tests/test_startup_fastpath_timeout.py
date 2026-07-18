"""A2-timeout (startup hazard): the fast-path DB init must be wall-clock bounded.

The full-init path wraps `initialize_database()` in `asyncio.wait_for(..., 45s)`
(main.py:795), but the production FAST path called `initialize_database(skip_schema=True)`
with NO timeout (main.py:758). That call still acquires an exclusive migration
flock; a stale lock holder (a wedged prior process / NFS lock) makes it hang
FOREVER at boot with no health signal — worse than the full-init path it mirrors.

Fix: route BOTH paths through a bounded helper `_init_db_bounded(skip_schema)`
that applies the same 45s wall-clock timeout and raises on breach (so launchd's
KeepAlive at least surfaces a bounded restart instead of an infinite hang).
"""
import asyncio

import pytest


@pytest.mark.asyncio
async def test_fastpath_db_init_is_bounded(monkeypatch):
    """A hung initialize_database is bounded by a timeout, not infinite.

    We patch `initialize_database` to hang forever and assert the bounded
    helper raises within a small test-scaled timeout instead of never returning.
    """
    import main

    async def _hang(*_args, **_kwargs):
        await asyncio.sleep(3600)  # simulate a wedged migration flock

    monkeypatch.setattr(main, "initialize_database", _hang)

    # NON-VACUOUS assertion: the helper must raise its OWN RuntimeError (from its
    # internal wait_for breach), NOT merely be killed by the outer test guard.
    #   - Bounded helper (timeout=0.2)  → raises RuntimeError in ~0.2s → passes.
    #   - Unbounded helper (mutation)   → hangs → the outer wait_for(2.0) fires an
    #     asyncio.TimeoutError, which is NOT RuntimeError → pytest.raises(RuntimeError)
    #     does NOT catch it → the test FAILS. This is what makes the mutation RED.
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(
            main._init_db_bounded(skip_schema=True, timeout=0.2),
            timeout=2.0,  # generous vs the helper's 0.2s; only fires if the helper is unbounded
        )


@pytest.mark.asyncio
async def test_fastpath_db_init_passes_through_on_success(monkeypatch):
    """Negative-control: when init completes quickly, the bounded helper returns
    normally and forwards skip_schema (no false timeout, no arg drop)."""
    import main

    seen = {}

    async def _ok(skip_schema: bool = False):
        seen["skip_schema"] = skip_schema

    monkeypatch.setattr(main, "initialize_database", _ok)

    await main._init_db_bounded(skip_schema=True, timeout=5.0)
    assert seen == {"skip_schema": True}, "skip_schema must be forwarded to initialize_database"
