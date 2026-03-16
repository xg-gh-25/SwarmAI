"""Tasks API endpoints for background agent task management."""
import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

from schemas.task import TaskCreate, TaskResponse, TaskMessageRequest, RunningTaskCount
from core.task_manager import task_manager
from core.skill_manager import skill_manager
from database import db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    status: Optional[str] = Query(None, description="Filter by status (comma-separated, OR semantics)"),
    agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
    workspace_id: Optional[str] = Query(None, description="Filter by workspace ID"),
    completed_after: Optional[str] = Query(None, description="Filter completed tasks after ISO 8601 date"),
):
    """List all tasks, optionally filtered.

    Status filter uses comma-separated values with OR semantics:
    ``?status=wip,draft,blocked`` means (status=wip OR status=draft OR status=blocked).
    Different parameter types use AND semantics:
    ``?status=completed&workspace_id=abc`` means (status=completed AND workspace_id=abc).
    """
    tasks = await task_manager.list_tasks(
        status=status,
        agent_id=agent_id,
        workspace_id=workspace_id,
        completed_after=completed_after,
    )
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
    """Create and start a new background task.

    Validates workspace policy before execution. If required skills or MCPs
    are disabled in the workspace, returns 409 Conflict with policy_violations.

    Requirements: 26.1-26.7, 34.1-34.7
    """
    # --- Policy enforcement: check required_skills / required_mcps ---
    if request.required_skills or request.required_mcps:
        violations = []
        ws_id = request.workspace_id

        # Resolve default workspace if not provided
        if not ws_id:
            config = await db.workspace_config.get_config()
            ws_id = config["id"] if config else None

        if ws_id:
            # Check required skills — in filesystem model all skills are always
            # enabled, so we only verify the skill exists in the cache.
            skill_cache = await skill_manager.get_cache()
            for skill_id in (request.required_skills or []):
                if skill_id not in skill_cache:
                    violations.append({
                        "entity_type": "skill",
                        "entity_id": skill_id,
                        "message": f"Skill {skill_id} not found in workspace {ws_id}",
                        "suggestedAction": f"Install skill {skill_id} or check the name",
                    })

            # Check required MCPs
            for mcp_id in (request.required_mcps or []):
                row = await db.workspace_mcps.get_by_workspace_and_mcp(ws_id, mcp_id)
                if not row or not row.get("enabled", 1):
                    violations.append({
                        "entity_type": "mcp",
                        "entity_id": mcp_id,
                        "message": f"MCP {mcp_id} is not enabled in workspace {ws_id}",
                        "suggestedAction": f"Enable MCP {mcp_id} in workspace settings",
                    })

        if violations:
            return JSONResponse(
                status_code=409,
                content={
                    "code": "POLICY_VIOLATION",
                    "message": "Required capabilities are not enabled in the workspace",
                    "policy_violations": violations,
                    "suggested_action": "Enable the required capabilities in workspace settings",
                },
            )

    try:
        task = await task_manager.create_task(
            agent_id=request.agent_id,
            message=request.message,
            content=request.content,
            enable_skills=request.enable_skills,
            enable_mcp=request.enable_mcp,
            workspace_id=request.workspace_id,
            source_todo_id=request.source_todo_id,
            priority=request.priority,
            description=request.description,
        )
        return TaskResponse(**task)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
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
            logger.info("SSE stream cancelled for task %s", task_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
