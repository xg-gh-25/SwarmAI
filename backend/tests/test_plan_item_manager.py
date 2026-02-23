"""Unit tests for PlanItemManager.

Tests CRUD operations, linked task completion cascade, reordering,
and default workspace assignment.

Requirements: 22.1-22.12
"""
import pytest
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from database import db
from core.plan_item_manager import plan_item_manager
from schemas.plan_item import (
    PlanItemCreate,
    PlanItemUpdate,
    PlanItemStatus,
    FocusType,
)
from schemas.todo import Priority
from tests.helpers import now_iso, create_workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_task(workspace_id: str, status: str = "draft", **kwargs) -> dict:
    now = now_iso()
    task = {
        "id": kwargs.get("id", f"task_{uuid4().hex[:12]}"),
        "agent_id": "default",
        "workspace_id": workspace_id,
        "session_id": None,
        "status": status,
        "title": kwargs.get("title", f"Task {uuid4().hex[:6]}"),
        "description": None,
        "priority": "none",
        "source_todo_id": None,
        "blocked_reason": None,
        "model": "claude-sonnet-4-20250514",
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "error": None,
        "work_dir": None,
        "updated_at": now,
    }
    return await db.tasks.put(task)


# ---------------------------------------------------------------------------
# Tests: CRUD
# ---------------------------------------------------------------------------

class TestPlanItemCreate:
    """Tests for PlanItemManager.create"""

    @pytest.mark.asyncio
    async def test_create_basic(self):
        ws = await create_workspace()
        data = PlanItemCreate(
            workspace_id=ws["id"],
            title="Write design doc",
        )
        result = await plan_item_manager.create(data)

        assert result.id is not None
        assert result.workspace_id == ws["id"]
        assert result.title == "Write design doc"
        assert result.status == PlanItemStatus.ACTIVE
        assert result.focus_type == FocusType.UPCOMING
        assert result.priority == Priority.NONE
        assert result.sort_order == 0

    @pytest.mark.asyncio
    async def test_create_with_all_fields(self):
        ws = await create_workspace()
        scheduled = datetime.now(timezone.utc) + timedelta(days=1)
        data = PlanItemCreate(
            workspace_id=ws["id"],
            title="Review PR",
            description="Review the authentication PR",
            status=PlanItemStatus.ACTIVE,
            priority=Priority.HIGH,
            focus_type=FocusType.TODAY,
            scheduled_date=scheduled,
            sort_order=5,
        )
        result = await plan_item_manager.create(data)

        assert result.title == "Review PR"
        assert result.description == "Review the authentication PR"
        assert result.priority == Priority.HIGH
        assert result.focus_type == FocusType.TODAY
        assert result.sort_order == 5

    @pytest.mark.asyncio
    async def test_create_defaults_to_swarmws(self):
        swarm_ws = await create_workspace("SwarmWS", is_default=True)
        data = PlanItemCreate(
            workspace_id="",
            title="Global plan item",
        )
        result = await plan_item_manager.create(data)
        assert result.workspace_id == swarm_ws["id"]

    @pytest.mark.asyncio
    async def test_create_with_linked_task(self):
        ws = await create_workspace()
        task = await _create_task(ws["id"])
        data = PlanItemCreate(
            workspace_id=ws["id"],
            title="Linked to task",
            source_task_id=task["id"],
        )
        result = await plan_item_manager.create(data)
        assert result.source_task_id == task["id"]


class TestPlanItemGet:
    """Tests for PlanItemManager.get"""

    @pytest.mark.asyncio
    async def test_get_existing(self):
        ws = await create_workspace()
        created = await plan_item_manager.create(
            PlanItemCreate(workspace_id=ws["id"], title="Get me")
        )
        result = await plan_item_manager.get(created.id)
        assert result is not None
        assert result.id == created.id
        assert result.title == "Get me"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self):
        result = await plan_item_manager.get("nonexistent-id")
        assert result is None


