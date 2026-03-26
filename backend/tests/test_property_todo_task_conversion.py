"""Property-based tests for ToDo to Task conversion round-trip.

**Feature: workspace-refactor, Property 8: ToDo to Task conversion round-trip**

Uses Hypothesis to verify that when a ToDo is converted to a Task, the
bidirectional linkage is correctly established:
- Task.source_todo_id = ToDo.id
- ToDo.task_id = Task.id
- ToDo.status = handled

**Validates: Requirements 4.7, 4.8, 5.6**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from datetime import datetime, timezone
from uuid import uuid4

from database import db
from core.todo_manager import todo_manager
from schemas.todo import (
    ToDoCreate,
    ToDoConvertToTaskRequest,
    ToDoSourceType,
    ToDoStatus,
    Priority,
)
from tests.helpers import ensure_default_workspace
from tests.helpers import PROPERTY_SETTINGS





# --- Strategies ---

todo_title_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=100,
).filter(lambda x: x.strip())

todo_description_strategy = st.one_of(
    st.none(),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
        min_size=0,
        max_size=200,
    ),
)

source_type_strategy = st.sampled_from(list(ToDoSourceType))
priority_strategy = st.sampled_from(list(Priority))

# Optional override title for the created Task
task_title_override_strategy = st.one_of(
    st.none(),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
        min_size=1,
        max_size=100,
    ).filter(lambda x: x.strip()),
)

# Optional override priority for the created Task
task_priority_override_strategy = st.one_of(
    st.none(),
    st.sampled_from(list(Priority)),
)


class TestToDoToTaskConversionRoundTrip:
    """Property 8: ToDo to Task conversion round-trip.

    *For any* ToDo that is converted to a Task, the system SHALL establish
    bidirectional linkage: Task.source_todo_id = ToDo.id, ToDo.task_id = Task.id,
    and ToDo.status = handled.

    **Validates: Requirements 4.7, 4.8, 5.6**
    """

    @given(
        title=todo_title_strategy,
        description=todo_description_strategy,
        source_type=source_type_strategy,
        priority=priority_strategy,
        task_title_override=task_title_override_strategy,
        task_priority_override=task_priority_override_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_conversion_establishes_bidirectional_link(
        self,
        title: str,
        description,
        source_type: ToDoSourceType,
        priority: Priority,
        task_title_override,
        task_priority_override,
    ):
        """Converting a ToDo to a Task links both entities and sets status to handled.

        **Validates: Requirements 4.7, 4.8, 5.6**
        """
        default_ws_id = await ensure_default_workspace()

        # 1. Create a ToDo
        todo_data = ToDoCreate(
            workspace_id=default_ws_id,
            title=title,
            description=description,
            source_type=source_type,
            priority=priority,
        )
        created_todo = await todo_manager.create(todo_data)
        todo_id = created_todo.id

        # 2. Convert ToDo to Task
        convert_request = ToDoConvertToTaskRequest(
            agent_id="default",
            title=task_title_override,
            priority=task_priority_override,
        )
        task = await todo_manager.convert_to_task(todo_id, convert_request)

        # Conversion must succeed
        assert task is not None, f"convert_to_task returned None for ToDo {todo_id}"

        task_id = task["id"]

        # 3. Verify bidirectional linkage

        # Property: Task.source_todo_id == ToDo.id  (Requirement 5.6)
        assert task["source_todo_id"] == todo_id, (
            f"Task.source_todo_id={task['source_todo_id']} != ToDo.id={todo_id}"
        )

        # Fetch the updated ToDo from DB
        updated_todo_row = await db.todos.get(todo_id)
        assert updated_todo_row is not None

        # Property: ToDo.task_id == Task.id  (Requirement 4.8)
        assert updated_todo_row["task_id"] == task_id, (
            f"ToDo.task_id={updated_todo_row['task_id']} != Task.id={task_id}"
        )

        # Property: ToDo.status == handled  (Requirement 4.7)
        assert updated_todo_row["status"] == ToDoStatus.HANDLED.value, (
            f"ToDo.status={updated_todo_row['status']} != 'handled'"
        )

        # Also verify the Task is persisted in the database
        stored_task = await db.tasks.get(task_id)
        assert stored_task is not None
        assert stored_task["source_todo_id"] == todo_id

        # Verify the Task inherits the ToDo's workspace_id
        assert task["workspace_id"] == default_ws_id, (
            f"Task.workspace_id={task['workspace_id']} != ToDo.workspace_id={default_ws_id}"
        )
