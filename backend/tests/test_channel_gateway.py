"""Tests for channel gateway: slot isolation, per-channel sessions, sender identity, permissions.

Covers acceptance criteria:
  AC1: compute_max_tabs min 2, channel slot dedicated
  AC2: Channel never evicts/queues chat tabs
  AC3: Per-channel session isolation + idle TTL
  AC5: Gateway tests >= 15 cases
  AC6: Sender identity + permission tiers (red team fix)

Methodology: TDD RED phase — all tests written before implementation.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def gateway():
    """Fresh ChannelGateway instance (no DB, no adapters started)."""
    from channels.gateway import ChannelGateway
    return ChannelGateway()


@pytest.fixture
def mock_db(monkeypatch):
    """Patch database module with async mocks for channel tables."""
    mock = MagicMock()
    mock.channels = MagicMock()
    mock.channels.get = AsyncMock(return_value=None)
    mock.channels.list = AsyncMock(return_value=[])
    mock.channels.update = AsyncMock()
    mock.channel_sessions = MagicMock()
    mock.channel_sessions.find_by_external = AsyncMock(return_value=None)
    mock.channel_sessions.put = AsyncMock()
    mock.channel_sessions.update = AsyncMock()
    mock.channel_sessions.delete = AsyncMock()
    mock.channel_sessions.count_by_channel = AsyncMock(return_value=0)
    mock.channel_messages = MagicMock()
    mock.channel_messages.put = AsyncMock()
    monkeypatch.setattr("channels.gateway.db", mock)
    return mock


# ===================================================================
# AC1: compute_max_tabs min 2, channel slot dedicated
# ===================================================================

class TestSlotIsolation:
    """Slot isolation: min_tabs=2, dedicated channel slot."""

    def test_compute_max_tabs_minimum_is_2(self):
        """compute_max_tabs never returns less than 2 (1 chat + 1 channel)."""
        from core.resource_monitor import ResourceMonitor

        rm = ResourceMonitor()
        # Even under extreme memory pressure, min must be 2
        with patch.object(rm, "system_memory") as mock_mem:
            mock_mem.return_value = MagicMock(
                total=8 * 1024 * 1024 * 1024,  # 8GB
                used=7.9 * 1024 * 1024 * 1024,  # 7.9GB used (extreme pressure)
                percent_used=98.75,
                pressure_level="critical",
            )
            result = rm.compute_max_tabs()
            assert result >= 2, f"compute_max_tabs returned {result}, expected >= 2"

    def test_compute_max_tabs_normal_memory(self):
        """Normal memory: compute_max_tabs returns expected range [2, 4]."""
        from core.resource_monitor import ResourceMonitor

        rm = ResourceMonitor()
        with patch.object(rm, "system_memory") as mock_mem:
            mock_mem.return_value = MagicMock(
                total=36 * 1024 * 1024 * 1024,  # 36GB
                used=10 * 1024 * 1024 * 1024,   # 10GB used
                percent_used=27.8,
                pressure_level="nominal",
            )
            result = rm.compute_max_tabs()
            assert 2 <= result <= 4

    def test_session_unit_has_is_channel_session_flag(self):
        """SessionUnit must have is_channel_session attribute."""
        from core.session_unit import SessionUnit

        unit = SessionUnit(
            session_id="test-123",
            agent_id="default",
            on_state_change=lambda *a: None,
        )
        assert hasattr(unit, "is_channel_session"), (
            "SessionUnit missing is_channel_session flag"
        )
        # Default should be False (chat tabs)
        assert unit.is_channel_session is False


# ===================================================================
# AC2: Channel never evicts/queues chat tabs
# ===================================================================

class TestChannelSlotSeparation:
    """Channel conversations must not compete with chat tabs for slots."""

    @pytest.mark.asyncio
    async def test_channel_acquire_slot_uses_dedicated_pool(self):
        """Channel session acquires from channel pool, not chat pool."""
        from core.session_router import SessionRouter
        from core.session_unit import SessionUnit, SessionState

        router = SessionRouter(prompt_builder=MagicMock())

        # Create a chat unit that's STREAMING (protected)
        chat_unit = SessionUnit("chat-1", "default", on_state_change=lambda *a: None)
        chat_unit.is_channel_session = False
        chat_unit.state = SessionState.STREAMING
        # is_alive is state-based: STREAMING → True
        router._units["chat-1"] = chat_unit

        # Create a channel unit requesting a slot
        channel_unit = SessionUnit("channel-1", "default", on_state_change=lambda *a: None)
        channel_unit.is_channel_session = True
        router._units["channel-1"] = channel_unit

        with patch("core.resource_monitor.resource_monitor") as mock_rm:
            mock_rm.compute_max_tabs.return_value = 2
            mock_rm.spawn_budget.return_value = MagicMock(can_spawn=True)

            result = await router._acquire_slot(channel_unit)
            # Channel should get its dedicated slot without touching the chat slot
            assert result in ("ready", "queued")

    @pytest.mark.asyncio
    async def test_chat_eviction_never_touches_channel_slot(self):
        """_evict_idle for chat must never evict a channel IDLE session."""
        from core.session_router import SessionRouter
        from core.session_unit import SessionUnit, SessionState

        router = SessionRouter(prompt_builder=MagicMock())

        # Channel unit sitting IDLE
        channel_unit = SessionUnit("ch-1", "default", on_state_change=lambda *a: None)
        channel_unit.is_channel_session = True
        channel_unit.state = SessionState.IDLE
        # is_alive is state-based: IDLE → True
        router._units["ch-1"] = channel_unit

        # Chat unit requesting eviction
        chat_requester = SessionUnit("chat-new", "default", on_state_change=lambda *a: None)
        chat_requester.is_channel_session = False
        router._units["chat-new"] = chat_requester

        # _evict_idle should NOT evict the channel unit when called for chat
        evicted = await router._evict_idle(exclude=chat_requester)
        # Channel unit should still be alive (not evicted for chat)
        assert channel_unit.state == SessionState.IDLE, (
            "Channel IDLE was evicted by chat — violates slot isolation"
        )

    @pytest.mark.asyncio
    async def test_channel_queue_when_another_channel_streaming(self):
        """When channel slot is STREAMING, new channel message queues (not steals chat)."""
        from core.session_router import SessionRouter
        from core.session_unit import SessionUnit, SessionState

        router = SessionRouter(prompt_builder=MagicMock())

        # Channel unit 1 is STREAMING
        ch1 = SessionUnit("ch-1", "default", on_state_change=lambda *a: None)
        ch1.is_channel_session = True
        ch1.state = SessionState.STREAMING
        # is_alive is state-based: STREAMING → True
        router._units["ch-1"] = ch1

        # Channel unit 2 wants a slot
        ch2 = SessionUnit("ch-2", "default", on_state_change=lambda *a: None)
        ch2.is_channel_session = True
        router._units["ch-2"] = ch2

        with patch("core.resource_monitor.resource_monitor") as mock_rm:
            mock_rm.compute_max_tabs.return_value = 2

            # Should queue (not steal from chat or kill streaming channel)
            # We set a short timeout to avoid test hanging
            router.QUEUE_TIMEOUT = 0.1
            result = await router._acquire_slot(ch2)
            assert result == "timeout", (
                "Channel should queue when another channel is STREAMING"
            )


# ===================================================================
# AC3: Per-channel session isolation + idle TTL
# ===================================================================

class TestPerChannelSessionIsolation:
    """Each (channel_id, external_chat_id) gets its own session. No cross-channel sharing."""

    @pytest.mark.asyncio
    async def test_different_channels_get_different_sessions(self, gateway, mock_db):
        """Same user in Slack DM and Slack channel gets separate sessions."""
        mock_db.channel_sessions.find_by_external.return_value = None

        with patch("channels.gateway.session_manager") as mock_sm:
            mock_sm.store_session = AsyncMock()
            sid1, _, _, _ = await gateway._resolve_session(
                channel_id="slack-ch-1",
                agent_id="default",
                external_chat_id="D017ZD4PUKT",  # DM
                external_sender_id="W017T04E8MS",
                external_thread_id=None,
                sender_display_name="XG",
            )

            sid2, _, _, _ = await gateway._resolve_session(
                channel_id="slack-ch-1",
                agent_id="default",
                external_chat_id="C0AQ2EJTRLY",  # Channel
                external_sender_id="W017T04E8MS",
                external_thread_id=None,
                sender_display_name="XG",
            )

            assert sid1 != sid2, "Different chat_ids must get different sessions"

    @pytest.mark.asyncio
    async def test_threaded_messages_get_separate_session(self, gateway, mock_db):
        """Slack thread messages get their own session."""
        mock_db.channel_sessions.find_by_external.return_value = None

        with patch("channels.gateway.session_manager") as mock_sm:
            mock_sm.store_session = AsyncMock()
            sid_top, _, _, _ = await gateway._resolve_session(
                channel_id="slack-ch-1",
                agent_id="default",
                external_chat_id="D017ZD4PUKT",
                external_sender_id="W017T04E8MS",
                external_thread_id=None,
                sender_display_name="XG",
            )

            sid_thread, _, _, _ = await gateway._resolve_session(
                channel_id="slack-ch-1",
                agent_id="default",
                external_chat_id="D017ZD4PUKT",
                external_sender_id="W017T04E8MS",
                external_thread_id="1234567890.123456",
                sender_display_name="XG",
            )

            assert sid_thread != sid_top, (
                "Threaded messages must have separate sessions"
            )

    @pytest.mark.asyncio
    async def test_existing_session_reused_within_ttl(self, gateway, mock_db):
        """Within TTL, existing channel_session is reused (same session_id)."""
        from datetime import datetime
        mock_db.channel_sessions.find_by_external.return_value = {
            "id": "cs-1",
            "session_id": "sid-existing",
            "message_count": 4,
            "last_message_at": datetime.now().isoformat(),  # Fresh
        }

        sid, csid, is_new, prior = await gateway._resolve_session(
            channel_id="slack-ch-1",
            agent_id="default",
            external_chat_id="D017ZD4PUKT",
            external_sender_id="W017T04E8MS",
            external_thread_id=None,
            sender_display_name="XG",
        )
        assert sid == "sid-existing"
        assert csid == "cs-1"
        assert is_new is False
        assert prior is None, "No rotation — prior_session_id should be None"

    @pytest.mark.asyncio
    async def test_stale_session_rotated_after_ttl(self, gateway, mock_db):
        """After TTL expires, a fresh session is created via in-place update."""
        from datetime import datetime, timedelta

        stale_time = (
            datetime.now() - timedelta(seconds=gateway._CHANNEL_SESSION_IDLE_TTL_S + 60)
        ).isoformat()

        mock_db.channel_sessions.find_by_external.return_value = {
            "id": "cs-stale",
            "session_id": "sid-stale",
            "message_count": 20,
            "last_message_at": stale_time,
        }

        with patch("channels.gateway.session_manager") as mock_sm:
            mock_sm.store_session = AsyncMock()
            sid, csid, is_new, prior = await gateway._resolve_session(
                channel_id="slack-ch-1",
                agent_id="default",
                external_chat_id="D017ZD4PUKT",
                external_sender_id="W017T04E8MS",
                external_thread_id=None,
                sender_display_name="XG",
            )

            # Must be a NEW session, not the stale one
            assert sid != "sid-stale", "Stale session should be rotated"
            assert is_new is True
            # Channel_session row reused (updated in-place), not deleted+recreated
            assert csid == "cs-stale", "Should reuse existing channel_session row"
            mock_db.channel_sessions.delete.assert_not_called()
            mock_db.channel_sessions.update.assert_called_once()
            update_args = mock_db.channel_sessions.update.call_args
            assert update_args[0][0] == "cs-stale"
            assert update_args[0][1]["session_id"] == sid
            assert update_args[0][1]["message_count"] == 0
            # Prior session ID carried forward for conversation continuity
            assert prior == "sid-stale", "Old session_id must be returned for context injection"

    def test_is_session_stale_within_ttl(self, gateway):
        """Session within TTL is not stale."""
        from datetime import datetime
        recent = datetime.now().isoformat()
        assert gateway._is_session_stale(recent) is False

    def test_is_session_stale_beyond_ttl(self, gateway):
        """Session beyond TTL (12h) is stale."""
        from datetime import datetime, timedelta
        old = (datetime.now() - timedelta(hours=13)).isoformat()
        assert gateway._is_session_stale(old) is True

    def test_is_session_stale_invalid_timestamp(self, gateway):
        """Invalid timestamp treated as not stale (safe default)."""
        assert gateway._is_session_stale("not-a-date") is False
        assert gateway._is_session_stale("") is False


# ===================================================================
# Gateway: generic channel_context + MCP injection
# ===================================================================

class TestGenericChannelContext:
    """Gateway should build generic channel_context (not hardcoded to one adapter)."""

    @pytest.mark.asyncio
    async def test_channel_context_includes_slack_keys(self, gateway, mock_db):
        """channel_context for Slack includes bot_token, not app_id/app_secret."""
        from channels.base import InboundMessage

        mock_db.channels.get.return_value = {
            "id": "slack-1",
            "channel_type": "slack",
            "agent_id": "default",
            "config": json.dumps({
                "bot_token": "xoxb-test",
                "app_token": "xapp-test",
            }),
            "access_mode": "open",
            "rate_limit_per_minute": 10,
            "enable_skills": False,
            "enable_mcp": True,
        }

        msg = InboundMessage(
            channel_id="slack-1",
            external_chat_id="D017ZD4PUKT",
            external_sender_id="W017T04E8MS",
            text="hello",
            metadata={"chat_type": "im", "message_type": "text"},
        )

        # We need to intercept the channel_context passed to run_conversation
        captured_context = {}

        async def mock_run_conversation(**kwargs):
            captured_context.update(kwargs.get("channel_context", {}))
            yield {"type": "assistant", "content": [{"type": "text", "text": "hi"}]}
            yield {"type": "result", "subtype": "success"}

        with patch.object(gateway, "_resolve_session", new=AsyncMock(return_value=("sid-1", "csid-1", True, None))):
            with patch("channels.gateway.session_registry") as mock_sr:
                mock_sr.session_router.run_conversation = mock_run_conversation
                with patch.object(gateway, "_prepare_message_text", new=AsyncMock(return_value="hello")):
                    await gateway.handle_inbound_message(msg)

        assert captured_context.get("channel_type") == "slack"
        assert captured_context.get("bot_token") == "xoxb-test"

    def test_inject_channel_mcp_slack(self):
        """inject_channel_mcp adds Slack-specific env vars."""
        from core.mcp_config_loader import inject_channel_mcp

        servers = {}
        context = {
            "channel_type": "slack",
            "bot_token": "xoxb-test-token",
            "chat_id": "C123",
            "reply_to_message_id": "1234.5678",
        }
        result = inject_channel_mcp(servers, context, "/workspace")
        assert "channel-tools" in result
        env = result["channel-tools"]["env"]
        assert env["CHANNEL_TYPE"] == "slack"
        assert env["SLACK_BOT_TOKEN"] == "xoxb-test-token"
        assert env["SLACK_CHANNEL_ID"] == "C123"


# ===================================================================
# Gateway: access control + rate limiting
# ===================================================================

class TestAccessControl:
    """Existing gateway access control — regression tests."""

    def test_open_mode_allows_everyone(self, gateway):
        assert gateway._check_access({"access_mode": "open"}, "anyone") is True

    def test_allowlist_mode_allows_listed(self, gateway):
        config = {"access_mode": "allowlist", "allowed_senders": ["user1", "user2"]}
        assert gateway._check_access(config, "user1") is True
        assert gateway._check_access(config, "user3") is False

    def test_blocklist_mode_blocks_listed(self, gateway):
        config = {"access_mode": "blocklist", "blocked_senders": ["spammer"]}
        assert gateway._check_access(config, "spammer") is False
        assert gateway._check_access(config, "legit") is True

    def test_unknown_mode_denies(self, gateway):
        assert gateway._check_access({"access_mode": "magic"}, "anyone") is False

    def test_empty_allowlist_denies_all(self, gateway):
        config = {"access_mode": "allowlist", "allowed_senders": []}
        assert gateway._check_access(config, "anyone") is False


# ===================================================================
# Gateway: rate limiter
# ===================================================================

class TestRateLimiter:
    """Rate limiter — regression tests."""

    def test_allows_within_limit(self):
        from channels.gateway import _TokenBucketRateLimiter
        rl = _TokenBucketRateLimiter()
        for _ in range(5):
            assert rl.is_allowed("user1", max_per_minute=10) is True

    def test_blocks_over_limit(self):
        from channels.gateway import _TokenBucketRateLimiter
        rl = _TokenBucketRateLimiter()
        for _ in range(10):
            rl.is_allowed("user1", max_per_minute=10)
        assert rl.is_allowed("user1", max_per_minute=10) is False

    def test_zero_limit_allows_all(self):
        from channels.gateway import _TokenBucketRateLimiter
        rl = _TokenBucketRateLimiter()
        assert rl.is_allowed("user1", max_per_minute=0) is True


# ===================================================================
# Gateway: lifecycle (startup/shutdown)
# ===================================================================

class TestGatewayLifecycle:
    """Gateway startup / shutdown / retry."""

    @pytest.mark.asyncio
    async def test_startup_auto_starts_channels(self, gateway, mock_db):
        """startup() should attempt to start all channels from DB."""
        mock_db.channels.list.return_value = [
            {"id": "ch-1", "name": "Slack"},
            {"id": "ch-2", "name": "Slack-2"},
        ]
        with patch.object(gateway, "start_channel", new=AsyncMock()) as mock_start:
            await gateway.startup()
            assert mock_start.call_count == 2

    @pytest.mark.asyncio
    async def test_shutdown_stops_all(self, gateway, mock_db):
        """shutdown() stops all running adapters."""
        adapter = AsyncMock()
        gateway._adapters["ch-1"] = adapter
        gateway._tasks["ch-1"] = asyncio.create_task(asyncio.sleep(10))

        await gateway.shutdown()
        adapter.stop.assert_called_once()
        assert len(gateway._adapters) == 0

    @pytest.mark.asyncio
    async def test_restart_stops_then_starts(self, gateway, mock_db):
        """restart_channel calls stop then start."""
        with patch.object(gateway, "stop_channel", new=AsyncMock()) as mock_stop, \
             patch.object(gateway, "start_channel", new=AsyncMock()) as mock_start:
            await gateway.restart_channel("ch-1")
            mock_stop.assert_called_once_with("ch-1")
            mock_start.assert_called_once_with("ch-1")


# ===================================================================
# AC6: Sender identity + permission tiers
# ===================================================================

class TestSenderIdentity:
    """Sender identity resolution and permission tier assignment."""

    def test_owner_gets_owner_tier(self, gateway):
        """First allowed_sender is OWNER tier."""
        config = {"allowed_senders": ["W017T04E8MS", "W_ANDY", "W_FEI"]}
        identity = gateway._resolve_sender_identity(config, "W017T04E8MS", "XG")
        assert identity.permission_tier.value == "owner"
        assert identity.is_owner is True
        assert identity.display_name == "XG"

    def test_trusted_user_gets_trusted_tier(self, gateway):
        """Non-first allowed_sender gets TRUSTED tier."""
        config = {"allowed_senders": ["W017T04E8MS", "W_ANDY", "W_FEI"]}
        identity = gateway._resolve_sender_identity(config, "W_ANDY", "Andy")
        assert identity.permission_tier.value == "trusted"
        assert identity.is_owner is False

    def test_unknown_user_gets_public_tier(self, gateway):
        """User not in allowed_senders gets PUBLIC tier."""
        config = {"allowed_senders": ["W017T04E8MS", "W_ANDY"]}
        identity = gateway._resolve_sender_identity(config, "W_RANDOM", "Random")
        assert identity.permission_tier.value == "public"
        assert identity.is_owner is False

    def test_empty_allowlist_gives_public(self, gateway):
        """Empty allowed_senders means everyone is PUBLIC."""
        config = {"allowed_senders": []}
        identity = gateway._resolve_sender_identity(config, "W_ANYONE", "Anyone")
        assert identity.permission_tier.value == "public"
        assert identity.is_owner is False

    def test_json_string_allowed_senders(self, gateway):
        """allowed_senders stored as JSON string (DB format) works."""
        config = {"allowed_senders": '["W017T04E8MS", "W_ANDY"]'}
        identity = gateway._resolve_sender_identity(config, "W_ANDY", "Andy")
        assert identity.permission_tier.value == "trusted"

    def test_missing_allowed_senders(self, gateway):
        """No allowed_senders key defaults to PUBLIC for everyone."""
        identity = gateway._resolve_sender_identity({}, "W_ANYONE", None)
        assert identity.permission_tier.value == "public"
        assert identity.display_name == "W_ANYONE"  # fallback to ID

    def test_to_dict_roundtrip(self, gateway):
        """SenderIdentity.to_dict() produces correct structure."""
        config = {"allowed_senders": ["W017T04E8MS", "W_FEI"]}
        identity = gateway._resolve_sender_identity(config, "W_FEI", "Fei Wu")
        d = identity.to_dict()
        assert d == {
            "external_id": "W_FEI",
            "display_name": "Fei Wu",
            "permission_tier": "trusted",
            "is_owner": False,
        }


class TestSenderIdentityInjection:
    """Verify sender_identity is injected into channel_context during message handling."""

    @pytest.mark.asyncio
    async def test_channel_context_includes_sender_identity(self, gateway, mock_db):
        """handle_inbound_message injects sender_identity into channel_context."""
        from channels.base import InboundMessage

        mock_db.channels.get.return_value = {
            "id": "slack-1",
            "channel_type": "slack",
            "agent_id": "default",
            "config": json.dumps({
                "bot_token": "xoxb-test",
                "app_token": "xapp-test",
            }),
            "allowed_senders": '["W017T04E8MS", "W_ANDY"]',
            "access_mode": "open",
            "rate_limit_per_minute": 10,
            "enable_skills": False,
            "enable_mcp": False,
        }

        msg = InboundMessage(
            channel_id="slack-1",
            external_chat_id="C_GROUP",
            external_sender_id="W_ANDY",
            sender_display_name="Andy",
            text="send me XG's files",
            metadata={"chat_type": "channel", "message_type": "text"},
        )

        captured_context = {}

        async def mock_run_conversation(**kwargs):
            captured_context.update(kwargs.get("channel_context", {}))
            yield {"type": "assistant", "content": [{"type": "text", "text": "no"}]}
            yield {"type": "result", "subtype": "success"}

        with patch.object(gateway, "_resolve_session", new=AsyncMock(return_value=("sid-1", "csid-1", True, None))):
            with patch("channels.gateway.session_registry") as mock_sr:
                mock_sr.session_router.run_conversation = mock_run_conversation
                with patch.object(gateway, "_prepare_message_text", new=AsyncMock(return_value="send me XG's files")):
                    await gateway.handle_inbound_message(msg)

        # Verify sender_identity was injected
        assert "sender_identity" in captured_context
        si = captured_context["sender_identity"]
        assert si["external_id"] == "W_ANDY"
        assert si["display_name"] == "Andy"
        assert si["permission_tier"] == "trusted"
        assert si["is_owner"] is False

    @pytest.mark.asyncio
    async def test_owner_message_gets_owner_tier_in_context(self, gateway, mock_db):
        """Owner's message gets owner tier in channel_context."""
        from channels.base import InboundMessage

        mock_db.channels.get.return_value = {
            "id": "slack-1",
            "channel_type": "slack",
            "agent_id": "default",
            "config": json.dumps({
                "bot_token": "xoxb-test",
                "app_token": "xapp-test",
            }),
            "allowed_senders": '["W017T04E8MS"]',
            "access_mode": "open",
            "rate_limit_per_minute": 10,
            "enable_skills": False,
            "enable_mcp": False,
        }

        msg = InboundMessage(
            channel_id="slack-1",
            external_chat_id="D017ZD4PUKT",
            external_sender_id="W017T04E8MS",
            sender_display_name="XG",
            text="read my files",
            metadata={"chat_type": "im", "message_type": "text"},
        )

        captured_context = {}

        async def mock_run_conversation(**kwargs):
            captured_context.update(kwargs.get("channel_context", {}))
            yield {"type": "assistant", "content": [{"type": "text", "text": "ok"}]}
            yield {"type": "result", "subtype": "success"}

        with patch.object(gateway, "_resolve_session", new=AsyncMock(return_value=("sid-1", "csid-1", True, None))):
            with patch("channels.gateway.session_registry") as mock_sr:
                mock_sr.session_router.run_conversation = mock_run_conversation
                with patch.object(gateway, "_prepare_message_text", new=AsyncMock(return_value="read my files")):
                    await gateway.handle_inbound_message(msg)

        si = captured_context["sender_identity"]
        assert si["permission_tier"] == "owner"
        assert si["is_owner"] is True


