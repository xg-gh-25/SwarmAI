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
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from core.initialization_manager import initialization_manager
from core.proposal_feedback import (
    REJECTION_REASON_KEY,
    STATUS_APPROVED,
    STATUS_REJECTED,
)
from core.ddd_cultivation import (
    AWAITING_HUMAN_STATUSES,
    CultivationProposal,
    read_pending_proposals,
    apply_to_ddd,
    log_application,
    _is_safe_section_name,
)
from core.persist_routing import ROUTING_TABLE
from utils.file_lock import flock_exclusive_nb, flock_unlock
from core.project_registry import DEFAULT_PROJECT_NAME

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cultivation", tags=["cultivation"])

# Every doc the routing table can legitimately target. DERIVED, never hand-listed, so a
# new route cannot silently become un-approvable at this boundary. Broader than the
# 4-name canonical tuple on purpose — see the note at the re-target guard below.
_ROUTABLE_DOCS = frozenset(
    r["doc"] for r in ROUTING_TABLE.values() if r.get("doc")
)


def _query_str(value, default=None):
    """Return ``value`` if it is a real string, else ``default``.

    FastAPI substitutes a ``Query(...)`` default only when a handler is invoked through
    the ASGI app. A DIRECT call — a test, or any internal caller — leaves the raw
    ``Query`` object in the parameter, and that object is TRUTHY. So the idiomatic
    ``if some_param:`` treated the sentinel as a caller-supplied value.

    This was latent for as long as the branches merely ASSIGNED the value, and became
    visible the moment one of them started validating it. It is a class bug, not a
    local one: ``reject_proposal``'s ``reason`` is written into the proposal JSON, where
    the sentinel raises ``TypeError: Object of type Query is not JSON serializable``
    mid-write, and ``list_proposals``' ``project`` reaches a ``"/" in project`` test
    that raises on a non-string. Normalising in one shared place is what keeps the three
    handlers from drifting apart again.
    """
    return value if isinstance(value, str) else default


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
    # Normalise the sentinel first (see _query_str): a direct caller can pass the raw
    # Query object, and the separator test below raises TypeError on a non-string —
    # which would turn a validation guard into a 500 rather than a 400.
    project = _query_str(project, DEFAULT_PROJECT_NAME)
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
async def list_proposals(project: str = Query(default=DEFAULT_PROJECT_NAME)):
    """List all pending (non-expired) DDD cultivation proposals for a project."""
    # Normalise at the ENTRY, not just inside the resolver: `project` is used again
    # below (read_pending_proposals), so a resolver-local normalisation would protect
    # the guard and still let the raw Query sentinel reach a path join. See _query_str.
    project = _query_str(project, DEFAULT_PROJECT_NAME)
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
    project: str = Query(default=DEFAULT_PROJECT_NAME),
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
        #
        # "Explicitly supplied" must mean a real STRING. FastAPI substitutes the
        # Query(...) default only when the handler is called through the ASGI app; a
        # direct call (tests, or any internal caller) leaves the raw Query object in
        # place, and it is truthy — so a bare `if target_doc:` treated the sentinel as
        # a user-supplied value. That was harmless while the branch merely assigned,
        # and became visible the moment it validated. Normalise once, here.
        target_doc = _query_str(target_doc)
        target_section = _query_str(target_section)
        #
        # Both overrides are attacker-controllable on this unauthenticated endpoint —
        # the same property that made `project` need _resolve_project_dir above. They
        # are VALIDATED here as an outer layer; the load-bearing containment lives in
        # the appliers themselves, because a proposal that already reached disk is
        # re-hydrated verbatim by CultivationProposal.from_dict and never passes back
        # through this handler.
        #
        # target_doc is checked against the ROUTING TABLE's doc set, not the narrower
        # 4-name canonical tuple: the routing table legitimately names MEMORY.md /
        # EVOLUTION.md / KNOWLEDGE.md too. Those do not exist inside a project, so an
        # apply still reports doc_missing — but that is a truthful "no such doc", and
        # turning it into "invalid parameter" here would send the next debugger to the
        # wrong layer. Membership is case-SENSITIVE on purpose: on a case-insensitive
        # filesystem "tech.md" resolves to a different, unmanaged file than "TECH.md".
        if target_doc:
            if target_doc not in _ROUTABLE_DOCS:
                raise HTTPException(
                    status_code=400,
                    detail=f"target_doc must be a routable DDD document, got '{target_doc}'",
                )
            proposal.target_doc = target_doc
        if target_section:
            # A section name is interpolated into a `## {name}` heading downstream, so a
            # line break would forge additional headings — enough to fabricate a whole
            # governance section from one approve call.
            if not _is_safe_section_name(target_section):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "target_section must be a non-blank single line without a "
                        "leading heading marker or HTML-comment/maturity syntax"
                    ),
                )
            proposal.target_section = target_section

        # OFFLOAD to a worker thread (run_06350217, Gate-2 security): the applier does
        # a SYNCHRONOUS fcntl/md_lock acquire + read-modify-write; running it inline
        # would block the single uvicorn event loop for ALL clients while a background
        # DDD channel holds the same <doc>.md.lock (mirror improvement_writeback_hook).
        import asyncio
        # "applied" and "created_section" both mean the lesson landed successfully
        # (created_section = the whitelisted heading was absent and auto-created).
        status = await asyncio.to_thread(apply_to_ddd, proposal, project_dir)
        success_states = ("applied", "created_section")
        # Admission root-fix backstop (run_97519f7c): a "duplicate" means the content
        # is ALREADY curated in the doc — the proposal is not a server error, it is a
        # settled no-op that must LEAVE the pending queue (the primary fix is
        # generation-side dedup in write_proposal; this clears any pre-existing dup a
        # human clicks). Terminate it as rejected + return 200 cleared, do NOT 500 and
        # leave it pending (the old behavior → unclearable churn).
        if status == "duplicate":
            mark_proposal_rejected(
                project_dir, proposal_id,
                reason="duplicate — content already present in the doc",
            )
            logger.info(
                "Cultivation proposal %s cleared as duplicate (already in %s#%s)",
                proposal_id, proposal.target_doc, proposal.target_section,
            )
            return {
                "status": "cleared",
                "proposal_id": proposal_id,
                "reason": "duplicate — already present, removed from queue",
            }
        if status not in success_states:
            # Rich diagnostic goes to the SERVER log (includes doc/section names);
            # the client gets a generic message so the API does not disclose the
            # internal DDD filesystem structure / section taxonomy to callers — the
            # cultivation router has no auth dependency (adversarial security MED).
            logger.warning(
                "Cultivation approve failed: proposal %s → %s#%s (status: %s)",
                proposal_id, proposal.target_doc, proposal.target_section, status,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Could not apply proposal (status: {status}).",
            )
        if status == "created_section":
            logger.warning(
                "Cultivation approve auto-created missing section: %s#%s (proposal %s)",
                proposal.target_doc, proposal.target_section, proposal_id,
            )

        # Update status in file (inside the lock — the mark is part of the
        # critical section, not a post-release write).
        mark_proposal_approved(project_dir, proposal_id)

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
    project: str = Query(default=DEFAULT_PROJECT_NAME),
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

        # Normalise before the value is PERSISTED: the raw Query sentinel is not JSON
        # serializable, so it would fail mid-write inside _update_proposal_status.
        mark_proposal_rejected(
            project_dir, proposal_id, reason=_query_str(reason)
        )

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
        flock_exclusive_nb(lock_fd)
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
            flock_unlock(lock_fd)
        except OSError:
            pass
        lock_fd.close()  # NO unlink — see docstring (inode race)


