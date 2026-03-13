"""Preservation property tests for stop/resume chat fix.

**Bugfix: stop-resume-chat-fix, Property 2: Preservation**

Tests that capture baseline behavior on UNFIXED code which must be preserved
after the fix is applied. These tests verify that:

- Genuine ``error_during_execution`` (no interrupt) still triggers cleanup
- ``interrupt_session()`` with missing client returns failure without side effects
- Normal ``ResultMessage`` completion preserves the client (no error handler triggered)
- SDK reader errors (no interrupt) still set ``had_error``

All tests MUST PASS on unfixed code. They will be re-run after the fix to
verify no regressions were introduced.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from claude_agent_sdk import ResultMessage, SystemMessage

from core.agent_manager import AgentManager
from core.content_accumulator import ContentBlockAccumulator


# ---------------------------------------------------------------------------
# Helpers (same patterns as test_stop_resume_bug_condition.py)
# ---------------------------------------------------------------------------

def make_result_message(
    subtype: str = "result",
    is_error: bool = False,
    result: str = "Done",
) -> ResultMessage:
    """Create a ResultMessage with the given parameters."""
    return ResultMessage(
        subtype=subtype,
        duration_ms=100,
        duration_api_ms=80,
        is_error=is_error,
        num_turns=1,
        session_id="test-session",
        total_cost_usd=0.01,
        result=result,
    )


def make_init_system_message(session_id: str = "test-session") -> SystemMessage:
    """Create a SystemMessage init message to bootstrap session context."""
    return SystemMessage(
        subtype="init",
        data={"session_id": session_id},
    )


async def collect_events_from_run_query(
    agent_manager: AgentManager,
    messages: list,
    session_id: str = "test-session",
    app_session_id: str | None = None,
) -> tuple[list[dict], dict]:
    """Run _run_query_on_client with mocked SDK messages and collect SSE events.

    Returns (events_list, session_context) so tests can inspect both
    the yielded events and the session_context state after execution.
    """
    mock_client = AsyncMock()
    mock_client.query = AsyncMock()

    async def mock_receive_response():
        for msg in messages:
            yield msg

    mock_client.receive_response = mock_receive_response

    session_context = {"sdk_session_id": session_id}
    if app_session_id is not None:
        session_context["app_session_id"] = app_session_id
    assistant_content = ContentBlockAccumulator()
    agent_config = {"model": "claude-sonnet-4-20250514"}

    events = []
    async for event in agent_manager._run_query_on_client(
        client=mock_client,
        query_content="test query",
        display_text="test query",
        agent_config=agent_config,
        session_context=session_context,
        assistant_content=assistant_content,
        is_resuming=False,
        content=None,
        user_message="test query",
        agent_id="default",
    ):
        events.append(event)

    return events, session_context


# ---------------------------------------------------------------------------
# Test Case 1: Genuine error_during_execution (no interrupt) → cleanup called
# ---------------------------------------------------------------------------


class TestGenuineErrorCleanup:
    """Preservation: Genuine error_during_execution WITHOUT a prior
    interrupt_session() call must still trigger _cleanup_session, set
    had_error=True, and emit an ERROR_DURING_EXECUTION SSE event.

    This test MUST PASS on unfixed code — it captures existing behavior.

    **Validates: Requirements 3.1**
    """

    @pytest.mark.asyncio
    async def test_genuine_error_triggers_cleanup_and_error_event(self):
        """Genuine error_during_execution (no interrupt) must clean up
        the session and emit an error event.

        MUST PASS on unfixed code — this is the baseline behavior to preserve.

        **Validates: Requirements 3.1**
        """
        agent_manager = AgentManager()
        session_id = "genuine-error-session"

        # Create a mock client and wrapper
        mock_client = AsyncMock()
        mock_wrapper = MagicMock()
        mock_wrapper.__aexit__ = AsyncMock(return_value=False)

        # Pre-populate _active_sessions (simulates a live session)
        agent_manager._active_sessions[session_id] = {
            "client": mock_client,
            "wrapper": mock_wrapper,
            "created_at": 0,
            "last_used": 0,
            "activity_extracted": False,
            "failure_tracker": MagicMock(),
        }

        # NOTE: We do NOT call interrupt_session() — this is a genuine error

        # Feed error_during_execution through _run_query_on_client
        init_msg = make_init_system_message(session_id=session_id)
        error_msg = make_result_message(
            subtype="error_during_execution",
            is_error=True,
            result="Genuine subprocess crash",
        )

        events, session_context = await collect_events_from_run_query(
            agent_manager,
            messages=[init_msg, error_msg],
            session_id=session_id,
        )

        # ASSERTION: had_error must be set for genuine errors
        assert session_context.get("had_error") is True, (
            "Genuine error_during_execution must set had_error=True. "
            "This is baseline behavior that must be preserved after the fix."
        )

        # ASSERTION: Client must be removed from _active_sessions (cleanup called)
        assert session_id not in agent_manager._active_sessions, (
            "Genuine error_during_execution must call _cleanup_session() "
            "which removes the client from _active_sessions. "
            f"Active sessions: {list(agent_manager._active_sessions.keys())}"
        )

        # ASSERTION: Error SSE event must be emitted
        error_events = [
            e for e in events
            if e.get("type") == "error"
            and e.get("code") == "ERROR_DURING_EXECUTION"
        ]
        assert len(error_events) > 0, (
            "Genuine error_during_execution must emit an error SSE event "
            "with code ERROR_DURING_EXECUTION. No such event found in: "
            f"{[e.get('code') for e in events if e.get('type') == 'error']}"
        )


# ---------------------------------------------------------------------------
# Test Case 2: interrupt_session() with missing client → returns failure
# ---------------------------------------------------------------------------


class TestInterruptMissingClient:
    """Preservation: Calling interrupt_session() when no client exists
    must return {"success": False} without side effects.

    This test MUST PASS on unfixed code — it captures existing behavior.

    **Validates: Requirements 3.6**
    """

    @pytest.mark.asyncio
    async def test_interrupt_missing_client_returns_failure(self):
        """interrupt_session() with no client must return
        failure without modifying _active_sessions.

        MUST PASS on unfixed code — this is the baseline behavior to preserve.

        **Validates: Requirements 3.6**
        """
        agent_manager = AgentManager()
        nonexistent_id = "nonexistent-session-id"

        # Pre-populate _active_sessions with a different session to verify
        # no side effects occur on other sessions
        other_session_id = "other-session"
        mock_client = AsyncMock()
        agent_manager._active_sessions[other_session_id] = {
            "client": mock_client,
            "wrapper": MagicMock(),
            "created_at": 0,
            "last_used": 0,
            "activity_extracted": False,
            "failure_tracker": MagicMock(),
        }

        # Snapshot _active_sessions before the call
        sessions_before = dict(agent_manager._active_sessions)

        # Call interrupt_session with a nonexistent session ID
        result = await agent_manager.interrupt_session(nonexistent_id)

        # ASSERTION: Must return failure
        assert result["success"] is False, (
            "interrupt_session() with missing client must return "
            f'{{"success": False}}. Got: {result}'
        )

        # ASSERTION: No side effects on _active_sessions
        assert agent_manager._active_sessions == sessions_before, (
            "interrupt_session() with missing client must not modify "
            "_active_sessions. Sessions changed unexpectedly."
        )


# ---------------------------------------------------------------------------
# Test Case 3: Normal ResultMessage completion → client preserved
# ---------------------------------------------------------------------------


class TestNormalCompletionPreservesClient:
    """Preservation: A normal ResultMessage (no error subtype) must NOT
    trigger the error handler. The client must remain in _active_sessions,
    no error events emitted, and had_error must NOT be set.

    This test MUST PASS on unfixed code — it captures existing behavior.

    **Validates: Requirements 3.4**
    """

    @pytest.mark.asyncio
    async def test_normal_completion_preserves_client(self):
        """Normal ResultMessage completion must not remove the client
        from _active_sessions or emit error events.

        MUST PASS on unfixed code — this is the baseline behavior to preserve.

        **Validates: Requirements 3.4**
        """
        agent_manager = AgentManager()
        session_id = "normal-completion-session"

        # Create a mock client and wrapper
        mock_client = AsyncMock()
        mock_wrapper = MagicMock()
        mock_wrapper.__aexit__ = AsyncMock(return_value=False)

        # Pre-populate _active_sessions
        agent_manager._active_sessions[session_id] = {
            "client": mock_client,
            "wrapper": mock_wrapper,
            "created_at": 0,
            "last_used": 0,
            "activity_extracted": False,
            "failure_tracker": MagicMock(),
        }

        # Feed a normal (non-error) ResultMessage
        init_msg = make_init_system_message(session_id=session_id)
        normal_msg = make_result_message(
            subtype="result",
            is_error=False,
            result="Task completed successfully.",
        )

        events, session_context = await collect_events_from_run_query(
            agent_manager,
            messages=[init_msg, normal_msg],
            session_id=session_id,
        )

        # ASSERTION: Client must still be in _active_sessions
        assert session_id in agent_manager._active_sessions, (
            "Normal completion must NOT remove the client from "
            "_active_sessions. The error handler should not fire "
            "for non-error ResultMessages. "
            f"Active sessions: {list(agent_manager._active_sessions.keys())}"
        )

        # ASSERTION: No error events emitted
        error_events = [
            e for e in events
            if e.get("type") == "error"
        ]
        assert len(error_events) == 0, (
            "Normal completion must NOT emit any error events. "
            f"Found error events: {error_events}"
        )

        # ASSERTION: had_error must NOT be set
        assert not session_context.get("had_error"), (
            "Normal completion must NOT set had_error. "
            f"session_context had_error = {session_context.get('had_error')}"
        )


# ---------------------------------------------------------------------------
# Test Case 4: SDK reader error (no interrupt) → had_error set
# ---------------------------------------------------------------------------


class TestSDKReaderErrorSetsHadError:
    """Preservation: When the SDK reader raises an exception (source="error")
    WITHOUT a prior interrupt, had_error must be set to True and an
    SDK_STREAM_ERROR event must be emitted.

    This test MUST PASS on unfixed code — it captures existing behavior.

    **Validates: Requirements 3.1**
    """

    @pytest.mark.asyncio
    async def test_sdk_reader_error_without_interrupt_sets_had_error(self):
        """SDK reader error without interrupt must set had_error=True
        and emit SDK_STREAM_ERROR event.

        MUST PASS on unfixed code — this is the baseline behavior to preserve.

        **Validates: Requirements 3.1**
        """
        agent_manager = AgentManager()
        session_id = "sdk-reader-error-session"

        # Create a mock client and wrapper
        mock_client = AsyncMock()
        mock_wrapper = MagicMock()
        mock_wrapper.__aexit__ = AsyncMock(return_value=False)

        # Pre-populate _active_sessions
        agent_manager._active_sessions[session_id] = {
            "client": mock_client,
            "wrapper": mock_wrapper,
            "created_at": 0,
            "last_used": 0,
            "activity_extracted": False,
            "failure_tracker": MagicMock(),
        }

        # NOTE: We do NOT call interrupt_session() — this is a genuine error

        # Simulate SDK reader raising an exception after yielding init
        inner_mock_client = AsyncMock()
        inner_mock_client.query = AsyncMock()

        async def mock_receive_with_error():
            yield make_init_system_message(session_id=session_id)
            raise Exception("Genuine subprocess crash")

        inner_mock_client.receive_response = mock_receive_with_error

        session_context = {"sdk_session_id": session_id}
        assistant_content = ContentBlockAccumulator()
        agent_config = {"model": "claude-sonnet-4-20250514"}

        events = []
        async for event in agent_manager._run_query_on_client(
            client=inner_mock_client,
            query_content="test query",
            display_text="test query",
            agent_config=agent_config,
            session_context=session_context,
            assistant_content=assistant_content,
            is_resuming=False,
            content=None,
            user_message="test query",
            agent_id="default",
        ):
            events.append(event)

        # ASSERTION: had_error must be set for genuine SDK reader errors
        assert session_context.get("had_error") is True, (
            "Genuine SDK reader error must set had_error=True. "
            "This is baseline behavior that must be preserved."
        )

        # ASSERTION: SDK_STREAM_ERROR event must be emitted
        stream_error_events = [
            e for e in events
            if e.get("type") == "error"
            and e.get("code") == "SDK_STREAM_ERROR"
        ]
        assert len(stream_error_events) > 0, (
            "Genuine SDK reader error must emit SDK_STREAM_ERROR event. "
            f"No such event found. Events: "
            f"{[e.get('code') for e in events if e.get('type') == 'error']}"
        )
