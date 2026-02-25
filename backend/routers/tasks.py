"""Tasks API endpoints for background agent task management."""
import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from schemas.task import TaskCreate, TaskResponse, TaskMessageRequest, RunningTaskCount
from core.task_manager import task_manager
from database import db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    status: Optional[str] = Query(None, description="Filter by status"),
    agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
):
    """List all tasks, optionally filtered by status or agent_id."""
    tasks = await task_manager.list_tasks(status=status, agent_id=agent_id)
    return [TaskResponse(**task) for task in tasks]


@router.get("/running/count", response_model=RunningTaskCount)
async def get_running_count():
    """Get count of running tasks (for sidebar badge)."""
    count = await task_manager.get_running_count()
    return RunningTaskCount(count=count)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str):
    """Get a specific task by ID."""
    task = await task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return TaskResponse(**task)


@router.post("", response_model=TaskResponse)
async def create_task(request: TaskCreate):
    """Create and start a new background task."""
    try:
        task = await task_manager.create_task(
            agent_id=request.agent_id,
            message=request.message,
            content=request.content,
            enable_skills=request.enable_skills,
            enable_mcp=request.enable_mcp,
            add_dirs=request.add_dirs,
        )
        return TaskResponse(**task)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{task_id}")
async def delete_task(task_id: str):
    """Delete a task (cancels if running)."""
    deleted = await task_manager.delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return {"status": "deleted", "task_id": task_id}


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str):
    """Cancel a running task."""
    cancelled = await task_manager.cancel_task(task_id)
    if not cancelled:
        raise HTTPException(status_code=400, detail=f"Task {task_id} is not running")
    return {"status": "cancelled", "task_id": task_id}


@router.post("/{task_id}/message")
async def send_message(task_id: str, request: TaskMessageRequest):
    """Send a message to a running task."""
    success = await task_manager.send_message(
        task_id=task_id,
        message=request.message,
        content=request.content,
    )
    if not success:
        raise HTTPException(status_code=400, detail=f"Task {task_id} is not running or not accepting messages")
    return {"status": "sent", "task_id": task_id}


@router.get("/{task_id}/stream")
async def stream_task(task_id: str):
    """Subscribe to task events via SSE."""
    task = await task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    async def event_generator():
        try:
            async for event in task_manager.subscribe(task_id):
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:
            logger.info(f"SSE stream cancelled for task {task_id}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
