"""
Swarm Job System — System Job Definitions

System jobs defined in code (not YAML). These are provisioned automatically
and cannot be deleted by users. User jobs live in user-jobs.yaml.

Schedule format: standard 5-field cron (minute hour dom month dow).
Dependency format: "after:<job-id>" — runs after dependency completes (success or failure).
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
    # Monday 11:00 ICT (03:00 UTC) — start of work week, user is at desk.
    # If laptop was closed, cron_utils catch-up (48h window) retriggers on next boot.
    Job(
        id="weekly-maintenance",
        name="Weekly Maintenance",
        type="maintenance",
        schedule="0 3 * * 1",          # Monday 03:00 UTC = 11:00 ICT
        enabled=True,
        category="system",
        config={},
    ),

    # --- Memory Health (LLM-powered: stale entry pruning, gap detection) ---
    Job(
        id="memory-health",
        name="Memory Health Check",
        type="memory_health",
        schedule="15 3 * * 1",         # Monday 03:15 UTC = 11:15 ICT
        enabled=True,
        category="system",
        config={},
    ),

    # --- DDD Auto-Refresh (detect stale project docs, generate proposals) ---
    Job(
        id="ddd-refresh",
        name="DDD Auto-Refresh",
        type="ddd_refresh",
        schedule="30 3 * * 1",         # Monday 03:30 UTC = 11:30 ICT
        enabled=True,
        category="system",
        config={},
    ),

    # --- Skill Proposer (reads health_findings.json, proposes skills for gaps) ---
    # Decoupled from memory-health: health_findings.json is populated by
    # ContextHealthHook (every session) AND memory-health (weekly LLM).
    # Skill proposer works fine with stale/partial data — no reason to block
    # on memory-health success.
    Job(
        id="skill-proposer",
        name="Skill Proposer",
        type="skill_proposer",
        schedule="45 3 * * 1",          # Monday 03:45 UTC = 11:45 ICT
        enabled=True,
        category="system",
        config={},
    ),

    # --- Signal Digest → Slack Notification ---
    # Fires after each digest, reads signal_digest.json, sends top items as Slack DM.
    # Disabled by default — requires ~/.swarm-ai/notify-channels.yaml with a
    # slack channel configured.  Enable via user-jobs.yaml override or by
    # creating the notify config file.
    Job(
        id="signal-notify-slack",
        name="Signal Digest → Slack",
        type="notify",
        schedule="after:signal-digest",
        enabled=False,
        category="system",
        config={
            "channel": "slack",
            "source": "signal_digest",  # read from signal_digest.json
            "max_items": 10,
        },
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

    # --- Evolution Cycle (standalone fallback) ---
    # Primary trigger is session-close hook (evolution_maintenance_hook.py),
    # but if the user's laptop is closed for days, sessions don't end and
    # the hook never fires. This scheduled job ensures the mine→score→optimize
    # pipeline runs at least once per week regardless of session activity.
    # Uses the same run_evolution_cycle() as the hook — idempotent via the
    # .evolution_last_run state file (7-day minimum interval).
    Job(
        id="evolution-cycle",
        name="Evolution Cycle",
        type="script",
        schedule="0 4 * * 4",          # Thursday 04:00 UTC = 12:00 ICT
        enabled=True,
        category="system",
        config={
            "command": "python -m backend.jobs.run_evolution",
            "cwd": _SWARMAI_ROOT,
        },
    ),
]

SYSTEM_JOB_IDS: set[str] = {j.id for j in SYSTEM_JOBS}


def get_all_system_jobs() -> list[Job]:
    """Return a copy of all system job definitions."""
    return list(SYSTEM_JOBS)