class TestPlanItemList:
    """Tests for PlanItemManager.list"""

    @pytest.mark.asyncio
    async def test_list_by_workspace(self):
        ws = await create_workspace()
        await plan_item_manager.create(PlanItemCreate(workspace_id=ws["id"], title="Item 1"))
        await plan_item_manager.create(PlanItemCreate(workspace_id=ws["id"], title="Item 2"))

        results = await plan_item_manager.list(workspace_id=ws["id"])
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_list_filter_by_focus_type(self):
        ws = await create_workspace()
        await plan_item_manager.create(
            PlanItemCreate(workspace_id=ws["id"], title="Today", focus_type=FocusType.TODAY)
        )
        await plan_item_manager.create(
            PlanItemCreate(workspace_id=ws["id"], title="Upcoming", focus_type=FocusType.UPCOMING)
        )

        results = await plan_item_manager.list(workspace_id=ws["id"], focus_type=FocusType.TODAY)
        assert len(results) == 1
        assert results[0].title == "Today"

    @pytest.mark.asyncio
    async def test_list_filter_by_status(self):
        ws = await create_workspace()
        await plan_item_manager.create(
            PlanItemCreate(workspace_id=ws["id"], title="Active item")
        )
        item2 = await plan_item_manager.create(
            PlanItemCreate(workspace_id=ws["id"], title="Deferred item")
        )
        await plan_item_manager.update(item2.id, PlanItemUpdate(status=PlanItemStatus.DEFERRED))

        results = await plan_item_manager.list(workspace_id=ws["id"], status=PlanItemStatus.ACTIVE)
        assert len(results) == 1
        assert results[0].title == "Active item"

    @pytest.mark.asyncio
    async def test_list_pagination(self):
        ws = await create_workspace()
        for i in range(5):
            await plan_item_manager.create(
                PlanItemCreate(workspace_id=ws["id"], title=f"Item {i}")
            )

        page1 = await plan_item_manager.list(workspace_id=ws["id"], limit=2, offset=0)
        page2 = await plan_item_manager.list(workspace_id=ws["id"], limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2

    @pytest.mark.asyncio
    async def test_list_isolates_workspaces(self):
        ws1 = await create_workspace("WS1")
        ws2 = await create_workspace("WS2")
        await plan_item_manager.create(PlanItemCreate(workspace_id=ws1["id"], title="WS1 item"))
        await plan_item_manager.create(PlanItemCreate(workspace_id=ws2["id"], title="WS2 item"))

        results = await plan_item_manager.list(workspace_id=ws1["id"])
        assert len(results) == 1
        assert results[0].title == "WS1 item"


class TestPlanItemUpdate:
    """Tests for PlanItemManager.update"""

    @pytest.mark.asyncio
    async def test_update_title(self):
        ws = await create_workspace()
        created = await plan_item_manager.create(
            PlanItemCreate(workspace_id=ws["id"], title="Original")
        )
        result = await plan_item_manager.update(created.id, PlanItemUpdate(title="Updated"))
        assert result.title == "Updated"

    @pytest.mark.asyncio
    async def test_update_status(self):
        ws = await create_workspace()
        created = await plan_item_manager.create(
            PlanItemCreate(workspace_id=ws["id"], title="Item")
        )
        result = await plan_item_manager.update(
            created.id, PlanItemUpdate(status=PlanItemStatus.COMPLETED)
        )
        assert result.status == PlanItemStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_update_focus_type(self):
        ws = await create_workspace()
        created = await plan_item_manager.create(
            PlanItemCreate(workspace_id=ws["id"], title="Item", focus_type=FocusType.UPCOMING)
        )
        result = await plan_item_manager.update(
            created.id, PlanItemUpdate(focus_type=FocusType.TODAY)
        )
        assert result.focus_type == FocusType.TODAY

    @pytest.mark.asyncio
    async def test_update_nonexistent(self):
        result = await plan_item_manager.update("nonexistent", PlanItemUpdate(title="X"))
        assert result is None

    @pytest.mark.asyncio
    async def test_update_no_changes(self):
        ws = await create_workspace()
        created = await plan_item_manager.create(
            PlanItemCreate(workspace_id=ws["id"], title="No change")
        )
        result = await plan_item_manager.update(created.id, PlanItemUpdate())
        assert result.title == "No change"


class TestPlanItemDelete:
    """Tests for PlanItemManager.delete"""

    @pytest.mark.asyncio
    async def test_delete_existing(self):
        ws = await create_workspace()
        created = await plan_item_manager.create(
            PlanItemCreate(workspace_id=ws["id"], title="Delete me")
        )
        assert await plan_item_manager.delete(created.id) is True
        assert await plan_item_manager.get(created.id) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self):
        assert await plan_item_manager.delete("nonexistent") is False


# ---------------------------------------------------------------------------
# Tests: Linked Task Completion Cascade
# ---------------------------------------------------------------------------

