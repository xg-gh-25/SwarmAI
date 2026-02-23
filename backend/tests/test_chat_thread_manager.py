"""Unit tests for ChatThreadManager.

Tests CRUD operations for ChatThreads, ChatMessages, and ThreadSummaries,
including workspace binding, workspace_id inheritance from ToDo/Task,
and default workspace assignment.

Requirements: 30.1-30.14
"""
import pytest
from uuid import uuid4

from database import db
from core.chat_thread_manager import chat_thread_manager
from schemas.chat_thread import (
    ChatMode,
    ChatMessageCreate,
    ChatThreadCreate,
    ChatThreadUpdate,
    MessageRole,
    SummaryType,
    ThreadSummaryCreate,
    ThreadSummaryUpdate,
)
from tests.helpers import now_iso, create_workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_agent(agent_id: str = None) -> dict:
    now = now_iso()
    aid = agent_id or str(uuid4())
    agent = {
        "id": aid,
        "name": f"Agent-{aid[:8]}",
        "description": "Test agent",
        "model": "claude-sonnet-4-20250514",
        "permission_mode": "default",
        "is_default": False,
        "created_at": now,
        "updated_at": now,
    }
    return await db.agents.put(agent)


async def _create_todo(workspace_id: str) -> dict:
    now = now_iso()
    todo = {
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "title": "Test ToDo",
        "description": "A test todo",
        "source": None,
        "source_type": "manual",
        "status": "pending",
        "priority": "none",
        "due_date": None,
        "task_id": None,
        "created_at": now,
        "updated_at": now,
    }
    return await db.todos.put(todo)


async def _create_task(workspace_id: str, agent_id: str) -> dict:
    now = now_iso()
    task = {
        "id": f"task_{uuid4().hex[:12]}",
        "agent_id": agent_id,
        "workspace_id": workspace_id,
        "session_id": None,
        "status": "draft",
        "title": "Test Task",
        "description": "A test task",
        "priority": "none",
        "source_todo_id": None,
        "blocked_reason": None,
        "model": "claude-sonnet-4-20250514",
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "error": None,
        "work_dir": None,
    }
    return await db.tasks.put(task)


# ---------------------------------------------------------------------------
# Tests: ChatThread Create
# ---------------------------------------------------------------------------


class TestChatThreadCreate:
    """Tests for ChatThreadManager.create_thread()."""

    @pytest.mark.asyncio
    async def test_create_basic(self):
        """Create a thread with minimal required fields."""
        ws = await create_workspace()
        agent = await _create_agent()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Test Thread",
            )
        )

        assert thread.id is not None
        assert thread.workspace_id == ws["id"]
        assert thread.agent_id == agent["id"]
        assert thread.mode == ChatMode.EXPLORE
        assert thread.title == "Test Thread"
        assert thread.task_id is None
        assert thread.todo_id is None

    @pytest.mark.asyncio
    async def test_create_execute_mode(self):
        """Create a thread in execute mode."""
        ws = await create_workspace()
        agent = await _create_agent()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Execute Thread",
                mode=ChatMode.EXECUTE,
            )
        )

        assert thread.mode == ChatMode.EXECUTE

    @pytest.mark.asyncio
    async def test_create_with_task_binding(self):
        """Create a thread bound to a Task."""
        ws = await create_workspace()
        agent = await _create_agent()
        task = await _create_task(ws["id"], agent["id"])

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                task_id=task["id"],
                title="Task Thread",
                mode=ChatMode.EXECUTE,
            )
        )

        assert thread.task_id == task["id"]
        assert thread.workspace_id == ws["id"]

    @pytest.mark.asyncio
    async def test_create_with_todo_binding(self):
        """Create a thread bound to a ToDo."""
        ws = await create_workspace()
        agent = await _create_agent()
        todo = await _create_todo(ws["id"])

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                todo_id=todo["id"],
                title="ToDo Thread",
            )
        )

        assert thread.todo_id == todo["id"]
        assert thread.workspace_id == ws["id"]

    @pytest.mark.asyncio
    async def test_create_inherits_workspace_from_todo(self):
        """Thread inherits workspace_id from linked ToDo.

        Validates: Requirement 30.7
        """
        ws_a = await create_workspace("WorkspaceA")
        ws_b = await create_workspace("WorkspaceB")
        agent = await _create_agent()
        todo = await _create_todo(ws_a["id"])

        # Provide ws_b as workspace_id, but todo belongs to ws_a
        # The thread should inherit ws_a from the todo
        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws_b["id"],
                agent_id=agent["id"],
                todo_id=todo["id"],
                title="Inherited WS Thread",
            )
        )

        assert thread.workspace_id == ws_a["id"]

    @pytest.mark.asyncio
    async def test_create_inherits_workspace_from_task(self):
        """Thread inherits workspace_id from linked Task."""
        ws_a = await create_workspace("WorkspaceA")
        ws_b = await create_workspace("WorkspaceB")
        agent = await _create_agent()
        task = await _create_task(ws_a["id"], agent["id"])

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws_b["id"],
                agent_id=agent["id"],
                task_id=task["id"],
                title="Inherited WS Thread",
            )
        )

        assert thread.workspace_id == ws_a["id"]

    @pytest.mark.asyncio
    async def test_create_defaults_to_swarmws(self):
        """Thread defaults to SwarmWS when no workspace_id provided.

        Validates: Requirement 30.5
        """
        swarm_ws = await create_workspace("SwarmWS", is_default=True)
        agent = await _create_agent()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id="",
                agent_id=agent["id"],
                title="Default WS Thread",
            )
        )

        assert thread.workspace_id == swarm_ws["id"]


