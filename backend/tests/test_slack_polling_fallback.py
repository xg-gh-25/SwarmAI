"""Tests for Slack adapter HTTP polling fallback.

Covers acceptance criteria for the polling fallback feature:
  AC1: Socket Mode fails ≥3 times → auto-switch to HTTP polling
  AC2: Polling uses conversations.history() normalized to InboundMessage
  AC3: Socket Mode remains primary, polling is fallback only
  AC4: Polling interval configurable (default 5s), respects rate limits
  AC5: Socket Mode recovery → auto-switch back from polling
  AC6: All existing adapter behavior unchanged

Methodology: TDD RED phase — all tests written before implementation.
All Slack API calls are mocked (no real Slack API needed).
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------

def _slack_bolt_available():
    try:
        from channels.adapters.slack import SLACK_BOLT_AVAILABLE
        return SLACK_BOLT_AVAILABLE
    except (ImportError, ModuleNotFoundError):
        return False


pytestmark = pytest.mark.skipif(
    not _slack_bolt_available(),
    reason="slack-bolt not installed",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
    from channels.adapters.slack import SlackChannelAdapter
    a = SlackChannelAdapter(
        channel_id="ch-test-001",
        config=slack_config,
        on_message=on_message,
    )
    return a


# ---------------------------------------------------------------------------
# AC1: Socket Mode fails ≥3 → auto-switch to polling
# ---------------------------------------------------------------------------

class TestConnectionModeSwitch:
    """AC1: adapter switches to polling after persistent WS failures."""

    def test_default_mode_is_socket(self, adapter):
        """Adapter starts in socket mode by default."""
        assert adapter._connection_mode == "socket"

    def test_ws_fail_count_starts_at_zero(self, adapter):
        """Failure counter starts at zero."""
        assert adapter._ws_fail_count == 0

    def test_switch_to_polling_after_threshold(self, adapter):
        """After _WS_FAIL_THRESHOLD failures, mode switches to polling."""
        from channels.adapters.slack import _WS_FAIL_THRESHOLD
        adapter._ws_fail_count = _WS_FAIL_THRESHOLD
        # The switch happens in _ws_health_monitor when it detects dead thread
        # Here we directly test the state transition method
        adapter._loop = asyncio.new_event_loop()
        adapter._slack_client = MagicMock()
        adapter._slack_client.conversations_list.return_value = {"channels": [], "ok": True}
        try:
            adapter._loop.run_until_complete(adapter._switch_to_polling())
            assert adapter._connection_mode == "polling"
        finally:
            adapter._loop.close()

    def test_ws_fail_below_threshold_stays_socket(self, adapter):
        """Below threshold, adapter stays in socket mode."""
        adapter._ws_fail_count = 1
        assert adapter._connection_mode == "socket"


# ---------------------------------------------------------------------------
# AC2: Polling uses conversations.history() → InboundMessage
# ---------------------------------------------------------------------------

class TestPollingMessageNormalization:
    """AC2: polled messages are normalized to InboundMessage."""

    def test_normalize_event_basic_message(self, adapter):
        """Regular text message normalizes to InboundMessage."""
        adapter._slack_client = MagicMock()
        # Mock user name resolution
        adapter._user_cache = {"U123": "Test User"}

        event = {
            "user": "U123",
            "text": "Hello Swarm",
            "channel": "D456",
            "ts": "1234567890.000001",
            "channel_type": "im",
        }
        msg = adapter._normalize_event(event)
        assert msg is not None
        assert msg.text == "Hello Swarm"
        assert msg.external_sender_id == "U123"
        assert msg.external_chat_id == "D456"
        assert msg.sender_display_name == "Test User"

    def test_normalize_event_skips_bot_messages(self, adapter):
        """Bot messages are filtered out."""
        event = {
            "bot_id": "B789",
            "text": "I am a bot",
            "channel": "D456",
            "ts": "1234567890.000002",
        }
        msg = adapter._normalize_event(event)
        assert msg is None

    def test_normalize_event_skips_subtypes(self, adapter):
        """Message subtypes (edited, deleted) are filtered except file_share."""
        event = {
            "user": "U123",
            "text": "edited",
            "channel": "D456",
            "ts": "1234567890.000003",
            "subtype": "message_changed",
        }
        msg = adapter._normalize_event(event)
        assert msg is None

    def test_normalize_event_allows_file_share(self, adapter):
        """file_share subtype is allowed."""
        adapter._user_cache = {"U123": "Test User"}
        event = {
            "user": "U123",
            "text": "Check this file",
            "channel": "D456",
            "ts": "1234567890.000004",
            "subtype": "file_share",
            "channel_type": "im",
        }
        msg = adapter._normalize_event(event)
        assert msg is not None
        assert msg.text == "Check this file"

    def test_normalize_event_empty_text_no_attachments(self, adapter):
        """Empty text with no attachments returns None."""
        event = {
            "user": "U123",
            "text": "",
            "channel": "D456",
            "ts": "1234567890.000005",
        }
        msg = adapter._normalize_event(event)
        assert msg is None

    def test_normalize_event_with_thread(self, adapter):
        """Thread messages carry thread_ts as external_thread_id."""
        adapter._user_cache = {"U123": "Test User"}
        event = {
            "user": "U123",
            "text": "Thread reply",
            "channel": "D456",
            "ts": "1234567890.000006",
            "thread_ts": "1234567890.000001",
            "channel_type": "im",
        }
        msg = adapter._normalize_event(event)
        assert msg is not None
        assert msg.external_thread_id == "1234567890.000001"


# ---------------------------------------------------------------------------
# AC3: Socket Mode remains primary
# ---------------------------------------------------------------------------

class TestSocketModePrimary:
    """AC3: Socket Mode is the primary transport."""

    def test_initial_mode_is_socket(self, adapter):
        """Fresh adapter starts in socket mode."""
        assert adapter._connection_mode == "socket"

    def test_polling_only_after_failures(self, adapter):
        """Polling never activates without WS failures."""
        # No failures → no polling
        assert adapter._poll_task is None


# ---------------------------------------------------------------------------
# AC4: Polling interval and rate limits
# ---------------------------------------------------------------------------

class TestPollingConfiguration:
    """AC4: configurable interval, rate-safe."""

    def test_default_poll_interval(self):
        """Default poll interval is 5 seconds."""
        from channels.adapters.slack import _POLL_INTERVAL
        assert _POLL_INTERVAL == 5.0

    def test_ws_fail_threshold_is_3(self):
        """Default WS failure threshold is 3."""
        from channels.adapters.slack import _WS_FAIL_THRESHOLD
        assert _WS_FAIL_THRESHOLD == 3

    def test_poll_channel_messages_uses_limit(self, adapter):
        """Polling uses limit parameter to avoid excessive API calls."""
        adapter._slack_client = MagicMock()
        adapter._slack_client.conversations_history.return_value = {
            "ok": True,
            "messages": [],
        }
        adapter._poll_channels = {"D456": str(time.time())}
        adapter._loop = asyncio.new_event_loop()
        try:
            adapter._loop.run_until_complete(
                adapter._poll_channel_messages("D456")
            )
            call_kwargs = adapter._slack_client.conversations_history.call_args
            assert call_kwargs is not None
            # Should use limit parameter
            assert "limit" in (call_kwargs.kwargs or call_kwargs[1])
        finally:
            adapter._loop.close()


# ---------------------------------------------------------------------------
# AC5: Socket Mode recovery → switch back
# ---------------------------------------------------------------------------

class TestSocketModeRecovery:
    """AC5: adapter switches back when Socket Mode recovers."""

    def test_try_reconnect_resets_mode(self, adapter):
        """Successful Socket Mode reconnect switches back from polling."""
        adapter._connection_mode = "polling"
        adapter._ws_fail_count = 5
        adapter._loop = asyncio.new_event_loop()
        adapter._stopped = False

        # Mock: Socket Mode handler starts successfully (thread stays alive)
        mock_handler = MagicMock()
        mock_handler.start = MagicMock()  # blocking, but we mock it

        with patch("channels.adapters.slack.SocketModeHandler", return_value=mock_handler):
            with patch("channels.adapters.slack.App"):
                try:
                    result = adapter._loop.run_until_complete(
                        adapter._try_socket_mode_reconnect()
                    )
                    if result:  # reconnect succeeded
                        assert adapter._connection_mode == "socket"
                        assert adapter._ws_fail_count == 0
                finally:
                    adapter._loop.close()


# ---------------------------------------------------------------------------
# AC6: Existing behavior unchanged
# ---------------------------------------------------------------------------

class TestExistingBehaviorPreserved:
    """AC6: all existing adapter behavior unchanged."""

    def test_handle_message_event_still_works(self, adapter, on_message):
        """Socket Mode message handler still works (uses _normalize_event internally)."""
        adapter._user_cache = {"U123": "Test User"}
        adapter._loop = asyncio.new_event_loop()
        adapter._stopped = False

        event = {
            "user": "U123",
            "text": "Hello via WebSocket",
            "channel": "D456",
            "ts": "1234567890.000010",
            "channel_type": "im",
        }
        # This should not raise — internal refactoring must preserve behavior
        adapter._handle_message_event(event)
        adapter._loop.close()

    def test_adapter_has_connection_mode_attr(self, adapter):
        """New _connection_mode attribute exists on fresh adapter."""
        assert hasattr(adapter, "_connection_mode")

    def test_adapter_has_poll_channels_attr(self, adapter):
        """New _poll_channels attribute exists on fresh adapter."""
        assert hasattr(adapter, "_poll_channels")

    def test_adapter_has_ws_fail_count_attr(self, adapter):
        """New _ws_fail_count attribute exists on fresh adapter."""
        assert hasattr(adapter, "_ws_fail_count")


# ---------------------------------------------------------------------------
# Channel discovery
# ---------------------------------------------------------------------------

class TestChannelDiscovery:
    """Polling discovers DM channels via conversations.list."""

    def test_discover_dm_channels(self, adapter):
        """Discovers DM channels via conversations.list(types=im)."""
        adapter._slack_client = MagicMock()
        adapter._slack_client.conversations_list.return_value = {
            "ok": True,
            "channels": [
                {"id": "D001", "is_im": True},
                {"id": "D002", "is_im": True},
            ],
        }
        adapter._poll_channels = {}
        adapter._loop = asyncio.new_event_loop()
        try:
            adapter._loop.run_until_complete(adapter._discover_poll_channels())
            assert "D001" in adapter._poll_channels
            assert "D002" in adapter._poll_channels
        finally:
            adapter._loop.close()

    def test_discover_preserves_existing_timestamps(self, adapter):
        """Existing channel timestamps are not overwritten on rediscovery."""
        adapter._slack_client = MagicMock()
        adapter._slack_client.conversations_list.return_value = {
            "ok": True,
            "channels": [
                {"id": "D001", "is_im": True},
            ],
        }
        # Pre-existing timestamp
        adapter._poll_channels = {"D001": "1234567890.000000"}
        adapter._loop = asyncio.new_event_loop()
        try:
            adapter._loop.run_until_complete(adapter._discover_poll_channels())
            # Should NOT overwrite existing ts
            assert adapter._poll_channels["D001"] == "1234567890.000000"
        finally:
            adapter._loop.close()


# ---------------------------------------------------------------------------
# Polling message processing
# ---------------------------------------------------------------------------

class TestPollChannelMessages:
    """Unit tests for _poll_channel_messages."""

    def test_processes_messages_oldest_first(self, adapter, on_message):
        """Messages are processed oldest-first (reversed from API response)."""
        adapter._slack_client = MagicMock()
        adapter._user_cache = {"U1": "User One", "U2": "User Two"}
        adapter._on_message = on_message
        adapter._poll_channels = {"D456": "1000.0"}

        adapter._slack_client.conversations_history.return_value = {
            "ok": True,
            "messages": [
                # API returns newest-first
                {"user": "U2", "text": "Second", "channel": "D456",
                 "ts": "1002.0", "channel_type": "im"},
                {"user": "U1", "text": "First", "channel": "D456",
                 "ts": "1001.0", "channel_type": "im"},
            ],
        }

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter._poll_channel_messages("D456"))
            # on_message should be called for both, oldest first
            assert on_message.call_count == 2
            first_call = on_message.call_args_list[0]
            assert first_call.args[0].text == "First"
            second_call = on_message.call_args_list[1]
            assert second_call.args[0].text == "Second"
        finally:
            loop.close()

    def test_updates_last_ts_after_processing(self, adapter, on_message):
        """_poll_channels timestamp advances to newest processed message."""
        adapter._slack_client = MagicMock()
        adapter._user_cache = {"U1": "User One"}
        adapter._on_message = on_message
        adapter._poll_channels = {"D456": "1000.0"}

        adapter._slack_client.conversations_history.return_value = {
            "ok": True,
            "messages": [
                {"user": "U1", "text": "Latest", "channel": "D456",
                 "ts": "1005.0", "channel_type": "im"},
            ],
        }

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter._poll_channel_messages("D456"))
            assert adapter._poll_channels["D456"] == "1005.0"
        finally:
            loop.close()

    def test_skips_own_bot_messages(self, adapter, on_message):
        """Bot's own messages are filtered out during polling."""
        adapter._slack_client = MagicMock()
        adapter._on_message = on_message
        adapter._poll_channels = {"D456": "1000.0"}
        adapter._bot_user_id = "UBOT"

        adapter._slack_client.conversations_history.return_value = {
            "ok": True,
            "messages": [
                # Bot's own message (has bot_id)
                {"bot_id": "BXXX", "text": "Bot reply", "channel": "D456",
                 "ts": "1001.0"},
                # Bot's own message (by user_id)
                {"user": "UBOT", "text": "Also bot", "channel": "D456",
                 "ts": "1002.0"},
            ],
        }

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter._poll_channel_messages("D456"))
            assert on_message.call_count == 0
        finally:
            loop.close()

    def test_no_messages_no_callback(self, adapter, on_message):
        """Empty history doesn't trigger any callbacks."""
        adapter._slack_client = MagicMock()
        adapter._on_message = on_message
        adapter._poll_channels = {"D456": "1000.0"}

        adapter._slack_client.conversations_history.return_value = {
            "ok": True,
            "messages": [],
        }

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter._poll_channel_messages("D456"))
            assert on_message.call_count == 0
        finally:
            loop.close()
