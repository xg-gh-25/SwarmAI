"""AttentionAuthority — the single READ-AGGREGATION layer behind the unified
"Need You" channel.

Design: Knowledge/Designs/2026-08-08-unified-need-you-channel-design.md

WHY this is a read-aggregation layer and NOT a new store/table:
    The 5 sources each own their lifecycle correctly (escalation has resolve,
    proposal has apply/reject, run has resume). A central "attention" table
    would force every source to dual-write → state drift — the exact lesson from
    2026-08-03-unified-review-trigger ("git is already the authority, don't build
    a new table"). So this module OWNS no state: it READS the 5 sources' existing
    entry points and NORMALIZES them into one shape.

The single output is `collect()` → `AttentionResult` (items + counts), consumed by:
    - GET /api/attention → frontend needs-you overlay
    - the same endpoint → agent SENSE ("show me / handle Need You")

Two tiers (design §2 — "does the user NOT-acting break something?"):
    - BLOCKING : work is STOPPED, nothing moves without the user
                 (L2 escalation · paused pipeline `pause_kind=="decision"` · circuit-broken job)
    - REVIEW   : work self-advances (L1 timeout auto-accepts); the user's input
                 is confirm-or-override (cultivation · governance · L1 consult)

Brain attribution (design §4 Gate-1 correction): every item carries the brain
(project) it belongs to, EXCEPT governance — governance proposals are
workspace-global OS-level rule/gate changes with NO project, so `brain=None`
(rendered in an "OS-level" group; excluded from any per-brain count).

Actions: NONE here. There is no /act endpoint. Each item carries a `dispatch`
payload; the action is "inject into a chat tab" via the EXISTING onItemClick
mechanism (design principle 3 — reuse, do not build a new dispatch channel).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional
import logging

logger = logging.getLogger(__name__)

# Circuit-breaker threshold — a job enters BLOCKING only when it has failed this
# many times in a row (matches jobs.scheduler check_circuit_breaker at
# _FAILURE_ALERT_THRESHOLD=3). This is a FILTER TIGHTENING vs the old frontend
# behaviour (`consecutive_failures > 0`): transient one-off failures self-heal on
# the next scheduled run and are NOT the user's problem — only a broken streak is.
_JOB_CIRCUIT_BREAKER_THRESHOLD = 3

# R3 闭环 dispatch: a fresh community digest/report surfaces as REVIEW for this many
# hours, then ages out (the window IS the state — no read-marker file, keeps collect()
# read-only). 20h covers the daily-scan cadence gap without perpetual re-surfacing.
_DIGEST_FRESH_HOURS = 20
# Which JobResults belong to the community engine (by job_id substring).
_COMMUNITY_JOB_MARKER = "github-community"

# Tiers
TIER_BLOCKING = "blocking"
TIER_REVIEW = "review"

# Human-readable phrasing for cognitive source-classes, kept in sync with the
# frontend CMBrainOverlay CLASS_PHRASE map. A governance proposal's raw class
# (CLASS_A/B/C/...) is meaningless to the user — this turns it into the failure
# pattern the rule is meant to prevent, so the "Need You" card reads as a sentence
# ("inferred without verifying · recurred 3×") instead of a bare type ("governance rule").
_CLASS_PHRASE = {
    "CLASS_A": "over-confidence → skipped a step",
    "CLASS_B": "inferred without verifying",
    "CLASS_C": "fixed the wrong layer",
    "SESSION_LOW_QUALITY": "low-quality session signal",
}


def _class_phrase(source_class: str) -> str:
    if not source_class:
        return "recurring pattern"
    return _CLASS_PHRASE.get(source_class, f"recurring pattern ({source_class})")


@dataclass
class AttentionItem:
    """One normalized "needs you" item, source-agnostic.

    `id` carries a source prefix (e.g. "escalation:esc_abc") so it is globally
    unique across the 5 sources. `brain` is the owning project, or None for
    OS-level (governance). `dispatch.message` is what gets injected into a chat
    tab when the user clicks the item (reusing onItemClick).
    """

    id: str
    source: str          # escalation | paused_run | cultivation | governance | job
    tier: str            # blocking | review
    brain: Optional[str]  # project name, or None for OS-level (governance)
    title: str
    detail: str = ""
    dispatch: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AttentionResult:
    items: list[AttentionItem] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=lambda: {TIER_BLOCKING: 0, TIER_REVIEW: 0})

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [it.to_dict() for it in self.items],
            "counts": dict(self.counts),
        }


# ── Per-source collectors ────────────────────────────────────────────────────
# Each collector is fail-soft: on ANY error it logs + returns [] so one broken
# source never blanks the whole channel (the channel's value is completeness, so
# a partial channel with a logged gap beats a crashed one).


def _collect_escalations(workspace_root: Path) -> list[AttentionItem]:
    """L2 BLOCK → BLOCKING ; L1 CONSULT → REVIEW. (L0 INFORM is auto-resolved on
    creation, never open, so it never appears here.)"""
    items: list[AttentionItem] = []
    try:
        from core.escalation import get_open_escalations, Level  # local import: avoid cycle
        from routers.ddd_brain import _list_project_dirs

        for pdir in _list_project_dirs():
            project = pdir.name
            try:
                for esc in get_open_escalations(workspace_root, project):
                    lvl = int(getattr(esc, "level", Level.BLOCK))
                    if lvl == int(Level.BLOCK):
                        tier = TIER_BLOCKING
                    elif lvl == int(Level.CONSULT):
                        tier = TIER_REVIEW
                    else:
                        continue  # INFORM (0) — not actionable, skip
                    items.append(AttentionItem(
                        id=f"escalation:{esc.id}",
                        source="escalation",
                        tier=tier,
                        brain=project,
                        title=esc.title,
                        detail=esc.situation or "",
                        dispatch={
                            "message": f"Resolve escalation {esc.id} for {project}: {esc.title}",
                            "context": {"kind": "escalation", "project": project,
                                        "escalation_id": esc.id, "level": lvl},
                        },
                    ))
            except Exception as exc:  # per-project isolation
                logger.warning("attention: escalation read failed for %s: %s", project, exc)
    except Exception as exc:
        logger.warning("attention: escalation source failed: %s", exc)
    return items


def _collect_cultivation(workspace_root: Path) -> list[AttentionItem]:
    """Pending DDD cultivation proposals (append/retire/rewrite awaiting human) → REVIEW."""
    items: list[AttentionItem] = []
    try:
        from core.ddd_cultivation import read_pending_proposals
        from routers.ddd_brain import _list_project_dirs

        for pdir in _list_project_dirs():
            project = pdir.name
            try:
                for p in read_pending_proposals(workspace_root, project):
                    pid = getattr(p, "proposal_id", None) or getattr(p, "id", "")
                    title = f"{p.target_doc}: {(p.content or '')[:80]}"
                    items.append(AttentionItem(
                        id=f"cultivation:{project}:{pid}",
                        source="cultivation",
                        tier=TIER_REVIEW,
                        brain=project,
                        title=title,
                        detail=(p.content or "")[:400],
                        dispatch={
                            "message": f"Review cultivation proposal for {project}: {title}",
                            "context": {"kind": "cultivation", "project": project,
                                        "proposal_id": pid, "target_doc": p.target_doc},
                        },
                    ))
            except Exception as exc:
                logger.warning("attention: cultivation read failed for %s: %s", project, exc)
    except Exception as exc:
        logger.warning("attention: cultivation source failed: %s", exc)
    return items


def _collect_governance() -> list[AttentionItem]:
    """Workspace-global governance proposals (rule/gate) → REVIEW, brain=None (OS-level).

    Governance is NOT per-project: it changes SOUL/AGENT rules for the whole OS,
    so it has no project field → brain=None. It is excluded from per-brain counts
    (see brain-card _pending_count) and rendered in the overlay's OS-level group.
    """
    items: list[AttentionItem] = []
    try:
        from core.eval_service import get_eval_service

        pending = get_eval_service().get_pending_governance()
        for gp in pending.get("proposals", []):
            gid = gp.get("id") or gp.get("proposal_id") or ""
            kind = gp.get("proposal_kind") or gp.get("kind") or "rule"

            # Title: prefer an explicit title/summary, else BUILD one from the actual
            # proposed rule text — never fall back to the bare "governance rule" type,
            # which tells the user nothing (the card looked empty before this).
            rule_text = str(gp.get("proposed_rule") or "").strip()
            explicit = gp.get("title") or gp.get("summary")
            if explicit:
                title = str(explicit)
            elif rule_text:
                title = f"New {kind}: {rule_text}"
            else:
                title = f"New governance {kind} (no rule text)"

            # Detail: the human failure-pattern this rule prevents + how often it
            # recurred + confidence — the "why you're seeing this" the card lacked.
            occ = gp.get("occurrence_count")
            conf = gp.get("confidence")
            phrase = _class_phrase(str(gp.get("source_class") or ""))
            bits = [phrase]
            if isinstance(occ, int):
                bits.append(f"recurred {occ}×")
            if isinstance(conf, (int, float)):
                bits.append(f"{round(conf * 100)}% confidence")
            why = " · ".join(bits)
            rationale = str(gp.get("rationale") or gp.get("description") or "").strip()
            detail = f"{why} — {rationale}" if rationale else why

            items.append(AttentionItem(
                id=f"governance:{gid}",
                source="governance",
                tier=TIER_REVIEW,
                brain=None,  # OS-level — no owning brain
                title=title[:120],
                detail=detail[:400],
                dispatch={
                    "message": f"Review governance {kind} proposal {gid}: {title}",
                    "context": {"kind": "governance", "proposal_id": gid, "proposal_kind": kind},
                },
            ))
    except Exception as exc:
        logger.warning("attention: governance source failed: %s", exc)
    return items


def _collect_paused_runs(pipeline_runs: Optional[list[dict]] = None) -> list[AttentionItem]:
    """Paused pipeline runs whose checkpoint reason is a DECISION (not crash residue)
    → BLOCKING. Reuses the pipelines router's cross-project loader + classifier."""
    items: list[AttentionItem] = []
    try:
        from routers.pipelines import _load_pipeline_runs, _to_response
        from schemas.pipeline_run import PipelineRunStatus

        raws = pipeline_runs if pipeline_runs is not None else _load_pipeline_runs()
        for raw in raws:
            try:
                resp = _to_response(raw)
                if resp.status != PipelineRunStatus.PAUSED:
                    continue
                if getattr(resp, "pause_kind", None) != "decision":
                    continue  # crash_residue is the reaper's job, not the user's
                project = raw.get("_project", raw.get("project", "unknown"))
                # PipelineRunResponse names the field `id` (schemas/pipeline_run.py),
                # and _load_pipeline_runs stores the raw dict under key `id` too —
                # NOT `run_id`. (adversarial HIGH: `run_id` → empty id, colliding
                # "paused_run:" ids + a blank resume message.)
                run_id = getattr(resp, "id", None) or raw.get("id", "")
                requirement = getattr(resp, "requirement", "") or raw.get("requirement", "")
                reason = ""
                cp = raw.get("checkpoint")
                if isinstance(cp, dict):
                    reason = cp.get("reason", "")
                items.append(AttentionItem(
                    id=f"paused_run:{run_id}",
                    source="paused_run",
                    tier=TIER_BLOCKING,
                    brain=project,
                    title=f"Paused: {requirement[:80]}",
                    detail=reason,
                    dispatch={
                        "message": f"Resume pipeline {run_id} for {project} — decision needed: {reason}",
                        "context": {"kind": "paused_run", "project": project, "run_id": run_id},
                    },
                ))
            except Exception as exc:
                logger.warning("attention: paused-run coerce failed: %s", exc)
    except Exception as exc:
        logger.warning("attention: paused-run source failed: %s", exc)
    return items


