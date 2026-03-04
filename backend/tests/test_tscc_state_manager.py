"""Tests for the simplified TSCCStateManager.

Tests the in-memory per-thread state manager after removal of telemetry
event handling (agent_activity, tool_invocation, sources_updated,
capability_activated, summary_updated).  Covers:

- State creation and retrieval (workspace and project scopes)
- Lifecycle state transitions (valid and invalid)
- LRU eviction behaviour
- Concurrent access safety
- State clearing
"""

import asyncio

import pytest

from core.tscc_state_manager import TSCCStateManager, VALID_TRANSITIONS


@pytest.fixture
def manager():
    return TSCCStateManager(max_entries=200)


@pytest.fixture
def small_manager():
    """Manager with small capacity for eviction tests."""
    return TSCCStateManager(max_entries=3)


# ── State creation and retrieval ──────────────────────────────────────

class TestGetOrCreateState:

    @pytest.mark.asyncio
    async def test_creates_default_workspace_state(self, manager):
        state = await manager.get_or_create_state("t1")
        assert state.thread_id == "t1"
        assert state.scope_type == "workspace"
        assert state.lifecycle_state == "new"
        assert state.live_state.context.scope_label == "Workspace: SwarmWS (General)"

    @pytest.mark.asyncio
    async def test_creates_project_scoped_state(self, manager):
        state = await manager.get_or_create_state("t1", project_id="proj-1", thread_title="My Project")
        assert state.scope_type == "project"
        assert state.live_state.context.scope_label == "Project: My Project"

    @pytest.mark.asyncio
    async def test_returns_existing_state(self, manager):
        s1 = await manager.get_or_create_state("t1")
        s2 = await manager.get_or_create_state("t1")
        assert s1 is s2

    @pytest.mark.asyncio
    async def test_scope_label_workspace_when_project_id_none(self, manager):
        state = await manager.get_or_create_state("t1", project_id=None)
        assert "Workspace" in state.live_state.context.scope_label

    @pytest.mark.asyncio
    async def test_scope_label_never_contains_none_string(self, manager):
        state = await manager.get_or_create_state("t1", project_id=None)
        assert "None" not in state.live_state.context.scope_label

    @pytest.mark.asyncio
    async def test_get_state_returns_none_for_unknown(self, manager):
        result = await manager.get_state("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_state_returns_existing(self, manager):
        await manager.get_or_create_state("t1")
        state = await manager.get_state("t1")
        assert state is not None
        assert state.thread_id == "t1"


# ── Lifecycle state transitions ───────────────────────────────────────

class TestSetLifecycleState:

    @pytest.mark.asyncio
    async def test_valid_transitions(self, manager):
        await manager.get_or_create_state("t1")
        await manager.set_lifecycle_state("t1", "active")
        state = await manager.get_state("t1")
        assert state.lifecycle_state == "active"

    @pytest.mark.asyncio
    async def test_all_valid_transitions(self, manager):
        for from_state, to_states in VALID_TRANSITIONS.items():
            for to_state in to_states:
                tid = f"t-{from_state}-{to_state}"
                await manager.get_or_create_state(tid)
                # Force the from_state by walking from "new"
                if from_state != "new":
                    await manager.set_lifecycle_state(tid, "active")
                    if from_state not in ("new", "active"):
                        await manager.set_lifecycle_state(tid, from_state)
                await manager.set_lifecycle_state(tid, to_state)
                state = await manager.get_state(tid)
                assert state.lifecycle_state == to_state

    @pytest.mark.asyncio
    async def test_invalid_transition_raises(self, manager):
        await manager.get_or_create_state("t1")
        with pytest.raises(ValueError, match="Invalid lifecycle transition"):
            await manager.set_lifecycle_state("t1", "idle")

    @pytest.mark.asyncio
    async def test_nonexistent_thread_raises(self, manager):
        with pytest.raises(KeyError):
            await manager.set_lifecycle_state("nonexistent", "active")


# ── Clear state ───────────────────────────────────────────────────────

class TestClearState:

    @pytest.mark.asyncio
    async def test_removes_thread_state(self, manager):
        await manager.get_or_create_state("t1")
        await manager.clear_state("t1")
        assert await manager.get_state("t1") is None

    @pytest.mark.asyncio
    async def test_clear_nonexistent_is_noop(self, manager):
        await manager.clear_state("nonexistent")  # should not raise


# ── LRU eviction ──────────────────────────────────────────────────────

class TestLRUEviction:

    @pytest.mark.asyncio
    async def test_evicts_oldest_entry(self, small_manager):
        await small_manager.get_or_create_state("t1")
        await small_manager.get_or_create_state("t2")
        await small_manager.get_or_create_state("t3")
        # t1 is oldest — creating t4 should evict it
        await small_manager.get_or_create_state("t4")
        assert await small_manager.get_state("t1") is None
        assert await small_manager.get_state("t2") is not None

    @pytest.mark.asyncio
    async def test_eviction_cleans_up_lock(self, small_manager):
        await small_manager.get_or_create_state("t1")
        await small_manager.get_or_create_state("t2")
        await small_manager.get_or_create_state("t3")
        await small_manager.get_or_create_state("t4")
        assert "t1" not in small_manager._locks

    @pytest.mark.asyncio
    async def test_access_refreshes_lru(self, small_manager):
        await small_manager.get_or_create_state("t1")
        await small_manager.get_or_create_state("t2")
        await small_manager.get_or_create_state("t3")
        # Access t1 to refresh it
        await small_manager.get_state("t1")
        # Now t2 is oldest — creating t4 should evict t2
        await small_manager.get_or_create_state("t4")
        assert await small_manager.get_state("t1") is not None
        assert await small_manager.get_state("t2") is None


# ── Concurrent access ─────────────────────────────────────────────────

class TestConcurrentAccess:

    @pytest.mark.asyncio
    async def test_concurrent_lifecycle_transitions(self, manager):
        """Multiple concurrent lifecycle transitions don't corrupt state."""
        await manager.get_or_create_state("t1")
        await manager.set_lifecycle_state("t1", "active")

        async def transition_to(state_name):
            try:
                await manager.set_lifecycle_state("t1", state_name)
            except (ValueError, KeyError):
                pass  # expected for invalid transitions

        # Fire multiple transitions concurrently
        await asyncio.gather(
            transition_to("paused"),
            transition_to("idle"),
            transition_to("failed"),
        )
        # State should be one of the valid targets from "active"
        state = await manager.get_state("t1")
        assert state.lifecycle_state in {"paused", "idle", "failed"}

    @pytest.mark.asyncio
    async def test_concurrent_different_threads(self, manager):
        """Concurrent operations on different threads don't interfere."""
        tasks = [
            manager.get_or_create_state(f"t{i}") for i in range(20)
        ]
        await asyncio.gather(*tasks)
        for i in range(20):
            state = await manager.get_state(f"t{i}")
            assert state is not None


# ── Timestamp updates ─────────────────────────────────────────────────

class TestLastUpdatedAt:

    @pytest.mark.asyncio
    async def test_updated_on_lifecycle_change(self, manager):
        state = await manager.get_or_create_state("t1")
        original_ts = state.last_updated_at
        await manager.set_lifecycle_state("t1", "active")
        state = await manager.get_state("t1")
        assert state.last_updated_at >= original_ts
