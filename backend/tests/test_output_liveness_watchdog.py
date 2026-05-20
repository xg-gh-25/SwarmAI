"""Tests for the output liveness watchdog in SessionUnit._pid_watchdog_loop.

The output liveness watchdog kills a subprocess that is alive but has not
produced any SDK events for longer than MESSAGE_TIMEOUT. This handles the
case where the Anthropic API hangs but the subprocess remains running —
asyncio.wait_for cannot cancel native pipe I/O reads, so the PID watchdog
is the only out-of-band mechanism to break the hang.

Testing methodology: unit tests with mocked time and state.
Key properties verified:
- AC1: Subprocess killed after MESSAGE_TIMEOUT of silence during STREAMING
- AC2: No false kills during IDLE, WAITING_INPUT, or when _last_event_time is None
- AC3: Kill triggers DEAD transition (existing retry path handles recovery)
"""
from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_unit(session_id: str = "test-session", pid: int | None = None) -> SessionUnit:
    """Create a minimal SessionUnit for testing."""
    unit = SessionUnit.__new__(SessionUnit)
    unit.session_id = session_id
    unit.state = SessionState.COLD
    unit._sdk_session_id = None
    unit._client = None
    # Use a mock wrapper so the pid property returns what we want
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
    unit._PID_WATCHDOG_INTERVAL = 0.05  # Fast for tests
    unit.last_used = time.time()
    unit.is_channel_session = False
    unit._retry_count = 0
    unit._max_retries = 3
    unit._on_state_change = None
    unit._stop_event = asyncio.Event()
    # Mock _force_kill to avoid real process operations
    unit._force_kill = AsyncMock()
    return unit


# ── AC1: Kill after MESSAGE_TIMEOUT silence ─────────────────────────────


class TestOutputLivenessKill:
    """Verify subprocess is killed when silent too long during STREAMING."""

    @pytest.mark.asyncio
    async def test_kills_subprocess_after_timeout(self):
        """AC1: When STREAMING and _last_event_time exceeds timeout, kill."""
        unit = _make_unit(pid=12345)
        unit.state = SessionState.STREAMING
        # Last event was 400 seconds ago (> 300s default timeout)
        unit._last_event_time = time.time() - 400

        with patch("os.kill") as mock_kill:
            # os.kill(pid, 0) = process alive
            mock_kill.return_value = None

            # Run the watchdog loop — it should detect silence and kill
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)  # Let at least 2 poll cycles run
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Should have transitioned to DEAD
        assert unit.state == SessionState.DEAD
        # Should have called _force_kill
        unit._force_kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_adaptive_timeout(self):
        """Timeout scales with context size via _compute_message_timeout."""
        unit = _make_unit(pid=12345)
        unit.state = SessionState.STREAMING
        # Set large context — timeout should be > 300s
        unit._last_known_context_tokens = 600_000  # 600K / 3000 = 200s, max(300, 200) = 300s
        # Last event only 100s ago — should NOT kill
        unit._last_event_time = time.time() - 100

        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None

            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Should NOT have killed — 100s < 300s timeout
        assert unit.state == SessionState.STREAMING
        unit._force_kill.assert_not_called()


# ── AC2: No false kills ─────────────────────────────────────────────────


class TestNoFalseKills:
    """Verify the watchdog does NOT kill in non-applicable states."""

    @pytest.mark.asyncio
    async def test_no_kill_when_idle(self):
        """IDLE state: watchdog should not check output liveness."""
        unit = _make_unit(pid=12345)
        unit.state = SessionState.IDLE
        unit._last_event_time = time.time() - 9999  # Very stale

        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None

            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert unit.state == SessionState.IDLE
        unit._force_kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_kill_when_waiting_input(self):
        """WAITING_INPUT: user is deciding, no timeout should apply."""
        unit = _make_unit(pid=12345)
        unit.state = SessionState.WAITING_INPUT
        unit._last_event_time = time.time() - 9999

        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None

            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert unit.state == SessionState.WAITING_INPUT
        unit._force_kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_kill_when_last_event_time_none(self):
        """Before first event received, don't apply output liveness."""
        unit = _make_unit(pid=12345)
        unit.state = SessionState.STREAMING
        unit._last_event_time = None  # No events yet (init phase)

        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None

            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert unit.state == SessionState.STREAMING
        unit._force_kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_kill_when_recent_event(self):
        """Recent event: process is working normally, don't kill."""
        unit = _make_unit(pid=12345)
        unit.state = SessionState.STREAMING
        unit._last_event_time = time.time() - 10  # 10s ago = fine

        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None

            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert unit.state == SessionState.STREAMING
        unit._force_kill.assert_not_called()


# ── AC3: DEAD transition triggers recovery path ─────────────────────────


class TestRecoveryPath:
    """Verify the kill triggers correct state for retry."""

    @pytest.mark.asyncio
    async def test_transitions_to_dead_before_kill(self):
        """State should be DEAD when _force_kill is called."""
        unit = _make_unit(pid=12345)
        unit.state = SessionState.STREAMING
        unit._last_event_time = time.time() - 400

        states_during_kill = []

        async def capture_state_on_kill():
            states_during_kill.append(unit.state)

        unit._force_kill = AsyncMock(side_effect=capture_state_on_kill)

        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None

            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # State was DEAD when _force_kill was called
        assert states_during_kill == [SessionState.DEAD]

    @pytest.mark.asyncio
    async def test_existing_pid_death_detection_still_works(self):
        """Original PID watchdog behavior: process gone → DEAD."""
        unit = _make_unit(pid=99999)
        unit.state = SessionState.STREAMING
        unit._last_event_time = time.time()  # Recent event

        with patch("os.kill") as mock_kill:
            # Process doesn't exist
            mock_kill.side_effect = ProcessLookupError()

            task = asyncio.create_task(unit._pid_watchdog_loop(99999))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Should transition to DEAD via original path
        assert unit.state == SessionState.DEAD
        # _force_kill NOT called — process already dead
        unit._force_kill.assert_not_called()
