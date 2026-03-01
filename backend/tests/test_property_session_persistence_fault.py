"""Property-based tests for session ID persistence fault condition on backend restart.

**Bugfix: chat-message-persistence-on-restart, Properties 1, 5, 6, 7**

Tests that when the backend restarts (losing ``_active_sessions``), a tab
resuming a conversation with a previously valid ``session_id`` does NOT
trigger a session ID replacement cascade.

This is a BUG CONDITION EXPLORATION test. On UNFIXED code, these tests are
EXPECTED TO FAIL — failure confirms the bug exists. The tests encode the
EXPECTED (correct) behavior and will pass once the fix is implemented.

Key properties verified:
- ``TestRunConversationResumeFallback``  — Property 1 + 5 (run_conversation)
- ``TestContinueWithAnswerResumeFallback`` — Property 6 (continue_with_answer)
- ``TestSkillCreatorResumeFallback``     — Property 7 (run_skill_creator_conversation)

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 2.5**
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from hypothesis import given, strategies as st, settings, HealthCheck

from claude_agent_sdk import ResultMessage, SystemMessage

from core.agent_manager import AgentManager


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

# Session IDs: non-empty printable strings (no control chars or whitespace-only)
session_id_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=1,
    max_size=50,
).filter(lambda x: x.strip())

# User message content: non-empty text
message_content_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=200,
).filter(lambda x: x.strip())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_init_system_message(session_id: str) -> SystemMessage:
    """Create a SystemMessage init message with the given SDK session ID."""
    return SystemMessage(
        subtype="init",
        data={"session_id": session_id},
    )


def make_result_message(session_id: str) -> ResultMessage:
    """Create a normal (non-error) ResultMessage signaling conversation complete."""
    return ResultMessage(
        subtype="result",
        duration_ms=100,
        duration_api_ms=80,
        is_error=False,
        num_turns=1,
        session_id=session_id,
        total_cost_usd=0.01,
        result="This is the assistant response.",
    )


async def run_conversation_and_collect(
    original_session_id: str,
    user_msg: str,
    sdk_new_session_id: str = "sdk-new-id-999",
) -> dict:
    """Simulate run_conversation with a resumed session after backend restart.

    Sets up an AgentManager with EMPTY _active_sessions (simulating restart),
    mocks ClaudeSDKClient to emit an init message with a NEW SDK session ID,
    and collects all SSE events + _save_message calls.

    Returns a dict with:
      - events: list of all yielded SSE events
      - save_calls: list of (session_id, role, content) tuples from _save_message
      - agent_manager: the AgentManager instance (to inspect _active_sessions)
    """
    agent_manager = AgentManager()
    # Ensure _active_sessions is EMPTY (simulates backend restart)
    agent_manager._active_sessions = {}
    agent_manager._clients = {}
    # Provide mock config so _execute_on_session doesn't crash on
    # self._config.get("use_bedrock") during Bedrock pre-flight check
    mock_config = MagicMock()
    mock_config.get = MagicMock(return_value=False)
    agent_manager._config = mock_config

    save_calls = []

    async def tracking_save(self, session_id, role, content, model=None):
        save_calls.append((session_id, role, content))
        # Don't actually write to DB — just track calls
        return {"id": "mock-msg-id", "session_id": session_id,
                "role": role, "content": content}

    # Mock SDK messages: init with NEW session ID, then a result
    init_msg = make_init_system_message(sdk_new_session_id)
    result_msg = make_result_message(sdk_new_session_id)

    mock_client = AsyncMock()
    mock_client.query = AsyncMock()

    async def mock_receive_response():
        yield init_msg
        yield result_msg

    mock_client.receive_response = mock_receive_response

    mock_wrapper = MagicMock()
    mock_wrapper.__aenter__ = AsyncMock(return_value=mock_client)
    mock_wrapper.__aexit__ = AsyncMock(return_value=False)

    mock_options = MagicMock()
    mock_options.allowed_tools = []
    mock_options.permission_mode = "default"
    mock_options.mcp_servers = None
    mock_options.cwd = "/tmp"

    events = []
    with patch("core.agent_manager._configure_claude_environment"):
        with patch.object(agent_manager, "_build_options",
                          new_callable=AsyncMock, return_value=mock_options):
            with patch("core.agent_manager._ClaudeClientWrapper",
                        return_value=mock_wrapper):
                with patch.object(AgentManager, "_save_message",
                                  tracking_save):
                    with patch(
                        "core.agent_manager.session_manager.store_session",
                        new_callable=AsyncMock,
                    ):
                        async for event in agent_manager.run_conversation(
                            agent_id="default",
                            user_message=user_msg,
                            session_id=original_session_id,
                        ):
                            events.append(event)

    return {
        "events": events,
        "save_calls": save_calls,
        "agent_manager": agent_manager,
    }


async def continue_with_answer_and_collect(
    original_session_id: str,
    answer_text: str,
    sdk_new_session_id: str = "sdk-new-id-999",
) -> dict:
    """Simulate continue_with_answer with a resumed session after restart.

    Same setup as run_conversation_and_collect but calls continue_with_answer.
    """
    agent_manager = AgentManager()
    agent_manager._active_sessions = {}
    agent_manager._clients = {}
    # Provide mock config so _execute_on_session doesn't crash on
    # self._config.get("use_bedrock") during Bedrock pre-flight check
    mock_config = MagicMock()
    mock_config.get = MagicMock(return_value=False)
    agent_manager._config = mock_config

    save_calls = []

    async def tracking_save(self, session_id, role, content, model=None):
        save_calls.append((session_id, role, content))
        return {"id": "mock-msg-id", "session_id": session_id,
                "role": role, "content": content}

    init_msg = make_init_system_message(sdk_new_session_id)
    result_msg = make_result_message(sdk_new_session_id)

    mock_client = AsyncMock()
    mock_client.query = AsyncMock()

    async def mock_receive_response():
        yield init_msg
        yield result_msg

    mock_client.receive_response = mock_receive_response

    mock_wrapper = MagicMock()
    mock_wrapper.__aenter__ = AsyncMock(return_value=mock_client)
    mock_wrapper.__aexit__ = AsyncMock(return_value=False)

    mock_options = MagicMock()
    mock_options.allowed_tools = []
    mock_options.permission_mode = "default"
    mock_options.mcp_servers = None
    mock_options.cwd = "/tmp"

    events = []
    with patch("core.agent_manager._configure_claude_environment"):
        with patch.object(agent_manager, "_build_options",
                          new_callable=AsyncMock, return_value=mock_options):
            with patch("core.agent_manager._ClaudeClientWrapper",
                        return_value=mock_wrapper):
                with patch.object(AgentManager, "_save_message",
                                  tracking_save):
                    with patch(
                        "core.agent_manager.session_manager.store_session",
                        new_callable=AsyncMock,
                    ):
                        async for event in agent_manager.continue_with_answer(
                            agent_id="default",
                            session_id=original_session_id,
                            tool_use_id="tool-use-123",
                            answers={"question": answer_text},
                        ):
                            events.append(event)

    return {
        "events": events,
        "save_calls": save_calls,
        "agent_manager": agent_manager,
    }


async def skill_creator_and_collect(
    original_session_id: str,
    user_msg: str,
    sdk_new_session_id: str = "sdk-new-id-999",
) -> dict:
    """Simulate run_skill_creator_conversation with resumed session after restart.

    Same setup as run_conversation_and_collect but calls
    run_skill_creator_conversation.
    """
    agent_manager = AgentManager()
    agent_manager._active_sessions = {}
    agent_manager._clients = {}

    save_calls = []

    async def tracking_save(self, session_id, role, content, model=None):
        save_calls.append((session_id, role, content))
        return {"id": "mock-msg-id", "session_id": session_id,
                "role": role, "content": content}

    init_msg = make_init_system_message(sdk_new_session_id)
    result_msg = make_result_message(sdk_new_session_id)

    mock_client = AsyncMock()
    mock_client.query = AsyncMock()

    async def mock_receive_response():
        yield init_msg
        yield result_msg

    mock_client.receive_response = mock_receive_response

    mock_wrapper = MagicMock()
    mock_wrapper.__aenter__ = AsyncMock(return_value=mock_client)
    mock_wrapper.__aexit__ = AsyncMock(return_value=False)

    mock_options = MagicMock()
    mock_options.allowed_tools = []
    mock_options.permission_mode = "default"
    mock_options.mcp_servers = None
    mock_options.cwd = "/tmp"

    events = []
    with patch("core.agent_manager._configure_claude_environment"):
        with patch.object(agent_manager, "_build_options",
                          new_callable=AsyncMock, return_value=mock_options):
            with patch("core.agent_manager._ClaudeClientWrapper",
                        return_value=mock_wrapper):
                with patch.object(AgentManager, "_save_message",
                                  tracking_save):
                    with patch(
                        "core.agent_manager.session_manager.store_session",
                        new_callable=AsyncMock,
                    ):
                        async for ev in agent_manager.run_skill_creator_conversation(
                            skill_name="test-skill",
                            skill_description="A test skill",
                            user_message=user_msg,
                            session_id=original_session_id,
                        ):
                            events.append(ev)

    return {
        "events": events,
        "save_calls": save_calls,
        "agent_manager": agent_manager,
    }


# ---------------------------------------------------------------------------
# Property Tests — Fault Condition Exploration
# ---------------------------------------------------------------------------


class TestRunConversationResumeFallback:
    """Property 1 + 5: Session ID Stability and _active_sessions Keying.

    For any run_conversation call where is_resuming=True and no active
    in-memory client exists (backend restart), the code SHALL:
    - Emit exactly ONE session_start event with the ORIGINAL session ID
    - Save user message exactly ONCE under the original session ID
    - Save assistant response under the original session ID
    - Key _active_sessions by the original session ID

    On UNFIXED code these assertions FAIL — confirming the bug.

    **Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 2.5**
    """

    @pytest.mark.asyncio
    async def test_single_session_start_with_original_id(self):
        """Concrete case: resume with 'original-abc', SDK assigns 'sdk-new-xyz'.

        **Validates: Requirements 2.1, 2.5**
        """
        result = await run_conversation_and_collect(
            original_session_id="original-abc",
            user_msg="Hello after restart",
            sdk_new_session_id="sdk-new-xyz",
        )

        session_starts = [
            e for e in result["events"]
            if e.get("type") == "session_start"
        ]

        # EXPECTED: exactly one session_start
        assert len(session_starts) == 1, (
            f"Expected exactly 1 session_start event, got {len(session_starts)}: "
            f"{session_starts}"
        )
        # EXPECTED: session_start contains original ID, not SDK's new ID
        assert session_starts[0]["sessionId"] == "original-abc", (
            f"Expected session_start with 'original-abc', "
            f"got '{session_starts[0]['sessionId']}'"
        )


    @pytest.mark.asyncio
    async def test_user_message_saved_once_under_original_id(self):
        """User message must be saved exactly once under original session ID.

        **Validates: Requirements 2.3**
        """
        result = await run_conversation_and_collect(
            original_session_id="original-abc",
            user_msg="Hello after restart",
            sdk_new_session_id="sdk-new-xyz",
        )

        user_saves = [
            (sid, role, content)
            for sid, role, content in result["save_calls"]
            if role == "user"
        ]

        assert len(user_saves) == 1, (
            f"Expected exactly 1 user message save, got {len(user_saves)}: "
            f"{user_saves}"
        )
        assert user_saves[0][0] == "original-abc", (
            f"Expected user message saved under 'original-abc', "
            f"got '{user_saves[0][0]}'"
        )

    @pytest.mark.asyncio
    async def test_assistant_response_saved_under_original_id(self):
        """Assistant response must be saved under original session ID.

        **Validates: Requirements 2.3**
        """
        result = await run_conversation_and_collect(
            original_session_id="original-abc",
            user_msg="Hello after restart",
            sdk_new_session_id="sdk-new-xyz",
        )

        assistant_saves = [
            (sid, role, content)
            for sid, role, content in result["save_calls"]
            if role == "assistant"
        ]

        assert len(assistant_saves) >= 1, (
            f"Expected at least 1 assistant message save, "
            f"got {len(assistant_saves)}"
        )
        for sid, role, content in assistant_saves:
            assert sid == "original-abc", (
                f"Expected assistant message saved under 'original-abc', "
                f"got '{sid}'"
            )


    @pytest.mark.asyncio
    async def test_active_sessions_keyed_by_original_id(self):
        """_active_sessions must be keyed by original session ID, not SDK's.

        **Validates: Requirements 2.2, 2.4**
        """
        result = await run_conversation_and_collect(
            original_session_id="original-abc",
            user_msg="Hello after restart",
            sdk_new_session_id="sdk-new-xyz",
        )

        am = result["agent_manager"]

        assert "original-abc" in am._active_sessions, (
            f"Expected _active_sessions to contain 'original-abc', "
            f"but keys are: {list(am._active_sessions.keys())}"
        )
        assert "sdk-new-xyz" not in am._active_sessions, (
            f"Expected _active_sessions to NOT contain SDK ID 'sdk-new-xyz', "
            f"but it does: {list(am._active_sessions.keys())}"
        )

    @given(
        original_id=session_id_strategy,
        user_msg=message_content_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_property_session_id_stability_on_resume_fallback(
        self,
        original_id: str,
        user_msg: str,
    ):
        """Property 1+5: For ALL session IDs and messages, resume-fallback
        must preserve the original session ID everywhere.

        Uses Hypothesis to generalize beyond hardcoded values.

        **Validates: Requirements 2.1, 2.2, 2.3, 2.5**
        """
        # Use a deterministic SDK ID that differs from original
        sdk_id = f"sdk-replaced-{hash(original_id) % 10000}"

        result = await run_conversation_and_collect(
            original_session_id=original_id,
            user_msg=user_msg,
            sdk_new_session_id=sdk_id,
        )

        # Property 1: Exactly one session_start with original ID
        session_starts = [
            e for e in result["events"]
            if e.get("type") == "session_start"
        ]
        assert len(session_starts) == 1, (
            f"Expected 1 session_start, got {len(session_starts)}: "
            f"{session_starts}"
        )
        assert session_starts[0]["sessionId"] == original_id, (
            f"session_start has '{session_starts[0]['sessionId']}', "
            f"expected '{original_id}'"
        )

        # Property 2: User message saved exactly once under original ID
        user_saves = [
            s for s in result["save_calls"] if s[1] == "user"
        ]
        assert len(user_saves) == 1, (
            f"Expected 1 user save, got {len(user_saves)}: {user_saves}"
        )
        assert user_saves[0][0] == original_id

        # Property 5: _active_sessions keyed by original ID
        am = result["agent_manager"]
        assert original_id in am._active_sessions, (
            f"_active_sessions missing '{original_id}', "
            f"keys: {list(am._active_sessions.keys())}"
        )


class TestContinueWithAnswerResumeFallback:
    """Property 6: continue_with_answer Resume-Fallback.

    For any continue_with_answer call where is_resuming=True and no active
    in-memory client exists, the code SHALL save the user answer exactly
    once under the ORIGINAL session ID and use it for all downstream
    persistence.

    On UNFIXED code these assertions FAIL — confirming the bug.

    **Validates: Requirements 2.1, 2.2, 2.3**
    """

    @pytest.mark.asyncio
    async def test_user_answer_saved_once_under_original_id(self):
        """Concrete case: answer with 'original-abc', SDK assigns new ID.

        **Validates: Requirements 2.1, 2.3**
        """
        result = await continue_with_answer_and_collect(
            original_session_id="original-abc",
            answer_text="Yes, proceed",
            sdk_new_session_id="sdk-new-xyz",
        )

        user_saves = [
            (sid, role, content)
            for sid, role, content in result["save_calls"]
            if role == "user"
        ]

        assert len(user_saves) == 1, (
            f"Expected exactly 1 user answer save, got {len(user_saves)}: "
            f"{user_saves}"
        )
        assert user_saves[0][0] == "original-abc", (
            f"Expected user answer saved under 'original-abc', "
            f"got '{user_saves[0][0]}'"
        )

    @given(
        original_id=session_id_strategy,
        answer=message_content_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_property_continue_answer_session_stability(
        self,
        original_id: str,
        answer: str,
    ):
        """Property 6: For ALL session IDs and answers, resume-fallback
        in continue_with_answer preserves the original session ID.

        **Validates: Requirements 2.1, 2.2, 2.3**
        """
        sdk_id = f"sdk-replaced-{hash(original_id) % 10000}"

        result = await continue_with_answer_and_collect(
            original_session_id=original_id,
            answer_text=answer,
            sdk_new_session_id=sdk_id,
        )

        user_saves = [
            s for s in result["save_calls"] if s[1] == "user"
        ]
        assert len(user_saves) == 1, (
            f"Expected 1 user save, got {len(user_saves)}: {user_saves}"
        )
        assert user_saves[0][0] == original_id


class TestSkillCreatorResumeFallback:
    """Property 7: run_skill_creator_conversation Resume-Fallback.

    For any run_skill_creator_conversation call where is_resuming=True and
    no active in-memory client exists, the code SHALL emit exactly one
    session_start with the original session ID, save messages under it,
    and key _active_sessions by it.

    On UNFIXED code these assertions FAIL — confirming the bug.

    **Validates: Requirements 2.1, 2.2, 2.5**
    """

    @pytest.mark.asyncio
    async def test_single_session_start_with_original_id(self):
        """Concrete case: skill creator resume with 'original-abc'.

        **Validates: Requirements 2.1, 2.5**
        """
        result = await skill_creator_and_collect(
            original_session_id="original-abc",
            user_msg="Create a test skill",
            sdk_new_session_id="sdk-new-xyz",
        )

        session_starts = [
            e for e in result["events"]
            if e.get("type") == "session_start"
        ]

        assert len(session_starts) == 1, (
            f"Expected exactly 1 session_start event, "
            f"got {len(session_starts)}: {session_starts}"
        )
        assert session_starts[0]["sessionId"] == "original-abc", (
            f"Expected session_start with 'original-abc', "
            f"got '{session_starts[0]['sessionId']}'"
        )

    @pytest.mark.asyncio
    async def test_active_sessions_keyed_by_original_id(self):
        """_active_sessions must be keyed by original session ID.

        **Validates: Requirements 2.2**
        """
        result = await skill_creator_and_collect(
            original_session_id="original-abc",
            user_msg="Create a test skill",
            sdk_new_session_id="sdk-new-xyz",
        )

        am = result["agent_manager"]

        assert "original-abc" in am._active_sessions, (
            f"Expected _active_sessions to contain 'original-abc', "
            f"but keys are: {list(am._active_sessions.keys())}"
        )

    @given(
        original_id=session_id_strategy,
        user_msg=message_content_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_property_skill_creator_session_stability(
        self,
        original_id: str,
        user_msg: str,
    ):
        """Property 7: For ALL session IDs and messages, skill creator
        resume-fallback preserves the original session ID.

        **Validates: Requirements 2.1, 2.2, 2.5**
        """
        sdk_id = f"sdk-replaced-{hash(original_id) % 10000}"

        result = await skill_creator_and_collect(
            original_session_id=original_id,
            user_msg=user_msg,
            sdk_new_session_id=sdk_id,
        )

        session_starts = [
            e for e in result["events"]
            if e.get("type") == "session_start"
        ]
        assert len(session_starts) == 1, (
            f"Expected 1 session_start, got {len(session_starts)}: "
            f"{session_starts}"
        )
        assert session_starts[0]["sessionId"] == original_id

        am = result["agent_manager"]
        assert original_id in am._active_sessions, (
            f"_active_sessions missing '{original_id}', "
            f"keys: {list(am._active_sessions.keys())}"
        )