# ---------------------------------------------------------------------------
# Tests: ChatThread Get
# ---------------------------------------------------------------------------


class TestChatThreadGet:
    """Tests for ChatThreadManager.get_thread()."""

    @pytest.mark.asyncio
    async def test_get_existing(self):
        ws = await create_workspace()
        agent = await _create_agent()

        created = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Get Test",
            )
        )

        fetched = await chat_thread_manager.get_thread(created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.title == "Get Test"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self):
        result = await chat_thread_manager.get_thread("nonexistent-id")
        assert result is None


# ---------------------------------------------------------------------------
# Tests: ChatThread List
# ---------------------------------------------------------------------------


class TestChatThreadList:
    """Tests for ChatThreadManager.list_threads()."""

    @pytest.mark.asyncio
    async def test_list_by_workspace(self):
        ws = await create_workspace()
        agent = await _create_agent()

        for i in range(3):
            await chat_thread_manager.create_thread(
                ChatThreadCreate(
                    workspace_id=ws["id"],
                    agent_id=agent["id"],
                    title=f"Thread {i}",
                )
            )

        threads = await chat_thread_manager.list_threads(workspace_id=ws["id"])
        assert len(threads) == 3

    @pytest.mark.asyncio
    async def test_list_by_task(self):
        ws = await create_workspace()
        agent = await _create_agent()
        task = await _create_task(ws["id"], agent["id"])

        await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                task_id=task["id"],
                title="Task Thread",
            )
        )
        await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Other Thread",
            )
        )

        threads = await chat_thread_manager.list_threads(task_id=task["id"])
        assert len(threads) == 1
        assert threads[0].task_id == task["id"]

    @pytest.mark.asyncio
    async def test_list_by_todo(self):
        ws = await create_workspace()
        agent = await _create_agent()
        todo = await _create_todo(ws["id"])

        await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                todo_id=todo["id"],
                title="ToDo Thread",
            )
        )

        threads = await chat_thread_manager.list_threads(todo_id=todo["id"])
        assert len(threads) == 1
        assert threads[0].todo_id == todo["id"]

    @pytest.mark.asyncio
    async def test_list_pagination(self):
        ws = await create_workspace()
        agent = await _create_agent()

        for i in range(5):
            await chat_thread_manager.create_thread(
                ChatThreadCreate(
                    workspace_id=ws["id"],
                    agent_id=agent["id"],
                    title=f"Thread {i}",
                )
            )

        page1 = await chat_thread_manager.list_threads(
            workspace_id=ws["id"], limit=2, offset=0
        )
        page2 = await chat_thread_manager.list_threads(
            workspace_id=ws["id"], limit=2, offset=2
        )

        assert len(page1) == 2
        assert len(page2) == 2

    @pytest.mark.asyncio
    async def test_list_isolates_workspaces(self):
        ws_a = await create_workspace("WS_A")
        ws_b = await create_workspace("WS_B")
        agent = await _create_agent()

        await chat_thread_manager.create_thread(
            ChatThreadCreate(workspace_id=ws_a["id"], agent_id=agent["id"], title="A")
        )
        await chat_thread_manager.create_thread(
            ChatThreadCreate(workspace_id=ws_b["id"], agent_id=agent["id"], title="B")
        )

        threads_a = await chat_thread_manager.list_threads(workspace_id=ws_a["id"])
        threads_b = await chat_thread_manager.list_threads(workspace_id=ws_b["id"])

        assert len(threads_a) == 1
        assert len(threads_b) == 1
        assert threads_a[0].title == "A"
        assert threads_b[0].title == "B"


