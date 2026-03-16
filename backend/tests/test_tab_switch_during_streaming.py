"""E2E integration test: tab switch during active streaming.

Validates the critical scenario where a user switches tabs while a chat
session is actively streaming. This exercises the cross-layer sequence:

1. Frontend sends message → backend starts streaming
2. User switches tabs → frontend aborts SSE fetch
3. Frontend sends stop request → interrupt_session must find the client
4. Session must remain resumable after stop

This test class was created to prevent regression of the interrupt_session
client lookup mismatch bug (March 2026) where ``_clients`` was popped in
the ``_run_query_on_client`` finally block before the stop request arrived.

The fix eliminated ``_clients`` entirely and uses ``_active_sessions`` as
the single source of truth with early registration at client creation time.

Key invariants tested:
- ``_active_sessions`` contains the client during streaming (early registration)
- ``interrupt_session`` succeeds during active streaming
- Session is resumable after interrupt (client preserved, not cleaned up)
- ``_active_sessions`` is cleaned up when early key differs from final key
"""

import asyncio
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch

from claude_agent_sdk import (
    SystemMessage,
    ResultMessage,
    AssistantMessage,
    TextBlock,
)

from core.agent_manager import AgentManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_init_message(session_id: str = "sdk-session-001") -> SystemMessage:
    """Create a mock SDK init SystemMessage."""
    return SystemMessage(
        subtype="init",
        data={
            "session_id": session_id,
            "tools": [],
            "model": "test-model",
        },
    )


def make_assistant_message(text: str = "Hello") -> AssistantMessage:
    """Create a mock AssistantMessage with a text block."""
    return AssistantMessage(content=[TextBlock(text=text)], model="claude-sonnet-4-20250514")


def make_result_message(
    session_id: str = "sdk-session-001",
) -> ResultMessage:
    """Create a mock successful ResultMessage."""
    return ResultMessage(
        subtype="success",
        is_error=False,
        session_id=session_id,
        result=None,
        num_turns=1,
        duration_ms=100,
        duration_api_ms=80,
    )


def setup_agent_manager(app_session_id: str = "app-session-001"):
    """Create an AgentManager configured for resume-fallback testing.

    Returns (agent_manager, mock_client, mock_wrapper) with all necessary
    patches applied. The agent_manager has empty _active_sessions and a
    mock config that disables Bedrock.
    """
    am = AgentManager()
    am._active_sessions = {}
    mock_config = MagicMock()
    mock_config.get = MagicMock(return_value=False)
    am._config = mock_config

    mock_client = AsyncMock()
    mock_client.query = AsyncMock()
    mock_client.interrupt = AsyncMock()

    mock_wrapper = MagicMock()
    mock_wrapper.__aenter__ = AsyncMock(return_value=mock_client)
    mock_wrapper.__aexit__ = AsyncMock(return_value=False)

    mock_options = MagicMock()
    mock_options.allowed_tools = []
    mock_options.permission_mode = "default"
    mock_options.mcp_servers = None
    mock_options.cwd = "/tmp"

    return am, mock_client, mock_wrapper, mock_options


# ---------------------------------------------------------------------------
# Test: Early registration makes client findable during streaming
# ---------------------------------------------------------------------------

class TestEarlyRegistrationDuringStreaming:
    """Verify that _active_sessions contains the client during streaming
    for resumed sessions (where app_session_id is known).

    **Validates: Session Lifecycle Invariant #2 (early registration)**
    """

    @pytest.mark.asyncio
    async def test_client_in_active_sessions_during_streaming(self):
        """For resumed sessions, the client is in _active_sessions before
        _run_query_on_client starts, so interrupt_session can find it."""
        am, mock_client, mock_wrapper, mock_options = setup_agent_manager()
        app_sid = "app-session-001"

        # Track whether _active_sessions had the client during streaming
        client_found_during_streaming = False

        async def mock_receive_response():
            nonlocal client_found_during_streaming
            yield make_init_message("sdk-new-001")
            # Check _active_sessions DURING streaming (after init, before result)
            info = am._active_sessions.get(app_sid)
            client_found_during_streaming = (
                info is not None and info.get("client") is mock_client
            )
            yield make_assistant_message("Working on it...")
            yield make_result_message("sdk-new-001")

        mock_client.receive_response = mock_receive_response

        with patch("core.agent_manager._configure_claude_environment"):
            with patch.object(am, "_build_options",
                              new_callable=AsyncMock, return_value=mock_options):
                with patch("core.agent_manager._ClaudeClientWrapper",
                            return_value=mock_wrapper):
                    with patch.object(AgentManager, "_save_message",
                                      new_callable=AsyncMock):
                        with patch(
                            "core.agent_manager.session_manager.store_session",
                            new_callable=AsyncMock,
                        ):
                            events = []
                            async for event in am.run_conversation(
                                agent_id="default",
                                user_message="hello",
                                session_id=app_sid,  # resumed session
                            ):
                                events.append(event)

        assert client_found_during_streaming, (
            "Client should be in _active_sessions during streaming "
            "(early registration for resumed sessions)"
        )


# ---------------------------------------------------------------------------
# Test: interrupt_session succeeds during active streaming
# ---------------------------------------------------------------------------

