"""Unit tests for chat thread project_id schema, queries, and binding.

Tests the ``project_id`` and ``context_version`` columns added to the
``chat_threads`` table as part of the SwarmWS Intelligence cadence (Cadence 4).
Covers:

- Creating threads with and without a ``project_id``
- Default NULL semantics when ``project_id`` is omitted
- ``list_by_project()`` — returns threads WHERE project_id = ?
- ``list_global()`` — returns threads WHERE project_id IS NULL
- ``bind_thread()`` with replace and add modes
- ``increment_context_version()`` returns incremented value
- Safe schema evolution (ALTER TABLE on existing DB)

Requirements: 26.1, 26.4, 26.5, 26.6, 35.1, 37.1
"""
import pytest
import aiosqlite
import tempfile
import os
from uuid import uuid4

from database import db
from database.sqlite import SQLiteDatabase
from tests.helpers import now_iso, create_workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_agent(agent_id: str = None) -> dict:
    """Create a test agent and return its dict."""
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


async def _create_thread(
    workspace_id: str,
    agent_id: str,
    project_id: str = None,
    task_id: str = None,
    todo_id: str = None,
    title: str = "Test Thread",
) -> dict:
    """Create a chat thread with optional project_id and return its dict."""
    now = now_iso()
    thread = {
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "agent_id": agent_id,
        "project_id": project_id,
        "task_id": task_id,
        "todo_id": todo_id,
        "mode": "explore",
        "title": title,
        "created_at": now,
        "updated_at": now,
    }
    return await db.chat_threads.put(thread)


# ---------------------------------------------------------------------------
# Tests: Thread creation with project_id
# ---------------------------------------------------------------------------


class TestChatThreadProjectCreation:
    """Tests for creating threads with and without project_id."""

    @pytest.mark.asyncio
    async def test_create_thread_with_project_id(self):
        """A thread created with a project_id stores it correctly.

        Validates: Requirement 26.5
        """
        ws = await create_workspace()
        agent = await _create_agent()
        project_id = str(uuid4())

        thread = await _create_thread(
            workspace_id=ws["id"],
            agent_id=agent["id"],
            project_id=project_id,
        )

        fetched = await db.chat_threads.get(thread["id"])
        assert fetched is not None
        assert fetched["project_id"] == project_id

    @pytest.mark.asyncio
    async def test_create_global_thread_null_project(self):
        """A thread created without project_id has project_id = NULL.

        Validates: Requirement 26.4, 26.5
        """
        ws = await create_workspace()
        agent = await _create_agent()

        thread = await _create_thread(
            workspace_id=ws["id"],
            agent_id=agent["id"],
            project_id=None,
        )

        fetched = await db.chat_threads.get(thread["id"])
        assert fetched is not None
        assert fetched["project_id"] is None

    @pytest.mark.asyncio
    async def test_project_id_defaults_to_null(self):
        """When project_id is not provided at all, it defaults to NULL.

        Validates: Requirement 26.5
        """
        ws = await create_workspace()
        agent = await _create_agent()
        now = now_iso()

        # Insert without specifying project_id key at all
        thread = {
            "id": str(uuid4()),
            "workspace_id": ws["id"],
            "agent_id": agent["id"],
            "mode": "explore",
            "title": "No project_id key",
            "created_at": now,
            "updated_at": now,
        }
        await db.chat_threads.put(thread)

        fetched = await db.chat_threads.get(thread["id"])
        assert fetched is not None
        assert fetched["project_id"] is None

    @pytest.mark.asyncio
    async def test_context_version_defaults_to_zero(self):
        """New threads start with context_version = 0.

        Validates: Requirement 26.6
        """
        ws = await create_workspace()
        agent = await _create_agent()

        thread = await _create_thread(
            workspace_id=ws["id"],
            agent_id=agent["id"],
        )

        fetched = await db.chat_threads.get(thread["id"])
        assert fetched is not None
        assert fetched["context_version"] == 0


# ---------------------------------------------------------------------------
# Tests: list_by_project and list_global
# ---------------------------------------------------------------------------


