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
    apply_retire_proposal,
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
async def approve_proposal(
    proposal_id: str,
    project: str = Query(default="SwarmAI"),
    target_doc: Optional[str] = Query(default=None),
    target_section: Optional[str] = Query(default=None),
):
    """Approve a proposal: apply content to target DDD document.

    Approve-time RE-TARGET (capability C §9-D1, run_e346b8ed): a
    conversation-derived proposal carries only a SUGGESTED target_doc/section
    (the extractor never guess-writes). At approve time XG may override the
    target via ``target_doc`` / ``target_section`` — that is where attribution
    is actually decided, not by a channel→project binding. Omit both to apply
    to the proposal's suggested target unchanged (existing behavior for
    reflect/decision proposals).
    """
    ws_path = initialization_manager.get_cached_workspace_path()
    if not ws_path:
        raise HTTPException(status_code=503, detail="Workspace not initialized")

    root = Path(ws_path)
    project_dir = root / "Projects" / project

    proposal = _find_proposal(project_dir, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail=f"Proposal '{proposal_id}' not found")

    # Approve-time re-target: only override fields explicitly supplied (§9-D1).
    if target_doc:
        proposal.target_doc = target_doc
    if target_section:
        proposal.target_section = target_section

    # Dispatch on change_type (run_b8f10185): append → apply_to_ddd (the additive
    # path); retire/rewrite → apply_retire_proposal (reversible retire_entry:
    # archive + dated .bak + identity-strip). This is the ONLY apply path for a
    # destructive change reaching THIS router (an escalated retire the human is
    # approving). Confident retires auto-apply upstream in _cultivate_proposals
    # (run_ecc7a32b) and never reach here; this path is the human-gated remainder.
    if proposal.change_type in ("retire", "rewrite"):
        status = apply_retire_proposal(proposal, project_dir)
        success_states = ("retired", "rewritten")
    else:
        # Apply to DDD document (returns a status string, not a bool).
        # "applied" and "created_section" both mean the lesson landed successfully
        # (created_section = the whitelisted heading was absent and auto-created).
        status = apply_to_ddd(proposal, project_dir)
        success_states = ("applied", "created_section")
    if status not in success_states:
        # Rich diagnostic goes to the SERVER log (includes doc/section names);
        # the client gets a generic message so the API does not disclose the
        # internal DDD filesystem structure / section taxonomy to callers — the
        # cultivation router has no auth dependency (adversarial security MED).
        logger.warning(
            "Cultivation approve failed: proposal %s → %s#%s (change_type: %s, status: %s)",
            proposal_id, proposal.target_doc, proposal.target_section,
            proposal.change_type, status,
        )
        client_detail = {
            "duplicate": "Proposal content is already present (duplicate).",
            "no_target": "Retire proposal has no located target entry.",
        }.get(status, f"Could not apply proposal (status: {status}).")
        # Gate-2 LOW (run_b8f10185): client-correctable retire outcomes (fail-loud
        # no-match / ambiguous / keep-class refused / missing target) are 422, not
        # 500 — they reflect a bad proposal the caller can fix, not a server fault.
        client_correctable = status == "no_target" or status.startswith("retire_failed:")
        raise HTTPException(
            status_code=422 if client_correctable else 500,
            detail=client_detail,
        )
    if status == "created_section":
        logger.warning(
            "Cultivation approve auto-created missing section: %s#%s (proposal %s)",
            proposal.target_doc, proposal.target_section, proposal_id,
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
