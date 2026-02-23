"""Workspace configuration schemas for Skills, MCPs, and Knowledgebases.

This module defines the Pydantic models for workspace configuration entities,
including Skills, MCPs, Knowledgebases, audit logging, and policy violations.

Requirements: 19.6, 19.7, 19.8, 25.2
"""
from datetime import datetime
from enum import Enum
from typing import Optional, List, Any
from pydantic import BaseModel, Field


class KnowledgebaseSourceType(str, Enum):
    """Knowledgebase source type values.
    
    Requirement 18.3: THE System SHALL support Knowledgebase source types:
    local_file, url, indexed_document, context_file, vector_index.
    """
    LOCAL_FILE = "local_file"
    URL = "url"
    INDEXED_DOCUMENT = "indexed_document"
    CONTEXT_FILE = "context_file"
    VECTOR_INDEX = "vector_index"


class ChangeType(str, Enum):
    """Audit log change type values.
    
    Requirement 25.3: THE System SHALL support change_type values:
    enabled, disabled, added, removed, updated.
    """
    ENABLED = "enabled"
    DISABLED = "disabled"
    ADDED = "added"
    REMOVED = "removed"
    UPDATED = "updated"


class EntityType(str, Enum):
    """Audit log entity type values.
    
    Requirement 25.4: THE System SHALL support entity_type values:
    skill, mcp, knowledgebase, workspace_setting.
    """
    SKILL = "skill"
    MCP = "mcp"
    KNOWLEDGEBASE = "knowledgebase"
    WORKSPACE_SETTING = "workspace_setting"


class PolicyViolationType(str, Enum):
    """Policy violation type values for execution blocking."""
    SKILL = "skill"
    MCP = "mcp"


# ============================================================================
# Workspace Skill Configuration
# ============================================================================

class WorkspaceSkillConfig(BaseModel):
    """Configuration for a Skill within a workspace.
    
    Requirement 19.6: THE API SHALL provide CRUD endpoints for workspace
    Skills configuration: GET/PUT /api/workspaces/{id}/skills.
    
    Requirement 19.9: THE Frontend SHALL define TypeScript interfaces:
    WorkspaceSkillConfig, WorkspaceMcpConfig, WorkspaceKnowledgebaseConfig.
    """
    skill_id: str = Field(..., description="ID of the skill")
    skill_name: str = Field(..., description="Display name of the skill")
    enabled: bool = Field(..., description="Whether the skill is enabled for this workspace")
    is_privileged: bool = Field(
        default=False,
        description="Whether this skill requires explicit user confirmation to enable"
    )


class WorkspaceSkillConfigUpdate(BaseModel):
    """Request model for updating workspace skill configurations.
    
    Used with PUT /api/workspaces/{id}/skills endpoint.
    """
    configs: List[WorkspaceSkillConfig] = Field(
        ...,
        description="List of skill configurations to update"
    )


# ============================================================================
# Workspace MCP Configuration
# ============================================================================

class WorkspaceMcpConfig(BaseModel):
    """Configuration for an MCP server within a workspace.
    
    Requirement 19.7: THE API SHALL provide CRUD endpoints for workspace
    MCP configuration: GET/PUT /api/workspaces/{id}/mcps.
    
    Requirement 19.9: THE Frontend SHALL define TypeScript interfaces:
    WorkspaceSkillConfig, WorkspaceMcpConfig, WorkspaceKnowledgebaseConfig.
    """
    mcp_server_id: str = Field(..., description="ID of the MCP server")
    mcp_server_name: str = Field(..., description="Display name of the MCP server")
    enabled: bool = Field(..., description="Whether the MCP server is enabled for this workspace")
    is_privileged: bool = Field(
        default=False,
        description="Whether this MCP server requires explicit user confirmation to enable"
    )


class WorkspaceMcpConfigUpdate(BaseModel):
    """Request model for updating workspace MCP configurations.
    
    Used with PUT /api/workspaces/{id}/mcps endpoint.
    """
    configs: List[WorkspaceMcpConfig] = Field(
        ...,
        description="List of MCP configurations to update"
    )


