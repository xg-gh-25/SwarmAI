"""
Swarm Jobs API — Unified endpoint for job system management.

Provides REST API for listing, running, and managing both system
and user jobs. Aggregates all three execution models (hooks, cron, services)
into a single status view.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from jobs.paths import JOB_RESULTS_DIR, JOB_RESULTS_JSONL

logger = logging.getLogger("swarm.routers.jobs")

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# Cap the full last-run output body so a runaway job report can't bloat the drawer.
_MAX_OUTPUT_BYTES = 32 * 1024
# Newest-N runs surfaced in the drawer's recent-runs list.
_RECENT_LIMIT = 10


class JobStatusResponse(BaseModel):
    id: str
    name: str
    type: str
    schedule: str
    enabled: bool
    category: str
    source: str  # "system" or "user"
    last_run: str | None = None
    last_status: str = "never"
    last_error: str | None = None
    total_runs: int = 0
    consecutive_failures: int = 0


class RunJobRequest(BaseModel):
    job_id: str
    dry_run: bool = False


class RunJobResponse(BaseModel):
    job_id: str
    status: str
    summary: str
    duration_seconds: float = 0.0


@router.get("/", response_model=list[JobStatusResponse])
async def list_jobs():
    """List all jobs (system + user) with their current status."""
    try:
        from jobs.scheduler import load_jobs, load_state
        jobs = load_jobs()
        state = load_state()

        result = []
        for job in jobs:
            js = state.jobs.get(job.id)
            result.append(JobStatusResponse(
                id=job.id,
                name=job.name,
                type=job.type,
                schedule=job.schedule,
                enabled=job.enabled,
                category=job.category,
                source="system" if job.category == "system" else "user",
                last_run=js.last_run.isoformat() if (js and js.last_run) else None,
                last_status=js.last_status if js else "never",
                last_error=js.last_error if js else None,
                total_runs=js.total_runs if js else 0,
                consecutive_failures=js.consecutive_failures if js else 0,
            ))
        return result
    except Exception as e:
        logger.error("Failed to list jobs: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run", response_model=RunJobResponse)
async def run_job(req: RunJobRequest):
    """Force-run a specific job immediately."""
    try:
        from jobs.scheduler import (
            load_jobs, load_config, load_feeds, load_state,
            save_state_reconciled, load_user_context, load_defaults,
        )
        from jobs.executor import execute_job

        config = load_config()
        feeds = load_feeds(config)
        defaults = load_defaults(config)
        jobs = load_jobs()
        state = load_state()
        user_context = load_user_context()

        job = next((j for j in jobs if j.id == req.job_id), None)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job '{req.job_id}' not found")
        if not job.enabled:
            raise HTTPException(status_code=400, detail=f"Job '{req.job_id}' is disabled")

        if req.dry_run:
            return RunJobResponse(
                job_id=req.job_id,
                status="dry_run",
                summary=f"Would execute: {job.id} ({job.type})",
            )

        all_job_ids = {j.id for j in jobs}
        result = execute_job(job, state, feeds, user_context, defaults, all_job_ids)
        # Reconciled save preserves events hooks emitted during the run.
        save_state_reconciled(state)

        return RunJobResponse(
            job_id=result.job_id,
            status=result.status,
            summary=result.summary,
            duration_seconds=result.duration_seconds,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to run job '%s': %s", req.job_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def unified_status():
    """Unified job system status — aggregates all 4 categories.

    Returns:
        scheduled_jobs: Cron + user jobs with last-run and health
        session_hooks: Background hook executor status and per-hook stats
        services: Managed subsidiary services (Slack bot, etc.)
        overview: Summary counts and monthly spend
    """
    result: dict = {
        "scheduled_jobs": {},
        "session_hooks": {},
        "services": [],
        "overview": {},
    }

    # 1. Scheduled jobs (cron + user)
    try:
        from jobs.scheduler import load_state, load_jobs
        # load_state/load_jobs read state.json + jobs.yaml/user-jobs.yaml (blocking) —
        # both off the event loop in one worker thread (run_b2d3ece0).
        def _load():
            return load_state(), load_jobs()
        state, jobs = await asyncio.to_thread(_load)

        # Only ENABLED jobs count toward health/failing — a disabled job is
        # inert (neither healthy nor failing). brain-push (enabled=False) must
        # not inflate the failing count (same enabled-gap class as run_01d2fd9d).
        active = [j for j in jobs if j.enabled]
        ok = sum(1 for j in active if state.jobs.get(j.id) and state.jobs[j.id].last_status == "success")
        err = sum(1 for j in active if state.jobs.get(j.id) and state.jobs[j.id].consecutive_failures > 0)

        result["scheduled_jobs"] = {
            "total": len(jobs),
            "enabled": len(active),
            "healthy": ok,
            "failing": err,
            "never_run": len(active) - ok - err,
            "monthly_spend_usd": state.monthly_spend_usd,
            "buffered_signals": len(state.raw_signals),
            "dedup_cache_size": len(state.dedup_cache),
        }
    except Exception as e:
        logger.error("Failed to load scheduled jobs status: %s", e)
        result["scheduled_jobs"] = {"error": str(e)}

    # 2. Session hooks
    try:
        from core import session_registry
        executor = session_registry.hook_executor
        if executor:
            result["session_hooks"] = executor.get_status()
        else:
            result["session_hooks"] = {"worker_running": False, "hooks": [], "queue_size": 0}
    except Exception as e:
        logger.error("Failed to get hook status: %s", e)
        result["session_hooks"] = {"error": str(e)}

    # 3. Managed services
    try:
        from core.service_manager import service_manager
        result["services"] = service_manager.get_status()
    except Exception as e:
        logger.error("Failed to get service status: %s", e)
        result["services"] = [{"error": str(e)}]

    # 4. Overview
    sj = result["scheduled_jobs"]
    sh = result["session_hooks"]
    hooks_count = len(sh.get("hooks", []))
    services_count = len(result["services"])
    result["overview"] = {
        "total_scheduled_jobs": sj.get("total", 0),
        "total_session_hooks": hooks_count,
        "total_services": services_count,
        "total_components": sj.get("total", 0) + hooks_count + services_count,
    }

    return result


class JobRunEntry(BaseModel):
    date: str
    status: str
    tokens: int = 0
    duration: float = 0.0
    has_output: bool = False


class JobRunsResponse(BaseModel):
    job_id: str
    last_output: str | None = None
    last_output_date: str | None = None
    recent: list[JobRunEntry] = []


def _slug(job_id: str) -> str:
    """Mirror executor._write_job_result's filename slug (executor.py:2219)."""
    return job_id.replace(" ", "-").lower()


