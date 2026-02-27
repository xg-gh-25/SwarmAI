"""Task schemas for background agent task management.

This module defines the Pydantic models for Task entities used in the
background agent execution system.  Key public symbols:

- ``TaskStatus``       — Enum of valid task statuses (draft, wip, blocked,
                         completed, cancelled).  Legacy values (pending,
                         running, failed) are mapped at the TaskManager layer.
- ``TaskCreate``       — Request model for creating a new task.
- ``TaskResponse``     — Response model returned by all task endpoints.
                         Includes ``review_required`` (bool, default False) and
                         ``review_risk_level`` (Optional[str], default None) for
                         future risk-assessment support (Spec 4, Req 6.2/6.3).
- ``TaskMessageRequest`` — Request model for sending messages to running tasks.
- ``RunningTaskCount`` — Response model for the running-task count endpoint.
"""
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class TaskStatus(str, Enum):
    """Task execution status.

    These are the canonical status values used throughout the system.
    Legacy values (pending, running, failed) stored in older DB rows are
    transparently mapped by ``TaskManager._map_legacy_status()``:
      pending → draft, running → wip, failed → blocked.
    """
    DRAFT = "draft"
    WIP = "wip"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskCreate(BaseModel):
    """Request to create a new task."""
    agent_id: str
    message: Optional[str] = None
    content: Optional[list[dict]] = None
    enable_skills: bool = False
    enable_mcp: bool = False
    workspace_id: Optional[str] = None
    source_todo_id: Optional[str] = None
    priority: str = "none"
    description: Optional[str] = None
    required_skills: Optional[list[str]] = None
    required_mcps: Optional[list[str]] = None


class TaskResponse(BaseModel):
    """Task response model.

    Includes all fields stored by ``TaskManager.create_task`` and returned
    by ``TaskManager.get_task`` / ``list_tasks``.  Fields added during the
    workspace-refactor (description, priority, workspace_id, source_todo_id,
    blocked_reason) have defaults so that older DB rows without these columns
    still deserialize correctly.
    """
    id: str
    agent_id: str
    session_id: Optional[str] = None
    status: TaskStatus
    title: str
    description: Optional[str] = None
    priority: Optional[str] = "none"
    workspace_id: Optional[str] = None
    source_todo_id: Optional[str] = None
    blocked_reason: Optional[str] = None
    model: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    work_dir: Optional[str] = None
    review_required: bool = False
    review_risk_level: Optional[str] = None


class TaskMessageRequest(BaseModel):
    """Request to send a message to a running task."""
    message: Optional[str] = None
    content: Optional[list[dict]] = None


class RunningTaskCount(BaseModel):
    """Response for running task count."""
    count: int
