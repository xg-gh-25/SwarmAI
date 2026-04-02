"""Tests for Lazy MCP Loading — tier-based spawn filtering.

Acceptance criteria:
  AC1: mcp-dev.json supports tier field with backward-compatible default
  AC2: build_mcp_config() filters by tier at spawn time
  AC3: Channel sessions additionally load channel tier MCPs
  AC4: on-demand MCPs listed in system prompt as deferred
  AC5: Session can respawn with additional MCPs via reclaim_for_mcp_swap()
  AC6: All existing tests pass (0 regressions) — covered by full suite run
  AC7: Force-load matches both name AND id fields (no silent failures)
  AC8: _extra_mcps survives kill/respawn cycle
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mcp_workspace(tmp_path):
    """Create a workspace with mcp-dev.json containing tier annotations."""
    mcps_dir = tmp_path / ".claude" / "mcps"
    mcps_dir.mkdir(parents=True)

    entries = [
        {
            "id": "user-builder-mcp",
            "name": "builder-mcp",
            "connection_type": "stdio",
            "config": {"command": "/usr/bin/builder-mcp", "args": []},
            "enabled": True,
            "tier": "always",
        },
        {
            "id": "user-slack-mcp",
            "name": "slack-mcp",
            "connection_type": "stdio",
            "config": {"command": "/usr/bin/slack-mcp", "args": []},
            "enabled": True,
            "tier": "channel",
        },
        {
            "id": "user-outlook-mcp",
            "name": "aws-outlook-mcp",
            "connection_type": "stdio",
            "config": {"command": "/usr/bin/outlook-mcp", "args": []},
            "enabled": True,
            "tier": "ondemand",
        },
        {
            "id": "user-sentral-mcp",
            "name": "aws-sentral-mcp",
            "connection_type": "stdio",
            "config": {"command": "/usr/bin/sentral-mcp", "args": []},
            "enabled": True,
            "tier": "ondemand",
        },
    ]

    (mcps_dir / "mcp-dev.json").write_text(json.dumps(entries))
    (mcps_dir / "mcp-catalog.json").write_text("[]")
    return tmp_path


@pytest.fixture
def mcp_workspace_no_tiers(tmp_path):
    """Workspace with old-style mcp-dev.json (no tier field)."""
    mcps_dir = tmp_path / ".claude" / "mcps"
    mcps_dir.mkdir(parents=True)

    entries = [
        {
            "id": "user-builder-mcp",
            "name": "builder-mcp",
            "connection_type": "stdio",
            "config": {"command": "/usr/bin/builder-mcp", "args": []},
            "enabled": True,
        },
        {
            "id": "user-outlook-mcp",
            "name": "aws-outlook-mcp",
            "connection_type": "stdio",
            "config": {"command": "/usr/bin/outlook-mcp", "args": []},
            "enabled": True,
        },
    ]

    (mcps_dir / "mcp-dev.json").write_text(json.dumps(entries))
    (mcps_dir / "mcp-catalog.json").write_text("[]")
    return tmp_path


@pytest.fixture
def mcp_workspace_divergent_names(tmp_path):
    """Workspace where entry name ≠ id — tests the dual-match logic.

    Real-world scenario: mcp-dev.json has `id: "user-custom-tool"` but
    `name: "custom-tool"`.  Agent/frontend might use either string when
    requesting force-load via enable_mcp_for_session().
    """
    mcps_dir = tmp_path / ".claude" / "mcps"
    mcps_dir.mkdir(parents=True)

    entries = [
        {
            "id": "user-custom-tool",
            "name": "custom-tool",
            "connection_type": "stdio",
            "config": {"command": "/usr/bin/custom", "args": []},
            "enabled": True,
            "tier": "ondemand",
        },
        {
            "id": "user-internal-tool",
            "name": "internal-tool",
            "connection_type": "stdio",
            "config": {"command": "/usr/bin/internal", "args": []},
            "enabled": True,
            "tier": "ondemand",
        },
    ]

    (mcps_dir / "mcp-dev.json").write_text(json.dumps(entries))
    (mcps_dir / "mcp-catalog.json").write_text("[]")
    return tmp_path


# ---------------------------------------------------------------------------
# AC1: tier field with backward-compatible default
# ---------------------------------------------------------------------------

class TestTierBackwardCompat:
    """Entries without a tier field default to 'always' — all load."""

    def test_missing_tier_defaults_to_always(self, mcp_workspace_no_tiers):
        from core.mcp_config_loader import load_mcp_config

        servers, disallowed = load_mcp_config(
            mcp_workspace_no_tiers, enable_mcp=True,
        )
        # Both MCPs should load (no tier = always)
        assert len(servers) == 2
        assert "builder-mcp" in servers
        assert "aws-outlook-mcp" in servers

    def test_invalid_tier_treated_as_always(self, tmp_path):
        mcps_dir = tmp_path / ".claude" / "mcps"
        mcps_dir.mkdir(parents=True)

        entries = [{
            "id": "test-mcp",
            "name": "test-mcp",
            "connection_type": "stdio",
            "config": {"command": "/usr/bin/test", "args": []},
            "enabled": True,
            "tier": "INVALID_VALUE",
        }]
        (mcps_dir / "mcp-dev.json").write_text(json.dumps(entries))
        (mcps_dir / "mcp-catalog.json").write_text("[]")

        from core.mcp_config_loader import load_mcp_config

        servers, _ = load_mcp_config(tmp_path, enable_mcp=True)
        # Invalid tier should be treated as always — MCP loads
        assert "test-mcp" in servers


# ---------------------------------------------------------------------------
# AC2: build_mcp_config() filters by tier at spawn time
# ---------------------------------------------------------------------------

class TestTierFiltering:
    """Only 'always' tier MCPs load by default."""

    def test_tier_filtering_loads_only_always(self, mcp_workspace):
        from core.mcp_config_loader import load_mcp_config_tiered

        servers, disallowed, deferred = load_mcp_config_tiered(
            mcp_workspace, enable_mcp=True,
        )
        # Only builder-mcp (tier=always) should load
        assert "builder-mcp" in servers
        assert "slack-mcp" not in servers
        assert "aws-outlook-mcp" not in servers
        assert "aws-sentral-mcp" not in servers

    def test_deferred_contains_non_always_mcps(self, mcp_workspace):
        from core.mcp_config_loader import load_mcp_config_tiered

        _, _, deferred = load_mcp_config_tiered(
            mcp_workspace, enable_mcp=True,
        )
        # Deferred should list the names + descriptions of non-loaded MCPs
        deferred_names = [d["name"] for d in deferred]
        assert "slack-mcp" in deferred_names
        assert "aws-outlook-mcp" in deferred_names
        assert "aws-sentral-mcp" in deferred_names
        assert "builder-mcp" not in deferred_names

    def test_deferred_includes_tier_and_description(self, mcp_workspace):
        from core.mcp_config_loader import load_mcp_config_tiered

        _, _, deferred = load_mcp_config_tiered(
            mcp_workspace, enable_mcp=True,
        )
        for item in deferred:
            assert "name" in item
            assert "tier" in item

    def test_disabled_mcp_not_in_deferred(self, tmp_path):
        """Disabled MCPs should not appear in deferred list."""
        mcps_dir = tmp_path / ".claude" / "mcps"
        mcps_dir.mkdir(parents=True)

        entries = [{
            "id": "disabled-mcp",
            "name": "disabled-mcp",
            "connection_type": "stdio",
            "config": {"command": "/usr/bin/x", "args": []},
            "enabled": False,
            "tier": "ondemand",
        }]
        (mcps_dir / "mcp-dev.json").write_text(json.dumps(entries))
        (mcps_dir / "mcp-catalog.json").write_text("[]")

        from core.mcp_config_loader import load_mcp_config_tiered

        servers, _, deferred = load_mcp_config_tiered(
            tmp_path, enable_mcp=True,
        )
        assert len(servers) == 0
        assert len(deferred) == 0  # disabled = not deferred, just off


# ---------------------------------------------------------------------------
# AC2b: extra_always forces ondemand MCPs to load
# ---------------------------------------------------------------------------

class TestExtraAlways:
    """Per-session overrides force deferred MCPs to load."""

    def test_extra_always_forces_ondemand_to_load(self, mcp_workspace):
        from core.mcp_config_loader import load_mcp_config_tiered

        servers, _, deferred = load_mcp_config_tiered(
            mcp_workspace, enable_mcp=True,
            extra_always={"aws-outlook-mcp"},
        )
        # builder-mcp (always) + aws-outlook-mcp (forced) should load
        assert "builder-mcp" in servers
        assert "aws-outlook-mcp" in servers
        # aws-sentral-mcp still deferred
        deferred_names = [d["name"] for d in deferred]
        assert "aws-sentral-mcp" in deferred_names
        assert "aws-outlook-mcp" not in deferred_names

    def test_extra_always_forces_channel_to_load_without_channel_context(self, mcp_workspace):
        from core.mcp_config_loader import load_mcp_config_tiered

        servers, _, deferred = load_mcp_config_tiered(
            mcp_workspace, enable_mcp=True,
            extra_always={"slack-mcp"},
        )
        assert "slack-mcp" in servers
        deferred_names = [d["name"] for d in deferred]
        assert "slack-mcp" not in deferred_names

    def test_extra_always_empty_set_no_effect(self, mcp_workspace):
        from core.mcp_config_loader import load_mcp_config_tiered

        servers, _, deferred = load_mcp_config_tiered(
            mcp_workspace, enable_mcp=True,
            extra_always=set(),
        )
        assert len(servers) == 1  # Only builder-mcp
        assert len(deferred) == 3  # slack + outlook + sentral

    def test_extra_always_nonexistent_mcp_ignored(self, mcp_workspace):
        from core.mcp_config_loader import load_mcp_config_tiered

        servers, _, deferred = load_mcp_config_tiered(
            mcp_workspace, enable_mcp=True,
            extra_always={"nonexistent-mcp"},
        )
        # Nothing extra loaded — nonexistent name doesn't match any entry
        assert len(servers) == 1
        assert len(deferred) == 3


# ---------------------------------------------------------------------------
# AC3: Channel sessions load channel tier MCPs
# ---------------------------------------------------------------------------

class TestChannelTier:
    """Channel sessions should load both 'always' AND 'channel' tier MCPs."""

    def test_channel_session_includes_channel_tier(self, mcp_workspace):
        from core.mcp_config_loader import load_mcp_config_tiered

        servers, _, deferred = load_mcp_config_tiered(
            mcp_workspace,
            enable_mcp=True,
            channel_context={"channel_type": "slack"},
        )
        # builder-mcp (always) + slack-mcp (channel) should load
        assert "builder-mcp" in servers
        assert "slack-mcp" in servers
        # ondemand still deferred
        assert "aws-outlook-mcp" not in servers
        deferred_names = [d["name"] for d in deferred]
        assert "aws-outlook-mcp" in deferred_names
        assert "slack-mcp" not in deferred_names

    def test_non_channel_session_excludes_channel_tier(self, mcp_workspace):
        from core.mcp_config_loader import load_mcp_config_tiered

        servers, _, deferred = load_mcp_config_tiered(
            mcp_workspace, enable_mcp=True,
        )
        assert "slack-mcp" not in servers
        deferred_names = [d["name"] for d in deferred]
        assert "slack-mcp" in deferred_names


# ---------------------------------------------------------------------------
# AC4: Deferred MCPs injected into system prompt
# ---------------------------------------------------------------------------

class TestDeferredPromptInjection:
    """System prompt should list deferred MCPs so agent knows they exist."""

    def test_prompt_builder_returns_deferred(self, mcp_workspace):
        """build_mcp_config should return deferred info for prompt injection."""
        from core.prompt_builder import PromptBuilder

        config = MagicMock()
        config.get = MagicMock(return_value=None)
        builder = PromptBuilder(config)

        servers, disallowed, deferred = builder.build_mcp_config(
            str(mcp_workspace), enable_mcp=True,
        )

        # build_mcp_config should now return 3 values (not 2)
        assert isinstance(deferred, list)
        assert len(deferred) > 0
        # builder-mcp is always, others are deferred
        deferred_names = [d["name"] for d in deferred]
        assert "slack-mcp" in deferred_names

    def test_deferred_mcp_text_formatting(self):
        """The deferred MCP names should appear in formatted text."""
        from core.prompt_builder import PromptBuilder

        deferred = [
            {"name": "aws-outlook-mcp", "tier": "ondemand",
             "description": "Outlook email and calendar"},
            {"name": "aws-sentral-mcp", "tier": "ondemand",
             "description": "AWS account lookup"},
        ]

        text = PromptBuilder.format_deferred_mcp_section(deferred)
        assert "aws-outlook-mcp" in text
        assert "aws-sentral-mcp" in text
        assert "Outlook email" in text

    def test_empty_deferred_returns_empty_string(self):
        """No deferred MCPs → empty section."""
        from core.prompt_builder import PromptBuilder

        text = PromptBuilder.format_deferred_mcp_section([])
        assert text == ""


# ---------------------------------------------------------------------------
# AC5: Session respawn with additional MCPs
# ---------------------------------------------------------------------------

class TestMcpRespawn:
    """Session can add MCPs and respawn via reclaim_for_mcp_swap."""

    @pytest.mark.asyncio
    async def test_reclaim_for_mcp_swap_requires_idle(self):
        """reclaim_for_mcp_swap raises if not IDLE."""
        from core.session_unit import SessionUnit, SessionState

        unit = SessionUnit.__new__(SessionUnit)
        unit.state = SessionState.STREAMING
        unit.session_id = "test-session"
        unit._extra_mcps = set()

        with pytest.raises(RuntimeError, match="Cannot reclaim"):
            await unit.reclaim_for_mcp_swap()

    @pytest.mark.asyncio
    async def test_reclaim_stores_mcp_name(self):
        """reclaim_for_mcp_swap stores mcp_name in _extra_mcps."""
        from core.session_unit import SessionUnit, SessionState

        unit = SessionUnit.__new__(SessionUnit)
        unit.state = SessionState.IDLE
        unit.session_id = "test-session"
        unit._extra_mcps = set()

        async def mock_kill():
            unit.state = SessionState.COLD

        unit.kill = mock_kill

        await unit.reclaim_for_mcp_swap(mcp_name="aws-outlook-mcp")
        assert "aws-outlook-mcp" in unit._extra_mcps
        assert unit.state == SessionState.COLD

    @pytest.mark.asyncio
    async def test_reclaim_for_waiting_input_raises(self):
        """reclaim_for_mcp_swap raises on WAITING_INPUT — protected state."""
        from core.session_unit import SessionUnit, SessionState

        unit = SessionUnit.__new__(SessionUnit)
        unit.state = SessionState.WAITING_INPUT
        unit.session_id = "test-session"
        unit._extra_mcps = set()

        with pytest.raises(RuntimeError, match="Cannot reclaim"):
            await unit.reclaim_for_mcp_swap(mcp_name="some-mcp")


# ---------------------------------------------------------------------------
# AC7: Force-load matches both name AND id (no silent failures)
# ---------------------------------------------------------------------------

class TestNameIdDualMatch:
    """Force-load should match against both name and id fields."""

    def test_force_by_name_when_name_differs_from_id(self, mcp_workspace_divergent_names):
        """Using display name to force-load works."""
        from core.mcp_config_loader import load_mcp_config_tiered

        servers, _, deferred = load_mcp_config_tiered(
            mcp_workspace_divergent_names, enable_mcp=True,
            extra_always={"custom-tool"},
        )
        assert "custom-tool" in servers

    def test_force_by_id_when_name_differs_from_id(self, mcp_workspace_divergent_names):
        """Using the id field to force-load also works."""
        from core.mcp_config_loader import load_mcp_config_tiered

        servers, _, deferred = load_mcp_config_tiered(
            mcp_workspace_divergent_names, enable_mcp=True,
            extra_always={"user-custom-tool"},
        )
        # Should match via id even though name is "custom-tool"
        assert "custom-tool" in servers

    def test_force_by_id_second_entry(self, mcp_workspace_divergent_names):
        """Force-load using the id field of a different entry."""
        from core.mcp_config_loader import load_mcp_config_tiered

        servers, _, deferred = load_mcp_config_tiered(
            mcp_workspace_divergent_names, enable_mcp=True,
            extra_always={"user-internal-tool"},
        )
        # Matched via id "user-internal-tool", loaded as name "internal-tool"
        assert "internal-tool" in servers

    def test_force_neither_name_nor_id_no_match(self, mcp_workspace_divergent_names):
        """Completely wrong name doesn't load anything extra."""
        from core.mcp_config_loader import load_mcp_config_tiered

        servers, _, deferred = load_mcp_config_tiered(
            mcp_workspace_divergent_names, enable_mcp=True,
            extra_always={"wrong-name"},
        )
        assert len(servers) == 0
        assert len(deferred) == 2  # Both entries still deferred


