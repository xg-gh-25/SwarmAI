"""Autonomous jobs API router — Radar panel data source.

Reads job definitions from ``backend/jobs/system_jobs.py`` (system) and
``user-jobs.yaml`` (user) plus runtime state from ``state.json``.
Serves real data to the Radar JOBS panel via ``/api/autonomous-jobs``.

Key endpoints:

- ``GET /``  — Returns list of autonomous jobs with runtime state (HTTP 200)

The router is registered in main.py with prefix ``/api/autonomous-jobs``.

NOTE: This is the READ endpoint for the Radar UI. The WRITE endpoints
(run, status) live in ``routers/jobs.py`` at ``/api/jobs/``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from schemas.autonomous_job import (
    AutonomousJobCategory,
    AutonomousJobResponse,
    AutonomousJobStatus,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _derive_status(enabled: bool, consecutive_failures: int) -> AutonomousJobStatus:
    """Derive display status from job state.

    Logic:
      - disabled → paused
      - consecutive_failures >= 3 → error (circuit breaker tripped)
      - enabled + healthy → running
    """
    if not enabled:
        return AutonomousJobStatus.PAUSED
    if consecutive_failures >= 3:
        return AutonomousJobStatus.ERROR
    return AutonomousJobStatus.RUNNING


@router.get("", response_model=list[AutonomousJobResponse])
async def list_autonomous_jobs() -> list[AutonomousJobResponse]:
    """Return all autonomous jobs with runtime state.

    Reads from the unified job system (backend/jobs/) which loads
    system jobs from code (system_jobs.py) and user jobs from
    user-jobs.yaml, with runtime state from state.json.
    """
    try:
        from jobs.scheduler import load_jobs, load_state

        jobs = load_jobs()
        state = load_state()
    except Exception as e:
        logger.warning("Failed to load jobs: %s", e)
        return []

    results: list[AutonomousJobResponse] = []
    for job in jobs:
        js = state.jobs.get(job.id)
        consecutive_failures = js.consecutive_failures if js else 0
        status = _derive_status(job.enabled, consecutive_failures)
        category = (
            AutonomousJobCategory.USER_DEFINED
            if job.category in ("user", "user_defined")
            else AutonomousJobCategory.SYSTEM
        )

        results.append(AutonomousJobResponse(
            id=job.id,
            name=job.name,
            category=category,
            status=status,
            schedule=job.schedule,
            last_run_at=js.last_run.isoformat() if (js and js.last_run) else None,
            next_run_at=None,  # Would need cron-next calc — omit for now
            description=getattr(job, "description", None),
            total_runs=js.total_runs if js else 0,
            consecutive_failures=consecutive_failures,
            last_status=js.last_status if js else "never",
        ))

    return results
