"""Unit tests for TSCCStateManager.

Tests the in-memory per-thread TSCC state manager, covering:

- ``get_or_create_state`` — default state creation with correct scope labels
- ``apply_event`` — all five telemetry event types and their state mutations
- ``set_lifecycle_state`` — valid and invalid lifecycle transitions
- ``clear_state`` — thread state removal
- LRU eviction when max_entries is reached
- Deduplication logic for agents, capabilities, and sources
- FIFO enforcement for what_ai_doing (max 4) and key_summary (max 5)
- Concurrent access via asyncio.Lock
- Scope label correctness (no "None" or empty labels)
"""

import asyncio
import pytest

from core.tscc_state_manager import TSCCStateManager, VALID_TRANSITIONS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def manager():
    """Fresh TSCCStateManager with default capacity."""
    return TSCCStateManager(max_entries=200)


@pytest.fixture
def small_manager():
    """TSCCStateManager with small capacity for LRU eviction tests."""
    return TSCCStateManager(max_entries=3)


# ---------------------------------------------------------------------------
# get_or_create_state
# ---------------------------------------------------------------------------

class TestGetOrCreateState:
    """Tests for get_or_create_state."""

    @pytest.mark.asyncio
    async def test_creates_default_workspace_state(self, manager):
        state = await manager.get_or_create_state("t1")
        assert state.thread_id == "t1"
        assert state.scope_type == "workspace"
        assert state.lifecycle_state == "new"
        assert state.live_state.context.scope_label == "Workspace: SwarmWS (General)"
        assert state.live_state.active_agents == []
        assert state.live_state.what_ai_doing == []
        assert state.live_state.key_summary == []

    @pytest.mark.asyncio
    async def test_creates_project_scoped_state(self, manager):
        state = await manager.get_or_create_state("t2", project_id="proj-1", thread_title="My Project")
        assert state.scope_type == "project"
        assert state.live_state.context.scope_label == "Project: My Project"
        assert state.project_id == "proj-1"

    @pytest.mark.asyncio
    async def test_returns_existing_state(self, manager):
        s1 = await manager.get_or_create_state("t1")
        s2 = await manager.get_or_create_state("t1")
        assert s1 is s2

    @pytest.mark.asyncio
    async def test_scope_label_workspace_when_project_id_none(self, manager):
        state = await manager.get_or_create_state("t1", project_id=None)
        assert state.live_state.context.scope_label == "Workspace: SwarmWS (General)"

    @pytest.mark.asyncio
    async def test_scope_label_never_contains_none_string(self, manager):
        state = await manager.get_or_create_state("t1", project_id=None)
        label = state.live_state.context.scope_label
        assert "None" not in label
        assert "No project" not in label
        assert "not selected" not in label
        assert label != ""


# ---------------------------------------------------------------------------
# apply_event — agent_activity
# ---------------------------------------------------------------------------

class TestApplyEventAgentActivity:
    """Tests for agent_activity event handling."""

    @pytest.mark.asyncio
    async def test_unknown_thread_is_noop(self, manager):
        """apply_event silently ignores events for unknown threads."""
        await manager.apply_event("nonexistent", {
            "type": "agent_activity",
            "data": {"agent_name": "A", "description": "d"},
        })
        # No state created, no error raised
        assert await manager.get_state("nonexistent") is None

    @pytest.mark.asyncio
    async def test_adds_agent_to_active_agents(self, manager):
        await manager.get_or_create_state("t1")
        await manager.apply_event("t1", {
            "type": "agent_activity",
            "data": {"agent_name": "ResearchAgent", "description": "Analyzing docs"},
        })
        state = await manager.get_state("t1")
        assert "ResearchAgent" in state.live_state.active_agents

    @pytest.mark.asyncio
    async def test_deduplicates_agents(self, manager):
        await manager.get_or_create_state("t1")
        event = {"type": "agent_activity", "data": {"agent_name": "A1", "description": "step"}}
        await manager.apply_event("t1", event)
        await manager.apply_event("t1", event)
        state = await manager.get_state("t1")
        assert state.live_state.active_agents.count("A1") == 1

    @pytest.mark.asyncio
    async def test_updates_what_ai_doing(self, manager):
        await manager.get_or_create_state("t1")
        await manager.apply_event("t1", {
            "type": "agent_activity",
            "data": {"agent_name": "A1", "description": "Doing stuff"},
        })
        state = await manager.get_state("t1")
        assert "Doing stuff" in state.live_state.what_ai_doing

    @pytest.mark.asyncio
    async def test_what_ai_doing_max_4_fifo(self, manager):
        await manager.get_or_create_state("t1")
        for i in range(6):
            await manager.apply_event("t1", {
                "type": "agent_activity",
                "data": {"agent_name": f"A{i}", "description": f"step-{i}"},
            })
        state = await manager.get_state("t1")
        assert len(state.live_state.what_ai_doing) == 4
        # Should keep the last 4
        assert state.live_state.what_ai_doing == ["step-2", "step-3", "step-4", "step-5"]


