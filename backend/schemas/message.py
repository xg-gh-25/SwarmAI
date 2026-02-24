"""Message and Chat-related Pydantic models."""
from pydantic import BaseModel, Field
from typing import Literal, Any
from datetime import datetime


# Multimodal content block types for file attachments
class ImageSourceBase64(BaseModel):
    """Base64 image source."""
    type: Literal["base64"] = "base64"
    media_type: str  # "image/png", "image/jpeg", "image/gif", "image/webp"
    data: str  # Base64 encoded image data


class ImageContent(BaseModel):
    """Image content block for multimodal messages."""
    type: Literal["image"] = "image"
    source: ImageSourceBase64


class DocumentSourceBase64(BaseModel):
    """Base64 document source."""
    type: Literal["base64"] = "base64"
    media_type: str  # "application/pdf"
    data: str  # Base64 encoded document data


class DocumentContent(BaseModel):
    """Document content block for multimodal messages (PDF)."""
    type: Literal["document"] = "document"
    source: DocumentSourceBase64


class ChatRequest(BaseModel):
    """Request model for chat.

    Supports both simple text messages and multimodal content with attachments.
    - For simple text: use `message` field
    - For multimodal: use `content` field with array of content blocks
    """

    agent_id: str
    message: str | None = None  # Optional if content is provided
    content: list[dict[str, Any]] | None = None  # Multimodal content array
    session_id: str | None = None
    enable_skills: bool = False
    enable_mcp: bool = False
    add_dirs: list[str] | None = None  # Additional directories for Claude to access


class AnswerQuestionRequest(BaseModel):
    """Request model for answering AskUserQuestion."""

    agent_id: str
    session_id: str
    tool_use_id: str
    answers: dict[str, str]
    enable_skills: bool = False
    enable_mcp: bool = False


class TextContent(BaseModel):
    """Text content block."""

    type: Literal["text"] = "text"
    text: str


class ToolUseContent(BaseModel):
    """Tool use content block."""

    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


class ToolResultContent(BaseModel):
    """Tool result content block."""

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str | None = None
    is_error: bool = False


ContentBlock = TextContent | ToolUseContent | ToolResultContent


class AssistantMessageResponse(BaseModel):
    """Response model for assistant message."""

    type: Literal["assistant"] = "assistant"
    content: list[dict[str, Any]]
    model: str | None = None


class ResultMessageResponse(BaseModel):
    """Response model for result message."""

    type: Literal["result"] = "result"
    session_id: str
    duration_ms: int
    total_cost_usd: float | None = None
    num_turns: int
    is_error: bool = False


class ChatSession(BaseModel):
    """Chat session model."""

    id: str
    agent_id: str
    title: str
    created_at: datetime
    last_accessed_at: datetime


class ChatSessionResponse(BaseModel):
    """Response model for chat session."""

    id: str
    agent_id: str
    title: str
    created_at: str
    last_accessed_at: str
    work_dir: str | None = None


class ChatMessageResponse(BaseModel):
    """Response model for chat message."""

    id: str
    session_id: str
    role: str  # 'user' or 'assistant'
    content: list[dict[str, Any]]
    model: str | None = None
    created_at: str
