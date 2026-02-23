"""PlanItem schemas for the Plan section of the Daily Work Operating Loop.

This module defines the Pydantic models for PlanItem entities, which represent
prioritized work items in the Plan section. PlanItems can be workspace-scoped
(local) or SwarmWS-scoped (global/cross-domain).

Requirements: 22.1, 22.2, 22.3, 22.4
"""
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

from .todo import Priority


class PlanItemStatus(str, Enum):
    """PlanItem status values.
    
    Requirement 22.2: THE System SHALL support PlanItem status values:
    active, deferred, completed, cancelled.
    """
    ACTIVE = "active"
    DEFERRED = "deferred"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class FocusType(str, Enum):
    """PlanItem focus type values for categorization.
    
    Requirement 22.3: THE System SHALL support PlanItem focus_type values:
    today, upcoming, blocked.
    """
    TODAY = "today"
    UPCOMING = "upcoming"
    BLOCKED = "blocked"


class PlanItemCreate(BaseModel):
    """Request model for creating a new PlanItem.
    
    Requirement 22.1: THE System SHALL store PlanItem entities in the database
    (DB-canonical) with fields: id, workspace_id, title, description,
    source_todo_id, source_task_id, status, priority, scheduled_date,
    focus_type, sort_order, created_at, updated_at.
    """
    workspace_id: str = Field(..., description="ID of the workspace this PlanItem belongs to")
    title: str = Field(..., min_length=1, max_length=500, description="Title of the PlanItem")
    description: Optional[str] = Field(None, max_length=10000, description="Detailed description")
    source_todo_id: Optional[str] = Field(None, description="ID of the source ToDo if created from a Signal")
    source_task_id: Optional[str] = Field(None, description="ID of the linked Task for execution tracking")
    status: PlanItemStatus = Field(
        default=PlanItemStatus.ACTIVE,
        description="Current status of the PlanItem"
    )
    priority: Priority = Field(
        default=Priority.NONE,
        description="Priority level of the PlanItem"
    )
    scheduled_date: Optional[datetime] = Field(None, description="Scheduled date for the PlanItem")
    focus_type: FocusType = Field(
        default=FocusType.UPCOMING,
        description="Focus category for the PlanItem"
    )
    sort_order: int = Field(
        default=0,
        ge=0,
        description="Sort order within the focus_type category"
    )


class PlanItemUpdate(BaseModel):
    """Request model for updating an existing PlanItem.
    
    All fields are optional - only provided fields will be updated.
    """
    title: Optional[str] = Field(None, min_length=1, max_length=500, description="Title of the PlanItem")
    description: Optional[str] = Field(None, max_length=10000, description="Detailed description")
    source_todo_id: Optional[str] = Field(None, description="ID of the source ToDo")
    source_task_id: Optional[str] = Field(None, description="ID of the linked Task")
    status: Optional[PlanItemStatus] = Field(None, description="Current status of the PlanItem")
    priority: Optional[Priority] = Field(None, description="Priority level")
    scheduled_date: Optional[datetime] = Field(None, description="Scheduled date for the PlanItem")
    focus_type: Optional[FocusType] = Field(None, description="Focus category")
    sort_order: Optional[int] = Field(None, ge=0, description="Sort order within the focus_type category")


class PlanItemResponse(BaseModel):
    """Response model for PlanItem entities.
    
    Requirement 22.1: THE System SHALL store PlanItem entities in the database
    (DB-canonical) with fields: id, workspace_id, title, description,
    source_todo_id, source_task_id, status, priority, scheduled_date,
    focus_type, sort_order, created_at, updated_at.
    
    Requirement 22.4: PlanItems can be workspace-scoped (local) or
    SwarmWS-scoped (global/cross-domain).
    """
    id: str = Field(..., description="Unique identifier for the PlanItem")
    workspace_id: str = Field(..., description="ID of the workspace this PlanItem belongs to")
    title: str = Field(..., description="Title of the PlanItem")
    description: Optional[str] = Field(None, description="Detailed description")
    source_todo_id: Optional[str] = Field(None, description="ID of the source ToDo if created from a Signal")
    source_task_id: Optional[str] = Field(None, description="ID of the linked Task for execution tracking")
    status: PlanItemStatus = Field(..., description="Current status of the PlanItem")
    priority: Priority = Field(..., description="Priority level of the PlanItem")
    scheduled_date: Optional[datetime] = Field(None, description="Scheduled date for the PlanItem")
    focus_type: FocusType = Field(..., description="Focus category for the PlanItem")
    sort_order: int = Field(..., description="Sort order within the focus_type category")
    created_at: datetime = Field(..., description="Timestamp when the PlanItem was created")
    updated_at: datetime = Field(..., description="Timestamp when the PlanItem was last updated")
