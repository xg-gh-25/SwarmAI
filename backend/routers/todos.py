"""ToDo/Signal API endpoints for incoming work item management.

This module provides CRUD endpoints for ToDo entities (displayed as "Signals"
in the UI). ToDos represent incoming work items in the Daily Work Operating Loop.

Includes lifecycle management endpoints:
- Quick-action ``/mark-handled`` and ``/mark-cancelled`` for frontend buttons
- Session binding ``/bind-todo`` for drag-to-chat auto-completion

Requirements: 6.1-6.8
"""
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from schemas.todo import (
    ToDoCreate,
    ToDoUpdate,
    ToDoResponse,
    ToDoStatus,
    ToDoConvertToTaskRequest,
)
from core.todo_manager import todo_manager
from database import db  # Still needed for session metadata update in bind-session

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=list[ToDoResponse])
async def list_todos(
    workspace_id: Optional[str] = Query(None, description="Filter by workspace ID"),
    status: Optional[ToDoStatus] = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=200, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
):
    """List all ToDos with optional filtering and pagination.

    Requirement 6.1: GET /api/todos with workspace_id and status filters.
    Requirement 6.8: Support pagination with limit/offset parameters.
    """
    todos = await todo_manager.list(
        workspace_id=workspace_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return todos


@router.post("", response_model=ToDoResponse, status_code=201)
async def create_todo(data: ToDoCreate):
    """Create a new ToDo.

    Requirement 6.2: POST /api/todos to create a new ToDo.
    """
    try:
        todo = await todo_manager.create(data)
        return todo
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{todo_id}", response_model=ToDoResponse)
async def get_todo(todo_id: str):
    """Get a specific ToDo by ID.

    Requirement 6.3: GET /api/todos/{id} to retrieve a specific ToDo.
    """
    todo = await todo_manager.get(todo_id)
    if not todo:
        raise HTTPException(status_code=404, detail=f"ToDo {todo_id} not found")
    return todo


@router.put("/{todo_id}", response_model=ToDoResponse)
async def update_todo(todo_id: str, data: ToDoUpdate):
    """Update an existing ToDo.

    Requirement 6.4: PUT /api/todos/{id} to update a ToDo.
    """
    todo = await todo_manager.update(todo_id, data)
    if not todo:
        raise HTTPException(status_code=404, detail=f"ToDo {todo_id} not found")
    return todo


@router.delete("/{todo_id}")
async def delete_todo(todo_id: str):
    """Soft-delete a ToDo by setting status to 'deleted'.

    Requirement 6.5: DELETE /api/todos/{id} to soft-delete a ToDo.
    """
    deleted = await todo_manager.delete(todo_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"ToDo {todo_id} not found")
    return {"status": "deleted", "todo_id": todo_id}


@router.post("/{todo_id}/convert-to-task")
async def convert_todo_to_task(todo_id: str, data: ToDoConvertToTaskRequest):
    """Convert a ToDo to a Task.

    Creates a new Task linked to the ToDo and updates the ToDo status
    to 'handled' with the task_id reference.

    Requirement 6.6: POST /api/todos/{id}/convert-to-task.
    """
    try:
        task = await todo_manager.convert_to_task(todo_id, data)
        if not task:
            raise HTTPException(status_code=404, detail=f"ToDo {todo_id} not found")
        return task
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Lifecycle quick-actions (frontend buttons)
# ---------------------------------------------------------------------------

@router.post("/{todo_id}/mark-handled")
async def mark_todo_handled(todo_id: str):
    """Mark a ToDo as handled (completed).

    Used by frontend action buttons in the Radar sidebar.
    """
    transitioned = await todo_manager.transition_status(
        todo_id, "handled", source="manual",
    )
    if transitioned is None:
        raise HTTPException(status_code=404, detail=f"ToDo {todo_id} not found")
    return {"status": "handled", "todo_id": todo_id}


@router.post("/{todo_id}/mark-cancelled")
async def mark_todo_cancelled(todo_id: str):
    """Mark a ToDo as cancelled (dismissed).

    Used by frontend action buttons in the Radar sidebar.
    """
    transitioned = await todo_manager.transition_status(
        todo_id, "cancelled", source="manual",
    )
    if transitioned is None:
        raise HTTPException(status_code=404, detail=f"ToDo {todo_id} not found")
    return {"status": "cancelled", "todo_id": todo_id}


# ---------------------------------------------------------------------------
# Session ↔ ToDo binding (drag-to-chat)
# ---------------------------------------------------------------------------

class BindTodoRequest(BaseModel):
    """Request body for binding a todo to a session."""
    todo_id: str


@router.post("/bind-session/{session_id}")
async def bind_todo_to_session(session_id: str, request: BindTodoRequest):
    """Bind a ToDo to a chat session.

    Called when user drags a todo into a chat tab.  Stores the todo_id
    in the session's metadata JSON so the post-session TodoLifecycleHook
    can auto-complete it.  Also transitions the todo to ``in_discussion``.
    """
    # Validate todo exists
    todo = await todo_manager.get(request.todo_id)
    if not todo:
        raise HTTPException(status_code=404, detail=f"ToDo {request.todo_id} not found")

    # Validate session exists
    session = await db.sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    # Update session metadata with todo_id
    metadata = session.get("metadata") or "{}"
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            metadata = {}

    metadata["todo_id"] = request.todo_id
    await db.sessions.update(session_id, {
        "metadata": json.dumps(metadata),
        "updated_at": datetime.now().isoformat(),
    })

    # Transition todo to in_discussion if pending
    transitioned = await todo_manager.transition_status(
        request.todo_id, "in_discussion", source="drag_to_chat",
    )
    new_status = "in_discussion" if transitioned else todo.status
    logger.info(
        "ToDo %s bound to session %s (status=%s)",
        request.todo_id[:8], session_id[:8], new_status,
    )

    return {
        "session_id": session_id,
        "todo_id": request.todo_id,
        "todo_status": new_status,
    }
