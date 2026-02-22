"""Permission request/response schemas for Human-in-the-Loop approval."""

from pydantic import BaseModel, Field
from typing import Literal, Optional
from datetime import datetime


class PermissionRequest(BaseModel):
    """Permission request sent to frontend for user approval."""
    id: str = Field(..., description="Unique identifier for the permission request")
    session_id: str = Field(..., description="Chat session ID")
    tool_name: str = Field(..., description="Name of the tool requesting permission")
    tool_input: dict = Field(..., description="Input parameters for the tool")
    reason: str = Field(..., description="Reason why this command requires approval")
    options: list[str] = Field(default=["approve", "deny"], description="Available decision options")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class PermissionResponseRequest(BaseModel):
    """User's decision on a permission request."""
    session_id: str = Field(..., description="Chat session ID")
    request_id: str = Field(..., description="ID of the permission request being responded to")
    decision: Literal["approve", "deny"] = Field(..., description="User's decision")
    feedback: Optional[str] = Field(default=None, description="Optional user feedback")


class PermissionRequestResponse(BaseModel):
    """Response after recording a permission decision."""
    status: str = Field(default="recorded")
    request_id: str
