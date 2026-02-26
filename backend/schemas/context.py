"""Pydantic models for the context assembly preview API.

This module defines request/response schemas for the context preview
endpoint (``GET /api/projects/{id}/context``) and the mid-session thread
binding endpoint (``POST /api/chat_threads/{thread_id}/bind``).

All field names use ``snake_case`` per the backend API naming convention.
The frontend service layer converts to ``camelCase`` before consumption.

Key public symbols:

- ``ContextLayerResponse``    — Single layer in the preview response
- ``ContextPreviewResponse``  — Full preview response with all layers
- ``ThreadBindRequest``       — Request body for thread binding API
- ``ThreadBindResponse``      — Response for thread binding API
"""

from typing import Optional

from pydantic import BaseModel, Field


class ContextLayerResponse(BaseModel):
    """A single context layer in the preview response.

    ``source_path`` is always workspace-relative (never absolute) to
    protect sensitive user directory information.

    Validates: Requirements 33.2, PE Fix #8 (path safety)
    """

    layer_number: int = Field(..., description="Layer priority (1=highest)")
    name: str = Field(..., description="Human-readable layer name")
    source_path: str = Field(
        ..., description="Workspace-relative path of the source"
    )
    token_count: int = Field(
        ..., description="Estimated token count for this layer"
    )
    content_preview: str = Field(
        ..., description="Content truncated to preview limit"
    )
    truncated: bool = Field(
        False, description="Whether this layer was truncated"
    )
    truncation_stage: int = Field(
        0, description="Truncation stage applied (0=none, 1/2/3)"
    )


class ContextPreviewResponse(BaseModel):
    """Full context assembly preview response.

    Returned by ``GET /api/projects/{id}/context``.  Includes every
    assembled layer with token counts, a budget-exceeded flag, a
    human-readable truncation summary, and an ETag for cache validation.

    Validates: Requirements 33.2, 33.3, 33.7
    """

    project_id: str = Field(..., description="Project UUID")
    thread_id: Optional[str] = Field(
        None, description="Optional chat thread ID"
    )
    layers: list[ContextLayerResponse] = Field(default_factory=list)
    total_token_count: int = Field(
        0, description="Sum of all layer token counts"
    )
    budget_exceeded: bool = Field(
        False, description="Whether token budget was exceeded"
    )
    token_budget: int = Field(10000, description="Configured token budget")
    truncation_summary: str = Field(
        "", description="Human-readable truncation summary"
    )
    etag: str = Field("", description="Version-based ETag for caching")


class ThreadBindRequest(BaseModel):
    """Request body for mid-session thread binding.

    Supports two modes:
    - ``replace``: overwrites existing task_id / todo_id
    - ``add``: only sets fields that are currently NULL

    An optional ``force`` flag overrides the cross-project binding
    guardrail (PE Enhancement C).

    Validates: Requirements 35.1, PE Fix #5 (mid-session binding)
    """

    task_id: Optional[str] = Field(None, description="Task ID to bind")
    todo_id: Optional[str] = Field(None, description="ToDo ID to bind")
    mode: str = Field(
        "replace", description="Binding mode: 'replace' or 'add'"
    )
    force: Optional[bool] = Field(
        None,
        description="Override cross-project binding guardrail when True",
    )


class ThreadBindResponse(BaseModel):
    """Response for thread binding API.

    Returns the updated binding state and the incremented
    ``context_version`` counter used for cache invalidation.

    Validates: Requirements 35.1, PE Fix #5 (mid-session binding)
    """

    thread_id: str
    task_id: Optional[str] = None
    todo_id: Optional[str] = None
    context_version: int = Field(
        ..., description="Incremented version after binding"
    )
