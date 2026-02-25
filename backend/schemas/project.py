"""Project metadata schemas for the SwarmWS single-workspace model (Cadence 2).

Enriched from Cadence 1 with description, priority, schema_version, version
counter, and update_history tracking.

- ``ProjectStatus``          — Literal type for lifecycle statuses
- ``ProjectPriority``        — Literal type for priority levels
- ``ProjectHistoryAction``   — Literal type for history action kinds
- ``ProjectHistorySource``   — Literal type for change sources
- ``ProjectHistoryEntry``    — Single update_history entry
- ``ProjectMetadata``        — Full .project.json model
- ``ProjectCreate``          — POST request body
- ``ProjectUpdate``          — PUT request body
- ``ProjectResponse``        — API response model
- ``ProjectHistoryResponse`` — GET /projects/{id}/history response
"""
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

ProjectStatus = Literal["active", "archived", "completed"]
ProjectPriority = Literal["low", "medium", "high", "critical"]
ProjectHistoryAction = Literal[
    "created",
    "updated",
    "status_changed",
    "renamed",
    "archived",
    "restored",
    "tags_modified",
    "priority_changed",
    "schema_migrated",
]
ProjectHistorySource = Literal["user", "agent", "system", "migration"]


class ProjectHistoryEntry(BaseModel):
    """A single entry in the project update_history array."""

    version: int
    timestamp: str
    action: ProjectHistoryAction
    changes: dict[str, Any] = Field(default_factory=dict)
    source: ProjectHistorySource


class ProjectMetadata(BaseModel):
    """Full project metadata stored in .project.json."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="")
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    status: ProjectStatus = Field(default="active")
    tags: list[str] = Field(default_factory=list)
    priority: Optional[ProjectPriority] = None
    schema_version: str = Field(default="1.0.0")
    version: int = Field(default=1)
    update_history: list[ProjectHistoryEntry] = Field(default_factory=list)


class ProjectCreate(BaseModel):
    """POST /api/projects request body."""

    name: str = Field(..., min_length=1, max_length=100)


class ProjectUpdate(BaseModel):
    """PUT /api/projects/{id} request body."""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    status: Optional[ProjectStatus] = None
    tags: Optional[list[str]] = None
    priority: Optional[ProjectPriority] = None


class ProjectResponse(ProjectMetadata):
    """API response model for a project (extends full metadata)."""

    path: str = Field(default="")


class ProjectHistoryResponse(BaseModel):
    """GET /api/projects/{id}/history response."""

    project_id: str
    history: list[ProjectHistoryEntry]
