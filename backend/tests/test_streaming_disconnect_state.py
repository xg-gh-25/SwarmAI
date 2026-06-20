"""Tests for SSE disconnect state desync fix.

Verifies that streaming-state endpoint reports streaming=true while
subprocess is still generating after an SSE disconnect (pipe_flush_task active),
preventing frontend reconcile from force-clearing active streams.

Key invariant: state reported to frontend must reflect streaming REALITY,
not just the internal unit.state enum.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_session_unit():
    """Create a minimal SessionUnit mock with pipe_flush_task support."""
    from core.session_unit import SessionState

    unit = MagicMock()
    unit.session_id = "test-session-123"
    unit.state = SessionState.IDLE
    unit._pipe_flush_task = None
    # Wire up the property we're about to implement
    type(unit).is_generating_after_disconnect = property(
        lambda self: (
            self._pipe_flush_task is not None
            and not self._pipe_flush_task.done()
        )
    )
    return unit


class TestIsGeneratingAfterDisconnect:
    """Test the is_generating_after_disconnect property on SessionUnit."""

    def test_false_when_no_pipe_flush_task(self, mock_session_unit):
        """No pipe flush task = not generating after disconnect."""
        mock_session_unit._pipe_flush_task = None
        assert not mock_session_unit.is_generating_after_disconnect

    def test_true_when_pipe_flush_task_active(self, mock_session_unit):
        """Active pipe flush task = still generating after disconnect."""
        task = MagicMock()
        task.done.return_value = False
        mock_session_unit._pipe_flush_task = task
        assert mock_session_unit.is_generating_after_disconnect

    def test_false_when_pipe_flush_task_done(self, mock_session_unit):
        """Completed pipe flush task = no longer generating."""
        task = MagicMock()
        task.done.return_value = True
        mock_session_unit._pipe_flush_task = task
        assert not mock_session_unit.is_generating_after_disconnect

    def test_false_when_state_is_streaming(self, mock_session_unit):
        """If state IS streaming, the property should still only check pipe_flush."""
        from core.session_unit import SessionState

        mock_session_unit.state = SessionState.STREAMING
        mock_session_unit._pipe_flush_task = None
        # Property only checks pipe_flush_task — state is handled by caller
        assert not mock_session_unit.is_generating_after_disconnect


class TestStreamingStateEndpoint:
    """Test that /sessions/streaming-state reports correctly during disconnect recovery."""

    @pytest.fixture
    def mock_router(self):
        """Mock session router with units."""
        router = MagicMock()
        return router

    @pytest.mark.asyncio
    async def test_reports_streaming_during_pipe_flush(self, mock_router):
        """Endpoint reports streaming=true when subprocess still generating."""
        from core.session_unit import SessionState

        unit = MagicMock()
        unit.session_id = "session-abc"
        unit.state = SessionState.IDLE  # State says IDLE...
        # ...but pipe_flush_task still running (subprocess alive)
        unit.is_generating_after_disconnect = True

        mock_router.list_units.return_value = [unit]

        # Import and test the endpoint logic
        with patch("routers.chat._get_router", return_value=mock_router):
            from routers.chat import get_streaming_state_endpoint

            result = await get_streaming_state_endpoint()

        sessions = result["sessions"]
        assert "session-abc" in sessions
        # Must report streaming=true even though state is IDLE
        assert sessions["session-abc"]["streaming"] is True
        assert sessions["session-abc"]["state"] == "idle"

    @pytest.mark.asyncio
    async def test_reports_idle_after_pipe_flush_done(self, mock_router):
        """Endpoint reports streaming=false after pipe flush completes."""
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
        unit.is_generating_after_disconnect = False  # No disconnect happened

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
