"""Workspace configuration API endpoints for Skills, MCPs, Knowledgebases, Context, and Audit Log.

This module provides endpoints for managing workspace-specific configurations:
- Skills: GET/PUT using intersection model (swarmws_enabled ∩ workspace_enabled)
- MCPs: GET/PUT using intersection model (swarmws_enabled ∩ workspace_enabled)
- Knowledgebases: GET/POST/PUT/DELETE using union model with exclusions
- Context: GET/PUT for context.md, POST for compression
- Audit Log: GET with pagination

Requirements: 19.6, 19.7, 19.8, 25.5, 29.9, 29.10
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from schemas.workspace_config import (
    WorkspaceSkillConfig,
    WorkspaceSkillConfigUpdate,
    WorkspaceMcpConfig,
    WorkspaceMcpConfigUpdate,
    WorkspaceKnowledgebaseConfig,
    WorkspaceKnowledgebaseCreate,
    WorkspaceKnowledgebaseUpdate,
    AuditLogEntry,
    ChangeType,
    EntityType,
)
from core.context_manager import context_manager
from core.audit_manager import audit_manager
from core.skill_manager import skill_manager
from database import db

logger = logging.getLogger(__name__)

router = APIRouter()


def _dict_to_kb_config(data: dict) -> WorkspaceKnowledgebaseConfig:
    """Convert a database dict to WorkspaceKnowledgebaseConfig."""
    excluded = data.get("excluded_sources")
    if isinstance(excluded, str):
        try:
            excluded = json.loads(excluded)
        except (json.JSONDecodeError, TypeError):
            excluded = None
    if isinstance(excluded, list):
        int_excluded = []
        for item in excluded:
            try:
                int_excluded.append(int(item))
            except (ValueError, TypeError):
                pass
        excluded = int_excluded if int_excluded else None

    metadata = data.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            metadata = None

    return WorkspaceKnowledgebaseConfig(
        id=data["id"],
        source_type=data["source_type"],
        source_path=data["source_path"],
        display_name=data["display_name"],
        metadata=metadata,
        excluded_sources=excluded,
    )


class ContextContent(BaseModel):
    """Request/response model for context content."""
    content: str


# ============================================================================
# Skills Endpoints (Intersection Model)
# ============================================================================


@router.get("/{workspace_id}/skills", response_model=list[WorkspaceSkillConfig])
async def get_effective_skills(workspace_id: str):
    """Get effective skills for a workspace.

    Returns all enabled skills for the workspace from the workspace_skills table.

    Requirement 19.6: GET /api/workspaces/{id}/skills.
    """
    try:
        # Get all skills from filesystem cache
        all_skills = await skill_manager.get_cache()
        
        ws_configs = await db.workspace_skills.list_by_workspace(workspace_id)
        enabled_ids = {c["skill_id"] for c in ws_configs if c.get("enabled", 1)}

        configs = []
        for skill_id in enabled_ids:
            skill_info = all_skills.get(skill_id)
            if skill_info:
                configs.append(WorkspaceSkillConfig(
                    skill_id=skill_info.folder_name,
                    skill_name=skill_info.name,
                    enabled=True,
                    is_privileged=False,  # Filesystem skills don't have privileged flag
                ))
        return configs
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{workspace_id}/skills", response_model=list[WorkspaceSkillConfig])
async def update_skill_configs(workspace_id: str, data: WorkspaceSkillConfigUpdate):
    """Update skill configurations for a workspace.

    Updates the enabled/disabled state of skills for the workspace and
    logs changes to the audit trail.

    Requirement 19.6: PUT /api/workspaces/{id}/skills.
    """
    from datetime import datetime, timezone
    from uuid import uuid4

    try:
        # Snapshot current state
        ws_configs = await db.workspace_skills.list_by_workspace(workspace_id)
        current_state = {c["skill_id"]: bool(c.get("enabled", 1)) for c in ws_configs}
        existing_by_skill = {c["skill_id"]: c for c in ws_configs}

        now = datetime.now(timezone.utc).isoformat()

        for config in data.configs:
            old_enabled = current_state.get(config.skill_id)
            existing = existing_by_skill.get(config.skill_id)

            if existing:
                await db.workspace_skills.update(existing["id"], {
                    "enabled": 1 if config.enabled else 0,
                    "updated_at": now,
                })
            else:
                await db.workspace_skills.put({
                    "id": str(uuid4()),
                    "workspace_id": workspace_id,
                    "skill_id": config.skill_id,
                    "enabled": 1 if config.enabled else 0,
                    "created_at": now,
                    "updated_at": now,
                })

            # Log the change if state actually changed
            if old_enabled is None or old_enabled != config.enabled:
                change_type = ChangeType.ENABLED if config.enabled else ChangeType.DISABLED
                await audit_manager.log_change(
                    workspace_id=workspace_id,
                    change_type=change_type,
                    entity_type=EntityType.SKILL,
                    entity_id=config.skill_id,
                    old_value=json.dumps({"enabled": old_enabled}),
                    new_value=json.dumps({"enabled": config.enabled}),
                    changed_by="user",
                )

        # Return updated effective skills
        return await get_effective_skills(workspace_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================
# MCPs Endpoints (Intersection Model)
# ============================================================================


@router.get("/{workspace_id}/mcps", response_model=list[WorkspaceMcpConfig])
async def get_effective_mcps(workspace_id: str):
    """Get effective MCP servers for a workspace.

    Returns all enabled MCPs for the workspace from the workspace_mcps table.

    Requirement 19.7: GET /api/workspaces/{id}/mcps.
    """
    try:
        all_mcps = await db.mcp_servers.list()
        mcps_by_id = {m["id"]: m for m in all_mcps}

        ws_configs = await db.workspace_mcps.list_by_workspace(workspace_id)
        enabled_ids = {c["mcp_server_id"] for c in ws_configs if c.get("enabled", 1)}

        configs = []
        for mcp_id in enabled_ids:
            mcp = mcps_by_id.get(mcp_id)
            if mcp:
                configs.append(WorkspaceMcpConfig(
                    mcp_server_id=mcp["id"],
                    mcp_server_name=mcp.get("name", ""),
                    enabled=True,
                    is_privileged=bool(mcp.get("is_privileged", 0)),
                ))
        return configs
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{workspace_id}/mcps", response_model=list[WorkspaceMcpConfig])
async def update_mcp_configs(workspace_id: str, data: WorkspaceMcpConfigUpdate):
    """Update MCP server configurations for a workspace.

    Updates the enabled/disabled state of MCP servers for the workspace
    and logs changes to the audit trail.

    Requirement 19.7: PUT /api/workspaces/{id}/mcps.
    """
    from datetime import datetime, timezone
    from uuid import uuid4

    try:
        # Snapshot current state
        ws_configs = await db.workspace_mcps.list_by_workspace(workspace_id)
        current_state = {c["mcp_server_id"]: bool(c.get("enabled", 1)) for c in ws_configs}
        existing_by_mcp = {c["mcp_server_id"]: c for c in ws_configs}

        now = datetime.now(timezone.utc).isoformat()

        for config in data.configs:
            old_enabled = current_state.get(config.mcp_server_id)
            existing = existing_by_mcp.get(config.mcp_server_id)

            if existing:
                await db.workspace_mcps.update(existing["id"], {
                    "enabled": 1 if config.enabled else 0,
                    "updated_at": now,
                })
            else:
                await db.workspace_mcps.put({
                    "id": str(uuid4()),
                    "workspace_id": workspace_id,
                    "mcp_server_id": config.mcp_server_id,
                    "enabled": 1 if config.enabled else 0,
                    "created_at": now,
                    "updated_at": now,
                })

            if old_enabled is None or old_enabled != config.enabled:
                change_type = ChangeType.ENABLED if config.enabled else ChangeType.DISABLED
                await audit_manager.log_change(
                    workspace_id=workspace_id,
                    change_type=change_type,
                    entity_type=EntityType.MCP,
                    entity_id=config.mcp_server_id,
                    old_value=json.dumps({"enabled": old_enabled}),
                    new_value=json.dumps({"enabled": config.enabled}),
                    changed_by="user",
                )

        # Return updated effective MCPs
        return await get_effective_mcps(workspace_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================
# Knowledgebases Endpoints (Union Model with Exclusions)
# ============================================================================


@router.get("/{workspace_id}/knowledgebases", response_model=list[WorkspaceKnowledgebaseConfig])
async def get_knowledgebases(workspace_id: str):
    """Get knowledgebases for a workspace.

    Returns all knowledgebases associated with the workspace.

    Requirement 19.8: GET /api/workspaces/{id}/knowledgebases.
    """
    try:
        kbs = await db.workspace_knowledgebases.list_by_workspace(workspace_id)
        return [_dict_to_kb_config(kb) for kb in kbs]
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{workspace_id}/knowledgebases", response_model=WorkspaceKnowledgebaseConfig, status_code=201)
async def add_knowledgebase(workspace_id: str, data: WorkspaceKnowledgebaseCreate):
    """Add a new knowledgebase source to a workspace.

    Requirement 19.8: POST /api/workspaces/{id}/knowledgebases.
    """
    from datetime import datetime, timezone
    from uuid import uuid4

    now = datetime.now(timezone.utc).isoformat()
    kb_id = str(uuid4())

    kb_dict = {
        "id": kb_id,
        "workspace_id": workspace_id,
        "source_type": data.source_type.value if hasattr(data.source_type, "value") else data.source_type,
        "source_path": data.source_path,
        "display_name": data.display_name,
        "metadata": json.dumps(data.metadata) if data.metadata else None,
        "excluded_sources": json.dumps(data.excluded_sources) if data.excluded_sources else None,
        "created_at": now,
        "updated_at": now,
    }

    await db.workspace_knowledgebases.put(kb_dict)

    await audit_manager.log_change(
        workspace_id=workspace_id,
        change_type=ChangeType.ADDED,
        entity_type=EntityType.KNOWLEDGEBASE,
        entity_id=kb_id,
        new_value=json.dumps({"display_name": data.display_name, "source_type": kb_dict["source_type"]}),
        changed_by="user",
    )

    return WorkspaceKnowledgebaseConfig(
        id=kb_id,
        source_type=data.source_type,
        source_path=data.source_path,
        display_name=data.display_name,
        metadata=data.metadata,
        excluded_sources=data.excluded_sources,
    )


@router.put("/{workspace_id}/knowledgebases/{kb_id}", response_model=WorkspaceKnowledgebaseConfig)
async def update_knowledgebase(workspace_id: str, kb_id: str, data: WorkspaceKnowledgebaseUpdate):
    """Update an existing knowledgebase source.

    Requirement 19.8: PUT /api/workspaces/{id}/knowledgebases/{kb_id}.
    """
    existing = await db.workspace_knowledgebases.get(kb_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Knowledgebase {kb_id} not found")

    updates = {}
    if data.source_type is not None:
        updates["source_type"] = data.source_type.value if hasattr(data.source_type, "value") else data.source_type
    if data.source_path is not None:
        updates["source_path"] = data.source_path
    if data.display_name is not None:
        updates["display_name"] = data.display_name
    if data.metadata is not None:
        updates["metadata"] = json.dumps(data.metadata)
    if data.excluded_sources is not None:
        updates["excluded_sources"] = json.dumps(data.excluded_sources)

    if updates:
        updated = await db.workspace_knowledgebases.update(kb_id, updates)
    else:
        updated = existing

    if not updated:
        raise HTTPException(status_code=404, detail=f"Knowledgebase {kb_id} not found")

    await audit_manager.log_change(
        workspace_id=workspace_id,
        change_type=ChangeType.UPDATED,
        entity_type=EntityType.KNOWLEDGEBASE,
        entity_id=kb_id,
        old_value=json.dumps({"display_name": existing.get("display_name")}),
        new_value=json.dumps({"display_name": updated.get("display_name", existing.get("display_name"))}),
        changed_by="user",
    )

    return _dict_to_kb_config(updated)


@router.delete("/{workspace_id}/knowledgebases/{kb_id}")
async def delete_knowledgebase(workspace_id: str, kb_id: str):
    """Delete a knowledgebase source from a workspace.

    Requirement 19.8: DELETE /api/workspaces/{id}/knowledgebases/{kb_id}.
    """
    existing = await db.workspace_knowledgebases.get(kb_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Knowledgebase {kb_id} not found")

    deleted = await db.workspace_knowledgebases.delete(kb_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Knowledgebase {kb_id} not found")

    await audit_manager.log_change(
        workspace_id=workspace_id,
        change_type=ChangeType.REMOVED,
        entity_type=EntityType.KNOWLEDGEBASE,
        entity_id=kb_id,
        old_value=json.dumps({"display_name": existing.get("display_name")}),
        changed_by="user",
    )

    return {"status": "deleted", "knowledgebase_id": kb_id}



# ============================================================================
# Context Endpoints
# ============================================================================


@router.get("/{workspace_id}/context")
async def get_context(workspace_id: str):
    """Get the context.md content for a workspace.

    Requirement 29.9: GET /api/workspaces/{id}/context.
    """
    try:
        content = await context_manager.get_context(workspace_id)
        return {"content": content}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{workspace_id}/context")
async def update_context(workspace_id: str, data: ContextContent):
    """Update the context.md content for a workspace.

    Requirement 29.9: PUT /api/workspaces/{id}/context.
    """
    try:
        await context_manager.update_context(workspace_id, data.content)
        return {"status": "updated", "workspace_id": workspace_id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{workspace_id}/context/compress")
async def compress_context(workspace_id: str):
    """Trigger compression of context.md into compressed-context.md.

    Requirement 29.10: POST /api/workspaces/{id}/context/compress.
    """
    try:
        compressed = await context_manager.compress_context(workspace_id)
        return {"status": "compressed", "workspace_id": workspace_id, "content": compressed}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ============================================================================
# Audit Log Endpoints
# ============================================================================


@router.get("/{workspace_id}/audit-log")
async def get_audit_log(
    workspace_id: str,
    limit: int = Query(50, ge=1, le=200, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
):
    """Get audit log entries for a workspace with pagination.

    Requirement 25.5: GET /api/workspaces/{id}/audit-log.
    """
    result = await audit_manager.get_audit_log(
        workspace_id=workspace_id,
        limit=limit,
        offset=offset,
    )
    # Serialize AuditLogEntry models to dicts for JSON response
    result["entries"] = [
        entry.model_dump(mode="json") if hasattr(entry, "model_dump") else entry
        for entry in result.get("entries", [])
    ]
    return result
