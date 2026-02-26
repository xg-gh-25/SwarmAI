"""ChatThread manager for conversation thread management.

This module provides the ``ChatThreadManager`` class for managing ChatThread,
ChatMessage, and ThreadSummary entities.  ChatThreads bind conversations
to the Agent → Task/ToDo → Workspace relationship, enabling properly
scoped and retrievable chat context.

ChatThreads, ChatMessages, and ThreadSummaries are DB-canonical
(stored in database, not filesystem).

Key public symbols:

- ``ChatThreadManager``       — Main manager class (singleton at module level)
- ``chat_thread_manager``     — Global instance for import convenience

Manager capabilities:

- ``create_thread``           — Create thread with optional ``project_id``
- ``list_threads_by_project`` — List threads scoped to a project UUID
- ``list_global_threads``     — List threads with ``project_id IS NULL``
- ``bind_thread``             — Mid-session task/todo binding with cross-project guardrail
- ``add_message``             — Add message and increment ``context_version``

Requirements: 26.1, 26.4, 26.5, 30.1-30.14, 35.1-35.6, 38.3
"""
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4

from database import db
from schemas.chat_thread import (
    ChatMode,
    ChatMessageCreate,
    ChatMessageResponse,
    ChatThreadCreate,
    ChatThreadResponse,
    ChatThreadUpdate,
    MessageRole,
    SummaryType,
    ThreadSummaryCreate,
    ThreadSummaryResponse,
    ThreadSummaryUpdate,
)

logger = logging.getLogger(__name__)