class TestCascadeTaskCompletion:
    """Tests for PlanItemManager.cascade_task_completion

    Validates: Requirements 22.7
    """

    @pytest.mark.asyncio
    async def test_cascade_completes_linked_plan_item(self):
        ws = await create_workspace()
        task = await _create_task(ws["id"], status="completed")

        created = await plan_item_manager.create(
            PlanItemCreate(
                workspace_id=ws["id"],
                title="Linked item",
                source_task_id=task["id"],
            )
        )
        assert created.status == PlanItemStatus.ACTIVE

        count = await plan_item_manager.cascade_task_completion(task["id"])
        assert count == 1

        updated = await plan_item_manager.get(created.id)
        assert updated.status == PlanItemStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_cascade_skips_already_completed(self):
        ws = await create_workspace()
        task = await _create_task(ws["id"], status="completed")

        item = await plan_item_manager.create(
            PlanItemCreate(
                workspace_id=ws["id"],
                title="Already done",
                source_task_id=task["id"],
            )
        )
        await plan_item_manager.update(item.id, PlanItemUpdate(status=PlanItemStatus.COMPLETED))

        count = await plan_item_manager.cascade_task_completion(task["id"])
        assert count == 0

    @pytest.mark.asyncio
    async def test_cascade_multiple_plan_items(self):
        ws = await create_workspace()
        task = await _create_task(ws["id"])

        for i in range(3):
            await plan_item_manager.create(
                PlanItemCreate(
                    workspace_id=ws["id"],
                    title=f"Item {i}",
                    source_task_id=task["id"],
                )
            )

        count = await plan_item_manager.cascade_task_completion(task["id"])
        assert count == 3

    @pytest.mark.asyncio
    async def test_cascade_no_linked_items(self):
        count = await plan_item_manager.cascade_task_completion("nonexistent-task")
        assert count == 0

    @pytest.mark.asyncio
    async def test_cascade_does_not_affect_unlinked(self):
        ws = await create_workspace()
        task = await _create_task(ws["id"])

        linked = await plan_item_manager.create(
            PlanItemCreate(workspace_id=ws["id"], title="Linked", source_task_id=task["id"])
        )
        unlinked = await plan_item_manager.create(
            PlanItemCreate(workspace_id=ws["id"], title="Unlinked")
        )

        await plan_item_manager.cascade_task_completion(task["id"])

        assert (await plan_item_manager.get(linked.id)).status == PlanItemStatus.COMPLETED
        assert (await plan_item_manager.get(unlinked.id)).status == PlanItemStatus.ACTIVE


# ---------------------------------------------------------------------------
# Tests: Reordering
# ---------------------------------------------------------------------------

class TestReorder:
    """Tests for PlanItemManager.reorder

    Validates: Requirements 22.6
    """

    @pytest.mark.asyncio
    async def test_reorder_updates_sort_order(self):
        ws = await create_workspace()
        items = []
        for i in range(3):
            item = await plan_item_manager.create(
                PlanItemCreate(
                    workspace_id=ws["id"],
                    title=f"Item {i}",
                    focus_type=FocusType.TODAY,
                    sort_order=i,
                )
            )
            items.append(item)

        # Reverse the order
        reversed_ids = [items[2].id, items[1].id, items[0].id]
        result = await plan_item_manager.reorder(ws["id"], FocusType.TODAY, reversed_ids)

        assert len(result) == 3
        assert result[0].sort_order == 0
        assert result[0].id == items[2].id
        assert result[1].sort_order == 1
        assert result[2].sort_order == 2
        assert result[2].id == items[0].id

    @pytest.mark.asyncio
    async def test_reorder_invalid_id_raises(self):
        ws = await create_workspace()
        await plan_item_manager.create(
            PlanItemCreate(workspace_id=ws["id"], title="Item", focus_type=FocusType.TODAY)
        )

        with pytest.raises(ValueError, match="not found"):
            await plan_item_manager.reorder(ws["id"], FocusType.TODAY, ["bad-id"])

    @pytest.mark.asyncio
    async def test_reorder_wrong_focus_type_raises(self):
        ws = await create_workspace()
        item = await plan_item_manager.create(
            PlanItemCreate(workspace_id=ws["id"], title="Upcoming item", focus_type=FocusType.UPCOMING)
        )

        with pytest.raises(ValueError, match="not found"):
            await plan_item_manager.reorder(ws["id"], FocusType.TODAY, [item.id])

    @pytest.mark.asyncio
    async def test_reorder_partial_list(self):
        ws = await create_workspace()
        items = []
        for i in range(3):
            item = await plan_item_manager.create(
                PlanItemCreate(
                    workspace_id=ws["id"],
                    title=f"Item {i}",
                    focus_type=FocusType.TODAY,
                    sort_order=i,
                )
            )
            items.append(item)

        # Reorder only 2 of 3
        result = await plan_item_manager.reorder(
            ws["id"], FocusType.TODAY, [items[1].id, items[0].id]
        )
        assert len(result) == 2
        assert result[0].id == items[1].id
        assert result[0].sort_order == 0
        assert result[1].id == items[0].id
        assert result[1].sort_order == 1