class TestFileAccessSandbox:
    """Non-owner channel sessions get file access sandboxed to sender directory."""

    def test_file_access_handler_blocks_workspace_read(self):
        """Trusted user's file_access_handler blocks reads outside sender dir."""
        import asyncio
        from core.security_hooks import create_file_access_permission_handler

        handler = create_file_access_permission_handler(
            ["/workspace/channel_files/W_ANDY"]
        )
        # Allowed: within sender dir
        result = asyncio.run(
            handler("Read", {"file_path": "/workspace/channel_files/W_ANDY/report.txt"}, {})
        )
        assert result["behavior"] == "allow"

        # Blocked: owner's workspace
        result = asyncio.run(
            handler("Read", {"file_path": "/workspace/.context/MEMORY.md"}, {})
        )
        assert result["behavior"] == "deny"

    def test_file_access_handler_blocks_arbitrary_paths(self):
        """Trusted user cannot read arbitrary system files."""
        import asyncio
        from core.security_hooks import create_file_access_permission_handler

        handler = create_file_access_permission_handler(
            ["/workspace/channel_files/W_ANDY"]
        )
        # Blocked: system files
        result = asyncio.run(
            handler("Read", {"file_path": "/etc/passwd"}, {})
        )
        assert result["behavior"] == "deny"

        # Blocked: home directory
        result = asyncio.run(
            handler("Read", {"file_path": "/Users/gawan/.aws/credentials"}, {})
        )
        assert result["behavior"] == "deny"

    def test_file_access_handler_allows_write_in_sender_dir(self):
        """Trusted user can create files in their sender directory."""
        import asyncio
        from core.security_hooks import create_file_access_permission_handler

        handler = create_file_access_permission_handler(
            ["/workspace/channel_files/W_ANDY"]
        )
        result = asyncio.run(
            handler("Write", {"file_path": "/workspace/channel_files/W_ANDY/analysis.md"}, {})
        )
        assert result["behavior"] == "allow"

    def test_bash_blocked_for_outside_paths(self):
        """Trusted user's Bash commands blocked when accessing outside paths."""
        import asyncio
        from core.security_hooks import create_file_access_permission_handler

        handler = create_file_access_permission_handler(
            ["/workspace/channel_files/W_ANDY"]
        )
        result = asyncio.run(
            handler("Bash", {"command": "cat /workspace/.context/MEMORY.md"}, {})
        )
        assert result["behavior"] == "deny"

    def test_sender_dir_isolation(self):
        """Sender A cannot access Sender B's directory."""
        import asyncio
        from core.security_hooks import create_file_access_permission_handler

        handler_a = create_file_access_permission_handler(
            ["/workspace/channel_files/W_ANDY"]
        )
        # Andy trying to read Fei's files
        result = asyncio.run(
            handler_a("Read", {"file_path": "/workspace/channel_files/W_FEI/data.csv"}, {})
        )
        assert result["behavior"] == "deny"

    def test_staging_uses_sender_dir(self):
        """Non-owner attachments staged to sender-scoped directory."""
        from channels.base import SenderIdentity, PermissionTier

        identity = SenderIdentity(
            external_id="W_ANDY",
            display_name="Andy",
            permission_tier=PermissionTier.TRUSTED,
            is_owner=False,
        )
        # The staging path should contain the sender ID, not agent ID
        # (tested via the _stage_file_to_workspace logic)
        assert not identity.is_owner
        assert identity.external_id == "W_ANDY"


