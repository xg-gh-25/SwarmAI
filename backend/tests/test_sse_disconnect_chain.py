"""Tests for SSE disconnect → auto_recover_stuck kill chain fix.

Covers:
- AC1: Backend send() does NOT kill actively-streaming sessions (stall < 60s)
- AC2/AC3: Tested via frontend tests (not in this file)
- AC4: SESSION_BUSY error yielded when session is actively streaming
- AC5: Genuinely stuck sessions (stall > threshold) still get recovered

Test methodology: unit tests with mocked SessionUnit state.
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_unit import SessionUnit, SessionState


# ---------------------------------------------------------------------------
# AC1: Backend send() does NOT kill actively-streaming sessions (stall < 60s)
# ---------------------------------------------------------------------------


class TestAutoRecoverStallGuard:
    """Verify that auto_recover_stuck respects stall threshold."""

    @pytest.fixture
    def streaming_unit(self):
        """Create a SessionUnit stuck in STREAMING with recent activity."""
        unit = SessionUnit(session_id="test-ac1", agent_id="default")
        unit._transition(SessionState.IDLE)
        unit._transition(SessionState.STREAMING)
        # Set last event time to NOW (1 second ago — actively streaming)
        unit._last_event_time = time.time() - 1
        unit._streaming_start_time = time.time() - 30
        # Mock the existing subprocess
        unit._client = MagicMock()
        unit._client.interrupt = AsyncMock()
        unit._wrapper = MagicMock()
        unit._wrapper.__aexit__ = AsyncMock(return_value=False)
        return unit

    @pytest.fixture
    def stuck_unit(self):
        """Create a SessionUnit stuck in STREAMING with NO recent activity."""
        from core.session_unit import AUTO_RECOVER_STALL_THRESHOLD
        unit = SessionUnit(session_id="test-stuck", agent_id="default")
        unit._transition(SessionState.IDLE)
        unit._transition(SessionState.STREAMING)
        # Set last event time well beyond threshold — genuinely stuck
        unit._last_event_time = time.time() - (AUTO_RECOVER_STALL_THRESHOLD + 30)
        unit._streaming_start_time = time.time() - (AUTO_RECOVER_STALL_THRESHOLD + 60)
        unit._client = MagicMock()
        unit._client.interrupt = AsyncMock()
        unit._wrapper = MagicMock()
        unit._wrapper.__aexit__ = AsyncMock(return_value=False)
        return unit

    @pytest.mark.asyncio
    async def test_active_session_not_killed(self, streaming_unit):
        """AC1: send() on actively-streaming (stall=1s) raises SessionBusyError."""
        from core.exceptions import SessionBusyError

        mock_options = MagicMock()
        mock_options.model = "test-model"
        mock_options.system_prompt = "test"

        with pytest.raises(SessionBusyError) as exc_info:
            async for _ in streaming_unit.send(
                query_content="Should not kill",
                options=mock_options,
            ):
                pass

        # Session should still be STREAMING (not killed)
        assert streaming_unit.state == SessionState.STREAMING
        assert "actively streaming" in str(exc_info.value).lower() or \
               "session_busy" in str(exc_info.value.code).lower()

    @pytest.mark.asyncio
    async def test_stuck_session_calls_force_unstick(self, stuck_unit):
        """AC5: send() on genuinely stuck (stall>60s) calls force_unstick_streaming."""
        mock_options = MagicMock()
        mock_options.model = "test-model"
        mock_options.system_prompt = "test"

        # Mock force_unstick to transition to COLD via proper state machine
        async def mock_force_unstick():
            stuck_unit._transition(SessionState.DEAD)
            stuck_unit._transition(SessionState.COLD)

        with patch.object(
            stuck_unit, "force_unstick_streaming", side_effect=mock_force_unstick,
        ) as mock_unstick, patch(
            "core.claude_environment._ClaudeClientWrapper",
        ) as mock_wrapper_cls, patch(
            "core.claude_environment._configure_claude_environment",
        ), patch(
            "core.session_unit._spawn_lock", asyncio.Lock()
        ), patch(
            "core.claude_environment._env_lock", asyncio.Lock()
        ), patch(
            "core.resource_monitor.resource_monitor.spawn_budget",
            return_value=MagicMock(can_spawn=True),
        ):
            mock_client = MagicMock()
            mock_wrapper_instance = MagicMock()
            mock_wrapper_instance.__aenter__ = AsyncMock(return_value=mock_client)
            mock_wrapper_instance.__aexit__ = AsyncMock(return_value=False)
            mock_wrapper_cls.return_value = mock_wrapper_instance

            async def fake_stream(query):
                yield {"type": "result", "session_id": "test-stuck"}

            with patch.object(stuck_unit, "_stream_response", side_effect=fake_stream):
                events = []
                async for event in stuck_unit.send(
                    query_content="Recover me",
                    options=mock_options,
                    config=MagicMock(),
                ):
                    events.append(event)

            # force_unstick should have been called (genuinely stuck)
            mock_unstick.assert_called_once()

    @pytest.mark.asyncio
    async def test_stall_threshold_boundary(self):
        """Edge case: stall exactly at threshold should recover (not reject)."""
        from core.session_unit import AUTO_RECOVER_STALL_THRESHOLD

        unit = SessionUnit(session_id="test-boundary", agent_id="default")
        unit._transition(SessionState.IDLE)
        unit._transition(SessionState.STREAMING)
        # Set stall to exactly the threshold
        unit._last_event_time = time.time() - AUTO_RECOVER_STALL_THRESHOLD
        unit._streaming_start_time = time.time() - (AUTO_RECOVER_STALL_THRESHOLD + 10)
        unit._client = MagicMock()
        unit._client.interrupt = AsyncMock()
        unit._wrapper = MagicMock()
        unit._wrapper.__aexit__ = AsyncMock(return_value=False)

        mock_options = MagicMock()
        mock_options.model = "test-model"
        mock_options.system_prompt = "test"

        # Mock force_unstick — we just want to verify it's CALLED (not rejected)
        async def mock_force_unstick():
            unit._state = SessionState.COLD

        with patch.object(
            unit, "force_unstick_streaming", side_effect=mock_force_unstick,
        ) as mock_unstick:
            # Stall >= threshold → should call force_unstick, NOT raise SessionBusyError
            from core.exceptions import SessionBusyError
            try:
                async for event in unit.send(
                    query_content="Boundary",
                    options=mock_options,
                    config=MagicMock(),
                ):
                    break  # Just need to verify force_unstick was called
            except Exception:
                pass  # Spawn may fail — we only care about force_unstick being called

            mock_unstick.assert_called_once()


# ---------------------------------------------------------------------------
# AC4: SESSION_BUSY error yielded when session is actively streaming
# ---------------------------------------------------------------------------


class TestSessionBusyErrorEvent:
    """Verify SessionRouter yields SESSION_BUSY error on active streaming."""

    @pytest.mark.asyncio
    async def test_router_yields_session_busy_on_active_session(self):
        """When backend detects active streaming, SSE yields SESSION_BUSY error."""
        from core.session_router import SessionRouter
        from core.exceptions import SessionBusyError

        mock_pb = MagicMock()
        mock_pb.build_options = AsyncMock(return_value=MagicMock(
            model="test", system_prompt="test",
        ))
        router = SessionRouter(prompt_builder=mock_pb, config=MagicMock())

        # Create a unit and mock send() to raise SessionBusyError
        unit = router.get_or_create_unit("test-busy-sess", "default")
        unit._transition(SessionState.IDLE)

        async def mock_send(**kwargs):
            raise SessionBusyError(detail="Session actively streaming")
            # Make it an async generator
            yield  # pragma: no cover

        with patch.object(unit, "send", side_effect=SessionBusyError(detail="actively streaming")), \
             patch("core.agent_defaults.build_agent_config", new_callable=AsyncMock, return_value={"model": "test"}), \
             patch("database.db") as mock_db, \
             patch("core.session_manager.session_manager") as mock_sm:
            mock_sm.store_session = AsyncMock()
            mock_db.messages = MagicMock()
            mock_db.messages.put = AsyncMock()

            events = []
            async for event in router.run_conversation(
                session_id="test-busy-sess",
                agent_id="default",
                user_message="Should get SESSION_BUSY",
            ):
                events.append(event)

        # Should get a SESSION_BUSY error event
        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) >= 1
        assert error_events[0].get("code") == "SESSION_BUSY"


# ---------------------------------------------------------------------------
# SessionBusyError exists in exceptions module
# ---------------------------------------------------------------------------


class TestSessionBusyErrorClass:
    """Verify SessionBusyError is properly defined."""

    def test_session_busy_error_exists(self):
        """SessionBusyError should be importable from exceptions."""
        from core.exceptions import SessionBusyError
        err = SessionBusyError()
        assert err.code == "SESSION_BUSY"

    def test_session_busy_error_is_app_exception(self):
        """SessionBusyError should inherit from AppException."""
        from core.exceptions import SessionBusyError, AppException
        assert issubclass(SessionBusyError, AppException)

    def test_auto_recover_threshold_constant_exists(self):
        """AUTO_RECOVER_STALL_THRESHOLD should be exported from session_unit."""
        from core.session_unit import AUTO_RECOVER_STALL_THRESHOLD
        assert AUTO_RECOVER_STALL_THRESHOLD == 180.0
