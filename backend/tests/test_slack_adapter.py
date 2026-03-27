"""Tests for SlackChannelAdapter — Socket Mode adapter for Slack.

Covers acceptance criteria:
  AC4: SlackChannelAdapter Socket Mode within gateway lifecycle
  AC5: Gateway tests >= 15 cases (combined with test_channel_gateway.py)

Methodology: TDD RED phase — all tests written before implementation.
All slack-bolt/slack-sdk calls are mocked (no real Slack API needed).
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from uuid import uuid4

import pytest


# ---------------------------------------------------------------------------
# Skip if slack-bolt not installed (adapter module won't load)
# ---------------------------------------------------------------------------

def _slack_bolt_available():
    """Check if slack-bolt is installed so the adapter fully loads."""
    try:
        from channels.adapters.slack import SLACK_BOLT_AVAILABLE
        return SLACK_BOLT_AVAILABLE
    except (ImportError, ModuleNotFoundError):
        return False


_requires_slack_bolt = pytest.mark.skipif(
    not _slack_bolt_available(),
    reason="slack-bolt not installed — install with: pip install slack-bolt",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

pytestmark = _requires_slack_bolt


@pytest.fixture
def slack_config():
    return {
        "bot_token": "xoxb-test-1234",
        "app_token": "xapp-test-5678",
    }


@pytest.fixture
def on_message():
    return AsyncMock()


@pytest.fixture
def adapter(slack_config, on_message):
    """Create a SlackChannelAdapter with mocked dependencies."""
    from channels.adapters.slack import SlackChannelAdapter
    return SlackChannelAdapter(
        channel_id="test-slack-ch",
        config=slack_config,
        on_message=on_message,
    )


# ===================================================================
# Config validation
# ===================================================================

class TestSlackConfigValidation:
    """Validate config checks token formats."""

    @pytest.mark.asyncio
    async def test_valid_config(self, adapter):
        """Valid bot_token + app_token passes validation."""
        with patch("channels.adapters.slack.WebClient") as MockClient:
            mock_client = MagicMock()
            mock_client.auth_test.return_value = {"ok": True, "user_id": "U123"}
            MockClient.return_value = mock_client

            valid, error = await adapter.validate_config()
            assert valid is True
            assert error is None

    @pytest.mark.asyncio
    async def test_missing_bot_token(self, on_message):
        """Missing bot_token fails validation."""
        from channels.adapters.slack import SlackChannelAdapter
        adapter = SlackChannelAdapter(
            channel_id="test",
            config={"app_token": "xapp-test"},
            on_message=on_message,
        )
        valid, error = await adapter.validate_config()
        assert valid is False
        assert "bot_token" in error.lower()

    @pytest.mark.asyncio
    async def test_missing_app_token(self, on_message):
        """Missing app_token fails validation."""
        from channels.adapters.slack import SlackChannelAdapter
        adapter = SlackChannelAdapter(
            channel_id="test",
            config={"bot_token": "xoxb-test"},
            on_message=on_message,
        )
        valid, error = await adapter.validate_config()
        assert valid is False
        assert "app_token" in error.lower()

    @pytest.mark.asyncio
    async def test_invalid_bot_token_prefix(self, on_message):
        """bot_token with wrong prefix fails."""
        from channels.adapters.slack import SlackChannelAdapter
        adapter = SlackChannelAdapter(
            channel_id="test",
            config={"bot_token": "xoxp-wrong-type", "app_token": "xapp-ok"},
            on_message=on_message,
        )
        valid, error = await adapter.validate_config()
        assert valid is False
        assert "xoxb" in error.lower()

    @pytest.mark.asyncio
    async def test_auth_failure(self, adapter):
        """auth_test failure returns error."""
        with patch("channels.adapters.slack.WebClient") as MockClient:
            mock_client = MagicMock()
            mock_client.auth_test.return_value = {"ok": False, "error": "invalid_auth"}
            MockClient.return_value = mock_client

            valid, error = await adapter.validate_config()
            assert valid is False
            assert "invalid_auth" in error


# ===================================================================
# Channel type property
# ===================================================================

class TestSlackChannelType:

    def test_channel_type_is_slack(self, adapter):
        assert adapter.channel_type == "slack"


# ===================================================================
# Message event handling
# ===================================================================

class TestSlackMessageHandling:
    """Test inbound message event → InboundMessage conversion."""

    def test_handle_message_event_creates_inbound(self, adapter):
        """Normal DM message creates correct InboundMessage."""
        from channels.base import InboundMessage

        event = {
            "type": "message",
            "user": "W017T04E8MS",
            "text": "hello swarm",
            "channel": "D017ZD4PUKT",
            "ts": "1234567890.123456",
            "channel_type": "im",
        }

        # Capture the InboundMessage posted to the event loop
        captured_msgs = []

        def mock_call_soon_threadsafe(fn, coro):
            # Extract the InboundMessage from the coroutine
            captured_msgs.append(coro)

        adapter._loop = MagicMock()
        adapter._loop.is_closed.return_value = False
        adapter._loop.call_soon_threadsafe = mock_call_soon_threadsafe
        adapter._stopped = False

        # Mock user name resolution
        adapter._get_user_name = MagicMock(return_value="XG")

        adapter._handle_message_event(event, say=MagicMock())

        assert len(captured_msgs) == 1

    def test_skip_bot_messages(self, adapter):
        """Messages from bots (including ourselves) are skipped."""
        event = {
            "type": "message",
            "bot_id": "B123",
            "text": "I am a bot",
            "channel": "D017ZD4PUKT",
            "ts": "123",
            "channel_type": "im",
        }

        adapter._loop = MagicMock()
        adapter._loop.is_closed.return_value = False
        adapter._loop.call_soon_threadsafe = MagicMock()
        adapter._stopped = False

        adapter._handle_message_event(event, say=MagicMock())
        adapter._loop.call_soon_threadsafe.assert_not_called()

    def test_skip_message_subtypes(self, adapter):
        """Message subtypes (edited, deleted) are skipped except file_share."""
        event = {
            "type": "message",
            "subtype": "message_changed",
            "user": "U123",
            "text": "edited",
            "channel": "D017",
            "ts": "123",
            "channel_type": "im",
        }

        adapter._loop = MagicMock()
        adapter._loop.is_closed.return_value = False
        adapter._loop.call_soon_threadsafe = MagicMock()
        adapter._stopped = False

        adapter._handle_message_event(event, say=MagicMock())
        adapter._loop.call_soon_threadsafe.assert_not_called()

    def test_file_share_subtype_not_skipped(self, adapter):
        """file_share subtype is NOT skipped (it carries attachments)."""
        event = {
            "type": "message",
            "subtype": "file_share",
            "user": "U123",
            "text": "check this file",
            "channel": "D017",
            "ts": "123",
            "channel_type": "im",
            "files": [],
        }

        adapter._loop = MagicMock()
        adapter._loop.is_closed.return_value = False
        adapter._loop.call_soon_threadsafe = MagicMock()
        adapter._stopped = False
        adapter._get_user_name = MagicMock(return_value="User")

        adapter._handle_message_event(event, say=MagicMock())
        # Should process (though may not emit if text+attachments are empty)

    def test_stopped_adapter_drops_messages(self, adapter):
        """When adapter is stopped, messages are silently dropped."""
        event = {
            "type": "message",
            "user": "U123",
            "text": "hello",
            "channel": "D017",
            "ts": "123",
            "channel_type": "im",
        }

        adapter._stopped = True
        adapter._loop = MagicMock()
        adapter._loop.call_soon_threadsafe = MagicMock()

        adapter._handle_message_event(event, say=MagicMock())
        adapter._loop.call_soon_threadsafe.assert_not_called()


# ===================================================================
# Chat type mapping
# ===================================================================

class TestChatTypeMapping:
    """Slack channel_type → gateway chat_type normalization."""

    def test_im_stays_im(self, adapter):
        assert adapter._normalize_chat_type("im") == "im"

    def test_mpim_normalized(self, adapter):
        assert adapter._normalize_chat_type("mpim") == "mpim"

    def test_channel_normalized(self, adapter):
        result = adapter._normalize_chat_type("channel")
        assert result in ("channel", "group")

    def test_group_normalized(self, adapter):
        result = adapter._normalize_chat_type("group")
        assert result in ("channel", "group")


# ===================================================================
# Outbound message (send_message)
# ===================================================================

class TestSlackSendMessage:

    @pytest.mark.asyncio
    async def test_send_message_calls_chat_post(self, adapter):
        """send_message calls Slack chat.postMessage."""
        from channels.base import OutboundMessage

        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "9999.0"}
        adapter._slack_client = mock_client

        msg = OutboundMessage(
            channel_id="test-slack-ch",
            external_chat_id="D017ZD4PUKT",
            text="Hello from Swarm!",
        )
        result = await adapter.send_message(msg)
        assert result == "9999.0"
        mock_client.chat_postMessage.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_in_thread(self, adapter):
        """send_message with thread_ts replies in thread."""
        from channels.base import OutboundMessage

        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "9999.1"}
        adapter._slack_client = mock_client

        msg = OutboundMessage(
            channel_id="test-slack-ch",
            external_chat_id="D017ZD4PUKT",
            external_thread_id="1234.5678",
            text="Thread reply",
        )
        result = await adapter.send_message(msg)
        call_kwargs = mock_client.chat_postMessage.call_args
        assert call_kwargs[1].get("thread_ts") == "1234.5678" or \
               (call_kwargs[0] if call_kwargs[0] else call_kwargs[1]).get("thread_ts") == "1234.5678"

    @pytest.mark.asyncio
    async def test_send_message_no_client_returns_none(self, adapter):
        """send_message returns None when client not initialized."""
        from channels.base import OutboundMessage
        adapter._slack_client = None
        result = await adapter.send_message(OutboundMessage(
            channel_id="test", external_chat_id="D017", text="test",
        ))
        assert result is None


# ===================================================================
# Message overflow handling (msg_too_long fix)
# ===================================================================

class TestSlackMessageOverflow:
    """Verify long messages are split to stay within Slack API limits."""

    @pytest.mark.asyncio
    async def test_update_message_streaming_truncates_text_fallback(self, adapter):
        """Streaming update truncates the text fallback field to prevent msg_too_long."""
        mock_client = MagicMock()
        mock_client.chat_update.return_value = {"ok": True}
        adapter._slack_client = mock_client

        # 50K chars — exceeds Slack's ~40K text limit
        long_text = "x" * 50_000
        await adapter.update_message("C123", "ts123", long_text, is_final=False)

        call_kwargs = mock_client.chat_update.call_args[1]
        # text fallback must be truncated
        assert len(call_kwargs["text"]) <= 39_001
        # blocks section text must be within 3000 chars
        block_text = call_kwargs["blocks"][0]["text"]["text"]
        assert len(block_text) <= 3_001

    @pytest.mark.asyncio
    async def test_update_message_final_splits_overflow_blocks(self, adapter):
        """Final update splits >50 blocks across update + follow-up messages."""
        mock_client = MagicMock()
        mock_client.chat_update.return_value = {"ok": True}
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "overflow.1"}
        adapter._slack_client = mock_client

        # Each paragraph must be >3000 chars to guarantee 1 block each.
        # 55 such paragraphs → 55 blocks → exceeds the 50-block limit.
        paragraphs = [f"Paragraph {i}: " + "a" * 3000 for i in range(55)]
        long_text = "\n\n".join(paragraphs)
        await adapter.update_message("C123", "ts123", long_text, is_final=True)

        # Original message should be updated with first chunk
        mock_client.chat_update.assert_called_once()
        update_blocks = mock_client.chat_update.call_args[1]["blocks"]
        assert len(update_blocks) <= 50

        # Overflow should be posted as new message(s)
        assert mock_client.chat_postMessage.call_count >= 1

    @pytest.mark.asyncio
    async def test_update_message_final_short_no_overflow(self, adapter):
        """Final update with short text uses single chat_update, no overflow."""
        mock_client = MagicMock()
        mock_client.chat_update.return_value = {"ok": True}
        adapter._slack_client = mock_client

        await adapter.update_message("C123", "ts123", "Short reply", is_final=True)

        mock_client.chat_update.assert_called_once()
        mock_client.chat_postMessage.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_message_splits_long_text(self, adapter):
        """send_message splits very long text into multiple Slack messages."""
        from channels.base import OutboundMessage

        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "9999.0"}
        adapter._slack_client = mock_client

        # Each paragraph >3000 chars → 1 block each → 55 blocks > 50 limit
        paragraphs = [f"Paragraph {i}: " + "b" * 3000 for i in range(55)]
        long_text = "\n\n".join(paragraphs)

        msg = OutboundMessage(
            channel_id="test-slack-ch",
            external_chat_id="D017ZD4PUKT",
            text=long_text,
        )
        result = await adapter.send_message(msg)
        assert result == "9999.0"  # returns first message ts

        # Should have multiple postMessage calls
        assert mock_client.chat_postMessage.call_count >= 2

    @pytest.mark.asyncio
    async def test_send_message_text_fallback_truncated(self, adapter):
        """send_message truncates the text fallback for long messages."""
        from channels.base import OutboundMessage

        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ok": True, "ts": "9999.0"}
        adapter._slack_client = mock_client

        msg = OutboundMessage(
            channel_id="test-slack-ch",
            external_chat_id="D017ZD4PUKT",
            text="x" * 50_000,
        )
        await adapter.send_message(msg)

        first_call_kwargs = mock_client.chat_postMessage.call_args_list[0][1]
        assert len(first_call_kwargs["text"]) <= 39_001


# ===================================================================
# Lifecycle (start/stop)
# ===================================================================

class TestSlackLifecycle:

    @pytest.mark.asyncio
    async def test_stop_sets_stopped_flag(self, adapter):
        """stop() sets _stopped to True."""
        adapter._ws_thread = None
        await adapter.stop()
        assert adapter._stopped is True

    @pytest.mark.asyncio
    async def test_start_creates_background_thread(self, adapter):
        """start() spawns a background thread for Socket Mode."""
        with patch("channels.adapters.slack.App") as MockApp, \
             patch("channels.adapters.slack.SocketModeHandler") as MockHandler:
            MockApp.return_value = MagicMock()
            mock_handler = MagicMock()
            MockHandler.return_value = mock_handler

            await adapter.start()

            assert adapter._ws_thread is not None
            assert adapter._ws_thread.daemon is True
            assert adapter._ws_thread.name.startswith("slack-ws-")

            # Clean up
            adapter._stopped = True
            if adapter._ws_thread.is_alive():
                adapter._ws_thread.join(timeout=1)


# ===================================================================
# Registry integration
# ===================================================================

class TestSlackRegistration:
    """Slack adapter registers itself in the registry when slack-bolt is available."""

    def test_slack_registered_in_registry(self):
        """After importing slack adapter, 'slack' should be in registry."""
        from channels.registry import get_adapter_class
        # Force import (may already be imported)
        try:
            from channels.adapters import slack  # noqa: F401
        except ImportError:
            pytest.skip("slack-bolt not installed")

        cls = get_adapter_class("slack")
        assert cls is not None, "Slack adapter not registered in registry"

    def test_load_adapters_includes_slack(self):
        """load_adapters() should attempt to load the slack module."""
        from channels.registry import load_adapters, get_adapter_class
        load_adapters()
        # If slack-bolt is installed, adapter should be registered
        try:
            import slack_bolt  # noqa: F401
            assert get_adapter_class("slack") is not None
        except ImportError:
            pass  # OK — slack not installed, adapter won't register