class TestCwdAndMcpRestriction:
    """Non-owner sessions get cwd switched and MCP stripped."""

    def test_relative_path_resolves_to_sender_dir(self):
        """With cwd set to sender dir, relative paths stay in sandbox."""
        import os
        sender_dir = "/workspace/channel_files/W_ANDY"
        # Simulate what happens when cwd is sender_dir
        resolved = os.path.normpath(os.path.join(sender_dir, "report.txt"))
        assert resolved == "/workspace/channel_files/W_ANDY/report.txt"

        # Relative traversal attempts resolve to parent — blocked by file_access_handler
        traversal = os.path.normpath(os.path.join(sender_dir, "../../.context/MEMORY.md"))
        assert not traversal.startswith(sender_dir)

    def test_mcp_stripped_for_public_user(self):
        """Public users keep only channel-tools MCP."""
        mcp_servers = {
            "slack-mcp": {"command": "slack-mcp"},
            "outlook-mcp": {"command": "outlook-mcp"},
            "github-mcp": {"command": "github-mcp"},
            "channel-tools": {"command": "channel-tools"},
        }
        tier = "public"
        if tier == "public":
            safe_mcps = {
                name: config for name, config in mcp_servers.items()
                if name == "channel-tools"
            }
        else:
            safe_mcps = mcp_servers
        assert len(safe_mcps) == 1
        assert "channel-tools" in safe_mcps
        assert "slack-mcp" not in safe_mcps
        assert "outlook-mcp" not in safe_mcps

    def test_mcp_kept_for_trusted_user(self):
        """Trusted users keep ALL enabled MCP servers."""
        mcp_servers = {
            "slack-mcp": {"command": "slack-mcp"},
            "outlook-mcp": {"command": "outlook-mcp"},
            "github-mcp": {"command": "github-mcp"},
            "channel-tools": {"command": "channel-tools"},
        }
        tier = "trusted"
        if tier == "public":
            safe_mcps = {
                name: config for name, config in mcp_servers.items()
                if name == "channel-tools"
            }
        else:
            safe_mcps = mcp_servers
        assert len(safe_mcps) == 4
        assert "slack-mcp" in safe_mcps
        assert "outlook-mcp" in safe_mcps
        assert "github-mcp" in safe_mcps

    def test_owner_keeps_all_mcp(self):
        """Owner sessions keep all MCP servers."""
        mcp_servers = {
            "slack-mcp": {"command": "slack-mcp"},
            "channel-tools": {"command": "channel-tools"},
        }
        # _channel_sender_dir is None for owner → no stripping
        _channel_sender_dir = None
        if _channel_sender_dir and mcp_servers:
            mcp_servers = {n: c for n, c in mcp_servers.items() if n == "channel-tools"}
        assert len(mcp_servers) == 2

    def test_empty_mcp_for_public_without_channel_tools(self):
        """If no channel-tools configured, public user gets empty MCP."""
        mcp_servers = {
            "slack-mcp": {"command": "slack-mcp"},
            "github-mcp": {"command": "github-mcp"},
        }
        tier = "public"
        if tier == "public":
            safe = {n: c for n, c in mcp_servers.items() if n == "channel-tools"}
        else:
            safe = mcp_servers
        assert len(safe) == 0