# ============================================================================
# Workspace Knowledgebase Configuration
# ============================================================================

class WorkspaceKnowledgebaseConfig(BaseModel):
    """Configuration for a Knowledgebase source within a workspace.
    
    Requirement 19.5: THE Database SHALL create a workspace_knowledgebases table
    with columns: id, workspace_id, source_type, source_path, display_name,
    metadata, excluded_sources (JSON array storing KnowledgebaseSource IDs as
    integers, NOT file paths), created_at, updated_at.
    
    Requirement 19.8: THE API SHALL provide CRUD endpoints for workspace
    Knowledgebase configuration: GET/POST/PUT/DELETE /api/workspaces/{id}/knowledgebases.
    
    Requirement 19.9: THE Frontend SHALL define TypeScript interfaces:
    WorkspaceSkillConfig, WorkspaceMcpConfig, WorkspaceKnowledgebaseConfig.
    """
    id: str = Field(..., description="Unique identifier for the knowledgebase configuration")
    source_type: KnowledgebaseSourceType = Field(
        ...,
        description="Type of knowledgebase source"
    )
    source_path: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Path or URL to the knowledgebase source"
    )
    display_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable name for the knowledgebase source"
    )
    metadata: Optional[dict[str, Any]] = Field(
        None,
        description="Additional metadata for the knowledgebase source"
    )
    excluded_sources: Optional[List[int]] = Field(
        None,
        description="List of inherited source IDs to exclude (for union model with exclusions)"
    )


class WorkspaceKnowledgebaseCreate(BaseModel):
    """Request model for creating a new Knowledgebase source.
    
    Used with POST /api/workspaces/{id}/knowledgebases endpoint.
    """
    source_type: KnowledgebaseSourceType = Field(
        ...,
        description="Type of knowledgebase source"
    )
    source_path: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Path or URL to the knowledgebase source"
    )
    display_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable name for the knowledgebase source"
    )
    metadata: Optional[dict[str, Any]] = Field(
        None,
        description="Additional metadata for the knowledgebase source"
    )
    excluded_sources: Optional[List[int]] = Field(
        None,
        description="List of inherited source IDs to exclude"
    )


class WorkspaceKnowledgebaseUpdate(BaseModel):
    """Request model for updating an existing Knowledgebase source.
    
    All fields are optional - only provided fields will be updated.
    Used with PUT /api/workspaces/{id}/knowledgebases/{kb_id} endpoint.
    """
    source_type: Optional[KnowledgebaseSourceType] = Field(
        None,
        description="Type of knowledgebase source"
    )
    source_path: Optional[str] = Field(
        None,
        min_length=1,
        max_length=2000,
        description="Path or URL to the knowledgebase source"
    )
    display_name: Optional[str] = Field(
        None,
        min_length=1,
        max_length=255,
        description="Human-readable name for the knowledgebase source"
    )
    metadata: Optional[dict[str, Any]] = Field(
        None,
        description="Additional metadata for the knowledgebase source"
    )
    excluded_sources: Optional[List[int]] = Field(
        None,
        description="List of inherited source IDs to exclude"
    )


# ============================================================================
# Audit Log
# ============================================================================

class AuditLogEntry(BaseModel):
    """Audit log entry for tracking workspace configuration changes.
    
    Requirement 25.2: THE System SHALL store audit entries with fields:
    id, workspace_id, change_type, entity_type, entity_id, old_value,
    new_value, changed_by, changed_at.
    """
    id: str = Field(..., description="Unique identifier for the audit log entry")
    workspace_id: str = Field(..., description="ID of the workspace where the change occurred")
    change_type: ChangeType = Field(..., description="Type of change that was made")
    entity_type: EntityType = Field(..., description="Type of entity that was changed")
    entity_id: str = Field(..., description="ID of the entity that was changed")
    old_value: Optional[str] = Field(
        None,
        description="Previous value before the change (JSON serialized)"
    )
    new_value: Optional[str] = Field(
        None,
        description="New value after the change (JSON serialized)"
    )
    changed_by: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="User identifier who made the change"
    )
    changed_at: datetime = Field(..., description="Timestamp when the change was made")


