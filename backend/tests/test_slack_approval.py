"""Slack-native owner approval UI (run_6038cd2c).

Drives the REAL code under change — gateway.add_trusted_sender (the single
approval-path allowlist writer) and slack_approval's owner-check / replay-guard /
block builder. DB mocked at the boundary only (GUI32/PIT13: never mock the code
under change).

Security posture (Gate-1-revised):
  * owner invariant: allowed_senders[0] never displaced (append-only).
  * owner-only: only allowed_senders[0] may act on a card.
  * fail-closed: unknown action / missing / expired / resolved pending → no-op.
  * cache coherence: a write refreshes _channel_cache so the tier flip is not latent.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from channels.base import PermissionTier
from channels.gateway import ChannelGateway, _parse_json_list
from channels import slack_approval as sa


@pytest.fixture
def gateway():
    return ChannelGateway()


def _mock_db(monkeypatch, channel_row):
    """A boundary mock whose channels.get returns a MUTABLE row and whose
    channels.update mutates it — so a re-get after update sees the new value
    (models the real DB round-trip that add_trusted_sender relies on)."""
    m = MagicMock()
    state = {"row": dict(channel_row)}

    async def _get(cid):
        return dict(state["row"]) if state["row"] else None

    async def _update(cid, updates):
        state["row"].update(updates)
        return dict(state["row"])

    m.channels = MagicMock()
    m.channels.get = AsyncMock(side_effect=_get)
    m.channels.update = AsyncMock(side_effect=_update)
    monkeypatch.setattr("channels.gateway.db", m)
    return m, state


# ── add_trusted_sender: the single approval-path writer ──────────────────────

class TestAddTrustedSender:
    @pytest.mark.asyncio
    async def test_appends_and_preserves_owner(self, gateway, monkeypatch):
        _mock_db(monkeypatch, {"id": "ch1", "allowed_senders": '["OWNER"]'})
        added = await gateway.add_trusted_sender("ch1", "ALICE")
        assert added is True
        # Owner still index 0, Alice appended → TRUSTED.
        row = gateway._channel_cache["ch1"]
        allowed = _parse_json_list(row["allowed_senders"])
        assert allowed == ["OWNER", "ALICE"], "append-only; owner immovable at 0"
        # Tier resolves TRUSTED for the new sender, OWNER unchanged.
        assert gateway._resolve_sender_identity(row, "ALICE", "Alice").permission_tier == PermissionTier.TRUSTED
        assert gateway._resolve_sender_identity(row, "OWNER", "XG").permission_tier == PermissionTier.OWNER

    @pytest.mark.asyncio
    async def test_refreshes_cache_so_tier_change_not_latent(self, gateway, monkeypatch):
        """The skeptic's C2: a write that doesn't refresh _channel_cache leaves the
        tier change latent behind a stale cache. Mutation-anchor test."""
        _, state = _mock_db(monkeypatch, {"id": "ch1", "allowed_senders": '["OWNER"]'})
        # Pre-seed a STALE cache (owner-only) — the bug this guards against.
        gateway._channel_cache["ch1"] = {"id": "ch1", "allowed_senders": '["OWNER"]'}
        await gateway.add_trusted_sender("ch1", "ALICE")
        cached = _parse_json_list(gateway._channel_cache["ch1"]["allowed_senders"])
        assert "ALICE" in cached, "cache must be refreshed post-write, not stale"

    @pytest.mark.asyncio
    async def test_idempotent_on_already_present(self, gateway, monkeypatch):
        _mock_db(monkeypatch, {"id": "ch1", "allowed_senders": '["OWNER","ALICE"]'})
        assert await gateway.add_trusted_sender("ch1", "ALICE") is False  # no-op
        assert await gateway.add_trusted_sender("ch1", "OWNER") is False  # owner no-op

    @pytest.mark.asyncio
    async def test_refuses_empty_allowlist_no_owner(self, gateway, monkeypatch):
        """Fail-closed: no owner at index 0 → refuse (can't preserve an invariant
        that doesn't exist yet)."""
        _mock_db(monkeypatch, {"id": "ch1", "allowed_senders": "[]"})
        assert await gateway.add_trusted_sender("ch1", "ALICE") is False

    @pytest.mark.asyncio
    async def test_refuses_blank_owner(self, gateway, monkeypatch):
        """Gate-2 RANK-1 depth: a blank owner at index 0 is not a valid owner →
        refuse (don't let a degenerate config grant TRUSTED)."""
        _mock_db(monkeypatch, {"id": "ch1", "allowed_senders": '[""]'})
        assert await gateway.add_trusted_sender("ch1", "ALICE") is False

    @pytest.mark.asyncio
    async def test_missing_channel_is_noop(self, gateway, monkeypatch):
        _mock_db(monkeypatch, {})  # get returns None
        assert await gateway.add_trusted_sender("nope", "ALICE") is False


# ── slack_approval: owner-only + replay guard + value parsing ────────────────

class TestOwnerOnly:
    def test_only_owner_may_click(self):
        cfg = {"allowed_senders": '["OWNER","ALICE"]'}
        assert sa.is_owner_click(cfg, "OWNER") is True
        assert sa.is_owner_click(cfg, "ALICE") is False   # trusted ≠ owner
        assert sa.is_owner_click(cfg, "RANDO") is False   # public
        assert sa.is_owner_click({"allowed_senders": "[]"}, "ANY") is False  # no owner

    def test_empty_string_owner_never_escalates(self):
        """Gate-2 RANK-1: a degenerate [""] owner + a blank clicker must NOT pass
        via '' == ''. Both owner slot and clicker must be non-empty."""
        assert sa.is_owner_click({"allowed_senders": '[""]'}, "") is False
        assert sa.is_owner_click({"allowed_senders": '[""]'}, "X") is False
        assert sa.is_owner_click({"allowed_senders": '["OWNER"]'}, "") is False


class TestReplayGuard:
    def test_fresh_pending_actionable(self):
        assert sa.pending_is_actionable({"status": "pending", "created_at": 1000.0}, now=1000.0) is True

    def test_resolved_pending_is_noop(self):
        """State-based replay guard: a second click after approve/deny is a no-op."""
        assert sa.pending_is_actionable({"status": "approved", "created_at": 1000.0}, now=1000.0) is False
        assert sa.pending_is_actionable({"status": "denied", "created_at": 1000.0}, now=1000.0) is False

    def test_expired_pending_is_noop(self):
        old = 1000.0
        assert sa.pending_is_actionable(
            {"status": "pending", "created_at": old},
            now=old + sa.PENDING_TTL_SECONDS + 1,
        ) is False

    def test_missing_pending_is_noop(self):
        assert sa.pending_is_actionable(None) is False


class TestActionValue:
    def test_roundtrip(self):
        blocks = sa.build_approval_blocks(
            sender_id="ALICE", sender_display_name="Alice",
            pending_id="pend123", channel_label="C1",
        )
        # Find a button value and parse it back.
        btn = blocks[1]["elements"][0]
        assert btn["action_id"] == sa.ACTION_ALLOW
        pid, sid = sa.parse_action_value(btn["value"])
        assert (pid, sid) == ("pend123", "ALICE")

    def test_malformed_value_failcloses(self):
        assert sa.parse_action_value("") == ("", "")
        assert sa.parse_action_value("no-colon") == ("", "")

    def test_unknown_action_not_in_known_set(self):
        assert "swarm_bogus" not in sa._KNOWN_ACTIONS
        assert sa.ACTION_ALLOW in sa._KNOWN_ACTIONS and sa.ACTION_DENY in sa._KNOWN_ACTIONS


# ── _maybe_prompt_owner_approval: one-shot owner DM + dedup ──────────────────

def _inbound(sender="ALICE", chat="C1"):
    from channels.base import InboundMessage
    return InboundMessage(
        channel_id="ch1", external_chat_id=chat, external_sender_id=sender,
        external_thread_id="T1", external_message_id="M1", text="hello",
        sender_display_name="Alice", attachments=[],
        metadata={"chat_type": "channel", "is_mention": False},
    )


class TestOwnerPrompt:
    @pytest.mark.asyncio
    async def test_prompts_owner_once_and_dedups(self, gateway, monkeypatch):
        _, state = _mock_db(monkeypatch, {"id": "ch1", "allowed_senders": '["OWNER"]'})
        sent = []

        class _Adapter:
            async def send_blocks_to_user(self, user_id, blocks, fallback):
                sent.append((user_id, blocks))
        gateway._adapters["ch1"] = _Adapter()

        ch = await __import__("channels.gateway", fromlist=["db"]).db.channels.get("ch1")
        first = await gateway._maybe_prompt_owner_approval(ch, "ch1", _inbound())
        assert first is True and len(sent) == 1
        assert sent[0][0] == "OWNER", "card DM'd to the owner"

        # Second message from same sender → dedup, no new DM.
        ch2 = await __import__("channels.gateway", fromlist=["db"]).db.channels.get("ch1")
        second = await gateway._maybe_prompt_owner_approval(ch2, "ch1", _inbound())
        assert second is False and len(sent) == 1, "one prompt per (channel,sender)"

    @pytest.mark.asyncio
    async def test_no_adapter_capability_skips_quietly(self, gateway, monkeypatch):
        _mock_db(monkeypatch, {"id": "ch1", "allowed_senders": '["OWNER"]'})
        gateway._adapters["ch1"] = object()  # no send_blocks_to_user
        ch = {"id": "ch1", "allowed_senders": '["OWNER"]'}
        assert await gateway._maybe_prompt_owner_approval(ch, "ch1", _inbound()) is False

    @pytest.mark.asyncio
    async def test_no_owner_no_prompt(self, gateway, monkeypatch):
        _mock_db(monkeypatch, {"id": "ch1", "allowed_senders": "[]"})
        gateway._adapters["ch1"] = MagicMock()
        ch = {"id": "ch1", "allowed_senders": "[]"}
        assert await gateway._maybe_prompt_owner_approval(ch, "ch1", _inbound()) is False

    @pytest.mark.asyncio
    async def test_pending_cap_refuses_flood(self, gateway, monkeypatch):
        """Gate-2 RANK-3: a flood of distinct senders can't grow pending_approvals
        past the cap. A full set of LIVE pendings → a new sender is refused."""
        import json as _json, time as _t
        from channels.gateway import _MAX_PENDING_APPROVALS
        now = _t.time()
        flood = {
            f"U{i}": {"pending_id": f"p{i}", "status": "pending", "created_at": now}
            for i in range(_MAX_PENDING_APPROVALS)
        }
        _mock_db(monkeypatch, {
            "id": "ch1", "allowed_senders": '["OWNER"]',
            "pending_approvals": _json.dumps(flood),
        })

        class _Adapter:
            async def send_blocks_to_user(self, *a): return "ts"
        gateway._adapters["ch1"] = _Adapter()
        ch = {"id": "ch1", "allowed_senders": '["OWNER"]',
              "pending_approvals": _json.dumps(flood)}
        # A brand-new sender at cap → refused (no unbounded growth).
        assert await gateway._maybe_prompt_owner_approval(ch, "ch1", _inbound("NEWBIE")) is False


# ── resolve_approval: owner-only + replay guard + allow/deny ─────────────────

def _row_with_pending(pending_id="pend1", sender="ALICE", status="pending"):
    import json as _json, time as _t
    return {
        "id": "ch1", "allowed_senders": '["OWNER"]',
        "pending_approvals": _json.dumps({
            sender: {"pending_id": pending_id, "status": status, "created_at": _t.time()}
        }),
    }


class TestResolveApproval:
    @pytest.mark.asyncio
    async def test_owner_allow_adds_trusted(self, gateway, monkeypatch):
        _mock_db(monkeypatch, _row_with_pending())
        await gateway.resolve_approval("ch1", sa.ACTION_ALLOW, "pend1:ALICE", "OWNER")
        allowed = _parse_json_list(gateway._channel_cache["ch1"]["allowed_senders"])
        assert allowed == ["OWNER", "ALICE"], "owner Allow → sender appended TRUSTED"

    @pytest.mark.asyncio
    async def test_nonowner_click_never_mutates(self, gateway, monkeypatch):
        _mock_db(monkeypatch, _row_with_pending())
        # A trusted-but-not-owner (or any) clicker must NOT be able to self/other-approve.
        await gateway.resolve_approval("ch1", sa.ACTION_ALLOW, "pend1:ALICE", "RANDO")
        row = await __import__("channels.gateway", fromlist=["db"]).db.channels.get("ch1")
        assert _parse_json_list(row["allowed_senders"]) == ["OWNER"], "non-owner click is inert"

    @pytest.mark.asyncio
    async def test_deny_does_not_add(self, gateway, monkeypatch):
        _mock_db(monkeypatch, _row_with_pending())
        await gateway.resolve_approval("ch1", sa.ACTION_DENY, "pend1:ALICE", "OWNER")
        assert _parse_json_list(gateway._channel_cache["ch1"]["allowed_senders"]) == ["OWNER"]

    @pytest.mark.asyncio
    async def test_replay_second_allow_is_noop(self, gateway, monkeypatch):
        _mock_db(monkeypatch, _row_with_pending())
        await gateway.resolve_approval("ch1", sa.ACTION_ALLOW, "pend1:ALICE", "OWNER")
        # Second click on the now-resolved (approved) pending → no-op (idempotent add anyway).
        await gateway.resolve_approval("ch1", sa.ACTION_ALLOW, "pend1:ALICE", "OWNER")
        assert _parse_json_list(gateway._channel_cache["ch1"]["allowed_senders"]) == ["OWNER", "ALICE"]

    @pytest.mark.asyncio
    async def test_stale_pending_id_rejected(self, gateway, monkeypatch):
        _mock_db(monkeypatch, _row_with_pending(pending_id="NEW"))
        # Button carries an OLD pending_id that no longer matches → no-op.
        await gateway.resolve_approval("ch1", sa.ACTION_ALLOW, "OLD:ALICE", "OWNER")
        row = await __import__("channels.gateway", fromlist=["db"]).db.channels.get("ch1")
        assert _parse_json_list(row["allowed_senders"]) == ["OWNER"]

    @pytest.mark.asyncio
    async def test_unknown_action_ignored(self, gateway, monkeypatch):
        _mock_db(monkeypatch, _row_with_pending())
        await gateway.resolve_approval("ch1", "swarm_bogus", "pend1:ALICE", "OWNER")
        assert _parse_json_list(gateway._channel_cache.get("ch1", {"allowed_senders": '["OWNER"]'})["allowed_senders"]) == ["OWNER"]


# ── R27: handlers registered in BOTH socket-start sites (AC7) ────────────────

class TestDualSiteRegistration:
    def test_register_handlers_covers_all_events_and_actions(self):
        """Both socket-start sites call _register_handlers — so asserting THAT one
        method registers message+app_mention+member_joined+both actions proves the
        reconnect path can't silently drop them (R27)."""
        from channels.adapters.slack import (
            SlackChannelAdapter, _APPROVAL_ACTION_ALLOW, _APPROVAL_ACTION_DENY,
        )
        events, actions = [], []

        class _FakeBolt:
            def event(self, name):
                events.append(name)
                return lambda fn: fn
            def action(self, name):
                actions.append(name)
                return lambda fn: fn

        adapter = SlackChannelAdapter.__new__(SlackChannelAdapter)
        adapter._register_handlers(_FakeBolt())
        assert set(events) >= {"message", "app_mention", "member_joined_channel"}
        assert set(actions) == {_APPROVAL_ACTION_ALLOW, _APPROVAL_ACTION_DENY}