def _collect_jobs() -> list[AttentionItem]:
    """Circuit-broken jobs (consecutive_failures >= threshold) → BLOCKING.

    Filter tightening vs the old frontend (>0): only a BROKEN STREAK is the
    user's problem; transient failures self-heal next scheduled run. Jobs are
    not per-project → brain=None (OS-level infra).
    """
    items: list[AttentionItem] = []
    try:
        from jobs.scheduler import load_jobs, load_state

        state = load_state()
        jobs_by_id = {j.id: j for j in load_jobs()}
        for job_id, js in (state.jobs or {}).items():
            if (js.consecutive_failures or 0) < _JOB_CIRCUIT_BREAKER_THRESHOLD:
                continue
            job = jobs_by_id.get(job_id)
            name = getattr(job, "name", None) or job_id
            items.append(AttentionItem(
                id=f"job:{job_id}",
                source="job",
                tier=TIER_BLOCKING,
                brain=None,  # OS-level infra
                title=f"Circuit-broken job: {name} (×{js.consecutive_failures})",
                detail=(js.last_error or "")[:400],
                dispatch={
                    "message": f"Triage circuit-broken job {job_id} ({js.consecutive_failures} consecutive failures): {js.last_error or ''}",
                    "context": {"kind": "job", "job_id": job_id,
                                "consecutive_failures": js.consecutive_failures},
                },
            ))
    except Exception as exc:
        logger.warning("attention: job source failed: %s", exc)
    return items