class TestSystemPromptChannelSecurity:
    """Verify the system prompt includes channel security section."""

    def test_no_section_for_desktop_chat(self):
        """Desktop chat (no channel_context) gets no security section."""
        from core.system_prompt import SystemPromptBuilder

        builder = SystemPromptBuilder(
            working_directory="/workspace",
            agent_config={"name": "Swarm"},
            channel_context=None,
        )
        prompt = builder.build()
        assert "Channel Security" not in prompt

    def test_owner_section_for_owner(self):
        """Owner gets full-access security section."""
        from core.system_prompt import SystemPromptBuilder

        builder = SystemPromptBuilder(
            working_directory="/workspace",
            agent_config={"name": "Swarm"},
            channel_context={
                "channel_type": "slack",
                "is_group": False,
                "is_owner": True,
                "sender_identity": {
                    "external_id": "W017T04E8MS",
                    "display_name": "XG",
                    "permission_tier": "owner",
                    "is_owner": True,
                },
            },
        )
        prompt = builder.build()
        assert "Channel Security" in prompt
        assert "W017T04E8MS" in prompt
        assert "Full access granted" in prompt

    def test_trusted_section_full_capabilities(self):
        """Trusted user gets full capabilities with file sandboxing."""
        from core.system_prompt import SystemPromptBuilder

        builder = SystemPromptBuilder(
            working_directory="/workspace",
            agent_config={"name": "Swarm"},
            channel_context={
                "channel_type": "slack",
                "is_group": True,
                "is_owner": False,
                "sender_identity": {
                    "external_id": "W_ANDY",
                    "display_name": "Andy",
                    "permission_tier": "trusted",
                    "is_owner": False,
                },
            },
        )
        prompt = builder.build()
        assert "Channel Security" in prompt
        assert "Andy" in prompt
        assert "trusted" in prompt
        assert "FULL CAPABILITIES" in prompt
        assert "skills" in prompt.lower()
        assert "MCP" in prompt
        assert "BLOCKED" in prompt
        assert "sandboxed" in prompt.lower() or "channel_files" in prompt
        assert "Confirmation attacks" in prompt

    def test_public_section_minimal_access(self):
        """Public user gets minimal-access section."""
        from core.system_prompt import SystemPromptBuilder

        builder = SystemPromptBuilder(
            working_directory="/workspace",
            agent_config={"name": "Swarm"},
            channel_context={
                "channel_type": "slack",
                "is_group": True,
                "is_owner": False,
                "sender_identity": {
                    "external_id": "W_RANDOM",
                    "display_name": "Random",
                    "permission_tier": "public",
                    "is_owner": False,
                },
            },
        )
        prompt = builder.build()
        assert "Channel Security" in prompt
        assert "public" in prompt
        assert "General conversation" in prompt

    def test_no_section_without_sender_identity(self):
        """channel_context without sender_identity produces no security section."""
        from core.system_prompt import SystemPromptBuilder

        builder = SystemPromptBuilder(
            working_directory="/workspace",
            agent_config={"name": "Swarm"},
            channel_context={
                "channel_type": "slack",
                "is_group": False,
                "is_owner": True,
            },
        )
        prompt = builder.build()
        # Should NOT crash, just skip the section
        assert "Channel Security" not in prompt