# ---------------------------------------------------------------------------
# Tests: ChatThread Update
# ---------------------------------------------------------------------------


class TestChatThreadUpdate:
    """Tests for ChatThreadManager.update_thread()."""

    @pytest.mark.asyncio
    async def test_update_title(self):
        ws = await create_workspace()
        agent = await _create_agent()

        created = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Original",
            )
        )

        updated = await chat_thread_manager.update_thread(
            created.id, ChatThreadUpdate(title="Updated Title")
        )

        assert updated is not None
        assert updated.title == "Updated Title"

    @pytest.mark.asyncio
    async def test_update_mode(self):
        ws = await create_workspace()
        agent = await _create_agent()

        created = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Mode Test",
                mode=ChatMode.EXPLORE,
            )
        )

        updated = await chat_thread_manager.update_thread(
            created.id, ChatThreadUpdate(mode=ChatMode.EXECUTE)
        )

        assert updated.mode == ChatMode.EXECUTE

    @pytest.mark.asyncio
    async def test_update_promote_to_task(self):
        """Promote a chat to a Task by setting task_id.

        Validates: Requirement 30.6
        """
        ws = await create_workspace()
        agent = await _create_agent()
        task = await _create_task(ws["id"], agent["id"])

        created = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Promote Test",
            )
        )

        updated = await chat_thread_manager.update_thread(
            created.id, ChatThreadUpdate(task_id=task["id"])
        )

        assert updated.task_id == task["id"]

    @pytest.mark.asyncio
    async def test_update_nonexistent(self):
        result = await chat_thread_manager.update_thread(
            "nonexistent-id", ChatThreadUpdate(title="Nope")
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_update_no_changes(self):
        ws = await create_workspace()
        agent = await _create_agent()

        created = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="No Change",
            )
        )

        updated = await chat_thread_manager.update_thread(
            created.id, ChatThreadUpdate()
        )

        assert updated is not None
        assert updated.title == "No Change"


# ---------------------------------------------------------------------------
# Tests: ChatThread Delete
# ---------------------------------------------------------------------------


class TestChatThreadDelete:
    """Tests for ChatThreadManager.delete_thread()."""

    @pytest.mark.asyncio
    async def test_delete_existing(self):
        ws = await create_workspace()
        agent = await _create_agent()

        created = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Delete Me",
            )
        )

        result = await chat_thread_manager.delete_thread(created.id)
        assert result is True

        fetched = await chat_thread_manager.get_thread(created.id)
        assert fetched is None

    @pytest.mark.asyncio
    async def test_delete_cascades_messages_and_summaries(self):
        """Deleting a thread also deletes its messages and summaries."""
        ws = await create_workspace()
        agent = await _create_agent()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Cascade Test",
            )
        )

        # Add messages
        await chat_thread_manager.add_message(
            ChatMessageCreate(
                thread_id=thread.id,
                role=MessageRole.USER,
                content="Hello",
            )
        )

        # Add summary
        await chat_thread_manager.create_summary(
            ThreadSummaryCreate(
                thread_id=thread.id,
                summary_text="Test summary",
            )
        )

        # Delete thread
        await chat_thread_manager.delete_thread(thread.id)

        # Verify cascade
        messages = await chat_thread_manager.list_messages(thread.id)
        assert len(messages) == 0

        summary = await chat_thread_manager.get_summary(thread.id)
        assert summary is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self):
        result = await chat_thread_manager.delete_thread("nonexistent-id")
        assert result is False


# ---------------------------------------------------------------------------
# Tests: ChatMessage
# ---------------------------------------------------------------------------


