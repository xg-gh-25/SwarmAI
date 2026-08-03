"""Tests for SSE disconnect state desync fix.

Verifies that streaming-state endpoint reports streaming=true while
subprocess is still generating after an SSE disconnect, preventing
frontend reconcile from force-clearing active streams.

Key invariant: state reported to frontend must reflect streaming REALITY,
not just the internal unit.state enum.
"""
from unittest.mock import MagicMock, patch

import pytest


class TestIsPostDisconnectFlushing:
    """Root-1 SSOT Phase 2 (L6, Option B): the is_generating_after_disconnect
    flag+property are DELETED. The eviction guard now derives from the live
    pipe-flush task via is_post_disconnect_flushing — no manually-managed flag."""

    @pytest.fixture
    def unit(self):
        from core.session_unit import SessionState, SessionUnit

        unit = MagicMock(spec=SessionUnit)
        unit.state = SessionState.IDLE
        unit._pipe_flush_task = None
        type(unit).is_post_disconnect_flushing = SessionUnit.is_post_disconnect_flushing
        return unit

    def test_false_when_no_flush_task(self, unit):
        """No pipe-flush task = not flushing = evictable."""
        unit._pipe_flush_task = None
        assert not unit.is_post_disconnect_flushing

    def test_true_when_flush_task_running(self, unit):
        """A live (not done) pipe-flush task = subprocess finishing post-disconnect."""
        task = MagicMock()
        task.done.return_value = False
        unit._pipe_flush_task = task
        assert unit.is_post_disconnect_flushing

    def test_false_when_flush_task_done(self, unit):
        """A completed flush task = no longer flushing."""
        task = MagicMock()
        task.done.return_value = True
        unit._pipe_flush_task = task
        assert not unit.is_post_disconnect_flushing


class TestRecoverFromDisconnectCleanIdle:
    """Option B-soft: recover_from_disconnect transitions to a CLEAN IDLE (no
    generating-limbo flag). The subprocess is left alive by the separate
    flush_subprocess_pipe task (1A — long turns survive a transient SSE blip)."""

    def test_transitions_to_clean_idle_on_recovery(self):
        """recover_from_disconnect transitions STREAMING→IDLE and returns True,
        WITHOUT setting any generating flag (the flag no longer exists)."""
        from core.session_unit import SessionState, SessionUnit

        unit = MagicMock()
        unit.state = SessionState.STREAMING
        unit.last_used = 0

        result = SessionUnit.recover_from_disconnect(unit)

        assert result is True
        unit._transition.assert_called_once_with(SessionState.IDLE)
        # The deleted flag must NOT be referenced.
        assert not hasattr(unit, "_generating_after_disconnect") or \
            unit._generating_after_disconnect is not True

    def test_noop_when_not_streaming(self):
        """No-op when not in STREAMING state."""
        from core.session_unit import SessionState, SessionUnit

        unit = MagicMock()
        unit.state = SessionState.IDLE

        result = SessionUnit.recover_from_disconnect(unit)

        assert result is False
        unit._transition.assert_not_called()


class TestStreamingStateEndpoint:
    """Test that /sessions/streaming-state reports correctly during disconnect recovery."""

    @pytest.fixture
    def mock_router(self):
        router = MagicMock()
        return router

    @pytest.mark.asyncio
    async def test_post_disconnect_idle_reports_not_streaming(self, mock_router):
        """Root-1 SSOT Phase 2 (L6, Option B): a disconnect now yields a CLEAN
        IDLE — there is no generating-after-disconnect special case, so the mirror
        reports streaming=false. The subprocess may still be finishing a long turn
        (left alive, 1A); its content loads from DB on the next reconcile."""
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
    async def test_reports_post_disconnect_flushing_true_when_flush_task_live(self, mock_router):
        """Honest-signal fix (OT01): a CLEAN-IDLE unit whose subprocess is still
        finishing a long turn post-disconnect (live _pipe_flush_task) must expose
        post_disconnect_flushing=True so the frontend reconcile keeps waiting
        instead of surfacing a false 'Connection lost' error."""
        from core.session_unit import SessionState, SessionUnit

        unit = MagicMock()
        unit.session_id = "session-flush"
        unit.state = SessionState.IDLE  # clean IDLE post-disconnect
        unit._pending_question = None
        unit._last_drained_seqs = []
        # Live (not-done) pipe-flush task → still flushing.
        task = MagicMock()
        task.done.return_value = False
        unit._pipe_flush_task = task
        type(unit).is_post_disconnect_flushing = SessionUnit.is_post_disconnect_flushing

        mock_router.list_units.return_value = [unit]

        with patch("routers.chat._get_router", return_value=mock_router):
            from routers.chat import get_streaming_state_endpoint
            result = await get_streaming_state_endpoint()

        entry = result["sessions"]["session-flush"]
        assert entry["streaming"] is False        # clean IDLE — not streaming
        assert entry["post_disconnect_flushing"] is True   # but subprocess alive

    @pytest.mark.asyncio
    async def test_reports_post_disconnect_flushing_false_when_no_task(self, mock_router):
        """A genuinely-done IDLE session reports post_disconnect_flushing=False so
        the reconcile loop can resolve (surface error / clear) when appropriate."""
        from core.session_unit import SessionState, SessionUnit

        unit = MagicMock()
        unit.session_id = "session-done"
        unit.state = SessionState.IDLE
        unit._pending_question = None
        unit._last_drained_seqs = []
        unit._pipe_flush_task = None  # no flush task → done
        type(unit).is_post_disconnect_flushing = SessionUnit.is_post_disconnect_flushing

        mock_router.list_units.return_value = [unit]

        with patch("routers.chat._get_router", return_value=mock_router):
            from routers.chat import get_streaming_state_endpoint
            result = await get_streaming_state_endpoint()

        assert result["sessions"]["session-done"]["post_disconnect_flushing"] is False

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
    These tests enforce AC7 at the source level so the flag can never silently
    reappear, while confirming the pipe-flush cleanup (1A leave-alive) survives."""

    def test_flag_absent_from_session_unit_source(self):
        """No functional use of the deleted flag/property in SessionUnit — AC7.

        We check for the assignment/access patterns (`self._generating_after_disconnect`
        and `.is_generating_after_disconnect`) rather than the bare token, because
        the replacement property's docstring legitimately *mentions* the old name
        when explaining what it replaced."""
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

    def test_pipe_flush_cleanup_preserved(self):
        """_cleanup_internal still cancels the pipe-flush task (1A leave-alive
        path must still clean up on real teardown)."""
        import inspect
        from core.session_unit import SessionUnit

        source = inspect.getsource(SessionUnit._cleanup_internal)
        assert "_pipe_flush_task = None" in source
