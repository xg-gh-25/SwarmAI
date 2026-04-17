"""Tests for Slack HTTP polling fallback mode.

TDD RED phase: these tests define the expected behavior when
Socket Mode WebSocket fails and the adapter switches to HTTP polling
via the slack-mcp bridge.

Acceptance criteria tested:
  AC1: WebSocket failure triggers auto-switch to polling
  AC2: Polling via slack-mcp get_messages retrieves new messages
  AC3: Messages processed through same _on_message pipeline
  AC4: Auto-switch back on WebSocket recovery
  AC5: Configurable polling interval, no rate limit issues
"""

import asyncio
import json
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# Import target classes
from channels.adapters.slack import SlackChannelAdapter, SlackMcpBridge
from channels.base import InboundMessage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_adapter(on_message=None, config=None) -> SlackChannelAdapter:
    """Create a SlackChannelAdapter with test config."""
    default_config = {
        "bot_token": "xoxb-test-token-1234",
        "app_token": "xapp-test-token-5678",
        "polling_interval": 2,  # fast for tests
    }
    if config:
        default_config.update(config)
    return SlackChannelAdapter(
        channel_id="test-channel-id",
        config=default_config,
        on_message=on_message or AsyncMock(),
    )


def _make_slack_message(text: str, user: str = "U_TESTER", ts: str = "1700000000.000001") -> dict:
    """Build a Slack message dict as returned by get_messages."""
    return {
        "text": text,
        "user": user,
        "channel": "D_TEST_DM",
        "ts": ts,
        "channel_type": "im",
    }


# ---------------------------------------------------------------------------
# AC1: WebSocket failure triggers auto-switch to polling
# ---------------------------------------------------------------------------

class TestAC1_WebSocketFailureTriggersPollSwitch:
    """When Socket Mode WebSocket fails with BrokenPipeError or connection
    refused, the adapter should automatically switch to HTTP polling mode."""

    def test_connection_mode_default_is_websocket(self):
        adapter = _make_adapter()
        assert adapter._connection_mode == "websocket"

    def test_switch_to_polling_on_ws_failure(self):
        """After WebSocket fails, connection_mode should become 'polling'."""
        adapter = _make_adapter()
        adapter._connection_mode = "websocket"
        adapter._loop = asyncio.new_event_loop()

        # Simulate WebSocket failure notification
        adapter._on_ws_failure("BrokenPipeError: connection lost")

        assert adapter._connection_mode == "polling"

    def test_polling_thread_starts_on_switch(self):
        """When switching to polling, a polling thread should be started."""
        adapter = _make_adapter()
        adapter._loop = asyncio.new_event_loop()
        adapter._connection_mode = "websocket"

        adapter._on_ws_failure("BrokenPipeError")

        assert adapter._polling_thread is not None
        assert adapter._polling_thread.is_alive() or adapter._connection_mode == "polling"

        # Cleanup
        adapter._stopped = True
        if adapter._polling_thread and adapter._polling_thread.is_alive():
            adapter._polling_thread.join(timeout=3)
        adapter._loop.close()


# ---------------------------------------------------------------------------
# AC2: Polling via slack-mcp get_messages retrieves new messages
# ---------------------------------------------------------------------------

class TestAC2_PollingRetrievesMessages:
    """HTTP polling should use slack-mcp get_messages to fetch new messages
    and filter by timestamp to avoid duplicates."""

    def test_poll_calls_mcp_get_messages(self):
        """_poll_once should call MCP bridge with get_messages tool."""
        adapter = _make_adapter()
        adapter._mcp_bridge = MagicMock(spec=SlackMcpBridge)
        adapter._mcp_bridge.call_tool.return_value = {
            "content": [{"type": "text", "text": json.dumps({"messages": []})}]
        }

        adapter._poll_once("D_TEST_DM")

        adapter._mcp_bridge.call_tool.assert_called_once()
        call_args = adapter._mcp_bridge.call_tool.call_args
        assert call_args[0][0] == "get_messages"  # tool name

    def test_poll_filters_old_messages(self):
        """Messages with ts <= _last_seen_ts should be skipped."""
        adapter = _make_adapter()
        adapter._last_seen_ts = "1700000000.000010"
        adapter._mcp_bridge = MagicMock(spec=SlackMcpBridge)

        old_msg = _make_slack_message("old", ts="1700000000.000005")
        new_msg = _make_slack_message("new", ts="1700000000.000020")
        adapter._mcp_bridge.call_tool.return_value = {
            "content": [{"type": "text", "text": json.dumps({
                "messages": [old_msg, new_msg]
            })}]
        }

        results = adapter._poll_once("D_TEST_DM")

        # Only the new message should be returned
        assert len(results) == 1
        assert results[0]["text"] == "new"

    def test_poll_updates_last_seen_ts(self):
        """After processing, _last_seen_ts should be updated to newest ts."""
        adapter = _make_adapter()
        adapter._last_seen_ts = "1700000000.000001"
        adapter._mcp_bridge = MagicMock(spec=SlackMcpBridge)

        msg = _make_slack_message("hello", ts="1700000000.000050")
        adapter._mcp_bridge.call_tool.return_value = {
            "content": [{"type": "text", "text": json.dumps({
                "messages": [msg]
            })}]
        }

        adapter._poll_once("D_TEST_DM")

        assert adapter._last_seen_ts == "1700000000.000050"


