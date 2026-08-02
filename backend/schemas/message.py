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


class CanvasState(BaseModel):
    """Snapshot of the Canvas (output panel) UI state at request time.

    All fields default-valued so a partial/legacy payload never fails validation.
    `open` = Canvas panel is showing; `output_count` = number of session output
    files listed; pin/mute/collapsed = the user's Canvas controls.
    """

    open: bool = False
    output_count: int = 0
    pinned: bool = False
    muted: bool = False
    collapsed: bool = False


class EditorContext(BaseModel):
    """Request-time snapshot of the agent's own UI state (proprioception, SENSE).

    Superset of the original "currently open file" descriptor — kept the class
    name + `editor_context` wire field for backward-compat. A legacy client that
    sends only {file_path, file_name} still validates unchanged; a newer client
    adds `canvas` + `active_overlay`. `file_path`/`file_name` default to "" so a
    canvas/overlay-only payload (Canvas open with no file) also validates.
    `active_overlay` is the `swarm:show-*` event id of the fullscreen nav overlay
    currently open (None = no overlay open, i.e. chat/Canvas view).
    """

    file_path: str = Field("", max_length=1024)
    file_name: str = Field("", max_length=256)
    canvas: CanvasState | None = None
    active_overlay: str | None = Field(None, max_length=128)


class TerminalContext(BaseModel):
    """Read-only view of an integrated terminal the user explicitly attached (P2).

    Single direction: terminal → session. Lets the agent see recent terminal
    output (e.g. a build log) without copy-paste. buffer_tail is capped to keep
    the injected context bounded.
    """

    buffer_tail: str = Field(..., max_length=16000)
    cwd: str = Field("", max_length=1024)


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
    editor_context: EditorContext | None = None  # Currently open file in editor panel
    terminal_context: TerminalContext | None = None  # Attached terminal output (P2)
    client_id: str | None = None  # Correlation ID for optimistic message dedup


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