# ---------------------------------------------------------------------------
# apply_event — tool_invocation
# ---------------------------------------------------------------------------

class TestApplyEventToolInvocation:
    """Tests for tool_invocation event handling."""

    @pytest.mark.asyncio
    async def test_updates_what_ai_doing(self, manager):
        await manager.get_or_create_state("t1")
        await manager.apply_event("t1", {
            "type": "tool_invocation",
            "data": {"tool_name": "search", "description": "Searching files"},
        })
        state = await manager.get_state("t1")
        assert "Searching files" in state.live_state.what_ai_doing

    @pytest.mark.asyncio
    async def test_what_ai_doing_max_4_fifo(self, manager):
        await manager.get_or_create_state("t1")
        for i in range(5):
            await manager.apply_event("t1", {
                "type": "tool_invocation",
                "data": {"tool_name": f"t{i}", "description": f"action-{i}"},
            })
        state = await manager.get_state("t1")
        assert len(state.live_state.what_ai_doing) == 4
        assert state.live_state.what_ai_doing[0] == "action-1"


# ---------------------------------------------------------------------------
# apply_event — capability_activated
# ---------------------------------------------------------------------------

class TestApplyEventCapabilityActivated:
    """Tests for capability_activated event handling."""

    @pytest.mark.asyncio
    async def test_adds_skill(self, manager):
        await manager.get_or_create_state("t1")
        await manager.apply_event("t1", {
            "type": "capability_activated",
            "data": {"cap_type": "skill", "cap_name": "web-search", "label": "Web Search"},
        })
        state = await manager.get_state("t1")
        assert "web-search" in state.live_state.active_capabilities.skills

    @pytest.mark.asyncio
    async def test_adds_mcp(self, manager):
        await manager.get_or_create_state("t1")
        await manager.apply_event("t1", {
            "type": "capability_activated",
            "data": {"cap_type": "mcp", "cap_name": "github", "label": "GitHub"},
        })
        state = await manager.get_state("t1")
        assert "github" in state.live_state.active_capabilities.mcps

    @pytest.mark.asyncio
    async def test_adds_tool(self, manager):
        await manager.get_or_create_state("t1")
        await manager.apply_event("t1", {
            "type": "capability_activated",
            "data": {"cap_type": "tool", "cap_name": "bash", "label": "Bash"},
        })
        state = await manager.get_state("t1")
        assert "bash" in state.live_state.active_capabilities.tools

    @pytest.mark.asyncio
    async def test_deduplicates_per_category(self, manager):
        await manager.get_or_create_state("t1")
        event = {"type": "capability_activated", "data": {"cap_type": "skill", "cap_name": "s1", "label": "S1"}}
        await manager.apply_event("t1", event)
        await manager.apply_event("t1", event)
        state = await manager.get_state("t1")
        assert state.live_state.active_capabilities.skills.count("s1") == 1


# ---------------------------------------------------------------------------
# apply_event — sources_updated
# ---------------------------------------------------------------------------