class TestChatMessage:
    """Tests for ChatThreadManager message operations."""

    @pytest.mark.asyncio
    async def test_add_message(self):
        ws = await create_workspace()
        agent = await _create_agent()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Message Test",
            )
        )

        msg = await chat_thread_manager.add_message(
            ChatMessageCreate(
                thread_id=thread.id,
                role=MessageRole.USER,
                content="Hello, world!",
            )
        )

        assert msg.id is not None
        assert msg.thread_id == thread.id
        assert msg.role == MessageRole.USER
        assert msg.content == "Hello, world!"
        assert msg.tool_calls is None

    @pytest.mark.asyncio
    async def test_add_message_with_tool_calls(self):
        ws = await create_workspace()
        agent = await _create_agent()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Tool Calls Test",
            )
        )

        tool_calls_json = '[{"name": "search", "args": {"q": "test"}}]'
        msg = await chat_thread_manager.add_message(
            ChatMessageCreate(
                thread_id=thread.id,
                role=MessageRole.ASSISTANT,
                content="Let me search for that.",
                tool_calls=tool_calls_json,
            )
        )

        assert msg.tool_calls == tool_calls_json

    @pytest.mark.asyncio
    async def test_add_message_updates_thread_timestamp(self):
        ws = await create_workspace()
        agent = await _create_agent()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Timestamp Test",
            )
        )

        original_updated = thread.updated_at

        # Small delay to ensure timestamp difference
        import asyncio
        await asyncio.sleep(0.05)

        await chat_thread_manager.add_message(
            ChatMessageCreate(
                thread_id=thread.id,
                role=MessageRole.USER,
                content="Update timestamp",
            )
        )

        refreshed = await chat_thread_manager.get_thread(thread.id)
        assert refreshed.updated_at >= original_updated

    @pytest.mark.asyncio
    async def test_add_message_to_nonexistent_thread(self):
        with pytest.raises(ValueError, match="not found"):
            await chat_thread_manager.add_message(
                ChatMessageCreate(
                    thread_id="nonexistent-thread",
                    role=MessageRole.USER,
                    content="Should fail",
                )
            )

    @pytest.mark.asyncio
    async def test_list_messages_ordered(self):
        ws = await create_workspace()
        agent = await _create_agent()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Order Test",
            )
        )

        roles = [MessageRole.USER, MessageRole.ASSISTANT, MessageRole.USER]
        contents = ["First", "Second", "Third"]

        for role, content in zip(roles, contents):
            await chat_thread_manager.add_message(
                ChatMessageCreate(
                    thread_id=thread.id,
                    role=role,
                    content=content,
                )
            )

        messages = await chat_thread_manager.list_messages(thread.id)
        assert len(messages) == 3
        assert messages[0].content == "First"
        assert messages[1].content == "Second"
        assert messages[2].content == "Third"

    @pytest.mark.asyncio
    async def test_list_messages_pagination(self):
        ws = await create_workspace()
        agent = await _create_agent()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Pagination Test",
            )
        )

        for i in range(5):
            await chat_thread_manager.add_message(
                ChatMessageCreate(
                    thread_id=thread.id,
                    role=MessageRole.USER,
                    content=f"Message {i}",
                )
            )

        page1 = await chat_thread_manager.list_messages(thread.id, limit=2, offset=0)
        page2 = await chat_thread_manager.list_messages(thread.id, limit=2, offset=2)

        assert len(page1) == 2
        assert len(page2) == 2

    @pytest.mark.asyncio
    async def test_all_message_roles(self):
        """Test all four message roles: user, assistant, tool, system."""
        ws = await create_workspace()
        agent = await _create_agent()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Roles Test",
            )
        )

        for role in MessageRole:
            msg = await chat_thread_manager.add_message(
                ChatMessageCreate(
                    thread_id=thread.id,
                    role=role,
                    content=f"Message from {role.value}",
                )
            )
            assert msg.role == role

        messages = await chat_thread_manager.list_messages(thread.id)
        assert len(messages) == 4


# ---------------------------------------------------------------------------
# Tests: ThreadSummary
# ---------------------------------------------------------------------------


