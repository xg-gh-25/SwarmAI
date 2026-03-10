"""Property-based exploration tests for the resume stale ResultMessage bug.

Tests the fault condition: during ``--resume``, the SDK replays old messages
including a stale ``ResultMessage`` from the previous turn. The message loop
exits on the first ``ResultMessage`` it sees, returning stale results.

These tests are EXPECTED TO FAIL on unfixed code — failure confirms the bug
exists. After the fix (generation counter pattern), these tests should PASS.

Testing methodology: property-based (Hypothesis) + concrete unit tests.
Key property: stale ``ResultMessage``s during resume are never yielded as
``assistant`` SSE events.

**Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.3, 2.4**
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from hypothesis import given, strategies as st, settings, HealthCheck

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)

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

# Generate arbitrary non-empty stale result text for property-based tests
stale_text_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=100,
).filter(lambda x: x.strip())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_init_system_message(session_id: str = "test-session-123") -> SystemMessage:
    """Create a SystemMessage init message to bootstrap session context."""
    return SystemMessage(
        subtype="init",
        data={"session_id": session_id},
    )


def make_result_message(
    result: str = "stale result text",
    num_turns: int = 1,
    is_error: bool = False,
    subtype: str | None = None,
) -> ResultMessage:
    """Create a ResultMessage with the given parameters."""
    return ResultMessage(
        subtype=subtype,
        duration_ms=100,
        duration_api_ms=80,
        is_error=is_error,
        num_turns=num_turns,
        session_id="test-session-123",
        total_cost_usd=0.001,
        result=result,
    )


async def collect_events_from_run_query(
    agent_manager: AgentManager,
    messages: list,
    session_id: str = "test-session-123",
    is_resuming: bool = False,
) -> list[dict]:
    """Run _run_query_on_client with mocked SDK messages and collect SSE events.

    Sets up a mock ClaudeSDKClient that yields the given messages,
    then collects all SSE events yielded by _run_query_on_client.

    Parameters
    ----------
    agent_manager : AgentManager
        The agent manager instance.
    messages : list
        SDK messages to yield from the mock client.
    session_id : str
        Session ID for the mock init message.
    is_resuming : bool
        Whether this is a resume session (triggers stale-result detection path).
    """
    mock_client = AsyncMock()
    mock_client.query = AsyncMock()

    async def mock_receive_response():
        for msg in messages:
            yield msg

    mock_client.receive_response = mock_receive_response

    session_context = {"sdk_session_id": session_id}
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
        is_resuming=is_resuming,
        content=None,
        user_message="test query",
        agent_id="default",
    ):
        events.append(event)

    return events


# ---------------------------------------------------------------------------
# Test Class — Fault Condition Exploration
# ---------------------------------------------------------------------------


class TestStaleResultFaultCondition:
    """Property 1: Fault Condition — Stale ResultMessages during resume.

    For any resume session where the SDK replays a stale ResultMessage with
    num_turns<=1 and no preceding ToolUseBlocks, the stale result text MUST
    NOT be yielded as an ``assistant`` SSE event.

    On UNFIXED code, these tests are EXPECTED TO FAIL — the message loop
    exits on the first ResultMessage, yielding the stale text to the frontend.

    **Validates: Requirements 1.1, 1.3, 2.1, 2.3, 2.4**
    """

    @pytest.mark.asyncio
    async def test_stale_result_not_yielded_during_resume(self):
        """Basic stale result: resume session receives ResultMessage(num_turns=1).

        Mock receive_response to yield [SystemMessage(init), ResultMessage(stale)].
        With is_resuming=True, the stale result text should NOT appear as an
        assistant SSE event.

        On unfixed code, the loop exits on the first ResultMessage and yields
        the stale text — so this test FAILS, confirming the bug.

        **Validates: Requirements 1.1, 2.1**
        """
        agent_manager = AgentManager()

        init_msg = make_init_system_message()
        stale_msg = make_result_message(
            result="I've updated the README",
            num_turns=1,
        )

        events = await collect_events_from_run_query(
            agent_manager,
            messages=[init_msg, stale_msg],
            is_resuming=True,
        )

        # Find assistant events that contain the stale text
        stale_assistant_events = [
            e for e in events
            if e.get("type") == "assistant"
            and any(
                block.get("text") == "I've updated the README"
                for block in e.get("content", [])
            )
        ]

        # EXPECTED: stale result is NOT yielded as assistant event
        assert len(stale_assistant_events) == 0, (
            "Stale ResultMessage text should NOT be yielded as an assistant SSE "
            "event during resume, but it was. This confirms the bug: the message "
            "loop exits on the first ResultMessage without checking if it's stale. "
            f"Stale events found: {stale_assistant_events}"
        )

    @pytest.mark.asyncio
    async def test_repeated_replay_exhausts_retries(self):
        """Repeated replay: SDK replays same stale ResultMessage on every retry.

        Mock receive_response to always yield the same stale ResultMessage
        (simulating the SDK replaying old messages on each retry attempt).
        Assert the stale result text is NOT yielded as an assistant event.

        On unfixed code, _stale_retry_count hits _MAX_STALE_RETRIES=2 and
        the stale result is accepted — so this test FAILS.

        **Validates: Requirements 1.3, 2.3**
        """
        agent_manager = AgentManager()

        call_count = 0

        # Each call to receive_response replays the same stale messages
        async def mock_receive_response():
            nonlocal call_count
            call_count += 1
            yield make_init_system_message()
            yield make_result_message(
                result=f"stale result from replay #{call_count}",
                num_turns=1,
            )

        mock_client = AsyncMock()
        mock_client.query = AsyncMock()
        mock_client.receive_response = mock_receive_response

        session_context = {"sdk_session_id": "test-session-123"}
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
            is_resuming=True,
            content=None,
            user_message="test query",
            agent_id="default",
        ):
            events.append(event)

        # Find assistant events that contain any stale replay text
        stale_assistant_events = [
            e for e in events
            if e.get("type") == "assistant"
            and any(
                "stale result from replay" in (block.get("text") or "")
                for block in e.get("content", [])
            )
        ]

        # EXPECTED: no stale replay text yielded as assistant event
        assert len(stale_assistant_events) == 0, (
            "After exhausting retries, the system should NOT yield stale replay "
            "text as an assistant event. On unfixed code, _stale_retry_count "
            "hits _MAX_STALE_RETRIES and the stale result is accepted. "
            f"Stale events found: {stale_assistant_events}"
        )

    @given(stale_text=stale_text_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_property_stale_result_text_never_yielded(
        self,
        stale_text: str,
    ):
        """Property: for ALL stale result text, resume with num_turns<=1 and
        no tool use MUST NOT yield the stale text as an assistant SSE event.

        Uses Hypothesis to generate arbitrary stale result text strings.
        On unfixed code, the loop exits on the first ResultMessage and yields
        the stale text — so this test FAILS for all generated inputs.

        **Validates: Requirements 1.1, 2.1, 2.4**
        """
        agent_manager = AgentManager()

        init_msg = make_init_system_message()
        stale_msg = make_result_message(
            result=stale_text,
            num_turns=1,
        )

        events = await collect_events_from_run_query(
            agent_manager,
            messages=[init_msg, stale_msg],
            is_resuming=True,
        )

        # Find assistant events that contain the generated stale text
        stale_assistant_events = [
            e for e in events
            if e.get("type") == "assistant"
            and any(
                block.get("text") == stale_text
                for block in e.get("content", [])
            )
        ]

        # EXPECTED: stale text is NEVER yielded as assistant event
        assert len(stale_assistant_events) == 0, (
            f"Stale ResultMessage text {stale_text!r} should NOT be yielded as "
            "an assistant SSE event during resume with num_turns=1 and no tool "
            "use. This confirms the bug: the message loop exits on the first "
            f"ResultMessage. Stale events: {stale_assistant_events}"
        )
