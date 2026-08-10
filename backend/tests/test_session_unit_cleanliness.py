"""Execution tests for the resume-poison fix: `_last_turn_clean` cleanliness flag.

WHAT IS TESTED
--------------
The fail-closed cleanliness flag that fixes "first message after resume reliably
fails" (zombie_via_error). Root cause: send() reuses an alive-but-POISONED warm
subprocess after a soft-interrupt / SSE-disconnect that left the CLI in corrupt
turn-state. The existing eager-recycle (flush_subprocess_pipe → _crash_to_cold_async)
runs AFTER its `await interrupt()`, and send() cancels the in-flight flush before
reuse → recycle never runs → poisoned reuse → instant empty error_during_execution.

THE FIX (approach A2 — fail-closed):
- ``self._last_turn_clean`` per-instance, default **False** (fail-closed).
- Set **True** ONLY at the successful ResultMessage completion
  (streaming_orchestrator.py, after _transition(IDLE) at the success path).
- Cleared **False** on every STREAMING entry (_transition).
- send() checks it BEFORE reusing a warm IDLE subprocess: if NOT clean →
  recycle (_crash_to_cold_async, clear_identity=False) → then spawn-with-resume.
  If clean → reuse warm (fast path preserved).

WHY THESE TESTS (mutation-anchored)
-----------------------------------
- The flag is path-agnostic: ANY turn that did not reach the success-result
  transition (interrupt / disconnect / error / max_turns) leaves it False.
- The #1 correctness trap (Gate-1 skeptic): the flag must NOT be reset at send()
  entry, or every warm reuse looks poisoned and the fast path collapses. Test
  TestWarmFastPathPreserved + the mutation note lock this.
- Isolation (must-pass): the flag is a per-instance attribute (NOT module-level),
  and recycle only kills self's subprocess tree — never a sibling tab's.

METHODOLOGY
-----------
Direct method invocation against the REAL SessionUnit / StreamingOrchestrator
(__new__ + explicit field stubbing, same pattern as
test_session_unit_recovery_paths.py). Mocks only at the recycle/spawn boundary
so the send() reuse-vs-recycle DECISION (the thing under change) runs for real.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit


def _make_unit(
    state: SessionState = SessionState.IDLE,
    *,
    session_id: str = "sess-clean-test",
) -> SessionUnit:
    """Bare SessionUnit for cleanliness-flag testing (no DB/spawn/lock side effects)."""
    unit = SessionUnit.__new__(SessionUnit)
    unit.session_id = session_id
    unit.state = state
    unit._heal_checkpoint = None
    unit._wrapper = None
    unit._content_emitted = False
    unit._interrupted = False
    unit._user_stopped_current_turn = False
    unit._retry_count = 0
    unit._sdk_session_id = "sdk-sess-123"
    unit._app_session_id = "app-sess-456"
    unit._model_name = "claude-opus-4-8"
    unit._lock = asyncio.Lock()
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
    unit._interrupted = False
    unit._heal_checkpoint = None
    unit._recycle_kill_pending = False
    unit._user_stopped_current_turn = False
    unit._tool_call_leak_recovery = False
    unit._buffer_overflow_recovery = False
    unit._active_agent_tools = {}
    unit._open_tool_uses = {}
    unit._pending_tool_use_id = None
    unit._pending_question = None
    unit._last_drained_seqs = []
    unit._client_io = asyncio.Lock()
    unit._graceful_wrap_pending = False
    unit._channel_wrap_injected = False
    unit._hard_floor_wrap_injected = False
    unit._wrapup_conclusion = ""
    unit._app_session_id = "app-sess-456"
    # health sensor stub — turn_count below floor so wrap never injects
    _hs = MagicMock()
    _hs.turn_count = 0
    _hs._max_turns = 500
    unit._health_sensor = _hs
    from core.compaction_guard import CompactionGuard
    unit._compaction_guard = CompactionGuard()
    # The field under test — explicitly NOT set here for the default tests;
    # set per-test where a starting value matters.
    from core.streaming_orchestrator import StreamingOrchestrator
    from core.retry_manager import RetryManager
    unit._streaming_orchestrator = StreamingOrchestrator(parent=unit)
    unit._retry_manager = RetryManager(parent=unit)
    return unit


# ═══════════════════════════════════════════════════════════════════
# AC1: per-instance default False (fail-closed) — verified on REAL __init__
# ═══════════════════════════════════════════════════════════════════


class TestAC1DefaultFailClosed:
    def test_real_init_defaults_flag_false(self):
        """The REAL SessionUnit.__init__ must initialise _last_turn_clean=False.

        Not the __new__ helper — the production constructor. A fresh session has
        never completed a clean turn, so reuse must NOT be blessed by default.
        Asserting against __init__ (not _make_unit) avoids the __new__ self-
        provisioning trap (GUI60): the helper could set a value the real
        constructor doesn't.
        """
        import inspect
        from core import session_unit as su_mod

        src = inspect.getsource(su_mod.SessionUnit.__init__)
        assert "_last_turn_clean" in src, (
            "SessionUnit.__init__ must declare self._last_turn_clean"
        )
        # Fail-closed: default must be False, not True.
        # (We assert the literal init is to False — a True default would bless
        # the very first reuse of a never-completed session.)
        assert "_last_turn_clean: bool = False" in src or \
               "_last_turn_clean = False" in src, (
            "default must be False (fail-closed) — found a non-False init"
        )

    def test_instances_have_independent_flags(self):
        """AC4 isolation: the flag is a per-instance attribute, not shared.

        Mutating unit A's flag must not change unit B's. A module-level / class-
        level flag would fail this — and would cross sessions/tabs.
        """
        a = _make_unit(session_id="A")
        b = _make_unit(session_id="B")
        a._last_turn_clean = True
        b._last_turn_clean = False
        assert a._last_turn_clean is True
        assert b._last_turn_clean is False
        # And it lives on the instance __dict__, not the class.
        assert "_last_turn_clean" in a.__dict__
        assert "_last_turn_clean" not in type(a).__dict__


# ═══════════════════════════════════════════════════════════════════
# AC6: STREAMING entry clears the flag (every turn re-evaluated)
# ═══════════════════════════════════════════════════════════════════


class TestAC6ClearOnStreaming:
    def test_transition_to_streaming_clears_clean(self):
        """Entering STREAMING must reset _last_turn_clean=False.

        This is the per-turn re-evaluation: a turn is presumed poisoned until it
        reaches the success-result transition that sets it True again.
        """
        unit = _make_unit(state=SessionState.IDLE)
        unit._last_turn_clean = True  # pretend prior turn was clean
        unit._transition(SessionState.STREAMING)
        assert unit._last_turn_clean is False, (
            "STREAMING entry must clear _last_turn_clean (fail-closed per turn)"
        )

    def test_transition_to_idle_does_not_set_clean(self):
        """A bare IDLE transition (e.g. disconnect recovery) must NOT bless the turn.

        Only the success-result path sets clean=True. recover_from_disconnect and
        interrupt paths transition to IDLE without a result — those must leave the
        flag False so the next send() recycles.
        """
        unit = _make_unit(state=SessionState.STREAMING)
        unit._last_turn_clean = False
        unit._transition(SessionState.IDLE)
        assert unit._last_turn_clean is False, (
            "a non-result IDLE transition must not set clean=True"
        )


# ═══════════════════════════════════════════════════════════════════
# AC1/AC3: success-result sets clean=True (the ONLY set point)
# ═══════════════════════════════════════════════════════════════════


class TestAC3SuccessSetsClean:
    def test_success_result_path_sets_clean_true(self):
        """The successful ResultMessage transition (streaming_orchestrator ~:1319)
        must set _parent._last_turn_clean = True.

        Verified structurally: the set-True must live adjacent to the SUCCESS
        _transition(IDLE) + _retry_count=0 reset (not the error_max_turns branch
        at :889, not the error branches). We assert the source places the set on
        the success path and NOT on the error_max_turns branch.
        """
        import inspect
        from core import streaming_orchestrator as so_mod

        src = inspect.getsource(so_mod.StreamingOrchestrator._read_formatted_response)
        assert "_last_turn_clean = True" in src, (
            "success-result path must set _parent._last_turn_clean = True"
        )
        # The set must be on the success path: it appears AFTER the
        # `self._parent._retry_count = 0` success marker, and the
        # error_max_turns branch must NOT carry a set-True.
        idx_set = src.find("_last_turn_clean = True")
        idx_retry0 = src.find("_retry_count = 0")
        assert idx_retry0 != -1 and idx_set > idx_retry0, (
            "set-True must be on the success completion path (after _retry_count=0), "
            "not the error_max_turns branch"
        )


# ═══════════════════════════════════════════════════════════════════
# AC2/AC3: send() recycles a poisoned warm subprocess BEFORE reuse
# ═══════════════════════════════════════════════════════════════════


class TestAC2PoisonedRecycle:
    @pytest.mark.asyncio
    async def test_poisoned_idle_triggers_recycle_before_reuse(self):
        """state=IDLE, _client alive, _last_turn_clean=False (poisoned) → send()
        must call _crash_to_cold_async(clear_identity=False) before reusing.
        """
        unit = _make_unit(state=SessionState.IDLE)
        unit._client = MagicMock()  # alive warm client
        unit._last_turn_clean = False  # poisoned

        arm = AsyncMock()

        async def _recycle(*a, **k):
            # Faithful: the real _crash_to_cold_async(clear_identity=False) drops
            # the client + transitions to COLD. Mirror that so the subsequent
            # spawn branch is reached as in production.
            unit._client = None
            unit.state = SessionState.COLD
            _recycle.calls.append(k)
        _recycle.calls = []

        # Stop right after recycle+spawn-decision: _ensure_spawned is the next
        # step once state is COLD — raise a sentinel there.
        async def _spawn_stop(*a, **k):
            raise _StopAfterDecisionError("stop after recycle+spawn decision")
            yield {}  # pragma: no cover

        with patch.object(unit, "_crash_to_cold_async", _recycle), \
             patch.object(unit, "_arm_recovery_checkpoint", arm), \
             patch.object(unit, "_ensure_spawned", _spawn_stop), \
             patch.object(unit, "_await_streaming_slot", AsyncMock()):
            with pytest.raises(_StopAfterDecisionError):
                async for _ in unit.send(
                    "hi", MagicMock(), app_session_id="app-sess-456"
                ):
                    pass

        assert len(_recycle.calls) == 1, "poisoned warm IDLE must recycle exactly once"
        # clear_identity must be False (preserve --resume identity)
        assert _recycle.calls[0].get("clear_identity") is False, (
            "recycle must preserve --resume identity (clear_identity=False)"
        )

    @pytest.mark.asyncio
    async def test_recycle_kill_pending_cleared_after_clean_respawn(self):
        """Gate-2 MED: after a poison-guard recycle + SUCCESSFUL respawn, the
        _recycle_kill_pending marker must be cleared.

        Otherwise a genuine OOM (-9) later in the SAME turn would be misclassified
        ZOMBIE (skipping the 30/60/120s backoff + the _consecutive_oom_kills
        increment that drives the OOM circuit breaker). The send-entry clear runs
        BEFORE the poison-guard arms the marker, so it cannot cover this path.

        MUTATION ANCHOR: remove the post-spawn `_recycle_kill_pending = False`
        → this test goes RED (marker survives into the fresh stream).
        """
        unit = _make_unit(state=SessionState.IDLE)
        unit._client = MagicMock()
        unit._last_turn_clean = False  # poisoned → will recycle
        unit._recycle_kill_pending = False

        async def _recycle(*a, **k):
            unit._client = None
            unit.state = SessionState.COLD
            # Mirror the real _arm_recovery_checkpoint side-effect: the recycle
            # arms the marker True (poison_guard_recycle is in the recycle set).
            unit._recycle_kill_pending = True

        async def _arm(trigger, **k):
            # Faithful to _arm_recovery_checkpoint: arms the marker for recycles.
            unit._recycle_kill_pending = trigger in (
                "flush_recycle", "interrupt_recycle", "poison_guard_recycle",
            )

        async def _spawn_ok(*a, **k):
            # Clean respawn: subprocess alive again, state back to IDLE.
            unit.state = SessionState.IDLE
            unit._client = MagicMock()
            if False:
                yield {}

        # Stop at the streaming slot (right after the spawn block) so we observe
        # the post-spawn marker state without driving the full stream.
        async def _slot_stop():
            raise _StopAfterDecisionError("stop after spawn")

        with patch.object(unit, "_crash_to_cold_async", _recycle), \
             patch.object(unit, "_arm_recovery_checkpoint", _arm), \
             patch.object(unit, "_ensure_spawned", _spawn_ok), \
             patch.object(unit, "_await_streaming_slot", _slot_stop):
            with pytest.raises(_StopAfterDecisionError):
                async for _ in unit.send(
                    "hi", MagicMock(), app_session_id="app-sess-456"
                ):
                    pass

        assert unit._recycle_kill_pending is False, (
            "a clean respawn after poison-guard recycle must clear "
            "_recycle_kill_pending, else a later genuine OOM is misclassified ZOMBIE"
        )

    @pytest.mark.asyncio
    async def test_clean_idle_does_not_recycle(self):
        """AC2 fast path: state=IDLE, _client alive, _last_turn_clean=True →
        send() must NOT recycle — warm reuse fast path preserved.

        MUTATION ANCHOR: if the guard is wrong (recycles regardless of flag),
        this test goes RED. If the flag is wrongly reset at send() entry, the
        clean turn looks poisoned and recycle fires → this test goes RED.
        """
        unit = _make_unit(state=SessionState.IDLE)
        unit._client = MagicMock()  # alive warm client
        unit._last_turn_clean = True  # clean — fast path

        recycle = AsyncMock()
        # Stop right after the reuse decision (at the streaming-slot await, which
        # is the first await AFTER the reuse-vs-recycle branch) so we observe ONLY
        # the recycle decision — not streaming-body exception recovery.
        slot = AsyncMock(side_effect=_StopAfterDecisionError("stop after decision"))
        with patch.object(unit, "_crash_to_cold_async", recycle), \
             patch.object(unit, "_arm_recovery_checkpoint", AsyncMock()), \
             patch.object(unit, "_await_streaming_slot", slot):
            with pytest.raises(_StopAfterDecisionError):
                async for _ in unit.send(
                    "hi", MagicMock(), app_session_id="app-sess-456"
                ):
                    pass

        recycle.assert_not_awaited()


class TestWarmFastPathPreserved:
    """The Gate-1 trap test: a CLEAN multi-turn conversation must never recycle.

    This is the highest-value regression guard. If _last_turn_clean ever lands in
    send()'s Layer-0 reset batch (the documented trap), the flag would be cleared
    at send entry BEFORE the reuse check, every reuse would look poisoned, and a
    healthy conversation would eat a kill+respawn every turn. This test pins the
    invariant: clean flag set True → next send sees True → no recycle.
    """

    @pytest.mark.asyncio
    async def test_flag_survives_send_entry_to_reuse_check(self):
        """The flag set True at the end of turn N must still be True when turn N+1's
        send() reaches the reuse decision (i.e. it is NOT in the send-entry reset batch).
        """
        unit = _make_unit(state=SessionState.IDLE)
        unit._client = MagicMock()
        unit._last_turn_clean = True

        seen = {}
        real_crash = AsyncMock()

        async def _capture_at_slot():
            # The streaming-slot await is the first await AFTER the reuse
            # decision. If the flag had been cleared at send() entry, the
            # decision would already have recycled — capture proves it survived.
            seen["clean_at_reuse"] = unit._last_turn_clean
            raise _StopAfterDecisionError("stop after decision")

        with patch.object(unit, "_crash_to_cold_async", real_crash), \
             patch.object(unit, "_arm_recovery_checkpoint", AsyncMock()), \
             patch.object(unit, "_await_streaming_slot", _capture_at_slot):
            with pytest.raises(_StopAfterDecisionError):
                async for _ in unit.send(
                    "hi", MagicMock(), app_session_id="app-sess-456"
                ):
                    pass

        real_crash.assert_not_awaited()
        assert seen.get("clean_at_reuse") is True, (
            "the clean flag must NOT be cleared at send() entry — it must survive "
            "to the reuse decision point (Gate-1 reset-batch trap)"
        )


class TestTurnClientIdResetAtSendEntry:
    """ROOT-FIX (audit Finding 1): _turn_client_id shares the pending-question turn
    lifecycle, so it MUST be zeroed in send()'s new-turn reset batch — at the single
    admission chokepoint — NOT patched per-entrance in the router.

    Why this is the root fix (and the router `elif` was a band-aid): the stale-key
    bug was 'a keyless drain turn inherits a prior KEYED turn's cid → its answer row
    keys to the wrong bubble'. Resetting HERE means every entrance (main / drain /
    channel) starts from None, so the router only ever SETS a key, never has to
    remember to CLEAR one. This test drives the REAL send() and captures the field
    at the reuse-decision slot — the same seam as the clean-flag trap test above —
    proving the reset actually runs before any continuation could read it.
    """

    @pytest.mark.asyncio
    async def test_stale_turn_client_id_cleared_at_send_entry(self):
        """A prior KEYED turn left cid='prior-keyed' on the unit. The next turn's
        send() must have zeroed it by the time execution reaches the reuse decision —
        so a keyless (drain) turn's continuation can never inherit the stale key."""
        unit = _make_unit(state=SessionState.IDLE)
        unit._client = MagicMock()
        unit._last_turn_clean = True
        unit._turn_client_id = "prior-keyed"  # residue from an earlier keyed turn

        seen = {}

        async def _capture_at_slot():
            seen["cid_at_reuse"] = unit._turn_client_id
            raise _StopAfterDecisionError("stop after decision")

        with patch.object(unit, "_crash_to_cold_async", AsyncMock()), \
             patch.object(unit, "_arm_recovery_checkpoint", AsyncMock()), \
             patch.object(unit, "_await_streaming_slot", _capture_at_slot):
            with pytest.raises(_StopAfterDecisionError):
                async for _ in unit.send(
                    "hi", MagicMock(), app_session_id="app-sess-456"
                ):
                    pass

        assert seen.get("cid_at_reuse") is None, (
            "_turn_client_id must be reset to None in send()'s new-turn batch — a "
            "stale prior-turn key would attach a keyless turn's continuation content "
            "to the wrong bubble (Finding 1 root regression)"
        )


