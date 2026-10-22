"""Drives the REAL streaming_orchestrator._read_formatted_response TimeoutError
handler to verify the tool-free CPU-gated extension (run_dcd668a6).

This is the path that actually killed session 2b22a852 at 13:22 (the precise
wait_for timer, NOT the watchdog). The test makes the SDK read time out once,
then — depending on the CPU verdict — the loop must either EXTEND (loop again,
consume the next message) or RAISE (kill as today).

- AC1: verdict 'working' at the timeout mark → NO RuntimeError, extension
       counter bumped, the next real message is consumed.
- AC2: verdict 'wedged' → RuntimeError raised (kill path preserved).
- AC6: is_first_message (INIT) path is covered too — extension applies at init.
- AC7: verdict 'unknown' → RuntimeError raised (fail-safe).
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit
from core.streaming_orchestrator import StreamingOrchestrator


class _FakeReceiveResponse:
    """Async iterator: the first read outlasts one timeout (1.5x), then raises
    _ExtendedMarker. The read loop never cancels a read at a timeout; it extends
    and keeps waiting on the SAME read, so the marker proves the extension."""

    def __init__(self, hang_s: float, result_msg):
        self._hang_s = hang_s
        self._result = result_msg
        self._served = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        self._served += 1
        if self._served == 1:
            # Outlast the tiny test timeout; the loop keeps this read pending.
            await asyncio.sleep(self._hang_s)
        # Finishing after the deadline PROVES the timeout handler extended
        # instead of raising. A UNIQUE marker confirms the extension without
        # driving the full message-processing / post-stream path.
        raise _ExtendedMarker()


class _ExtendedMarker(Exception):
    """Raised on the 2nd read — proves the loop extended past the timeout."""


def _make_orchestrator(verdict: str, timeout_s: float = 0.05):
    """Build a real orchestrator over a minimal fake parent SessionUnit."""
    from claude_agent_sdk import ResultMessage

    parent = SessionUnit.__new__(SessionUnit)
    parent.session_id = "orch-tf"
    parent.state = SessionState.STREAMING
    parent._sdk_session_id = None
    parent._streaming_start_time = time.time()
    parent._last_event_time = time.time()
    parent._last_known_context_tokens = 0
    parent._tool_free_extensions = 0
    parent._open_tool_uses = {}
    parent._active_agent_tools = set()
    parent._auto_surfaced_run_ids_set = set()
    parent._pending_question = None
    parent._pending_tool_use_id = None
    parent._tool_hang_episodes = 0
    parent._tool_hang_interrupted = False
    parent._tool_hang_interrupt_at = None
    parent._lifecycle_response_count = 0
    parent._content_emitted = False
    parent._interrupted = False
    parent._last_turn_clean = True
    parent._mcp_health_checked = True
    parent._model_name = "test-model"
    parent._compaction_guard = MagicMock()
    parent.last_used = time.time()
    parent._health_sensor = MagicMock()
    parent._maybe_build_elapsed_heartbeat = MagicMock(return_value=None)
    parent._transition = MagicMock()
    parent._compute_message_timeout = lambda: timeout_s
    parent._compute_init_timeout = lambda: timeout_s
    parent._last_progress_time = None  # streaming_stall_seconds reads this
    # pid drives the verdict gate
    wrapper = MagicMock()
    wrapper.pid = 999
    parent._wrapper = wrapper
    # constants (class-level, but set explicitly for clarity)
    parent.TOOL_FREE_MAX_EXTENSIONS = 4
    parent.TOOL_FREE_HARD_CEILING_S = 1800.0
    parent._tool_free_hang_verdict = AsyncMock(return_value=verdict)

    result_msg = ResultMessage(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
        num_turns=1, session_id="orch-tf", total_cost_usd=0.0, usage={},
        result="ok",
    )
    orch = StreamingOrchestrator(parent)
    parent._client = MagicMock()
    parent._client.receive_response = lambda: _FakeReceiveResponse(
        hang_s=timeout_s * 1.5, result_msg=result_msg
    )
    return orch, parent


async def _drain(orch):
    """Drive the read loop; returns the yielded events (raises propagate)."""
    events = []
    async for ev in orch._read_formatted_response():
        events.append(ev)
    return events


@pytest.mark.asyncio
async def test_ac1_working_verdict_extends_not_raises():
    """AC1: CPU-busy at the timeout mark → extend (no raise), counter bumped,
    next message consumed."""
    orch, parent = _make_orchestrator(verdict="working")
    # Must NOT raise RuntimeError (the kill path). Reaching the 2nd read (the
    # _ExtendedMarker) proves the loop extended instead of killing.
    with pytest.raises(_ExtendedMarker):
        await _drain(orch)
    assert parent._tool_free_extensions == 1, "extension counter must bump once"
    parent._tool_free_hang_verdict.assert_awaited()


@pytest.mark.asyncio
async def test_ac2_wedged_verdict_raises():
    """AC2: wedged → RuntimeError (kill path preserved)."""
    orch, parent = _make_orchestrator(verdict="wedged")
    with pytest.raises(RuntimeError, match="Streaming timeout"):
        await _drain(orch)
    assert parent._tool_free_extensions == 0


@pytest.mark.asyncio
async def test_ac7_unknown_verdict_raises_failsafe():
    """AC7: unknown (unmeasurable) → RuntimeError (fail-safe, never hang forever)."""
    orch, parent = _make_orchestrator(verdict="unknown")
    with pytest.raises(RuntimeError, match="Streaming timeout"):
        await _drain(orch)
    assert parent._tool_free_extensions == 0


@pytest.mark.asyncio
async def test_ac6_init_path_extends_too():
    """AC6: the is_first_message/INIT timeout also extends on 'working'
    (large-context cold-resume TTFT is the most common false-timeout)."""
    orch, parent = _make_orchestrator(verdict="working")
    # is_first_message starts True; the INIT_TIMEOUT == our tiny timeout, so the
    # FIRST read times out on the init path → must extend, not raise.
    with pytest.raises(_ExtendedMarker):
        await _drain(orch)
    assert parent._tool_free_extensions == 1


# ── 2026-10-18 offline-dive follow-ups ────────────────────────────────────
# - an OPEN tool defers the timeout to the tool-aware hang tier (no kill);
# - the hard ceiling is measured from the last SDK event, not turn start;
# - a prompt on the board surfaces at once (no queue, no rescue);
# - the stream task's OWN cancellation propagates and ends its read.


async def _live_watchdog():
    """A running stand-in for SessionUnit._pid_watchdog_task."""
    return asyncio.create_task(asyncio.sleep(3600))


@pytest.mark.asyncio
async def test_open_tool_defers_timeout_instead_of_killing():
    orch, parent = _make_orchestrator(verdict="wedged")  # would raise if probed
    # value shape is (start_time, name) — see SessionUnit._oldest_open_tool
    parent._open_tool_uses = {"toolu_1": (time.time() - 400, "Bash")}
    parent._pid_watchdog_task = await _live_watchdog()
    try:
        with pytest.raises(_ExtendedMarker):
            await _drain(orch)
    finally:
        parent._pid_watchdog_task.cancel()
    parent._tool_free_hang_verdict.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["no_watchdog", "watchdog_done", "not_streaming", "past_ceiling"])
async def test_open_tool_does_not_defer_when_hang_tier_cannot_act(case):
    """H1: deferring with a dead watchdog / non-STREAMING unit / a tool past the
    watchdog's own hard ceiling would never end the turn → probe/raise path."""
    orch, parent = _make_orchestrator(verdict="wedged")
    start = time.time() - 400
    if case == "past_ceiling":
        start = time.time() - (
            SessionUnit.TOOL_HANG_HARD_CEILING_S + SessionUnit.TOOL_HANG_INTERRUPT_GRACE_S + 60
        )
    parent._open_tool_uses = {"toolu_1": (start, "Bash")}
    wd = None
    if case != "no_watchdog":
        wd = await _live_watchdog()
        if case == "watchdog_done":
            wd.cancel()
            with pytest.raises(asyncio.CancelledError):
                await wd
    parent._pid_watchdog_task = wd
    if case == "not_streaming":
        parent.state = SessionState.WAITING_INPUT
    try:
        with pytest.raises(RuntimeError, match="Streaming timeout"):
            await _drain(orch)
    finally:
        if wd is not None:
            wd.cancel()
    parent._tool_free_hang_verdict.assert_awaited()


