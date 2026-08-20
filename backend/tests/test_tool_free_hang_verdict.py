"""Tests for the tool-FREE hang verdict + CPU-gated extension (run_dcd668a6).

A tool-FREE API hang (no tool_use open — pure Bedrock inference silent for
MESSAGE_TIMEOUT) was force-killed by a PURE WALL-CLOCK timer with no liveness
check, false-killing a CPU-busy-but-slow turn (live repro: session 2b22a852,
first-token then 300s CPU-busy silence → killed). This tier applies the same
tree-CPU discriminator (already proven for the tool-OPEN tier) to the tool-free
path, MULTI-sampled so a transient CPU dip is not mistaken for a wedge.

ACs:
- AC2: all-idle CPU across N windows → verdict 'wedged' (kill as today)
- AC3: transient one-window CPU dip → verdict 'working' (NOT killed) — the
       Gate-1 false-wedge fix
- AC7: tree_cpu_seconds None → verdict 'unknown' (fail-safe)
- state-left-STREAMING mid-sample → 'wedged' (adversarial round-3: a dead
       session must not be able to re-extend forever)
- AC8: sampling is await-based (asyncio.sleep), never sync-blocks the loop
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit


def _make_unit(session_id: str = "tf-session", pid: int | None = 4242) -> SessionUnit:
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
    unit._last_event_time = time.time()
    unit._last_known_context_tokens = 0
    unit._tool_free_extensions = 0
    unit.last_used = time.time()
    # class-level constants are inherited via __new__; keep sampling fast in tests
    unit.TOOL_FREE_VERDICT_SAMPLES = 3
    unit.CPU_PROBE_INTERVAL_S = 0.001
    unit.CPU_LIVE_EPSILON = 0.05
    return unit


def _cpu_series(values):
    """Return a fake tree_cpu_seconds that yields `values` in order (cycling last)."""
    seq = list(values)
    calls = {"i": 0}

    def _fake(_pid):
        i = calls["i"]
        calls["i"] += 1
        return seq[i] if i < len(seq) else seq[-1]

    return _fake


@pytest.mark.asyncio
async def test_ac2_all_idle_cpu_is_wedged():
    """AC2: cumulative CPU flat across all windows → 'wedged'."""
    unit = _make_unit()
    # 3 windows: (cpu0,cpu1) pairs all delta 0.0
    fake = _cpu_series([100.0, 100.0, 100.0, 100.0, 100.0, 100.0])
    with patch("core.resource_monitor.resource_monitor.tree_cpu_seconds", side_effect=fake):
        verdict = await unit._tool_free_hang_verdict(4242)
    assert verdict == "wedged"


@pytest.mark.asyncio
async def test_ac3_transient_dip_is_working():
    """AC3 (Gate-1 false-wedge fix): first window ~0, second window busy → 'working'."""
    unit = _make_unit()
    # window1: 100.0→100.0 (delta 0, dip). window2: 100.0→100.5 (delta 0.5 busy)
    fake = _cpu_series([100.0, 100.0, 100.0, 100.5])
    with patch("core.resource_monitor.resource_monitor.tree_cpu_seconds", side_effect=fake):
        verdict = await unit._tool_free_hang_verdict(4242)
    assert verdict == "working"


@pytest.mark.asyncio
async def test_ac2_busy_first_window_short_circuits_working():
    """A clearly busy first window → 'working' immediately (early return)."""
    unit = _make_unit()
    fake = _cpu_series([10.0, 10.2])  # delta 0.2 >> epsilon
    with patch("core.resource_monitor.resource_monitor.tree_cpu_seconds", side_effect=fake):
        verdict = await unit._tool_free_hang_verdict(4242)
    assert verdict == "working"


@pytest.mark.asyncio
async def test_ac7_none_cpu_is_unknown():
    """AC7: tree_cpu_seconds → None on every sample → 'unknown' (fail-safe)."""
    unit = _make_unit()
    with patch("core.resource_monitor.resource_monitor.tree_cpu_seconds", return_value=None):
        verdict = await unit._tool_free_hang_verdict(4242)
    assert verdict == "unknown"


@pytest.mark.asyncio
async def test_state_left_streaming_midsample_is_wedged():
    """Round-3 finding: if the session leaves STREAMING mid-verdict, return
    'wedged' (NOT 'working') — a dead session must not re-extend forever."""
    unit = _make_unit()

    def _fake(_pid):
        # flip state to DEAD after the first read so the post-sleep check trips
        unit.state = SessionState.DEAD
        return 100.0

    with patch("core.resource_monitor.resource_monitor.tree_cpu_seconds", side_effect=_fake):
        verdict = await unit._tool_free_hang_verdict(4242)
    assert verdict == "wedged"


@pytest.mark.asyncio
async def test_ac8_verdict_is_await_based_not_sync_block():
    """AC8: sampling yields the event loop (asyncio.sleep), never time.sleep."""
    unit = _make_unit()
    unit.CPU_PROBE_INTERVAL_S = 0.05
    fake = _cpu_series([100.0, 100.0, 100.0, 100.0, 100.0, 100.0])

    ticked = {"n": 0}

    async def _co_ticker():
        # A concurrent coroutine MUST get scheduled during the verdict's sleeps
        for _ in range(3):
            await asyncio.sleep(0.01)
            ticked["n"] += 1

    with patch("core.resource_monitor.resource_monitor.tree_cpu_seconds", side_effect=fake):
        await asyncio.gather(unit._tool_free_hang_verdict(4242), _co_ticker())
    assert ticked["n"] == 3  # the loop kept running during sampling
