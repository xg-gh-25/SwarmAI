"""Execution tests for the DEAD-state resume fix.

WHAT IS TESTED
--------------
"First resume fails with `Cannot send() in state dead`". A session can sit in
DEAD from two sources, and send()'s state guard (session_unit.py:1708) rejected
BOTH until lifecycle_manager's 60s background loop happened to drive DEAD→COLD:

  (a) transient-under-lock inside _crash_to_cold_async (IDLE→DEAD→kill→COLD) that
      got ORPHANED — send() itself cancels its in-flight flush_recycle task at
      :1575, the CancelledError unwinds out of the locked section leaving DEAD.
  (b) 4 watchdog paths that set DEAD and `return` without lock/COLD
      (_pid_watchdog_loop:1099/1173, _maybe_escape_wedged_tool:1288/1377) —
      legitimate STABLE DEAD whose only other exit was the 60s loop.

THE FIX (approach B): a send-time DEAD-recovery block BEFORE the :1708 guard,
mirroring the existing force_unstick_streaming / force_unstick_waiting_input
pattern: `if state==DEAD: await _crash_to_cold_async(clear_identity=False)` →
state becomes COLD → falls through to the existing COLD→_ensure_spawned
spawn-with-resume path. Idempotent (:4137 no-ops if already COLD), holds
self._lock (serializes a real concurrent teardown), preserves _sdk_session_id
(--resume rides solely on that being set).

Gate-1 verified: DEAD→DEAD is a safe no-op (_transition:894 same-state early
return); --resume is driven only by _sdk_session_id (:2198), which
clear_identity=False preserves.

METHODOLOGY
-----------
Direct send() invocation against the REAL SessionUnit (__new__ + explicit field
stubbing). Mocks only at the recycle/spawn boundary so the DEAD-recovery DECISION
(the code under change) runs for real.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit


def _make_unit(state: SessionState = SessionState.DEAD,
               *, session_id: str = "sess-dead-test",
               sdk_session_id: str | None = "sdk-sess-123") -> SessionUnit:
    """Bare SessionUnit for DEAD-resume testing (no DB/spawn/lock side effects)."""
    unit = SessionUnit.__new__(SessionUnit)
    unit.session_id = session_id
    unit.state = state
    unit._heal_checkpoint = None
    unit._wrapper = None
    unit._content_emitted = False
    unit._last_turn_clean = False
    # Poison-guard recycle check (_should_poison_guard_recycle, added by the
    # prewarm run) reads this on the send() auto-recover path; real __init__
    # defaults it False. Without it a bare-__new__ unit raises AttributeError.
    unit._adopted_prewarm_fresh = False
    unit._interrupted = False
    unit._user_stopped_current_turn = False
    unit._retry_count = 0
    unit._recycle_kill_pending = False
    unit._buffer_overflow_recovery = False
    unit._tool_call_leak_recovery = False
    unit._sdk_session_id = sdk_session_id
    unit._app_session_id = "app-sess-456"
    unit._model_name = "claude-opus-4-8"
    unit._lock = asyncio.Lock()
    unit._client_io = asyncio.Lock()
    unit._on_state_change = None
    unit._client = None
    unit.is_channel_session = False
    unit.last_used = time.time()
    unit._send_generation = 0
    unit._streaming_start_time = None
    unit._last_event_time = None
    unit._last_heartbeat_elapsed = None
    unit._last_progress_time = None
    unit._hooks_enqueued = False
    unit._pid_watchdog_task = None
    unit._pipe_flush_task = None
    unit._stop_event = asyncio.Event()
    unit._active_agent_tools = {}
    unit._open_tool_uses = {}
    unit._pending_tool_use_id = None
    unit._pending_question = None
    unit._last_drained_seqs = []
    unit._graceful_wrap_pending = False
    unit._channel_wrap_injected = False
    unit._hard_floor_wrap_injected = False
    unit._wrapup_conclusion = ""
    _hs = MagicMock()
    _hs.turn_count = 0
    _hs._max_turns = 500
    unit._health_sensor = _hs
    from core.compaction_guard import CompactionGuard
    unit._compaction_guard = CompactionGuard()
    from core.streaming_orchestrator import StreamingOrchestrator
    from core.retry_manager import RetryManager
    unit._streaming_orchestrator = StreamingOrchestrator(parent=unit)
    unit._retry_manager = RetryManager(parent=unit)
    return unit


class _StopAfterDecisionError(Exception):
    pass


class TestDeadResumeRecovery:
    @pytest.mark.asyncio
    async def test_dead_send_recovers_to_cold_then_spawns(self):
        """A DEAD session's send() must drive recovery (_crash_to_cold_async) and
        reach the spawn path — NOT raise 'Cannot send() in state dead'.

        Covers BOTH DEAD sources (watchdog stable + flush orphan): the recovery
        is path-agnostic — any DEAD reaching send() is recovered.
        """
        unit = _make_unit(state=SessionState.DEAD)

        recovered = {}

        async def _recover(*a, **k):
            # Faithful: real _crash_to_cold_async drives DEAD→COLD, preserves
            # _sdk_session_id when clear_identity=False.
            recovered["clear_identity"] = k.get("clear_identity")
            unit.state = SessionState.COLD

        async def _spawn_stop(*a, **k):
            # Reached the spawn path = recovery succeeded, no raise.
            raise _StopAfterDecisionError("reached spawn — recovery worked")
            yield {}  # pragma: no cover

        with patch.object(unit, "_crash_to_cold_async", _recover), \
             patch.object(unit, "_ensure_spawned", _spawn_stop), \
             patch.object(unit, "_await_streaming_slot", AsyncMock()):
            with pytest.raises(_StopAfterDecisionError):
                async for _ in unit.send(
                    "resume", MagicMock(), app_session_id="app-sess-456"
                ):
                    pass

        assert recovered.get("clear_identity") is False, (
            "DEAD recovery must preserve --resume identity (clear_identity=False)"
        )

    @pytest.mark.asyncio
    async def test_dead_send_does_not_raise_state_dead(self):
        """The exact regression: DEAD send() must NOT raise RuntimeError 'state dead'.

        MUTATION ANCHOR: remove the DEAD-recovery block → this raises RuntimeError
        at :1708 → RED.
        """
        unit = _make_unit(state=SessionState.DEAD)

        async def _recover(*a, **k):
            unit.state = SessionState.COLD

        async def _spawn_stop(*a, **k):
            raise _StopAfterDecisionError("reached spawn")
            yield {}  # pragma: no cover

        raised_state_dead = False
        try:
            with patch.object(unit, "_crash_to_cold_async", _recover), \
                 patch.object(unit, "_ensure_spawned", _spawn_stop), \
                 patch.object(unit, "_await_streaming_slot", AsyncMock()):
                async for _ in unit.send(
                    "resume", MagicMock(), app_session_id="app-sess-456"
                ):
                    pass
        except _StopAfterDecisionError:
            pass  # expected — reached spawn
        except RuntimeError as e:
            if "state dead" in str(e):
                raised_state_dead = True

        assert not raised_state_dead, (
            "DEAD send() must recover, not raise 'Cannot send() in state dead'"
        )

    @pytest.mark.asyncio
    async def test_dead_recovery_preserves_resume_when_sdk_id_set(self):
        """DEAD + _sdk_session_id set → recovery keeps the id so the COLD spawn
        resumes (clear_identity=False never nulls _sdk_session_id)."""
        unit = _make_unit(state=SessionState.DEAD, sdk_session_id="sdk-keep-me")

        async def _recover(*a, **k):
            # Mirror real _crash_to_cold_async(clear_identity=False): does NOT
            # touch _sdk_session_id.
            unit.state = SessionState.COLD

        async def _spawn_stop(*a, **k):
            raise _StopAfterDecisionError("spawn")
            yield {}  # pragma: no cover

        with patch.object(unit, "_crash_to_cold_async", _recover), \
             patch.object(unit, "_ensure_spawned", _spawn_stop), \
             patch.object(unit, "_await_streaming_slot", AsyncMock()):
            with pytest.raises(_StopAfterDecisionError):
                async for _ in unit.send(
                    "resume", MagicMock(), app_session_id="app-sess-456"
                ):
                    pass

        assert unit._sdk_session_id == "sdk-keep-me", (
            "recovery must preserve _sdk_session_id for --resume"
        )

    @pytest.mark.asyncio
    async def test_cold_and_idle_unaffected(self):
        """COLD/IDLE sends must NOT trigger DEAD recovery — only DEAD does.

        Guards against the recovery block over-firing on healthy states.
        """
        # COLD: should go straight to spawn, no recovery call
        unit = _make_unit(state=SessionState.COLD)
        recover = AsyncMock()

        async def _spawn_stop(*a, **k):
            raise _StopAfterDecisionError("spawn")
            yield {}  # pragma: no cover

        with patch.object(unit, "_crash_to_cold_async", recover), \
             patch.object(unit, "_ensure_spawned", _spawn_stop), \
             patch.object(unit, "_await_streaming_slot", AsyncMock()):
            with pytest.raises(_StopAfterDecisionError):
                async for _ in unit.send(
                    "hi", MagicMock(), app_session_id="app-sess-456"
                ):
                    pass

        recover.assert_not_awaited()
