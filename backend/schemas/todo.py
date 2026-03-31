"""ToDo/Signal schemas for incoming work item management.

This module defines the Pydantic models for ToDo entities, which represent
incoming work signals in the Daily Work Operating Loop. In the UI, these
are displayed as "Signals" but the technical entity name is "ToDo".

Key public symbols:

- ``ToDoStatus``       — Enum of lifecycle states (pending, overdue, …, deleted)
- ``ToDoSourceType``   — Enum of origin types (manual, email, …, chat, ai_detected)
- ``Priority``         — Enum of priority levels (high, medium, low, none)
- ``ToDoCreate``       — Request model for creating a ToDo (includes linked_context)
- ``ToDoUpdate``       — Request model for partial updates (includes linked_context)
- ``ToDoResponse``     — Response model with all stored fields (includes linked_context)
- ``ToDoConvertToTaskRequest`` — Request model for ToDo → Task conversion
- ``CONTEXT_REQUIREMENTS`` — Required context fields per source_type
- ``validate_linked_context`` — Enforce minimum context bar (warn, never block)
- ``TODO_LIFECYCLE``   — Configurable lifecycle TTL/purge settings

Requirements: 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 5.3, 5.5
"""
import logging
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


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
    Requirement 5.1: Extended with chat and ai_detected source types.
    """
    MANUAL = "manual"
    EMAIL = "email"
    SLACK = "slack"
    MEETING = "meeting"
    INTEGRATION = "integration"
    CHAT = "chat"
    AI_DETECTED = "ai_detected"


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
    linked_context: Optional[str] = Field(
        None,
        max_length=10000,
        description="JSON string with reference metadata, e.g. "
                    '{"type": "thread", "thread_id": "abc123"}'
    )


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
    linked_context: Optional[str] = Field(
        None,
        max_length=10000,
        description="JSON string with reference metadata"
    )


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
    linked_context: Optional[str] = Field(
        None,
        description="JSON string with reference metadata"
    )
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


# ── Context Requirements per Source Type ─────────────────────────────

# Required context fields per source_type.
# Universal fields (next_step, created_by) are always required on top.
CONTEXT_REQUIREMENTS: dict[str, list[str]] = {
    "email": [
        "email_subject", "email_from", "email_date",
        "email_snippet", "suggested_action",
    ],
    "slack": [
        "channel_name", "sender", "message_snippet", "thread_url",
    ],
    "chat": [
        "session_id", "user_intent",
    ],
    "ai_detected": [
        "detection_reason", "files",
    ],
    "meeting": [
        "meeting_title", "meeting_date", "attendees", "action_item",
    ],
    # manual and integration only need universal fields
}

# Universal fields required for ALL source types
_UNIVERSAL_REQUIRED = ["next_step"]


def validate_linked_context(source_type: str, ctx: dict) -> dict:
    """Enforce minimum context fields per source_type.

    Validates that the linked_context dict contains required fields for the
    given source_type. Logs warnings on missing fields but NEVER blocks
    creation — tags ``_missing_fields`` in the returned dict.

    Args:
        source_type: The todo's source_type (email, slack, chat, etc.)
        ctx: The linked_context dict to validate.

    Returns:
        The same dict, potentially with ``_missing_fields`` key added.
    """
    required = list(_UNIVERSAL_REQUIRED)
    required.extend(CONTEXT_REQUIREMENTS.get(source_type, []))

    missing = [f for f in required if not ctx.get(f)]
    if missing:
        logger.warning(
            "ToDo [%s] missing context fields: %s", source_type, missing,
        )
        ctx["_missing_fields"] = missing
    return ctx


# ── Lifecycle Configuration ──────────────────────────────────────────

TODO_LIFECYCLE = {
    "soft_expire_days": 30,       # pending → cancelled
    "overdue_cancel_days": 14,    # overdue → cancelled
    "purge_retention_days": 14,   # terminal → hard delete
    "archive_before_purge": True, # dump to JSONL before delete
}
