"""Swarm Workspace Pydantic models for request/response handling."""
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class SwarmWorkspaceBase(BaseModel):
    """Base workspace model with common fields."""

    name: str = Field(..., min_length=1, max_length=100)
    file_path: str = Field(..., min_length=1)
    context: str = Field(..., min_length=1)
    icon: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate workspace name."""
        if len(v) > 100:
            raise ValueError("Name must not exceed 100 characters")
        return v

    @field_validator("file_path")
    @classmethod
    def validate_file_path(cls, v: str) -> str:
        """Validate file path format and security.
        
        Path must be absolute, start with ~, or use {app_data_dir} placeholder,
        and must not contain path traversal.
        """
        # Check for path traversal sequences
        if ".." in v:
            raise ValueError("Invalid file path: path traversal not allowed")
        
        # Check path format - must be absolute, start with ~, or use {app_data_dir} placeholder
        if not (v.startswith("/") or v.startswith("~") or v.startswith("{app_data_dir}")):
            raise ValueError("File path must be absolute, start with ~, or use {app_data_dir} placeholder")
        
        return v


class SwarmWorkspaceCreate(SwarmWorkspaceBase):
    """Request model for creating a workspace."""

    pass


class SwarmWorkspaceUpdate(BaseModel):
    """Request model for updating a workspace."""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    file_path: Optional[str] = Field(None, min_length=1)
    context: Optional[str] = Field(None, min_length=1)
    icon: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        """Validate workspace name if provided."""
        if v is not None and len(v) > 100:
            raise ValueError("Name must not exceed 100 characters")
        return v

    @field_validator("file_path")
    @classmethod
    def validate_file_path(cls, v: Optional[str]) -> Optional[str]:
        """Validate file path format and security if provided."""
        if v is None:
            return v
        
        # Check for path traversal sequences
        if ".." in v:
            raise ValueError("Invalid file path: path traversal not allowed")
        
        # Check path format - must be absolute, start with ~, or use {app_data_dir} placeholder
        if not (v.startswith("/") or v.startswith("~") or v.startswith("{app_data_dir}")):
            raise ValueError("File path must be absolute, start with ~, or use {app_data_dir} placeholder")
        
        return v


class SwarmWorkspaceResponse(SwarmWorkspaceBase):
    """Response model for workspace."""

    id: str
    is_default: bool = False
    is_archived: bool = False
    archived_at: Optional[str] = None
    created_at: str
    updated_at: str
