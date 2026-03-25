"""
Swarm Jobs API — Unified endpoint for job system management.

Provides REST API for listing, running, and managing both system
and user jobs. Aggregates all three execution models (hooks, cron, services)
into a single status view.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("swarm.routers.jobs")

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


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
        from backend.jobs.scheduler import load_jobs, load_state
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
        from backend.jobs.scheduler import (
            load_jobs, load_config, load_feeds, load_state,
            save_state, load_user_context, load_defaults,
        )
        from backend.jobs.executor import execute_job

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
        save_state(state)

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
async def scheduler_status():
    """Get scheduler overview — monthly spend, signal buffer, job summary."""
    try:
        from backend.jobs.scheduler import load_state, load_jobs
        state = load_state()
        jobs = load_jobs()

        ok = sum(1 for j in jobs if state.jobs.get(j.id) and state.jobs[j.id].last_status == "success")
        err = sum(1 for j in jobs if state.jobs.get(j.id) and state.jobs[j.id].consecutive_failures > 0)

        return {
            "total_jobs": len(jobs),
            "healthy": ok,
            "failing": err,
            "never_run": len(jobs) - ok - err,
            "monthly_spend_usd": state.monthly_spend_usd,
            "buffered_signals": len(state.raw_signals),
            "dedup_cache_size": len(state.dedup_cache),
        }
    except Exception as e:
        logger.error("Failed to get scheduler status: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