class ChatThreadManager:
    """Manages ChatThread, ChatMessage, and ThreadSummary entities.

    ChatThreads represent conversation threads bound to a workspace and
    optionally to a Task or ToDo. They support explore (lightweight) and
    execute (structured) modes.

    Key Features:
    - CRUD operations for ChatThreads
    - Message storage in ChatMessages table
    - Thread summary generation/update in ThreadSummaries table
    - Workspace binding with inheritance from ToDo/Task
    - Default workspace assignment to SwarmWS
    - Project-scoped thread listing (``list_threads_by_project``)
    - Global (unassociated) thread listing (``list_global_threads``)
    - Mid-session thread binding with cross-project guardrail (``bind_thread``)

    Requirements: 26.1, 26.4, 26.5, 30.1-30.14, 35.1-35.6, 38.3
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_default_workspace_id(self) -> str:
        """Get the default workspace (SwarmWS) ID.

        Returns:
            str: The ID of the default workspace (SwarmWS).

        Raises:
            ValueError: If no default workspace exists.
        """
        default_workspace = await db.workspace_config.get_config()
        if not default_workspace:
            raise ValueError(
                "SwarmWS workspace config not found. "
                "Please initialize the application first."
            )
        return default_workspace["id"]

    def _parse_datetime(self, value: Optional[str]) -> Optional[datetime]:
        """Parse a datetime string to datetime object."""
        if not value:
            return None
        try:
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return None

    def _thread_to_response(self, data: dict) -> ChatThreadResponse:
        """Convert a database dict to ChatThreadResponse."""
        return ChatThreadResponse(
            id=data["id"],
            workspace_id=data["workspace_id"],
            agent_id=data["agent_id"],
            project_id=data.get("project_id"),
            task_id=data.get("task_id"),
            todo_id=data.get("todo_id"),
            mode=data["mode"],
            title=data["title"],
            context_version=data.get("context_version", 0),
            created_at=self._parse_datetime(data["created_at"]) or datetime.now(timezone.utc),
            updated_at=self._parse_datetime(data["updated_at"]) or datetime.now(timezone.utc),
        )

    def _message_to_response(self, data: dict) -> ChatMessageResponse:
        """Convert a database dict to ChatMessageResponse."""
        return ChatMessageResponse(
            id=data["id"],
            thread_id=data["thread_id"],
            role=data["role"],
            content=data["content"],
            tool_calls=data.get("tool_calls"),
            created_at=self._parse_datetime(data["created_at"]) or datetime.now(timezone.utc),
        )

    def _summary_to_response(self, data: dict) -> ThreadSummaryResponse:
        """Convert a database dict to ThreadSummaryResponse."""
        key_decisions = data.get("key_decisions")
        if isinstance(key_decisions, str):
            try:
                key_decisions = json.loads(key_decisions)
            except (json.JSONDecodeError, TypeError):
                key_decisions = None

        open_questions = data.get("open_questions")
        if isinstance(open_questions, str):
            try:
                open_questions = json.loads(open_questions)
            except (json.JSONDecodeError, TypeError):
                open_questions = None

        return ThreadSummaryResponse(
            id=data["id"],
            thread_id=data["thread_id"],
            summary_type=data["summary_type"],
            summary_text=data["summary_text"],
            key_decisions=key_decisions,
            open_questions=open_questions,
            updated_at=self._parse_datetime(data["updated_at"]) or datetime.now(timezone.utc),
        )

    async def _resolve_workspace_id(self, data: ChatThreadCreate) -> str:
        """Resolve the workspace_id for a new ChatThread.

        Inherits workspace_id from the linked ToDo or Task when applicable.
        Falls back to the provided workspace_id, then to SwarmWS default.

        Args:
            data: ChatThreadCreate with optional workspace_id, todo_id, task_id.

        Returns:
            The resolved workspace_id.

        Validates: Requirements 30.5, 30.7
        """
        # If a todo_id is provided, inherit workspace_id from the ToDo
        # Requirement 30.7: inherit workspace_id from ToDo
        if data.todo_id:
            todo = await db.todos.get(data.todo_id)
            if todo and todo.get("workspace_id"):
                return todo["workspace_id"]

        # If a task_id is provided, inherit workspace_id from the Task
        if data.task_id:
            task = await db.tasks.get(data.task_id)
            if task and task.get("workspace_id"):
                return task["workspace_id"]

        # Use the explicitly provided workspace_id
        if data.workspace_id:
            return data.workspace_id

        # Fall back to SwarmWS default
        return await self._get_default_workspace_id()

    # ------------------------------------------------------------------
    # ChatThread CRUD
    # ------------------------------------------------------------------

    async def create_thread(self, data: ChatThreadCreate) -> ChatThreadResponse:
        """Create a new ChatThread with workspace binding.

        When a todo_id or task_id is provided, the workspace_id is inherited
        from that entity. Otherwise uses the provided workspace_id or defaults
        to SwarmWS.

        Threads created from a project context store the project's UUID in
        ``project_id``.  Threads created outside a project context store
        ``project_id = NULL``.

        Args:
            data: ChatThreadCreate schema with thread details.

        Returns:
            ChatThreadResponse: The created ChatThread.

        Validates: Requirements 26.1, 26.5, 30.1, 30.5, 30.7
        """
        workspace_id = await self._resolve_workspace_id(data)

        now = datetime.now(timezone.utc).isoformat()
        thread_id = str(uuid4())

        thread_dict = {
            "id": thread_id,
            "workspace_id": workspace_id,
            "agent_id": data.agent_id,
            "project_id": data.project_id,
            "task_id": data.task_id,
            "todo_id": data.todo_id,
            "mode": data.mode.value,
            "title": data.title,
            "context_version": 0,
            "created_at": now,
            "updated_at": now,
        }

        result = await db.chat_threads.put(thread_dict)
        logger.info(
            "Created ChatThread %s in workspace %s (project=%s, mode=%s)",
            thread_id, workspace_id, data.project_id, data.mode.value,
        )
        return self._thread_to_response(result)

    async def get_thread(self, thread_id: str) -> Optional[ChatThreadResponse]:
        """Get a ChatThread by ID.

        Args:
            thread_id: The ID of the ChatThread to retrieve.

        Returns:
            ChatThreadResponse if found, None otherwise.
        """
        result = await db.chat_threads.get(thread_id)
        if not result:
            return None
        return self._thread_to_response(result)

    async def list_threads(
        self,
        workspace_id: Optional[str] = None,
        task_id: Optional[str] = None,
        todo_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ChatThreadResponse]:
        """List ChatThreads with optional filtering.

        Args:
            workspace_id: Filter by workspace ID.
            task_id: Filter by task ID.
            todo_id: Filter by todo ID.
            limit: Maximum number of results (default 50).
            offset: Number of results to skip for pagination.

        Returns:
            List of ChatThreadResponse objects.

        Validates: Requirements 30.11
        """
        if task_id:
            results = await db.chat_threads.list_by_task(task_id)
        elif todo_id:
            results = await db.chat_threads.list_by_todo(todo_id)
        elif workspace_id:
            results = await db.chat_threads.list_by_workspace(workspace_id)
        else:
            results = await db.chat_threads.list()

        paginated = results[offset:offset + limit]
        return [self._thread_to_response(r) for r in paginated]

    async def list_threads_by_project(
        self, project_id: str
    ) -> List[ChatThreadResponse]:
        """List all ChatThreads associated with a specific project.

        Delegates to ``db.chat_threads.list_by_project()`` which queries
        threads WHERE ``project_id = ?``.

        Args:
            project_id: The project UUID to filter by.

        Returns:
            List of ChatThreadResponse objects ordered by updated_at DESC.

        Validates: Requirements 26.1
        """
        results = await db.chat_threads.list_by_project(project_id)
        return [self._thread_to_response(r) for r in results]

    async def list_global_threads(self) -> List[ChatThreadResponse]:
        """List all ChatThreads not associated with any project.

        Returns threads where ``project_id IS NULL``, representing global
        SwarmWS chats not bound to a specific project.

        Returns:
            List of ChatThreadResponse objects ordered by updated_at DESC.

        Validates: Requirements 26.4
        """
        results = await db.chat_threads.list_global()
        return [self._thread_to_response(r) for r in results]

    async def bind_thread(
        self,
        thread_id: str,
        task_id: Optional[str] = None,
        todo_id: Optional[str] = None,
        mode: str = "replace",
        force: bool = False,
    ) -> dict:
        """Bind or rebind a thread to a task/todo mid-session.

        Implements the cross-project guardrail: if ``force`` is False and
        the task's project differs from the thread's project, returns an
        error dict with a 409-style conflict message.

        Args:
            thread_id: The thread to bind.
            task_id: Task ID to bind (or None).
            todo_id: ToDo ID to bind (or None).
            mode: ``'replace'`` overwrites existing bindings;
                  ``'add'`` only sets fields that are currently NULL.
            force: If True, bypass the cross-project guardrail.

        Returns:
            On success: dict with ``thread_id``, ``task_id``, ``todo_id``,
            ``context_version``.
            On conflict: dict with ``error`` key and ``status`` = 409.

        Validates: Requirements 35.1, 35.2, 35.3, 35.4, 35.5, 35.6, 38.3
        """
        # Fetch the thread to check existence and project_id
        thread = await db.chat_threads.get(thread_id)
        if not thread:
            logger.warning("Bind attempt on non-existent thread %s", thread_id)
            return {"error": f"Thread {thread_id} not found", "status": 404}

        thread_project_id = thread.get("project_id")

        # Cross-project guardrail (PE Enhancement C)
        if not force and task_id:
            task = await db.tasks.get(task_id)
            if task:
                # Tasks use workspace_id; check if the thread's project
                # context differs from the task's workspace context.
                # If the thread has a project_id, we compare it against
                # the task's workspace_id as a proxy for project ownership.
                task_workspace_id = task.get("workspace_id")
                if (
                    thread_project_id is not None
                    and task_workspace_id is not None
                    and thread_project_id != task_workspace_id
                ):
                    logger.debug(
                        "Cross-project binding blocked: thread=%s "
                        "thread_project=%s task_workspace=%s mode=%s",
                        thread_id, thread_project_id, task_workspace_id, mode,
                    )
                    return {
                        "error": (
                            f"Task belongs to workspace '{task_workspace_id}'. "
                            f"Thread is associated with project '{thread_project_id}'. "
                            "Binding cross-project tasks may cause context confusion. "
                            "Re-send with force=true to override."
                        ),
                        "status": 409,
                    }

        # Delegate to DB layer
        updated = await db.chat_threads.bind_thread(
            thread_id, task_id, todo_id, mode
        )
        if not updated:
            logger.warning("Bind failed — thread %s not found in DB", thread_id)
            return {"error": f"Thread {thread_id} not found", "status": 404}

        context_version = updated.get("context_version", 0)

        logger.debug(
            "Binding change: thread=%s task=%s todo=%s mode=%s "
            "context_version=%d",
            thread_id, task_id, todo_id, mode, context_version,
        )

        return {
            "thread_id": thread_id,
            "task_id": updated.get("task_id"),
            "todo_id": updated.get("todo_id"),
            "context_version": context_version,
        }

    async def update_thread(
        self, thread_id: str, data: ChatThreadUpdate
    ) -> Optional[ChatThreadResponse]:
        """Update an existing ChatThread.

        Args:
            thread_id: The ID of the ChatThread to update.
            data: ChatThreadUpdate schema with fields to update.

        Returns:
            Updated ChatThreadResponse if found, None otherwise.

        Validates: Requirements 30.6
        """
        existing = await db.chat_threads.get(thread_id)
        if not existing:
            return None

        updates = {}
        if data.task_id is not None:
            updates["task_id"] = data.task_id
        if data.todo_id is not None:
            updates["todo_id"] = data.todo_id
        if data.mode is not None:
            updates["mode"] = data.mode.value
        if data.title is not None:
            updates["title"] = data.title

        if not updates:
            return self._thread_to_response(existing)

        result = await db.chat_threads.update(thread_id, updates)
        if not result:
            return None

        logger.info(f"Updated ChatThread {thread_id}")
        return self._thread_to_response(result)

    async def delete_thread(self, thread_id: str) -> bool:
        """Delete a ChatThread and its associated messages and summaries.

        Args:
            thread_id: The ID of the ChatThread to delete.

        Returns:
            True if deleted, False if not found.

        Validates: Requirements 30.13
        """
        existing = await db.chat_threads.get(thread_id)
        if not existing:
            return False

        # Delete associated messages and summaries first (cascade)
        await db.chat_messages.delete_by_thread(thread_id)
        await db.thread_summaries.delete_by_thread(thread_id)
        await db.chat_threads.delete(thread_id)

        logger.info(f"Deleted ChatThread {thread_id} with messages and summaries")
        return True

    # ------------------------------------------------------------------
    # ChatMessage operations
    # ------------------------------------------------------------------

    async def add_message(self, data: ChatMessageCreate) -> ChatMessageResponse:
        """Add a message to a ChatThread.

        Also updates the thread's updated_at timestamp and increments
        the thread's ``context_version`` counter for cache invalidation.

        Args:
            data: ChatMessageCreate schema with message details.

        Returns:
            ChatMessageResponse: The created message.

        Raises:
            ValueError: If the thread does not exist.

        Validates: Requirements 30.3, 30.4, 34.2
        """
        thread = await db.chat_threads.get(data.thread_id)
        if not thread:
            raise ValueError(f"ChatThread {data.thread_id} not found")

        now = datetime.now(timezone.utc).isoformat()
        message_id = str(uuid4())

        message_dict = {
            "id": message_id,
            "thread_id": data.thread_id,
            "role": data.role.value,
            "content": data.content,
            "tool_calls": data.tool_calls,
            "created_at": now,
        }

        result = await db.chat_messages.put(message_dict)

        # Update thread's updated_at
        await db.chat_threads.update(data.thread_id, {"updated_at": now})

        # Increment thread_version for context cache invalidation (Req 34.2)
        await db.chat_threads.increment_context_version(data.thread_id)

        logger.debug(
            f"Added {data.role.value} message to thread {data.thread_id}"
        )
        return self._message_to_response(result)

    async def list_messages(
        self,
        thread_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> List[ChatMessageResponse]:
        """List messages for a ChatThread, ordered by creation time.

        Args:
            thread_id: The ID of the ChatThread.
            limit: Maximum number of results (default 100).
            offset: Number of results to skip for pagination.

        Returns:
            List of ChatMessageResponse objects ordered by created_at ASC.

        Validates: Requirements 30.3
        """
        results = await db.chat_messages.list_by_thread(thread_id)
        paginated = results[offset:offset + limit]
        return [self._message_to_response(r) for r in paginated]

    # ------------------------------------------------------------------
    # ThreadSummary operations
    # ------------------------------------------------------------------

    async def create_summary(
        self, data: ThreadSummaryCreate
    ) -> ThreadSummaryResponse:
        """Create a new ThreadSummary for a ChatThread.

        Args:
            data: ThreadSummaryCreate schema with summary details.

        Returns:
            ThreadSummaryResponse: The created summary.

        Raises:
            ValueError: If the thread does not exist.

        Validates: Requirements 30.9, 30.10
        """
        thread = await db.chat_threads.get(data.thread_id)
        if not thread:
            raise ValueError(f"ChatThread {data.thread_id} not found")

        now = datetime.now(timezone.utc).isoformat()
        summary_id = str(uuid4())

        summary_dict = {
            "id": summary_id,
            "thread_id": data.thread_id,
            "summary_type": data.summary_type.value,
            "summary_text": data.summary_text,
            "key_decisions": json.dumps(data.key_decisions) if data.key_decisions else None,
            "open_questions": json.dumps(data.open_questions) if data.open_questions else None,
            "updated_at": now,
        }

        result = await db.thread_summaries.put(summary_dict)
        logger.info(
            f"Created ThreadSummary {summary_id} for thread {data.thread_id} "
            f"(type={data.summary_type.value})"
        )
        return self._summary_to_response(result)

    async def get_summary(
        self, thread_id: str
    ) -> Optional[ThreadSummaryResponse]:
        """Get the latest ThreadSummary for a ChatThread.

        Args:
            thread_id: The ID of the ChatThread.

        Returns:
            ThreadSummaryResponse if found, None otherwise.

        Validates: Requirements 30.9
        """
        result = await db.thread_summaries.get_by_thread(thread_id)
        if not result:
            return None
        return self._summary_to_response(result)

    async def update_summary(
        self, summary_id: str, data: ThreadSummaryUpdate
    ) -> Optional[ThreadSummaryResponse]:
        """Update an existing ThreadSummary.

        Args:
            summary_id: The ID of the ThreadSummary to update.
            data: ThreadSummaryUpdate schema with fields to update.

        Returns:
            Updated ThreadSummaryResponse if found, None otherwise.

        Validates: Requirements 30.9
        """
        existing = await db.thread_summaries.get(summary_id)
        if not existing:
            return None

        updates = {}
        if data.summary_type is not None:
            updates["summary_type"] = data.summary_type.value
        if data.summary_text is not None:
            updates["summary_text"] = data.summary_text
        if data.key_decisions is not None:
            updates["key_decisions"] = json.dumps(data.key_decisions)
        if data.open_questions is not None:
            updates["open_questions"] = json.dumps(data.open_questions)

        if not updates:
            return self._summary_to_response(existing)

        result = await db.thread_summaries.update(summary_id, updates)
        if not result:
            return None

        logger.info(f"Updated ThreadSummary {summary_id}")
        return self._summary_to_response(result)

    async def delete_summary(self, thread_id: str) -> bool:
        """Delete all ThreadSummaries for a ChatThread.

        Args:
            thread_id: The ID of the ChatThread.

        Returns:
            True if any summaries were deleted, False otherwise.
        """
        count = await db.thread_summaries.delete_by_thread(thread_id)
        if count > 0:
            logger.info(f"Deleted {count} summaries for thread {thread_id}")
        return count > 0


# Global instance
chat_thread_manager = ChatThreadManager()
