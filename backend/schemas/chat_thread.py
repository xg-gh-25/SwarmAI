"""ChatThread and ThreadSummary schemas for the Chat Thread Model.

This module defines the Pydantic models for ChatThread, ChatMessage, and
ThreadSummary entities. ChatThreads bind conversations to the Agent → Task/ToDo
→ Workspace relationship, enabling properly scoped and retrievable chat context.

ChatThreads and ChatMessages are DB-canonical (stored in database, not filesystem).
ThreadSummaries are used for search indexing instead of raw messages.

Requirements: 30.1, 30.2, 30.3, 30.4, 30.9, 30.10
"""
from datetime import datetime
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


class ChatMode(str, Enum):
    """Chat mode values.
    
    Requirement 30.2: THE System SHALL support ChatThread mode values:
    explore, execute.
    
    - explore: Lightweight conversation mode for exploration and discovery
    - execute: Structured mode for task execution with agent orchestration
    """
    EXPLORE = "explore"
    EXECUTE = "execute"


class MessageRole(str, Enum):
    """Chat message role values.
    
    Requirement 30.4: THE System SHALL support ChatMessage role values:
    user, assistant, tool, system.
    """
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"


class SummaryType(str, Enum):
    """Thread summary type values.
    
    Requirement 30.10: THE System SHALL support ThreadSummary summary_type values:
    rolling, final.
    
    - rolling: Continuously updated summary as conversation progresses
    - final: Final summary generated when thread is completed/archived
    """
    ROLLING = "rolling"
    FINAL = "final"


class ChatThreadCreate(BaseModel):
    """Request model for creating a new ChatThread.
    
    Requirement 30.1: THE System SHALL store ChatThread entities in the database
    (DB-canonical) with fields: id, workspace_id, agent_id, task_id (nullable),
    todo_id (nullable), mode, title, created_at, updated_at.
    
    Requirement 26.5: Threads reference a ``project_id`` (UUID from
    ``.project.json``) instead of relying solely on ``workspace_id``.
    Threads not associated with any project have ``project_id`` set to
    ``None`` (NULL), indicating a global SwarmWS chat.
    """
    workspace_id: str = Field(..., description="ID of the workspace this ChatThread belongs to")
    agent_id: str = Field(..., description="ID of the agent associated with this thread")
    project_id: Optional[str] = Field(None, description="Project UUID, NULL for global chats")
    task_id: Optional[str] = Field(None, description="ID of the Task this thread is bound to")
    todo_id: Optional[str] = Field(None, description="ID of the ToDo this thread is bound to")
    mode: ChatMode = Field(
        default=ChatMode.EXPLORE,
        description="Mode of the chat thread (explore or execute)"
    )
    title: str = Field(..., min_length=1, max_length=500, description="Title of the chat thread")


class ChatThreadUpdate(BaseModel):
    """Request model for updating an existing ChatThread.
    
    All fields are optional - only provided fields will be updated.
    """
    task_id: Optional[str] = Field(None, description="ID of the Task this thread is bound to")
    todo_id: Optional[str] = Field(None, description="ID of the ToDo this thread is bound to")
    mode: Optional[ChatMode] = Field(None, description="Mode of the chat thread")
    title: Optional[str] = Field(None, min_length=1, max_length=500, description="Title of the chat thread")


class ChatThreadResponse(BaseModel):
    """Response model for ChatThread entities.
    
    Requirement 30.1: THE System SHALL store ChatThread entities in the database
    (DB-canonical) with fields: id, workspace_id, agent_id, task_id (nullable),
    todo_id (nullable), mode, title, created_at, updated_at.
    
    Requirement 26.5: Includes ``project_id`` referencing the project UUID from
    ``.project.json``.  A ``None`` value indicates a global SwarmWS chat not
    associated with any specific project.
    
    Requirement 26.6: ``context_version`` is a lightweight integer counter
    incremented whenever bindings or context-affecting state changes occur.
    Used for cache invalidation in the context snapshot cache.
    """
    id: str = Field(..., description="Unique identifier for the ChatThread")
    workspace_id: str = Field(..., description="ID of the workspace this ChatThread belongs to")
    agent_id: str = Field(..., description="ID of the agent associated with this thread")
    project_id: Optional[str] = Field(None, description="Project UUID, NULL for global chats")
    task_id: Optional[str] = Field(None, description="ID of the Task this thread is bound to")
    todo_id: Optional[str] = Field(None, description="ID of the ToDo this thread is bound to")
    mode: ChatMode = Field(..., description="Mode of the chat thread (explore or execute)")
    title: str = Field(..., description="Title of the chat thread")
    context_version: int = Field(0, description="Version counter for cache invalidation")
    created_at: datetime = Field(..., description="Timestamp when the ChatThread was created")
    updated_at: datetime = Field(..., description="Timestamp when the ChatThread was last updated")


