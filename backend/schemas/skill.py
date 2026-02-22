"""Skill-related Pydantic models."""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Literal


class SkillMetadata(BaseModel):
    """Skill metadata model."""

    id: str | None = None
    name: str = Field(..., min_length=1, max_length=255)
    description: str
    folder_name: str | None = None  # Folder name in ~/.claude/skills/
    local_path: str | None = None  # Full local path
    # Source tracking
    source_type: Literal["user", "plugin", "marketplace", "local"] = Field(
        default="user", description="Where this skill came from"
    )
    source_plugin_id: str | None = Field(default=None, description="Plugin ID if from plugin")
    source_marketplace_id: str | None = Field(default=None, description="Marketplace ID if from marketplace")
    # Git tracking (optional)
    git_url: str | None = None
    git_branch: str | None = Field(default="main")
    git_commit: str | None = None
    # Metadata
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    version: str = Field(default="1.0.0")
    is_system: bool = Field(default=False)
    # Version control fields
    current_version: int = Field(default=0, description="Current published version (0 = never published)")
    has_draft: bool = Field(default=False, description="Whether unpublished draft exists")


class SkillCreateRequest(BaseModel):
    """Request model for creating a skill via AI generation."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(
        ..., description="Natural language description of the skill to generate"
    )
    examples: list[str] | None = None


class SkillGenerateRequest(BaseModel):
    """Request model for generating a skill with AI."""

    description: str = Field(
        ..., description="Natural language description of the skill to generate"
    )
    examples: list[str] | None = None


class SkillResponse(BaseModel):
    """Response model for skill."""

    id: str
    name: str
    description: str
    folder_name: str | None = None
    local_path: str | None = None
    # Source tracking
    source_type: Literal["user", "plugin", "marketplace", "local"] = "user"
    source_plugin_id: str | None = None
    source_marketplace_id: str | None = None
    source_plugin_name: str | None = None  # Display name of source plugin
    source_marketplace_name: str | None = None  # Display name of source marketplace
    # Git tracking
    git_url: str | None = None
    git_branch: str | None = None
    git_commit: str | None = None
    # Metadata
    created_by: str | None = None
    created_at: str
    updated_at: str
    version: str
    is_system: bool
    # Version control fields
    current_version: int = 0
    has_draft: bool = False


class SyncError(BaseModel):
    """Error detail for sync operation."""
    skill: str
    error: str


class SyncResultResponse(BaseModel):
    """Response model for skill synchronization."""
    added: list[str] = Field(default_factory=list, description="Skills added during sync")
    updated: list[str] = Field(default_factory=list, description="Skills updated during sync")
    removed: list[str] = Field(default_factory=list, description="Orphaned DB records found")
    errors: list[SyncError] = Field(default_factory=list, description="Errors encountered during sync")
    total_local: int = Field(default=0, description="Total user skills found in local directory")
    total_plugins: int = Field(default=0, description="Total skills from installed plugins")
    total_db: int = Field(default=0, description="Total skills in database before sync")


class SkillGenerateWithAgentRequest(BaseModel):
    """Request model for generating a skill with agent conversation."""

    skill_name: str = Field(..., min_length=1, max_length=255, description="Name of the skill to create")
    skill_description: str = Field(..., description="Description of what the skill should do")
    session_id: Optional[str] = Field(None, description="Session ID for continuing conversation")
    message: Optional[str] = Field(None, description="Follow-up message for iterating on the skill")
    model: Optional[str] = Field(None, description="Model to use for skill generation (e.g., claude-sonnet-4-5-20250514)")


class SkillFinalizeRequest(BaseModel):
    """Request model for finalizing skill creation."""

    skill_name: str = Field(..., min_length=1, max_length=255, description="Sanitized folder name of the skill")
    display_name: Optional[str] = Field(None, max_length=255, description="User-provided display name for the skill")


# ============== Version Control Models ==============

class SkillVersionResponse(BaseModel):
    """Response model for a skill version."""

    id: str
    skill_id: str
    version: int
    git_commit: str | None = None
    local_path: str | None = None
    created_at: str
    change_summary: str | None = None


class SkillVersionListResponse(BaseModel):
    """Response model for listing skill versions."""

    skill_id: str
    skill_name: str
    current_version: int
    has_draft: bool
    versions: list[SkillVersionResponse]


class PublishDraftRequest(BaseModel):
    """Request model for publishing a draft."""

    change_summary: str | None = Field(None, max_length=500, description="Optional summary of changes")


class RollbackRequest(BaseModel):
    """Request model for rolling back to a specific version."""

    version: int = Field(..., ge=1, description="Version number to rollback to")