class TestChatThreadProjectQueries:
    """Tests for project-scoped and global thread listing."""

    @pytest.mark.asyncio
    async def test_list_by_project(self):
        """list_by_project returns only threads matching the project_id.

        Validates: Requirement 26.1
        """
        ws = await create_workspace()
        agent = await _create_agent()
        project_a = str(uuid4())
        project_b = str(uuid4())

        t1 = await _create_thread(ws["id"], agent["id"], project_id=project_a, title="A-1")
        t2 = await _create_thread(ws["id"], agent["id"], project_id=project_a, title="A-2")
        await _create_thread(ws["id"], agent["id"], project_id=project_b, title="B-1")
        await _create_thread(ws["id"], agent["id"], project_id=None, title="Global")

        results = await db.chat_threads.list_by_project(project_a)
        result_ids = {r["id"] for r in results}

        assert len(results) == 2
        assert t1["id"] in result_ids
        assert t2["id"] in result_ids

    @pytest.mark.asyncio
    async def test_list_by_project_empty(self):
        """list_by_project returns empty list for non-existent project.

        Validates: Requirement 26.1
        """
        results = await db.chat_threads.list_by_project(str(uuid4()))
        assert results == []

    @pytest.mark.asyncio
    async def test_list_global(self):
        """list_global returns only threads with project_id IS NULL.

        Validates: Requirement 26.4
        """
        ws = await create_workspace()
        agent = await _create_agent()
        project_id = str(uuid4())

        await _create_thread(ws["id"], agent["id"], project_id=project_id, title="Project")
        g1 = await _create_thread(ws["id"], agent["id"], project_id=None, title="Global-1")
        g2 = await _create_thread(ws["id"], agent["id"], project_id=None, title="Global-2")

        results = await db.chat_threads.list_global()
        result_ids = {r["id"] for r in results}

        assert len(results) == 2
        assert g1["id"] in result_ids
        assert g2["id"] in result_ids

    @pytest.mark.asyncio
    async def test_list_global_empty(self):
        """list_global returns empty list when all threads have a project.

        Validates: Requirement 26.4
        """
        ws = await create_workspace()
        agent = await _create_agent()

        await _create_thread(ws["id"], agent["id"], project_id=str(uuid4()))

        results = await db.chat_threads.list_global()
        assert results == []


# ---------------------------------------------------------------------------
# Tests: bind_thread
# ---------------------------------------------------------------------------


class TestChatThreadBinding:
    """Tests for mid-session thread binding via bind_thread()."""

    @pytest.mark.asyncio
    async def test_bind_replace_mode(self):
        """bind_thread with mode='replace' overwrites existing task_id/todo_id.

        Validates: Requirement 35.1, 35.2
        """
        ws = await create_workspace()
        agent = await _create_agent()
        thread = await _create_thread(
            ws["id"], agent["id"], task_id="old-task", todo_id="old-todo",
        )

        result = await db.chat_threads.bind_thread(
            thread["id"], task_id="new-task", todo_id="new-todo", mode="replace",
        )

        assert result is not None
        assert result["task_id"] == "new-task"
        assert result["todo_id"] == "new-todo"

    @pytest.mark.asyncio
    async def test_bind_replace_clears_with_none(self):
        """bind_thread with mode='replace' can set fields to None.

        Validates: Requirement 35.2
        """
        ws = await create_workspace()
        agent = await _create_agent()
        thread = await _create_thread(
            ws["id"], agent["id"], task_id="existing-task", todo_id="existing-todo",
        )

        result = await db.chat_threads.bind_thread(
            thread["id"], task_id=None, todo_id=None, mode="replace",
        )

        assert result is not None
        assert result["task_id"] is None
        assert result["todo_id"] is None

    @pytest.mark.asyncio
    async def test_bind_add_mode_fills_nulls(self):
        """bind_thread with mode='add' only sets fields that are currently NULL.

        Validates: Requirement 35.1, 35.3
        """
        ws = await create_workspace()
        agent = await _create_agent()
        # Thread starts with no bindings
        thread = await _create_thread(ws["id"], agent["id"])

        result = await db.chat_threads.bind_thread(
            thread["id"], task_id="task-1", todo_id="todo-1", mode="add",
        )

        assert result is not None
        assert result["task_id"] == "task-1"
        assert result["todo_id"] == "todo-1"

    @pytest.mark.asyncio
    async def test_bind_add_mode_preserves_existing(self):
        """bind_thread with mode='add' preserves existing non-NULL bindings.

        Validates: Requirement 35.3
        """
        ws = await create_workspace()
        agent = await _create_agent()
        thread = await _create_thread(
            ws["id"], agent["id"], task_id="original-task",
        )

        result = await db.chat_threads.bind_thread(
            thread["id"], task_id="new-task", todo_id="new-todo", mode="add",
        )

        assert result is not None
        # task_id should be preserved (was not NULL)
        assert result["task_id"] == "original-task"
        # todo_id should be set (was NULL)
        assert result["todo_id"] == "new-todo"

    @pytest.mark.asyncio
    async def test_bind_increments_context_version(self):
        """bind_thread increments context_version after binding.

        Validates: Requirement 26.6, 35.4
        """
        ws = await create_workspace()
        agent = await _create_agent()
        thread = await _create_thread(ws["id"], agent["id"])

        before = await db.chat_threads.get(thread["id"])
        assert before["context_version"] == 0

        result = await db.chat_threads.bind_thread(
            thread["id"], task_id="task-1", todo_id=None, mode="replace",
        )

        assert result is not None
        assert result["context_version"] == 1

    @pytest.mark.asyncio
    async def test_bind_nonexistent_thread(self):
        """bind_thread returns None for a non-existent thread.

        Validates: Requirement 35.1
        """
        result = await db.chat_threads.bind_thread(
            "nonexistent-id", task_id="task-1", todo_id=None, mode="replace",
        )
        assert result is None


