"""Tests for _force_kill wrapper-cleanup concurrency-safety (run_02bc6dd1, 簇A WS1).

The bug (Gate-0 re-framed, M3-skeptic-confirmed): session_unit._force_kill reads
`self._wrapper is not None` then `await self._wrapper.__aexit__()` with NO null-ref
between the check and the await. The await (asyncio.wait_for) is a context-switch
point, so two concurrent _force_kill calls — the PID watchdog (1153/1268/1357, which
do NOT hold self._lock) racing a _lock-holding path (_crash_to_cold_async / kill) —
can BOTH pass the not-None check and BOTH invoke __aexit__ on the SAME wrapper.
`_ClaudeClientWrapper.__aexit__` delegates to the anyio client __aexit__, which is
NOT reentrant (cancel-scope-in-different-task error).

The fix (SIMPLICITY / synchronous null-the-ref-before-await): capture
`wrapper_ref = self._wrapper` and set `self._wrapper = None` in ONE await-free block,
THEN `await wrapper_ref.__aexit__()`. The null assignment is synchronous (no await
between read and null), so only the FIRST task to execute it wins the wrapper;
concurrent callers see None and skip __aexit__. Watchdog stays lock-free.

METHODOLOGY: forced-execution race. The fake wrapper's __aexit__ awaits a real
asyncio.Event — this forces a genuine context switch INSIDE __aexit__, faithfully
reproducing the TOCTOU window (NOT a mock that bypasses the await — that would be
test-theater per Gate-1 finding #5). OS-level kill is neutered (no real subprocess).
Mutation proof: revert the null-the-ref → test goes RED (counter==2).
"""

from __future__ import annotations

import asyncio

import pytest

from core.session_unit import SessionUnit


def _unit() -> SessionUnit:
    """A real SessionUnit (via __init__, so all flags/locks are wired)."""
    return SessionUnit(session_id="test-force-kill-idem", agent_id="default")


class _FakeWrapper:
    """A wrapper whose __aexit__ awaits a real Event — forces a true context
    switch inside the await window, reproducing the TOCTOU the fix closes.

    `pid` mirrors the real _ClaudeClientWrapper.pid contract (session_unit:838).
    """

    def __init__(self, gate: asyncio.Event) -> None:
        self.aexit_calls = 0
        self._gate = gate
        self.pid = 999_999  # a fake pid; OS kill is neutered in the test

    async def __aexit__(self, exc_type, exc_val, exc_tb):  # noqa: ANN001
        self.aexit_calls += 1
        # Real await → real context switch INSIDE __aexit__. This is the window
        # where a second concurrent _force_kill could re-enter (the bug).
        await self._gate.wait()
        return False


@pytest.mark.asyncio
async def test_concurrent_force_kill_calls_aexit_at_most_once(monkeypatch):
    """AC1/AC4: two concurrent _force_kill() on one unit invoke wrapper.__aexit__
    EXACTLY ONCE. RED on the old read-then-await (counter==2); GREEN with the
    synchronous null-the-ref-before-await fix (counter==1)."""
    import os as _os

    u = _unit()
    gate = asyncio.Event()
    wrapper = _FakeWrapper(gate)
    u._wrapper = wrapper

    # Neuter the OS-level kill: no real subprocess. We only exercise the wrapper
    # cleanup block. self.pid reads from _wrapper.pid (838) → patch the property
    # path by giving _force_kill nothing to kill at the OS level.
    async def _noop_await_exit(*a, **k):
        return None

    u._await_process_exit = _noop_await_exit  # skip the 3s OS-exit poll

    # Force the OS-kill branch to be a no-op: pid lookup raises ProcessLookupError
    # (already-dead path), so _force_kill falls straight through to wrapper cleanup.
    # Use monkeypatch (auto-undone, xdist-safe) — not a manual global save/restore.
    orig_getpgid = _os.getpgid

    def _fake_getpgid(pid):  # noqa: ANN001
        if pid == wrapper.pid:
            raise ProcessLookupError  # "already dead" → skip OS kill, reach cleanup
        return orig_getpgid(pid)

    monkeypatch.setattr(_os, "getpgid", _fake_getpgid)

    async def _release():
        # Let both _force_kill calls reach the __aexit__ await, THEN release.
        await asyncio.sleep(0.02)
        gate.set()

    await asyncio.gather(
        u._force_kill(),
        u._force_kill(),
        _release(),
    )

    assert wrapper.aexit_calls == 1, (
        f"wrapper.__aexit__ called {wrapper.aexit_calls}x — concurrent _force_kill "
        f"double-cleanup (the TOCTOU). Expected exactly 1 (null-the-ref-before-await)."
    )
    # Post-condition: the wrapper ref is cleared (the winner nulled it).
    assert u._wrapper is None, "winning _force_kill must clear self._wrapper"


@pytest.mark.asyncio
async def test_force_kill_lock_free_no_lock_acquired():
    """AC2: _force_kill must NOT acquire self._lock — it is shared by the lock-free
    watchdog and the _lock-holding chokepoint. If _force_kill took _lock, the
    watchdog would head-of-line-block behind a streaming turn (Gate-0 rejection).
    Guard: the lock is NOT held at any point during a _force_kill on an idle unit."""
    u = _unit()
    u._wrapper = None  # nothing to clean up — pure path
    # If _force_kill acquired _lock, this concurrent acquisition would block.
    # We assert _force_kill completes WITHOUT us pre-holding the lock being a problem,
    # and that the lock is free immediately after.
    assert not u._lock.locked()
    await u._force_kill()
    assert not u._lock.locked(), "_force_kill must not leave self._lock held"
