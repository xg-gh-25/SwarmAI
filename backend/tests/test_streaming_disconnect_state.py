"""Tests for the streaming-state mirror (GET /sessions/streaming-state).

A client disconnect does not touch the turn (core/session_events): the unit
keeps its state, and the mirror reports streaming = (state == STREAMING), the
waiting-input request and the drain hint. The exact key set is bound by
test_streaming_state_contract.py.
"""
from unittest.mock import MagicMock, patch

import pytest


class TestStreamingStateEndpoint:
    """Test that /sessions/streaming-state reports each unit's live state."""

    @pytest.fixture
    def mock_router(self):
        router = MagicMock()
        return router

    @pytest.mark.asyncio
    async def test_post_disconnect_idle_reports_not_streaming(self, mock_router):
        """An IDLE unit reports streaming=false and state 'idle' (there is no
        post-disconnect special case: a disconnect does not change the state)."""
        from core.session_unit import SessionState

        unit = MagicMock()
        unit.session_id = "session-abc"
        unit.state = SessionState.IDLE  # clean IDLE after disconnect
        unit._pending_question = None
        unit._last_drained_seqs = []

        mock_router.list_units.return_value = [unit]

        with patch("routers.chat._get_router", return_value=mock_router):
            from routers.chat import get_streaming_state_endpoint
            result = await get_streaming_state_endpoint()

        sessions = result["sessions"]
        assert "session-abc" in sessions
        assert sessions["session-abc"]["streaming"] is False
        assert sessions["session-abc"]["state"] == "idle"

    @pytest.mark.asyncio
    async def test_reports_idle_after_generation_done(self, mock_router):
        """Endpoint reports streaming=false for an idle session."""
        from core.session_unit import SessionState

        unit = MagicMock()
        unit.session_id = "session-def"
        unit.state = SessionState.IDLE
        unit._pending_question = None
        unit._last_drained_seqs = []

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

    @pytest.mark.asyncio
    async def test_read_api_surfaces_waiting_input_and_pending_question(self, mock_router):
        """Root-1 SSOT Phase 2 (L5/AC6): a WAITING_INPUT session exposes
        waiting_input=True + the pending_question payload (F5 — re-renderable even
        if the ask_user_question SSE event was lost)."""
        from core.session_unit import SessionState

        unit = MagicMock()
        unit.session_id = "session-wi"
        unit.state = SessionState.WAITING_INPUT
        unit.is_generating_after_disconnect = False
        unit._pending_question = {"tool_use_id": "t1", "questions": [{"question": "Pick?"}]}
        unit._last_drained_seqs = []

        mock_router.list_units.return_value = [unit]

        with patch("routers.chat._get_router", return_value=mock_router):
            from routers.chat import get_streaming_state_endpoint
            result = await get_streaming_state_endpoint()

        entry = result["sessions"]["session-wi"]
        assert entry["waiting_input"] is True
        assert entry["state"] == "waiting_input"
        assert entry["pending_question"]["tool_use_id"] == "t1"
        assert "pending_count" in entry  # field present even when 0

    @pytest.mark.asyncio
    async def test_read_api_hides_pending_question_when_idle(self, mock_router):
        """pending_question is None unless the session is WAITING_INPUT (no leak of
        a stale question into an idle session's mirror)."""
        from core.session_unit import SessionState

        unit = MagicMock()
        unit.session_id = "session-idle"
        unit.state = SessionState.IDLE
        unit._pending_question = {"tool_use_id": "stale", "questions": []}
        unit._last_drained_seqs = [3, 4]

        mock_router.list_units.return_value = [unit]

        with patch("routers.chat._get_router", return_value=mock_router):
            from routers.chat import get_streaming_state_endpoint
            result = await get_streaming_state_endpoint()

        entry = result["sessions"]["session-idle"]
        assert entry["waiting_input"] is False
        assert entry["pending_question"] is None  # not surfaced when idle
        assert entry["last_drained_seqs"] == [3, 4]  # drain hint surfaced


class TestGeneratingFlagFullyDeleted:
    """Option B (L6): the _generating_after_disconnect flag + property are GONE.
    This test enforces AC7 at the source level so the flag can never silently
    reappear."""

    def test_flag_absent_from_session_unit_source(self):
        """No functional use of the deleted flag/property in SessionUnit — AC7.

        We check for the assignment/access patterns (`self._generating_after_disconnect`
        and `.is_generating_after_disconnect`) rather than the bare token, so a
        comment that names the old flag does not fail the test."""
        import inspect
        from core.session_unit import SessionUnit
        import core.session_unit as su_mod

        source = inspect.getsource(su_mod)
        assert "self._generating_after_disconnect" not in source, \
            "the Option-B-deleted flag is still assigned/read in functional code"
        assert ".is_generating_after_disconnect" not in source, \
            "the Option-B-deleted property is still accessed in functional code"
        assert not hasattr(SessionUnit, "is_generating_after_disconnect"), \
            "the Option-B-deleted property reappeared"