class TestThreadSummary:
    """Tests for ChatThreadManager summary operations."""

    @pytest.mark.asyncio
    async def test_create_summary(self):
        ws = await create_workspace()
        agent = await _create_agent()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Summary Test",
            )
        )

        summary = await chat_thread_manager.create_summary(
            ThreadSummaryCreate(
                thread_id=thread.id,
                summary_text="This thread discussed project planning.",
            )
        )

        assert summary.id is not None
        assert summary.thread_id == thread.id
        assert summary.summary_type == SummaryType.ROLLING
        assert summary.summary_text == "This thread discussed project planning."
        assert summary.key_decisions is None
        assert summary.open_questions is None

    @pytest.mark.asyncio
    async def test_create_summary_with_decisions_and_questions(self):
        ws = await create_workspace()
        agent = await _create_agent()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Full Summary Test",
            )
        )

        summary = await chat_thread_manager.create_summary(
            ThreadSummaryCreate(
                thread_id=thread.id,
                summary_type=SummaryType.FINAL,
                summary_text="Final summary of the discussion.",
                key_decisions=["Use React", "Deploy to AWS"],
                open_questions=["What about testing?"],
            )
        )

        assert summary.summary_type == SummaryType.FINAL
        assert summary.key_decisions == ["Use React", "Deploy to AWS"]
        assert summary.open_questions == ["What about testing?"]

    @pytest.mark.asyncio
    async def test_create_summary_for_nonexistent_thread(self):
        with pytest.raises(ValueError, match="not found"):
            await chat_thread_manager.create_summary(
                ThreadSummaryCreate(
                    thread_id="nonexistent-thread",
                    summary_text="Should fail",
                )
            )

    @pytest.mark.asyncio
    async def test_get_summary(self):
        ws = await create_workspace()
        agent = await _create_agent()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Get Summary Test",
            )
        )

        await chat_thread_manager.create_summary(
            ThreadSummaryCreate(
                thread_id=thread.id,
                summary_text="Test summary content.",
            )
        )

        fetched = await chat_thread_manager.get_summary(thread.id)
        assert fetched is not None
        assert fetched.summary_text == "Test summary content."

    @pytest.mark.asyncio
    async def test_get_summary_nonexistent(self):
        result = await chat_thread_manager.get_summary("nonexistent-thread")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_summary(self):
        ws = await create_workspace()
        agent = await _create_agent()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Update Summary Test",
            )
        )

        created = await chat_thread_manager.create_summary(
            ThreadSummaryCreate(
                thread_id=thread.id,
                summary_text="Original summary.",
            )
        )

        updated = await chat_thread_manager.update_summary(
            created.id,
            ThreadSummaryUpdate(
                summary_text="Updated summary.",
                key_decisions=["Decision A"],
            ),
        )

        assert updated is not None
        assert updated.summary_text == "Updated summary."
        assert updated.key_decisions == ["Decision A"]

    @pytest.mark.asyncio
    async def test_update_summary_type(self):
        ws = await create_workspace()
        agent = await _create_agent()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Type Update Test",
            )
        )

        created = await chat_thread_manager.create_summary(
            ThreadSummaryCreate(
                thread_id=thread.id,
                summary_type=SummaryType.ROLLING,
                summary_text="Rolling summary.",
            )
        )

        updated = await chat_thread_manager.update_summary(
            created.id,
            ThreadSummaryUpdate(summary_type=SummaryType.FINAL),
        )

        assert updated.summary_type == SummaryType.FINAL

    @pytest.mark.asyncio
    async def test_update_summary_nonexistent(self):
        result = await chat_thread_manager.update_summary(
            "nonexistent-id",
            ThreadSummaryUpdate(summary_text="Nope"),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_summary(self):
        ws = await create_workspace()
        agent = await _create_agent()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                title="Delete Summary Test",
            )
        )

        await chat_thread_manager.create_summary(
            ThreadSummaryCreate(
                thread_id=thread.id,
                summary_text="To be deleted.",
            )
        )

        result = await chat_thread_manager.delete_summary(thread.id)
        assert result is True

        fetched = await chat_thread_manager.get_summary(thread.id)
        assert fetched is None

    @pytest.mark.asyncio
    async def test_delete_summary_nonexistent(self):
        result = await chat_thread_manager.delete_summary("nonexistent-thread")
        assert result is False