class TestApplyEventSourcesUpdated:
    """Tests for sources_updated event handling."""

    @pytest.mark.asyncio
    async def test_adds_source(self, manager):
        await manager.get_or_create_state("t1")
        await manager.apply_event("t1", {
            "type": "sources_updated",
            "data": {"source_path": "src/main.py", "origin": "Project"},
        })
        state = await manager.get_state("t1")
        assert len(state.live_state.active_sources) == 1
        assert state.live_state.active_sources[0].path == "src/main.py"

    @pytest.mark.asyncio
    async def test_deduplicates_by_path_origin_tuple(self, manager):
        await manager.get_or_create_state("t1")
        event = {"type": "sources_updated", "data": {"source_path": "a.py", "origin": "Project"}}
        await manager.apply_event("t1", event)
        await manager.apply_event("t1", event)
        state = await manager.get_state("t1")
        assert len(state.live_state.active_sources) == 1

    @pytest.mark.asyncio
    async def test_same_path_different_origin_keeps_both(self, manager):
        await manager.get_or_create_state("t1")
        await manager.apply_event("t1", {
            "type": "sources_updated",
            "data": {"source_path": "a.py", "origin": "Project"},
        })
        await manager.apply_event("t1", {
            "type": "sources_updated",
            "data": {"source_path": "a.py", "origin": "Knowledge Base"},
        })
        state = await manager.get_state("t1")
        assert len(state.live_state.active_sources) == 2


# ---------------------------------------------------------------------------
# apply_event — summary_updated
# ---------------------------------------------------------------------------

class TestApplyEventSummaryUpdated:
    """Tests for summary_updated event handling."""

    @pytest.mark.asyncio
    async def test_replaces_key_summary(self, manager):
        await manager.get_or_create_state("t1")
        await manager.apply_event("t1", {
            "type": "summary_updated",
            "data": {"key_summary": ["point 1", "point 2"]},
        })
        state = await manager.get_state("t1")
        assert state.live_state.key_summary == ["point 1", "point 2"]

    @pytest.mark.asyncio
    async def test_key_summary_max_5(self, manager):
        await manager.get_or_create_state("t1")
        await manager.apply_event("t1", {
            "type": "summary_updated",
            "data": {"key_summary": ["a", "b", "c", "d", "e", "f", "g"]},
        })
        state = await manager.get_state("t1")
        assert len(state.live_state.key_summary) == 5


# ---------------------------------------------------------------------------
# set_lifecycle_state
# ---------------------------------------------------------------------------

class TestSetLifecycleState:
    """Tests for lifecycle state transitions."""

    @pytest.mark.asyncio
    async def test_valid_transitions(self, manager):
        await manager.get_or_create_state("t1")
        # new -> active
        await manager.set_lifecycle_state("t1", "active")
        state = await manager.get_state("t1")
        assert state.lifecycle_state == "active"

    @pytest.mark.asyncio
    async def test_all_valid_transitions(self, manager):
        for from_state, to_states in VALID_TRANSITIONS.items():
            for to_state in to_states:
                m = TSCCStateManager()
                await m.get_or_create_state("t")
                # Force the from_state
                m._states["t"].lifecycle_state = from_state
                await m.set_lifecycle_state("t", to_state)
                s = await m.get_state("t")
                assert s.lifecycle_state == to_state

    @pytest.mark.asyncio
    async def test_invalid_transition_raises(self, manager):
        await manager.get_or_create_state("t1")
        # new -> paused is invalid
        with pytest.raises(ValueError, match="Invalid lifecycle transition"):
            await manager.set_lifecycle_state("t1", "paused")

    @pytest.mark.asyncio
    async def test_cancelled_to_active_direct(self, manager):
        await manager.get_or_create_state("t1")
        manager._states["t1"].lifecycle_state = "cancelled"
        await manager.set_lifecycle_state("t1", "active")
        state = await manager.get_state("t1")
        assert state.lifecycle_state == "active"

    @pytest.mark.asyncio
    async def test_nonexistent_thread_raises(self, manager):
        with pytest.raises(KeyError):
            await manager.set_lifecycle_state("nope", "active")


# ---------------------------------------------------------------------------
# clear_state
# ---------------------------------------------------------------------------

class TestClearState:
    """Tests for clear_state."""

    @pytest.mark.asyncio
    async def test_removes_thread_state(self, manager):
        await manager.get_or_create_state("t1")
        await manager.clear_state("t1")
        state = await manager.get_state("t1")
        assert state is None

    @pytest.mark.asyncio
    async def test_clear_nonexistent_is_noop(self, manager):
        # Should not raise
        await manager.clear_state("nonexistent")


