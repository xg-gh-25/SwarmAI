"""Tests for SlackChannelAdapter — Socket Mode adapter for Slack.

Covers acceptance criteria:
  AC4: SlackChannelAdapter Socket Mode within gateway lifecycle
  AC5: Gateway tests >= 15 cases (combined with test_channel_gateway.py)

Methodology: TDD RED phase — all tests written before implementation.
All slack-bolt/slack-sdk calls are mocked (no real Slack API needed).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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
async def adapter(slack_config, on_message):
    """Create a SlackChannelAdapter with mocked dependencies.

    Yields the adapter and ensures all background tasks are properly
    cleaned up to avoid 'Task was destroyed but pending' warnings.
    """
    from channels.adapters.slack import SlackChannelAdapter
    a = SlackChannelAdapter(
        channel_id="test-slack-ch",
        config=slack_config,
        on_message=on_message,
    )
    yield a
    # Ensure adapter knows it's stopped
    a._stopped = True
    # Cancel and await any pending tasks — guard against closed event loop
    # (some tests create their own loop which is already closed at teardown)
    try:
        for attr in ("_monitor_task", "_poll_task"):
            task = getattr(a, attr, None)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        # Close MCP bridge
        if a._mcp_bridge:
            await a._mcp_bridge.close()
    except RuntimeError:
        pass  # event loop closed — tasks will be GC'd
    # Join WS thread if alive
    ws = a._ws_thread
    if ws and ws.is_alive():
        ws.join(timeout=1)


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

        event = {
            "type": "message",
            "user": "REDACTED_ID1",
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
            # Cleanup handled by the adapter fixture teardown


# ===================================================================
# Registry integration
# ===================================================================

# ===================================================================
# Native streaming — chat.startStream/appendStream/stopStream
# Zero rate limit path for real-time token-by-token streaming.
# ===================================================================


class TestSlackNativeStreaming:
    """Verify native streaming SDK parameter names match slack_sdk signatures.

    Critical: chat_startStream uses ``markdown_text`` (not ``text``),
    chat_appendStream uses ``ts`` + ``markdown_text`` (not ``stream_id`` + ``text``),
    chat_stopStream uses ``ts`` + ``markdown_text`` (not ``stream_id`` + ``text``).
    """

    def test_supports_native_streaming_true(self, adapter):
        """Slack adapter supports native streaming."""
        assert adapter.supports_native_streaming is True

    @pytest.mark.asyncio
    async def test_start_stream_returns_none_without_client(self, adapter):
        """start_stream returns None when no Slack client."""
        adapter._slack_client = None
        result = await adapter.start_stream("C001")
        assert result is None

    @pytest.mark.asyncio
    async def test_start_stream_uses_markdown_text(self, adapter):
        """start_stream passes text as ``markdown_text`` to SDK."""
        mock_client = MagicMock()
        mock_client.chat_startStream.return_value = {"ts": "1234.5678"}
        adapter._slack_client = mock_client

        ts = await adapter.start_stream(
            "C001", external_thread_id="1111.0000", text="Hello",
        )
        assert ts == "1234.5678"
        call_kwargs = mock_client.chat_startStream.call_args[1]
        assert call_kwargs["channel"] == "C001"
        assert call_kwargs["thread_ts"] == "1111.0000"
        assert call_kwargs["markdown_text"] == "Hello"
        # Must NOT contain 'text' — Slack SDK rejects it
        assert "text" not in call_kwargs

    @pytest.mark.asyncio
    async def test_append_stream_uses_ts_and_markdown_text(self, adapter):
        """append_stream uses ``ts`` (not ``stream_id``) and ``markdown_text``."""
        mock_client = MagicMock()
        mock_client.chat_appendStream.return_value = {}
        adapter._slack_client = mock_client

        await adapter.append_stream("C001", "1234.5678", "some text")
        mock_client.chat_appendStream.assert_called_once_with(
            channel="C001", ts="1234.5678", markdown_text="some text",
        )

    @pytest.mark.asyncio
    async def test_append_stream_ignores_empty(self, adapter):
        """append_stream is a no-op for empty text."""
        mock_client = MagicMock()
        adapter._slack_client = mock_client

        await adapter.append_stream("C001", "1234.5678", "")
        mock_client.chat_appendStream.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_stream_bare_then_chat_update(self, adapter):
        """stop_stream sends bare stopStream (no content) then chat.update."""
        mock_client = MagicMock()
        mock_client.chat_stopStream.return_value = {}
        mock_client.chat_update.return_value = {}
        adapter._slack_client = mock_client

        await adapter.stop_stream("C001", "1234.5678", text="Final.")

        # Step 1: bare stopStream — only channel + ts, no content
        stop_kwargs = mock_client.chat_stopStream.call_args[1]
        assert stop_kwargs == {"channel": "C001", "ts": "1234.5678"}
        assert "markdown_text" not in stop_kwargs
        assert "blocks" not in stop_kwargs

        # Step 2: chat.update replaces with Block Kit
        mock_client.chat_update.assert_called_once()
        update_kwargs = mock_client.chat_update.call_args[1]
        assert update_kwargs["channel"] == "C001"
        assert update_kwargs["ts"] == "1234.5678"
        assert "blocks" in update_kwargs
        assert isinstance(update_kwargs["blocks"], list)

    @pytest.mark.asyncio
    async def test_stop_stream_auto_generates_blocks_in_update(self, adapter):
        """stop_stream converts text to Block Kit blocks via chat.update."""
        mock_client = MagicMock()
        mock_client.chat_stopStream.return_value = {}
        mock_client.chat_update.return_value = {}
        adapter._slack_client = mock_client

        await adapter.stop_stream("C001", "1234.5678", text="Hello world")

        # chat.update should have generated blocks from text
        update_kwargs = mock_client.chat_update.call_args[1]
        assert "blocks" in update_kwargs
        assert isinstance(update_kwargs["blocks"], list)
        # Verify block content contains our text
        block_text = update_kwargs["blocks"][0]["text"]["text"]
        assert "Hello world" in block_text

    @pytest.mark.asyncio
    async def test_stop_stream_raises_on_stop_failure(self, adapter):
        """stop_stream re-raises if stopStream fails — gateway can fall back."""
        mock_client = MagicMock()
        mock_client.chat_stopStream.side_effect = Exception("network error")
        adapter._slack_client = mock_client

        with pytest.raises(Exception, match="network error"):
            await adapter.stop_stream("C001", "1234.5678", text="Final.")

        # chat.update should NOT have been called
        mock_client.chat_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_stream_raises_on_update_failure(self, adapter):
        """stop_stream re-raises if chat.update fails — gateway can fall back."""
        mock_client = MagicMock()
        mock_client.chat_stopStream.return_value = {}
        mock_client.chat_update.side_effect = Exception("rate limited")
        adapter._slack_client = mock_client

        with pytest.raises(Exception, match="rate limited"):
            await adapter.stop_stream("C001", "1234.5678", text="Final.")

        # stopStream should have been called first
        mock_client.chat_stopStream.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_stream_exception_returns_none(self, adapter):
        """start_stream returns None on SDK error — gateway falls back to legacy."""
        mock_client = MagicMock()
        mock_client.chat_startStream.side_effect = Exception("API error")
        adapter._slack_client = mock_client

        result = await adapter.start_stream("C001", external_thread_id="1111.0000")
        assert result is None


# ===================================================================
# Status reactions
# ===================================================================


class TestSlackReactions:
    """Emoji status reactions on inbound messages."""

    @pytest.mark.asyncio
    async def test_add_reaction(self, adapter):
        mock_client = MagicMock()
        mock_client.reactions_add.return_value = {"ok": True}
        adapter._slack_client = mock_client

        await adapter.add_reaction("C001", "1234.5678", "eyes")
        mock_client.reactions_add.assert_called_once_with(
            channel="C001", timestamp="1234.5678", name="eyes",
        )

    @pytest.mark.asyncio
    async def test_remove_reaction(self, adapter):
        mock_client = MagicMock()
        mock_client.reactions_remove.return_value = {"ok": True}
        adapter._slack_client = mock_client

        await adapter.remove_reaction("C001", "1234.5678", "eyes")
        mock_client.reactions_remove.assert_called_once_with(
            channel="C001", timestamp="1234.5678", name="eyes",
        )

    @pytest.mark.asyncio
    async def test_add_reaction_no_client(self, adapter):
        """Reaction is a no-op without a client."""
        adapter._slack_client = None
        await adapter.add_reaction("C001", "1234.5678", "eyes")  # no error

    @pytest.mark.asyncio
    async def test_add_reaction_exception_swallowed(self, adapter):
        """Reaction failures don't propagate."""
        mock_client = MagicMock()
        mock_client.reactions_add.side_effect = Exception("rate limited")
        adapter._slack_client = mock_client

        await adapter.add_reaction("C001", "1234.5678", "eyes")  # no error


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