# ---------------------------------------------------------------------------
# AC8: _extra_mcps survives kill/respawn (cleanup doesn't reset it)
# ---------------------------------------------------------------------------

class TestExtraMcpsSurvival:
    """_extra_mcps persists across kill/respawn — not cleared by cleanup."""

    def test_cleanup_internal_preserves_extra_mcps(self):
        """_cleanup_internal() must NOT reset _extra_mcps."""
        from core.session_unit import SessionUnit, SessionState

        unit = SessionUnit.__new__(SessionUnit)
        unit.session_id = "test-session"
        unit._extra_mcps = {"aws-outlook-mcp", "slack-mcp"}
        unit._client = MagicMock()
        unit._wrapper = MagicMock()
        unit._interrupted = False
        unit._retry_count = 0
        unit._model_name = "claude-4.6-opus"
        unit._peak_tree_rss_bytes = 1024

        unit._cleanup_internal()

        # _extra_mcps must survive — this is the whole point of lazy MCP
        assert unit._extra_mcps == {"aws-outlook-mcp", "slack-mcp"}
        # Other fields should be cleaned
        assert unit._client is None
        assert unit._wrapper is None

    def test_full_cleanup_preserves_extra_mcps(self):
        """_full_cleanup() preserves _extra_mcps even for non-resumable crashes.

        Rationale: user explicitly enabled these MCPs. Even after a crash,
        their next send() should still spawn with the same MCP set.
        Only a new session (new tab) should start with defaults.
        """
        from core.session_unit import SessionUnit

        unit = SessionUnit.__new__(SessionUnit)
        unit.session_id = "test-session"
        unit._extra_mcps = {"aws-outlook-mcp"}
        unit._client = None
        unit._wrapper = None
        unit._interrupted = False
        unit._retry_count = 0
        unit._model_name = None
        unit._peak_tree_rss_bytes = 0
        unit._sdk_session_id = "old-session-id"
        unit._lifecycle_response_count = 0
        unit._consecutive_oom_kills = 0

        unit._full_cleanup()

        # _extra_mcps survives — user intent persists across crashes
        assert unit._extra_mcps == {"aws-outlook-mcp"}
        # But session identity is cleared (non-resumable)
        assert unit._sdk_session_id is None