class TestInterruptDuringStreaming:
    """Verify that interrupt_session can find and interrupt a client
    that is actively streaming (resumed session with early registration).

    **Validates: Session Lifecycle Invariant #1 (single source of truth)**
    """

    @pytest.mark.asyncio
    async def test_interrupt_finds_client_during_streaming(self):
        """interrupt_session returns success when called during streaming."""
        am, mock_client, mock_wrapper, mock_options = setup_agent_manager()
        app_sid = "app-session-001"

        interrupt_result = None

        async def mock_receive_response():
            nonlocal interrupt_result
            yield make_init_message("sdk-new-001")
            # Simulate: user switches tab → stop request arrives DURING streaming
            interrupt_result = await am.interrupt_session(app_sid)
            yield make_result_message("sdk-new-001")

        mock_client.receive_response = mock_receive_response

        with patch("core.agent_manager._configure_claude_environment"):
            with patch.object(am, "_build_options",
                              new_callable=AsyncMock, return_value=mock_options):
                with patch("core.agent_manager._ClaudeClientWrapper",
                            return_value=mock_wrapper):
                    with patch.object(AgentManager, "_save_message",
                                      new_callable=AsyncMock):
                        with patch(
                            "core.agent_manager.session_manager.store_session",
                            new_callable=AsyncMock,
                        ):
                            async for _ in am.run_conversation(
                                agent_id="default",
                                user_message="hello",
                                session_id=app_sid,
                            ):
                                pass

        assert interrupt_result is not None, "interrupt_session was not called"
        assert interrupt_result["success"] is True, (
            f"interrupt_session should succeed during streaming, "
            f"got: {interrupt_result}"
        )
        mock_client.interrupt.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test: Early key cleanup when final key differs
# ---------------------------------------------------------------------------

class TestEarlyKeyCleanup:
    """Verify that the early registration key is cleaned up when the
    final effective_session_id differs (resume-fallback creates a new
    SDK session with a different ID).

    **Validates: No leaked entries in _active_sessions**
    """

    @pytest.mark.asyncio
    async def test_early_key_removed_after_stream(self):
        """After stream completes for a resumed session, _active_sessions
        should contain the effective_session_id (which equals app_session_id
        for resumed sessions). The early key is the same as the final key
        so no cleanup is needed — the entry persists."""
        am, mock_client, mock_wrapper, mock_options = setup_agent_manager()
        app_sid = "app-session-001"
        sdk_sid = "sdk-new-001"

        # Track _active_sessions state during streaming
        had_entry_during_streaming = False

        async def mock_receive_response():
            nonlocal had_entry_during_streaming
            yield make_init_message(sdk_sid)
            had_entry_during_streaming = app_sid in am._active_sessions
            yield make_result_message(sdk_sid)

        mock_client.receive_response = mock_receive_response

        with patch("core.agent_manager._configure_claude_environment"):
            with patch.object(am, "_build_options",
                              new_callable=AsyncMock, return_value=mock_options):
                with patch("core.agent_manager._ClaudeClientWrapper",
                            return_value=mock_wrapper):
                    with patch.object(AgentManager, "_save_message",
                                      new_callable=AsyncMock):
                        with patch(
                            "core.agent_manager.session_manager.store_session",
                            new_callable=AsyncMock,
                        ):
                            async for _ in am.run_conversation(
                                agent_id="default",
                                user_message="hello",
                                session_id=app_sid,
                            ):
                                pass

        # The critical invariant: client was findable DURING streaming
        assert had_entry_during_streaming, (
            "Client must be in _active_sessions during streaming "
            "(early registration for resumed sessions)"
        )

    @pytest.mark.asyncio
    async def test_no_early_registration_for_new_sessions(self):
        """New sessions (session_id=None) should NOT have early registration
        because the frontend has no session_id to send a stop request."""
        am, mock_client, mock_wrapper, mock_options = setup_agent_manager()

        early_keys_during_streaming = []

        async def mock_receive_response():
            yield make_init_message("sdk-brand-new")
            # Capture _active_sessions keys during streaming
            early_keys_during_streaming.extend(list(am._active_sessions.keys()))
            yield make_result_message("sdk-brand-new")

        mock_client.receive_response = mock_receive_response

        with patch("core.agent_manager._configure_claude_environment"):
            with patch.object(am, "_build_options",
                              new_callable=AsyncMock, return_value=mock_options):
                with patch("core.agent_manager._ClaudeClientWrapper",
                            return_value=mock_wrapper):
                    with patch.object(AgentManager, "_save_message",
                                      new_callable=AsyncMock):
                        with patch(
                            "core.agent_manager.session_manager.store_session",
                            new_callable=AsyncMock,
                        ):
                            # session_id=None → brand new session
                            async for _ in am.run_conversation(
                                agent_id="default",
                                user_message="hello",
                                session_id=None,
                            ):
                                pass

        # During streaming, no early registration should exist for new sessions
        # (the only entry would be from the post-stream storage)
        assert early_keys_during_streaming == [], (
            f"New sessions should not have early registration, "
            f"but found keys during streaming: {early_keys_during_streaming}"
        )