# ===================================================================
# User name resolution — negative caching (AC1, AC2)
# ===================================================================

class TestUserNameResolution:
    """Verify display_name cache and known-user annotations."""

    def test_negative_cache_prevents_retry(self, adapter):
        """After both API calls fail, subsequent lookups skip API (AC1)."""
        mock_client = MagicMock()
        mock_client.users_info.side_effect = Exception("missing_scope")
        mock_client.users_profile_get.side_effect = Exception("missing_scope")
        adapter._slack_client = mock_client

        # First call: tries both APIs, falls back to user_id
        result1 = adapter._get_user_name("U_UNKNOWN")
        assert result1 == "U_UNKNOWN"
        assert mock_client.users_info.call_count == 1

        # Second call: should NOT call APIs again (cached negative result)
        result2 = adapter._get_user_name("U_UNKNOWN")
        assert result2 == "U_UNKNOWN"
        assert mock_client.users_info.call_count == 1  # still 1, not 2

    # NOTE (run_a1f4c2d8): these two tests used to assert the CONTENT of the ambient
    # known-users map — `len(_KNOWN_USERS) > 0` and `"REDACTED_ID1" in _KNOWN_USERS`.
    # That broke in BOTH directions once slack.py moved the mappings out of the repo
    # (`_DEFAULT_KNOWN_USERS = {}`, loaded from ~/.swarm-ai/slack-known-users.json —
    # a secret-hygiene change: real Slack IDs must not be committed):
    #   - on a DEV box the config exists, so _KNOWN_USERS holds REAL ids and the
    #     redacted placeholder is absent  → RED;
    #   - on CI there is no config at all, so _KNOWN_USERS == {} and even
    #     `len(...) > 0` fails                                        → RED.
    # A unit test must not depend on machine-local config. Both now INJECT a fixture
    # map and assert the MECHANISM (a pre-populated map is adopted as cache and
    # short-circuits the API), which is the actual AC2 contract — not the contents of
    # whoever's laptop is running pytest.

    @staticmethod
    def _adapter_with_known_users(slack_config, on_message, known: dict[str, str]):
        """Build an adapter whose module-level known-users map is `known`.

        The map is read in __init__ (``self._user_cache = dict(_KNOWN_USERS)``), so the
        patch must be active DURING construction. No background tasks are started by
        __init__ (that happens in start()), so no teardown is needed here.
        """
        from channels.adapters import slack as slack_mod

        with patch.object(slack_mod, "_KNOWN_USERS", known):
            return slack_mod.SlackChannelAdapter(
                channel_id="test-slack-ch",
                config=slack_config,
                on_message=on_message,
            )

    def test_known_users_prepopulated(self, slack_config, on_message):
        """A known-users map is adopted into the adapter's cache at construction (AC2)."""
        fixture = {"U_FIXTURE_1": "Fixture One", "U_FIXTURE_2": "Fixture Two"}
        fresh = self._adapter_with_known_users(slack_config, on_message, fixture)

        # Every fixture mapping is present in the cache the adapter starts with...
        for uid, name in fixture.items():
            assert fresh._user_cache.get(uid) == name
        # ...and it is a COPY, not the module dict (a per-adapter cache mutation must
        # never leak back into the shared module-level map).
        assert fresh._user_cache is not fixture

    def test_known_user_resolved_without_api(self, slack_config, on_message):
        """A known user resolves from cache — zero Slack API calls (AC2)."""
        fresh = self._adapter_with_known_users(
            slack_config, on_message, {"U_FIXTURE_1": "Fixture One"})
        fresh._slack_client = MagicMock()

        assert fresh._get_user_name("U_FIXTURE_1") == "Fixture One"
        fresh._slack_client.users_info.assert_not_called()
        # Non-negotiable half: an UNKNOWN id must still go to the API, otherwise this
        # test would pass on an adapter that never calls Slack at all.
        fresh._slack_client.users_info.return_value = {"ok": False}
        fresh._get_user_name("U_NOT_IN_FIXTURE")
        fresh._slack_client.users_info.assert_called_once()

    def test_successful_lookup_still_cached(self, adapter):
        """Successful API resolution is still cached (existing behavior preserved)."""
        mock_client = MagicMock()
        mock_client.users_info.return_value = {
            "ok": True,
            "user": {"real_name": "Test User", "profile": {"real_name_normalized": "Test User"}},
        }
        adapter._slack_client = mock_client

        result1 = adapter._get_user_name("U_NEW")
        assert result1 == "Test User"
        result2 = adapter._get_user_name("U_NEW")
        assert result2 == "Test User"
        assert mock_client.users_info.call_count == 1


