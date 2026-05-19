"""Tests for PID Watchdog — out-of-band subprocess death detection.

Verifies that SessionUnit detects subprocess death (OOM kill, jetsam)
independently of the pipe/stream, and auto-recovers by transitioning
to DEAD state within the polling interval.

Key properties:
- Watchdog starts when entering STREAMING state
- Watchdog stops when leaving STREAMING/WAITING_INPUT
- Subprocess death (PID gone) triggers DEAD transition
- Channel sessions get recovery notification callback
- Watchdog does NOT fire in IDLE/COLD/DEAD states
"""
from __future__ import annotations

import asyncio
import os
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit


@pytest.fixture
def unit():
    """Create a minimal SessionUnit for testing."""
    u = SessionUnit(session_id="test-watchdog-001", agent_id="default")
    u._wrapper = MagicMock()
    u._wrapper.pid = 99999  # Fake PID
    u._client = MagicMock()
    # Use short interval for tests (default is 5s)
    u._PID_WATCHDOG_INTERVAL = 0.05
    return u


class TestPidWatchdogDetection:
    """AC1: PID watchdog detects subprocess death within 10s."""

    @pytest.mark.asyncio
    async def test_watchdog_detects_dead_pid(self, unit):
        """When subprocess PID is gone, watchdog transitions to DEAD."""
        # Put unit in STREAMING state
        unit.state = SessionState.IDLE
        unit._transition(SessionState.STREAMING)

        # Start watchdog
        unit._start_pid_watchdog()
        assert unit._pid_watchdog_task is not None

        # Simulate PID death: os.kill raises ProcessLookupError
        with patch("os.kill", side_effect=ProcessLookupError("No such process")):
            # Wait for one poll cycle + margin
            await asyncio.sleep(0.2)  # Tests use shorter interval

        # Should have transitioned to DEAD
        assert unit.state == SessionState.DEAD

    @pytest.mark.asyncio
    async def test_watchdog_does_not_fire_for_alive_pid(self, unit):
        """When PID is alive, watchdog does nothing."""
        unit.state = SessionState.IDLE
        unit._transition(SessionState.STREAMING)
        unit._start_pid_watchdog()

        # os.kill(pid, 0) succeeds = process alive (no exception)
        with patch("os.kill", return_value=None):
            await asyncio.sleep(0.2)

        # Should still be STREAMING
        assert unit.state == SessionState.STREAMING

        # Cleanup
        unit._stop_pid_watchdog()

    @pytest.mark.asyncio
    async def test_watchdog_handles_permission_error_as_alive(self, unit):
        """PermissionError from os.kill means process exists but not ours."""
        unit.state = SessionState.IDLE
        unit._transition(SessionState.STREAMING)
        unit._start_pid_watchdog()

        with patch("os.kill", side_effect=PermissionError("Operation not permitted")):
            await asyncio.sleep(0.2)

        # PermissionError = process exists, just can't signal it
        assert unit.state == SessionState.STREAMING
        unit._stop_pid_watchdog()


class TestPidWatchdogLifecycle:
    """AC5: Watchdog cancelled on state transition."""

    @pytest.mark.asyncio
    async def test_watchdog_starts_on_streaming(self, unit):
        """Watchdog task is created when entering STREAMING."""
        unit.state = SessionState.IDLE
        unit._transition(SessionState.STREAMING)

        # _transition should have started watchdog
        assert unit._pid_watchdog_task is not None
        assert not unit._pid_watchdog_task.done()
        unit._stop_pid_watchdog()

    @pytest.mark.asyncio
    async def test_watchdog_stops_on_idle(self, unit):
        """Watchdog is cancelled when transitioning to IDLE."""
        unit.state = SessionState.IDLE
        unit._transition(SessionState.STREAMING)
        assert unit._pid_watchdog_task is not None

        # Transition to IDLE (response complete)
        unit._transition(SessionState.IDLE)

        # Watchdog should be cancelled
        assert unit._pid_watchdog_task is None or unit._pid_watchdog_task.cancelled()

    @pytest.mark.asyncio
    async def test_watchdog_not_started_without_pid(self, unit):
        """No watchdog if PID not available."""
        unit._wrapper = None  # No wrapper = no PID
        unit.state = SessionState.IDLE
        unit._transition(SessionState.STREAMING)

        assert unit._pid_watchdog_task is None

    @pytest.mark.asyncio
    async def test_watchdog_stops_on_dead(self, unit):
        """Watchdog cancelled when entering DEAD state."""
        unit.state = SessionState.IDLE
        unit._transition(SessionState.STREAMING)
        assert unit._pid_watchdog_task is not None

        unit._transition(SessionState.DEAD)
        assert unit._pid_watchdog_task is None or unit._pid_watchdog_task.cancelled()


class TestChannelEvictionProtection:
    """AC3: Channel sessions protected from eviction."""

    def test_channel_session_flag_exists(self, unit):
        """SessionUnit has is_channel_session attribute."""
        assert hasattr(unit, "is_channel_session")
        assert unit.is_channel_session is False

    def test_channel_session_marked(self, unit):
        """Channel sessions can be marked."""
        unit.is_channel_session = True
        assert unit.is_channel_session is True