class ChatMessageCreate(BaseModel):
    """Request model for creating a new ChatMessage.
    
    Requirement 30.3: THE System SHALL store ChatMessage entities in the database
    with fields: id, thread_id, role, content, tool_calls (nullable), created_at.
    """
    thread_id: str = Field(..., description="ID of the ChatThread this message belongs to")
    role: MessageRole = Field(..., description="Role of the message sender")
    content: str = Field(..., description="Content of the message")
    tool_calls: Optional[str] = Field(None, description="JSON-encoded tool calls (for assistant messages)")


class ChatMessageResponse(BaseModel):
    """Response model for ChatMessage entities.
    
    Requirement 30.3: THE System SHALL store ChatMessage entities in the database
    with fields: id, thread_id, role, content, tool_calls (nullable), created_at.
    """
    id: str = Field(..., description="Unique identifier for the ChatMessage")
    thread_id: str = Field(..., description="ID of the ChatThread this message belongs to")
    role: MessageRole = Field(..., description="Role of the message sender")
    content: str = Field(..., description="Content of the message")
    tool_calls: Optional[str] = Field(None, description="JSON-encoded tool calls (for assistant messages)")
    created_at: datetime = Field(..., description="Timestamp when the ChatMessage was created")


class ThreadSummaryCreate(BaseModel):
    """Request model for creating a new ThreadSummary.
    
    Requirement 30.9: THE System SHALL store ThreadSummary entities with fields:
    id, thread_id, summary_type, summary_text, key_decisions, open_questions, updated_at.
    """
    thread_id: str = Field(..., description="ID of the ChatThread this summary belongs to")
    summary_type: SummaryType = Field(
        default=SummaryType.ROLLING,
        description="Type of summary (rolling or final)"
    )
    summary_text: str = Field(..., min_length=1, description="AI-generated summary of the thread")
    key_decisions: Optional[List[str]] = Field(None, description="List of key decisions made in the thread")
    open_questions: Optional[List[str]] = Field(None, description="List of open questions from the thread")


class ThreadSummaryUpdate(BaseModel):
    """Request model for updating an existing ThreadSummary.
    
    All fields are optional - only provided fields will be updated.
    """
    summary_type: Optional[SummaryType] = Field(None, description="Type of summary")
    summary_text: Optional[str] = Field(None, min_length=1, description="AI-generated summary of the thread")
    key_decisions: Optional[List[str]] = Field(None, description="List of key decisions made in the thread")
    open_questions: Optional[List[str]] = Field(None, description="List of open questions from the thread")


class ThreadSummaryResponse(BaseModel):
    """Response model for ThreadSummary entities.
    
    Requirement 30.9: THE System SHALL store ThreadSummary entities with fields:
    id, thread_id, summary_type, summary_text, key_decisions, open_questions, updated_at.
    
    ThreadSummaries are used for search indexing instead of raw ChatMessages
    (Requirement 31.1).
    """
    id: str = Field(..., description="Unique identifier for the ThreadSummary")
    thread_id: str = Field(..., description="ID of the ChatThread this summary belongs to")
    summary_type: SummaryType = Field(..., description="Type of summary (rolling or final)")
    summary_text: str = Field(..., description="AI-generated summary of the thread")
    key_decisions: Optional[List[str]] = Field(None, description="List of key decisions made in the thread")
    open_questions: Optional[List[str]] = Field(None, description="List of open questions from the thread")
    updated_at: datetime = Field(..., description="Timestamp when the ThreadSummary was last updated")
