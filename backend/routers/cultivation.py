"""DDD Cultivation API — list, approve, and reject proposals.

Provides the backend for the Approval UX in the briefing/session.
Proposals are JSON files in Projects/<project>/.artifacts/proposals/.

Endpoints:
    GET  /api/cultivation/proposals?project=SwarmAI  — list pending
    POST /api/cultivation/proposals/{id}/approve     — approve + apply
    POST /api/cultivation/proposals/{id}/reject      — reject + archive
"""

from __future__ import annotations

import fcntl
import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from core.initialization_manager import initialization_manager
from core.ddd_cultivation import (
    AWAITING_HUMAN_STATUSES,
    CultivationProposal,
    read_pending_proposals,
    apply_to_ddd,
    apply_retire_proposal,
    log_application,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cultivation", tags=["cultivation"])


def _resolve_project_dir(project: str) -> Path:
    """Resolve + VALIDATE a `project` query param to its Projects/<project> dir.

    The `project` param is attacker-controllable on these unauthenticated
    endpoints. Without validation, `project="../.."` escapes the Projects/ tree
    (the sink read_pending_proposals globs `<dir>/.artifacts/proposals/*.json`,
    so traversal = arbitrary proposal-JSON exfiltration). Fail-closed: reject any
    name with a path separator or `..` component, then assert the resolved path is
    contained under Projects/. run_24d9f714 (adversarial security MED): the str→Path
    fix made this pre-existing traversal reachable, so the guard lands with it and
    covers all three endpoints (list/approve/reject) that share this sink.
    """
    ws_path = initialization_manager.get_cached_workspace_path()
    if not ws_path:
        raise HTTPException(status_code=503, detail="Workspace not initialized")

    # Reject traversal BEFORE any filesystem touch. A legit project is a single
    # path segment (no "/", "\\", or ".." component).
    if "/" in project or "\\" in project or ".." in Path(project).parts:
        raise HTTPException(status_code=400, detail="Invalid project name")

    root = Path(ws_path)
    projects_root = (root / "Projects").resolve()
    project_dir = (projects_root / project).resolve()
    # Belt-and-suspenders: even if the checks above are bypassed, the resolved
    # path MUST stay under Projects/ (fail-closed containment).
    if project_dir != projects_root and projects_root not in project_dir.parents:
        raise HTTPException(status_code=400, detail="Invalid project name")
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    return project_dir


@router.get("/proposals")
async def list_proposals(project: str = Query(default="SwarmAI")):
    """List all pending (non-expired) DDD cultivation proposals for a project."""
    # Validate + resolve (fail-closed traversal guard); root is its parent's parent.
    project_dir = _resolve_project_dir(project)
    root = project_dir.parent.parent

    # read_pending_proposals(workspace_dir: Path, ...) does `workspace_dir / "Projects"` —
    # pass the Path, never str(root) (str / str → TypeError → 500). run_24d9f714.
    pending = read_pending_proposals(root, project)

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
    # Validate + resolve (fail-closed traversal guard, run_24d9f714).
    project_dir = _resolve_project_dir(project)

    # Serialize find→apply→mark for this id (run_93594880): a concurrent
    # approve+reject must not interleave to a torn status. 409 on contention.
    with _proposal_lock(project_dir, proposal_id):
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

        # Update status in file (inside the lock — the mark is part of the
        # critical section, not a post-release write).
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
    # Validate + resolve (fail-closed traversal guard, run_24d9f714).
    project_dir = _resolve_project_dir(project)

    # Same per-proposal lock as approve (run_93594880): reject must not race a
    # concurrent approve on the same id to a torn status.
    with _proposal_lock(project_dir, proposal_id):
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
    """Find an ACTIONABLE proposal by ID in the project's proposals directory.

    Returns the proposal ONLY when it is still awaiting a human decision — status
    in AWAITING_HUMAN_STATUSES and not expired. A terminal (applied/rejected) or
    expired proposal returns None → the approve/reject handlers 404 on it. This is
    the by-id twin of read_pending_proposals' list-view filter, reusing the SAME
    AWAITING_HUMAN_STATUSES constant so a proposal hidden from the list can never
    be re-approved by id (run_93594880: without this, a rejected proposal was
    re-approvable and an expired one was approvable though invisible to the list).
    """
    proposals_dir = project_dir / ".artifacts" / "proposals"
    if not proposals_dir.exists():
        return None

    for f in proposals_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("id") == proposal_id:
                proposal = CultivationProposal.from_dict(data)
                if proposal.status not in AWAITING_HUMAN_STATUSES:
                    return None  # terminal (applied/rejected) — not actionable
                if proposal.is_expired():
                    return None  # past TTL — parity with read_pending_proposals
                return proposal
        except (json.JSONDecodeError, OSError, KeyError):
            continue

    return None


@contextmanager
def _proposal_lock(project_dir: Path, proposal_id: str):
    """Serialize the find→apply→mark critical section for ONE proposal id.

    Per-proposal advisory flock on .artifacts/proposals/{id}.lock (distinct ids
    stay parallel). Non-blocking: a concurrent holder → HTTPException(409) rather
    than an event-loop stall (LOCK_NB returns immediately, safe to call in an async
    handler). Without this, two concurrent approve/reject on the same id both pass
    _find_proposal (both see it actionable) and race the status write (run_93594880).

    ⚠️ Deliberately does NOT unlink the .lock file on release — unlinking a flock'd
    path is the inode-divergence race (run_edcfd0e5 / build.md MECHANISM note): a
    waiter would re-create a NEW inode and both holders would "own the lock". The
    stray .lock sidecar is harmless; leave it (matches ddd_orchestrator.py's
    corrected pattern, NOT apply_to_ddd's latent unlink). Lock order is
    proposal(outer) → doc(inner, taken by apply_to_ddd) — no cycle.
    """
    lock_path = project_dir / ".artifacts" / "proposals" / f"{proposal_id}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = None
    try:
        lock_fd = open(lock_path, "w")  # noqa: SIM115
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        if lock_fd:
            lock_fd.close()
        raise HTTPException(
            status_code=409,
            detail="Proposal is being modified by another request.",
        )
    try:
        yield
    finally:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fd.close()  # NO unlink — see docstring (inode race)


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
