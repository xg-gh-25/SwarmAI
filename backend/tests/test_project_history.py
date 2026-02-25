"""Unit and property-based tests for project update history correctness.

Tests the ``SwarmWorkspaceManager.update_project()`` method and its
private helpers (``_compute_action_type``, ``_compute_changes_diff``,
``_enforce_history_cap``) to verify that update history entries are
correctly appended, action types follow the priority mapping, multi-field
updates record all changed fields, source parameters propagate, and
existing history entries remain unchanged after new updates.

Key test areas:

- ``TestSingleUpdateHistory``       — Single update appends correct entry
- ``TestActionTypePriority``        — Action type priority mapping
- ``TestMultiFieldUpdate``          — Multi-field updates record all changes
- ``TestSourcePropagation``         — Source parameter propagation
- ``TestHistoryImmutability``       — Existing entries unchanged after update
- ``TestUpdateHistoryCapEnforcement`` — Property 8: Cap enforcement via Hypothesis

**Requirements: 27.4, 27.5, 31.1, 31.2, 31.4, 31.5, 31.8**
"""

import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest
from hypothesis import given, settings, HealthCheck, strategies as st

from core.swarm_workspace_manager import SwarmWorkspaceManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_test_project(
    manager: SwarmWorkspaceManager,
    workspace_path: str,
    name: str = "test-project",
) -> dict:
    """Create a project and return its metadata."""
    projects_dir = Path(workspace_path) / "Projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    return await manager.create_project(
        project_name=name,
        workspace_path=workspace_path,
    )


# ---------------------------------------------------------------------------
# Tests: Single update appends correct history entry
# ---------------------------------------------------------------------------


class TestSingleUpdateHistory:
    """Verify a single update appends a correct history entry.

    Requirements: 27.4, 31.1, 31.2
    """

    @pytest.mark.asyncio
    async def test_status_update_appends_history_entry(self, tmp_path: Path):
        """Updating status appends a history entry with version, timestamp,
        action, changes, and source."""
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_test_project(manager, ws)
        project_id = created["id"]

        updated = await manager.update_project(
            project_id,
            {"status": "archived"},
            source="user",
            workspace_path=ws,
        )

        assert updated["version"] == 2
        assert updated["status"] == "archived"

        history = updated["update_history"]
        assert len(history) == 2  # created + status_changed

        entry = history[-1]
        assert entry["version"] == 2
        assert isinstance(entry["timestamp"], str) and len(entry["timestamp"]) > 0
        assert entry["action"] == "status_changed"
        assert entry["changes"] == {"status": {"from": "active", "to": "archived"}}
        assert entry["source"] == "user"

    @pytest.mark.asyncio
    async def test_description_update_appends_history_entry(self, tmp_path: Path):
        """Updating description appends a history entry with action 'updated'."""
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_test_project(manager, ws)
        project_id = created["id"]

        updated = await manager.update_project(
            project_id,
            {"description": "New description"},
            source="user",
            workspace_path=ws,
        )

        assert updated["version"] == 2
        assert updated["description"] == "New description"

        entry = updated["update_history"][-1]
        assert entry["version"] == 2
        assert entry["action"] == "updated"
        assert entry["changes"] == {
            "description": {"from": "", "to": "New description"}
        }

    @pytest.mark.asyncio
    async def test_no_op_update_does_not_append_history(self, tmp_path: Path):
        """Updating with the same values should not append a history entry."""
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_test_project(manager, ws)
        project_id = created["id"]

        # Update with the same status — no actual change
        result = await manager.update_project(
            project_id,
            {"status": "active"},
            source="user",
            workspace_path=ws,
        )

        # Version should remain 1, history should still have only the created entry
        assert result["version"] == 1
        assert len(result["update_history"]) == 1


# ---------------------------------------------------------------------------
# Tests: Action type priority
# ---------------------------------------------------------------------------


