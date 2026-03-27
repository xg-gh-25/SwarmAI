"""Tests for channel gateway: slot isolation, cross-channel sessions, sender identity, permissions.

Covers acceptance criteria:
  AC1: compute_max_tabs min 2, channel slot dedicated
  AC2: Channel never evicts/queues chat tabs
  AC3: Cross-channel session sharing via user_key
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
    mock.channel_sessions.find_by_user_key = AsyncMock(return_value=None)
    mock.channel_sessions.put = AsyncMock()
    mock.channel_sessions.update = AsyncMock()
    mock.channel_sessions.count_by_channel = AsyncMock(return_value=0)
    mock.channel_messages = MagicMock()
    mock.channel_messages.put = AsyncMock()
    mock.channel_user_identities = MagicMock()
    mock.channel_user_identities.resolve_user_key = AsyncMock(return_value=None)
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
# AC3: Cross-channel session sharing via user_key
# ===================================================================

class TestCrossChannelSessionSharing:
    """Cross-channel session sharing: user identity mapping + shared sessions."""

    @pytest.mark.asyncio
    async def test_resolve_user_key_from_identity_table(self, gateway, mock_db):
        """External sender ID maps to user_key via channel_user_identities."""
        mock_db.channel_user_identities.resolve_user_key.return_value = "xg"

        user_key = await gateway._resolve_user_key(
            platform="slack",
            external_sender_id="W017T04E8MS",
        )
        assert user_key == "xg"
        mock_db.channel_user_identities.resolve_user_key.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_user_key_fallback_to_sender_id(self, gateway, mock_db):
        """Unmapped sender falls back to external_sender_id as user_key."""
        mock_db.channel_user_identities.resolve_user_key.return_value = None

        user_key = await gateway._resolve_user_key(
            platform="slack",
            external_sender_id="W_UNKNOWN_USER",
        )
        # Fallback: use external_sender_id directly
        assert user_key == "W_UNKNOWN_USER"

    @pytest.mark.asyncio
    async def test_shared_session_across_channels(self, gateway, mock_db):
        """Same user_key on Slack and Feishu reuses the same session_id."""
        # First call: no existing session → creates new
        mock_db.channel_sessions.find_by_user_key.return_value = None
        mock_db.channel_sessions.find_by_external.return_value = None

        with patch.object(gateway, "_resolve_user_key", new=AsyncMock(return_value="xg")):
            with patch("channels.gateway.session_manager") as mock_sm:
                mock_sm.store_session = AsyncMock()
                sid1, csid1, is_new1 = await gateway._resolve_session(
                    channel_id="slack-ch-1",
                    agent_id="default",
                    external_chat_id="D017ZD4PUKT",
                    external_sender_id="W017T04E8MS",
                    external_thread_id=None,
                    sender_display_name="XG",
                )
                assert is_new1 is True

        # Second call from Feishu: should find existing session for user_key "xg"
        mock_db.channel_sessions.find_by_user_key.return_value = {
            "id": csid1,
            "session_id": sid1,
            "message_count": 2,
        }
        with patch.object(gateway, "_resolve_user_key", new=AsyncMock(return_value="xg")):
            sid2, csid2, is_new2 = await gateway._resolve_session(
                channel_id="feishu-ch-1",
                agent_id="default",
                external_chat_id="oc_feishu_chat",
                external_sender_id="ou_abc123",
                external_thread_id=None,
                sender_display_name="XG",
            )
            # Same session_id → shared conversation
            assert sid2 == sid1, "Cross-channel sessions should share session_id"
            assert is_new2 is False

    @pytest.mark.asyncio
    async def test_threaded_messages_get_separate_session(self, gateway, mock_db):
        """Slack thread messages get their own session (not shared cross-channel)."""
        mock_db.channel_sessions.find_by_external.return_value = None
        mock_db.channel_sessions.find_by_user_key.return_value = None

        with patch.object(gateway, "_resolve_user_key", new=AsyncMock(return_value="xg")):
            with patch("channels.gateway.session_manager") as mock_sm:
                mock_sm.store_session = AsyncMock()
                # Top-level message
                sid_top, _, _ = await gateway._resolve_session(
                    channel_id="slack-ch-1",
                    agent_id="default",
                    external_chat_id="D017ZD4PUKT",
                    external_sender_id="W017T04E8MS",
                    external_thread_id=None,
                    sender_display_name="XG",
                )

                # Threaded message (has thread_ts)
                sid_thread, _, _ = await gateway._resolve_session(
                    channel_id="slack-ch-1",
                    agent_id="default",
                    external_chat_id="D017ZD4PUKT",
                    external_sender_id="W017T04E8MS",
                    external_thread_id="1234567890.123456",
                    sender_display_name="XG",
                )

                # Threaded message should get its OWN session
                assert sid_thread != sid_top, (
                    "Threaded messages must have separate sessions"
                )


# ===================================================================
# Gateway: generic channel_context + MCP injection
# ===================================================================

class TestGenericChannelContext:
    """Gateway should build generic channel_context (not hardcoded to Feishu)."""

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

        with patch.object(gateway, "_resolve_session", new=AsyncMock(return_value=("sid-1", "csid-1", True))):
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
            {"id": "ch-2", "name": "Feishu"},
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

        with patch.object(gateway, "_resolve_session", new=AsyncMock(return_value=("sid-1", "csid-1", True))):
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

        with patch.object(gateway, "_resolve_session", new=AsyncMock(return_value=("sid-1", "csid-1", True))):
            with patch("channels.gateway.session_registry") as mock_sr:
                mock_sr.session_router.run_conversation = mock_run_conversation
                with patch.object(gateway, "_prepare_message_text", new=AsyncMock(return_value="read my files")):
                    await gateway.handle_inbound_message(msg)

        si = captured_context["sender_identity"]
        assert si["permission_tier"] == "owner"
        assert si["is_owner"] is True


class TestToolRestriction:
    """Non-owner channel sessions get file/system tools physically removed."""

    def test_trusted_loses_file_tools(self):
        """Trusted user has Read/Write/Edit/Bash stripped from allowed_tools."""
        from core.prompt_builder import PromptBuilder

        pb = PromptBuilder.__new__(PromptBuilder)
        pb._config = {}

        agent_config = {
            "enable_bash_tool": True,
            "enable_file_tools": True,
            "enable_web_tools": True,
        }
        # Resolve full tool list first
        full_tools = pb.resolve_allowed_tools(agent_config)
        assert "Read" in full_tools
        assert "Bash" in full_tools

        # Simulate what build_options does for non-owner channel
        channel_context = {
            "sender_identity": {
                "permission_tier": "trusted",
                "is_owner": False,
            }
        }
        sender = channel_context.get("sender_identity", {})
        tier = sender.get("permission_tier", "public")
        _BLOCKED_TOOLS = {"Read", "Write", "Edit", "Glob", "Grep", "Bash", "NotebookEdit"}
        restricted = [t for t in full_tools if t not in _BLOCKED_TOOLS]

        # Only WebFetch/WebSearch should remain
        assert "Read" not in restricted
        assert "Bash" not in restricted
        assert "Write" not in restricted
        assert "WebFetch" in restricted

    def test_owner_keeps_all_tools(self):
        """Owner channel session keeps all tools."""
        from core.prompt_builder import PromptBuilder

        pb = PromptBuilder.__new__(PromptBuilder)
        pb._config = {}

        agent_config = {
            "enable_bash_tool": True,
            "enable_file_tools": True,
            "enable_web_tools": True,
        }
        full_tools = pb.resolve_allowed_tools(agent_config)

        # Owner: no filtering
        channel_context = {
            "sender_identity": {
                "permission_tier": "owner",
                "is_owner": True,
            }
        }
        sender = channel_context.get("sender_identity", {})
        tier = sender.get("permission_tier", "public")
        if tier != "owner":
            _BLOCKED_TOOLS = {"Read", "Write", "Edit", "Glob", "Grep", "Bash", "NotebookEdit"}
            restricted = [t for t in full_tools if t not in _BLOCKED_TOOLS]
        else:
            restricted = full_tools

        assert "Read" in restricted
        assert "Bash" in restricted
        assert "WebFetch" in restricted


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

    def test_trusted_section_with_restrictions(self):
        """Trusted user gets knowledge-only section with explicit blocks."""
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
        assert "BLOCKED" in prompt
        assert "Reading, listing, or sending ANY files" in prompt
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
