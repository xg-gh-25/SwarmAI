"""Property-based tests for default workspace assignment.

**Feature: workspace-refactor, Property 3: Default workspace assignment**

Uses Hypothesis to verify that when a ToDo or Task is created without
specifying a workspace_id, it gets assigned the SwarmWS default workspace ID.

**Validates: Requirements 1.3, 1.4**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from datetime import datetime, timezone
from uuid import uuid4

from database import db
from core.todo_manager import todo_manager
from core.task_manager import task_manager
from schemas.todo import ToDoCreate, ToDoSourceType, Priority
from tests.helpers import ensure_default_workspace
from tests.helpers import PROPERTY_SETTINGS





# Strategies for generating ToDo creation data
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


class TestToDoDefaultWorkspaceAssignment:
    """Property 3: Default workspace assignment for ToDos.

    *For any* ToDo created without a workspace_id, the system SHALL assign
    it to SwarmWS by default.

    **Validates: Requirements 1.3**
    """

    @given(
        title=todo_title_strategy,
        description=todo_description_strategy,
        source_type=source_type_strategy,
        priority=priority_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_todo_without_workspace_gets_default(
        self,
        title: str,
        description,
        source_type: ToDoSourceType,
        priority: Priority,
    ):
        """A ToDo created with empty workspace_id gets SwarmWS.id assigned.

        **Validates: Requirements 1.3**
        """
        default_ws_id = await ensure_default_workspace()

        # Create ToDo with empty workspace_id (triggers default assignment)
        todo_data = ToDoCreate(
            workspace_id="",  # Empty string triggers default
            title=title,
            source_type=source_type,
            priority=priority,
            description=description,
        )

        result = await todo_manager.create(todo_data)

        # Property: workspace_id must equal the default workspace ID
        assert result.workspace_id == default_ws_id, (
            f"Expected workspace_id={default_ws_id}, got {result.workspace_id}"
        )

        # Verify it's persisted correctly in the database
        stored = await db.todos.get(result.id)
        assert stored is not None
        assert stored["workspace_id"] == default_ws_id


class TestTaskDefaultWorkspaceAssignment:
    """Property 3: Default workspace assignment for Tasks.

    *For any* Task created without a workspace_id, the system SHALL assign
    it to SwarmWS by default.

    **Validates: Requirements 1.4**
    """

    @given(
        title=todo_title_strategy,
        priority=st.sampled_from(["high", "medium", "low", "none"]),
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_task_without_workspace_gets_default(
        self,
        title: str,
        priority: str,
    ):
        """A Task created without workspace_id gets SwarmWS.id assigned.

        **Validates: Requirements 1.4**
        """
        default_ws_id = await ensure_default_workspace()

        # Create task without workspace_id (triggers default assignment)
        task = await task_manager.create_task(
            agent_id="default",
            message=title,
            workspace_id=None,  # None triggers default
            priority=priority,
        )

        # Property: workspace_id must equal the default workspace ID
        assert task["workspace_id"] == default_ws_id, (
            f"Expected workspace_id={default_ws_id}, got {task['workspace_id']}"
        )

        # Verify it's persisted correctly in the database
        stored = await db.tasks.get(task["id"])
        assert stored is not None
        assert stored["workspace_id"] == default_ws_id

        # Cleanup: cancel the task if it's running to avoid background task leaks
        try:
            await task_manager.cancel_task(task["id"])
        except Exception:
            pass
