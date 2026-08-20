"""Tests for the two tool-FREE kill paths consulting the CPU-liveness authority
(run_dcd668a6). Split-brain fix: BOTH the orchestrator wait_for timeout AND the
_pid_watchdog output-liveness backstop must gate a kill on _tool_free_hang_verdict.

- AC5: _pid_watchdog backstop does NOT force-kill a CPU-busy ('working') tool-free
       turn; DOES force-kill a 'wedged' one; DOES force-kill past the hard ceiling
       regardless of CPU.
- AC9: SDK-internal guard — a fresh __anext__ after a prior wait_for-cancelled
       __anext__ on an anyio memory stream fed by a DETACHED task yields the next
       message intact (no buffer corruption). Locks the architecture fact the
       orchestrator extension relies on; goes RED if a future SDK moved the read
       buffer into the consumer coroutine.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from core.session_unit import SessionState, SessionUnit


def _make_unit(session_id: str = "tf-kill", pid: int | None = 777) -> SessionUnit:
    unit = SessionUnit.__new__(SessionUnit)
    unit.session_id = session_id
    unit.state = SessionState.STREAMING
    unit._sdk_session_id = None
    unit._client = None
    if pid is not None:
        wrapper = MagicMock()
        wrapper.pid = pid
        unit._wrapper = wrapper
    else:
        unit._wrapper = None
    unit._streaming_start_time = time.time()
    unit._last_event_time = time.time() - 400  # 400s of silence (> 300s timeout)
    unit._last_known_context_tokens = 0
    unit._open_tool_uses = {}
    unit._tool_hang_interrupted = False
    unit._tool_hang_interrupt_at = None
    unit._tool_hang_episodes = 0
    unit._tool_free_extensions = 0
    unit._consecutive_unstick_timeouts = 0
    unit._pid_watchdog_task = None
    unit._PID_WATCHDOG_INTERVAL = 0.02
    unit.last_used = time.time()
    unit.is_channel_session = False
    unit._retry_count = 0
    unit._max_retries = 3
    unit._on_state_change = None
    unit._stop_event = asyncio.Event()
    unit._peak_tree_rss_bytes = 0
    unit._last_proactive_restart = 0
    unit._hooks_enqueued = False
    unit._force_kill = AsyncMock()
    unit.interrupt = AsyncMock(return_value=True)
    # keep verdict sampling near-instant in tests
    unit.TOOL_FREE_VERDICT_SAMPLES = 2
    unit.CPU_PROBE_INTERVAL_S = 0.001
    return unit


# ── AC5: watchdog backstop consults the verdict ─────────────────────────


@pytest.mark.asyncio
async def test_watchdog_skips_kill_when_working():
    """AC5: CPU-busy ('working') tool-free silence → watchdog does NOT force-kill."""
    unit = _make_unit(pid=777)
    with patch("os.kill", return_value=None), \
         patch.object(unit, "_tool_free_hang_verdict", AsyncMock(return_value="working")):
        task = asyncio.create_task(unit._pid_watchdog_loop(777))
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    unit._force_kill.assert_not_called()
    assert unit.state == SessionState.STREAMING  # never transitioned to DEAD


@pytest.mark.asyncio
async def test_watchdog_does_not_kill_if_state_left_streaming_during_verdict():
    """Gate-2 CRITICAL: the verdict await lasts ~SAMPLES×INTERVAL; during it the
    orchestrator (a separate task) can transition STREAMING → WAITING_INPUT (a
    permission / ask_user_question). The watchdog MUST re-check state after the
    verdict and NOT force-kill a session legitimately waiting for user input —
    even though the verdict returns 'wedged' as its own state-change failsafe."""
    unit = _make_unit(pid=781)

    async def _verdict_that_flips_state(_pid):
        # Simulate the ~6s verdict during which a permission arrives:
        unit.state = SessionState.WAITING_INPUT
        return "wedged"  # verdict's own state-guard failsafe

    with patch("os.kill", return_value=None), \
         patch.object(unit, "_tool_free_hang_verdict", _verdict_that_flips_state):
        task = asyncio.create_task(unit._pid_watchdog_loop(781))
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    unit._force_kill.assert_not_called()
    assert unit.state == SessionState.WAITING_INPUT  # not clobbered to DEAD


@pytest.mark.asyncio
async def test_watchdog_kills_when_wedged():
    """AC5: genuinely wedged ('wedged') → watchdog force-kills as before."""
    unit = _make_unit(pid=778)
    with patch("os.kill", return_value=None), \
         patch.object(unit, "_tool_free_hang_verdict", AsyncMock(return_value="wedged")):
        task = asyncio.create_task(unit._pid_watchdog_loop(778))
        await asyncio.sleep(0.2)
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            task.cancel()
    unit._force_kill.assert_called()
    assert unit.state == SessionState.DEAD


@pytest.mark.asyncio
async def test_watchdog_kills_past_hard_ceiling_regardless_of_cpu():
    """AC4/AC5: silence past TOOL_FREE_HARD_CEILING_S → kill even if CPU-busy
    (a busy-loop that pegs CPU forever is never 'wedged' — ceiling is the bound)."""
    unit = _make_unit(pid=779)
    # silence beyond the hard ceiling
    unit._last_event_time = time.time() - (unit.TOOL_FREE_HARD_CEILING_S + 100)
    verdict_spy = AsyncMock(return_value="working")
    with patch("os.kill", return_value=None), \
         patch.object(unit, "_tool_free_hang_verdict", verdict_spy):
        task = asyncio.create_task(unit._pid_watchdog_loop(779))
        await asyncio.sleep(0.2)
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            task.cancel()
    # past the ceiling, the verdict is not even consulted — kill outright
    verdict_spy.assert_not_called()
    unit._force_kill.assert_called()
    assert unit.state == SessionState.DEAD


# ── AC9: SDK detached-reader guard ──────────────────────────────────────


@pytest.mark.asyncio
async def test_fresh_anext_after_cancelled_anext_yields_next_message():
    """AC9 (locks the Gate-1 CRITICAL refutation): the SDK reads in a DETACHED
    task feeding an anyio memory stream. A wait_for-cancelled consumer __anext__
    cancels only the consumer's park on that stream — NOT the background reader
    or its buffer. So a FRESH __anext__ afterward yields the next message intact.

    This mirrors the SDK architecture (query.py:121 memory stream + :227
    spawn_detached(_read_messages)). If a future SDK moved the read buffer into
    the consumer coroutine, this guard goes RED before prod stream corruption.
    """
    send, receive = anyio.create_memory_object_stream(max_buffer_size=100)

    async def _detached_reader():
        # Simulates the SDK's background reader: feeds messages over time,
        # independent of whoever is consuming (or cancelling) on the receive end.
        for i in range(3):
            await asyncio.sleep(0.02)
            await send.send({"seq": i})
        send.close()

    reader = asyncio.create_task(_detached_reader())
    aiter = receive.__aiter__()

    # First consume attempt: time out (cancel the __anext__ before msg 0 arrives).
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(aiter.__anext__(), timeout=0.001)

    # The detached reader kept running; a FRESH __anext__ must yield msg 0 intact,
    # then 1, then 2 — no corruption, no lost/garbled message.
    got = []
    for _ in range(3):
        got.append(await aiter.__anext__())
    assert got == [{"seq": 0}, {"seq": 1}, {"seq": 2}]

    await reader
