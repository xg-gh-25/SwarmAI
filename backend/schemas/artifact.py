"""Artifact schemas for the Artifacts section of the Daily Work Operating Loop.

This module defines the Pydantic models for Artifact entities, which represent
durable knowledge outputs produced from task execution. Artifacts use hybrid storage:
content stored as files in filesystem, metadata tracked in database.

Requirements: 27.2, 27.3
"""
from datetime import datetime
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


class ArtifactType(str, Enum):
    """Artifact type values.
    
    Requirement 27.3: THE System SHALL support artifact_type values:
    plan, report, doc, decision, other.
    """
    PLAN = "plan"
    REPORT = "report"
    DOC = "doc"
    DECISION = "decision"
    OTHER = "other"


class ArtifactCreate(BaseModel):
    """Request model for creating a new Artifact.
    
    Requirement 27.2: THE System SHALL store Artifact metadata in the database
    with fields: id, workspace_id, task_id (nullable), artifact_type, title,
    file_path, version, created_by, created_at, updated_at.
    """
    workspace_id: str = Field(..., description="ID of the workspace this Artifact belongs to")
    task_id: Optional[str] = Field(None, description="ID of the source Task that produced this Artifact")
    artifact_type: ArtifactType = Field(
        default=ArtifactType.OTHER,
        description="Type of artifact"
    )
    title: str = Field(..., min_length=1, max_length=500, description="Title of the Artifact")
    file_path: str = Field(..., min_length=1, max_length=1000, description="Relative path to the artifact file within the workspace")
    version: int = Field(
        default=1,
        ge=1,
        description="Version number of the artifact"
    )
    created_by: str = Field(..., min_length=1, max_length=255, description="User or agent identifier who created the artifact")
    tags: Optional[List[str]] = Field(None, description="Optional list of tags for the artifact")


class ArtifactUpdate(BaseModel):
    """Request model for updating an existing Artifact.
    
    All fields are optional - only provided fields will be updated.
    """
    task_id: Optional[str] = Field(None, description="ID of the source Task")
    artifact_type: Optional[ArtifactType] = Field(None, description="Type of artifact")
    title: Optional[str] = Field(None, min_length=1, max_length=500, description="Title of the Artifact")
    file_path: Optional[str] = Field(None, min_length=1, max_length=1000, description="Relative path to the artifact file")
    version: Optional[int] = Field(None, ge=1, description="Version number of the artifact")
    tags: Optional[List[str]] = Field(None, description="List of tags for the artifact")


class ArtifactResponse(BaseModel):
    """Response model for Artifact entities.
    
    Requirement 27.2: THE System SHALL store Artifact metadata in the database
    with fields: id, workspace_id, task_id (nullable), artifact_type, title,
    file_path, version, created_by, created_at, updated_at.
    """
    id: str = Field(..., description="Unique identifier for the Artifact")
    workspace_id: str = Field(..., description="ID of the workspace this Artifact belongs to")
    task_id: Optional[str] = Field(None, description="ID of the source Task that produced this Artifact")
    artifact_type: ArtifactType = Field(..., description="Type of artifact")
    title: str = Field(..., description="Title of the Artifact")
    file_path: str = Field(..., description="Relative path to the artifact file within the workspace")
    version: int = Field(..., description="Version number of the artifact")
    created_by: str = Field(..., description="User or agent identifier who created the artifact")
    tags: Optional[List[str]] = Field(None, description="List of tags for the artifact")
    created_at: datetime = Field(..., description="Timestamp when the Artifact was created")
    updated_at: datetime = Field(..., description="Timestamp when the Artifact was last updated")
