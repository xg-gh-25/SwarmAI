"""Property-based tests for overdue detection.

**Feature: workspace-refactor, Property 7: Overdue detection**

Uses Hypothesis to verify that the ToDoManager.check_overdue() background job
correctly transitions ToDos with past due_date and pending status to overdue,
while leaving other ToDos unaffected.

**Validates: Requirements 4.5, 4.6**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from database import db
from core.todo_manager import todo_manager
from schemas.todo import ToDoCreate, ToDoStatus, ToDoSourceType, Priority
from tests.helpers import ensure_default_workspace
from tests.helpers import PROPERTY_SETTINGS





# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Past dates: 1 to 365 days ago (always in the past relative to UTC now)
past_days_strategy = st.integers(min_value=1, max_value=365)

# Future dates: 1 to 365 days from now
future_days_strategy = st.integers(min_value=1, max_value=365)

todo_title_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=100,
).filter(lambda x: x.strip())

source_type_strategy = st.sampled_from(list(ToDoSourceType))
priority_strategy = st.sampled_from(list(Priority))

# Statuses that are NOT pending (should not be affected by overdue check)
non_pending_statuses = st.sampled_from([
    ToDoStatus.OVERDUE,
    ToDoStatus.IN_DISCUSSION,
    ToDoStatus.HANDLED,
    ToDoStatus.CANCELLED,
])


# ---------------------------------------------------------------------------
# Helpers — workspace setup imported from conftest
# ---------------------------------------------------------------------------


class TestOverduePendingToDosBecomOverdue:
    """Property 7: Overdue detection — pending ToDos with past due_date.

    *For any* ToDo where due_date is in the past and status is pending,
    after the overdue check job runs, the status SHALL be overdue.

    **Validates: Requirements 4.5**
    """

    @given(
        title=todo_title_strategy,
        days_ago=past_days_strategy,
        source_type=source_type_strategy,
        priority=priority_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_pending_todo_with_past_due_date_becomes_overdue(
        self,
        title: str,
        days_ago: int,
        source_type: ToDoSourceType,
        priority: Priority,
    ):
        """A pending ToDo with a past due_date becomes overdue after check_overdue.

        **Validates: Requirements 4.5**
        """
        ws_id = await ensure_default_workspace()
        past_due = datetime.now(timezone.utc) - timedelta(days=days_ago)

        todo_data = ToDoCreate(
            workspace_id=ws_id,
            title=title,
            source_type=source_type,
            priority=priority,
            due_date=past_due,
        )
        created = await todo_manager.create(todo_data)
        assert created.status == ToDoStatus.PENDING

        # Run the overdue check background job
        updated_count = await todo_manager.check_overdue()

        # Property: the ToDo must now be overdue
        assert updated_count >= 1, "Expected at least one ToDo to be marked overdue"

        stored = await db.todos.get(created.id)
        assert stored is not None
        assert stored["status"] == ToDoStatus.OVERDUE.value, (
            f"Expected status='overdue', got '{stored['status']}' "
            f"for ToDo with due_date {days_ago} days ago"
        )


class TestFutureDueDateRemainingPending:
    """Property 7: Overdue detection — future due_date stays pending.

    *For any* ToDo where due_date is in the future and status is pending,
    after the overdue check job runs, the status SHALL remain pending.

    **Validates: Requirements 4.5**
    """

    @given(
        title=todo_title_strategy,
        days_ahead=future_days_strategy,
        source_type=source_type_strategy,
        priority=priority_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_pending_todo_with_future_due_date_stays_pending(
        self,
        title: str,
        days_ahead: int,
        source_type: ToDoSourceType,
        priority: Priority,
    ):
        """A pending ToDo with a future due_date remains pending after check_overdue.

        **Validates: Requirements 4.5**
        """
        ws_id = await ensure_default_workspace()
        future_due = datetime.now(timezone.utc) + timedelta(days=days_ahead)

        todo_data = ToDoCreate(
            workspace_id=ws_id,
            title=title,
            source_type=source_type,
            priority=priority,
            due_date=future_due,
        )
        created = await todo_manager.create(todo_data)
        assert created.status == ToDoStatus.PENDING

        # Run the overdue check background job
        await todo_manager.check_overdue()

        # Property: the ToDo must still be pending
        stored = await db.todos.get(created.id)
        assert stored is not None
        assert stored["status"] == ToDoStatus.PENDING.value, (
            f"Expected status='pending', got '{stored['status']}' "
            f"for ToDo with due_date {days_ahead} days in the future"
        )


class TestNonPendingToDosUnaffected:
    """Property 7: Overdue detection — non-pending ToDos are not affected.

    *For any* ToDo where status is NOT pending, the overdue check job
    SHALL NOT change its status, regardless of due_date.

    **Validates: Requirements 4.6**
    """

    @given(
        title=todo_title_strategy,
        days_ago=past_days_strategy,
        non_pending_status=non_pending_statuses,
        priority=priority_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_non_pending_todo_with_past_due_date_unchanged(
        self,
        title: str,
        days_ago: int,
        non_pending_status: ToDoStatus,
        priority: Priority,
    ):
        """A non-pending ToDo with a past due_date is not changed by check_overdue.

        **Validates: Requirements 4.6**
        """
        ws_id = await ensure_default_workspace()
        past_due = datetime.now(timezone.utc) - timedelta(days=days_ago)

        # Create as pending first, then update to the target status
        todo_data = ToDoCreate(
            workspace_id=ws_id,
            title=title,
            source_type=ToDoSourceType.MANUAL,
            priority=priority,
            due_date=past_due,
        )
        created = await todo_manager.create(todo_data)

        # Manually set the status to the non-pending value
        from schemas.todo import ToDoUpdate
        await todo_manager.update(created.id, ToDoUpdate(status=non_pending_status))

        # Verify the status was set correctly
        before = await db.todos.get(created.id)
        assert before["status"] == non_pending_status.value

        # Run the overdue check background job
        await todo_manager.check_overdue()

        # Property: the status must remain unchanged
        after = await db.todos.get(created.id)
        assert after is not None
        assert after["status"] == non_pending_status.value, (
            f"Expected status='{non_pending_status.value}', got '{after['status']}' "
            f"— check_overdue should not modify non-pending ToDos"
        )
