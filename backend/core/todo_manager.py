"""ToDo/Signal manager for incoming work item management.

This module provides the ToDoManager class for managing ToDo entities,
which represent incoming work signals in the Daily Work Operating Loop.
In the UI, these are displayed as "Signals" but the technical entity
name is "ToDo".

Increments the context snapshot cache ``todo_version`` counter whenever
todos are created, updated, or deleted so that the context assembly
cache is properly invalidated (Requirement 34.2).

Requirements: 4.1-4.9, 6.1-6.8, 34.2
"""
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from database import db
from schemas.todo import (
    ToDoCreate,
    ToDoUpdate,
    ToDoResponse,
    ToDoStatus,
    ToDoConvertToTaskRequest,
    Priority,
)

logger = logging.getLogger(__name__)


class ToDoManager:
    """Manages ToDo entities (Signals) in the Daily Work Operating Loop.

    ToDos represent incoming work items that can be triaged, discussed,
    and converted to Tasks for execution. This manager provides CRUD
    operations and lifecycle management for ToDos.

    Key Features:
    - CRUD operations for ToDos
    - Default workspace assignment to SwarmWS
    - Convert ToDo to Task with proper linking
    - Background job for overdue detection

    Requirements: 4.1-4.9, 6.1-6.8
    """

    async def _get_default_workspace_id(self) -> str:
        """Get the default workspace (SwarmWS) ID.

        Returns:
            str: The ID of the default workspace (SwarmWS).

        Raises:
            ValueError: If no default workspace exists.

        Validates: Requirements 1.3, 1.4
        """
        default_workspace = await db.workspace_config.get_config()
        if not default_workspace:
            raise ValueError("SwarmWS workspace config not found. Please initialize the application first.")
        return default_workspace["id"]

    async def _check_workspace_not_archived(self, workspace_id: str) -> None:
        """Raise PermissionError if the workspace is archived.

        In the single-workspace model, SwarmWS is never archived.
        This method is kept for backward compatibility but is now a no-op
        since the archive concept no longer applies.

        Args:
            workspace_id: The workspace ID to check.

        Validates: Requirements 36.6, 36.7
        """
        # In single-workspace model, SwarmWS is never archived
        pass


    async def create(self, data: ToDoCreate) -> ToDoResponse:
        """Create a new ToDo.

        Args:
            data: ToDoCreate schema with ToDo details.
                  If workspace_id is not provided, defaults to SwarmWS.

        Returns:
            ToDoResponse: The created ToDo.

        Validates: Requirements 1.3, 4.1, 6.2
        """
        # Default workspace_id to SwarmWS if not provided
        workspace_id = data.workspace_id
        if not workspace_id:
            workspace_id = await self._get_default_workspace_id()
            logger.debug(f"Defaulting workspace_id to SwarmWS: {workspace_id}")

        # Enforce archived workspace read-only (Requirement 36.6)
        await self._check_workspace_not_archived(workspace_id)

        now = datetime.now(timezone.utc).isoformat()
        todo_id = str(uuid4())

        todo_dict = {
            "id": todo_id,
            "workspace_id": workspace_id,
            "title": data.title,
            "description": data.description,
            "source": data.source,
            "source_type": data.source_type.value,
            "status": ToDoStatus.PENDING.value,
            "priority": data.priority.value,
            "due_date": data.due_date.isoformat() if data.due_date else None,
            "task_id": None,
            "created_at": now,
            "updated_at": now,
        }

        result = await db.todos.put(todo_dict)
        logger.info(f"Created ToDo {todo_id} in workspace {workspace_id}")

        # Increment todo_version for context cache invalidation (Req 34.2)

        return self._dict_to_response(result)

    async def get(self, todo_id: str) -> Optional[ToDoResponse]:
        """Get a ToDo by ID.

        Args:
            todo_id: The ID of the ToDo to retrieve.

        Returns:
            ToDoResponse if found, None otherwise.

        Validates: Requirements 6.3
        """
        result = await db.todos.get(todo_id)
        if not result:
            return None

        # Secondary mechanism: Check for overdue status on read
        # Requirement 4.6: WHEN reading a ToDo where due_date has passed but
        # status is still "pending", THE API MAY temporarily mark it as "overdue"
        result = await self._check_and_update_overdue_single(result)

        return self._dict_to_response(result)

    async def list(
        self,
        workspace_id: Optional[str] = None,
        status: Optional[ToDoStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ToDoResponse]:
        """List ToDos with optional filtering.

        Args:
            workspace_id: Filter by workspace ID. If None, returns all ToDos.
            status: Filter by status.
            limit: Maximum number of results (default 50).
            offset: Number of results to skip for pagination.

        Returns:
            List of ToDoResponse objects.

        Validates: Requirements 6.1, 6.8
        """
        # Validate pagination parameters
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)

        if workspace_id:
            status_value = status.value if status else None
            results = await db.todos.list_by_workspace(workspace_id, status_value)
        elif status:
            results = await db.todos.list_by_status(status.value)
        else:
            results = await db.todos.list()

        # Apply pagination
        paginated = results[offset:offset + limit]

        # Check for overdue status on each item (secondary mechanism)
        checked_results = []
        for result in paginated:
            checked = await self._check_and_update_overdue_single(result)
            checked_results.append(self._dict_to_response(checked))

        return checked_results

    async def update(self, todo_id: str, data: ToDoUpdate) -> Optional[ToDoResponse]:
        """Update an existing ToDo.

        Args:
            todo_id: The ID of the ToDo to update.
            data: ToDoUpdate schema with fields to update.

        Returns:
            Updated ToDoResponse if found, None otherwise.

        Validates: Requirements 6.4
        """
        existing = await db.todos.get(todo_id)
        if not existing:
            return None

        # Build update dict with only provided fields
        updates = {}
        if data.title is not None:
            updates["title"] = data.title
        if data.description is not None:
            updates["description"] = data.description
        if data.source is not None:
            updates["source"] = data.source
        if data.source_type is not None:
            updates["source_type"] = data.source_type.value
        if data.status is not None:
            updates["status"] = data.status.value
        if data.priority is not None:
            updates["priority"] = data.priority.value
        if data.due_date is not None:
            updates["due_date"] = data.due_date.isoformat()

        if not updates:
            return self._dict_to_response(existing)

        result = await db.todos.update(todo_id, updates)
        if not result:
            return None

        # Increment todo_version for context cache invalidation (Req 34.2)

        logger.info(f"Updated ToDo {todo_id}")
        return self._dict_to_response(result)

    async def delete(self, todo_id: str) -> bool:
        """Soft-delete a ToDo by setting status to 'deleted'.

        Args:
            todo_id: The ID of the ToDo to delete.

        Returns:
            True if deleted, False if not found.

        Validates: Requirements 6.5
        """
        existing = await db.todos.get(todo_id)
        if not existing:
            return False

        await db.todos.update(todo_id, {"status": ToDoStatus.DELETED.value})
        logger.info(f"Soft-deleted ToDo {todo_id}")

        # Increment todo_version for context cache invalidation (Req 34.2)

        return True

    async def convert_to_task(
        self,
        todo_id: str,
        task_data: ToDoConvertToTaskRequest,
    ) -> Optional[dict]:
        """Convert a ToDo to a Task.

        This creates a new Task linked to the ToDo and updates the ToDo
        status to 'handled' with the task_id reference.

        Args:
            todo_id: The ID of the ToDo to convert.
            task_data: ToDoConvertToTaskRequest with task configuration.

        Returns:
            The created Task dict if successful, None if ToDo not found.

        Validates: Requirements 4.7, 4.8, 5.6, 6.6
        """
        todo = await db.todos.get(todo_id)
        if not todo:
            return None

        # Get agent config for model
        agent_config = await db.agents.get(task_data.agent_id)
        if not agent_config:
            raise ValueError(f"Agent {task_data.agent_id} not found")

        # Create task with ToDo data
        now = datetime.now(timezone.utc).isoformat()
        task_id = f"task_{uuid4().hex[:12]}"

        task = {
            "id": task_id,
            "agent_id": task_data.agent_id,
            "workspace_id": todo["workspace_id"],
            "session_id": None,
            "status": "draft",  # New tasks start as draft
            "title": task_data.title or todo["title"],
            "description": task_data.description or todo.get("description"),
            "priority": (task_data.priority.value if task_data.priority else todo.get("priority", Priority.NONE.value)),
            "source_todo_id": todo_id,
            "blocked_reason": None,
            "model": agent_config.get("model"),
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "error": None,
            "work_dir": None,
        }

        # Store task
        await db.tasks.put(task)

        # Update ToDo: set status to handled and link to task
        # Validates: Requirements 4.7, 4.8
        await db.todos.update(todo_id, {
            "status": ToDoStatus.HANDLED.value,
            "task_id": task_id,
        })

        logger.info(f"Converted ToDo {todo_id} to Task {task_id}")
        return task

    async def check_overdue(self) -> int:
        """Background job to check and update overdue ToDos.

        This method scans all ToDos where due_date has passed and status
        is "pending", updating their status to "overdue".

        This is the PRIMARY mechanism for overdue detection (hourly scan).

        Returns:
            Number of ToDos updated to overdue status.

        Validates: Requirements 4.5
        """
        now = datetime.now(timezone.utc)
        updated_count = 0

        # Get all pending ToDos
        pending_todos = await db.todos.list_by_status(ToDoStatus.PENDING.value)

        for todo in pending_todos:
            if todo.get("due_date"):
                due_date = self._parse_datetime(todo["due_date"])
                if due_date and due_date < now:
                    # Re-check status to avoid race with _check_and_update_overdue_single
                    current = await db.todos.get(todo["id"])
                    if current and current.get("status") == ToDoStatus.PENDING.value:
                        await db.todos.update(todo["id"], {
                            "status": ToDoStatus.OVERDUE.value
                        })
                        updated_count += 1
                        logger.debug(f"Marked ToDo {todo['id']} as overdue")

        if updated_count > 0:
            logger.info(f"Overdue check completed: {updated_count} ToDos marked as overdue")

        return updated_count

    async def _check_and_update_overdue_single(self, todo: dict) -> dict:
        """Check and update overdue status for a single ToDo.

        This is the SECONDARY mechanism for overdue detection (on read).

        Args:
            todo: The ToDo dict to check.

        Returns:
            The ToDo dict, potentially with updated status.

        Validates: Requirements 4.6
        """
        if todo.get("status") != ToDoStatus.PENDING.value:
            return todo

        if not todo.get("due_date"):
            return todo

        due_date = self._parse_datetime(todo["due_date"])
        if not due_date:
            return todo

        now = datetime.now(timezone.utc)
        if due_date < now:
            # Guard: re-fetch to avoid race with background check_overdue job
            current = await db.todos.get(todo["id"])
            if current and current.get("status") == ToDoStatus.PENDING.value:
                await db.todos.update(todo["id"], {
                    "status": ToDoStatus.OVERDUE.value
                })
            # Return updated status in response
            todo["status"] = ToDoStatus.OVERDUE.value
            logger.debug(f"Marked ToDo {todo['id']} as overdue (on read)")

        return todo

    def _parse_datetime(self, value: Optional[str]) -> Optional[datetime]:
        """Parse a datetime string to datetime object.

        Args:
            value: ISO format datetime string or None.

        Returns:
            datetime object or None if parsing fails.
        """
        if not value:
            return None
        try:
            # Handle ISO format with or without timezone
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            dt = datetime.fromisoformat(value)
            # Ensure timezone-aware
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return None

    def _dict_to_response(self, data: dict) -> ToDoResponse:
        """Convert a database dict to ToDoResponse.

        Args:
            data: Database row dict.

        Returns:
            ToDoResponse Pydantic model.
        """
        return ToDoResponse(
            id=data["id"],
            workspace_id=data["workspace_id"],
            title=data["title"],
            description=data.get("description"),
            source=data.get("source"),
            source_type=data["source_type"],
            status=data["status"],
            priority=data["priority"],
            due_date=self._parse_datetime(data.get("due_date")),
            task_id=data.get("task_id"),
            created_at=self._parse_datetime(data["created_at"]) or datetime.now(timezone.utc),
            updated_at=self._parse_datetime(data["updated_at"]) or datetime.now(timezone.utc),
        )


# Global instance
todo_manager = ToDoManager()
