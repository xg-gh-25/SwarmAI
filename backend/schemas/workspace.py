"""Workspace schemas for file browser API."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class WorkspaceListRequest(BaseModel):
    """Request to list files in a directory."""

    path: str = "."


class WorkspaceFileInfo(BaseModel):
    """Information about a single file or directory."""

    name: str
    type: str  # "file" or "directory"
    size: int
    modified: datetime


class WorkspaceListResponse(BaseModel):
    """Response containing directory listing."""

    files: list[WorkspaceFileInfo]
    current_path: str
    parent_path: Optional[str] = None  # None if at root


class WorkspaceFileResponse(BaseModel):
    """Response containing file content."""

    content: str  # UTF-8 text or base64 encoded binary
    encoding: str  # "utf-8" or "base64"
    size: int
    mime_type: str


class WorkspaceUploadRequest(BaseModel):
    """Request to upload a file to workspace."""

    filename: str  # Original filename
    content: str  # Base64 encoded file content
    path: str = "."  # Relative path within workspace (default: root)


class WorkspaceUploadResponse(BaseModel):
    """Response after file upload."""

    path: str  # Full relative path to the uploaded file
    filename: str
    size: int