class TestEnableMcpForSession:
    """SessionRouter.enable_mcp_for_session delegates to reclaim_for_mcp_swap."""

    @pytest.mark.asyncio
    async def test_enable_mcp_unknown_session(self):
        """Returns failure for unknown session_id."""
        from core.session_router import SessionRouter

        router = SessionRouter.__new__(SessionRouter)
        router._units = {}

        result = await router.enable_mcp_for_session("nonexistent", "outlook")
        assert result["success"] is False
        assert "not found" in result["message"]

    @pytest.mark.asyncio
    async def test_enable_mcp_not_idle(self):
        """Returns failure when session is not IDLE."""
        from core.session_router import SessionRouter
        from core.session_unit import SessionUnit, SessionState

        router = SessionRouter.__new__(SessionRouter)
        unit = SessionUnit.__new__(SessionUnit)
        unit.session_id = "test-sess"
        unit.state = SessionState.STREAMING
        unit._extra_mcps = set()
        router._units = {"test-sess": unit}

        result = await router.enable_mcp_for_session("test-sess", "outlook")
        assert result["success"] is False
        assert "Cannot reclaim" in result["message"]

    @pytest.mark.asyncio
    async def test_enable_mcp_success(self):
        """Returns success when session is IDLE and reclaim works."""
        from core.session_router import SessionRouter
        from core.session_unit import SessionUnit, SessionState

        router = SessionRouter.__new__(SessionRouter)
        unit = SessionUnit.__new__(SessionUnit)
        unit.session_id = "test-sess"
        unit.state = SessionState.IDLE
        unit._extra_mcps = set()

        # Mock kill() to just transition state
        async def mock_kill():
            unit.state = SessionState.COLD

        unit.kill = mock_kill
        router._units = {"test-sess": unit}

        result = await router.enable_mcp_for_session("test-sess", "aws-outlook-mcp")
        assert result["success"] is True
        assert "aws-outlook-mcp" in result["message"]
        # Verify the MCP name is stored for next spawn
        assert "aws-outlook-mcp" in unit._extra_mcps


# ---------------------------------------------------------------------------
# Integration: prompt_builder wiring
# ---------------------------------------------------------------------------

class TestPromptBuilderWiring:
    """build_mcp_config call site passes tier context correctly."""

    def test_build_mcp_config_returns_three_values(self, mcp_workspace):
        """The method signature now returns (servers, disallowed, deferred)."""
        from core.prompt_builder import PromptBuilder

        config = MagicMock()
        config.get = MagicMock(return_value=None)
        builder = PromptBuilder(config)

        result = builder.build_mcp_config(str(mcp_workspace), enable_mcp=True)

        assert len(result) == 3
        servers, disallowed, deferred = result
        assert isinstance(servers, dict)
        assert isinstance(disallowed, list)
        assert isinstance(deferred, list)