class TestActionTypePriority:
    """Verify action type priority: rename > status_changed > tags_modified
    > priority_changed > updated.

    Requirements: 31.1, 31.2
    """

    @pytest.mark.asyncio
    async def test_name_change_produces_renamed_action(self, tmp_path: Path):
        """When name is changed, action should be 'renamed' regardless of
        other fields."""
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_test_project(manager, ws)
        project_id = created["id"]

        updated = await manager.update_project(
            project_id,
            {"name": "new-name", "status": "archived"},
            source="user",
            workspace_path=ws,
        )

        entry = updated["update_history"][-1]
        assert entry["action"] == "renamed"

    @pytest.mark.asyncio
    async def test_status_change_produces_status_changed_action(self, tmp_path: Path):
        """When status is changed (without name), action should be
        'status_changed'."""
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_test_project(manager, ws)
        project_id = created["id"]

        updated = await manager.update_project(
            project_id,
            {"status": "completed", "tags": ["new-tag"]},
            source="user",
            workspace_path=ws,
        )

        entry = updated["update_history"][-1]
        assert entry["action"] == "status_changed"

    @pytest.mark.asyncio
    async def test_tags_change_produces_tags_modified_action(self, tmp_path: Path):
        """When tags are changed (without name or status), action should be
        'tags_modified'."""
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_test_project(manager, ws)
        project_id = created["id"]

        updated = await manager.update_project(
            project_id,
            {"tags": ["alpha", "beta"]},
            source="user",
            workspace_path=ws,
        )

        entry = updated["update_history"][-1]
        assert entry["action"] == "tags_modified"

    @pytest.mark.asyncio
    async def test_priority_change_produces_priority_changed_action(
        self, tmp_path: Path
    ):
        """When priority is changed (without name, status, or tags), action
        should be 'priority_changed'."""
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_test_project(manager, ws)
        project_id = created["id"]

        updated = await manager.update_project(
            project_id,
            {"priority": "high"},
            source="user",
            workspace_path=ws,
        )

        entry = updated["update_history"][-1]
        assert entry["action"] == "priority_changed"

    @pytest.mark.asyncio
    async def test_description_only_produces_updated_action(self, tmp_path: Path):
        """When only description is changed, action should be 'updated'."""
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_test_project(manager, ws)
        project_id = created["id"]

        updated = await manager.update_project(
            project_id,
            {"description": "A new description"},
            source="user",
            workspace_path=ws,
        )

        entry = updated["update_history"][-1]
        assert entry["action"] == "updated"

    @pytest.mark.asyncio
    async def test_priority_with_description_produces_priority_changed(
        self, tmp_path: Path
    ):
        """Priority takes precedence over description for action type."""
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_test_project(manager, ws)
        project_id = created["id"]

        updated = await manager.update_project(
            project_id,
            {"priority": "low", "description": "Updated desc"},
            source="user",
            workspace_path=ws,
        )

        entry = updated["update_history"][-1]
        assert entry["action"] == "priority_changed"


# ---------------------------------------------------------------------------
# Tests: Multi-field update records all changed fields
# ---------------------------------------------------------------------------


class TestMultiFieldUpdate:
    """Verify multi-field updates record all changed fields in the changes dict.

    Requirements: 31.1, 31.2
    """

    @pytest.mark.asyncio
    async def test_multi_field_update_records_all_changes(self, tmp_path: Path):
        """Updating status, tags, and description records all three in changes."""
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_test_project(manager, ws)
        project_id = created["id"]

        updated = await manager.update_project(
            project_id,
            {
                "status": "completed",
                "tags": ["done"],
                "description": "Finished project",
            },
            source="user",
            workspace_path=ws,
        )

        entry = updated["update_history"][-1]
        changes = entry["changes"]

        assert "status" in changes
        assert changes["status"] == {"from": "active", "to": "completed"}

        assert "tags" in changes
        assert changes["tags"] == {"from": [], "to": ["done"]}

        assert "description" in changes
        assert changes["description"] == {"from": "", "to": "Finished project"}

    @pytest.mark.asyncio
    async def test_multi_field_update_with_priority_and_tags(self, tmp_path: Path):
        """Updating priority and tags records both in changes."""
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_test_project(manager, ws)
        project_id = created["id"]

        updated = await manager.update_project(
            project_id,
            {"priority": "critical", "tags": ["urgent"]},
            source="user",
            workspace_path=ws,
        )

        entry = updated["update_history"][-1]
        changes = entry["changes"]

        assert "priority" in changes
        assert changes["priority"] == {"from": None, "to": "critical"}

        assert "tags" in changes
        assert changes["tags"] == {"from": [], "to": ["urgent"]}


# ---------------------------------------------------------------------------
# Tests: Source parameter propagation
# ---------------------------------------------------------------------------