# ---------------------------------------------------------------------------
# AC3: Messages processed through same _on_message pipeline
# ---------------------------------------------------------------------------

class TestAC3_MessagesThroughSamePipeline:
    """Polled messages should be converted to InboundMessage and routed
    through the same _on_message callback as WebSocket messages."""

    def test_polled_message_creates_inbound_message(self):
        """A polled Slack message should become an InboundMessage."""
        on_message = AsyncMock()
        adapter = _make_adapter(on_message=on_message)
        adapter._loop = asyncio.new_event_loop()

        msg = _make_slack_message("hello from poll", user="U_ALICE", ts="1700000000.000099")

        adapter._process_polled_message(msg)

        # Give call_soon_threadsafe a moment
        adapter._loop.run_until_complete(asyncio.sleep(0.1))

        assert on_message.called
        inbound = on_message.call_args[0][0]
        assert isinstance(inbound, InboundMessage)
        assert inbound.text == "hello from poll"
        assert inbound.external_sender_id == "U_ALICE"

        adapter._loop.close()

    def test_polled_bot_messages_are_skipped(self):
        """Messages with bot_id should be skipped (same as WebSocket path)."""
        on_message = AsyncMock()
        adapter = _make_adapter(on_message=on_message)
        adapter._loop = asyncio.new_event_loop()

        msg = _make_slack_message("bot says hi")
        msg["bot_id"] = "B_SOME_BOT"

        adapter._process_polled_message(msg)
        adapter._loop.run_until_complete(asyncio.sleep(0.1))

        assert not on_message.called

        adapter._loop.close()


# ---------------------------------------------------------------------------
# AC4: Auto-switch back on WebSocket recovery
# ---------------------------------------------------------------------------

class TestAC4_AutoSwitchBackToWebSocket:
    """While in polling mode, periodically attempt WebSocket reconnection.
    On success, switch back to WebSocket mode and stop polling."""

    def test_ws_recovery_check_interval(self):
        """Adapter should have a configurable WS recovery check interval."""
        adapter = _make_adapter()
        # Default should be 60 seconds
        assert adapter._ws_recovery_interval >= 30

    def test_switch_back_to_websocket_on_recovery(self):
        """When WebSocket reconnects, mode should switch back."""
        adapter = _make_adapter()
        adapter._connection_mode = "polling"

        # Simulate successful WS recovery
        adapter._on_ws_recovered()

        assert adapter._connection_mode == "websocket"


# ---------------------------------------------------------------------------
# AC5: Configurable polling interval, no rate limit issues
# ---------------------------------------------------------------------------

class TestAC5_ConfigurablePollingInterval:
    """Polling interval should be configurable and default to a safe value."""

    def test_default_polling_interval(self):
        # Don't pass polling_interval — let the default (5s) apply
        adapter = SlackChannelAdapter(
            channel_id="test-ch",
            config={"bot_token": "xoxb-x", "app_token": "xapp-x"},
            on_message=AsyncMock(),
        )
        assert adapter._polling_interval >= 3  # default 5s is safe

    def test_custom_polling_interval(self):
        adapter = _make_adapter(config={
            "bot_token": "xoxb-x",
            "app_token": "xapp-x",
            "polling_interval": 10,
        })
        assert adapter._polling_interval == 10

    def test_minimum_polling_interval_enforced(self):
        """Even if config says 0.5s, enforce a minimum to avoid rate limits."""
        adapter = _make_adapter(config={
            "bot_token": "xoxb-x",
            "app_token": "xapp-x",
            "polling_interval": 0.5,
        })
        assert adapter._polling_interval >= 2  # minimum 2s