# ---------------------------------------------------------------------------
# Tests: increment_context_version
# ---------------------------------------------------------------------------


class TestIncrementContextVersion:
    """Tests for the increment_context_version method."""

    @pytest.mark.asyncio
    async def test_increment_returns_new_value(self):
        """increment_context_version returns the incremented value.

        Validates: Requirement 26.6
        """
        ws = await create_workspace()
        agent = await _create_agent()
        thread = await _create_thread(ws["id"], agent["id"])

        new_version = await db.chat_threads.increment_context_version(thread["id"])
        assert new_version == 1

    @pytest.mark.asyncio
    async def test_increment_successive_calls(self):
        """Successive calls to increment_context_version produce strictly increasing values.

        Validates: Requirement 26.6
        """
        ws = await create_workspace()
        agent = await _create_agent()
        thread = await _create_thread(ws["id"], agent["id"])

        v1 = await db.chat_threads.increment_context_version(thread["id"])
        v2 = await db.chat_threads.increment_context_version(thread["id"])
        v3 = await db.chat_threads.increment_context_version(thread["id"])

        assert v1 == 1
        assert v2 == 2
        assert v3 == 3

    @pytest.mark.asyncio
    async def test_increment_nonexistent_thread(self):
        """increment_context_version returns -1 for a non-existent thread.

        Validates: Requirement 26.6
        """
        result = await db.chat_threads.increment_context_version("nonexistent-id")
        assert result == -1


# ---------------------------------------------------------------------------
# Tests: Schema evolution safety
# ---------------------------------------------------------------------------


