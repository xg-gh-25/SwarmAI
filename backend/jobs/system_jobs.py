"""
Swarm Job System — System Job Definitions

System jobs defined in code (not YAML). These are provisioned automatically
and cannot be deleted by users. User jobs live in user-jobs.yaml.

Schedule format: standard 5-field cron (minute hour dom month dow).
Dependency format: "after:<job-id>" — runs after dependency succeeds.
"""

from __future__ import annotations

from pathlib import Path

from .models import Job, JobSafety

# swarmai/ root — used as cwd for script jobs that need `python -m backend.jobs.*`
_SWARMAI_ROOT = str(Path(__file__).resolve().parents[2])

# All times in UTC
SYSTEM_JOBS: list[Job] = [
    # --- Signal Pipeline ---
    Job(
        id="signal-fetch",
        name="Fetch Signals",
        type="signal_fetch",
        schedule="0 2,8,14 * * *",   # 3x daily: ICT 10:00, 16:00, 22:00
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
        schedule="0 1 * * *",          # Daily ICT 09:00 (before first fetch at ICT 10:00)
        enabled=True,
        category="system",
        config={"command": "python -m backend.jobs.self_tune", "cwd": _SWARMAI_ROOT},
    ),

    # --- Maintenance (lightweight: prune caches, trim state, reset counters) ---
    Job(
        id="weekly-maintenance",
        name="Weekly Maintenance",
        type="maintenance",
        schedule="0 3 * * 0",          # Sunday 3am UTC
        enabled=True,
        category="system",
        config={},
    ),

    # --- Memory Health (LLM-powered: stale entry pruning, gap detection) ---
    Job(
        id="memory-health",
        name="Memory Health Check",
        type="memory_health",
        schedule="15 3 * * 0",         # Sunday 3:15am UTC (after maintenance)
        enabled=True,
        category="system",
        config={},
    ),

    # --- DDD Auto-Refresh (detect stale project docs, generate proposals) ---
    Job(
        id="ddd-refresh",
        name="DDD Auto-Refresh",
        type="ddd_refresh",
        schedule="30 3 * * 0",         # Sunday 3:30am UTC (after memory health)
        enabled=True,
        category="system",
        config={},
    ),

    # --- Skill Proposer (reads health_findings.json, proposes skills for gaps) ---
    Job(
        id="skill-proposer",
        name="Skill Proposer",
        type="skill_proposer",
        schedule="after:memory-health", # Depends on health_findings.json from memory-health
        enabled=True,
        category="system",
        config={},
    ),

    # --- Weekly Rollup ---
    Job(
        id="weekly-rollup",
        name="Weekly Signal Rollup",
        type="signal_digest",
        schedule="0 2 * * 1",          # Monday ICT 10:00 — fresh weekly rollup
        enabled=True,
        category="system",
        config={"window_days": 7},
    ),
]

SYSTEM_JOB_IDS: set[str] = {j.id for j in SYSTEM_JOBS}


def get_all_system_jobs() -> list[Job]:
    """Return a copy of all system job definitions."""
    return list(SYSTEM_JOBS)
