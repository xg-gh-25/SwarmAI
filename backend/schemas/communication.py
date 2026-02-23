"""Communication schemas for the Communicate section of the Daily Work Operating Loop.

This module defines the Pydantic models for Communication entities, which represent
stakeholder alignment work items in the Communicate section. Communications track
interactions with stakeholders including emails, Slack messages, meetings, and other
communication channels.

Requirements: 23.1, 23.2, 23.3, 23.4
"""
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

from .todo import Priority


class CommunicationStatus(str, Enum):
    """Communication status values.
    
    Requirement 23.2: THE System SHALL support Communication status values:
    pending_reply, ai_draft, follow_up, sent, cancelled.
    """
    PENDING_REPLY = "pending_reply"
    AI_DRAFT = "ai_draft"
    FOLLOW_UP = "follow_up"
    SENT = "sent"
    CANCELLED = "cancelled"


class ChannelType(str, Enum):
    """Communication channel type values.
    
    Requirement 23.3: THE System SHALL support Communication channel_type values:
    email, slack, meeting, other.
    """
    EMAIL = "email"
    SLACK = "slack"
    MEETING = "meeting"
    OTHER = "other"


class CommunicationCreate(BaseModel):
    """Request model for creating a new Communication.
    
    Requirement 23.1: THE System SHALL store Communication entities in the database
    (DB-canonical) with fields: id, workspace_id, title, description, recipient,
    channel_type, status, priority, due_date, ai_draft_content, sent_at,
    created_at, updated_at.
    """
    workspace_id: str = Field(..., description="ID of the workspace this Communication belongs to")
    title: str = Field(..., min_length=1, max_length=500, description="Title of the Communication")
    description: Optional[str] = Field(None, max_length=10000, description="Detailed description")
    recipient: str = Field(..., min_length=1, max_length=500, description="Recipient of the communication")
    channel_type: ChannelType = Field(
        default=ChannelType.OTHER,
        description="Type of communication channel"
    )
    status: CommunicationStatus = Field(
        default=CommunicationStatus.PENDING_REPLY,
        description="Current status of the Communication"
    )
    priority: Priority = Field(
        default=Priority.NONE,
        description="Priority level of the Communication"
    )
    due_date: Optional[datetime] = Field(None, description="Due date for the Communication")
    ai_draft_content: Optional[str] = Field(
        None,
        max_length=50000,
        description="AI-generated draft content for the communication"
    )
    source_task_id: Optional[str] = Field(None, description="ID of the source Task for context")
    source_todo_id: Optional[str] = Field(None, description="ID of the source ToDo for context")


class CommunicationUpdate(BaseModel):
    """Request model for updating an existing Communication.
    
    All fields are optional - only provided fields will be updated.
    """
    title: Optional[str] = Field(None, min_length=1, max_length=500, description="Title of the Communication")
    description: Optional[str] = Field(None, max_length=10000, description="Detailed description")
    recipient: Optional[str] = Field(None, min_length=1, max_length=500, description="Recipient of the communication")
    channel_type: Optional[ChannelType] = Field(None, description="Type of communication channel")
    status: Optional[CommunicationStatus] = Field(None, description="Current status of the Communication")
    priority: Optional[Priority] = Field(None, description="Priority level")
    due_date: Optional[datetime] = Field(None, description="Due date for the Communication")
    ai_draft_content: Optional[str] = Field(
        None,
        max_length=50000,
        description="AI-generated draft content for the communication"
    )
    source_task_id: Optional[str] = Field(None, description="ID of the source Task for context")
    source_todo_id: Optional[str] = Field(None, description="ID of the source ToDo for context")
    sent_at: Optional[datetime] = Field(None, description="Timestamp when the communication was sent")


class CommunicationResponse(BaseModel):
    """Response model for Communication entities.
    
    Requirement 23.1: THE System SHALL store Communication entities in the database
    (DB-canonical) with fields: id, workspace_id, title, description, recipient,
    channel_type, status, priority, due_date, ai_draft_content, sent_at,
    created_at, updated_at.
    
    Requirement 23.7: THE System SHALL allow Communications to be linked to source
    Tasks or ToDos for context.
    """
    id: str = Field(..., description="Unique identifier for the Communication")
    workspace_id: str = Field(..., description="ID of the workspace this Communication belongs to")
    title: str = Field(..., description="Title of the Communication")
    description: Optional[str] = Field(None, description="Detailed description")
    recipient: str = Field(..., description="Recipient of the communication")
    channel_type: ChannelType = Field(..., description="Type of communication channel")
    status: CommunicationStatus = Field(..., description="Current status of the Communication")
    priority: Priority = Field(..., description="Priority level of the Communication")
    due_date: Optional[datetime] = Field(None, description="Due date for the Communication")
    ai_draft_content: Optional[str] = Field(None, description="AI-generated draft content")
    source_task_id: Optional[str] = Field(None, description="ID of the source Task for context")
    source_todo_id: Optional[str] = Field(None, description="ID of the source ToDo for context")
    sent_at: Optional[datetime] = Field(None, description="Timestamp when the communication was sent")
    created_at: datetime = Field(..., description="Timestamp when the Communication was created")
    updated_at: datetime = Field(..., description="Timestamp when the Communication was last updated")
