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


@pytest.fixture(autouse=True)
def _redirect_pending_db(tmp_path, monkeypatch):
    """Point session_pending's SQLite path at a throwaway tmp DB for every test.

    Several tests mock the ``database.db`` singleton. Without this, any code that
    resolves ``session_pending._get_db_path()`` -> ``str(db.messages.db_path)``
    (the SESSION_BUSY -> mark_pending conversion AND the background drain worker's
    claim_pending_batch) gets the MagicMock repr as the path, and
    sqlite/aiosqlite ``connect()`` creates junk files literally named
    ``<MagicMock name='db.messages.db_path' id='...'>`` in the CWD. Redirecting the
    single ``_db_path_override`` seam covers all those paths at once and keeps the
    worktree clean.
    """
    from core import session_pending
    monkeypatch.setattr(
        session_pending, "_db_path_override", str(tmp_path / "pending.db"), raising=False,
    )


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

            with patch.object(stuck_unit._streaming_orchestrator, "_stream_response", side_effect=fake_stream):
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

    @pytest.fixture(autouse=True)
    def _no_background_drain(self, monkeypatch):
        """These tests make send() raise SESSION_BUSY on an IDLE unit (production
        raises it only for a streaming unit or a CLI that is not quiescent) and
        replace database.db with a MagicMock. The queued row then looks deliverable,
        and the turn's end kicks the drain (docs/design/chat-turn-liveness.md S1),
        which would run against the mocked database after the test. The drain is
        covered by test_session_drain.py and test_turn_liveness_p0.py."""
        from core.session_router import SessionRouter

        async def _skip(self, session_id):
            return None
        monkeypatch.setattr(SessionRouter, "drain_pending", _skip)

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

        # NOTE: mark_pending_by_id MUST be patched here. The SESSION_BUSY handler
        # in run_conversation converts the persisted row to a pending message via
        # session_pending.mark_pending_by_id, which resolves _get_db_path() ->
        # str(db.messages.db_path). With database.db mocked, that string is the
        # MagicMock repr and aiosqlite.connect() would create a junk file named
        # "<MagicMock name='db.messages.db_path' ...>" in the CWD. This test only
        # asserts the SESSION_BUSY event, so stub the pending conversion.
        with patch.object(unit, "send", side_effect=SessionBusyError(detail="actively streaming")), \
             patch("core.agent_defaults.build_agent_config", new_callable=AsyncMock, return_value={"model": "test"}), \
             patch("database.db") as mock_db, \
             patch("core.session_pending.mark_pending_by_id", new_callable=AsyncMock, return_value=1), \
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

    @pytest.mark.asyncio
    async def test_session_busy_converts_message_to_pending(self):
        """Root-1 SSOT Phase 2 (L2): SESSION_BUSY must NO LONGER delete the row.

        The persisted user message is converted to a server-side pending message
        (mark_pending_by_id, sent=0) owned by the drain worker, and the error
        carries pendingSeq/pendingId (NOT retryPayload — the server now owns
        durability + delivery, so a frontend re-send would double-deliver). This
        makes the SessionBusyError text ("saved and will be sent automatically")
        finally TRUE.
        """
        from core.session_router import SessionRouter
        from core.exceptions import SessionBusyError

        mock_pb = MagicMock()
        mock_pb.build_options = AsyncMock(return_value=MagicMock(
            model="test", system_prompt="test",
        ))
        router = SessionRouter(prompt_builder=mock_pb, config=MagicMock())

        unit = router.get_or_create_unit("test-busy-preserve", "default")
        unit._transition(SessionState.IDLE)

        with patch.object(unit, "send", side_effect=SessionBusyError(detail="actively streaming")), \
             patch("core.agent_defaults.build_agent_config", new_callable=AsyncMock, return_value={"model": "test"}), \
             patch("database.db") as mock_db, \
             patch("core.session_pending.mark_pending_by_id", new_callable=AsyncMock, return_value=7) as mock_mark, \
             patch("core.session_manager.session_manager") as mock_sm:
            mock_sm.store_session = AsyncMock()
            mock_db.messages = MagicMock()
            mock_db.messages.put = AsyncMock()
            # delete_last_user_message must NOT be called any more.
            mock_db.messages.delete_last_user_message = AsyncMock(return_value=True)

            events = []
            async for event in router.run_conversation(
                session_id="test-busy-preserve",
                agent_id="default",
                user_message="Don't lose me",
            ):
                events.append(event)

        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) >= 1
        busy = error_events[0]
        assert busy.get("code") == "SESSION_BUSY"

        # (a) the orphan DELETE is gone — the row is preserved, not deleted.
        mock_db.messages.delete_last_user_message.assert_not_awaited()

        # (b) the persisted row was converted to pending (sent=0) for the drain worker.
        mock_mark.assert_awaited_once()
        assert mock_mark.await_args.args[0] == "test-busy-preserve"

        # (c) error carries the pending id/seq, NOT retryPayload (server owns delivery).
        assert busy.get("pendingSeq") == 7
        assert busy.get("pendingId") is not None
        assert busy.get("retryPayload") is None, \
            "with server-side pending, retryPayload would cause a double-send"

    @pytest.mark.asyncio
    async def test_abort_sentinel_not_forwarded_to_sse(self):
        """run_conversation's send-consumer loop must INTERCEPT the internal
        {_abort} sentinel (send()'s dead→streaming state-flip guard yields a
        SESSION_BUSY error event THEN {_abort}). The error must reach the client
        but the typeless {_abort} frame must NOT be forwarded to the SSE stream.
        (Gate-2 correctness finding, run_c9fa2382.)"""
        from core.session_router import SessionRouter

        mock_pb = MagicMock()
        mock_pb.build_options = AsyncMock(return_value=MagicMock(
            model="test", system_prompt="test",
        ))
        router = SessionRouter(prompt_builder=mock_pb, config=MagicMock())
        unit = router.get_or_create_unit("test-abort-sess", "default")
        unit._transition(SessionState.IDLE)

        # Mirror send()'s clean-abort emission: a user-facing error, then the
        # internal {_abort} sentinel.
        async def _send_error_then_abort(**kwargs):
            yield {"type": "error", "code": "SESSION_BUSY",
                   "message": "refreshed while starting", "error": "refreshed while starting"}
            yield {"_abort": True}

        with patch.object(unit, "send", _send_error_then_abort), \
             patch("core.agent_defaults.build_agent_config", new_callable=AsyncMock, return_value={"model": "test"}), \
             patch("database.db") as mock_db, \
             patch("core.session_manager.session_manager") as mock_sm:
            mock_sm.store_session = AsyncMock()
            mock_db.messages = MagicMock()
            mock_db.messages.put = AsyncMock()

            events = []
            async for event in router.run_conversation(
                session_id="test-abort-sess",
                agent_id="default",
                user_message="hi",
            ):
                events.append(event)

        # The SESSION_BUSY error reached the client.
        assert any(
            e.get("type") == "error" and e.get("code") == "SESSION_BUSY"
            for e in events
        ), f"SESSION_BUSY error must be forwarded, got {events}"
        # The internal {_abort} sentinel must NOT leak into the SSE stream.
        assert not any(e.get("_abort") for e in events), \
            f"the internal _abort sentinel must be intercepted, not streamed; got {events}"


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