# ---------------------------------------------------------------------------
# LRU eviction
# ---------------------------------------------------------------------------

class TestLRUEviction:
    """Tests for LRU eviction when max_entries is reached."""

    @pytest.mark.asyncio
    async def test_evicts_oldest_entry(self, small_manager):
        await small_manager.get_or_create_state("t1")
        await small_manager.get_or_create_state("t2")
        await small_manager.get_or_create_state("t3")
        # At capacity. Adding t4 should evict t1.
        await small_manager.get_or_create_state("t4")
        assert await small_manager.get_state("t1") is None
        assert await small_manager.get_state("t2") is not None

    @pytest.mark.asyncio
    async def test_eviction_cleans_up_lock(self, small_manager):
        await small_manager.get_or_create_state("t1")
        await small_manager.get_or_create_state("t2")
        await small_manager.get_or_create_state("t3")
        assert "t1" in small_manager._locks
        # Evict t1
        await small_manager.get_or_create_state("t4")
        assert "t1" not in small_manager._locks

    @pytest.mark.asyncio
    async def test_access_refreshes_lru(self, small_manager):
        await small_manager.get_or_create_state("t1")
        await small_manager.get_or_create_state("t2")
        await small_manager.get_or_create_state("t3")
        # Access t1 to refresh it
        await small_manager.get_state("t1")
        # Now add t4 — should evict t2 (oldest untouched)
        await small_manager.get_or_create_state("t4")
        assert await small_manager.get_state("t1") is not None
        assert await small_manager.get_state("t2") is None


# ---------------------------------------------------------------------------
# Concurrent access (asyncio.Lock)
# ---------------------------------------------------------------------------

class TestConcurrentAccess:
    """Tests verifying per-thread locking works correctly."""

    @pytest.mark.asyncio
    async def test_concurrent_apply_events(self, manager):
        await manager.get_or_create_state("t1")

        async def apply_agents(start: int, count: int):
            for i in range(start, start + count):
                await manager.apply_event("t1", {
                    "type": "agent_activity",
                    "data": {"agent_name": f"Agent-{i}", "description": f"step-{i}"},
                })

        # Run two coroutines concurrently
        await asyncio.gather(
            apply_agents(0, 10),
            apply_agents(10, 10),
        )
        state = await manager.get_state("t1")
        # All 20 unique agents should be present
        assert len(state.live_state.active_agents) == 20

    @pytest.mark.asyncio
    async def test_concurrent_different_threads(self, manager):
        await manager.get_or_create_state("t1")
        await manager.get_or_create_state("t2")

        async def apply_to_thread(tid: str):
            for i in range(5):
                await manager.apply_event(tid, {
                    "type": "agent_activity",
                    "data": {"agent_name": f"A-{tid}-{i}", "description": f"d-{i}"},
                })

        await asyncio.gather(apply_to_thread("t1"), apply_to_thread("t2"))
        s1 = await manager.get_state("t1")
        s2 = await manager.get_state("t2")
        assert len(s1.live_state.active_agents) == 5
        assert len(s2.live_state.active_agents) == 5
        # No cross-thread leakage
        assert all(a.startswith("A-t1-") for a in s1.live_state.active_agents)
        assert all(a.startswith("A-t2-") for a in s2.live_state.active_agents)


# ---------------------------------------------------------------------------
# last_updated_at
# ---------------------------------------------------------------------------

class TestLastUpdatedAt:
    """Tests that last_updated_at is refreshed on mutations."""

    @pytest.mark.asyncio
    async def test_updated_on_apply_event(self, manager):
        state = await manager.get_or_create_state("t1")
        ts_before = state.last_updated_at
        await manager.apply_event("t1", {
            "type": "summary_updated",
            "data": {"key_summary": ["new"]},
        })
        state = await manager.get_state("t1")
        assert state.last_updated_at >= ts_before

    @pytest.mark.asyncio
    async def test_updated_on_lifecycle_change(self, manager):
        state = await manager.get_or_create_state("t1")
        ts_before = state.last_updated_at
        await manager.set_lifecycle_state("t1", "active")
        state = await manager.get_state("t1")
        assert state.last_updated_at >= ts_before