@pytest.mark.asyncio
async def test_extension_does_not_reset_silence_ceiling():
    """M1: the ceiling counts the SDK silence; an extension (which resets
    _last_event_time for the watchdog) must not restart that clock."""
    orch, parent = _make_orchestrator(verdict="working", timeout_s=0.2)
    parent.TOOL_FREE_HARD_CEILING_S = 0.3  # 1st timeout (~0.2s) extends, 2nd (~0.4s) must not

    class _AlwaysSilent:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(3600)

    parent._client.receive_response = lambda: _AlwaysSilent()
    with pytest.raises(RuntimeError, match="Streaming timeout"):
        await _drain(orch)
    # Old behaviour (ceiling from _last_event_time, reset by each extension)
    # extended all TOOL_FREE_MAX_EXTENSIONS times.
    assert parent._tool_free_extensions == 1


@pytest.mark.asyncio
async def test_ceiling_counts_silence_not_turn_age():
    orch, parent = _make_orchestrator(verdict="working")
    parent._streaming_start_time = time.time() - 5000  # turn far past 1800s ceiling
    parent._last_event_time = time.time()              # but it was progressing
    with pytest.raises(_ExtendedMarker):
        await _drain(orch)
    parent._tool_free_hang_verdict.assert_awaited()
    assert parent._tool_free_extensions == 1