# ---------------------------------------------------------------------------
# A turn's POST stream no longer owns the turn (core/session_events): a client
# drop detaches the consumer in sse_with_heartbeat (tests/test_chat_detach.py),
# so chat_stream's message_generator has no disconnect branch any more. A real
# cancellation (daemon shutdown) must propagate, not be swallowed as a "drop".
# ---------------------------------------------------------------------------


class TestChatStreamPassesTurnEvents:
    """message_generator relays the router's envelopes and never swallows a cancel."""

    @pytest.mark.asyncio
    async def test_cancel_propagates_and_envelopes_pass_through(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        import routers.chat as chat_mod

        env = {"type": "session_start", "sessionId": "srv-1", "turnId": "t1", "eventSeq": 1}

        async def fake_run_conversation(**kwargs):
            yield env
            raise asyncio.CancelledError()

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "agent_id": "default", "message": "hi", "session_id": None,
            "enable_skills": False, "enable_mcp": False,
        })
        mock_router = MagicMock()
        mock_router.run_conversation = fake_run_conversation
        with patch.object(chat_mod, "_get_router", return_value=mock_router), \
             patch.object(chat_mod, "agent_exists", new_callable=AsyncMock, return_value=True), \
             patch.object(chat_mod, "sse_with_heartbeat", side_effect=lambda gen, **kw: gen):
            resp = await chat_mod.chat_stream(mock_request)
            got = []
            with pytest.raises(asyncio.CancelledError):
                async for ev in resp.body_iterator:
                    got.append(ev)
        assert got == [env]
