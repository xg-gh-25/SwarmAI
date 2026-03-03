"""Filesystem-based skill Pydantic models.

This module defines the API request/response schemas for the filesystem-backed
skills system.  Database-specific models have been removed; identity is now
the skill's folder name (kebab-case string) rather than a DB UUID.

Public models:

- ``SkillResponse``              — API response for a single skill
- ``SkillCreateRequest``         — Request body for creating a user skill
- ``SkillUpdateRequest``         — Request body for updating a user skill
- ``SkillGenerateWithAgentRequest`` — Request body for AI-powered skill generation
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class SkillResponse(BaseModel):
    """API response for a single skill."""

    folder_name: str
    name: str
    description: str
    version: str = "1.0.0"
    source_tier: Literal["built-in", "user", "plugin"]
    read_only: bool
    content: str | None = None


class SkillCreateRequest(BaseModel):
    """Request to create a new user skill."""

    folder_name: str = Field(
        ...,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$",
        max_length=128,
    )
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., max_length=2000)
    content: str = Field(..., max_length=500_000)


class SkillUpdateRequest(BaseModel):
    """Request to update an existing user skill."""

    name: str | None = Field(None, max_length=255)
    description: str | None = Field(None, max_length=2000)
    content: str | None = Field(None, max_length=500_000)


class SkillGenerateWithAgentRequest(BaseModel):
    """Request model for generating a skill with agent conversation."""

    skill_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Name of the skill to create",
    )
    skill_description: str = Field(
        ...,
        description="Description of what the skill should do",
    )
    session_id: Optional[str] = Field(
        None,
        description="Session ID for continuing conversation",
    )
    message: Optional[str] = Field(
        None,
        description="Follow-up message for iterating on the skill",
    )
    model: Optional[str] = Field(
        None,
        description="Model to use for skill generation",
    )