def _read_output_body(md_path) -> str | None:
    """Read a JobResults .md, strip its YAML frontmatter, cap the body. None on any error."""
    try:
        text = md_path.read_text(errors="replace")[:_MAX_OUTPUT_BYTES]
    except OSError:
        return None
    # Strip leading '---\n...\n---\n' frontmatter if present.
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 1)
            if nl != -1:
                text = text[nl + 1:]
    return text.lstrip("\n") or None


@router.get("/{job_id}/runs", response_model=JobRunsResponse)
async def job_runs(job_id: str):
    """Per-job run history: real per-run status/tokens/duration from the JSONL index,
    plus the FULL last-run output body from the latest run's markdown.

    Pure read, fail-soft: a never-run job, a missing index, or a path-traversal
    job_id all return an empty payload (never a 500, never a file outside
    JOB_RESULTS_DIR). Paths are always derived inside JOB_RESULTS_DIR and
    containment-checked with resolve()+is_relative_to — the raw param is never
    concatenated into a path without that guard.
    """
    empty = JobRunsResponse(job_id=job_id, last_output=None, last_output_date=None, recent=[])

    # The whole history read (JOB_RESULTS_JSONL read + parse + per-run .md reads) is
    # blocking FS I/O — run it in ONE worker thread (run_b2d3ece0), not on the loop.
    def _build() -> JobRunsResponse:
        if not JOB_RESULTS_JSONL.exists():
            return empty
        matches: list[dict] = []
        for line in JOB_RESULTS_JSONL.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            # EXACT match only — no prefix/substring (kills channel-monitor vs
            # channel-monitor-fallback collision) and no path is built from job_id.
            if rec.get("job_id") == job_id:
                matches.append(rec)

        if not matches:
            return empty

        # Newest first by run_at ISO string (sortable). Skip records lacking run_at.
        matches.sort(key=lambda r: r.get("run_at") or "", reverse=True)

        recent: list[JobRunEntry] = []
        for rec in matches[:_RECENT_LIMIT]:
            run_at = rec.get("run_at") or ""
            date = run_at[:10]
            md_name = f"{date}-{_slug(job_id)}.md" if date else ""
            has_output = False
            if md_name:
                candidate = (JOB_RESULTS_DIR / md_name).resolve()
                # Containment guard: candidate MUST live inside JOB_RESULTS_DIR.
                if candidate.is_relative_to(JOB_RESULTS_DIR.resolve()) and candidate.is_file():
                    has_output = True
            recent.append(JobRunEntry(
                date=date,
                status=str(rec.get("status", "unknown")),
                tokens=int(rec.get("tokens_used", 0) or 0),
                duration=float(rec.get("duration_seconds", 0.0) or 0.0),
                has_output=has_output,
            ))

        # Full last-output body: the newest run that actually has a readable .md.
        last_output: str | None = None
        last_output_date: str | None = None
        for rec in matches:
            date = (rec.get("run_at") or "")[:10]
            if not date:
                continue
            candidate = (JOB_RESULTS_DIR / f"{date}-{_slug(job_id)}.md").resolve()
            if not candidate.is_relative_to(JOB_RESULTS_DIR.resolve()) or not candidate.is_file():
                continue
            body = _read_output_body(candidate)
            if body:
                last_output = body
                last_output_date = date
                break

        return JobRunsResponse(
            job_id=job_id,
            last_output=last_output,
            last_output_date=last_output_date,
            recent=recent,
        )

    try:
        return await asyncio.to_thread(_build)
    except Exception as e:  # fail-soft: the drawer must never 500
        logger.error("job_runs failed for '%s': %s", job_id, e)
        return empty