def mark_proposal_approved(project_dir: Path, proposal_id: str) -> None:
    """Persist a human APPROVAL outcome. The single writer of the success status.

    Named (rather than an inline literal at the endpoint) so that the producer and
    the consumer that measures channel precision — proposal_feedback's
    compute_channel_stats — sit on ONE definition, STATUS_APPROVED. The success arm
    was previously orphaned by exactly this seam: the endpoint wrote "applied" while
    the reader counted "approved", so precision read 0.0 forever and the auto-write
    threshold ratcheted up on a dead input. Route every approval through here.
    """
    _update_proposal_status(project_dir, proposal_id, STATUS_APPROVED)


def mark_proposal_rejected(
    project_dir: Path, proposal_id: str, reason: Optional[str] = None
) -> None:
    """Persist a human REJECTION outcome. The single writer of the reject status.

    Counterpart to mark_proposal_approved — see that docstring for why the
    vocabulary lives in one constant shared with the reader.
    """
    _update_proposal_status(project_dir, proposal_id, STATUS_REJECTED, reason=reason)


def _update_proposal_status(
    project_dir: Path,
    proposal_id: str,
    new_status: str,
    reason: Optional[str] = None,
) -> None:
    """Write a raw status value. Prefer mark_proposal_approved /
    mark_proposal_rejected — they bind the OUTCOME vocabulary to the constants the
    precision reader consumes. This low-level form stays for statuses that are not
    an approve/reject outcome."""
    proposals_dir = project_dir / ".artifacts" / "proposals"
    if not proposals_dir.exists():
        return

    for f in proposals_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("id") == proposal_id:
                data["status"] = new_status
                if reason:
                    # The shared key, for the same reason the status is shared:
                    # the precision reader buckets rejections by this field, and
                    # a second spelling here makes its breakdown silently empty.
                    data[REJECTION_REASON_KEY] = reason
                f.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                return
        except (json.JSONDecodeError, OSError):
            continue
