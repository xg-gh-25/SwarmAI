"""Property-based tests for preservation of existing behavior.

**Bugfix: claude-sdk-auth-error-handling, Property 2: Preservation**

Tests that non-error ResultMessages and existing error handling remain unchanged.
These tests MUST PASS on the UNFIXED code — they establish the baseline behavior
that the fix must preserve.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**
"""
import pytest
import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch
from hypothesis import given, strategies as st, settings as hyp_settings, HealthCheck

from claude_agent_sdk import ResultMessage, SystemMessage

from core.agent_manager import AgentManager
from core.content_accumulator import ContentBlockAccumulator


# ---------------------------------------------------------------------------
# Hypothesis settings
# ---------------------------------------------------------------------------

PROPERTY_SETTINGS = hyp_settings(
    max_examples=5,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate arbitrary non-empty result text for normal (non-error) ResultMessages
normal_result_text_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=200,
).filter(lambda x: x.strip())

# Generate arbitrary error text for error_during_execution messages
error_execution_text_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=200,
).filter(lambda x: x.strip())


# ---------------------------------------------------------------------------
# Helpers (reuse patterns from test_property_auth_error_fault.py)
# ---------------------------------------------------------------------------

def make_result_message(
    is_error: bool = False,
    result: str = "Hello world",
    subtype: str = "result",
    total_cost_usd: float = 0.01,
) -> ResultMessage:
    """Create a ResultMessage with the given parameters."""
    return ResultMessage(
        subtype=subtype,
        duration_ms=100,
        duration_api_ms=80,
        is_error=is_error,
        num_turns=1,
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
) -> tuple[list[dict], ContentBlockAccumulator]:
    """Run _run_query_on_client with mocked SDK messages and collect yielded SSE events.

    Returns both the events list and the ContentBlockAccumulator so tests can
    verify content persistence behavior.
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
        is_resuming=False,
        content=None,
        user_message="test query",
        agent_id="default",
    ):
        events.append(event)

    return events, assistant_content


# ---------------------------------------------------------------------------
# Property Tests — Preservation of Non-Error ResultMessages
# ---------------------------------------------------------------------------


class TestNormalResultMessagePreservation:
    """Property 2: Preservation — Non-Error ResultMessages yield assistant SSE events.

    For any ResultMessage where is_error=False and result is non-empty text,
    the yielded SSE events MUST contain type: "assistant" with the result text.

    **Validates: Requirements 3.1, 3.5**
    """

    @given(result_text=normal_result_text_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_property_non_error_result_yields_assistant_event(
        self,
        result_text: str,
    ):
        """Property: for ALL ResultMessage with is_error=False and non-empty result,
        yielded SSE events MUST contain type: "assistant" with the result text.

        **Validates: Requirements 3.1**
        """
        agent_manager = AgentManager()

        init_msg = make_init_system_message()
        normal_msg = make_result_message(
            is_error=False,
            result=result_text,
            subtype="result",
            total_cost_usd=0.01,
        )

        events, _ = await collect_events_from_run_query(
            agent_manager,
            messages=[init_msg, normal_msg],
        )

        # Find assistant events containing the result text
        assistant_events_with_text = [
            e for e in events
            if e.get("type") == "assistant"
            and any(
                block.get("text") == result_text
                for block in e.get("content", [])
            )
        ]

        assert len(assistant_events_with_text) > 0, (
            f"Expected at least one SSE event with type='assistant' containing "
            f"result text {result_text!r} for ResultMessage(is_error=False), "
            f"but got none. All events: {events}"
        )


class TestNormalResultContentPersistence:
    """Preservation: ResultMessage with is_error=False must persist content
    via assistant_content.add().

    **Validates: Requirements 3.1**
    """

    @given(result_text=normal_result_text_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_property_non_error_result_persists_content(
        self,
        result_text: str,
    ):
        """Property: for ALL ResultMessage with is_error=False and non-empty result,
        the content MUST be accumulated in assistant_content for DB persistence.

        **Validates: Requirements 3.1**
        """
        agent_manager = AgentManager()

        init_msg = make_init_system_message()
        normal_msg = make_result_message(
            is_error=False,
            result=result_text,
            subtype="result",
            total_cost_usd=0.01,
        )

        _, assistant_content = await collect_events_from_run_query(
            agent_manager,
            messages=[init_msg, normal_msg],
        )

        # Verify content was accumulated for persistence
        accumulated_texts = [
            block.get("text")
            for block in assistant_content.blocks
            if block.get("type") == "text"
        ]

        assert result_text in accumulated_texts, (
            f"Expected result text {result_text!r} to be accumulated in "
            f"assistant_content for DB persistence, but accumulated texts were: "
            f"{accumulated_texts}"
        )


class TestErrorDuringExecutionPreservation:
    """Preservation: ResultMessage with subtype='error_during_execution' must
    continue to yield type: "error" and clean up session from _active_sessions.

    **Validates: Requirements 3.2**
    """

    @given(error_text=error_execution_text_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_property_error_during_execution_yields_error_event(
        self,
        error_text: str,
    ):
        """Property: for ALL ResultMessage with subtype='error_during_execution',
        yielded SSE events MUST contain type: "error" with the error text.

        **Validates: Requirements 3.2**
        """
        agent_manager = AgentManager()

        session_id = "exec-error-session-789"
        init_msg = make_init_system_message(session_id=session_id)
        error_msg = make_result_message(
            is_error=True,
            result=error_text,
            subtype="error_during_execution",
            total_cost_usd=0,
        )

        # Pre-populate _active_sessions so cleanup can be verified
        agent_manager._active_sessions[session_id] = {
            "client": MagicMock(),
            "wrapper": MagicMock(__aexit__=AsyncMock(return_value=False)),
            "created_at": 0,
            "last_used": 0,
        }

        events, _ = await collect_events_from_run_query(
            agent_manager,
            messages=[init_msg, error_msg],
            session_id=session_id,
        )

        # Must yield an error event
        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) > 0, (
            f"Expected at least one SSE event with type='error' for "
            f"ResultMessage(subtype='error_during_execution', result={error_text!r}), "
            f"but got none. All events: {events}"
        )

        # Session must be cleaned from _active_sessions
        assert session_id not in agent_manager._active_sessions, (
            f"Session {session_id} should be cleaned from _active_sessions after "
            f"error_during_execution, but it was still present."
        )


class TestBedrockAuthPreservation:
    """Preservation: Bedrock-configured environments (no API key, use_bedrock=True)
    must pass pre-flight validation without error.

    **Validates: Requirements 3.4**
    """

    @pytest.mark.asyncio
    async def test_bedrock_configured_no_api_key_passes_validation(self):
        """Bedrock auth (use_bedrock=True) must work without ANTHROPIC_API_KEY.

        The _configure_claude_environment function should set CLAUDE_CODE_USE_BEDROCK
        and NOT raise an error when no API key is present but Bedrock is enabled.

        **Validates: Requirements 3.4**
        """
        from core.claude_environment import _configure_claude_environment
        from core.app_config_manager import AppConfigManager

        # Save and clear any existing env vars
        saved_env = {}
        for key in ["ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "AWS_REGION"]:
            saved_env[key] = os.environ.get(key)
            os.environ.pop(key, None)

        try:
            # Create an AppConfigManager with Bedrock enabled, no API key
            config = AppConfigManager.__new__(AppConfigManager)
            config._cache = {
                "use_bedrock": True,
                "aws_region": "us-east-1",
                "anthropic_base_url": None,
                "claude_code_disable_experimental_betas": True,
            }

            # Should NOT raise any exception
            _configure_claude_environment(config)

            # Verify Bedrock env var is set
            assert os.environ.get("CLAUDE_CODE_USE_BEDROCK") == "true", (
                "Expected CLAUDE_CODE_USE_BEDROCK to be set to 'true' when "
                "use_bedrock=True in settings"
            )
        finally:
            # Restore original env vars
            for key, val in saved_env.items():
                if val is not None:
                    os.environ[key] = val
                else:
                    os.environ.pop(key, None)


class TestSuccessfulSessionStoragePreservation:
    """Preservation: Successful conversations must continue to store sessions
    in _active_sessions for future resume calls.

    **Validates: Requirements 3.3**
    """

    @pytest.mark.asyncio
    async def test_successful_conversation_stores_session(self):
        """After a successful conversation (is_error=False), the session MUST
        be stored in _active_sessions for future resume.

        **Validates: Requirements 3.3**
        """
        agent_manager = AgentManager()

        session_id = "success-session-001"
        init_msg = make_init_system_message(session_id=session_id)
        success_msg = make_result_message(
            is_error=False,
            result="Here is the answer to your question.",
            subtype="result",
            total_cost_usd=0.05,
        )

        # Run through _execute_on_session to test full session storage path
        with patch("core.agent_manager._configure_claude_environment", new_callable=MagicMock):
            mock_options = MagicMock()
            mock_options.allowed_tools = []
            mock_options.permission_mode = "default"
            mock_options.mcp_servers = None
            mock_options.cwd = "/tmp"

            with patch.object(agent_manager, "_build_options", new_callable=AsyncMock, return_value=mock_options):
                mock_client = AsyncMock()
                mock_client.query = AsyncMock()

                async def mock_receive_response():
                    yield init_msg
                    yield success_msg

                mock_client.receive_response = mock_receive_response

                mock_wrapper = MagicMock()
                mock_wrapper.__aenter__ = AsyncMock(return_value=mock_client)
                mock_wrapper.__aexit__ = AsyncMock(return_value=False)

                with patch("core.agent_manager._ClaudeClientWrapper", return_value=mock_wrapper):
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

        # The session MUST be stored in _active_sessions
        assert session_id in agent_manager._active_sessions, (
            f"Session {session_id} should be stored in _active_sessions after a "
            f"successful conversation, but it was not found. "
            f"Active sessions: {list(agent_manager._active_sessions.keys())}"
        )

        # Verify the stored session has the expected structure
        stored = agent_manager._active_sessions[session_id]
        assert "client" in stored, "Stored session must contain 'client'"
        assert "wrapper" in stored, "Stored session must contain 'wrapper'"
        assert "created_at" in stored, "Stored session must contain 'created_at'"
