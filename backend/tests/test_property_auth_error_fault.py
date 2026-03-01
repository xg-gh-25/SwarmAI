"""Property-based tests for Claude SDK auth error fault condition.

**Bugfix: claude-sdk-auth-error-handling, Property 1: Fault Condition**

Tests that ResultMessage objects with is_error=True and subtype != 'error_during_execution'
are correctly yielded as SSE error events, NOT as assistant events.

This is a BUG CONDITION EXPLORATION test. On UNFIXED code, these tests are
EXPECTED TO FAIL — failure confirms the bug exists. The tests encode the
EXPECTED (correct) behavior and will pass once the fix is implemented.

**Validates: Requirements 1.1, 1.2, 2.1, 2.2**
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from hypothesis import given, strategies as st, settings, HealthCheck

from claude_agent_sdk import ResultMessage, SystemMessage

from core.agent_manager import AgentManager
from core.content_accumulator import ContentBlockAccumulator


# ---------------------------------------------------------------------------
# Hypothesis settings
# ---------------------------------------------------------------------------

PROPERTY_SETTINGS = settings(
    max_examples=5,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate arbitrary non-empty error text strings for ResultMessage.result
error_text_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=200,
).filter(lambda x: x.strip())

# Subtypes that are NOT 'error_during_execution' (the bug condition)
non_error_execution_subtype_strategy = st.sampled_from([
    "result",
    "init",
    "",
    "unknown",
    "auth_error",
])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_result_message(
    is_error: bool = True,
    result: str = "Not logged in · Please run /login",
    subtype: str = "result",
    total_cost_usd: float = 0,
) -> ResultMessage:
    """Create a ResultMessage with the given parameters."""
    return ResultMessage(
        subtype=subtype,
        duration_ms=0,
        duration_api_ms=0,
        is_error=is_error,
        num_turns=0,
        session_id="test-session-123",
        total_cost_usd=total_cost_usd,
        result=result,
    )


def make_init_system_message(session_id: str = "test-session-123") -> SystemMessage:
    """Create a SystemMessage init message to bootstrap session context."""
    return SystemMessage(
        subtype="init",
        data={"session_id": session_id},
    )


async def collect_events_from_run_query(
    agent_manager: AgentManager,
    messages: list,
    session_id: str = "test-session-123",
) -> list[dict]:
    """Run _run_query_on_client with mocked SDK messages and collect yielded SSE events.

    Sets up a mock ClaudeSDKClient that yields the given messages,
    then collects all SSE events yielded by _run_query_on_client.
    """
    # Create mock client
    mock_client = AsyncMock()
    mock_client.query = AsyncMock()

    # Mock receive_response to yield our test messages
    async def mock_receive_response():
        for msg in messages:
            yield msg

    mock_client.receive_response = mock_receive_response

    # Set up session context and accumulator
    session_context = {"sdk_session_id": session_id}
    assistant_content = ContentBlockAccumulator()
    agent_config = {"model": "claude-sonnet-4-20250514"}

    # Collect all yielded events
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

    return events


# ---------------------------------------------------------------------------
# Property Tests — Fault Condition Exploration
# ---------------------------------------------------------------------------


class TestAuthErrorFaultCondition:
    """Property 1: Fault Condition — Error ResultMessages Yield Error SSE Events.

    For any ResultMessage where is_error=True and subtype != 'error_during_execution',
    the yielded SSE events MUST contain type: "error" and MUST NOT contain
    type: "assistant" with the error text.

    **Validates: Requirements 1.1, 2.1**
    """

    @pytest.mark.asyncio
    async def test_auth_error_yields_error_event_not_assistant(self):
        """Concrete auth error case: 'Not logged in · Please run /login'.

        The SDK returns this when no API key is configured. On unfixed code,
        this is yielded as type: "assistant" — confirming the bug.

        **Validates: Requirements 1.1, 2.1**
        """
        agent_manager = AgentManager()

        init_msg = make_init_system_message()
        error_msg = make_result_message(
            is_error=True,
            result="Not logged in · Please run /login",
            subtype="result",
            total_cost_usd=0,
        )

        events = await collect_events_from_run_query(
            agent_manager,
            messages=[init_msg, error_msg],
        )

        # Extract event types
        error_events = [e for e in events if e.get("type") == "error"]
        assistant_events = [
            e for e in events
            if e.get("type") == "assistant"
            and any(
                block.get("text") == "Not logged in · Please run /login"
                for block in e.get("content", [])
            )
        ]

        # EXPECTED BEHAVIOR: error event present, no assistant event with error text
        assert len(error_events) > 0, (
            "Expected at least one SSE event with type='error' for is_error=True ResultMessage, "
            f"but got none. All events: {events}"
        )
        assert len(assistant_events) == 0, (
            "Expected NO SSE event with type='assistant' containing the error text, "
            f"but found: {assistant_events}"
        )

    @pytest.mark.asyncio
    async def test_general_error_yields_error_event_not_assistant(self):
        """Concrete general error case: 'Rate limit exceeded'.

        On unfixed code, this is also yielded as type: "assistant".

        **Validates: Requirements 1.1, 2.1**
        """
        agent_manager = AgentManager()

        init_msg = make_init_system_message()
        error_msg = make_result_message(
            is_error=True,
            result="Rate limit exceeded",
            subtype="result",
            total_cost_usd=0,
        )

        events = await collect_events_from_run_query(
            agent_manager,
            messages=[init_msg, error_msg],
        )

        error_events = [e for e in events if e.get("type") == "error"]
        assistant_events = [
            e for e in events
            if e.get("type") == "assistant"
            and any(
                block.get("text") == "Rate limit exceeded"
                for block in e.get("content", [])
            )
        ]

        assert len(error_events) > 0, (
            "Expected at least one SSE event with type='error' for is_error=True ResultMessage, "
            f"but got none. All events: {events}"
        )
        assert len(assistant_events) == 0, (
            "Expected NO SSE event with type='assistant' containing the error text, "
            f"but found: {assistant_events}"
        )

    @given(error_text=error_text_strategy, subtype=non_error_execution_subtype_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_property_any_is_error_true_yields_error_not_assistant(
        self,
        error_text: str,
        subtype: str,
    ):
        """Property: for ALL ResultMessage with is_error=True and subtype != 'error_during_execution',
        yielded SSE events MUST contain type: "error" and MUST NOT contain type: "assistant"
        with the error text.

        Uses Hypothesis to generate arbitrary error text strings.

        **Validates: Requirements 1.1, 2.1**
        """
        agent_manager = AgentManager()

        init_msg = make_init_system_message()
        error_msg = make_result_message(
            is_error=True,
            result=error_text,
            subtype=subtype,
        )

        events = await collect_events_from_run_query(
            agent_manager,
            messages=[init_msg, error_msg],
        )

        error_events = [e for e in events if e.get("type") == "error"]
        assistant_events_with_error_text = [
            e for e in events
            if e.get("type") == "assistant"
            and any(
                block.get("text") == error_text
                for block in e.get("content", [])
            )
        ]

        assert len(error_events) > 0, (
            f"Expected at least one SSE event with type='error' for "
            f"ResultMessage(is_error=True, result={error_text!r}, subtype={subtype!r}), "
            f"but got none. All events: {events}"
        )
        assert len(assistant_events_with_error_text) == 0, (
            f"Expected NO SSE event with type='assistant' containing error text {error_text!r}, "
            f"but found: {assistant_events_with_error_text}"
        )


class TestErrorSessionNotStored:
    """Property 1 (session aspect): Session MUST NOT be stored in _active_sessions
    after an is_error=True ResultMessage.

    **Validates: Requirements 1.2, 2.2**
    """

    @pytest.mark.asyncio
    async def test_error_session_not_stored_in_active_sessions(self):
        """After an is_error=True ResultMessage, the session MUST NOT be in _active_sessions.

        On unfixed code, _execute_on_session unconditionally stores the session,
        so this test confirms the bug exists.

        **Validates: Requirements 1.2, 2.2**
        """
        agent_manager = AgentManager()

        init_msg = make_init_system_message(session_id="error-session-456")
        error_msg = make_result_message(
            is_error=True,
            result="Not logged in · Please run /login",
            subtype="result",
            total_cost_usd=0,
        )

        # We need to test through _execute_on_session to check _active_sessions storage.
        # Mock the environment config and client creation.
        with patch("core.agent_manager._configure_claude_environment", new_callable=MagicMock):
            # Mock _build_options
            mock_options = MagicMock()
            mock_options.allowed_tools = []
            mock_options.permission_mode = "default"
            mock_options.mcp_servers = None
            mock_options.cwd = "/tmp"

            with patch.object(agent_manager, "_build_options", new_callable=AsyncMock, return_value=mock_options):
                # Mock _ClaudeClientWrapper
                mock_client = AsyncMock()
                mock_client.query = AsyncMock()

                async def mock_receive_response():
                    yield init_msg
                    yield error_msg

                mock_client.receive_response = mock_receive_response

                mock_wrapper = MagicMock()
                mock_wrapper.__aenter__ = AsyncMock(return_value=mock_client)
                mock_wrapper.__aexit__ = AsyncMock(return_value=False)

                with patch("core.agent_manager._ClaudeClientWrapper", return_value=mock_wrapper):
                    # Collect events from _execute_on_session
                    events = []
                    async for event in agent_manager._execute_on_session(
                        agent_config={"model": "claude-sonnet-4-20250514"},
                        query_content="test",
                        display_text="test",
                        session_id=None,
                        enable_skills=False,
                        enable_mcp=False,
                        is_resuming=False,
                        content=None,
                        user_message="test",
                        agent_id="default",
                    ):
                        events.append(event)

        # The session should NOT be stored in _active_sessions
        session_id = "error-session-456"
        assert session_id not in agent_manager._active_sessions, (
            f"Session {session_id} should NOT be in _active_sessions after an "
            f"is_error=True ResultMessage, but it was found: "
            f"{agent_manager._active_sessions.get(session_id)}"
        )
