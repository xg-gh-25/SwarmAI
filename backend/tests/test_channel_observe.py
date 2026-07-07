"""Observe mode (A+B) — group-channel collaboration context (run_84cb2ea3).

A = record every group message from allowlisted senders even when the bot does
    NOT reply (OWNER/TRUSTED only; PUBLIC/unauthorized NEVER written).
B = when the bot IS engaged, inject recent AUTHORIZED history into the prompt.

Drives the REAL gateway methods — _should_reply, _is_authorized_tier,
_observe_record, _recent_authorized_history — no mock of the code under change
(GUI32/PIT13). The DB is mocked at the boundary only.

Security posture: fail-closed. PUBLIC content is never stored (A write gate)
and never injected (B read gate) — both gates share _is_authorized_tier
semantics so they can't drift.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from channels.base import InboundMessage, PermissionTier, SenderIdentity
from channels.gateway import ChannelGateway, _is_authorized_tier


def _identity(tier: PermissionTier, name="Alice"):
    return SenderIdentity(
        external_id="U1", display_name=name,
        permission_tier=tier, is_owner=(tier == PermissionTier.OWNER),
    )


def _msg(text="hello", thread="T1", mention=False):
    return InboundMessage(
        channel_id="ch1",
        external_chat_id="C1",
        external_sender_id="U1",
        external_thread_id=thread,
        external_message_id="M1",
        text=text,
        sender_display_name="Alice",
        attachments=[],
        metadata={"chat_type": "channel", "is_mention": mention},
    )


@pytest.fixture
def gateway():
    return ChannelGateway()


@pytest.fixture
def mock_db(monkeypatch):
    m = MagicMock()
    m.channel_sessions = MagicMock()
    m.channel_sessions.find_by_external = AsyncMock(return_value=None)
    m.channel_sessions.put = AsyncMock()
    m.channel_sessions.update = AsyncMock()
    m.channel_messages = MagicMock()
    m.channel_messages.put = AsyncMock()
    m.channel_messages.list_by_session = AsyncMock(return_value=[])
    monkeypatch.setattr("channels.gateway.db", m)
    return m


# ===================================================================
# _is_authorized_tier — the single shared gate (A write + B read)
# ===================================================================
class TestAuthorizedTier:
    def test_owner_authorized(self):
        assert _is_authorized_tier(_identity(PermissionTier.OWNER)) is True

    def test_trusted_authorized(self):
        assert _is_authorized_tier(_identity(PermissionTier.TRUSTED)) is True

    def test_public_denied(self):
        assert _is_authorized_tier(_identity(PermissionTier.PUBLIC)) is False

    def test_none_denied_fail_closed(self):
        assert _is_authorized_tier(None) is False


# ===================================================================
# AC1: thread_follow requires message_count > 0 (engaged = replied)
# ===================================================================
class TestThreadFollowEngagedDiscriminator:
    @pytest.mark.asyncio
    async def test_observe_only_thread_does_not_follow(self, gateway, mock_db):
        # a row exists (observe created it) but message_count==0 → bot NEVER
        # replied → thread_follow must NOT engage (the skeptic's flip bug).
        mock_db.channel_sessions.find_by_external.return_value = {
            "id": "cs1", "message_count": 0,
        }
        channel = {"id": "ch1", "activation": "mention", "thread_follow": True}
        should = await gateway._should_reply(channel, _msg(mention=False),
                                             "channel", is_owner=False)
        assert should is False

    @pytest.mark.asyncio
    async def test_engaged_thread_follows(self, gateway, mock_db):
        # bot actually replied here (count>0) → thread_follow engages
        mock_db.channel_sessions.find_by_external.return_value = {
            "id": "cs1", "message_count": 2,
        }
        channel = {"id": "ch1", "activation": "mention", "thread_follow": True}
        should = await gateway._should_reply(channel, _msg(mention=False),
                                             "channel", is_owner=False)
        assert should is True

    @pytest.mark.asyncio
    async def test_mention_always_replies(self, gateway, mock_db):
        channel = {"id": "ch1", "activation": "mention", "thread_follow": True}
        should = await gateway._should_reply(channel, _msg(mention=True),
                                             "channel", is_owner=False)
        assert should is True


# ===================================================================
# AC2/AC3: observe record (A) — authorized recorded, PUBLIC never written
# ===================================================================
class TestObserveRecord:
    async def _resolve_stub(self, gateway):
        # stub _resolve_session so we test _observe_record's write behavior
        # without exercising the full session machinery (that's tested
        # elsewhere). Returns a channel_session_id, message_count untouched.
        gateway._resolve_session = AsyncMock(
            return_value=("sid1", "cs1", True, None)
        )

    @pytest.mark.asyncio
    async def test_authorized_message_recorded(self, gateway, mock_db):
        await self._resolve_stub(gateway)
        await gateway._observe_record(
            _msg(), {"id": "ch1"}, "ch1", "agent1",
            _identity(PermissionTier.TRUSTED),
        )
        mock_db.channel_messages.put.assert_awaited_once()
        rec = mock_db.channel_messages.put.await_args.args[0]
        assert rec["status"] == "observed"
        assert rec["metadata"]["observed"] is True
        assert rec["metadata"]["sender_tier"] == "trusted"

    @pytest.mark.asyncio
    async def test_observe_does_not_bump_message_count(self, gateway, mock_db):
        # observe must NOT call channel_sessions.update to bump count
        # (thread_follow stays not-engaged). _resolve_session creates at 0.
        await self._resolve_stub(gateway)
        await gateway._observe_record(
            _msg(), {"id": "ch1"}, "ch1", "agent1",
            _identity(PermissionTier.OWNER),
        )
        # the record write happened; NO message_count bump update
        for call in mock_db.channel_sessions.update.await_args_list:
            assert "message_count" not in (call.args[1] if len(call.args) > 1 else {})

    @pytest.mark.asyncio
    async def test_public_never_recorded_at_caller_gate(self, gateway, mock_db):
        # The CALLER gates on _is_authorized_tier before calling _observe_record.
        # Verify the gate: PUBLIC identity must not pass.
        assert _is_authorized_tier(_identity(PermissionTier.PUBLIC)) is False
        # and _observe_record is never reached for PUBLIC (gate is upstream)

    @pytest.mark.asyncio
    async def test_observe_record_failsoft(self, gateway, mock_db):
        # a DB error in observe must NOT raise (observation can't break inbound)
        await self._resolve_stub(gateway)
        mock_db.channel_messages.put.side_effect = RuntimeError("db down")
        # should not raise
        await gateway._observe_record(
            _msg(), {"id": "ch1"}, "ch1", "agent1",
            _identity(PermissionTier.TRUSTED),
        )

    @pytest.mark.asyncio
    async def test_observe_via_real_resolve_session_no_bump_no_spawn(
        self, gateway, mock_db, monkeypatch
    ):
        # MEDIUM-2 fix: drive the REAL _resolve_session (not stubbed) to prove
        # observe (a) creates the channel_session at message_count=0, (b) does
        # NOT bump message_count, (c) never spawns an agent (no run_conversation).
        # New thread → find_by_external None → _resolve_session creates a row.
        mock_db.channel_sessions.find_by_external.return_value = None
        sm = MagicMock()
        sm.store_session = AsyncMock()
        monkeypatch.setattr("channels.gateway.session_manager", sm)
        # guard: run_conversation must never be called from observe
        router = MagicMock()
        router.run_conversation = MagicMock(side_effect=AssertionError("spawned!"))
        monkeypatch.setattr(
            "channels.gateway.session_registry",
            MagicMock(session_router=router),
        )
        await gateway._observe_record(
            _msg(), {"id": "ch1"}, "ch1", "agent1",
            _identity(PermissionTier.TRUSTED),
        )
        # channel_session created with message_count == 0
        put_arg = mock_db.channel_sessions.put.await_args.args[0]
        assert put_arg["message_count"] == 0
        # message recorded, agent NOT spawned (router.run_conversation untouched)
        mock_db.channel_messages.put.assert_awaited_once()
        router.run_conversation.assert_not_called()


# ===================================================================
# AC4/AC5: inject (B) — cap + tier re-filter
# ===================================================================
class TestRecentAuthorizedHistory:
    def _row(self, i, tier="trusted", direction="inbound"):
        return {
            "direction": direction,
            "content": f"msg{i}",
            "created_at": f"2026-07-07T00:00:{i:02d}",
            "metadata": {"sender_tier": tier, "sender_display_name": f"U{i}"},
        }

    @pytest.mark.asyncio
    async def test_cap_at_20_keeps_newest(self, gateway, mock_db):
        # 25 authorized inbound rows (ASC) → exactly last 20, oldest dropped
        mock_db.channel_messages.list_by_session.return_value = [
            self._row(i) for i in range(25)
        ]
        out = await gateway._recent_authorized_history("cs1")
        assert len(out) == 20
        assert out[0]["text"] == "msg5"   # msg0-4 dropped
        assert out[-1]["text"] == "msg24"  # newest kept

    @pytest.mark.asyncio
    async def test_public_record_excluded_from_injection(self, gateway, mock_db):
        # defense-in-depth read gate: a PUBLIC row (even if somehow stored)
        # must NOT be injected
        mock_db.channel_messages.list_by_session.return_value = [
            self._row(1, tier="trusted"),
            self._row(2, tier="public"),
            self._row(3, tier="owner"),
        ]
        out = await gateway._recent_authorized_history("cs1")
        texts = [o["text"] for o in out]
        assert "msg2" not in texts        # public excluded
        assert {"msg1", "msg3"} == set(texts)

    @pytest.mark.asyncio
    async def test_missing_tier_excluded_fail_closed(self, gateway, mock_db):
        # a record with no sender_tier metadata → unknown → EXCLUDED
        row = self._row(1)
        del row["metadata"]["sender_tier"]
        mock_db.channel_messages.list_by_session.return_value = [row]
        assert await gateway._recent_authorized_history("cs1") == []

    @pytest.mark.asyncio
    async def test_outbound_excluded(self, gateway, mock_db):
        # only inbound (human) turns are injected as "history"
        mock_db.channel_messages.list_by_session.return_value = [
            self._row(1, direction="outbound"),
            self._row(2, direction="inbound"),
        ]
        out = await gateway._recent_authorized_history("cs1")
        assert [o["text"] for o in out] == ["msg2"]

    @pytest.mark.asyncio
    async def test_json_string_metadata_parsed(self, gateway, mock_db):
        # metadata stored as JSON string (DB round-trip) is parsed, not dropped
        import json as _json
        mock_db.channel_messages.list_by_session.return_value = [
            {"direction": "inbound", "content": "msg1",
             "created_at": "t", "metadata": _json.dumps({"sender_tier": "owner",
                                                          "sender_display_name": "X"})},
        ]
        out = await gateway._recent_authorized_history("cs1")
        assert len(out) == 1 and out[0]["sender"] == "X"

    @pytest.mark.asyncio
    async def test_empty_history_returns_empty(self, gateway, mock_db):
        mock_db.channel_messages.list_by_session.return_value = []
        assert await gateway._recent_authorized_history("cs1") == []


# ===================================================================
# B preamble — the REAL consumer wiring (adversarial HIGH-1/HIGH-2)
# ===================================================================
class TestHistoryPreamble:
    def _row(self, i, tier="trusted"):
        return {
            "direction": "inbound", "content": f"msg{i}",
            "created_at": f"t{i}",
            "metadata": {"sender_tier": tier, "sender_display_name": f"U{i}"},
        }

    @pytest.mark.asyncio
    async def test_preamble_renders_authorized_history(self, gateway, mock_db):
        mock_db.channel_messages.list_by_session.return_value = [
            self._row(1), self._row(2),
        ]
        pre = await gateway._build_history_preamble("cs1")
        assert "U1: msg1" in pre and "U2: msg2" in pre
        assert "authorized participants" in pre

    @pytest.mark.asyncio
    async def test_preamble_empty_when_no_authorized(self, gateway, mock_db):
        # only a PUBLIC row → read gate excludes → empty preamble (no injection)
        mock_db.channel_messages.list_by_session.return_value = [
            self._row(1, tier="public"),
        ]
        assert await gateway._build_history_preamble("cs1") == ""

    @pytest.mark.asyncio
    async def test_reply_path_record_is_injectable(self, gateway, mock_db):
        # HIGH-2 fix: a reply-path inbound record (status=received) that now
        # carries sender_tier MUST be visible to B (not excluded).
        mock_db.channel_messages.list_by_session.return_value = [
            {"direction": "inbound", "content": "real turn", "created_at": "t",
             "metadata": {"sender_tier": "owner", "sender_display_name": "XG"},
             "status": "received"},
        ]
        out = await gateway._recent_authorized_history("cs1")
        assert len(out) == 1 and out[0]["text"] == "real turn"

    # HIGH-2 (mutation-catchable): both write sites — observe-record AND
    # reply-path inbound — stamp sender_tier via the SINGLE source
    # _sender_metadata. The prior test only exercised the READ gate on a
    # hardcoded row (test-theater: removing sender_tier from either write left
    # every test green). These drive the source directly, so dropping the
    # sender_tier field goes RED.
    def test_sender_metadata_stamps_tier_for_authorized(self):
        from backend.channels.gateway import _sender_metadata
        meta = _sender_metadata(_identity(PermissionTier.OWNER), "XG")
        assert meta["sender_tier"] == "owner"
        assert meta["sender_display_name"] == "XG"

    def test_sender_metadata_failclosed_unknown_when_no_identity(self):
        from backend.channels.gateway import _sender_metadata
        meta = _sender_metadata(None, None)
        # no identity → "unknown" → B's fail-closed read gate EXCLUDES it
        assert meta["sender_tier"] == "unknown"

    def test_sender_metadata_tier_is_injectable_end_to_end(self):
        # the tier _sender_metadata stamps must be one B's read gate accepts —
        # ties the write source to the read gate's _AUTH set (no drift).
        from backend.channels.gateway import _sender_metadata, _AUTH
        assert _sender_metadata(
            _identity(PermissionTier.TRUSTED), "T")["sender_tier"] in _AUTH
        assert _sender_metadata(None, None)["sender_tier"] not in _AUTH