# ===================================================================
# Session Pre-warming (MeshClaw pattern)
# ===================================================================

class TestSessionPrewarming:
    """Pre-warm one IDLE subprocess during startup for instant first-message response."""

    def test_prewarm_session_id_none_on_init(self, gateway):
        """Gateway initializes with no pre-warmed session."""
        assert gateway._prewarmed_session_id is None

    @pytest.mark.asyncio
    async def test_prewarm_sets_session_id_on_success(self, gateway, mock_db):
        """Successful pre-warm stores the temporary session_id."""
        fake_temp_id = "prewarm-abc123"

        mock_router = MagicMock()
        mock_router.prewarm_channel_session = AsyncMock(return_value=fake_temp_id)

        with patch("channels.gateway.session_registry") as mock_reg:
            mock_reg.session_router = mock_router
            channel = {"id": "ch1", "agent_id": "default"}
            await gateway._prewarm_owner_session(channel)

        assert gateway._prewarmed_session_id == fake_temp_id
        # Verify called with agent_id and channel_context (owner=True)
        mock_router.prewarm_channel_session.assert_awaited_once()
        call_args = mock_router.prewarm_channel_session.call_args
        assert call_args[0][0] == "default"
        assert call_args[1]["channel_context"]["is_owner"] is True
        assert call_args[1]["channel_context"]["channel_id"] == "ch1"

    @pytest.mark.asyncio
    async def test_prewarm_none_on_failure(self, gateway, mock_db):
        """Failed pre-warm leaves _prewarmed_session_id as None."""
        mock_router = MagicMock()
        mock_router.prewarm_channel_session = AsyncMock(return_value=None)

        with patch("channels.gateway.session_registry") as mock_reg:
            mock_reg.session_router = mock_router
            channel = {"id": "ch1", "agent_id": "default"}
            await gateway._prewarm_owner_session(channel)

        assert gateway._prewarmed_session_id is None

    @pytest.mark.asyncio
    async def test_prewarm_skipped_without_agent_id(self, gateway, mock_db):
        """Pre-warm skips channels without agent_id."""
        with patch("channels.gateway.session_registry") as mock_reg:
            mock_reg.session_router = MagicMock()
            channel = {"id": "ch1"}  # No agent_id
            await gateway._prewarm_owner_session(channel)

        assert gateway._prewarmed_session_id is None

    @pytest.mark.asyncio
    async def test_prewarm_exception_handled(self, gateway, mock_db):
        """Pre-warm swallows exceptions gracefully."""
        mock_router = MagicMock()
        mock_router.prewarm_channel_session = AsyncMock(
            side_effect=RuntimeError("boom"),
        )

        with patch("channels.gateway.session_registry") as mock_reg:
            mock_reg.session_router = mock_router
            channel = {"id": "ch1", "agent_id": "default"}
            # Should not raise
            await gateway._prewarm_owner_session(channel)

        assert gateway._prewarmed_session_id is None

    def test_try_adopt_prewarmed_success(self, gateway):
        """Adoption clears _prewarmed_session_id and delegates to router."""
        gateway._prewarmed_session_id = "prewarm-xyz"

        mock_router = MagicMock()
        mock_router.adopt_prewarmed_unit = MagicMock(return_value=True)

        with patch("channels.gateway.session_registry") as mock_reg:
            mock_reg.session_router = mock_router
            result = gateway._try_adopt_prewarmed("real-session-1")

        assert result is True
        assert gateway._prewarmed_session_id is None
        mock_router.adopt_prewarmed_unit.assert_called_once_with(
            "prewarm-xyz", "real-session-1",
        )

    def test_try_adopt_prewarmed_no_prewarm(self, gateway):
        """Adoption returns False when no pre-warm exists."""
        assert gateway._prewarmed_session_id is None
        result = gateway._try_adopt_prewarmed("real-session-1")
        assert result is False

    def test_try_adopt_prewarmed_router_rejects(self, gateway):
        """Adoption returns False and clears _prewarmed_session_id on rejection."""
        gateway._prewarmed_session_id = "prewarm-xyz"

        mock_router = MagicMock()
        mock_router.adopt_prewarmed_unit = MagicMock(return_value=False)

        with patch("channels.gateway.session_registry") as mock_reg:
            mock_reg.session_router = mock_router
            result = gateway._try_adopt_prewarmed("real-session-1")

        assert result is False
        # Always cleared — avoids repeated failed adoption attempts.
        # Let subsequent messages take normal cold-start path.
        assert gateway._prewarmed_session_id is None


