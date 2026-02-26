"""Pydantic schemas for channel management."""
from pydantic import BaseModel, Field
from typing import Literal, Optional


class ChannelCreateRequest(BaseModel):
    """Request to create a new channel."""
    name: str = Field(..., min_length=1, max_length=255)
    channel_type: Literal["feishu", "slack", "discord", "web_widget"]
    agent_id: str
    config: dict = Field(default_factory=dict)
    access_mode: Literal["open", "allowlist", "blocklist"] = "allowlist"
    allowed_senders: list[str] = Field(default_factory=list)
    rate_limit_per_minute: int = Field(default=10, ge=1, le=100)
    enable_skills: bool = False
    enable_mcp: bool = False


class ChannelUpdateRequest(BaseModel):
    """Request to update a channel."""
    name: Optional[str] = None
    config: Optional[dict] = None
    agent_id: Optional[str] = None
    access_mode: Optional[Literal["open", "allowlist", "blocklist"]] = None
    allowed_senders: Optional[list[str]] = None
    blocked_senders: Optional[list[str]] = None
    rate_limit_per_minute: Optional[int] = Field(default=None, ge=1, le=100)
    enable_skills: Optional[bool] = None
    enable_mcp: Optional[bool] = None


class ChannelResponse(BaseModel):
    """Response for a channel."""
    id: str
    name: str
    channel_type: str
    agent_id: str
    agent_name: Optional[str] = None
    config: dict
    status: str
    error_message: Optional[str] = None
    access_mode: str
    allowed_senders: list[str]
    blocked_senders: list[str]
    rate_limit_per_minute: int
    enable_skills: bool
    enable_mcp: bool
    created_at: str
    updated_at: str


class ChannelStatusResponse(BaseModel):
    """Response for channel runtime status."""
    channel_id: str
    status: str
    uptime_seconds: Optional[float] = None
    messages_processed: int = 0
    active_sessions: int = 0
    error_message: Optional[str] = None


class ChannelSessionResponse(BaseModel):
    """Response for a channel session."""
    id: str
    channel_id: str
    external_chat_id: str
    external_sender_id: Optional[str] = None
    external_thread_id: Optional[str] = None
    session_id: str
    sender_display_name: Optional[str] = None
    message_count: int
    last_message_at: Optional[str] = None
    created_at: str