class TestSchemaEvolution:
    """Tests for safe schema evolution (ALTER TABLE on existing DB).

    Validates: Requirement 37.1
    """

    @pytest.mark.asyncio
    async def test_project_id_column_exists(self):
        """The chat_threads table has a project_id column."""
        async with aiosqlite.connect(str(db.db_path)) as conn:
            cursor = await conn.execute("PRAGMA table_info(chat_threads)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

        assert "project_id" in column_names

    @pytest.mark.asyncio
    async def test_context_version_column_exists(self):
        """The chat_threads table has a context_version column."""
        async with aiosqlite.connect(str(db.db_path)) as conn:
            cursor = await conn.execute("PRAGMA table_info(chat_threads)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

        assert "context_version" in column_names

    @pytest.mark.asyncio
    async def test_project_id_index_exists(self):
        """An index on chat_threads.project_id exists."""
        async with aiosqlite.connect(str(db.db_path)) as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='chat_threads'"
            )
            indexes = await cursor.fetchall()
            index_names = [idx[0] for idx in indexes]

        assert "idx_chat_threads_project_id" in index_names

    @pytest.mark.asyncio
    async def test_migration_on_existing_db(self):
        """Schema migration safely adds columns to an existing DB without errors.

        Creates a fresh SQLiteDatabase, initializes it (which runs migrations),
        and verifies the columns exist.

        Validates: Requirement 37.1
        """
        fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="migration_test_")
        os.close(fd)

        try:
            fresh_db = SQLiteDatabase(db_path=tmp_path)
            await fresh_db.initialize()

            # Verify columns exist after initialization
            async with aiosqlite.connect(tmp_path) as conn:
                cursor = await conn.execute("PRAGMA table_info(chat_threads)")
                columns = await cursor.fetchall()
                column_names = [col[1] for col in columns]

            assert "project_id" in column_names
            assert "context_version" in column_names

            # Re-initialize should be idempotent (no errors)
            fresh_db._initialized = False
            await fresh_db.initialize()
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Property-based tests: Chat thread project_id semantics
# ---------------------------------------------------------------------------

from hypothesis import given, settings, HealthCheck
import hypothesis.strategies as st

from core.chat_thread_manager import chat_thread_manager
from schemas.chat_thread import ChatThreadCreate, ChatMode
from tests.helpers import PROPERTY_SETTINGS





# Strategies

_uuid_strategy = st.uuids().map(str)

_title_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=100,
).filter(lambda x: x.strip())

_mode_strategy = st.sampled_from(list(ChatMode))

_optional_project_id_strategy = st.one_of(st.none(), _uuid_strategy)


async def _setup_agent_for_property() -> dict:
    """Create a test agent for property tests."""
    now = now_iso()
    aid = str(uuid4())
    return await db.agents.put({
        "id": aid,
        "name": f"PropAgent-{aid[:8]}",
        "description": "Property test agent",
        "model": "claude-sonnet-4-20250514",
        "permission_mode": "default",
        "is_default": False,
        "created_at": now,
        "updated_at": now,
    })