class AuditLogCreate(BaseModel):
    """Request model for creating a new audit log entry.
    
    Used internally when configuration changes are made.
    """
    workspace_id: str = Field(..., description="ID of the workspace where the change occurred")
    change_type: ChangeType = Field(..., description="Type of change that was made")
    entity_type: EntityType = Field(..., description="Type of entity that was changed")
    entity_id: str = Field(..., description="ID of the entity that was changed")
    old_value: Optional[str] = Field(
        None,
        description="Previous value before the change (JSON serialized)"
    )
    new_value: Optional[str] = Field(
        None,
        description="New value after the change (JSON serialized)"
    )
    changed_by: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="User identifier who made the change"
    )


# ============================================================================
# Policy Violation (for 409 responses)
# ============================================================================

class PolicyViolation(BaseModel):
    """Individual policy violation for execution blocking.
    
    Requirement 34.7: THE API SHALL return a 409 Conflict status with detailed
    policy_violations array when execution is blocked.
    """
    type: PolicyViolationType = Field(..., description="Type of capability that is missing")
    id: str = Field(..., description="ID of the missing capability")
    name: str = Field(..., description="Display name of the missing capability")
    reason: str = Field(..., description="Reason why the capability is unavailable")


class PolicyViolationResponse(BaseModel):
    """Response model for policy violation errors (409 Conflict).
    
    Requirement 34.7: THE API SHALL return a 409 Conflict status with detailed
    policy_violations array when execution is blocked.
    
    Requirement 26.3: THE System SHALL display a clear error message listing
    which Skills or MCPs are required but disabled.
    """
    code: str = Field(
        default="POLICY_VIOLATION",
        description="Error code for policy violation"
    )
    message: str = Field(
        default="Execution blocked: required capabilities are disabled",
        description="Human-readable error message"
    )
    detail: str = Field(
        default="Task requires capabilities that are disabled in this workspace",
        description="Additional context about the error"
    )
    policy_violations: List[PolicyViolation] = Field(
        ...,
        description="List of policy violations that caused the block"
    )
    suggested_action: str = Field(
        default="Enable required capabilities in workspace settings",
        description="Suggested action to resolve the issue"
    )


# ============================================================================
# Effective Configuration (computed at runtime)
# ============================================================================

class EffectiveSkillsResponse(BaseModel):
    """Response model for effective skills computed using intersection model.
    
    Requirement 16.5: WHEN an agent executes in a workspace, THE System SHALL
    compute effective Skills as the intersection of SwarmWS allowed Skills and
    workspace allowed Skills.
    """
    workspace_id: str = Field(..., description="ID of the workspace")
    skills: List[WorkspaceSkillConfig] = Field(
        ...,
        description="List of effective skills for the workspace"
    )


class EffectiveMcpsResponse(BaseModel):
    """Response model for effective MCPs computed using intersection model.
    
    Requirement 17.5: WHEN an agent executes in a workspace, THE System SHALL
    compute effective MCP servers as the intersection of SwarmWS allowed MCPs
    and workspace allowed MCPs.
    """
    workspace_id: str = Field(..., description="ID of the workspace")
    mcps: List[WorkspaceMcpConfig] = Field(
        ...,
        description="List of effective MCP servers for the workspace"
    )


class EffectiveKnowledgebasesResponse(BaseModel):
    """Response model for effective knowledgebases computed using union model.
    
    Requirement 18.4: WHEN an agent executes in a workspace, THE System SHALL
    compute effective Knowledgebase using the two-step algorithm:
    (1) union of SwarmWS and workspace sources, (2) minus workspace excluded sources.
    """
    workspace_id: str = Field(..., description="ID of the workspace")
    knowledgebases: List[WorkspaceKnowledgebaseConfig] = Field(
        ...,
        description="List of effective knowledgebase sources for the workspace"
    )