class TestSourcePropagation:
    """Verify source parameter propagation for 'user', 'agent', 'system'.

    Requirements: 31.4, 31.5
    """

    @pytest.mark.asyncio
    async def test_user_source_propagated(self, tmp_path: Path):
        """Source 'user' is recorded in the history entry."""
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_test_project(manager, ws)

        updated = await manager.update_project(
            created["id"],
            {"status": "archived"},
            source="user",
            workspace_path=ws,
        )

        assert updated["update_history"][-1]["source"] == "user"

    @pytest.mark.asyncio
    async def test_agent_source_propagated(self, tmp_path: Path):
        """Source 'agent' is recorded in the history entry."""
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_test_project(manager, ws)

        updated = await manager.update_project(
            created["id"],
            {"status": "completed"},
            source="agent",
            workspace_path=ws,
        )

        assert updated["update_history"][-1]["source"] == "agent"

    @pytest.mark.asyncio
    async def test_system_source_propagated(self, tmp_path: Path):
        """Source 'system' is recorded in the history entry."""
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_test_project(manager, ws)

        updated = await manager.update_project(
            created["id"],
            {"priority": "high"},
            source="system",
            workspace_path=ws,
        )

        assert updated["update_history"][-1]["source"] == "system"

    @pytest.mark.asyncio
    async def test_default_source_is_user(self, tmp_path: Path):
        """When source is not specified, it defaults to 'user'."""
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_test_project(manager, ws)

        updated = await manager.update_project(
            created["id"],
            {"description": "Updated"},
            workspace_path=ws,
        )

        assert updated["update_history"][-1]["source"] == "user"


# ---------------------------------------------------------------------------
# Tests: Existing history entries remain unchanged
# ---------------------------------------------------------------------------


class TestHistoryImmutability:
    """Verify existing history entries remain unchanged after new updates.

    Requirements: 31.8
    """

    @pytest.mark.asyncio
    async def test_prior_entries_unchanged_after_second_update(self, tmp_path: Path):
        """After two updates, the first update's history entry is unchanged."""
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_test_project(manager, ws)
        project_id = created["id"]

        # First update
        after_first = await manager.update_project(
            project_id,
            {"status": "archived"},
            source="user",
            workspace_path=ws,
        )
        # Snapshot the history after first update
        snapshot_created = copy.deepcopy(after_first["update_history"][0])
        snapshot_first = copy.deepcopy(after_first["update_history"][1])

        # Second update
        after_second = await manager.update_project(
            project_id,
            {"priority": "high"},
            source="agent",
            workspace_path=ws,
        )

        # Verify prior entries are unchanged
        assert after_second["update_history"][0] == snapshot_created
        assert after_second["update_history"][1] == snapshot_first

        # Verify the new entry is appended
        assert len(after_second["update_history"]) == 3
        assert after_second["update_history"][2]["action"] == "priority_changed"
        assert after_second["update_history"][2]["source"] == "agent"

    @pytest.mark.asyncio
    async def test_history_persisted_to_disk(self, tmp_path: Path):
        """History entries survive a re-read from disk."""
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_test_project(manager, ws)
        project_id = created["id"]

        await manager.update_project(
            project_id,
            {"tags": ["v1"]},
            source="user",
            workspace_path=ws,
        )

        # Read directly from disk with a fresh manager instance
        fresh_manager = SwarmWorkspaceManager()
        fetched = await fresh_manager.get_project(project_id, workspace_path=ws)

        assert len(fetched["update_history"]) == 2
        assert fetched["update_history"][0]["action"] == "created"
        assert fetched["update_history"][1]["action"] == "tags_modified"
        assert fetched["update_history"][1]["changes"]["tags"] == {
            "from": [],
            "to": ["v1"],
        }

    @pytest.mark.asyncio
    async def test_three_sequential_updates_preserve_all_entries(
        self, tmp_path: Path
    ):
        """Three sequential updates produce 4 history entries (1 created + 3
        updates), all with correct version numbers and unchanged prior entries."""
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_test_project(manager, ws)
        project_id = created["id"]

        # Update 1: status
        await manager.update_project(
            project_id, {"status": "archived"}, source="user", workspace_path=ws
        )
        # Update 2: tags
        await manager.update_project(
            project_id, {"tags": ["important"]}, source="agent", workspace_path=ws
        )
        # Update 3: priority
        result = await manager.update_project(
            project_id, {"priority": "critical"}, source="system", workspace_path=ws
        )

        history = result["update_history"]
        assert len(history) == 4

        # Verify version numbers are sequential
        for i, entry in enumerate(history):
            assert entry["version"] == i + 1

        # Verify actions
        assert history[0]["action"] == "created"
        assert history[1]["action"] == "status_changed"
        assert history[2]["action"] == "tags_modified"
        assert history[3]["action"] == "priority_changed"

        # Verify sources
        assert history[0]["source"] == "user"
        assert history[1]["source"] == "user"
        assert history[2]["source"] == "agent"
        assert history[3]["source"] == "system"


