"""ToDo/Signal schemas for incoming work item management.

This module defines the Pydantic models for ToDo entities, which represent
incoming work signals in the Daily Work Operating Loop. In the UI, these
are displayed as "Signals" but the technical entity name is "ToDo".

Requirements: 4.1, 4.2, 4.3, 4.4
"""
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class ToDoStatus(str, Enum):
    """ToDo status values.
    
    Requirement 4.2: THE System SHALL support ToDo status values:
    pending, overdue, in_discussion, handled, cancelled, deleted.
    """
    PENDING = "pending"
    OVERDUE = "overdue"
    IN_DISCUSSION = "in_discussion"
    HANDLED = "handled"
    CANCELLED = "cancelled"
    DELETED = "deleted"


class ToDoSourceType(str, Enum):
    """ToDo source type values.
    
    Requirement 4.3: THE System SHALL support ToDo source_type values:
    manual, email, slack, meeting, integration.
    """
    MANUAL = "manual"
    EMAIL = "email"
    SLACK = "slack"
    MEETING = "meeting"
    INTEGRATION = "integration"


class Priority(str, Enum):
    """Priority values for ToDos and other entities.
    
    Requirement 4.4: THE System SHALL support ToDo priority values:
    high, medium, low, none.
    """
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class ToDoCreate(BaseModel):
    """Request model for creating a new ToDo.
    
    Requirement 4.1: THE System SHALL store ToDo entities in the database
    (DB-canonical) with fields: id, workspace_id, title, description, source,
    source_type, status, priority, due_date, created_at, updated_at.
    """
    workspace_id: str = Field(..., description="ID of the workspace this ToDo belongs to")
    title: str = Field(..., min_length=1, max_length=500, description="Title of the ToDo")
    description: Optional[str] = Field(None, max_length=10000, description="Detailed description")
    source: Optional[str] = Field(None, max_length=1000, description="Source reference (e.g., email subject, meeting name)")
    source_type: ToDoSourceType = Field(
        default=ToDoSourceType.MANUAL,
        description="Type of source where this ToDo originated"
    )
    priority: Priority = Field(
        default=Priority.NONE,
        description="Priority level of the ToDo"
    )
    due_date: Optional[datetime] = Field(None, description="Due date for the ToDo")


class ToDoUpdate(BaseModel):
    """Request model for updating an existing ToDo.
    
    All fields are optional - only provided fields will be updated.
    """
    title: Optional[str] = Field(None, min_length=1, max_length=500, description="Title of the ToDo")
    description: Optional[str] = Field(None, max_length=10000, description="Detailed description")
    source: Optional[str] = Field(None, max_length=1000, description="Source reference")
    source_type: Optional[ToDoSourceType] = Field(None, description="Type of source")
    status: Optional[ToDoStatus] = Field(None, description="Current status of the ToDo")
    priority: Optional[Priority] = Field(None, description="Priority level")
    due_date: Optional[datetime] = Field(None, description="Due date for the ToDo")


class ToDoResponse(BaseModel):
    """Response model for ToDo entities.
    
    Requirement 4.1: THE System SHALL store ToDo entities in the database
    (DB-canonical) with fields: id, workspace_id, title, description, source,
    source_type, status, priority, due_date, created_at, updated_at.
    
    Additional field task_id is included for tracking conversion to Task
    (Requirements 4.7, 4.8).
    """
    id: str = Field(..., description="Unique identifier for the ToDo")
    workspace_id: str = Field(..., description="ID of the workspace this ToDo belongs to")
    title: str = Field(..., description="Title of the ToDo")
    description: Optional[str] = Field(None, description="Detailed description")
    source: Optional[str] = Field(None, description="Source reference")
    source_type: ToDoSourceType = Field(..., description="Type of source where this ToDo originated")
    status: ToDoStatus = Field(..., description="Current status of the ToDo")
    priority: Priority = Field(..., description="Priority level of the ToDo")
    due_date: Optional[datetime] = Field(None, description="Due date for the ToDo")
    task_id: Optional[str] = Field(None, description="ID of the Task if this ToDo was converted")
    created_at: datetime = Field(..., description="Timestamp when the ToDo was created")
    updated_at: datetime = Field(..., description="Timestamp when the ToDo was last updated")


class ToDoConvertToTaskRequest(BaseModel):
    """Request model for converting a ToDo to a Task.
    
    Requirements 4.7, 4.8: THE System SHALL allow converting a ToDo to a Task,
    linking the original ToDo to the created Task. WHEN a ToDo is converted to
    a Task, THE System SHALL update the ToDo status to handled and store the
    task_id reference.
    """
    agent_id: str = Field(..., description="ID of the agent to assign the task to")
    title: Optional[str] = Field(None, description="Override title for the task (defaults to ToDo title)")
    description: Optional[str] = Field(None, description="Override description for the task")
    priority: Optional[Priority] = Field(None, description="Override priority for the task")