def _collect_community_digests(workspace_root: Path) -> list[AttentionItem]:
    """A fresh, SUCCESSFUL community daily-digest / weekly-report → a REVIEW item
    whose dispatch.message tells the agent to LEARN + s_persist from it (R3 闭环).

    This closes the community loop: job → 🔔 → click → digest injected into chat →
    agent runs s_learn-content + s_persist to sediment the useful bits into DDD /
    Library. Distinct from _collect_jobs (which surfaces circuit-BROKEN jobs as
    BLOCKING) — here we surface a SUCCEEDED digest as REVIEW.

    Time-windowed + read-only (design constraint: collect() never writes disk): a
    digest surfaces for _DIGEST_FRESH_HOURS then ages out on its own — no marker
    file, no perpetual noise. Only the NEWEST digest per job_id surfaces (no
    stacking). Fail-soft: any error → [] (never blank the whole channel)."""
    items: list[AttentionItem] = []
    try:
        from datetime import datetime, timezone, timedelta
        from core.daily_activity_writer import parse_frontmatter

        jr_dir = workspace_root / "Knowledge" / "JobResults"
        if not jr_dir.is_dir():
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=_DIGEST_FRESH_HOURS)
        # newest fresh, successful community digest per job_id
        newest: dict[str, tuple[datetime, Path, dict]] = {}
        for p in jr_dir.glob("*.md"):
            try:
                # Read only the HEAD, not the whole file. Frontmatter lives in the
                # first few hundred bytes, but this used to read_text() the ENTIRE
                # file (299 JobResult .md files, ~549KB total) just to slice [:600] —
                # reading megabytes to look at kilobytes, on every attention collect().
                # open+read(600) touches only what's needed. errors="replace" keeps
                # the fail-soft contract (a bad byte must not abort the digest scan).
                with open(p, "r", encoding="utf-8", errors="replace") as _fh:
                    head = _fh.read(600)
                fm, _ = parse_frontmatter(head)
            except Exception:
                continue
            if not fm:
                continue
            job_id = str(fm.get("job_id", ""))
            if _COMMUNITY_JOB_MARKER not in job_id:
                continue
            if str(fm.get("status", "")).lower() != "success":
                continue
            run_at_raw = str(fm.get("run_at", ""))
            try:
                run_at = datetime.fromisoformat(run_at_raw.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone.utc)
            if run_at < cutoff:
                continue  # aged out of the freshness window
            prev = newest.get(job_id)
            if prev is None or run_at > prev[0]:
                newest[job_id] = (run_at, p, fm)

        for job_id, (run_at, path, fm) in newest.items():
            job_name = str(fm.get("job_name", job_id))
            items.append(AttentionItem(
                id=f"community_digest:{path.name}",
                source="community_digest",
                tier=TIER_REVIEW,  # a fresh digest is review-worthy, never blocking
                brain="GitHub_Community",
                title=f"Community digest ready: {job_name}",
                detail=f"A fresh community digest from {job_name} — dispatch to chat to learn + sediment.",
                dispatch={
                    "message": (
                        f"Read the community digest at {path} — then LEARN from it: "
                        f"for each engagement/reply/topic worth keeping, run s_persist to "
                        f"sediment it into the GitHub_Community DDD / Knowledge Library "
                        f"(what worked, new topic signal, a maintainer relationship). "
                        f"Skip the routine; sediment only what changes future judgment."
                    ),
                    "context": {"kind": "community_digest", "job_id": job_id,
                                "path": str(path)},
                },
            ))
    except Exception as exc:
        logger.warning("attention: community_digest source failed: %s", exc)
    return items


