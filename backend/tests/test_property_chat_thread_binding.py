"""Property-based tests for ChatThread workspace binding.

**Feature: workspace-refactor, Property 22: ChatThread workspace binding**

Uses Hypothesis to verify that ChatThread entities correctly inherit
workspace_id from linked ToDo or Task entities, and fall back to the
provided workspace_id or SwarmWS default when no binding exists.

**Validates: Requirements 30.1-30.14**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from datetime import datetime, timezone
from uuid import uuid4

from database import db
from core.chat_thread_manager import chat_thread_manager
from schemas.chat_thread import ChatThreadCreate, ChatMode
from tests.helpers import ensure_default_workspace, create_custom_workspace


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

mode_strategy = st.sampled_from(list(ChatMode))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_agent() -> dict:
    """Create a test agent and return its DB row."""
    now = datetime.now(timezone.utc).isoformat()
    aid = str(uuid4())
    return await db.agents.put({
        "id": aid,
        "name": f"Agent-{aid[:8]}",
        "description": "Test agent",
        "model": "claude-sonnet-4-20250514",
        "permission_mode": "default",
        "is_default": False,
        "created_at": now,
        "updated_at": now,
    })


async def _create_todo(workspace_id: str) -> dict:
    """Create a test ToDo in the given workspace and return its DB row."""
    now = datetime.now(timezone.utc).isoformat()
    return await db.todos.put({
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "title": "Test ToDo",
        "description": "A test todo for binding",
        "source": None,
        "source_type": "manual",
        "status": "pending",
        "priority": "none",
        "due_date": None,
        "task_id": None,
        "created_at": now,
        "updated_at": now,
    })


async def _create_task(workspace_id: str, agent_id: str) -> dict:
    """Create a test Task in the given workspace and return its DB row."""
    now = datetime.now(timezone.utc).isoformat()
    return await db.tasks.put({
        "id": f"task_{uuid4().hex[:12]}",
        "agent_id": agent_id,
        "workspace_id": workspace_id,
        "session_id": None,
        "status": "draft",
        "title": "Test Task",
        "description": "A test task for binding",
        "priority": "none",
        "source_todo_id": None,
        "blocked_reason": None,
        "model": "claude-sonnet-4-20250514",
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "error": None,
        "work_dir": None,
    })


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestChatThreadInheritsWorkspaceFromToDo:
    """Property 22: ChatThread workspace binding — ToDo inheritance.

    *For any* ChatThread created with a todo_id, the thread SHALL inherit
    the workspace_id from the linked ToDo, regardless of the workspace_id
    provided in the create request.

    **Validates: Requirements 30.1-30.14**
    """

    @given(title=title_strategy, mode=mode_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_thread_inherits_workspace_from_todo(
        self,
        title: str,
        mode: ChatMode,
    ):
        """ChatThread created with todo_id inherits ToDo's workspace_id.

        **Validates: Requirements 30.7**
        """
        # Create two distinct workspaces
        todo_ws_id = await create_custom_workspace()
        other_ws_id = await create_custom_workspace()
        agent = await _create_agent()
        todo = await _create_todo(todo_ws_id)

        # Create thread with todo_id, but provide a *different* workspace_id
        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=other_ws_id,
                agent_id=agent["id"],
                todo_id=todo["id"],
                mode=mode,
                title=title,
            )
        )

        # Property: workspace_id must equal the ToDo's workspace_id
        assert thread.workspace_id == todo_ws_id, (
            f"Expected workspace_id={todo_ws_id} (from ToDo), "
            f"got {thread.workspace_id}"
        )

        # Verify persisted correctly in the database
        stored = await db.chat_threads.get(thread.id)
        assert stored is not None
        assert stored["workspace_id"] == todo_ws_id


class TestChatThreadInheritsWorkspaceFromTask:
    """Property 22: ChatThread workspace binding — Task inheritance.

    *For any* ChatThread created with a task_id, the thread SHALL inherit
    the workspace_id from the linked Task, regardless of the workspace_id
    provided in the create request.

    **Validates: Requirements 30.1-30.14**
    """

    @given(title=title_strategy, mode=mode_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_thread_inherits_workspace_from_task(
        self,
        title: str,
        mode: ChatMode,
    ):
        """ChatThread created with task_id inherits Task's workspace_id.

        **Validates: Requirements 30.5, 30.8**
        """
        # Create two distinct workspaces
        task_ws_id = await create_custom_workspace()
        other_ws_id = await create_custom_workspace()
        agent = await _create_agent()
        task = await _create_task(task_ws_id, agent["id"])

        # Create thread with task_id, but provide a *different* workspace_id
        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=other_ws_id,
                agent_id=agent["id"],
                task_id=task["id"],
                mode=mode,
                title=title,
            )
        )

        # Property: workspace_id must equal the Task's workspace_id
        assert thread.workspace_id == task_ws_id, (
            f"Expected workspace_id={task_ws_id} (from Task), "
            f"got {thread.workspace_id}"
        )

        # Verify persisted correctly in the database
        stored = await db.chat_threads.get(thread.id)
        assert stored is not None
        assert stored["workspace_id"] == task_ws_id


class TestChatThreadDefaultsToSwarmWS:
    """Property 22: ChatThread workspace binding — default fallback.

    *For any* ChatThread created without a todo_id or task_id and with
    an empty workspace_id, the thread SHALL default to SwarmWS.

    **Validates: Requirements 30.1-30.14**
    """

    @given(title=title_strategy, mode=mode_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_thread_defaults_to_swarmws(
        self,
        title: str,
        mode: ChatMode,
    ):
        """ChatThread without binding defaults to SwarmWS workspace_id.

        **Validates: Requirements 30.5**
        """
        default_ws_id = await ensure_default_workspace()
        agent = await _create_agent()

        # Create thread with no todo_id, no task_id, empty workspace_id
        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id="",
                agent_id=agent["id"],
                mode=mode,
                title=title,
            )
        )

        # Property: workspace_id must equal the default SwarmWS ID
        assert thread.workspace_id == default_ws_id, (
            f"Expected workspace_id={default_ws_id} (SwarmWS default), "
            f"got {thread.workspace_id}"
        )

        # Verify persisted correctly in the database
        stored = await db.chat_threads.get(thread.id)
        assert stored is not None
        assert stored["workspace_id"] == default_ws_id
