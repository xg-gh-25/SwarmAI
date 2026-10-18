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
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit
from core.streaming_orchestrator import StreamingOrchestrator


class _FakeReceiveResponse:
    """Async iterator: first __anext__ hangs past the timeout, then yields a
    ResultMessage so the loop can terminate cleanly after an extension."""

    def __init__(self, hang_s: float, result_msg):
        self._hang_s = hang_s
        self._result = result_msg
        self._served = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        self._served += 1
        if self._served == 1:
            # Hang longer than the tiny test timeout → wait_for cancels this read.
            await asyncio.sleep(self._hang_s)
        # Reaching the SECOND read PROVES the timeout handler extended (looped
        # again) instead of raising. Raise a UNIQUE marker so the test confirms
        # the extension without driving the full message-processing / post-stream
        # path (out of scope for this timeout-handler test).
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
        hang_s=timeout_s * 20, result_msg=result_msg
    )
    return orch, parent


async def _drain(orch):
    """Drive the read loop under a fresh per-test permission queue (the real
    singleton binds an asyncio.Queue to the first test's loop → cross-loop hang).
    Returns the list of yielded events (raises propagate)."""
    from core import permission_manager as _pm_mod

    fresh_q = asyncio.Queue()
    with patch.object(
        _pm_mod.permission_manager, "get_session_queue", return_value=fresh_q
    ):
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
# - a pending permission that never reached this stream is re-queued+surfaced;
# - the loser-cancel loop no longer swallows the stream task's OWN cancellation.


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


@pytest.mark.asyncio
async def test_lost_permission_is_rescued_and_surfaced():
    from datetime import datetime, timedelta
    from core import permission_manager as _pm_mod

    orch, parent = _make_orchestrator(verdict="wedged")
    pending = [{
        "id": "perm_lost1", "session_id": parent.session_id, "tool_name": "Bash",
        "tool_input": '{"command": "rm -rf /tmp/x"}', "reason": "dangerous",
        "status": "pending",
        "created_at": (datetime.now() - timedelta(seconds=120)).isoformat(),
    }]
    pm = _pm_mod.permission_manager
    with patch.object(pm, "get_pending_for_session", return_value=pending), \
         patch.object(pm, "has_live_waiter", return_value=True):
        events = await _drain(orch)
    perm = [e for e in events if e.get("type") == "cmd_permission_request"]
    assert perm and perm[0]["requestId"] == "perm_lost1"
    assert perm[0]["toolInput"] == {"command": "rm -rf /tmp/x"}
    parent._transition.assert_called_with(SessionState.WAITING_INPUT)


@pytest.mark.asyncio
async def test_rescue_survives_a_stray_getter_on_the_queue():
    """A stray perm_queue.get() (e.g. an abandoned reader) would swallow a
    re-queued copy just like the original — the rescue must bypass the queue."""
    from datetime import datetime, timedelta
    from core import permission_manager as _pm_mod

    orch, parent = _make_orchestrator(verdict="wedged")
    pending = [{
        "id": "perm_lost2", "session_id": parent.session_id, "tool_name": "Bash",
        "tool_input": {"command": "git worktree add /tmp/w"}, "reason": "r",
        "created_at": (datetime.now() - timedelta(seconds=60)).isoformat(),
    }]
    q = asyncio.Queue()
    stray = asyncio.create_task(q.get())  # FIFO-first getter on the queue
    await asyncio.sleep(0)
    pm = _pm_mod.permission_manager
    try:
        with patch.object(pm, "get_session_queue", return_value=q), \
             patch.object(pm, "get_pending_for_session", return_value=pending), \
             patch.object(pm, "has_live_waiter", return_value=True):
            events = [ev async for ev in orch._read_formatted_response()]
    finally:
        stray.cancel()
    perm = [e for e in events if e.get("type") == "cmd_permission_request"]
    assert perm and perm[0]["requestId"] == "perm_lost2"


