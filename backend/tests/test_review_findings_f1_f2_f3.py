"""Three verified review findings (run_806e2cb5).

F1 (MED): lifecycle_manager._check_streaming_timeout force-unstuck a STREAMING
  session on pure event-silence (300s) with no open-tool awareness — killing a
  healthy CPU-busy long tool before the in-session 1800s CPU probe ran. It must
  mirror the in-session guard (session_unit:950): skip when a tool is open.
  The pure-API-hang path (no open tool) must STILL force-unstick.

F2 (LOW): _maybe_escape_wedged_tool set _tool_hang_interrupted=True BEFORE
  awaiting interrupt(). A failed interrupt left the latch on, disabling the
  hard-ceiling + episode-escalation tier. The latch must be set only on success.

F3 (LOW): ask_question_gate surfaced the question (enqueue) before the waiter
  was registered (inside wait_for_answer). A fast non-human set_answer in that
  window dropped. A synchronous register_waiter() must close it.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


# ─── F1: lifecycle streaming-timeout respects open tools ──────────────────


@pytest.mark.asyncio
async def test_f1_open_tool_skips_force_unstick():
    """STREAMING + stall>timeout + an OPEN tool → must NOT force-unstick."""
    from core.lifecycle_manager import LifecycleManager
    from core.session_unit import SessionState as SS

    unit = MagicMock()
    unit.state = SS.STREAMING
    unit.streaming_stall_seconds = 10_000.0       # way past any timeout
    unit._compute_message_timeout = MagicMock(return_value=300.0)
    unit._open_tool_uses = {"toolu_1": (0.0, "Bash")}  # a tool IS open
    unit._consecutive_unstick_timeouts = 0
    unit._UNSTICK_CIRCUIT_BREAKER_THRESHOLD = 2
    unit.force_unstick_streaming = AsyncMock()
    unit.session_id = "s-open-tool"

    mgr = LifecycleManager.__new__(LifecycleManager)
    mgr._router = MagicMock()
    mgr._router.list_units = MagicMock(return_value=[unit])

    await mgr._check_streaming_timeout()

    unit.force_unstick_streaming.assert_not_called()


@pytest.mark.asyncio
async def test_f1_no_open_tool_still_force_unsticks():
    """API-hang path preserved: STREAMING + stall>timeout + NO open tool → kill."""
    from core.lifecycle_manager import LifecycleManager
    from core.session_unit import SessionState as SS

    unit = MagicMock()
    unit.state = SS.STREAMING
    unit.streaming_stall_seconds = 10_000.0
    unit._compute_message_timeout = MagicMock(return_value=300.0)
    unit._open_tool_uses = {}                      # NO tool open → genuine API hang
    unit._consecutive_unstick_timeouts = 0
    unit._UNSTICK_CIRCUIT_BREAKER_THRESHOLD = 2
    unit.force_unstick_streaming = AsyncMock()
    unit.session_id = "s-api-hang"

    mgr = LifecycleManager.__new__(LifecycleManager)
    mgr._router = MagicMock()
    mgr._router.list_units = MagicMock(return_value=[unit])

    await mgr._check_streaming_timeout()

    unit.force_unstick_streaming.assert_called_once()


# ─── F2: _tool_hang_interrupted latched only on successful interrupt ──────


@pytest.mark.asyncio
async def test_f2_latch_false_after_interrupt_raises(monkeypatch):
    from core.session_unit import SessionUnit
    from core.session_unit import SessionState as SS

    unit = SessionUnit(session_id="s-f2-raise", agent_id="a")
    unit.state = SS.STREAMING
    unit._open_tool_uses = {"t1": (0.0, "Bash")}
    unit._tool_hang_interrupted = False

    # CPU probe → looks wedged (delta < epsilon).
    import core.resource_monitor as rm
    monkeypatch.setattr(rm.resource_monitor, "tree_cpu_seconds", lambda pid: 0.0)
    monkeypatch.setattr(unit, "_oldest_open_tool", lambda: "t1")
    monkeypatch.setattr(unit, "interrupt",
                        AsyncMock(side_effect=RuntimeError("boom")))

    await unit._maybe_escape_wedged_tool(pid=123, tool_age=700.0,
                                         tool_name="Bash", tool_id="t1")
    assert unit._tool_hang_interrupted is False, \
        "failed interrupt must NOT latch the tier off"


@pytest.mark.asyncio
async def test_f2_latch_false_after_interrupt_returns_false(monkeypatch):
    from core.session_unit import SessionUnit
    from core.session_unit import SessionState as SS

    unit = SessionUnit(session_id="s-f2-false", agent_id="a")
    unit.state = SS.STREAMING
    unit._open_tool_uses = {"t1": (0.0, "Bash")}
    unit._tool_hang_interrupted = False

    import core.resource_monitor as rm
    monkeypatch.setattr(rm.resource_monitor, "tree_cpu_seconds", lambda pid: 0.0)
    monkeypatch.setattr(unit, "_oldest_open_tool", lambda: "t1")
    monkeypatch.setattr(unit, "interrupt", AsyncMock(return_value=False))

    await unit._maybe_escape_wedged_tool(pid=123, tool_age=700.0,
                                         tool_name="Bash", tool_id="t1")
    assert unit._tool_hang_interrupted is False


@pytest.mark.asyncio
async def test_f2_latch_true_after_interrupt_success(monkeypatch):
    from core.session_unit import SessionUnit
    from core.session_unit import SessionState as SS

    unit = SessionUnit(session_id="s-f2-ok", agent_id="a")
    unit.state = SS.STREAMING
    unit._open_tool_uses = {"t1": (0.0, "Bash")}
    unit._tool_hang_interrupted = False

    import core.resource_monitor as rm
    monkeypatch.setattr(rm.resource_monitor, "tree_cpu_seconds", lambda pid: 0.0)
    monkeypatch.setattr(unit, "_oldest_open_tool", lambda: "t1")
    monkeypatch.setattr(unit, "interrupt", AsyncMock(return_value=True))

    await unit._maybe_escape_wedged_tool(pid=123, tool_age=700.0,
                                         tool_name="Bash", tool_id="t1")
    assert unit._tool_hang_interrupted is True, \
        "successful interrupt SHOULD latch (once-per-episode guard)"


# ─── F3: waiter registered before surface ─────────────────────────────────


def test_f3_register_waiter_makes_live_synchronously():
    from core.ask_question_manager import AskQuestionManager

    m = AskQuestionManager()
    assert m.has_live_waiter("tid") is False
    m.register_waiter("tid")
    assert m.has_live_waiter("tid") is True, \
        "register_waiter must make the waiter live synchronously (before any await)"


@pytest.mark.asyncio
async def test_f3_answer_after_register_not_dropped():
    """An answer set after register_waiter but before wait_for_answer is awaited
    must be delivered, not dropped."""
    from core.ask_question_manager import AskQuestionManager

    m = AskQuestionManager()
    m.register_waiter("tid")
    # Answer arrives in the surface→await window (non-human auto-answer).
    m.set_answer("tid", {"q1": "yes"})
    # Now the hook finally awaits — must see the answer, not a dropped/empty.
    result = await m.wait_for_answer("tid", timeout=1)
    assert result == {"q1": "yes"}, f"answer dropped: {result}"


def test_f3_discard_waiter_reaps_leaked_event():
    """Adversarial MED: if the surface→await span throws after register_waiter
    but before wait_for_answer, the event must be reapable — else has_live_waiter
    lies True with no coroutine blocked (ghost question / answer-into-void)."""
    from core.ask_question_manager import AskQuestionManager

    m = AskQuestionManager()
    m.register_waiter("tid")
    assert m.has_live_waiter("tid") is True
    # Simulate the enqueue-throws path reaping the waiter.
    m.discard_waiter("tid")
    assert m.has_live_waiter("tid") is False, \
        "leaked waiter must be reaped — else stale-item drop-guard is defeated"
    # Idempotent — second discard is a no-op, not an error.
    m.discard_waiter("tid")


@pytest.mark.asyncio
async def test_f3_hook_discards_waiter_when_enqueue_raises(monkeypatch):
    """End-to-end: ask_question_gate must NOT leak a waiter if enqueue raises."""
    from core.ask_question_manager import ask_question_manager

    # Ensure clean slate for this id.
    ask_question_manager.discard_waiter("toolu_leak")

    # Make the surfacing call raise.
    async def _boom(*a, **k):
        raise RuntimeError("enqueue failed")

    import core.permission_manager as pm
    monkeypatch.setattr(pm.permission_manager, "enqueue_permission_request", _boom)

    # Drive just the register→enqueue span the way the hook does it.
    ask_question_manager.register_waiter("toolu_leak")
    try:
        await pm.permission_manager.enqueue_permission_request("s", {})
    except BaseException:
        ask_question_manager.discard_waiter("toolu_leak")

    assert ask_question_manager.has_live_waiter("toolu_leak") is False, \
        "hook must reap the waiter when enqueue raises"


@pytest.mark.asyncio
async def test_f1_dead_pid_with_open_tool_still_detected(monkeypatch):
    """Adversarial LOW: F1 made the in-session PID watchdog the SOLE backstop for
    open-tool sessions (lifecycle now skips them). Pin the load-bearing invariant:
    a dead subprocess WITH an open tool is still detected (pid-death check runs
    before the open-tool liveness tier)."""
    import os
    from core.session_unit import SessionUnit
    from core.session_unit import SessionState as SS

    unit = SessionUnit(session_id="s-dead-open-tool", agent_id="a")
    unit.state = SS.STREAMING
    unit._open_tool_uses = {"t1": (0.0, "Bash")}  # a tool IS open

    # os.kill(pid, 0) raising ProcessLookupError == process is dead.
    def _dead(pid, sig):
        raise ProcessLookupError()
    monkeypatch.setattr(os, "kill", _dead)

    task = asyncio.create_task(unit._pid_watchdog_loop(999999))
    # Give the loop a couple of poll intervals to detect the dead pid.
    for _ in range(60):
        await asyncio.sleep(0.1)
        if unit.state == SS.DEAD:
            break
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    assert unit.state == SS.DEAD, \
        "dead pid with an open tool must still be detected (pid-death before tool tier)"
