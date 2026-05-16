"""DDD Cultivation API — list, approve, and reject proposals.

Provides the backend for the Approval UX in the briefing/session.
Proposals are JSON files in Projects/<project>/.artifacts/proposals/.

Endpoints:
    GET  /api/cultivation/proposals?project=SwarmAI  — list pending
    POST /api/cultivation/proposals/{id}/approve     — approve + apply
    POST /api/cultivation/proposals/{id}/reject      — reject + archive
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from core.initialization_manager import initialization_manager
from core.ddd_cultivation import (
    CultivationProposal,
    read_pending_proposals,
    apply_to_ddd,
    log_application,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cultivation", tags=["cultivation"])


@router.get("/proposals")
async def list_proposals(project: str = Query(default="SwarmAI")):
    """List all pending (non-expired) DDD cultivation proposals for a project."""
    ws_path = initialization_manager.get_cached_workspace_path()
    if not ws_path:
        raise HTTPException(status_code=503, detail="Workspace not initialized")

    root = Path(ws_path)
    project_dir = root / "Projects" / project
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")

    pending = read_pending_proposals(str(root), project)

    return {
        "project": project,
        "count": len(pending),
        "proposals": [p.to_dict() for p in pending],
    }


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: str, project: str = Query(default="SwarmAI")):
    """Approve a proposal: apply content to target DDD document."""
    ws_path = initialization_manager.get_cached_workspace_path()
    if not ws_path:
        raise HTTPException(status_code=503, detail="Workspace not initialized")

    root = Path(ws_path)
    project_dir = root / "Projects" / project

    proposal = _find_proposal(project_dir, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail=f"Proposal '{proposal_id}' not found")

    # Apply to DDD document
    success = apply_to_ddd(proposal, project_dir)
    if not success:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to apply proposal to {proposal.target_doc}#{proposal.target_section}",
        )

    # Update status in file
    _update_proposal_status(project_dir, proposal_id, "applied")

    # Log application
    log_application(proposal, project_dir)

    logger.info(
        "Cultivation proposal %s approved → %s#%s",
        proposal_id, proposal.target_doc, proposal.target_section,
    )

    return {
        "status": "applied",
        "proposal_id": proposal_id,
        "target": f"{proposal.target_doc}#{proposal.target_section}",
    }


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: str,
    project: str = Query(default="SwarmAI"),
    reason: Optional[str] = Query(default=None),
):
    """Reject a proposal: mark as rejected, archive for learning."""
    ws_path = initialization_manager.get_cached_workspace_path()
    if not ws_path:
        raise HTTPException(status_code=503, detail="Workspace not initialized")

    root = Path(ws_path)
    project_dir = root / "Projects" / project

    proposal = _find_proposal(project_dir, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail=f"Proposal '{proposal_id}' not found")

    _update_proposal_status(project_dir, proposal_id, "rejected", reason=reason)

    logger.info("Cultivation proposal %s rejected (reason: %s)", proposal_id, reason)

    return {
        "status": "rejected",
        "proposal_id": proposal_id,
        "reason": reason,
    }


def _find_proposal(project_dir: Path, proposal_id: str) -> Optional[CultivationProposal]:
    """Find a proposal by ID in the project's proposals directory."""
    proposals_dir = project_dir / ".artifacts" / "proposals"
    if not proposals_dir.exists():
        return None

    for f in proposals_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("id") == proposal_id:
                return CultivationProposal.from_dict(data)
        except (json.JSONDecodeError, OSError, KeyError):
            continue

    return None


def _update_proposal_status(
    project_dir: Path,
    proposal_id: str,
    new_status: str,
    reason: Optional[str] = None,
) -> None:
    """Update a proposal's status in its JSON file."""
    proposals_dir = project_dir / ".artifacts" / "proposals"
    if not proposals_dir.exists():
        return

    for f in proposals_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("id") == proposal_id:
                data["status"] = new_status
                if reason:
                    data["reject_reason"] = reason
                f.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                return
        except (json.JSONDecodeError, OSError):
            continue
