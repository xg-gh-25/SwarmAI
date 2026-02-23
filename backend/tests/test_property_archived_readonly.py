"""Property-based tests for archived workspace read-only enforcement.

**Feature: workspace-refactor, Property 24: Archived workspace read-only**

Uses Hypothesis to verify that write operations (create ToDo, create Task)
fail with PermissionError on archived workspaces, while read operations
(list ToDos, get ToDo) succeed.

**Validates: Requirements 36.1-36.11**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from datetime import datetime, timezone

from database import db
from core.todo_manager import ToDoManager
from schemas.todo import ToDoCreate, ToDoStatus, Priority
from tests.helpers import create_custom_workspace, seed_todo


PROPERTY_SETTINGS = settings(
    max_examples=2,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

title_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=100,
).filter(lambda x: x.strip())

priority_strategy = st.sampled_from(list(Priority))


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestArchivedWorkspaceBlocksCreateToDo:
    """Property 24: Archived workspace read-only — ToDo creation blocked.

    *For any* workspace where is_archived=true, creating a ToDo SHALL
    fail with PermissionError.

    **Validates: Requirements 36.6, 36.7**
    """

    @given(title=title_strategy, priority=priority_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_create_todo_in_archived_workspace_fails(
        self,
        title: str,
        priority: Priority,
    ):
        """Creating a ToDo in an archived workspace raises PermissionError.

        **Validates: Requirements 36.6**
        """
        archived_ws_id = await create_custom_workspace(name="ArchivedWS", is_archived=True)
        manager = ToDoManager()

        with pytest.raises(PermissionError, match="archived"):
            await manager.create(
                ToDoCreate(
                    workspace_id=archived_ws_id,
                    title=title,
                    priority=priority,
                )
            )

        # Verify nothing was persisted
        todos = await db.todos.list_by_workspace(archived_ws_id)
        assert len(todos) == 0, (
            f"Expected 0 ToDos in archived workspace, found {len(todos)}"
        )


class TestArchivedWorkspaceBlocksCreateTask:
    """Property 24: Archived workspace read-only — Task creation blocked.

    *For any* workspace where is_archived=true, creating a Task SHALL
    fail with PermissionError.

    **Validates: Requirements 36.6, 36.8**
    """

    @given(title=title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_create_task_in_archived_workspace_fails(
        self,
        title: str,
    ):
        """Creating a Task in an archived workspace raises PermissionError.

        **Validates: Requirements 36.8**
        """
        from core.task_manager import task_manager

        archived_ws_id = await create_custom_workspace(name="ArchivedWS", is_archived=True)

        with pytest.raises(PermissionError, match="archived"):
            await task_manager.create_task(
                agent_id="default",
                message=title,
                workspace_id=archived_ws_id,
            )


class TestArchivedWorkspaceAllowsRead:
    """Property 24: Archived workspace read-only — read operations succeed.

    *For any* workspace where is_archived=true that contains pre-existing
    ToDos, listing and getting those ToDos SHALL succeed.

    **Validates: Requirements 36.6, 36.9**
    """

    @given(title=title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_list_todos_in_archived_workspace_succeeds(
        self,
        title: str,
    ):
        """Listing ToDos from an archived workspace succeeds.

        **Validates: Requirements 36.6**
        """
        # Create active workspace, seed a ToDo, then archive it
        ws_id = await create_custom_workspace(name="ActiveWS")
        todo_id = await seed_todo(ws_id, "Pre-existing ToDo")

        # Archive the workspace
        now = datetime.now(timezone.utc).isoformat()
        await db.swarm_workspaces.update(ws_id, {
            "is_archived": 1,
            "archived_at": now,
        })

        # Read operations should succeed
        manager = ToDoManager()
        todos = await manager.list(workspace_id=ws_id)
        assert len(todos) >= 1, "Expected at least 1 ToDo from archived workspace"

        # Verify the seeded ToDo is present
        todo_ids = [t.id for t in todos]
        assert todo_id in todo_ids, (
            f"Seeded ToDo {todo_id} not found in archived workspace list"
        )

    @given(title=title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_get_todo_in_archived_workspace_succeeds(
        self,
        title: str,
    ):
        """Getting a specific ToDo from an archived workspace succeeds.

        **Validates: Requirements 36.9**
        """
        # Create active workspace, seed a ToDo, then archive it
        ws_id = await create_custom_workspace(name="ActiveWS")
        todo_id = await seed_todo(ws_id, "Pre-existing ToDo")

        # Archive the workspace
        now = datetime.now(timezone.utc).isoformat()
        await db.swarm_workspaces.update(ws_id, {
            "is_archived": 1,
            "archived_at": now,
        })

        # Get specific ToDo should succeed
        manager = ToDoManager()
        todo = await manager.get(todo_id)
        assert todo is not None, "Expected to retrieve ToDo from archived workspace"
        assert todo.id == todo_id
        assert todo.workspace_id == ws_id


class TestActiveWorkspaceAllowsWrite:
    """Property 24: Archived workspace read-only — active workspace contrast.

    *For any* workspace where is_archived=false, creating a ToDo SHALL
    succeed. This confirms the guard only blocks archived workspaces.

    **Validates: Requirements 36.1-36.11**
    """

    @given(title=title_strategy, priority=priority_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_create_todo_in_active_workspace_succeeds(
        self,
        title: str,
        priority: Priority,
    ):
        """Creating a ToDo in an active (non-archived) workspace succeeds.

        **Validates: Requirements 36.1**
        """
        active_ws_id = await create_custom_workspace(name="ActiveWS")
        manager = ToDoManager()

        todo = await manager.create(
            ToDoCreate(
                workspace_id=active_ws_id,
                title=title,
                priority=priority,
            )
        )

        assert todo is not None
        assert todo.workspace_id == active_ws_id
        assert todo.title == title
