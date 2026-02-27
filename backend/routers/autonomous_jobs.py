"""Autonomous jobs placeholder API router.

Provides a single GET endpoint that returns hardcoded mock data for the
Swarm Radar Autonomous Jobs zone. This is a placeholder implementation
for the initial release — future versions will query actual job state
from the database.

Key endpoints:

- ``GET /``  — Returns list of mock autonomous jobs (HTTP 200)

The router is registered in main.py with prefix ``/api/autonomous-jobs``.
"""

from fastapi import APIRouter
from schemas.autonomous_job import (
    AutonomousJobResponse,
    AutonomousJobCategory,
    AutonomousJobStatus,
)

router = APIRouter()


# Hardcoded mock data for the initial release
MOCK_JOBS: list[AutonomousJobResponse] = [
    # System built-in jobs (3)
    AutonomousJobResponse(
        id="sys-job-001",
        name="Workspace Sync",
        category=AutonomousJobCategory.SYSTEM,
        status=AutonomousJobStatus.RUNNING,
        schedule=None,
        last_run_at="2025-01-15T10:30:00Z",
        next_run_at=None,
        description="Synchronizes workspace files and settings across devices",
    ),
    AutonomousJobResponse(
        id="sys-job-002",
        name="Knowledge Indexing",
        category=AutonomousJobCategory.SYSTEM,
        status=AutonomousJobStatus.RUNNING,
        schedule=None,
        last_run_at="2025-01-15T10:25:00Z",
        next_run_at=None,
        description="Indexes workspace documents for semantic search",
    ),
    AutonomousJobResponse(
        id="sys-job-003",
        name="Overdue Check",
        category=AutonomousJobCategory.SYSTEM,
        status=AutonomousJobStatus.RUNNING,
        schedule=None,
        last_run_at="2025-01-15T10:00:00Z",
        next_run_at=None,
        description="Scans ToDos for overdue items and updates their status",
    ),
    # User-defined jobs (2)
    AutonomousJobResponse(
        id="user-job-001",
        name="Daily Digest",
        category=AutonomousJobCategory.USER_DEFINED,
        status=AutonomousJobStatus.RUNNING,
        schedule="Daily at 9am",
        last_run_at="2025-01-15T09:00:00Z",
        next_run_at="2025-01-16T09:00:00Z",
        description="Generates a daily summary of workspace activity",
    ),
    AutonomousJobResponse(
        id="user-job-002",
        name="Weekly Report",
        category=AutonomousJobCategory.USER_DEFINED,
        status=AutonomousJobStatus.PAUSED,
        schedule="Every Monday at 8am",
        last_run_at="2025-01-13T08:00:00Z",
        next_run_at=None,
        description="Compiles weekly progress report from completed tasks",
    ),
]


@router.get("", response_model=list[AutonomousJobResponse])
async def list_autonomous_jobs() -> list[AutonomousJobResponse]:
    """Return all autonomous jobs.

    In the initial release, this returns hardcoded mock data.
    Always returns HTTP 200 — there are no error cases.
    """
    return MOCK_JOBS
