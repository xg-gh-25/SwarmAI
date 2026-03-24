"""Escalation Protocol API endpoints.

Provides REST endpoints for managing escalations:

- ``GET  /api/escalations/{project}``          — list open escalations
- ``POST /api/escalations/{project}/{id}/resolve`` — resolve an escalation
- ``GET  /api/escalations/{project}/{id}``     — get a single escalation

Requirements: escalation-protocol-v2
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.escalation import (
    Level,
    build_sse_event,
    create_radar_todo,
    get_open_escalations,
    load_escalation,
    resolve,
    resolve_expired,
    save_escalation,
    mark_todo_handled,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/escalations", tags=["escalations"])


def _get_workspace_root() -> Path:
    """Resolve SwarmWS path. Function (not constant) for testability."""
    return Path.home() / ".swarm-ai" / "SwarmWS"


class ResolveRequest(BaseModel):
    """Request body for resolving an escalation."""
    resolution: str
    resolved_by: str = "user"


def _validate_project(project: str) -> str:
    """Validate project name to prevent path traversal.

    Rejects names containing path separators, parent references,
    or null bytes. Returns the validated name or raises HTTPException.
    """
    if not project or ".." in project or "/" in project or "\\" in project or "\0" in project:
        raise HTTPException(status_code=400, detail=f"Invalid project name: {project!r}")
    return project


@router.get("/{project}")
async def list_open(project: str):
    """List all open escalations for a project.

    Also runs timeout resolution for expired L1 CONSULTs.
    """
    project = _validate_project(project)
    # Auto-resolve expired L1s first (idempotent)
    auto_resolved = resolve_expired(_get_workspace_root(), project)
    if auto_resolved:
        logger.info(
            "escalation.auto_resolved %d expired L1 consultations for project %s",
            len(auto_resolved), project,
        )

    open_escs = get_open_escalations(_get_workspace_root(), project)
    return {
        "project": project,
        "open": [build_sse_event(e) for e in open_escs],
        "auto_resolved": [build_sse_event(e) for e in auto_resolved],
    }


@router.get("/{project}/{escalation_id}")
async def get_escalation(project: str, escalation_id: str):
    """Get a single escalation by ID."""
    project = _validate_project(project)
    esc = load_escalation(_get_workspace_root(), project, escalation_id)
    if esc is None:
        raise HTTPException(status_code=404, detail=f"Escalation {escalation_id} not found")
    return build_sse_event(esc)


@router.post("/{project}/{escalation_id}/resolve")
async def resolve_escalation(project: str, escalation_id: str, body: ResolveRequest):
    """Resolve an open escalation with the human's decision.

    Also marks the associated Radar todo as handled.
    """
    project = _validate_project(project)
    esc = load_escalation(_get_workspace_root(), project, escalation_id)
    if esc is None:
        raise HTTPException(status_code=404, detail=f"Escalation {escalation_id} not found")

    if esc.status != "open":
        raise HTTPException(
            status_code=409,
            detail=f"Escalation {escalation_id} is already {esc.status}",
        )

    resolved_esc = resolve(esc, resolution=body.resolution, resolved_by=body.resolved_by)
    save_escalation(_get_workspace_root(), resolved_esc)

    # Mark associated Radar todo as handled
    mark_todo_handled(escalation_id)

    logger.info(
        "escalation.resolved id=%s resolution=%s resolved_by=%s project=%s",
        escalation_id, body.resolution, body.resolved_by, project,
    )

    return {
        "status": "resolved",
        "escalation": build_sse_event(resolved_esc),
    }
