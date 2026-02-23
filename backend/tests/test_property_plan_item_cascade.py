"""Property-based tests for PlanItem linked task completion cascade.

**Feature: workspace-refactor, Property 29: PlanItem linked task completion cascade**

Uses Hypothesis to verify that when a Task linked to a PlanItem via
source_task_id has its status changed to "completed", the PlanItem's status
automatically cascades to "completed".

**Validates: Requirements 22.7**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from datetime import datetime, timezone
from uuid import uuid4

from database import db
from core.plan_item_manager import plan_item_manager
from schemas.plan_item import (
    PlanItemCreate,
    PlanItemStatus,
    FocusType,
)
from schemas.todo import Priority
from tests.helpers import ensure_default_workspace


PROPERTY_SETTINGS = settings(
    max_examples=2,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# --- Strategies ---

title_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=100,
).filter(lambda x: x.strip())

description_strategy = st.one_of(
    st.none(),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
        min_size=0,
        max_size=200,
    ),
)

priority_strategy = st.sampled_from(list(Priority))
focus_type_strategy = st.sampled_from(list(FocusType))

# PlanItem statuses that are NOT already completed (cascade should change them)
non_completed_status_strategy = st.sampled_from([
    PlanItemStatus.ACTIVE,
    PlanItemStatus.DEFERRED,
])


async def _create_task(workspace_id: str, title: str) -> dict:
    """Create a task directly in the DB (avoids agent execution overhead)."""
    now = datetime.now(timezone.utc).isoformat()
    task_id = f"task_{uuid4().hex[:12]}"

    # Seed the default agent if needed
    agent = await db.agents.get("default")
    if not agent:
        await db.agents.put({
            "id": "default",
            "name": "Default Agent",
            "description": "Default system agent",
            "model": "claude-sonnet-4-20250514",
            "permission_mode": "default",
            "is_default": True,
            "created_at": now,
            "updated_at": now,
        })

    task = {
        "id": task_id,
        "agent_id": "default",
        "session_id": None,
        "status": "wip",
        "title": title[:50],
        "description": None,
        "priority": "none",
        "workspace_id": workspace_id,
        "source_todo_id": None,
        "blocked_reason": None,
        "model": "claude-sonnet-4-20250514",
        "created_at": now,
        "started_at": now,
        "completed_at": None,
        "error": None,
        "work_dir": None,
    }
    await db.tasks.put(task)
    return task


class TestPlanItemLinkedTaskCompletionCascade:
    """Property 29: PlanItem linked task completion cascade.

    *For any* PlanItem linked to a Task via source_task_id, when the Task's
    status changes to completed, the PlanItem's status SHALL automatically
    change to completed.

    **Validates: Requirements 22.7**
    """

    @given(
        title=title_strategy,
        description=description_strategy,
        priority=priority_strategy,
        focus_type=focus_type_strategy,
        initial_status=non_completed_status_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_plan_item_completes_when_linked_task_completes(
        self,
        title: str,
        description,
        priority: Priority,
        focus_type: FocusType,
        initial_status: PlanItemStatus,
    ):
        """A PlanItem linked to a Task becomes completed when the Task completes.

        **Validates: Requirements 22.7**
        """
        ws_id = await ensure_default_workspace()

        # 1. Create a task in WIP status
        task = await _create_task(ws_id, title)

        # 2. Create a PlanItem linked to that task via source_task_id
        plan_item = await plan_item_manager.create(PlanItemCreate(
            workspace_id=ws_id,
            title=title,
            description=description,
            source_task_id=task["id"],
            status=initial_status,
            priority=priority,
            focus_type=focus_type,
        ))

        # Verify PlanItem starts with the non-completed status
        assert plan_item.status == initial_status

        # 3. Simulate task completion by updating task status in DB
        await db.tasks.update(task["id"], {
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

        # 4. Trigger the cascade
        updated_count = await plan_item_manager.cascade_task_completion(task["id"])

        # Property: at least one PlanItem was updated
        assert updated_count >= 1, (
            f"Expected at least 1 PlanItem to be cascaded, got {updated_count}"
        )

        # 5. Verify the PlanItem status is now completed
        stored = await db.plan_items.get(plan_item.id)
        assert stored is not None
        assert stored["status"] == PlanItemStatus.COMPLETED.value, (
            f"Expected PlanItem status='completed', got '{stored['status']}'"
        )

    @given(
        title=title_strategy,
        priority=priority_strategy,
        focus_type=focus_type_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_already_completed_plan_item_not_double_cascaded(
        self,
        title: str,
        priority: Priority,
        focus_type: FocusType,
    ):
        """A PlanItem already completed is not re-updated by cascade.

        **Validates: Requirements 22.7**
        """
        ws_id = await ensure_default_workspace()

        task = await _create_task(ws_id, title)

        # Create a PlanItem that is already completed
        plan_item = await plan_item_manager.create(PlanItemCreate(
            workspace_id=ws_id,
            title=title,
            source_task_id=task["id"],
            status=PlanItemStatus.COMPLETED,
            priority=priority,
            focus_type=focus_type,
        ))

        assert plan_item.status == PlanItemStatus.COMPLETED

        # Trigger cascade — should skip already-completed items
        updated_count = await plan_item_manager.cascade_task_completion(task["id"])

        assert updated_count == 0, (
            f"Expected 0 PlanItems updated (already completed), got {updated_count}"
        )

    @given(
        title=title_strategy,
        priority=priority_strategy,
        focus_type=focus_type_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_unlinked_plan_item_not_affected_by_cascade(
        self,
        title: str,
        priority: Priority,
        focus_type: FocusType,
    ):
        """A PlanItem without source_task_id is not affected by any task cascade.

        **Validates: Requirements 22.7**
        """
        ws_id = await ensure_default_workspace()

        task = await _create_task(ws_id, title)

        # Create a PlanItem NOT linked to any task
        plan_item = await plan_item_manager.create(PlanItemCreate(
            workspace_id=ws_id,
            title=title,
            status=PlanItemStatus.ACTIVE,
            priority=priority,
            focus_type=focus_type,
        ))

        assert plan_item.source_task_id is None

        # Trigger cascade for the task
        updated_count = await plan_item_manager.cascade_task_completion(task["id"])

        assert updated_count == 0, (
            f"Expected 0 PlanItems updated (unlinked), got {updated_count}"
        )

        # Verify PlanItem status unchanged
        stored = await db.plan_items.get(plan_item.id)
        assert stored["status"] == PlanItemStatus.ACTIVE.value
