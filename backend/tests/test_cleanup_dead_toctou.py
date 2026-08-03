"""Regression test for the _cleanup_dead TOCTOU race (run_ace705df Gate-2 HIGH).

WHAT IS TESTED
--------------
LifecycleManager._cleanup_dead checks `unit.state == DEAD`, then `await`s
_build_hook_context (a yield point), then acts. The send() DEAD-recovery path
(added in run_ace705df) can, during that await, drive the SAME unit
DEAD→COLD→…→STREAMING lock-free. If _cleanup_dead resumed and blindly ran its
cleanup+transition, it would wipe a unit send() already recovered and is
actively streaming — orphaning the freshly-spawned subprocess and corrupting
_streaming_count.

THE FIX: re-check `state == DEAD` AFTER the await; if send() reclaimed the unit
(no longer DEAD), skip it. When still DEAD, route DEAD→COLD through the
idempotent, self._lock-holding _crash_to_cold_async (serializes the two
DEAD→COLD drivers).

METHODOLOGY: drive the real _cleanup_dead against a stub unit whose state flips
DEAD→STREAMING DURING the awaited _build_hook_context (simulating the concurrent
send() recovery), and assert _cleanup_dead does NOT touch the reclaimed unit.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.lifecycle_manager import LifecycleManager
from core.session_unit import SessionState


def _make_manager(units):
    router = MagicMock()
    router.list_units.return_value = units
    mgr = LifecycleManager(router=router)
    mgr._hook_executor = MagicMock()  # enable the hook-firing branch
    return mgr


def _make_dead_unit(session_id="sess-toctou"):
    u = MagicMock()
    u.session_id = session_id
    u.state = SessionState.DEAD
    u._hooks_enqueued = False
    u._crash_to_cold_async = AsyncMock()
    u._cleanup_internal = MagicMock()
    u._transition = MagicMock()
    return u


class TestCleanupDeadTOCTOU:
    @pytest.mark.asyncio
    async def test_reclaimed_unit_is_skipped(self):
        """If send() flips the unit out of DEAD during the hook-context await,
        _cleanup_dead must SKIP it — no crash_to_cold, no cleanup, no release."""
        unit = _make_dead_unit()

        released = []
        mgr = _make_manager([unit])
        mgr._release_session_state = lambda sid: released.append(sid)
        mgr.enqueue_hooks = MagicMock()

        async def _build_ctx_then_reclaim(u):
            # Simulate the concurrent send() DEAD-recovery reclaiming the unit
            # WHILE we are awaiting here (the TOCTOU window).
            u.state = SessionState.STREAMING
            return {"session_id": u.session_id}

        mgr._build_hook_context = _build_ctx_then_reclaim

        await mgr._cleanup_dead()

        # The unit was reclaimed by send() → _cleanup_dead must not touch it.
        unit._crash_to_cold_async.assert_not_awaited()
        unit._cleanup_internal.assert_not_called()
        assert released == [], (
            "_release_session_state must NOT run on a unit send() reclaimed "
            "(would wipe module state of a live session)"
        )

    @pytest.mark.asyncio
    async def test_still_dead_unit_is_recovered_via_lock_held_path(self):
        """A unit that stays DEAD across the await must be driven DEAD→COLD via
        the idempotent, lock-holding _crash_to_cold_async (not hand-rolled)."""
        unit = _make_dead_unit()

        released = []
        mgr = _make_manager([unit])
        mgr._release_session_state = lambda sid: released.append(sid)
        mgr.enqueue_hooks = MagicMock()

        async def _build_ctx_no_reclaim(u):
            return {"session_id": u.session_id}  # state stays DEAD

        mgr._build_hook_context = _build_ctx_no_reclaim

        await mgr._cleanup_dead()

        # Still DEAD → recover through the lock-held idempotent transaction.
        unit._crash_to_cold_async.assert_awaited_once()
        _, kwargs = unit._crash_to_cold_async.await_args
        assert kwargs.get("clear_identity") is False, (
            "lifecycle DEAD→COLD must preserve --resume identity"
        )
        assert released == [unit.session_id]
        # Must NOT hand-roll the old lock-free cleanup+transition.
        unit._cleanup_internal.assert_not_called()
        unit._transition.assert_not_called()
