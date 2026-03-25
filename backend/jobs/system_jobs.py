"""
Swarm Job System — System Job Definitions

System jobs defined in code (not YAML). These are provisioned automatically
and cannot be deleted by users. User jobs live in user-jobs.yaml.

Schedule format: standard 5-field cron (minute hour dom month dow).
Dependency format: "after:<job-id>" — runs after dependency succeeds.
"""

from __future__ import annotations

from .models import Job, JobSafety

# All times in UTC
SYSTEM_JOBS: list[Job] = [
    # --- Signal Pipeline ---
    Job(
        id="signal-fetch",
        name="Fetch Signals",
        type="signal_fetch",
        schedule="0 8,14,20 * * *",   # 3x daily: 8am, 2pm, 8pm UTC
        enabled=True,
        category="system",
        config={"max_age_hours": 48},
    ),
    Job(
        id="signal-digest",
        name="Digest Signals",
        type="signal_digest",
        schedule="after:signal-fetch",
        enabled=True,
        category="system",
        config={},
    ),

    # --- Self-Tune ---
    Job(
        id="self-tune",
        name="Self-Tune Feeds",
        type="script",
        schedule="0 7 * * *",          # Daily 7am UTC (before first fetch at 8am)
        enabled=True,
        category="system",
        config={"command": "self-tune"},  # Handled specially by executor
    ),

    # --- Maintenance ---
    Job(
        id="weekly-maintenance",
        name="Weekly Maintenance",
        type="maintenance",
        schedule="0 3 * * 0",          # Sunday 3am UTC
        enabled=True,
        category="system",
        config={},
    ),

    # --- Weekly Rollup ---
    Job(
        id="weekly-rollup",
        name="Weekly Signal Rollup",
        type="signal_digest",
        schedule="0 20 * * 0",         # Sunday 8pm UTC
        enabled=True,
        category="system",
        config={"window_days": 7},
    ),
]

SYSTEM_JOB_IDS: set[str] = {j.id for j in SYSTEM_JOBS}


def get_all_system_jobs() -> list[Job]:
    """Return a copy of all system job definitions."""
    return list(SYSTEM_JOBS)
