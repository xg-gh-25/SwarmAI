"""Autonomous jobs API router.

Reads job definitions from ``jobs.yaml`` (system) and ``user-jobs.yaml`` (user)
plus runtime state from ``state.json`` in the SwarmWS scheduler directory.
Serves real data to the Radar JOBS panel.

Key endpoints:

- ``GET /``  — Returns list of autonomous jobs with runtime state (HTTP 200)

The router is registered in main.py with prefix ``/api/autonomous-jobs``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter

import yaml

from schemas.autonomous_job import (
    AutonomousJobCategory,
    AutonomousJobResponse,
    AutonomousJobStatus,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# SwarmWS job scheduler directory
_SWARMWS = Path.home() / ".swarm-ai" / "SwarmWS"
_JOBS_DIR = _SWARMWS / "Services" / "swarm-jobs"
_JOBS_FILE = _JOBS_DIR / "jobs.yaml"
_USER_JOBS_FILE = _JOBS_DIR / "user-jobs.yaml"
_STATE_FILE = _JOBS_DIR / "state.json"


def _load_jobs_yaml() -> list[dict[str, Any]]:
    """Load job definitions from jobs.yaml + user-jobs.yaml.

    Returns raw dicts. Silently returns empty list if files don't exist
    (scheduler not set up yet).
    """
    all_jobs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for path in (_JOBS_FILE, _USER_JOBS_FILE):
        if not path.exists():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for jd in data.get("jobs", []):
                job_id = jd.get("id")
                if not job_id or job_id in seen_ids:
                    continue
                all_jobs.append(jd)
                seen_ids.add(job_id)
        except Exception as e:
            logger.warning("Failed to load %s: %s", path.name, e)

    return all_jobs


def _load_state() -> dict[str, Any]:
    """Load scheduler runtime state from state.json.

    Returns raw dict. Empty dict if file doesn't exist.
    """
    if not _STATE_FILE.exists():
        return {}
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load state.json: %s", e)
        return {}


def _derive_status(job_def: dict, job_state: dict | None) -> AutonomousJobStatus:
    """Derive display status from job definition + runtime state.

    Logic:
      - disabled in yaml → paused
      - consecutive_failures >= 3 → error (circuit breaker tripped)
      - last_status == "failed" → error
      - enabled + no failures → running (scheduled and healthy)
    """
    if not job_def.get("enabled", True):
        return AutonomousJobStatus.PAUSED

    if job_state:
        if job_state.get("consecutive_failures", 0) >= 3:
            return AutonomousJobStatus.ERROR
        if job_state.get("last_status") == "failed":
            return AutonomousJobStatus.ERROR

    return AutonomousJobStatus.RUNNING


def _map_category(raw: str) -> AutonomousJobCategory:
    """Map yaml category string to enum."""
    if raw in ("user", "user_defined"):
        return AutonomousJobCategory.USER_DEFINED
    return AutonomousJobCategory.SYSTEM


@router.get("", response_model=list[AutonomousJobResponse])
async def list_autonomous_jobs() -> list[AutonomousJobResponse]:
    """Return all autonomous jobs with runtime state.

    Reads job definitions from yaml files and merges with runtime state
    from state.json. Always returns HTTP 200 — empty list if scheduler
    isn't set up.
    """
    job_defs = _load_jobs_yaml()
    if not job_defs:
        return []

    state_data = _load_state()
    jobs_state = state_data.get("jobs", {})

    results: list[AutonomousJobResponse] = []
    for jd in job_defs:
        job_id = jd["id"]
        js = jobs_state.get(job_id, {})

        status = _derive_status(jd, js)
        category = _map_category(jd.get("category", "system"))

        # Format last_run timestamp
        last_run = js.get("last_run")
        last_run_at = last_run if isinstance(last_run, str) else None

        results.append(AutonomousJobResponse(
            id=job_id,
            name=jd.get("name", job_id),
            category=category,
            status=status,
            schedule=jd.get("schedule"),
            last_run_at=last_run_at,
            next_run_at=None,  # Would need cron-next calc — omit for now
            description=None,
            total_runs=js.get("total_runs", 0),
            consecutive_failures=js.get("consecutive_failures", 0),
            last_status=js.get("last_status", "never"),
        ))

    return results
