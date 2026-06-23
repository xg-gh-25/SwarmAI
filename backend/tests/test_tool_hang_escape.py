"""Tests for the tool-aware liveness tier in SessionUnit._pid_watchdog_loop.

Problem: a STREAMING session can sit 30+ min on a stuck/silent TOOL execution
with the only escape being the user's "send a message to recover". The existing
output-liveness watchdog only fires at _compute_message_timeout() (up to 1800s)
and does a destructive _force_kill(). hang_detected is suppressed in STREAMING
by design (protects genuine long thinking).

This adds a tool-aware tier that distinguishes "stuck tool" (model emitted a
tool_use, then went silent — _open_tool_uses non-empty) from "model thinking"
(thinking_delta events keep _last_event_time fresh) or "API hang" (silence but
no open tool_use). For a stuck tool past TOOL_HANG_HARD_S it calls interrupt()
(warm — subprocess preserved) instead of _force_kill().

Testing methodology: unit tests with mocked time, state, and SDK interrupt.
Key properties:
- AC2: open tool_use + silence > HARD → interrupt() (warm), NOT _force_kill()
- AC3: events flowing (fresh _last_event_time) OR no open tool_use → NEVER interrupt
- AC4: repeated hard-tier escapes feed the circuit breaker
- AC5: tracking gates on the open-tool-use execution signal
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_unit(session_id: str = "test-session", pid: int | None = 12345) -> SessionUnit:
    """Minimal SessionUnit with the tool-hang fields wired."""
    unit = SessionUnit.__new__(SessionUnit)
    unit.session_id = session_id
    unit.state = SessionState.COLD
    unit._sdk_session_id = None
    unit._client = None
    if pid is not None:
        wrapper = MagicMock()
        wrapper.pid = pid
        unit._wrapper = wrapper
    else:
        unit._wrapper = None
    unit._hooks_enqueued = False
    unit._streaming_start_time = None
    unit._last_event_time = None
    unit._peak_tree_rss_bytes = 0
    unit._last_proactive_restart = 0
    unit._pid_watchdog_task = None
    unit._last_known_context_tokens = 0
    unit._PID_WATCHDOG_INTERVAL = 0.05  # fast for tests
    unit.last_used = time.time()
    unit.is_channel_session = False
    unit._retry_count = 0
    unit._max_retries = 3
    unit._on_state_change = None
    unit._stop_event = asyncio.Event()
    # New fields under test
    unit._open_tool_uses = {}
    unit._tool_hang_interrupted = False
    unit._tool_hang_interrupt_at = None
    unit._consecutive_unstick_timeouts = 0
    # Mocks: interrupt is warm (no kill), force_kill is the destructive backstop
    unit._force_kill = AsyncMock()
    unit.interrupt = AsyncMock()
    return unit


# ── AC2: hard-tier interrupt (tracer bullet) ────────────────────────────


class TestToolHangHardTier:
    @pytest.mark.asyncio
    async def test_stuck_tool_interrupts_not_kills(self):
        """AC2: open tool older than HARD + silence → interrupt(), NOT force_kill."""
        unit = _make_unit()
        unit.state = SessionState.STREAMING
        # A tool was emitted HARD+ seconds ago and never returned a result.
        stuck_age = SessionUnit.TOOL_HANG_HARD_S + 60
        unit._open_tool_uses = {"toolu_stuck": time.time() - stuck_age}
        # No SDK events since the tool was emitted → silence == tool age.
        unit._last_event_time = time.time() - stuck_age

        with patch("os.kill", return_value=None):
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        unit.interrupt.assert_awaited()           # warm escape fired
        unit._force_kill.assert_not_called()      # NOT the destructive path
        assert unit._tool_hang_interrupted is True

    @pytest.mark.asyncio
    async def test_interrupt_fires_once_per_episode(self):
        """The interrupt-once guard prevents re-interrupting every tick."""
        unit = _make_unit()
        unit.state = SessionState.STREAMING
        stuck_age = SessionUnit.TOOL_HANG_HARD_S + 60
        unit._open_tool_uses = {"toolu_stuck": time.time() - stuck_age}
        unit._last_event_time = time.time() - stuck_age

        with patch("os.kill", return_value=None):
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.25)  # several ticks
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Exactly one interrupt despite multiple poll cycles.
        assert unit.interrupt.await_count == 1

    @pytest.mark.asyncio
    async def test_hard_tier_increments_circuit_breaker(self):
        """AC4: each hard-tier escape feeds _consecutive_unstick_timeouts."""
        unit = _make_unit()
        unit.state = SessionState.STREAMING
        stuck_age = SessionUnit.TOOL_HANG_HARD_S + 60
        unit._open_tool_uses = {"toolu_stuck": time.time() - stuck_age}
        unit._last_event_time = time.time() - stuck_age

        with patch("os.kill", return_value=None):
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert unit._consecutive_unstick_timeouts == 1


# ── AC3: never interrupt legitimate work ────────────────────────────────


class TestNoFalseInterrupt:
    @pytest.mark.asyncio
    async def test_no_interrupt_when_events_flowing(self):
        """AC3: fresh _last_event_time (thinking deltas) → never interrupt."""
        unit = _make_unit()
        unit.state = SessionState.STREAMING
        # An open tool exists, but events are flowing (recent) → model working.
        unit._open_tool_uses = {"toolu_x": time.time() - 5}
        unit._last_event_time = time.time() - 2  # very recent

        with patch("os.kill", return_value=None):
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        unit.interrupt.assert_not_called()
        unit._force_kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_interrupt_when_no_open_tool(self):
        """AC3: silence but NO open tool_use → tool-tier does not fire.

        This is the genuine-thinking / API-hang case; the existing
        _compute_message_timeout backstop owns it, not the tool tier.
        """
        unit = _make_unit()
        unit.state = SessionState.STREAMING
        unit._open_tool_uses = {}  # nothing executing
        # Silent past HARD but below the force-kill backstop (300s base).
        unit._last_event_time = time.time() - (SessionUnit.TOOL_HANG_HARD_S + 30)

        with patch("os.kill", return_value=None):
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        unit.interrupt.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_interrupt_when_tool_young(self):
        """A recently-emitted tool (< HARD) is not stuck — no interrupt."""
        unit = _make_unit()
        unit.state = SessionState.STREAMING
        unit._open_tool_uses = {"toolu_y": time.time() - 30}  # young
        unit._last_event_time = time.time() - 30

        with patch("os.kill", return_value=None):
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        unit.interrupt.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_interrupt_when_not_streaming(self):
        """IDLE/WAITING_INPUT never run the tool tier even with a stale tool."""
        unit = _make_unit()
        unit.state = SessionState.WAITING_INPUT
        stuck_age = SessionUnit.TOOL_HANG_HARD_S + 60
        unit._open_tool_uses = {"toolu_stuck": time.time() - stuck_age}
        unit._last_event_time = time.time() - stuck_age

        with patch("os.kill", return_value=None):
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        unit.interrupt.assert_not_called()


# ── AC5: open-tool-use tracking helper ──────────────────────────────────


class TestOldestOpenToolAge:
    def test_none_when_empty(self):
        unit = _make_unit()
        assert unit._oldest_open_tool_age() is None

    def test_returns_oldest(self):
        unit = _make_unit()
        now = time.time()
        unit._open_tool_uses = {"a": now - 10, "b": now - 100, "c": now - 50}
        age = unit._oldest_open_tool_age()
        assert age is not None
        assert 95 < age < 105  # oldest is "b" at ~100s


# ── force-kill backstop still works for true API hang ───────────────────


class TestForceKillBackstopUnchanged:
    @pytest.mark.asyncio
    async def test_api_hang_no_open_tool_still_force_kills(self):
        """No open tool + silence past _compute_message_timeout → force_kill.

        The existing destructive backstop must remain intact for genuine
        API hangs (no tool executing).
        """
        unit = _make_unit()
        unit.state = SessionState.STREAMING
        unit._open_tool_uses = {}
        unit._last_known_context_tokens = 0  # base timeout 300s
        unit._last_event_time = time.time() - 400  # > 300s backstop

        with patch("os.kill", return_value=None):
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        unit._force_kill.assert_called_once()
        unit.interrupt.assert_not_called()
        assert unit.state == SessionState.DEAD
