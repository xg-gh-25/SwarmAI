"""Bug condition exploration test for stop/resume chat fix.

**Bugfix: stop-resume-chat-fix, Property 1: Bug Condition**

Tests that demonstrate the bug exists on UNFIXED code: when a user clicks Stop
during streaming, the SDK returns ``error_during_execution``, and the backend
unconditionally calls ``_cleanup_session()`` — destroying the reusable SDK client
from ``_active_sessions``.

These tests encode the EXPECTED (fixed) behavior:
- Client SHALL remain in ``_active_sessions`` after interrupt + error_during_execution
- No error SSE event SHALL be emitted
- ``had_error`` SHALL NOT be set on session_context

On UNFIXED code, these tests WILL FAIL — failure confirms the bug exists.
On FIXED code, these tests WILL PASS — passing confirms the fix works.

**Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2, 2.3**
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from claude_agent_sdk import ResultMessage, SystemMessage

from core.agent_manager import AgentManager
from core.content_accumulator import ContentBlockAccumulator


# ---------------------------------------------------------------------------
# Helpers
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
    set_interrupted_after_init: bool = False,
) -> tuple[list[dict], dict]:
    """Run _run_query_on_client with mocked SDK messages and collect SSE events.

    Returns (events_list, session_context) so tests can inspect both
    the yielded events and the session_context state after execution.

    When ``set_interrupted_after_init`` is True, the mock receive_response
    sets the ``interrupted`` flag on the session's ``_active_sessions`` entry
    after yielding the init SystemMessage but before yielding subsequent
    messages. This simulates the real-world timing where the user clicks
    Stop DURING streaming (after ``_run_query_on_client`` has started and
    cleared stale flags via Change 3).
    """
    mock_client = AsyncMock()
    mock_client.query = AsyncMock()

    async def mock_receive_response():
        for i, msg in enumerate(messages):
            yield msg
            # Simulate user clicking Stop after init message is processed.
            # In production: _run_query_on_client starts → clears stale flag
            # → streaming begins → user clicks Stop → interrupt_session sets
            # flag → SDK returns error → error handler checks flag.
            if set_interrupted_after_init and i == 0:
                info = agent_manager._active_sessions.get(session_id)
                if info:
                    info["interrupted"] = True

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
# Test Case 1: Interrupt + error_during_execution → client preserved
# ---------------------------------------------------------------------------


class TestInterruptPreservesClient:
    """Bug Condition: After interrupt_session() + error_during_execution,
    the client SHALL remain in _active_sessions.

    On UNFIXED code, _cleanup_session() is called unconditionally,
    destroying the client. This test WILL FAIL on unfixed code.

    **Validates: Requirements 1.1, 2.1**
    """

    @pytest.mark.asyncio
    async def test_client_preserved_after_interrupt_and_error(self):
        """After interrupt + error_during_execution, client must stay in
        _active_sessions for reuse by the next message.

        WILL FAIL on unfixed code because _cleanup_session destroys it.

        **Validates: Requirements 1.1, 2.1**
        """
        agent_manager = AgentManager()
        session_id = "interrupt-test-session"

        # Create a mock client and wrapper
        mock_client = AsyncMock()
        mock_client.interrupt = AsyncMock()
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

        # Register client in _clients (simulates active streaming)
        agent_manager._clients[session_id] = mock_client

        # Step 1+2: Feed error_during_execution through _run_query_on_client.
        # The interrupted flag is set DURING streaming (after init message)
        # to simulate the real-world timing where user clicks Stop while
        # streaming is active. This avoids the stale-flag clearing in Change 3.
        init_msg = make_init_system_message(session_id=session_id)
        error_msg = make_result_message(
            subtype="error_during_execution",
            is_error=True,
            result="Interrupted",
        )

        events, session_context = await collect_events_from_run_query(
            agent_manager,
            messages=[init_msg, error_msg],
            session_id=session_id,
            set_interrupted_after_init=True,
        )

        # ASSERTION: Client must still be in _active_sessions
        assert session_id in agent_manager._active_sessions, (
            f"BUG CONFIRMED: Client was removed from _active_sessions after "
            f"interrupt + error_during_execution. _cleanup_session() was called "
            f"unconditionally, destroying the reusable SDK client. "
            f"Active sessions: {list(agent_manager._active_sessions.keys())}"
        )


# ---------------------------------------------------------------------------
# Test Case 2: No error SSE event after interrupt + error_during_execution
# ---------------------------------------------------------------------------


class TestNoErrorEventAfterInterrupt:
    """Bug Condition: After interrupt + error_during_execution, no SSE error
    event with code ERROR_DURING_EXECUTION shall be emitted.

    On UNFIXED code, the error event IS emitted because the handler has
    no conditional branch for interrupts. This test WILL FAIL on unfixed code.

    **Validates: Requirements 1.3, 2.3**
    """

    @pytest.mark.asyncio
    async def test_no_error_event_emitted_after_interrupt(self):
        """After interrupt + error_during_execution, no error SSE event
        should be yielded to the frontend.

        WILL FAIL on unfixed code because error event is emitted unconditionally.

        **Validates: Requirements 1.3, 2.3**
        """
        agent_manager = AgentManager()
        session_id = "no-error-event-session"

        mock_client = AsyncMock()
        mock_client.interrupt = AsyncMock()
        mock_wrapper = MagicMock()
        mock_wrapper.__aexit__ = AsyncMock(return_value=False)

        agent_manager._active_sessions[session_id] = {
            "client": mock_client,
            "wrapper": mock_wrapper,
            "created_at": 0,
            "last_used": 0,
            "activity_extracted": False,
            "failure_tracker": MagicMock(),
        }
        agent_manager._clients[session_id] = mock_client

        # Feed error_during_execution with interrupted flag set DURING streaming
        init_msg = make_init_system_message(session_id=session_id)
        error_msg = make_result_message(
            subtype="error_during_execution",
            is_error=True,
            result="Interrupted",
        )

        events, session_context = await collect_events_from_run_query(
            agent_manager,
            messages=[init_msg, error_msg],
            session_id=session_id,
            set_interrupted_after_init=True,
        )

        # ASSERTION: No error events should be emitted
        error_events = [
            e for e in events
            if e.get("type") == "error"
            and e.get("code") == "ERROR_DURING_EXECUTION"
        ]
        assert len(error_events) == 0, (
            f"BUG CONFIRMED: Error event with code ERROR_DURING_EXECUTION was "
            f"emitted after user-initiated interrupt. The stop was intentional "
            f"and should not produce an error banner. Events: {error_events}"
        )

        # ASSERTION: had_error should NOT be set
        assert not session_context.get("had_error"), (
            f"BUG CONFIRMED: had_error was set to True after user-initiated "
            f"interrupt. This causes the session to be treated as failed."
        )


# ---------------------------------------------------------------------------
# Test Case 3: Resume after interrupt finds preserved client (PATH B)
# ---------------------------------------------------------------------------


class TestResumeAfterInterruptUsesPathB:
    """Bug Condition: After interrupt + error_during_execution, the next
    call to _get_active_client() SHALL return the preserved client so
    the session resumes on PATH B (reuse existing client).

    On UNFIXED code, _cleanup_session() destroys the client, so
    _get_active_client() returns None and PATH A is taken.
    This test WILL FAIL on unfixed code.

    **Validates: Requirements 1.2, 2.2**
    """

    @pytest.mark.asyncio
    async def test_get_active_client_returns_preserved_client(self):
        """After interrupt + error_during_execution, _get_active_client()
        must return the preserved client for PATH B resume.

        WILL FAIL on unfixed code because client was cleaned up.

        **Validates: Requirements 1.2, 2.2**
        """
        agent_manager = AgentManager()
        session_id = "resume-test-session"

        mock_client = AsyncMock()
        mock_client.interrupt = AsyncMock()
        mock_wrapper = MagicMock()
        mock_wrapper.__aexit__ = AsyncMock(return_value=False)

        agent_manager._active_sessions[session_id] = {
            "client": mock_client,
            "wrapper": mock_wrapper,
            "created_at": 0,
            "last_used": 0,
            "activity_extracted": False,
            "failure_tracker": MagicMock(),
        }
        agent_manager._clients[session_id] = mock_client

        # Feed error_during_execution with interrupted flag set DURING streaming
        init_msg = make_init_system_message(session_id=session_id)
        error_msg = make_result_message(
            subtype="error_during_execution",
            is_error=True,
            result="Interrupted",
        )

        await collect_events_from_run_query(
            agent_manager,
            messages=[init_msg, error_msg],
            session_id=session_id,
            set_interrupted_after_init=True,
        )

        # ASSERTION: _get_active_client should return the preserved client
        retrieved_client = agent_manager._get_active_client(session_id)
        assert retrieved_client is not None, (
            f"BUG CONFIRMED: _get_active_client() returned None after "
            f"interrupt + error_during_execution. The client was destroyed "
            f"by _cleanup_session(), forcing PATH A (new subprocess) on "
            f"the next message. This loses conversation context."
        )
        assert retrieved_client is mock_client, (
            f"Expected the same client instance to be preserved, but got "
            f"a different object. Original: {mock_client}, Got: {retrieved_client}"
        )


# ---------------------------------------------------------------------------
# Test Case 4: SDK reader error after interrupt → client preserved
# ---------------------------------------------------------------------------


class TestSDKReaderErrorAfterInterrupt:
    """Bug Condition: When the SDK reader raises an exception (source="error")
    after interrupt_session() was called, the client SHALL be preserved.

    On UNFIXED code, the source="error" handler unconditionally sets
    had_error=True and does not check for interrupt. This test WILL FAIL
    on unfixed code.

    **Validates: Requirements 2.1, 2.3**
    """

    @pytest.mark.asyncio
    async def test_sdk_reader_error_after_interrupt_preserves_client(self):
        """SDK reader error after interrupt should treat as user stop,
        not a fatal error. Client must be preserved.

        WILL FAIL on unfixed code because had_error is set unconditionally.

        **Validates: Requirements 2.1, 2.3**
        """
        agent_manager = AgentManager()
        session_id = "sdk-error-after-interrupt"

        mock_client = AsyncMock()
        mock_client.interrupt = AsyncMock()
        mock_wrapper = MagicMock()
        mock_wrapper.__aexit__ = AsyncMock(return_value=False)

        agent_manager._active_sessions[session_id] = {
            "client": mock_client,
            "wrapper": mock_wrapper,
            "created_at": 0,
            "last_used": 0,
            "activity_extracted": False,
            "failure_tracker": MagicMock(),
        }
        agent_manager._clients[session_id] = mock_client

        # Simulate SDK reader raising an exception after interrupt.
        # We set the interrupted flag DURING receive_response (after init)
        # to simulate the real-world timing where user clicks Stop while
        # streaming is active.
        inner_mock_client = AsyncMock()
        inner_mock_client.query = AsyncMock()

        async def mock_receive_with_error():
            yield make_init_system_message(session_id=session_id)
            # Simulate user clicking Stop during streaming
            info = agent_manager._active_sessions.get(session_id)
            if info:
                info["interrupted"] = True
            raise Exception("Connection interrupted")

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

        # ASSERTION: had_error should NOT be set after interrupt
        assert not session_context.get("had_error"), (
            f"BUG CONFIRMED: had_error was set to True after SDK reader "
            f"error following user-initiated interrupt. The error handler "
            f"does not distinguish interrupt-caused errors from genuine ones."
        )

        # ASSERTION: No SDK_STREAM_ERROR event should be emitted
        stream_error_events = [
            e for e in events
            if e.get("type") == "error"
            and e.get("code") == "SDK_STREAM_ERROR"
        ]
        assert len(stream_error_events) == 0, (
            f"BUG CONFIRMED: SDK_STREAM_ERROR event emitted after interrupt. "
            f"The SDK reader error was caused by the interrupt and should "
            f"not produce an error banner. Events: {stream_error_events}"
        )
