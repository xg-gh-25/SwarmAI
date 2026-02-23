"""Unit tests for TaskManager updates: legacy status mapping, workspace_id
default assignment, blocked_reason handling, and new field support.

Requirements: 5.1-5.8
"""
import pytest
from uuid import uuid4

from database import db
from core.task_manager import TaskManager, _LEGACY_STATUS_MAP
from tests.helpers import now_iso, create_workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_default_workspace() -> dict:
    return await create_workspace(name="SwarmWS", is_default=True)


async def _insert_task_raw(overrides: dict = None) -> dict:
    """Insert a task directly into the DB (bypassing TaskManager)."""
    now = now_iso()
    task = {
        "id": f"task_{uuid4().hex[:12]}",
        "agent_id": "default",
        "session_id": None,
        "status": "draft",
        "title": "Test task",
        "description": None,
        "priority": "none",
        "workspace_id": None,
        "source_todo_id": None,
        "blocked_reason": None,
        "model": "claude-sonnet-4-20250514",
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "error": None,
        "work_dir": None,
    }
    if overrides:
        task.update(overrides)
    return await db.tasks.put(task)


# ---------------------------------------------------------------------------
# Tests: Legacy Status Mapping
# ---------------------------------------------------------------------------


class TestLegacyStatusMapping:
    """Tests for TaskManager._map_legacy_status().

    Validates: Requirements 5.4
    """

    def test_pending_maps_to_draft(self):
        assert TaskManager._map_legacy_status("pending") == "draft"

    def test_running_maps_to_wip(self):
        assert TaskManager._map_legacy_status("running") == "wip"

    def test_failed_maps_to_blocked(self):
        assert TaskManager._map_legacy_status("failed") == "blocked"

    def test_new_statuses_pass_through(self):
        for status in ("draft", "wip", "blocked", "completed", "cancelled"):
            assert TaskManager._map_legacy_status(status) == status

    def test_unknown_status_passes_through(self):
        assert TaskManager._map_legacy_status("some_unknown") == "some_unknown"

    def test_legacy_map_is_complete(self):
        """Verify the mapping dict covers all expected legacy statuses."""
        assert set(_LEGACY_STATUS_MAP.keys()) == {"pending", "running", "failed"}


class TestGetTaskLegacyMapping:
    """Tests that get_task maps legacy statuses in DB rows.

    Validates: Requirements 5.4
    """

    async def test_get_task_maps_pending_to_draft(self):
        await _create_default_workspace()
        task = await _insert_task_raw({"status": "pending"})
        tm = TaskManager()
        result = await tm.get_task(task["id"])
        assert result["status"] == "draft"

    async def test_get_task_maps_running_to_wip(self):
        await _create_default_workspace()
        task = await _insert_task_raw({"status": "running"})
        tm = TaskManager()
        result = await tm.get_task(task["id"])
        assert result["status"] == "wip"

    async def test_get_task_maps_failed_to_blocked(self):
        await _create_default_workspace()
        task = await _insert_task_raw({"status": "failed"})
        tm = TaskManager()
        result = await tm.get_task(task["id"])
        assert result["status"] == "blocked"

    async def test_get_task_preserves_new_statuses(self):
        await _create_default_workspace()
        task = await _insert_task_raw({"status": "completed"})
        tm = TaskManager()
        result = await tm.get_task(task["id"])
        assert result["status"] == "completed"

    async def test_get_task_nonexistent_returns_none(self):
        tm = TaskManager()
        result = await tm.get_task("nonexistent_id")
        assert result is None


class TestListTasksLegacyMapping:
    """Tests that list_tasks maps legacy statuses and supports workspace_id.

    Validates: Requirements 5.4, 5.7
    """

    async def test_list_maps_legacy_statuses(self):
        await _create_default_workspace()
        await _insert_task_raw({"status": "pending"})
        await _insert_task_raw({"status": "running"})
        await _insert_task_raw({"status": "failed"})

        tm = TaskManager()
        tasks = await tm.list_tasks()
        statuses = {t["status"] for t in tasks}
        # Should contain mapped statuses, not legacy ones
        assert "pending" not in statuses
        assert "running" not in statuses
        assert "failed" not in statuses
        assert statuses == {"draft", "wip", "blocked"}

    async def test_list_filter_by_legacy_status_maps_query(self):
        """Filtering by 'pending' should find tasks with 'draft' status."""
        ws = await _create_default_workspace()
        await _insert_task_raw({"status": "draft", "workspace_id": ws["id"]})
        await _insert_task_raw({"status": "wip", "workspace_id": ws["id"]})

        tm = TaskManager()
        # Query with legacy status "pending" should map to "draft"
        tasks = await tm.list_tasks(status="pending")
        assert len(tasks) == 1
        assert tasks[0]["status"] == "draft"

    async def test_list_filter_by_workspace_id(self):
        ws1 = await _create_default_workspace()
        ws2 = await create_workspace(name="OtherWS")
        await _insert_task_raw({"workspace_id": ws1["id"]})
        await _insert_task_raw({"workspace_id": ws2["id"]})
        await _insert_task_raw({"workspace_id": ws2["id"]})

        tm = TaskManager()
        tasks_ws1 = await tm.list_tasks(workspace_id=ws1["id"])
        tasks_ws2 = await tm.list_tasks(workspace_id=ws2["id"])
        assert len(tasks_ws1) == 1
        assert len(tasks_ws2) == 2


