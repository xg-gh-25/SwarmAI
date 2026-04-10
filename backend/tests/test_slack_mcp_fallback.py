"""Tests for Slack MCP fallback — stdio JSON-RPC pipe to slack-mcp binary.

Validates that when WebClient HTTP calls fail (corp proxy blocking),
the adapter falls back to calling slack-mcp via subprocess stdio.

Acceptance criteria tested:
  AC1: send_message falls back to MCP on proxy/connection errors
  AC2: update_message falls back to MCP on same error class
  AC3: send_typing_indicator falls back to MCP on same error class
  AC4: MCP fallback sends plaintext (Block Kit not supported via MCP)
  AC5: Graceful degradation when slack-mcp binary unavailable
  AC6: Existing WebClient path still works as primary (no regression)
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_adapter(bot_token: str = "xoxb-test", app_token: str = "xapp-test"):
    """Create a SlackChannelAdapter with mocked dependencies."""
    from channels.adapters.slack import SlackChannelAdapter

    adapter = SlackChannelAdapter(
        channel_id="test-channel",
        config={"bot_token": bot_token, "app_token": app_token},
        on_message=AsyncMock(),
    )
    # Wire up a mock WebClient
    adapter._slack_client = MagicMock()
    adapter._loop = asyncio.get_event_loop()
    return adapter


def _make_outbound_message(text: str = "Hello", channel: str = "C123", thread_ts: str | None = None):
    """Create an OutboundMessage for testing."""
    from channels.base import OutboundMessage

    return OutboundMessage(
        channel_id="test-channel",
        external_chat_id=channel,
        external_thread_id=thread_ts,
        text=text,
    )


class _FakeConnectionError(Exception):
    """Simulates a connection error from slack_sdk / urllib3."""
    pass


def _raise_connection_error(**kwargs):
    """Callable that raises a connection error — simulates proxy block."""
    raise ConnectionError("403 Forbidden, blocked-by-allowlist")


def _raise_os_error(**kwargs):
    """Callable that raises OSError — simulates network failure."""
    raise OSError("[Errno 61] Connection refused")


# ---------------------------------------------------------------------------
# AC1: send_message falls back to MCP on proxy/connection errors
# ---------------------------------------------------------------------------

class TestSendMessageMcpFallback:
    """send_message should try WebClient first, then MCP on proxy errors."""

    @pytest.mark.asyncio
    async def test_send_falls_back_to_mcp_on_connection_error(self):
        """When WebClient raises ConnectionError, MCP fallback is attempted."""
        adapter = _make_adapter()
        adapter._slack_client.chat_postMessage.side_effect = _raise_connection_error

        msg = _make_outbound_message("Hello from MCP")

        with patch.object(adapter, '_mcp_post_message', new_callable=AsyncMock, return_value="1234.5678") as mock_mcp:
            result = await adapter.send_message(msg)
            mock_mcp.assert_called_once()
            assert result == "1234.5678"

    @pytest.mark.asyncio
    async def test_send_falls_back_to_mcp_on_os_error(self):
        """When WebClient raises OSError (connection refused), MCP fallback is attempted."""
        adapter = _make_adapter()
        adapter._slack_client.chat_postMessage.side_effect = _raise_os_error

        msg = _make_outbound_message("Hello from MCP")

        with patch.object(adapter, '_mcp_post_message', new_callable=AsyncMock, return_value="1234.5678") as mock_mcp:
            result = await adapter.send_message(msg)
            mock_mcp.assert_called_once()
            assert result == "1234.5678"

    @pytest.mark.asyncio
    async def test_send_passes_text_and_channel_to_mcp(self):
        """MCP fallback receives correct channel, text, and thread_ts."""
        adapter = _make_adapter()
        adapter._slack_client.chat_postMessage.side_effect = _raise_connection_error

        msg = _make_outbound_message("Test message", channel="C999", thread_ts="111.222")

        with patch.object(adapter, '_mcp_post_message', new_callable=AsyncMock, return_value="ts") as mock_mcp:
            await adapter.send_message(msg)
            mock_mcp.assert_called_once_with("C999", "Test message", "111.222")


# ---------------------------------------------------------------------------
# AC2: update_message falls back to MCP on same error class
# ---------------------------------------------------------------------------

class TestUpdateMessageMcpFallback:
    """update_message should fall back to MCP on proxy errors."""

    @pytest.mark.asyncio
    async def test_update_final_falls_back_to_mcp_on_connection_error(self):
        """Final update_message falls back to MCP on ConnectionError."""
        adapter = _make_adapter()
        adapter._slack_client.chat_update.side_effect = _raise_connection_error

        with patch.object(adapter, '_mcp_update_message', new_callable=AsyncMock, return_value=True) as mock_mcp:
            await adapter.update_message("C123", "ts123", "Updated text", is_final=True)
            mock_mcp.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_streaming_falls_back_to_mcp(self):
        """Non-final (streaming) update falls back to MCP on ConnectionError."""
        adapter = _make_adapter()
        adapter._slack_client.chat_update.side_effect = _raise_connection_error

        with patch.object(adapter, '_mcp_update_message', new_callable=AsyncMock, return_value=True) as mock_mcp:
            await adapter.update_message("C123", "ts123", "Streaming text", is_final=False)
            mock_mcp.assert_called_once()


# ---------------------------------------------------------------------------
# AC3: send_typing_indicator falls back to MCP on same error class
# ---------------------------------------------------------------------------

class TestTypingIndicatorMcpFallback:
    """send_typing_indicator should fall back to MCP on proxy errors."""

    @pytest.mark.asyncio
    async def test_typing_falls_back_to_mcp_on_connection_error(self):
        """Typing indicator falls back to MCP on ConnectionError."""
        adapter = _make_adapter()
        adapter._slack_client.chat_postMessage.side_effect = _raise_connection_error

        with patch.object(adapter, '_mcp_post_message', new_callable=AsyncMock, return_value="ts456") as mock_mcp:
            result = await adapter.send_typing_indicator("C123", "thread456")
            assert result == "ts456"
            mock_mcp.assert_called_once()


# ---------------------------------------------------------------------------
# AC4: MCP fallback sends plaintext
# ---------------------------------------------------------------------------

class TestMcpFallbackContent:
    """MCP fallback should send plaintext, not Block Kit blocks."""

    @pytest.mark.asyncio
    async def test_mcp_receives_plaintext_not_blocks(self):
        """MCP fallback receives the raw text, not Block Kit JSON."""
        adapter = _make_adapter()
        adapter._slack_client.chat_postMessage.side_effect = _raise_connection_error

        markdown_text = "## Header\n\n**Bold** text with `code`"
        msg = _make_outbound_message(markdown_text)

        with patch.object(adapter, '_mcp_post_message', new_callable=AsyncMock, return_value="ts") as mock_mcp:
            await adapter.send_message(msg)
            # Should receive the raw text, not mrkdwn-converted blocks
            call_args = mock_mcp.call_args
            assert call_args[0][1] == markdown_text  # second positional arg is text


# ---------------------------------------------------------------------------
# AC5: Graceful degradation when slack-mcp binary unavailable
# ---------------------------------------------------------------------------

class TestMcpGracefulDegradation:
    """When MCP binary is unavailable, adapter should degrade gracefully."""

    @pytest.mark.asyncio
    async def test_send_returns_none_when_mcp_also_fails(self):
        """If both WebClient AND MCP fail, send_message returns None."""
        adapter = _make_adapter()
        adapter._slack_client.chat_postMessage.side_effect = _raise_connection_error

        with patch.object(adapter, '_mcp_post_message', new_callable=AsyncMock, return_value=None):
            result = await adapter.send_message(_make_outbound_message())
            assert result is None

    @pytest.mark.asyncio
    async def test_update_silent_when_mcp_also_fails(self):
        """If both WebClient AND MCP fail, update_message doesn't crash."""
        adapter = _make_adapter()
        adapter._slack_client.chat_update.side_effect = _raise_connection_error

        with patch.object(adapter, '_mcp_update_message', new_callable=AsyncMock, return_value=False):
            # Should not raise — graceful degradation
            await adapter.update_message("C123", "ts", "text", is_final=True)


