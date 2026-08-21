"""Regression: the lifecycle_manager 60s _check_streaming_timeout loop is the
THIRD tool-free kill path (run_dcd668a6 E2E finding). It must consult the same
CPU-liveness verdict before force_unstick_streaming — else it re-opens the
split-brain: a CPU-busy slow turn the orchestrator + watchdog spare would still
be force-unstuck here on pure wall-clock stall.

- A CPU-busy ('working') tool-free stall → NOT force-unstuck.
- A genuinely wedged ('wedged') stall → force-unstuck as before.
- 'unknown' (unmeasurable) → force-unstuck (last-resort, don't spare).
- state left STREAMING during the verdict await → NOT force-unstuck.
- stall past the hard ceiling → force-unstuck regardless of CPU.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.lifecycle_manager import LifecycleManager
from core.session_unit import SessionState


def _streaming_unit(verdict: str, stall: float = 400.0, ceiling: float = 1800.0):
    unit = MagicMock()
    unit.session_id = "lc-tf"
    unit.state = SessionState.STREAMING
    unit.streaming_stall_seconds = stall
    unit._open_tool_uses = {}          # tool-FREE
    unit._streaming_start_time = time.time() - stall
    unit._last_event_time = time.time() - stall  # no event since spawn-ish; adaptive path
    unit._sdk_session_id = None
    unit._consecutive_unstick_timeouts = 0
    unit._UNSTICK_CIRCUIT_BREAKER_THRESHOLD = 2
    unit.pid = 555
    unit.TOOL_FREE_HARD_CEILING_S = ceiling
    unit._compute_message_timeout = lambda: 300.0
    unit._tool_free_hang_verdict = AsyncMock(return_value=verdict)
    unit.force_unstick_streaming = AsyncMock()
    return unit


def _mgr(unit):
    router = MagicMock()
    router.list_units.return_value = [unit]
    return LifecycleManager(router=router)


@pytest.mark.asyncio
async def test_lifecycle_spares_cpu_busy_stall():
    """'working' verdict → the 60s loop does NOT force-unstick (split-brain fix)."""
    unit = _streaming_unit(verdict="working")
    await _mgr(unit)._check_streaming_timeout()
    unit._tool_free_hang_verdict.assert_awaited()
    unit.force_unstick_streaming.assert_not_called()


@pytest.mark.asyncio
async def test_lifecycle_kills_wedged_stall():
    """'wedged' verdict → force-unstick still fires (true hang recovered)."""
    unit = _streaming_unit(verdict="wedged")
    await _mgr(unit)._check_streaming_timeout()
    unit.force_unstick_streaming.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifecycle_kills_on_unknown_verdict():
    """'unknown' (unmeasurable) → force-unstick (last resort, don't spare)."""
    unit = _streaming_unit(verdict="unknown")
    await _mgr(unit)._check_streaming_timeout()
    unit.force_unstick_streaming.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifecycle_skips_if_state_left_streaming_during_verdict():
    """State → WAITING_INPUT during the verdict await → do NOT force-unstick."""
    unit = _streaming_unit(verdict="wedged")

    async def _verdict_flips(_pid):
        unit.state = SessionState.WAITING_INPUT
        return "wedged"

    unit._tool_free_hang_verdict = _verdict_flips
    await _mgr(unit)._check_streaming_timeout()
    unit.force_unstick_streaming.assert_not_called()


@pytest.mark.asyncio
async def test_lifecycle_kills_past_hard_ceiling_regardless_of_cpu():
    """Stall past TOOL_FREE_HARD_CEILING_S → force-unstick even if CPU-busy
    (busy-loop bound); the verdict is not even consulted."""
    unit = _streaming_unit(verdict="working", stall=2000.0, ceiling=1800.0)
    await _mgr(unit)._check_streaming_timeout()
    unit._tool_free_hang_verdict.assert_not_awaited()
    unit.force_unstick_streaming.assert_awaited_once()
