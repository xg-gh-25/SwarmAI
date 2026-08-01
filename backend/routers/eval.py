"""
Self-Eval API endpoints — serves OS Eval Dashboard.

The agent's self-awareness surface: exposes behavioral contract health,
eval history, and golden set management.

Read endpoints: health score, run history, case details.
Mutation endpoints: CRUD on golden set cases, run triggers, promotions.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from core.ddd_paths import ddd_path
from core.eval_service import get_eval_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Request Models ──────────────────────────────────────────────────────────


class CreateCaseRequest(BaseModel):
    id: str = Field(..., min_length=1, description="Unique case ID (e.g. GS021)")
    category: str
    dimension: str
    title: str = ""
    level: str = "session"
    source: str = ""
    affected_by: list[str] = Field(default_factory=list)
    evaluators: list[str] = Field(default_factory=list)
    scenario: dict = Field(default_factory=dict)
    verification: dict = Field(default_factory=dict)
    expected_trajectory: list[str] = Field(default_factory=list)
    assertions: list[str] = Field(default_factory=list)


class UpdateCaseRequest(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    dimension: Optional[str] = None
    level: Optional[str] = None
    source: Optional[str] = None
    affected_by: Optional[list[str]] = None
    evaluators: Optional[list[str]] = None
    scenario: Optional[dict] = None
    verification: Optional[dict] = None
    expected_trajectory: Optional[list[str]] = None
    assertions: Optional[list[str]] = None
    tier: Optional[str] = None


class TriggerRunRequest(BaseModel):
    trigger: str = Field(default="manual", description="Trigger type")
    case_ids: Optional[list[str]] = Field(default=None, description="Specific case IDs to run")
    include_behavior: bool = Field(
        default=False,
        description="Opt-in to run the behavior tier (real agent spawns, ~17-120s/case + "
        "Bedrock cost). Default False keeps a blanket manual sweep safe — behavior cases "
        "are excluded unless explicitly requested, mirroring the CLI --include-behavior and "
        "the scheduled handler's explicit opt-in.",
    )


class HardDeleteRequest(BaseModel):
    case_ids: list[str] = Field(min_length=1, max_length=500,
                                description="Case IDs to PHYSICALLY remove (not soft-archive)")


# ─── GET Endpoints (P2) ─────────────────────────────────────────────────────


@router.get("/health")
async def get_eval_health():
    """Current OS Health Score + per-dimension breakdown + Intelligence Velocity."""
    svc = get_eval_service()
    health = svc.get_health()
    health["intelligence_velocity"] = svc.compute_intelligence_velocity(detail=True)
    return health


@router.get("/history")
async def get_eval_history(limit: int = Query(default=20, ge=1, le=100)):
    """List eval runs sorted by date (newest first)."""
    svc = get_eval_service()
    return svc.get_history(limit=limit)


@router.get("/golden-set")
async def get_golden_set(category: Optional[str] = Query(default=None)):
    """Return golden set cases (optionally filtered by category)."""
    svc = get_eval_service()
    return svc.get_golden_set(category=category)


@router.get("/session-quality")
async def get_session_quality():
    """Layer③ overview for the Session Quality tab: latest run summary (scored/
    low/drafts), weekly low-rate trend (drift radar), and pending-draft count."""
    svc = get_eval_service()
    return svc.get_session_quality()


@router.get("/session-quality/drafts")
async def get_session_quality_drafts():
    """Layer② pending-draft queue: harvested golden drafts awaiting human
    ratification. The tab renders Promote (→ POST /golden-set) / Discard."""
    svc = get_eval_service()
    return svc.get_session_quality_drafts()


@router.get("/golden-set/{case_id}")
async def get_case_detail(case_id: str):
    """Return full detail for a single golden set case."""
    svc = get_eval_service()
    detail = svc.get_case_detail(case_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")
    return detail


@router.get("/runs/{run_id}")
async def get_run_detail(run_id: str):
    """Get a specific eval run by ID."""
    svc = get_eval_service()
    run = svc.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return run


# ─── POST Endpoints (P3 — Mutations) ────────────────────────────────────────


@router.post("/golden-set")
async def create_case(req: CreateCaseRequest):
    """Create a new golden set case."""
    svc = get_eval_service()
    try:
        case = svc.add_case(req.model_dump(exclude_none=True))
        return {"status": "created", "case": case}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/golden-set/{case_id}")
async def update_case(case_id: str, req: UpdateCaseRequest):
    """Update an existing golden set case."""
    svc = get_eval_service()
    updates = req.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        case = svc.update_case(case_id, updates)
        return {"status": "updated", "case": case}
    except ValueError as e:
        raise HTTPException(status_code=400 if "Cannot change" in str(e) else 404, detail=str(e))


@router.delete("/golden-set/{case_id}")
async def delete_case(case_id: str):
    """Archive (soft-delete) a golden set case."""
    svc = get_eval_service()
    try:
        case = svc.delete_case(case_id)
        return {"status": "archived", "case_id": case_id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/golden-set/hard-delete")
async def hard_delete_cases(req: HardDeleteRequest):
    """PHYSICALLY remove cases from the golden_set file(s) — not soft-archive.
    Runs in the daemon's EvalService singleton so its in-memory state updates in
    the same act (no cross-process stale-memory resurrection — run_110678fb)."""
    svc = get_eval_service()
    result = svc.hard_delete_cases(req.case_ids)
    return {"status": "hard_deleted", **result}


@router.post("/run")
async def trigger_eval_run(req: TriggerRunRequest):
    """Trigger a full eval run in background. Returns run_id immediately."""
    svc = get_eval_service()
    try:
        run_id = svc.trigger_run(trigger=req.trigger, case_ids=req.case_ids,
                                 include_behavior=req.include_behavior)
        resp = {"status": "started", "run_id": run_id}
        if req.include_behavior:
            # Gate-2 MED#1: surface the real-agent-spawn cost magnitude so the
            # caller/GUI knows this opt-in sweep is expensive (~17-120s each).
            resp["behavior_cases"] = svc.behavior_case_count()
        return resp
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/canary")
def run_canary():
    """Run programmatic-only eval cases synchronously (<5s).

    Note: sync def so FastAPI runs in threadpool, not blocking event loop.
    """
    svc = get_eval_service()
    try:
        result = svc.run_canary()
        return result
    except Exception as e:
        logger.error("eval canary failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run-cases")
async def run_specific_cases(req: TriggerRunRequest):
    """Run specific case IDs. Same as /run but requires case_ids."""
    if not req.case_ids:
        raise HTTPException(status_code=400, detail="case_ids required")
    svc = get_eval_service()
    try:
        run_id = svc.trigger_run(trigger=req.trigger, case_ids=req.case_ids,
                                 include_behavior=req.include_behavior)
        resp = {"status": "started", "run_id": run_id}
        if req.include_behavior:
            resp["behavior_cases"] = svc.behavior_case_count()
        return resp
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/promote-stable")
async def promote_stable_cases():
    """Promote cases with 10+ consecutive passes to stable tier."""
    svc = get_eval_service()
    promoted = svc.promote_stable_cases()
    return {"status": "ok", "promoted": promoted, "count": len(promoted)}


@router.post("/reload")
async def reload_eval_data():
    """Reload golden set + history from disk."""
    svc = get_eval_service()
    svc.reload()
    return {"status": "reloaded", "cases": svc.case_count, "runs": svc.run_count}


# ─── Context Health (DDD & Memory Auto-Refresh) ────────────────────────────


@router.get("/context-health")
async def get_context_health():
    """Read-only context freshness report for the Eval dashboard.

    Returns recent auto-refresh activity (Layer 1 applied fixes) +
    DDD staleness signals + pending Layer 3 proposals.
    """
    from pathlib import Path
    from core.initialization_manager import initialization_manager

    # Default drift signal — ALWAYS present in the response so the frontend never
    # accesses an undefined key (Gate-1: a swallowed backend error must not crash the tab).
    _empty_drift = {"report_date": None, "findings": [], "drift_count": 0, "at_risk_cases": []}

    ws_path = initialization_manager.get_cached_workspace_path()
    if not ws_path:
        return {"refresh_log": [], "staleness": [], "pending_proposals": [],
                "weeks_available": 0, "semantic_drift": _empty_drift}

    root = Path(ws_path)
    result = {
        "refresh_log": [],
        "staleness": [],
        "pending_proposals": [],
        "weeks_available": 0,
        "semantic_drift": _empty_drift,
    }

    # 1. Read auto-refresh log (last 8 weeks)
    try:
        from core.auto_refresh import read_refresh_log
        log_path = root / ".context" / ".auto_refresh_log.jsonl"
        result["refresh_log"] = read_refresh_log(log_path, since_days=56)
        # Count distinct weeks
        weeks = set()
        for entry in result["refresh_log"]:
            ts = entry.get("timestamp", "")[:10]  # YYYY-MM-DD
            if ts:
                from datetime import datetime
                try:
                    dt = datetime.fromisoformat(ts)
                    weeks.add(dt.isocalendar()[:2])  # (year, week)
                except ValueError:
                    pass
        result["weeks_available"] = len(weeks)
    except Exception as exc:
        logger.debug("context-health: refresh log read failed: %s", exc)

    # 2. DDD staleness signals
    try:
        from core.ddd_orchestrator import DddCultivationOrchestrator
        orch = DddCultivationOrchestrator()
        staleness_findings = orch._ch_ddd_staleness(root, ws_path)
        result["staleness"] = [
            _parse_staleness_finding(f) for f in staleness_findings
            if f.startswith("DDD-STALE:")
        ]
    except Exception as exc:
        logger.debug("context-health: staleness check failed: %s", exc)

    # 3. Pending proposals (Layer 3 escalations)
    try:
        from core.ddd_cultivation import read_pending_proposals
        proposals = read_pending_proposals(root, "SwarmAI")
        result["pending_proposals"] = [
            {
                "id": p.id,
                "target_doc": p.target_doc,
                "target_section": p.target_section,
                "content": p.content[:200],
                "created_at": p.created_at,
                "confidence": p.confidence,
            }
            for p in proposals[:10]
        ]
    except Exception as exc:
        logger.debug("context-health: proposals read failed: %s", exc)

    # 3b. DDD SEMANTIC drift (ddd-self-audit findings) + at-risk golden cases.
    # Distinct from #2 staleness (that is ddd_refresh's SYNTACTIC mtime signal); this is
    # the LLM self-audit's semantic-contradiction findings, and it maps each finding to
    # the golden cases whose affected_by depends on the drifted doc — drift influences
    # eval through the EXISTING affected_by chain, no new score. Computed LIVE (R30#4).
    try:
        from core.ddd_drift_signal import get_semantic_drift, map_at_risk_cases
        from core.eval_service import get_eval_service

        drift = get_semantic_drift(root)
        try:
            cases = get_eval_service().get_golden_set()["cases"]
            drift["at_risk_cases"] = map_at_risk_cases(drift["findings"], cases)
        except Exception as exc:
            logger.debug("context-health: at-risk mapping failed: %s", exc)
            drift["at_risk_cases"] = []
        result["semantic_drift"] = drift
    except Exception as exc:
        logger.debug("context-health: semantic drift read failed: %s", exc)
        # result["semantic_drift"] keeps the _empty_drift default set above.

    # 4. Learning Dashboard — knowledge growth metrics (source:auto|manual)
    try:
        result["learning_dashboard"] = _build_learning_dashboard(root)
    except Exception as exc:
        logger.debug("context-health: learning dashboard failed: %s", exc)
        result["learning_dashboard"] = None

    # 5. Token block — read-only telemetry for the C&M Brain overlay UI (Context
    # tab + overview rail): calibrated per-file token sizes + composition % +
    # ownership/priority/lock. NOT consumed by Eval logic; OPTIONAL field, so
    # existing consumers (EvalDashboard) that don't read it are unaffected.
    try:
        from core.context_brain import build_context_token_block

        result["token_block"] = build_context_token_block(root / ".context")
    except Exception as exc:
        logger.debug("context-health: token block failed: %s", exc)
        result["token_block"] = None

    return result


def _build_learning_dashboard(root) -> dict:
    """Build learning metrics from DDD entry metadata (source tags + decay states).

    Scans IMPROVEMENT.md and TECH.md for entries with metadata comments.
    Returns counts for the OS Eval Context Health tab.
    """
    from pathlib import Path
    from datetime import datetime, timedelta
    from core.ddd_entry_lifecycle import parse_entries

    seven_days_ago = datetime.now().date() - timedelta(days=7)
    projects_dir = root / "Projects"

    auto_count = 0
    manual_count = 0
    legacy_count = 0  # entries without source tag
    distribution: dict[str, int] = {}
    dormant_count = 0
    archived_count = 0

    # Scan project DDD docs
    if projects_dir.is_dir():
        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir() or project_dir.name.startswith("."):
                continue
            for doc_name in ("IMPROVEMENT.md", "TECH.md"):
                doc_path = ddd_path(project_dir, doc_name)
                if not doc_path.exists():
                    continue
                try:
                    content = doc_path.read_text(encoding="utf-8")
                    entries = parse_entries(content)
                    for entry in entries:
                        # Count by source
                        if entry.source == "auto":
                            auto_count += 1
                        elif entry.source == "manual":
                            manual_count += 1
                        else:
                            legacy_count += 1
                        # Count by doc (distribution)
                        distribution[doc_name] = distribution.get(doc_name, 0) + 1
                        # Decay activity
                        if entry.decay_state == "dormant":
                            dormant_count += 1
                        elif entry.decay_state == "archived":
                            archived_count += 1
                except Exception:
                    continue

    # Scan cross-project files (MEMORY.md)
    memory_path = root / ".context" / "MEMORY.md"
    if memory_path.exists():
        try:
            content = memory_path.read_text(encoding="utf-8")
            entries = parse_entries(content)
            for entry in entries:
                if entry.source == "auto":
                    auto_count += 1
                elif entry.source == "manual":
                    manual_count += 1
                else:
                    legacy_count += 1
                distribution["MEMORY.md"] = distribution.get("MEMORY.md", 0) + 1
                if entry.decay_state == "dormant":
                    dormant_count += 1
                elif entry.decay_state == "archived":
                    archived_count += 1
        except Exception:
            pass

    # Count recent entries (created in last 7 days) — approximate from ref_count=0
    # True "new" detection would need created_date parsing; ref_count=0 is a proxy
    # (newly added entries haven't been referenced yet).

    return {
        "total_entries": auto_count + manual_count + legacy_count,
        "by_source": {
            "auto": auto_count,
            "manual": manual_count,
            "legacy": legacy_count,
        },
        "distribution": distribution,
        "decay": {
            "dormant": dormant_count,
            "archived": archived_count,
        },
    }


# ─── C&M Brain size trend (run_d0ba3f69) ─────────────────────────────────


@router.get("/brain-trend")
async def get_brain_trend():
    """Daily size-snapshot series for the C&M overlay trend charts.

    Returns the recorded {date, prompt_tokens, memory_bytes} points (per_file
    omitted from the trend response — it's large and only the totals drive the
    two line charts). NO backfill: the series starts at the feature's launch date
    and fills in one point per day. The frontend shows "collecting since launch"
    until there are >=2 points (never fabricates a baseline — R30).
    """
    from pathlib import Path
    from core.initialization_manager import initialization_manager
    from core.brain_size_series import read_series, SERIES_RELPATH

    ws_path = initialization_manager.get_cached_workspace_path()
    if not ws_path:
        return {"points": [], "count": 0, "launch_date": None}

    rows = read_series(Path(ws_path) / SERIES_RELPATH)
    points = [
        {"date": r.get("date"), "prompt_tokens": r.get("prompt_tokens", 0),
         "memory_bytes": r.get("memory_bytes", 0)}
        for r in rows
    ]
    return {
        "points": points,
        "count": len(points),
        "launch_date": points[0]["date"] if points else None,
    }


@router.get("/brain-graph")
async def get_brain_graph():
    """7-type knowledge graph + per-type drill-down for the C&M Memory tab.

    Node counts + latest-10 drill come from parse_entries(MEMORY.md) by entry_type
    (VALID_TYPES) — backend-served, frontend invents nothing (R30). Empty-but-valid
    (all-zero 7 nodes) when MEMORY.md is absent, so the tab always renders a stable
    graph shape.
    """
    from pathlib import Path
    from core.initialization_manager import initialization_manager
    from core.brain_graph import build_brain_graph
    from core.ddd_entry_lifecycle import VALID_TYPES

    ws_path = initialization_manager.get_cached_workspace_path()
    if not ws_path:
        return {"nodes": [{"type": t, "count": 0, "active": 0, "dormant": 0} for t in VALID_TYPES],
                "drill": {t: [] for t in VALID_TYPES}, "total": 0}

    memory_path = Path(ws_path) / ".context" / "MEMORY.md"
    try:
        content = memory_path.read_text(encoding="utf-8") if memory_path.is_file() else ""
    except OSError:
        content = ""
    return build_brain_graph(content)


# ─── Reports (HTML) ───────────────────────────────────────────────────────


@router.get("/reports")
async def list_reports():
    """List available HTML eval reports (newest first)."""
    from pathlib import Path
    from core.initialization_manager import initialization_manager

    ws_path = initialization_manager.get_cached_workspace_path()
    if not ws_path:
        return []

    reports_dir = Path(ws_path) / "Eval" / "EvalHistory"
    if not reports_dir.is_dir():
        return []

    reports = []
    for f in sorted(reports_dir.glob("*.html"), reverse=True):
        stat = f.stat()
        reports.append({
            "filename": f.name,
            "sizeBytes": stat.st_size,
            "modified": stat.st_mtime,
        })
    return reports


@router.get("/reports/{filename}")
async def get_report(filename: str):
    """Return HTML content of a specific eval report."""
    from pathlib import Path
    from fastapi.responses import HTMLResponse
    from core.initialization_manager import initialization_manager

    # Safety: only allow .html files, no path traversal
    if not filename.endswith(".html") or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    ws_path = initialization_manager.get_cached_workspace_path()
    if not ws_path:
        raise HTTPException(status_code=404, detail="Workspace not found")

    reports_dir = Path(ws_path) / "Eval" / "EvalHistory"
    report_path = reports_dir / filename

    # Defense in depth: resolve symlinks and verify path stays within reports_dir
    if not report_path.is_file():
        raise HTTPException(status_code=404, detail=f"Report '{filename}' not found")
    if report_path.is_symlink() or not report_path.resolve().is_relative_to(reports_dir.resolve()):
        raise HTTPException(status_code=403, detail="Access denied")

    content = report_path.read_text(encoding="utf-8")
    return HTMLResponse(content=content)


def _parse_staleness_finding(finding: str) -> dict:
    """Parse 'DDD-STALE: Project/Doc (Xd old, Y recent commits)' into dict."""
    import re
    m = re.match(
        r"DDD-STALE:\s*([\w-]+)/([\w-]+\.md)\s*\((\d+)d old,\s*(\d+) recent commits?\)",
        finding,
    )
    if m:
        return {
            "project": m.group(1),
            "doc": m.group(2),
            "days_stale": int(m.group(3)),
            "recent_commits": int(m.group(4)),
        }
    return {"raw": finding}


# --- v3 Phase 3: Governance proposal review ---

class GovernanceDecisionRequest(BaseModel):
    """Accept / reject / defer a governance proposal."""

    proposal_id: str = Field(..., min_length=1)
    decision: str = Field(..., pattern="^(accept|reject|defer)$")
    notes: Optional[str] = None


@router.get("/governance/pending")
async def get_pending_governance():
    """List governance proposals (rule/gate) awaiting human decision.

    Reads .evolution_proposals.json filtered to target=='governance'. Each item
    carries a stable `id` (gc_id or source_class:proposal_kind) for accept/reject.
    """
    svc = get_eval_service()
    return svc.get_pending_governance()


@router.post("/governance/decision")
async def decide_governance(req: GovernanceDecisionRequest):
    """Accept (-> register_rule/register_gate), reject (-> remove), or defer a proposal.

    NEVER writes SOUL/AGENT/STEERING — accept only records the fix in the tracker,
    which makes the rule->gate escalation reachable on the next recurrence.
    """
    svc = get_eval_service()
    result = svc.decide_governance(req.proposal_id, req.decision)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=f"proposal {req.proposal_id} not found")
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error", "bad request"))
    return result