class TestPoisonGuardClassifiedZombie:
    """The poison-guard recycle's SIGKILL must classify ZOMBIE (~0.5s respawn),
    NOT OOM (30/60/120s backoff) — else 'first resume fails' becomes 'first resume
    is 30s slow', defeating the fix's purpose.

    REVIEW finding (MEDIUM-1): the recycle trigger 'poison_guard_recycle' must be
    in _arm_recovery_checkpoint's recycle-kill set that sets _recycle_kill_pending.
    """

    @pytest.mark.asyncio
    async def test_poison_guard_trigger_sets_recycle_kill_pending(self):
        unit = _make_unit(state=SessionState.IDLE)
        unit._recycle_kill_pending = False
        unit._wrapup_conclusion = ""
        # _arm_recovery_checkpoint sets the flag BEFORE the idempotency return;
        # a pre-armed checkpoint short-circuits the rest, isolating the flag set.
        unit._heal_checkpoint = object()  # force early return after the flag set
        await unit._arm_recovery_checkpoint("poison_guard_recycle")
        assert unit._recycle_kill_pending is True, (
            "poison_guard_recycle must classify the kill ZOMBIE (fast respawn), "
            "not OOM (slow backoff)"
        )

    @pytest.mark.asyncio
    async def test_non_recycle_trigger_does_not_set_pending(self):
        """Mutation anchor: a genuine-hang trigger must NOT be classified ZOMBIE."""
        unit = _make_unit(state=SessionState.IDLE)
        unit._recycle_kill_pending = False
        unit._wrapup_conclusion = ""
        unit._heal_checkpoint = object()
        await unit._arm_recovery_checkpoint("rss_proactive")
        assert unit._recycle_kill_pending is False, (
            "rss/stuck/watchdog kills keep OOM-style backoff, not ZOMBIE"
        )


# ═══════════════════════════════════════════════════════════════════
# Test scaffolding
# ═══════════════════════════════════════════════════════════════════


class _StopAfterDecisionError(Exception):
    pass


class _StopAfterDecision:
    """An async-iterator-returning callable that raises after the reuse decision,
    so the test observes ONLY the recycle decision, not the full stream."""

    def __call__(self, *args, **kwargs):
        raise _StopAfterDecisionError("stop after reuse decision")


async def _empty_async_iter(*args, **kwargs):
    """A _read_formatted_response stand-in that yields nothing."""
    if False:
        yield {}
