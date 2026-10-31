"""Which MCP servers a channel sender may reach: an allowlist
(core/prompt_builder.mcps_for_tier, core/mcp_config_loader.trusted_shared_names).

An MCP server runs outside the session's limits, with the owner's own sign-ins, so a
non-owner who reaches it acts as the owner. Questions answered:
1. does a public user get only the channel's own tools, a trusted user also exactly the
   MCPs the owner shared, any other tier nothing more, and the owner everything?
2. is "shared" read from the MCP entry's own opt-in (share_with_trusted), with an entry
   that does not say so, or a disabled one, not shared?
3. in build_options: are the loaded MCPs and the "enable me" list of a trusted session
   filtered to what was shared?
"""
from __future__ import annotations

import json

import pytest

from core.mcp_config_loader import SHARE_FIELD, trusted_shared_names
from core.prompt_builder import CHANNEL_TOOLS_MCP, mcps_for_tier

ALL = {CHANNEL_TOOLS_MCP, "builder-mcp", "slack-mcp", "mail-mcp", "some-new-mcp"}


@pytest.mark.parametrize("tier,want", [
    ("public", {CHANNEL_TOOLS_MCP}),
    ("trusted", {CHANNEL_TOOLS_MCP, "slack-mcp"}),
    ("guest", {CHANNEL_TOOLS_MCP}),          # an unknown tier: nothing more
    ("owner", ALL),
])
def test_mcps_for_tier(tier, want):
    assert mcps_for_tier(ALL, tier, {"slack-mcp"}) == want


def _workspace(tmp_path, dev, catalog=()):
    mcps = tmp_path / ".claude" / "mcps"
    mcps.mkdir(parents=True)
    (mcps / "mcp-dev.json").write_text(json.dumps(list(dev)))
    (mcps / "mcp-catalog.json").write_text(json.dumps(list(catalog)))
    return tmp_path


def _entry(name, **kw):
    return {"id": name, "name": name, "connection_type": "stdio",
            "config": {"command": "true"}, "tier": "always", **kw}


def test_shared_names_come_from_the_entrys_own_opt_in(tmp_path):
    ws = _workspace(tmp_path, [
        _entry("slack-mcp", **{SHARE_FIELD: True}),
        _entry("builder-mcp"),
        _entry("mail-mcp", **{SHARE_FIELD: "yes"}),          # only literal true counts
        _entry("off-mcp", enabled=False, **{SHARE_FIELD: True}),
    ])
    assert trusted_shared_names(ws) == frozenset({"slack-mcp"})


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["trusted", "public"])
async def test_build_options_keeps_only_what_the_tier_may_reach(tier, tmp_path, monkeypatch):
    from tests.test_knowledge_restricted_channel import _options
    _workspace(tmp_path, [_entry("slack-mcp", **{SHARE_FIELD: True}), _entry("builder-mcp"),
                          _entry("deferred-shared", tier="ondemand", **{SHARE_FIELD: True}),
                          _entry("deferred-mine", tier="ondemand")])
    captured = {}

    ctx = {"channel_id": "c1", "channel_type": "slack",
           "sender_identity": {"permission_tier": tier, "external_id": "u1"}}
    opts = await _options(ctx, tmp_path, monkeypatch, enable_mcp=True, agent_config=captured)
    names = set((opts.mcp_servers or {}).keys())
    deferred = {d["name"] for d in captured.get("_deferred_mcps") or []}
    if tier == "trusted":
        assert "slack-mcp" in names and "builder-mcp" not in names
        assert deferred == {"deferred-shared"}
    else:
        assert not names & {"slack-mcp", "builder-mcp"}
        assert deferred == set()