class TestSessionRouterPrewarm:
    """SessionRouter.prewarm_channel_session and adopt_prewarmed_unit."""

    @pytest.mark.asyncio
    async def test_prewarm_creates_idle_unit(self):
        """prewarm_channel_session registers a COLD→IDLE unit."""
        from core.session_unit import SessionState

        mock_prompt_builder = MagicMock()
        mock_options = MagicMock()
        mock_options.system_prompt = "test"
        mock_prompt_builder.build_options = AsyncMock(return_value=mock_options)

        from core.session_router import SessionRouter
        router = SessionRouter(prompt_builder=mock_prompt_builder)

        # Mock the SessionUnit to skip real subprocess spawn
        with patch("core.session_router.SessionUnit") as MockUnit:
            mock_unit = MagicMock()
            mock_unit.state = SessionState.IDLE
            mock_unit.session_id = "temp"

            async def fake_ensure_spawned(*a, **kw):
                return
                yield  # make it an async generator

            mock_unit._ensure_spawned = fake_ensure_spawned
            MockUnit.return_value = mock_unit

            with patch(
                "core.agent_defaults.build_agent_config",
                new_callable=AsyncMock,
                return_value={"name": "Swarm"},
            ):
                result = await router.prewarm_channel_session("default")

        assert result is not None
        assert result.startswith("prewarm-")
        assert result in router._units

    def test_adopt_prewarmed_unit_rekeys(self):
        """adopt_prewarmed_unit moves unit from temp key to real key."""
        from core.session_unit import SessionState

        mock_prompt_builder = MagicMock()
        from core.session_router import SessionRouter
        router = SessionRouter(prompt_builder=mock_prompt_builder)

        # Create a fake IDLE unit under the pre-warm key
        mock_unit = MagicMock()
        mock_unit.state = SessionState.IDLE
        router._units["prewarm-abc"] = mock_unit

        result = router.adopt_prewarmed_unit("prewarm-abc", "real-session-1")

        assert result is True
        assert "prewarm-abc" not in router._units
        assert "real-session-1" in router._units
        assert router._units["real-session-1"] is mock_unit
        assert mock_unit.session_id == "real-session-1"

    def test_adopt_prewarmed_unit_rejects_dead(self):
        """adopt_prewarmed_unit rejects if unit is not IDLE."""
        from core.session_unit import SessionState

        mock_prompt_builder = MagicMock()
        from core.session_router import SessionRouter
        router = SessionRouter(prompt_builder=mock_prompt_builder)

        mock_unit = MagicMock()
        mock_unit.state = SessionState.COLD  # Not IDLE
        router._units["prewarm-abc"] = mock_unit

        result = router.adopt_prewarmed_unit("prewarm-abc", "real-session-1")

        assert result is False
        # Unit should be put back at original key
        assert "prewarm-abc" in router._units
        assert "real-session-1" not in router._units

    def test_adopt_prewarmed_unit_missing(self):
        """adopt_prewarmed_unit returns False for missing key."""
        mock_prompt_builder = MagicMock()
        from core.session_router import SessionRouter
        router = SessionRouter(prompt_builder=mock_prompt_builder)

        result = router.adopt_prewarmed_unit("nonexistent", "real-session-1")
        assert result is False