# ---------------------------------------------------------------------------
# Hypothesis settings
# ---------------------------------------------------------------------------

PROPERTY_SETTINGS = settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Number of updates to apply (always exceeds the 50-entry cap)
num_updates_strategy = st.integers(min_value=51, max_value=100)

# Sources to cycle through
_SOURCES = ["user", "agent", "system"]


def _make_unique_update(i: int) -> dict:
    """Return an update dict guaranteed to differ from the previous iteration.

    Each call uses a unique value derived from *i* so that consecutive
    updates always produce an actual change (never a no-op).  We use
    ``description`` with a unique string for every update — this field
    accepts arbitrary text, so every value is guaranteed to be new.
    We also rotate a secondary field to exercise different action types.
    """
    # Always include a unique description to guarantee a real change
    base = {"description": f"Revision {i}"}
    # Additionally rotate a secondary field for variety
    secondary_index = i % 3
    if secondary_index == 0:
        base["tags"] = [f"tag-{i}"]
    elif secondary_index == 1:
        base["priority"] = ["low", "medium", "high", "critical"][i % 4]
    # secondary_index == 2: description-only update
    return base


# ---------------------------------------------------------------------------
# Property 8: Update History Cap Enforcement
# ---------------------------------------------------------------------------


class TestUpdateHistoryCapEnforcement:
    """Property 8: Update History Cap Enforcement.

    # Feature: swarmws-projects, Property 8: Update History Cap Enforcement

    *For any* project that has undergone N updates where N > 50, the
    ``update_history`` array should contain exactly 50 entries, and those
    entries should be the 50 most recent (by version number). No entries
    with version ≤ (N − 50) should be present.

    **Validates: Requirements 27.5**
    """

    @given(num_updates=num_updates_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_history_capped_at_50_with_most_recent_entries(
        self,
        tmp_path: Path,
        num_updates: int,
    ):
        """After N (51–100) updates, history has exactly 50 entries — the most recent.

        **Validates: Requirements 27.5**
        """
        # Use a unique workspace dir per Hypothesis example
        workspace_dir = tmp_path / str(uuid4())
        workspace_dir.mkdir(parents=True, exist_ok=True)
        projects_dir = workspace_dir / "Projects"
        projects_dir.mkdir(parents=True, exist_ok=True)

        ws = str(workspace_dir)
        manager = SwarmWorkspaceManager()
        created = await _create_test_project(manager, ws, name="cap-test")
        project_id = created["id"]

        # Apply num_updates updates, each guaranteed to produce an actual change
        for i in range(1, num_updates + 1):
            update = _make_unique_update(i)
            source = _SOURCES[i % len(_SOURCES)]
            await manager.update_project(
                project_id,
                update,
                source=source,
                workspace_path=ws,
            )

        # Read final state
        result = await manager.get_project(project_id, workspace_path=ws)
        history = result["update_history"]

        # Total versions: 1 (created) + num_updates
        total_versions = 1 + num_updates

        # --- Cap enforcement: exactly 50 entries ---
        assert len(history) == 50, (
            f"Expected exactly 50 history entries after {num_updates} updates, "
            f"got {len(history)}"
        )

        # --- All entries are the most recent by version number ---
        expected_min_version = total_versions - 50 + 1
        versions = [entry["version"] for entry in history]

        for v in versions:
            assert v >= expected_min_version, (
                f"History entry with version {v} should have been trimmed; "
                f"expected only versions >= {expected_min_version}"
            )

        # --- Versions are in ascending order (oldest-first within the cap) ---
        assert versions == sorted(versions), (
            "History entries should be in ascending version order"
        )

        # --- The last entry's version matches the total version count ---
        assert history[-1]["version"] == total_versions, (
            f"Last history entry version should be {total_versions}, "
            f"got {history[-1]['version']}"
        )

        # --- The first entry's version is the expected minimum ---
        assert history[0]["version"] == expected_min_version, (
            f"First history entry version should be {expected_min_version}, "
            f"got {history[0]['version']}"
        )

        # --- Entries form a contiguous sequence of 50 versions ---
        expected_versions = list(range(expected_min_version, total_versions + 1))
        assert versions == expected_versions, (
            f"History versions should be contiguous from {expected_min_version} "
            f"to {total_versions}"
        )