# ===================================================================
# L1 activation: mention detection + double-fire dedup (run_4c5ad9c5)
# ===================================================================

class TestL1MentionAndDedup:
    """_normalize_event marks mentions and drops Slack's double-fire."""

    @pytest.mark.asyncio
    async def test_app_mention_event_marked_is_mention(self, adapter):
        """An event flagged _is_app_mention → metadata.is_mention True."""
        ev = {"user": "U1", "text": "hey bot", "channel": "C1",
              "ts": "100.1", "channel_type": "channel", "_is_app_mention": True}
        msg = adapter._normalize_event(ev)
        assert msg is not None and msg.metadata["is_mention"] is True

    @pytest.mark.asyncio
    async def test_bot_userid_in_text_marked_is_mention(self, adapter):
        """Raw text containing <@bot_user_id> → is_mention True (Socket Mode
        path where the message arrives as a plain 'message' event)."""
        adapter._bot_user_id = "UBOT"
        ev = {"user": "U1", "text": "hello <@UBOT> please help", "channel": "C1",
              "ts": "101.1", "channel_type": "channel"}
        msg = adapter._normalize_event(ev)
        assert msg is not None and msg.metadata["is_mention"] is True

    @pytest.mark.asyncio
    async def test_plain_message_not_marked_mention(self, adapter):
        """A plain non-mention message → is_mention False."""
        adapter._bot_user_id = "UBOT"
        ev = {"user": "U1", "text": "just chatting", "channel": "C1",
              "ts": "102.1", "channel_type": "channel"}
        msg = adapter._normalize_event(ev)
        assert msg is not None and msg.metadata["is_mention"] is False

    @pytest.mark.asyncio
    async def test_double_fire_deduped_by_ts(self, adapter):
        """The SAME (channel, ts) delivered twice (message + app_mention) →
        second normalize returns None (exactly-once) AND the surviving message
        carries is_mention=True (the mention signal must SURVIVE dedup — the
        adversarial-found bug was the mention being dropped)."""
        adapter._bot_user_id = "UBOT"
        ev1 = {"user": "U1", "text": "hi <@UBOT>", "channel": "C1",
               "ts": "103.1", "channel_type": "channel"}
        ev2 = {"user": "U1", "text": "hi <@UBOT>", "channel": "C1",
               "ts": "103.1", "channel_type": "channel", "_is_app_mention": True}
        first = adapter._normalize_event(ev1)
        second = adapter._normalize_event(ev2)
        assert first is not None
        assert first.metadata["is_mention"] is True, "mention must survive dedup"
        assert second is None, "duplicate ts must be dropped (double-fire)"

    @pytest.mark.asyncio
    async def test_missed_mention_upgraded_when_bot_id_unresolved(self, adapter):
        """Adversarial HIGH regression: if _bot_user_id is UNSET and the plain
        `message` event arrives FIRST (is_mention=False), the authoritative
        `app_mention` event (same ts) must NOT be silently dropped — it upgrades
        the missed mention and is re-emitted with is_mention=True, so the bot
        never ignores a real @mention."""
        adapter._bot_user_id = ""  # startup auth blip — identity unresolved
        plain = {"user": "U1", "text": "hey <@UBOT>", "channel": "C1",
                 "ts": "105.1", "channel_type": "channel"}
        app_mention = {"user": "U1", "text": "hey <@UBOT>", "channel": "C1",
                       "ts": "105.1", "channel_type": "channel", "_is_app_mention": True}
        first = adapter._normalize_event(plain)
        second = adapter._normalize_event(app_mention)
        assert first is not None and first.metadata["is_mention"] is False
        assert second is not None, "app_mention must re-emit to correct a missed mention"
        assert second.metadata["is_mention"] is True

    @pytest.mark.asyncio
    async def test_app_mention_reliable_without_bot_id(self, adapter):
        """_is_app_mention alone marks a mention even if _bot_user_id never
        resolves (the always-reliable signal)."""
        adapter._bot_user_id = ""
        ev = {"user": "U1", "text": "help me", "channel": "C1",
              "ts": "106.1", "channel_type": "channel", "_is_app_mention": True}
        msg = adapter._normalize_event(ev)
        assert msg is not None and msg.metadata["is_mention"] is True

    @pytest.mark.asyncio
    async def test_different_ts_not_deduped(self, adapter):
        """Distinct ts values are both processed (dedup is per-ts, not a mute)."""
        a = adapter._normalize_event(
            {"user": "U1", "text": "one", "channel": "C1", "ts": "104.1", "channel_type": "channel"})
        b = adapter._normalize_event(
            {"user": "U1", "text": "two", "channel": "C1", "ts": "104.2", "channel_type": "channel"})
        assert a is not None and b is not None

    @pytest.mark.asyncio
    async def test_seen_ts_set_is_bounded(self, adapter):
        """The dedup seen-set never grows past _SEEN_TS_MAX (no memory leak)."""
        for i in range(adapter._SEEN_TS_MAX + 50):
            adapter._normalize_event(
                {"user": "U1", "text": f"m{i}", "channel": "C1",
                 "ts": f"200.{i}", "channel_type": "channel"})
        assert len(adapter._seen_ts) <= adapter._SEEN_TS_MAX