def _publish_live(pm, session_id, rid, command="x"):
    """A prompt on the board, as a gate publishes it (its waiter registered)."""
    pm.publish_prompt(session_id, {
        "sessionId": session_id, "requestId": rid, "toolUseId": "tu_" + rid,
        "toolName": "Bash", "toolInput": {"command": command},
        "reason": "dangerous", "options": ["approve", "deny"],
    })


@pytest.mark.asyncio
async def test_prompt_published_mid_wait_surfaces_without_any_timeout():
    """A prompt published while the stream is silent wakes the loop at once:
    no tick, no read timeout, no rescue (the board replaced the lossy queue)."""
    from core import permission_manager as _pm_mod

    orch, parent = _make_orchestrator(verdict="wedged", timeout_s=30.0)
    pm = _pm_mod.permission_manager
    task = asyncio.ensure_future(_drain(orch))
    await asyncio.sleep(0.05)
    _publish_live(pm, parent.session_id, "perm_mid", "find ~/x -exec ls {} ;")
    events = await asyncio.wait_for(task, timeout=2)
    perm = [e for e in events if e.get("type") == "cmd_permission_request"]
    assert [e["requestId"] for e in perm] == ["perm_mid"]
    assert perm[0]["toolInput"] == {"command": "find ~/x -exec ls {} ;"}
    parent._transition.assert_called_with(SessionState.WAITING_INPUT)
    parent._tool_free_hang_verdict.assert_not_awaited()


@pytest.mark.asyncio
async def test_prompt_published_before_the_stream_reads_surfaces():
    from core import permission_manager as _pm_mod

    orch, parent = _make_orchestrator(verdict="wedged", timeout_s=30.0)
    _publish_live(_pm_mod.permission_manager, parent.session_id, "perm_early")
    events = await asyncio.wait_for(_drain(orch), timeout=2)
    assert [e["requestId"] for e in events
            if e.get("type") == "cmd_permission_request"] == ["perm_early"]


@pytest.mark.asyncio
async def test_board_is_read_under_the_gate_key_when_it_differs():
    from core import permission_manager as _pm_mod

    orch, parent = _make_orchestrator(verdict="wedged", timeout_s=30.0)
    parent._hook_session_context = {"sdk_session_id": "sdk-other-key"}
    _publish_live(_pm_mod.permission_manager, "sdk-other-key", "perm_k")
    events = await asyncio.wait_for(_drain(orch), timeout=2)
    perm = [e for e in events if e.get("type") == "cmd_permission_request"]
    assert perm and perm[0]["requestId"] == "perm_k"


@pytest.mark.asyncio
async def test_decided_prompt_is_not_surfaced():
    """A prompt decided before the stream saw it (e.g. from the tab) is off the
    board: nothing is surfaced and the silent stream times out normally."""
    from core import permission_manager as _pm_mod

    orch, parent = _make_orchestrator(verdict="wedged")
    pm = _pm_mod.permission_manager
    _publish_live(pm, parent.session_id, "perm_done")
    pm.set_permission_decision("perm_done", "approve")
    with pytest.raises(RuntimeError, match="Streaming timeout"):
        await _drain(orch)


class _SlowCancelRead:
    """First read blocks; when cancelled it takes a while to unwind."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(0.3)
            raise


@pytest.mark.asyncio
async def test_own_cancellation_propagates():
    orch, parent = _make_orchestrator(verdict="wedged", timeout_s=30.0)
    parent._client.receive_response = lambda: _SlowCancelRead()

    async def run():
        async for _ev in orch._read_formatted_response():
            pass

    task = asyncio.create_task(run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert orch._sdk_read is None  # the stream is over, so is its read