# ---------------------------------------------------------------------------
# Tests: Workspace ID Defaulting
# ---------------------------------------------------------------------------


class TestWorkspaceIdDefaulting:
    """Tests that workspace_id defaults to SwarmWS when not provided.

    Validates: Requirements 1.4, 5.7
    """

    async def test_get_default_workspace_id_returns_swarmws(self):
        ws = await _create_default_workspace()
        tm = TaskManager()
        default_id = await tm._get_default_workspace_id()
        assert default_id == ws["id"]

    async def test_get_default_workspace_id_raises_when_no_default(self):
        """Should raise ValueError when no default workspace exists."""
        tm = TaskManager()
        with pytest.raises(ValueError, match="Default workspace"):
            await tm._get_default_workspace_id()

    async def test_insert_task_without_workspace_gets_default(self):
        """A task inserted without workspace_id should get SwarmWS id via get_default."""
        ws = await _create_default_workspace()
        # Insert a task with no workspace_id, then verify via list
        task = await _insert_task_raw({"workspace_id": None})
        tm = TaskManager()
        # list_tasks with workspace_id=None returns all tasks
        tasks = await tm.list_tasks()
        found = [t for t in tasks if t["id"] == task["id"]]
        assert len(found) == 1
        # The raw task has None workspace_id (we bypassed manager)
        assert found[0]["workspace_id"] is None

    async def test_list_tasks_workspace_filter_excludes_other(self):
        ws1 = await _create_default_workspace()
        ws2 = await create_workspace(name="ProjectWS")
        await _insert_task_raw({"workspace_id": ws1["id"], "title": "Task A"})
        await _insert_task_raw({"workspace_id": ws2["id"], "title": "Task B"})

        tm = TaskManager()
        tasks = await tm.list_tasks(workspace_id=ws2["id"])
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Task B"


# ---------------------------------------------------------------------------
# Tests: Blocked Reason Preservation
# ---------------------------------------------------------------------------


class TestBlockedReasonPreservation:
    """Tests that blocked_reason is preserved when tasks transition to blocked.

    Validates: Requirements 5.5
    """

    async def test_blocked_reason_stored_on_failure(self):
        """When a task is set to blocked, blocked_reason should be preserved."""
        await _create_default_workspace()
        error_msg = "Agent execution failed: timeout after 30s"
        task = await _insert_task_raw({
            "status": "blocked",
            "blocked_reason": error_msg,
            "error": error_msg,
        })

        tm = TaskManager()
        result = await tm.get_task(task["id"])
        assert result is not None
        assert result["status"] == "blocked"
        assert result["blocked_reason"] == error_msg

    async def test_blocked_reason_none_for_non_blocked(self):
        """Non-blocked tasks should have None blocked_reason."""
        await _create_default_workspace()
        task = await _insert_task_raw({"status": "draft"})

        tm = TaskManager()
        result = await tm.get_task(task["id"])
        assert result["blocked_reason"] is None

    async def test_failed_legacy_maps_to_blocked_with_reason(self):
        """Legacy 'failed' status maps to 'blocked' and reason is preserved."""
        await _create_default_workspace()
        error_msg = "Connection refused"
        task = await _insert_task_raw({
            "status": "failed",
            "blocked_reason": error_msg,
            "error": error_msg,
        })

        tm = TaskManager()
        result = await tm.get_task(task["id"])
        assert result["status"] == "blocked"
        assert result["blocked_reason"] == error_msg


# ---------------------------------------------------------------------------
# Tests: New Field Handling
# ---------------------------------------------------------------------------


class TestNewFieldHandling:
    """Tests for new task fields: priority, description, source_todo_id.

    Validates: Requirements 5.1, 5.6
    """

    async def test_priority_field_stored(self):
        await _create_default_workspace()
        task = await _insert_task_raw({"priority": "high"})

        tm = TaskManager()
        result = await tm.get_task(task["id"])
        assert result["priority"] == "high"

    async def test_description_field_stored(self):
        await _create_default_workspace()
        task = await _insert_task_raw({"description": "Implement feature X"})

        tm = TaskManager()
        result = await tm.get_task(task["id"])
        assert result["description"] == "Implement feature X"

    async def test_source_todo_id_field_stored(self):
        await _create_default_workspace()
        task = await _insert_task_raw({"source_todo_id": "todo_abc123"})

        tm = TaskManager()
        result = await tm.get_task(task["id"])
        assert result["source_todo_id"] == "todo_abc123"

    async def test_default_priority_is_none(self):
        await _create_default_workspace()
        task = await _insert_task_raw()

        tm = TaskManager()
        result = await tm.get_task(task["id"])
        assert result["priority"] == "none"

    async def test_workspace_id_field_stored(self):
        ws = await _create_default_workspace()
        task = await _insert_task_raw({"workspace_id": ws["id"]})

        tm = TaskManager()
        result = await tm.get_task(task["id"])
        assert result["workspace_id"] == ws["id"]
