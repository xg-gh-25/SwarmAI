"""Tests for SSE disconnect state desync fix.

Verifies that streaming-state endpoint reports streaming=true while
subprocess is still generating after an SSE disconnect, preventing
frontend reconcile from force-clearing active streams.

Key invariant: state reported to frontend must reflect streaming REALITY,
not just the internal unit.state enum.
"""
import os
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


class TestIsGeneratingAfterDisconnect:
    """Test the is_generating_after_disconnect property on SessionUnit."""

    @pytest.fixture
    def unit(self):
        """Create a minimal real-ish SessionUnit for property testing."""
        from core.session_unit import SessionState, SessionUnit

        # Use MagicMock but wire up the real property
        unit = MagicMock(spec=SessionUnit)
        unit.state = SessionState.IDLE
        unit._generating_after_disconnect = False
        unit._pipe_flush_task = None

        # Wire the real property implementation
        type(unit).is_generating_after_disconnect = SessionUnit.is_generating_after_disconnect
        return unit

    def test_false_when_flag_not_set(self, unit):
        """No disconnect recovery = not generating."""
        unit._generating_after_disconnect = False
        assert not unit.is_generating_after_disconnect

    def test_true_when_flag_set_and_pid_alive(self, unit):
        """Flag set + subprocess alive = generating."""
        unit._generating_after_disconnect = True
        unit.pid = os.getpid()  # Use current process (always alive)
        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None  # Signal 0 succeeds
            assert unit.is_generating_after_disconnect

    def test_false_when_flag_set_but_pid_dead(self, unit):
        """Flag set but subprocess dead = not generating (auto-clears)."""
        unit._generating_after_disconnect = True
        unit.pid = 99999999  # Non-existent PID
        with patch("os.kill", side_effect=ProcessLookupError):
            result = unit.is_generating_after_disconnect
        assert result is False
        # Flag auto-cleared
        assert unit._generating_after_disconnect is False

    def test_false_when_flag_set_but_no_pid(self, unit):
        """Flag set but no PID = not generating (auto-clears)."""
        unit._generating_after_disconnect = True
        unit.pid = None
        assert not unit.is_generating_after_disconnect
        assert unit._generating_after_disconnect is False

    def test_false_when_state_not_idle(self, unit):
        """Only reports during IDLE state (post-disconnect transition)."""
        from core.session_unit import SessionState

        unit._generating_after_disconnect = True
        unit.state = SessionState.STREAMING  # Not IDLE
        unit.pid = os.getpid()
        assert not unit.is_generating_after_disconnect

    def test_false_when_state_is_cold(self, unit):
        """COLD state = subprocess not running = not generating."""
        from core.session_unit import SessionState

        unit._generating_after_disconnect = True
        unit.state = SessionState.COLD
        assert not unit.is_generating_after_disconnect


class TestRecoverFromDisconnectSetsFlag:
    """Test that recover_from_disconnect sets _generating_after_disconnect."""

    def test_flag_set_on_successful_recovery(self):
        """recover_from_disconnect sets the flag when transitioning."""
        from core.session_unit import SessionState

        unit = MagicMock()
        unit.state = SessionState.STREAMING
        unit._generating_after_disconnect = False
        unit.last_used = 0

        # Import and call the real method
        from core.session_unit import SessionUnit
        result = SessionUnit.recover_from_disconnect(unit)

        assert result is True
        assert unit._generating_after_disconnect is True

    def test_flag_not_set_when_not_streaming(self):
        """No-op when not in STREAMING state."""
        from core.session_unit import SessionState, SessionUnit

        unit = MagicMock()
        unit.state = SessionState.IDLE
        unit._generating_after_disconnect = False

        result = SessionUnit.recover_from_disconnect(unit)

        assert result is False
        assert unit._generating_after_disconnect is False


class TestStreamingStateEndpoint:
    """Test that /sessions/streaming-state reports correctly during disconnect recovery."""

    @pytest.fixture
    def mock_router(self):
        router = MagicMock()
        return router

    @pytest.mark.asyncio
    async def test_reports_streaming_during_post_disconnect_generation(self, mock_router):
        """Endpoint reports streaming=true when subprocess still generating."""
        from core.session_unit import SessionState

        unit = MagicMock()
        unit.session_id = "session-abc"
        unit.state = SessionState.IDLE  # State says IDLE...
        unit.is_generating_after_disconnect = True  # ...but still generating

        mock_router.list_units.return_value = [unit]

        with patch("routers.chat._get_router", return_value=mock_router):
            from routers.chat import get_streaming_state_endpoint
            result = await get_streaming_state_endpoint()

        sessions = result["sessions"]
        assert "session-abc" in sessions
        assert sessions["session-abc"]["streaming"] is True
        assert sessions["session-abc"]["state"] == "idle"

    @pytest.mark.asyncio
    async def test_reports_idle_after_generation_done(self, mock_router):
        """Endpoint reports streaming=false after subprocess finishes."""
        from core.session_unit import SessionState

        unit = MagicMock()
        unit.session_id = "session-def"
        unit.state = SessionState.IDLE
        unit.is_generating_after_disconnect = False

        mock_router.list_units.return_value = [unit]

        with patch("routers.chat._get_router", return_value=mock_router):
            from routers.chat import get_streaming_state_endpoint
            result = await get_streaming_state_endpoint()

        sessions = result["sessions"]
        assert sessions["session-def"]["streaming"] is False

    @pytest.mark.asyncio
    async def test_reports_streaming_when_state_is_streaming(self, mock_router):
        """Normal streaming (no disconnect) still reports correctly."""
        from core.session_unit import SessionState

        unit = MagicMock()
        unit.session_id = "session-ghi"
        unit.state = SessionState.STREAMING
        unit.is_generating_after_disconnect = False

        mock_router.list_units.return_value = [unit]

        with patch("routers.chat._get_router", return_value=mock_router):
            from routers.chat import get_streaming_state_endpoint
            result = await get_streaming_state_endpoint()

        sessions = result["sessions"]
        assert sessions["session-ghi"]["streaming"] is True

    @pytest.mark.asyncio
    async def test_prewarm_sessions_excluded(self, mock_router):
        """Prewarm sessions are still filtered out."""
        from core.session_unit import SessionState

        unit = MagicMock()
        unit.session_id = "prewarm-123"
        unit.state = SessionState.IDLE
        unit.is_generating_after_disconnect = False

        mock_router.list_units.return_value = [unit]

        with patch("routers.chat._get_router", return_value=mock_router):
            from routers.chat import get_streaming_state_endpoint
            result = await get_streaming_state_endpoint()

        assert "prewarm-123" not in result["sessions"]


class TestFlagClearingPaths:
    """Test that _generating_after_disconnect is cleared in all expected paths."""

    def test_send_clears_flag(self):
        """send() entry clears the flag (user resumed conversation)."""
        from core.session_unit import SessionUnit

        # We can't easily test the full send(), but verify the flag is
        # in the clear-block by checking the source code pattern
        import inspect
        source = inspect.getsource(SessionUnit.send)
        assert "_generating_after_disconnect = False" in source

    def test_cleanup_internal_clears_flag(self):
        """_cleanup_internal clears flag (subprocess died)."""
        from core.session_unit import SessionUnit

        import inspect
        source = inspect.getsource(SessionUnit._cleanup_internal)
        assert "_generating_after_disconnect = False" in source
        # Also clears _pipe_flush_task
        assert "_pipe_flush_task = None" in source
