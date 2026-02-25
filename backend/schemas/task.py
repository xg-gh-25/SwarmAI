"""Task schemas for background agent task management."""
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class TaskStatus(str, Enum):
    """Task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskCreate(BaseModel):
    """Request to create a new task."""
    agent_id: str
    message: Optional[str] = None
    content: Optional[list[dict]] = None
    enable_skills: bool = False
    enable_mcp: bool = False
    add_dirs: Optional[list[str]] = None


class TaskResponse(BaseModel):
    """Task response model."""
    id: str
    agent_id: str
    session_id: Optional[str] = None
    status: TaskStatus
    title: str
    model: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    work_dir: Optional[str] = None


class TaskMessageRequest(BaseModel):
    """Request to send a message to a running task."""
    message: Optional[str] = None
    content: Optional[list[dict]] = None


class RunningTaskCount(BaseModel):
    """Response for running task count."""
    count: int