# ── Aggregator ───────────────────────────────────────────────────────────────


def collect(
    workspace_root: Path,
    *,
    brain: Optional[str] = None,
    pipeline_runs: Optional[list[dict]] = None,
) -> AttentionResult:
    """Aggregate all 5 sources → normalized, tiered, brain-attributed items.

    Args:
        workspace_root: SwarmWS root (Projects/ lives under it).
        brain: if set, return ONLY items for that brain. Governance (brain=None)
               is EXCLUDED from a per-brain query — it is OS-level, not that
               brain's pending work (this is what fixes the brain-card badge:
               _pending_count passes brain=<name> and gets a truthful count that
               includes escalation but excludes OS-level governance).
        pipeline_runs: optional pre-loaded run dicts (lets a caller that already
               scanned runs avoid a second disk scan); None → load internally.

    Read-only. Never writes disk. Fail-soft per source.
    """
    items: list[AttentionItem] = []
    items += _collect_escalations(workspace_root)
    items += _collect_cultivation(workspace_root)
    items += _collect_governance()
    items += _collect_paused_runs(pipeline_runs)
    items += _collect_jobs()
    items += _collect_community_digests(workspace_root)

    if brain is not None:
        # Per-brain query: only that brain's items. Governance (brain=None) is
        # OS-level and NEVER attributed to a single brain.
        items = [it for it in items if it.brain == brain]

    # Order: BLOCKING first (design §5 main axis), stable within tier.
    items.sort(key=lambda it: 0 if it.tier == TIER_BLOCKING else 1)

    counts = {
        TIER_BLOCKING: sum(1 for it in items if it.tier == TIER_BLOCKING),
        TIER_REVIEW: sum(1 for it in items if it.tier == TIER_REVIEW),
    }
    return AttentionResult(items=items, counts=counts)
