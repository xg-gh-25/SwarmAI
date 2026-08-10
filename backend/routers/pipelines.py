"""Pipeline runs API router.

Reads pipeline run state from ``.artifacts/runs/*/run.json`` files (with legacy ``pipeline-run-*.json`` fallback)
across all projects in SwarmWS. Serves real data to the Radar pipeline panel.

Key endpoints:

- ``GET /``            -- All pipelines (active + recent completed)
- ``GET /?active=true`` -- Only running/paused pipelines

The router is registered in main.py with prefix ``/api/pipelines``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

from core.pipeline_profiles import get_profile_stages
from jobs.paths import SWARMWS
from schemas.pipeline_run import (
    PipelineAnalytics,
    PipelineCheckpoint,
    PipelineCommit,
    PipelineDashboard,
    PipelineOverall,
    PipelineProjectGroup,
    PipelineRunDetail,
    PipelineRunResponse,
    PipelineRunStatus,
    PipelineRunSummary,
    PipelineStageTokens,
    PipelineStatusSummary,
    PipelineTrendPoint,
)
# Canonical crash-zombie discriminator + terminal-run predicate — single source
# of truth in artifact_cli (already reused by proactive_intelligence). Importing
# them here (rather than re-inventing a string/status match) keeps ONE definition
# of "this pause is crash residue" / "this run is finished" across all consumers.
# _extract_run_metrics is the SAME metrics primitive cmd_run_analytics uses — the
# analytics endpoint reuses it (never a 3rd run.json parser — run_0f03fa9d split-brain).
from scripts.artifact_cli import (
    _CRASH_ZOMBIE_REASON,
    _effective_analytics_status,
    _extract_run_metrics,
    _is_garbage_run,
    is_terminal_run,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Runs stuck in "running" with no update for this long are auto-marked failed.
# Configurable via env var for long-running pipelines (default: 60 min).
try:
    _STALE_THRESHOLD_MINUTES = int(os.environ.get("PIPELINE_STALE_THRESHOLD_MINUTES", "60"))
except (ValueError, TypeError):
    _STALE_THRESHOLD_MINUTES = 60

# The exact failure_reason the FORMER write-on-read stale-detector (_mark_failed,
# removed run_2568c3fb) stamped on disk. NO code writes it anymore — it is retained
# ONLY so the one-time reconciliation script (scripts/reconcile_mislabeled_runs.py)
# can import + match it byte-for-byte to rewrite the legacy runs that old writer
# mislabeled. Nothing in this router writes it. (Today's on-disk terminal transition
# for a dead/stale run is the reaper's `abandoned` verdict — a DIFFERENT status +
# reason; and the GET path presents a stale running run as `failed` read-only. The
# three states are intentionally distinct — see _to_response.)
_STALE_FAILURE_REASON = "session ended without completion (auto-detected stale)"


def _get_swarmws() -> Path:
    """Resolve SwarmWS path. Function (not constant) for testability."""
    return SWARMWS


def _get_profile_stage_count(profile: str | None) -> int:
    return len(get_profile_stages(profile))


def _is_stale(state: dict) -> bool:
    """Check if a running pipeline has gone stale (no update in threshold).

    A TERMINAL run is never stale — even if left status="running" on disk.
    A run that finished all stages (a completed reflect/deliver marker) but
    crashed before `run-update --status completed` ran is FINISHED, not a
    failure. Honoring `is_terminal_run` here mirrors the SAME guard that
    `artifact_cli._abandon_verdict` (before its abandon verdict) and
    `proactive_intelligence` (before auto-resume) already apply — so all three
    consumers of on-disk run state agree: terminal runs are skipped, never
    re-labeled. Without this, the stale→failed presentation coercion in
    _to_response would flip delivered-but-crashed runs to "failed" (run_aad474c7:
    9/9 stages completed must present as completed, not failed).
    """
    if state.get("status") != "running":
        return False
    if is_terminal_run(state):
        return False
    updated_at = state.get("updated_at", "")
    if not updated_at:
        return True
    try:
        updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        age_minutes = (datetime.now(timezone.utc) - updated).total_seconds() / 60
        return age_minutes > _STALE_THRESHOLD_MINUTES
    except (ValueError, TypeError):
        return True


def _load_pipeline_runs() -> list[dict]:
    """Scan all projects for pipeline run files.

    Returns raw dicts sorted by updated_at (newest first). Never raises —
    returns empty list on any error. Read-only: this function NEVER writes disk
    (a GET handler writing disk is forbidden, Gate-1 CRITICAL run_d7146171). A
    dead/stale run is handled in TWO independent places, neither of them here:
    (1) the GET path PRESENTS a stale `running` run as `failed` — read-only
    coercion in _to_response; (2) the reaper (artifact_cli._auto_abandon_stale_runs
    / cleanup_orphans) writes the real on-disk terminal verdict, which is
    `abandoned` (reason orphaned_no_resume / crash_zombie) — NOT `failed`. The
    presented status and the on-disk status intentionally differ.

    NOTE (run_2568c3fb): callers MUST offload this to a thread
    (`await asyncio.to_thread(_load_pipeline_runs)`) — it stat()s + json.loads
    every run file, which blocks the event loop (and every parallel request) if
    run inline. A mtime read-boundary "active fast path" was considered and
    REJECTED: file mtime freezes for BOTH terminal and paused runs, so it cannot
    distinguish them — any mtime cutoff would silently drop a genuinely paused-
    decision run (the exact item the 🔔 attention queue exists to surface) once it
    ages past the window. The `active` filter therefore stays a pure in-memory
    status filter in list_pipelines (correct), and the scan cost is bounded by
    to_thread, not by a lossy pre-filter. If the full scan ever becomes a real
    (measured) cost, add a proper active-run index — not an mtime heuristic.
    """
    projects_dir = _get_swarmws() / "Projects"
    if not projects_dir.exists():
        return []

    runs = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        artifacts_dir = project_dir / ".artifacts"
        if not artifacts_dir.exists():
            continue

        seen_ids: set[str] = set()

        # New path: .artifacts/runs/*/run.json
        runs_subdir = artifacts_dir / "runs"
        if runs_subdir.exists():
            for rd in runs_subdir.iterdir():
                rf = rd / "run.json"
                if rf.exists():
                    try:
                        state = json.loads(rf.read_text(encoding="utf-8"))
                        state["_project"] = project_dir.name
                        seen_ids.add(state.get("id", ""))
                        runs.append(state)
                    except (json.JSONDecodeError, OSError, KeyError) as e:
                        logger.debug("Skipping %s: %s", rf, e)

        # Legacy path: .artifacts/pipeline-run-*.json
        for run_file in artifacts_dir.glob("pipeline-run-*.json"):
            try:
                state = json.loads(run_file.read_text(encoding="utf-8"))
                if state.get("id") in seen_ids:
                    continue
                state["_project"] = project_dir.name
                runs.append(state)
            except (json.JSONDecodeError, OSError, KeyError) as e:
                logger.debug("Skipping %s: %s", run_file.name, e)

    runs.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return runs


def _to_response(raw: dict) -> PipelineRunResponse:
    """Convert raw pipeline-run JSON dict to response model."""
    profile = raw.get("profile") or "full"
    stages = raw.get("stages", [])
    completed = sum(1 for s in stages if s.get("status") == "completed")
    total = _get_profile_stage_count(profile)
    consumed = sum(s.get("token_cost", 0) for s in stages)

    checkpoint_raw = raw.get("checkpoint")
    checkpoint = None
    if checkpoint_raw and isinstance(checkpoint_raw, dict):
        checkpoint = PipelineCheckpoint(
            reason=checkpoint_raw.get("reason", "unknown"),
            stage=checkpoint_raw.get("stage", "unknown"),
            checkpointed_at=checkpoint_raw.get("checkpointed_at", ""),
            completed_stages=checkpoint_raw.get("completed_stages", []),
            resumed_at=checkpoint_raw.get("resumed_at"),
        )

    try:
        status = PipelineRunStatus(raw.get("status", "running"))
    except ValueError:
        status = PipelineRunStatus.RUNNING

    # Terminal-but-crashed presentation (run_0f03fa9d): a run that finished all
    # stages (completed reflect/deliver) but crashed before `run-update --status
    # completed` is left status=running/paused on disk. It DELIVERED — present it
    # as completed rather than "running forever". Disk is NOT mutated (the stale
    # detector now skips terminal runs); this is read-path only, so the honest
    # status reaches BOTH the list partition and the summary counts. Only the
    # running/paused live-status values are coerced — an explicit terminal status
    # (failed/cancelled/abandoned) is never overridden.
    if status in (PipelineRunStatus.RUNNING, PipelineRunStatus.PAUSED) and is_terminal_run(raw):
        status = PipelineRunStatus.COMPLETED

    # Stale-running presentation (run_2568c3fb): a run stuck "running" past the
    # stale threshold with no terminal stages is a dead session's orphan — present
    # it as failed so it doesn't masquerade as active forever. READ-ONLY coercion
    # (replaces the former write-on-read _mark_failed — a GET must not write disk).
    # NOTE: this is only the PRESENTATION; the reaper independently writes a
    # DIFFERENT on-disk verdict (`abandoned`, not `failed`) — the two intentionally
    # differ. Ordered AFTER the terminal→completed coercion so a
    # delivered-but-crashed run is presented completed, never failed (_is_stale
    # already skips terminal runs, so this only catches genuine no-progress orphans).
    if status == PipelineRunStatus.RUNNING and _is_stale(raw):
        status = PipelineRunStatus.FAILED

    # Classify a paused run for attention-queue consumers (Radar "NEEDS YOU").
    # Only a genuine decision-pause should demand the user's attention; a pause
    # stamped with the canonical crash marker is residue left by a dead session.
    # Everything-not-crash → "decision" is intentional: budget / retry-exhausted /
    # gate_spawn_blocked / Gate BLOCK are ALL real pauses the user should act on
    # (mirrors artifact_cli._abandon_verdict, which reaps ONLY _CRASH_ZOMBIE_REASON).
    pause_kind: Optional[str] = None
    if status == PipelineRunStatus.PAUSED:
        reason = checkpoint_raw.get("reason") if isinstance(checkpoint_raw, dict) else None
        pause_kind = "crash_residue" if reason == _CRASH_ZOMBIE_REASON else "decision"

    return PipelineRunResponse(
        id=raw.get("id", "unknown"),
        project=raw.get("_project", raw.get("project", "unknown")),
        requirement=raw.get("requirement", "")[:80],
        status=status,
        profile=profile,
        progress=f"{completed}/{total}",
        stages_completed=completed,
        stages_total=total,
        tokens_consumed=consumed,
        taste_decisions=len(raw.get("taste_decisions", [])),
        checkpoint=checkpoint,
        pause_kind=pause_kind,
        abandon_reason=raw.get("abandon_reason"),
        created_at=raw.get("created_at", ""),
        updated_at=raw.get("updated_at", ""),
    )


@router.patch("/{run_id}/cancel")
async def cancel_pipeline(run_id: str) -> dict:
    """Cancel a pipeline run by updating its run.json status to 'cancelled'.

    Returns 200 on success, 404 if run not found. Atomic write prevents corruption.
    """
    from fastapi.responses import JSONResponse

    projects_dir = _get_swarmws() / "Projects"
    if not projects_dir.exists():
        return JSONResponse(status_code=404, content={"status": "not_found", "run_id": run_id})

    # The whole scan (iterdir across projects) + read_text + atomic tmp-write/replace
    # is blocking FS I/O — run it in ONE worker thread (run_6ea3cb12), not per-call on
    # the loop. Returns (outcome, project_name_or_error) for the loop to translate.
    def _cancel() -> tuple[str, str | None]:
        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            run_file = project_dir / ".artifacts" / "runs" / run_id / "run.json"
            if run_file.exists():
                try:
                    state = json.loads(run_file.read_text(encoding="utf-8"))
                    state["status"] = "cancelled"
                    # Atomic write: write to tmp then replace to prevent corruption
                    tmp_file = run_file.with_suffix(".tmp")
                    tmp_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
                    tmp_file.replace(run_file)
                    return ("cancelled", project_dir.name)
                except (json.JSONDecodeError, OSError) as e:
                    return ("error", str(e))
        return ("not_found", None)

    outcome, info = await asyncio.to_thread(_cancel)
    if outcome == "cancelled":
        logger.info("Cancelled pipeline %s in project %s", run_id, info)
        return {"status": "cancelled", "run_id": run_id}
    if outcome == "error":
        logger.error("Failed to cancel %s: %s", run_id, info)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "run_id": run_id, "detail": info},
        )
    return JSONResponse(status_code=404, content={"status": "not_found", "run_id": run_id})


@router.get("", response_model=PipelineDashboard)
async def list_pipelines(
    active: Optional[bool] = Query(None, description="If true, only running/paused"),
) -> PipelineDashboard:
    """Return all pipeline runs with aggregate summary.

    Always returns HTTP 200 — empty dashboard if no pipelines exist.
    """
    # to_thread: the scan stat()s + json.loads many files — never run it inline on
    # the event loop (it blocks /health + every parallel request). The `active`
    # filter is a pure in-memory status filter below (run_2568c3fb — a mtime
    # pre-filter was rejected as it silently drops aged paused-decision runs).
    all_runs = await asyncio.to_thread(_load_pipeline_runs)
    if not all_runs:
        return PipelineDashboard()

    # Pair each raw run dict with its response: is_terminal_run needs the RAW
    # dict (stage array), which _to_response does not carry forward. Filtering on
    # the response alone (status only) would leave terminal zombies in `active`.
    paired = [(raw, _to_response(raw)) for raw in all_runs]

    if active:
        # Active = running/paused, EXCLUDING terminal zombies. A run whose stages
        # are all done (reflect/deliver completed) but whose status was flipped to
        # paused+crash-reason by the orphan-transition is FINISHED, not resumable —
        # it must not surface as active to ANY consumer. is_terminal_run is
        # stage-based (not status-based) precisely so a genuine mid-pipeline pause
        # (evaluate+think done, no reflect/deliver) is NOT misread as terminal.
        responses = [
            r for raw, r in paired
            if r.status in (PipelineRunStatus.RUNNING, PipelineRunStatus.PAUSED)
            and not is_terminal_run(raw)
        ]
    else:
        responses = [r for _raw, r in paired]
        # Keep all active + max 5 completed per project
        active_runs = [r for r in responses if r.status in (PipelineRunStatus.RUNNING, PipelineRunStatus.PAUSED)]
        completed_runs = [r for r in responses if r.status not in (PipelineRunStatus.RUNNING, PipelineRunStatus.PAUSED)]
        seen: dict[str, int] = {}
        trimmed = []
        for r in completed_runs:
            count = seen.get(r.project, 0)
            if count < 5:
                trimmed.append(r)
                seen[r.project] = count + 1
        responses = active_runs + trimmed

    summary = PipelineStatusSummary(
        running=sum(1 for r in responses if r.status == PipelineRunStatus.RUNNING),
        paused=sum(1 for r in responses if r.status == PipelineRunStatus.PAUSED),
        completed=sum(1 for r in responses if r.status == PipelineRunStatus.COMPLETED),
        abandoned=sum(1 for r in responses if r.status == PipelineRunStatus.ABANDONED),
        total_tokens=sum(r.tokens_consumed for r in responses),
    )

    return PipelineDashboard(
        pipelines=responses,
        count=len(responses),
        summary=summary,
    )


# ── Retro-Analytics dashboard (run_f8494370) ─────────────────────────────────

import re as _re

# run_<hex> — the ONLY shape a run_id ever takes (run-create). Used to reject a
# path-traversal token BEFORE it touches the filesystem (Gate-1 BLOCK: run_id is
# a user-controlled path param; a bare `../../etc` would escape the runs dir).
_RUN_ID_RE = _re.compile(r"^run_[A-Za-z0-9]+$")


def _run_metrics_cached(project: str, run_id: str, raw: dict) -> dict:
    """METRICS.json if present, else generate on-read (G2 — covers paused/abandoned
    runs that never hit the completion-only auto-gen). Reuses _extract_run_metrics,
    the SAME primitive cmd_run_analytics uses — never a 2nd parser. Best-effort:
    returns {} on any failure (the dashboard must never 500 on a bad run dir)."""
    try:
        mfile = _get_swarmws() / "Projects" / project / ".artifacts" / "runs" / run_id / "METRICS.json"
        if mfile.exists():
            return json.loads(mfile.read_text(encoding="utf-8"))
        metrics = _extract_run_metrics(project, run_id, raw)
        # Cache to disk so a run's metrics are computed ONCE, not on every dashboard
        # open (854-run scan was 4.9s, ~170 runs regenerated each time). Mirrors
        # cmd_run_analytics's persist pattern. GUARD: only TERMINAL runs — a
        # still-live run's metrics change, so caching them would freeze stale
        # numbers (pre-mortem). Use the canonical is_terminal_run helper (imported
        # above) — NOT a hand-rolled status tuple: it also catches a
        # finished-but-status=paused run (the orphan-transition class, run_bf840159)
        # that a status="paused" check would wrongly exclude, regenerating it on
        # every open forever. A terminal run's metrics are immutable, so the cached
        # file is a derived-once artifact keyed by run_id, NOT a drift-prone
        # snapshot beside a live source (IMPROVEMENT:87). Gate-2 MED (run_258290ed).
        if is_terminal_run(raw):
            mfile.parent.mkdir(parents=True, exist_ok=True)
            mfile.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        return metrics
    except Exception as exc:  # noqa: BLE001 — dashboard read must be fault-tolerant
        # Keep the fault tolerance (a broken metrics file must not 500 the dashboard),
        # but {} renders as a run with no metrics at all — visually identical to a run
        # that produced none. Without this line, a systematic metrics regression shows up
        # only as a dashboard that has quietly gone blank.
        logger.warning("metrics unavailable for %s/%s, rendering empty: %s",
                       project, run_id, exc)
        return {}


def _iso_week_start(iso_ts: str) -> Optional[str]:
    """Monday (YYYY-MM-DD) of the ISO week containing iso_ts. None if unparseable."""
    try:
        d = datetime.fromisoformat(iso_ts)
        monday = d - __import__("datetime").timedelta(days=d.weekday())
        return monday.date().isoformat()
    except (ValueError, TypeError):
        return None


def _window_cutoff(window: str) -> Optional[datetime]:
    """Start of the analytics window. 30d = 30 days ago; ytd = Jan 1 this year.
    None = no lower bound (defensive: unknown window → show all, never crash)."""
    now = datetime.now(timezone.utc)
    if window == "ytd":
        return datetime(now.year, 1, 1, tzinfo=timezone.utc)
    if window == "30d":
        return now - __import__("datetime").timedelta(days=30)
    return None


@router.get("/analytics", response_model=PipelineAnalytics)
async def pipeline_analytics(
    window: str = Query("30d", description="Trend window: 30d | ytd"),
) -> PipelineAnalytics:
    """Retro-analytics: overall rollup + weekly trend + by-project grouping over
    every pipeline run in the window. RETROSPECTIVE (fetch-once, no live state) —
    chat is the live surface. Reuses _load_pipeline_runs (the existing all-project
    scanner) + _extract_run_metrics (the existing metrics primitive). Always 200.
    """
    if window not in ("30d", "ytd"):
        window = "30d"
    cutoff = _window_cutoff(window)
    # Retro analytics genuinely needs the FULL set (terminal runs included).
    # Off-loop (to_thread): the full scan stat()s + json.loads every run file.
    all_runs = await asyncio.to_thread(_load_pipeline_runs)

    def _in_window(raw: dict) -> bool:
        if cutoff is None:
            return True
        ts = raw.get("created_at") or raw.get("updated_at") or ""
        try:
            return datetime.fromisoformat(ts) >= cutoff
        except (ValueError, TypeError):
            return True  # undated run → keep (fail-show, never silently drop)

    # Garbage exclusion (run_0e68e235): a garbage run (abandoned OR crash-residue-
    # paused, that never delivered) must not enter ANY rollup — needs-you count,
    # completion_rate denominator, tokens, profile_mix, trend, by-project. Filtered
    # HERE, once, at the top so trend + overall + by_project all share the same
    # numerator AND denominator (Gate-1 #3). The 22 delivered-but-mislabeled
    # (abandoned/paused WITH a completed reflect/deliver) are NOT garbage — they
    # survive this filter and are recategorized to 'completed' below via
    # _effective_analytics_status (Gate-1 #4), so completions are never undercounted.
    runs = [r for r in all_runs if _in_window(r) and not _is_garbage_run(r)]

    by_project: dict[str, list[PipelineRunSummary]] = {}
    trend_buckets: dict[str, dict] = {}
    overall_completed = 0
    overall_tokens_actual = 0
    overall_tokens_est = 0
    profile_mix: dict[str, int] = {}
    all_cycles: list[float] = []
    overall_aborted = 0

    for raw in runs:
        project = raw.get("_project", raw.get("project", "unknown"))
        run_id = raw.get("id", "unknown")
        status = raw.get("status", "unknown")
        profile = raw.get("profile") or "unknown"
        m = _run_metrics_cached(project, run_id, raw)
        cycle = m.get("duration_minutes")
        tok_actual = int(m.get("total_tokens") or 0)
        budget = raw.get("budget") or {}
        tok_est = int(sum((budget.get("stage_estimates") or {}).values())) if isinstance(budget, dict) else 0

        # Effective analytics status (run_0e68e235): garbage is already filtered out
        # of `runs`, so any remaining abandoned/paused-with-a-completed-reflect/deliver
        # is the "mislabeled-done" class — count it as completed (Gate-1 #4), never
        # undercount. `eff_status` drives BOTH the completed rollups AND the summary's
        # status field, so the analytics endpoint and the live dashboard agree (no
        # split-brain where analytics says completed but the row still reads abandoned
        # — Gate-1 #2).
        eff_status = _effective_analytics_status(raw)
        # pause_kind: consume the backend's canonical classification, never re-derive.
        ckpt = raw.get("checkpoint") or {}
        reason = ckpt.get("reason") if isinstance(ckpt, dict) else None
        pause_kind = None
        if eff_status == "paused":
            pause_kind = "crash_residue" if reason == _CRASH_ZOMBIE_REASON else "decision"
        # After garbage exclusion, a surviving abandoned/decision-pause is a REAL
        # needs-you item (genuine decision-pause). A mislabeled-done run became
        # eff_status=='completed', so it is NOT aborted.
        is_aborted = eff_status == "abandoned" or pause_kind == "decision"

        summary = PipelineRunSummary(
            id=run_id,
            requirement=(raw.get("requirement", "") or "")[:100],
            status=eff_status,
            profile=profile,
            progress=m.get("stages_completed") is not None
            and f"{m.get('stages_completed', 0)}/{m.get('stages_total', 0)}" or "",
            cycle_time_min=cycle,
            tokens_actual=tok_actual,
            tokens_est=tok_est,
            created_at=raw.get("created_at", ""),
            updated_at=raw.get("updated_at", ""),
            pause_kind=pause_kind,
            checkpoint_reason=reason,
        )
        by_project.setdefault(project, []).append(summary)

        # overall rollups
        profile_mix[profile] = profile_mix.get(profile, 0) + 1
        overall_tokens_actual += tok_actual
        overall_tokens_est += tok_est
        if eff_status == "completed":
            overall_completed += 1
        if is_aborted:
            overall_aborted += 1
        if isinstance(cycle, (int, float)):
            all_cycles.append(float(cycle))

        # weekly trend bucket (by created_at)
        wk = _iso_week_start(raw.get("created_at", ""))
        if wk:
            b = trend_buckets.setdefault(wk, {"runs": 0, "completed": 0, "cycles": [], "tokens": 0})
            b["runs"] += 1
            b["tokens"] += tok_actual
            if eff_status == "completed":
                b["completed"] += 1
            if isinstance(cycle, (int, float)):
                b["cycles"].append(float(cycle))

    def _avg(xs: list[float]) -> Optional[float]:
        return round(sum(xs) / len(xs), 1) if xs else None

    # Build by-project groups (each with its own health rollup).
    groups: list[PipelineProjectGroup] = []
    for project, summaries in sorted(by_project.items()):
        completed = sum(1 for s in summaries if s.status == "completed")
        cycles = [s.cycle_time_min for s in summaries if isinstance(s.cycle_time_min, (int, float))]
        aborted = sum(1 for s in summaries if s.status == "abandoned" or s.pause_kind == "decision")
        # newest-first roster
        summaries.sort(key=lambda s: s.updated_at or s.created_at, reverse=True)
        # Cap the DETAIL list to the newest 20 (XG: 再多我们不用显示 — >20 has no
        # value, and SwarmAI has 750 runs → an unbounded list was a 750-button wall
        # + a 4.9s open). run_count below keeps the real total for the header.
        #
        # BUT: NEEDS-YOU runs (abandoned / paused-decision) are NEVER capped away
        # (Gate-2 MEDIUM, run_929024a8). The overlay's pinned "Needs you" region +
        # focus filter must surface EVERY needy run — else the count says "5 need
        # you" while the busiest projects (the ones that HAVE >20 runs, the exact
        # motivating case) hide the older needy ones the user cannot then see/click.
        # So visible = newest-20 ∪ all needy runs (order-preserved, deduped).
        def _is_needy(s: PipelineRunSummary) -> bool:
            return s.status == "abandoned" or s.pause_kind == "decision"
        _newest = summaries[:20]
        _newest_ids = {s.id for s in _newest}
        _extra_needy = [s for s in summaries[20:] if _is_needy(s) and s.id not in _newest_ids]
        visible = _newest + _extra_needy
        # report_path: stat REPORT.md ONLY for the VISIBLE runs (Gate-1 #6 — never
        # stat all 750; needy tail is tiny). Workspace-relative → resolve Stage 1.
        _runs_root = _get_swarmws() / "Projects" / project / ".artifacts" / "runs"
        for s in visible:
            rpt = _runs_root / s.id / "REPORT.md"
            if rpt.exists():
                s.report_path = f"Projects/{project}/.artifacts/runs/{s.id}/REPORT.md"
        groups.append(PipelineProjectGroup(
            project=project,
            run_count=len(summaries),  # TRUE total (health rollup), never the capped len
            completion_rate=round(completed / len(summaries), 3) if summaries else 0.0,
            avg_cycle_min=_avg([float(c) for c in cycles]),
            aborted_count=aborted,
            runs=visible,
        ))
    groups.sort(key=lambda g: g.run_count, reverse=True)

    trend = [
        PipelineTrendPoint(
            week=wk,
            runs=b["runs"],
            completed=b["completed"],
            avg_cycle_min=_avg(b["cycles"]),
            tokens=b["tokens"],
        )
        for wk, b in sorted(trend_buckets.items())
    ]

    total = len(runs)
    overall = PipelineOverall(
        total_runs=total,
        completed=overall_completed,
        completion_rate=round(overall_completed / total, 3) if total else 0.0,
        avg_cycle_min=_avg(all_cycles),
        tokens_actual=overall_tokens_actual,
        tokens_est=overall_tokens_est,
        profile_mix=profile_mix,
        aborted_count=overall_aborted,
    )

    return PipelineAnalytics(window=window, overall=overall, trend=trend, by_project=groups)


@router.get("/{run_id}", response_model=PipelineRunDetail)
async def pipeline_run_detail(run_id: str):
    """One run's full retrospective: REPORT.md body + reflect lessons + per-stage
    est-vs-actual tokens + related commits (G1). Path-traversal safe: run_id is
    validated against ^run_[A-Za-z0-9]+$ AND the resolved run dir is confirmed
    inside the project's runs/ dir (resolve()+relative_to) before ANY read.
    """
    from fastapi.responses import JSONResponse

    # Gate-1 BLOCK fix: reject a traversal/garbage token BEFORE touching the FS.
    if not _RUN_ID_RE.match(run_id):
        return JSONResponse(status_code=404, content={"status": "not_found", "run_id": run_id})

    projects_dir = _get_swarmws() / "Projects"
    if not projects_dir.exists():
        return JSONResponse(status_code=404, content={"status": "not_found", "run_id": run_id})

    # The whole scan (iterdir across projects) + run.json read + REPORT.md read is
    # blocking FS I/O — run it in ONE worker thread (run_6ea3cb12). Returns
    # (found, raw, report_md, report_path, project_dir_name) or a sentinel; the
    # metrics + response building (in-memory) stay on the loop below.
    def _scan_and_read():
        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            runs_root = (project_dir / ".artifacts" / "runs").resolve()
            run_dir = (runs_root / run_id).resolve()
            # Containment: the resolved run dir MUST live under runs_root (defense in
            # depth beyond the regex — never trust a single guard for a path read).
            try:
                run_dir.relative_to(runs_root)
            except ValueError:
                continue
            run_file = run_dir / "run.json"
            if not run_file.exists():
                continue

            try:
                raw = json.loads(run_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return ("corrupt", None, "", None, None)

            # REPORT.md body (retro), if present + its workspace-relative path so the
            # overlay can open it in Canvas via swarm:open-file (run_929024a8).
            report_md = ""
            report_path: Optional[str] = None
            rpt = run_dir / "REPORT.md"
            if rpt.exists():
                try:
                    report_md = rpt.read_text(encoding="utf-8")
                except OSError:
                    report_md = ""
                # Workspace-relative (e.g. Projects/<p>/.artifacts/runs/<id>/REPORT.md) —
                # hits resolve Stage 1 in /workspace/file/resolve. Guard relative_to in
                # case the run dir ever sits outside the workspace (defensive; it never
                # does today) — a report we can't express relative to the ws is unopenable
                # via the workspace resolver, so leave path None (button hidden).
                try:
                    report_path = str(rpt.relative_to(_get_swarmws()))
                except ValueError:
                    report_path = None
            return ("found", raw, report_md, report_path, project_dir.name)
        return ("not_found", None, "", None, None)

    outcome, raw, report_md, report_path, project_dir_name = await asyncio.to_thread(_scan_and_read)
    if outcome != "found":
        return JSONResponse(status_code=404, content={"status": "not_found", "run_id": run_id})

    if True:  # preserve original indentation block below (in-memory response build)
        project = raw.get("_project", raw.get("project", project_dir_name))
        m = _run_metrics_cached(project, run_id, raw)

        # reflect lessons live in the reflect stage record.
        reflect_lessons: list[str] = []
        for s in raw.get("stages", []):
            if s.get("stage") == "reflect":
                lessons = s.get("lessons") or []
                if isinstance(lessons, list):
                    reflect_lessons = [str(x) for x in lessons]
                break

        # per-stage est (budget.stage_estimates) vs actual (METRICS.stage_tokens)
        budget = raw.get("budget") or {}
        est_map = (budget.get("stage_estimates") or {}) if isinstance(budget, dict) else {}
        actual_map = m.get("stage_tokens") or {}
        stage_names = list(dict.fromkeys([*est_map.keys(), *actual_map.keys()]))
        stage_tokens = [
            PipelineStageTokens(stage=st, est=int(est_map.get(st, 0)), actual=int(actual_map.get(st, 0)))
            for st in stage_names
        ]

        commits = [
            PipelineCommit(
                repo=c.get("repo", ""), sha=c.get("sha", ""),
                files=list(c.get("files", [])),
            )
            for c in (raw.get("commits") or []) if isinstance(c, dict)
        ]

        ckpt = raw.get("checkpoint") or {}
        return PipelineRunDetail(
            id=run_id,
            project=project,
            requirement=raw.get("requirement", ""),
            # Effective status (run_0e68e235, Gate-2 MEDIUM): the analytics LIST shows
            # a delivered-but-mislabeled run as 'completed' (via _effective_analytics_
            # status). The detail drawer MUST agree, or clicking a "completed" row opens
            # a drawer reading "abandoned" — a split-brain. Same SSOT helper as the list.
            status=_effective_analytics_status(raw),
            profile=raw.get("profile") or "",
            cycle_time_min=m.get("duration_minutes"),
            report_md=report_md,
            report_path=report_path,
            reflect_lessons=reflect_lessons,
            stage_tokens=stage_tokens,
            commits=commits,
            checkpoint_reason=ckpt.get("reason") if isinstance(ckpt, dict) else None,
            created_at=raw.get("created_at", ""),
            updated_at=raw.get("updated_at", ""),
        )
