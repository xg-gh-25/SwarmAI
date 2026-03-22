"""Autonomous jobs API router.

Provides a single GET endpoint that returns autonomous jobs for the
Swarm Radar Jobs zone. Currently returns an empty list — future versions
will query actual job state from the scheduler/database.

Key endpoints:

- ``GET /``  — Returns list of autonomous jobs (HTTP 200)

The router is registered in main.py with prefix ``/api/autonomous-jobs``.
"""

from fastapi import APIRouter
from schemas.autonomous_job import AutonomousJobResponse

router = APIRouter()


@router.get("", response_model=list[AutonomousJobResponse])
async def list_autonomous_jobs() -> list[AutonomousJobResponse]:
    """Return all autonomous jobs.

    Returns an empty list until the job scheduler is implemented.
    Always returns HTTP 200 — there are no error cases.
    """
    return []
