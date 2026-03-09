"""Shared test helpers for parallel session property tests.

Provides mock factories and event collectors used by both the fault
condition exploration tests and the preservation property tests for
the parallel-chat-session-blocking bugfix.

Key helpers:

- ``make_init_message``           — Create a SystemMessage init event
- ``make_result_message``         — Create a successful ResultMessage
- ``build_mock_agent_manager``    — AgentManager with mocked dependencies
- ``build_mock_client_wrapper``   — Mock client factory yielding slow responses
- ``collect_events_new_session``  — Collect SSE events for a new session call
- ``collect_events_resumed``      — Collect SSE events for a resumed session call
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from claude_agent_sdk import SystemMessage, ResultMessage

from core.agent_manager import AgentManager


def make_init_message(session_id: str) -> SystemMessage:
    """Create a SystemMessage init message to bootstrap session context."""
    return SystemMessage(subtype="init", data={"session_id": session_id})


def make_result_message(session_id: str) -> ResultMessage:
    """Create a successful ResultMessage."""
    return ResultMessage(
        subtype="result",
        duration_ms=100,
        duration_api_ms=80,
        is_error=False,
        num_turns=1,
        session_id=session_id,
        total_cost_usd=0.001,
        result="Hello!",
    )


def build_mock_agent_manager() -> AgentManager:
    """Create an AgentManager with mocked dependencies."""
    mock_config = MagicMock()
    mock_config.get = MagicMock(return_value=False)
    return AgentManager(
        config_manager=mock_config,
        cmd_permission_manager=MagicMock(),
        credential_validator=MagicMock(),
    )


def build_mock_options() -> MagicMock:
    """Create a mock ClaudeAgentOptions object."""
    mock_options = MagicMock()
    mock_options.allowed_tools = []
    mock_options.permission_mode = "default"
    mock_options.mcp_servers = None
    mock_options.cwd = "/tmp"
    return mock_options


def build_mock_client_wrapper(
    session_id_a: str, session_id_b: str, delay: float = 0.1
):
    """Build a mock _ClaudeClientWrapper factory yielding slow responses.

    Returns a side_effect function that produces a different mock client
    for each call, each with its own session_id in the init message.
    """
    call_count = 0

    def wrapper_factory(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        sid = session_id_a if call_count == 1 else session_id_b

        mock_client = AsyncMock()
        mock_client.query = AsyncMock()

        async def mock_receive_response():
            await asyncio.sleep(delay)
            yield make_init_message(sid)
            yield make_result_message(sid)

        mock_client.receive_response = mock_receive_response

        mock_wrapper = MagicMock()
        mock_wrapper.__aenter__ = AsyncMock(return_value=mock_client)
        mock_wrapper.__aexit__ = AsyncMock(return_value=False)
        return mock_wrapper

    return wrapper_factory


async def collect_events_new_session(
    agent_manager: AgentManager, agent_id: str
) -> list[dict]:
    """Collect SSE events from a new-session _execute_on_session call."""
    events = []
    async for event in agent_manager._execute_on_session(
        agent_config={"model": "claude-sonnet-4-20250514"},
        query_content="test message",
        display_text="test message",
        session_id=None,
        enable_skills=False,
        enable_mcp=False,
        is_resuming=False,
        content=None,
        user_message="test message",
        agent_id=agent_id,
    ):
        events.append(event)
    return events


async def collect_events_resumed(
    agent_manager: AgentManager,
    agent_id: str,
    session_id: str | None = None,
    app_session_id: str | None = None,
) -> list[dict]:
    """Collect SSE events from a resumed-session _execute_on_session call."""
    events = []
    async for event in agent_manager._execute_on_session(
        agent_config={"model": "claude-sonnet-4-20250514"},
        query_content="test message",
        display_text="test message",
        session_id=session_id,
        enable_skills=False,
        enable_mcp=False,
        is_resuming=True,
        content=None,
        user_message="test message",
        agent_id=agent_id,
        app_session_id=app_session_id,
    ):
        events.append(event)
    return events