@pytest.mark.asyncio
async def test_rescue_on_heartbeat_tick_before_any_timeout():
    """The heartbeat-tick branch rescues too: the permission surfaces long
    before the read timeout (which would otherwise kill the turn)."""
    from datetime import datetime, timedelta
    from core import permission_manager as _pm_mod

    orch, parent = _make_orchestrator(verdict="wedged", timeout_s=30.0)
    orch._HEARTBEAT_POLL_S = 0.05
    pending = [{
        "id": "perm_tick", "session_id": parent.session_id, "tool_name": "Bash",
        "tool_input": {"command": "x"},
        "created_at": (datetime.now() - timedelta(seconds=60)).isoformat(),
    }]
    pm = _pm_mod.permission_manager
    with patch.object(pm, "get_pending_for_session", return_value=pending), \
         patch.object(pm, "has_live_waiter", return_value=True):
        events = await asyncio.wait_for(_drain(orch), timeout=5)
    assert [e["requestId"] for e in events if e.get("type") == "cmd_permission_request"] \
        == ["perm_tick"]
    parent._tool_free_hang_verdict.assert_not_awaited()  # no timeout happened


@pytest.mark.asyncio
async def test_rescue_scans_the_gate_key_when_it_differs():
    from datetime import datetime, timedelta
    from core import permission_manager as _pm_mod

    orch, parent = _make_orchestrator(verdict="wedged")
    parent._hook_session_context = {"sdk_session_id": "sdk-other-key"}
    lost = {"id": "perm_k", "session_id": "sdk-other-key", "tool_name": "Bash",
            "tool_input": {"command": "x"},
            "created_at": (datetime.now() - timedelta(seconds=60)).isoformat()}
    pm = _pm_mod.permission_manager
    with patch.object(pm, "get_pending_for_session",
                      side_effect=lambda k: [lost] if k == "sdk-other-key" else []), \
         patch.object(pm, "has_live_waiter", return_value=True):
        events = await _drain(orch)
    perm = [e for e in events if e.get("type") == "cmd_permission_request"]
    assert perm and perm[0]["requestId"] == "perm_k"
    assert perm[0]["sessionId"] == parent.session_id  # surfaced under the app id


@pytest.mark.asyncio
async def test_fresh_or_waiterless_permission_is_not_rescued():
    from datetime import datetime
    from core import permission_manager as _pm_mod

    orch, parent = _make_orchestrator(verdict="wedged")
    fresh = [{"id": "perm_new", "session_id": parent.session_id, "status": "pending",
              "created_at": datetime.now().isoformat()}]
    pm = _pm_mod.permission_manager
    with patch.object(pm, "get_pending_for_session", return_value=fresh), \
         patch.object(pm, "has_live_waiter", return_value=True):
        with pytest.raises(RuntimeError, match="Streaming timeout"):
            await _drain(orch)


class _SlowCancelRead:
    """First read blocks; when cancelled it takes a while to unwind (so the
    stream task is inside the loser-cancel `await task` when IT gets cancelled)."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(0.3)
            raise


@pytest.mark.asyncio
async def test_own_cancellation_is_not_swallowed_by_loser_cancel():
    from core import permission_manager as _pm_mod

    orch, parent = _make_orchestrator(verdict="wedged", timeout_s=30.0)
    parent._client.receive_response = lambda: _SlowCancelRead()
    q = asyncio.Queue()

    async def run():
        with patch.object(_pm_mod.permission_manager, "get_session_queue", return_value=q):
            async for _ev in orch._read_formatted_response():
                pass

    task = asyncio.create_task(run())
    await asyncio.sleep(0.05)
    # A permission wins the race → the loop cancels+awaits the slow sdk loser…
    q.put_nowait({"requestId": "perm_x", "sessionId": parent.session_id})
    await asyncio.sleep(0.1)
    task.cancel()  # …and the stream task itself is cancelled during that await.
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
