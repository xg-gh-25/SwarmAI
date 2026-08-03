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
from unittest.mock import MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit

# Suppress "Task was destroyed" warnings from xdist worker teardown
pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


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
        # _transition to STREAMING auto-starts watchdog
        unit.state = SessionState.IDLE
        unit._transition(SessionState.STREAMING)
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

        # os.kill(pid, 0) succeeds = process alive (no exception)
        with patch("os.kill", return_value=None):
            await asyncio.sleep(0.2)

        # Should still be STREAMING
        assert unit.state == SessionState.STREAMING

    @pytest.mark.asyncio
    async def test_watchdog_handles_permission_error_as_alive(self, unit):
        """PermissionError from os.kill means process exists but not ours."""
        unit.state = SessionState.IDLE
        unit._transition(SessionState.STREAMING)

        with patch("os.kill", side_effect=PermissionError("Operation not permitted")):
            await asyncio.sleep(0.2)

        # PermissionError = process exists, just can't signal it
        assert unit.state == SessionState.STREAMING


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


    @pytest.mark.asyncio
    async def test_watchdog_persists_through_waiting_input(self, unit):
        """Watchdog continues running during STREAMING → WAITING_INPUT."""
        unit.state = SessionState.IDLE
        unit._transition(SessionState.STREAMING)
        original_task = unit._pid_watchdog_task
        assert original_task is not None

        # Transition to WAITING_INPUT (permission prompt)
        unit._transition(SessionState.WAITING_INPUT)

        # Same watchdog task should still be running
        assert unit._pid_watchdog_task is original_task
        assert not original_task.done()

    @pytest.mark.asyncio
    async def test_watchdog_detects_death_in_waiting_input(self, unit):
        """Watchdog detects PID death even in WAITING_INPUT state."""
        unit.state = SessionState.IDLE
        unit._transition(SessionState.STREAMING)
        unit._transition(SessionState.WAITING_INPUT)
        assert unit._pid_watchdog_task is not None

        with patch("os.kill", side_effect=ProcessLookupError("No such process")):
            await asyncio.sleep(0.2)

        assert unit.state == SessionState.DEAD


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