class TestPropertyChatThreadProjectIdSemantics:
    """Property 6: Chat thread project_id semantics.

    *For any* chat thread record in the database, the ``project_id`` field
    SHALL be either a valid project UUID or NULL. Threads created with a
    project context SHALL have a non-null ``project_id``, and threads
    created without a project context SHALL have ``project_id = NULL``.

    Feature: swarmws-intelligence, Property 6: Chat thread project_id semantics

    **Validates: Requirements 26.4, 26.5**
    """

    @given(
        project_id=_uuid_strategy,
        title=_title_strategy,
        mode=_mode_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_thread_with_project_has_non_null_project_id(
        self,
        project_id: str,
        title: str,
        mode: ChatMode,
    ):
        """Threads created with a project context have non-null project_id
        matching the provided UUID.

        **Validates: Requirements 26.5**
        """
        ws = await create_workspace()
        agent = await _setup_agent_for_property()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                project_id=project_id,
                mode=mode,
                title=title,
            )
        )

        # Property: project_id must be non-null and match the provided UUID
        assert thread.project_id is not None, (
            "Thread created with project context must have non-null project_id"
        )
        assert thread.project_id == project_id, (
            f"Expected project_id={project_id}, got {thread.project_id}"
        )

        # Verify persisted correctly in the database
        stored = await db.chat_threads.get(thread.id)
        assert stored is not None
        assert stored["project_id"] == project_id

    @given(
        title=_title_strategy,
        mode=_mode_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_thread_without_project_has_null_project_id(
        self,
        title: str,
        mode: ChatMode,
    ):
        """Threads created without a project context have project_id = NULL.

        **Validates: Requirements 26.4**
        """
        ws = await create_workspace()
        agent = await _setup_agent_for_property()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                project_id=None,
                mode=mode,
                title=title,
            )
        )

        # Property: project_id must be NULL for global threads
        assert thread.project_id is None, (
            f"Thread created without project context must have project_id=NULL, "
            f"got {thread.project_id}"
        )

        # Verify persisted correctly in the database
        stored = await db.chat_threads.get(thread.id)
        assert stored is not None
        assert stored["project_id"] is None

    @given(
        project_id=_optional_project_id_strategy,
        title=_title_strategy,
        mode=_mode_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_project_id_round_trips_through_db(
        self,
        project_id,
        title: str,
        mode: ChatMode,
    ):
        """For any thread creation (with or without project_id), the stored
        value round-trips correctly through the database.

        **Validates: Requirements 26.4, 26.5**
        """
        ws = await create_workspace()
        agent = await _setup_agent_for_property()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                project_id=project_id,
                mode=mode,
                title=title,
            )
        )

        stored = await db.chat_threads.get(thread.id)
        assert stored is not None

        if project_id is not None:
            # With project context: stored value matches the UUID
            assert stored["project_id"] == project_id
            assert thread.project_id == project_id
        else:
            # Without project context: stored value is NULL
            assert stored["project_id"] is None
            assert thread.project_id is None

    @given(
        project_id=_uuid_strategy,
        title=_title_strategy,
        mode=_mode_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_project_threads_appear_in_list_by_project(
        self,
        project_id: str,
        title: str,
        mode: ChatMode,
    ):
        """Threads with a project_id are returned by list_by_project and
        NOT by list_global_threads.

        **Validates: Requirements 26.4, 26.5**
        """
        ws = await create_workspace()
        agent = await _setup_agent_for_property()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                project_id=project_id,
                mode=mode,
                title=title,
            )
        )

        by_project = await chat_thread_manager.list_threads_by_project(project_id)
        global_threads = await chat_thread_manager.list_global_threads()

        by_project_ids = {t.id for t in by_project}
        global_ids = {t.id for t in global_threads}

        assert thread.id in by_project_ids, (
            "Thread with project_id must appear in list_by_project results"
        )
        assert thread.id not in global_ids, (
            "Thread with project_id must NOT appear in list_global_threads results"
        )

    @given(
        title=_title_strategy,
        mode=_mode_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_global_threads_appear_in_list_global(
        self,
        title: str,
        mode: ChatMode,
    ):
        """Threads without a project_id are returned by list_global_threads
        and NOT by list_by_project for any random project UUID.

        **Validates: Requirements 26.4, 26.5**
        """
        ws = await create_workspace()
        agent = await _setup_agent_for_property()

        thread = await chat_thread_manager.create_thread(
            ChatThreadCreate(
                workspace_id=ws["id"],
                agent_id=agent["id"],
                project_id=None,
                mode=mode,
                title=title,
            )
        )

        global_threads = await chat_thread_manager.list_global_threads()
        # Check against a random project — should never contain this thread
        random_project = str(uuid4())
        by_project = await chat_thread_manager.list_threads_by_project(random_project)

        global_ids = {t.id for t in global_threads}
        by_project_ids = {t.id for t in by_project}

        assert thread.id in global_ids, (
            "Thread without project_id must appear in list_global_threads results"
        )
        assert thread.id not in by_project_ids, (
            "Thread without project_id must NOT appear in list_by_project results"
        )


# ---------------------------------------------------------------------------
# Property-based tests: Thread binding increments version
# ---------------------------------------------------------------------------

_bind_mode_strategy = st.sampled_from(["replace", "add"])

_optional_id_strategy = st.one_of(st.none(), _uuid_strategy)


class TestPropertyThreadBindingIncrementsVersion:
    """Property 12: Thread binding increments version.

    *For any* successful thread binding operation, the thread's
    ``context_version`` SHALL be strictly greater after the operation than
    before.  The bound ``task_id`` and/or ``todo_id`` SHALL reflect the
    binding request according to the specified mode (``replace`` overwrites,
    ``add`` fills NULLs only).

    Feature: swarmws-intelligence, Property 12: Thread binding increments version

    **Validates: Requirements 26.6, 35.4**
    """

    @given(
        bind_task_id=_optional_id_strategy,
        bind_todo_id=_optional_id_strategy,
        mode=_bind_mode_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_binding_increments_context_version(
        self,
        bind_task_id,
        bind_todo_id,
        mode: str,
    ):
        """context_version is strictly greater after any successful bind.

        **Validates: Requirements 26.6, 35.4**
        """
        ws = await create_workspace()
        agent = await _setup_agent_for_property()
        thread = await _create_thread(ws["id"], agent["id"])

        before = await db.chat_threads.get(thread["id"])
        version_before = before["context_version"]

        result = await db.chat_threads.bind_thread(
            thread["id"], task_id=bind_task_id, todo_id=bind_todo_id, mode=mode,
        )

        assert result is not None, "bind_thread must succeed for an existing thread"
        version_after = result["context_version"]

        assert version_after > version_before, (
            f"context_version must strictly increase after binding: "
            f"before={version_before}, after={version_after}"
        )

    @given(
        bind_task_id=_optional_id_strategy,
        bind_todo_id=_optional_id_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_replace_mode_overwrites_bindings(
        self,
        bind_task_id,
        bind_todo_id,
    ):
        """In replace mode, task_id/todo_id are overwritten with the request values.

        **Validates: Requirements 26.6, 35.4**
        """
        ws = await create_workspace()
        agent = await _setup_agent_for_property()
        # Start with pre-existing bindings
        thread = await _create_thread(
            ws["id"], agent["id"],
            task_id="pre-existing-task", todo_id="pre-existing-todo",
        )

        result = await db.chat_threads.bind_thread(
            thread["id"], task_id=bind_task_id, todo_id=bind_todo_id, mode="replace",
        )

        assert result is not None
        assert result["task_id"] == bind_task_id, (
            f"replace mode must overwrite task_id: "
            f"expected={bind_task_id}, got={result['task_id']}"
        )
        assert result["todo_id"] == bind_todo_id, (
            f"replace mode must overwrite todo_id: "
            f"expected={bind_todo_id}, got={result['todo_id']}"
        )

    @given(
        initial_task=_optional_id_strategy,
        initial_todo=_optional_id_strategy,
        add_task=_optional_id_strategy,
        add_todo=_optional_id_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_add_mode_fills_nulls_only(
        self,
        initial_task,
        initial_todo,
        add_task,
        add_todo,
    ):
        """In add mode, only NULL fields are filled; non-NULL fields are preserved.

        **Validates: Requirements 26.6, 35.4**
        """
        ws = await create_workspace()
        agent = await _setup_agent_for_property()
        thread = await _create_thread(
            ws["id"], agent["id"],
            task_id=initial_task, todo_id=initial_todo,
        )

        result = await db.chat_threads.bind_thread(
            thread["id"], task_id=add_task, todo_id=add_todo, mode="add",
        )

        assert result is not None

        # task_id: preserved if initially non-NULL, else set to add_task
        if initial_task is not None:
            assert result["task_id"] == initial_task, (
                f"add mode must preserve existing task_id: "
                f"initial={initial_task}, got={result['task_id']}"
            )
        else:
            assert result["task_id"] == add_task, (
                f"add mode must fill NULL task_id: "
                f"expected={add_task}, got={result['task_id']}"
            )

        # todo_id: preserved if initially non-NULL, else set to add_todo
        if initial_todo is not None:
            assert result["todo_id"] == initial_todo, (
                f"add mode must preserve existing todo_id: "
                f"initial={initial_todo}, got={result['todo_id']}"
            )
        else:
            assert result["todo_id"] == add_todo, (
                f"add mode must fill NULL todo_id: "
                f"expected={add_todo}, got={result['todo_id']}"
            )

    @given(
        num_binds=st.integers(min_value=2, max_value=5),
        mode=_bind_mode_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_successive_bindings_strictly_increase_version(
        self,
        num_binds: int,
        mode: str,
    ):
        """Multiple successive bindings produce strictly increasing context_version.

        **Validates: Requirements 26.6, 35.4**
        """
        ws = await create_workspace()
        agent = await _setup_agent_for_property()
        thread = await _create_thread(ws["id"], agent["id"])

        versions = []
        for i in range(num_binds):
            result = await db.chat_threads.bind_thread(
                thread["id"],
                task_id=f"task-{i}",
                todo_id=f"todo-{i}",
                mode=mode,
            )
            assert result is not None
            versions.append(result["context_version"])

        # All versions must be strictly increasing
        for i in range(1, len(versions)):
            assert versions[i] > versions[i - 1], (
                f"Versions must be strictly increasing: {versions}"
            )