# ---------------------------------------------------------------------------
# AC6: Existing WebClient path still works (no regression)
# ---------------------------------------------------------------------------

class TestWebClientPrimaryPath:
    """WebClient should remain the primary path when it works."""

    @pytest.mark.asyncio
    async def test_send_uses_webclient_when_no_error(self):
        """send_message uses WebClient directly when it succeeds."""
        adapter = _make_adapter()
        adapter._slack_client.chat_postMessage.return_value = {"ts": "primary.ts"}

        msg = _make_outbound_message("Normal message")

        with patch.object(adapter, '_mcp_post_message', new_callable=AsyncMock) as mock_mcp:
            result = await adapter.send_message(msg)
            assert result == "primary.ts"
            mock_mcp.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_uses_webclient_when_no_error(self):
        """update_message uses WebClient directly when it succeeds."""
        adapter = _make_adapter()
        adapter._slack_client.chat_update.return_value = {"ok": True}

        with patch.object(adapter, '_mcp_update_message', new_callable=AsyncMock) as mock_mcp:
            await adapter.update_message("C123", "ts", "text", is_final=True)
            mock_mcp.assert_not_called()

    @pytest.mark.asyncio
    async def test_typing_uses_webclient_when_no_error(self):
        """send_typing_indicator uses WebClient directly when it succeeds."""
        adapter = _make_adapter()
        adapter._slack_client.chat_postMessage.return_value = {"ts": "typing.ts"}

        with patch.object(adapter, '_mcp_post_message', new_callable=AsyncMock) as mock_mcp:
            result = await adapter.send_typing_indicator("C123")
            assert result == "typing.ts"
            mock_mcp.assert_not_called()


# ---------------------------------------------------------------------------
# SlackMcpBridge unit tests
# ---------------------------------------------------------------------------

class TestSlackMcpBridge:
    """Unit tests for the MCP stdio bridge itself."""

    def test_bridge_init_reads_mcp_config(self):
        """Bridge should read slack-mcp config from mcp-dev.json."""
        from channels.adapters.slack import SlackMcpBridge

        with patch('channels.adapters.slack._find_slack_mcp_config', return_value={
            "command": "/path/to/slack-mcp",
            "args": [],
            "env": {"SSB_INSTANCE_ID": "test", "ENTITY_ID": "test"},
        }):
            bridge = SlackMcpBridge()
            assert bridge._command == "/path/to/slack-mcp"

    def test_bridge_returns_none_when_no_config(self):
        """Bridge should be disabled when slack-mcp config not found."""
        from channels.adapters.slack import SlackMcpBridge

        with patch('channels.adapters.slack._find_slack_mcp_config', return_value=None):
            bridge = SlackMcpBridge()
            assert not bridge.available

    def test_bridge_call_tool_builds_correct_jsonrpc(self):
        """call_tool should build correct JSON-RPC 2.0 request."""
        from channels.adapters.slack import SlackMcpBridge

        bridge = SlackMcpBridge.__new__(SlackMcpBridge)
        bridge._command = "/path/to/slack-mcp"
        bridge._args = []
        bridge._env = {}
        bridge._process = None
        bridge._initialized = False
        bridge._request_id = 0

        request = bridge._build_request("post_message", {"channel_id": "C123", "text": "hi"})
        assert request["jsonrpc"] == "2.0"
        assert request["method"] == "tools/call"
        assert request["params"]["name"] == "post_message"
        assert request["params"]["arguments"]["channel_id"] == "C123"
        assert "id" in request
