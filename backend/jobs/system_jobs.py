"""
Swarm Job System — System Job Definitions

System jobs defined in code (not YAML). These are provisioned automatically
and cannot be deleted by users. User jobs live in user-jobs.yaml.

Schedule format: standard 5-field cron (minute hour dom month dow).
Dependency format: "after:<job-id>" — runs after dependency completes (success or failure).
"""

from __future__ import annotations

import os
from pathlib import Path

from config import get_swarmws
from utils.backend_bin import bundled_path, py_command

from .models import Job, JobSafety


def _py(*args: str) -> str:
    """A script job's command run by the backend program's own Python
    (``python-backend py ...``, design 2026-10-24-swarmai-usable-by-anyone §3.2): no
    system Python and no source checkout needed."""
    import shlex
    return shlex.join(py_command(*args))


# the product's Python script jobs need no source checkout and set no cwd: the executor
# runs a script job without one in config.get_app_data_dir() (jobs/executor.py)

# All times in UTC
SYSTEM_JOBS: list[Job] = [
    # --- Maintenance (lightweight: prune caches, trim state, reset counters) ---
    # Monday 11:00 ICT (03:00 UTC) — start of work week, user is at desk.
    # If laptop was closed, cron_utils catch-up (48h window) retriggers on next boot.
    Job(
        id="weekly-maintenance",
        name="Weekly Maintenance",
        type="maintenance",
        schedule="0 6 * * 1",          # Monday UTC 06:00 = ICT 14:00
        enabled=True,
        category="system",
        config={},
    ),

    # --- Memory Health (LLM-powered: stale entry pruning, gap detection) ---
    Job(
        id="memory-health",
        name="Memory Health Check",
        type="memory_health",
        schedule="15 6 * * 1",         # Monday UTC 06:15 = ICT 14:15
        enabled=True,
        category="system",
        config={},
    ),

    # --- Runtime Session-Health Probe (zero-LLM daemon+session liveness) ---
    # Distinct axis from loops-health (static self-cognition): this is the
    # DYNAMIC runtime snapshot — daemon up, sessions progressing (not wedged,
    # RP41 double-signal), RSS under budget, no unrecovered failure events.
    # Red → Slack. (run_f646b175)
    Job(
        id="session-health-probe",
        name="Runtime Session-Health Probe",
        type="session_health_probe",
        schedule="*/15 * * * *",       # every 15 minutes
        enabled=True,
        category="system",
        config={},
    ),

    # --- Scheduled Full Eval + Drift Alert (run_5edf2cc0 G7; renamed run_95d9acbc) ---
    # Runs the FULL golden set (programmatic + LLM judge — Bedrock cost is fine on
    # this cadence, never gates) and compares the overall score vs the prior run.
    # Alerts Slack on BVT-red (spine regression) OR score-drift below baseline.
    # The gate (ci_eval_gate / prod.sh release) is the HARD stop; this is the
    # continuous-monitoring eye that catches model/dependency drift (AWS
    # Eval-First: "baseline is a drifting quantity, retest continuously").
    # NOTE: cron fires EVERY Monday 12:30 ICT, but the handler's OWN 14-day gate
    # (_should_run_biweekly, its own timestamp file) makes a REAL run happen only
    # once per 2 weeks (run_6980cb35). This is now the SINGLE biweekly driver and
    # runs the FULL golden set INCLUDING behavior-tier cases (include_behavior=True).
    # Behavior spawns real agents (~17-95s typical, per-case cap 240s → worst-case ~96min): this is an
    # IN-PROCESS handler so JobSafety.timeout_seconds does NOT bound it (only the
    # per-behavior-case spawn timeouts inside eval_trajectory_capture do) — the
    # 3600 below documents intent for the learner, not an enforced wall-clock.
    # The old weekly os-eval-biweekly CLI job + the separate monthly behavior job
    # were retired in favour of this one (behavior folded into the biweekly sweep).
    Job(
        id="eval-scheduled",
        name="Scheduled Full Eval + Drift Alert (every 2 weeks, incl behavior)",
        type="eval_scheduled",
        schedule="30 10 * * 1",         # Monday UTC 10:30 = ICT 18:30 — LAST Monday slot on purpose: this
                                        # runs ~96min in-process and blocks the serial scheduler loop, so it
                                        # must trail every other weekday job (nothing time-sensitive queues
                                        # behind it). 14-day gate in handler → real run only biweekly.
        enabled=True,
        category="system",
        safety=JobSafety(timeout_seconds=3600),
        config={},
    ),

    # --- Pipeline-run worktree sweep (docs/design/pipeline-run-worktree.md §8.3) ---
    # In-process (needs SessionRouter for owner liveness). Releases worktrees of ended
    # or long-idle runs (unintegrated work → verified ledger snapshot first), quarantines
    # unregistered dirs. No-op (no git calls) while ~/.swarm-ai/worktrees is absent.
    Job(
        id="worktree-sweep",
        name="Pipeline worktree sweep",
        type="worktree_sweep",
        schedule="*/30 * * * *",       # interval probe (the scheduler loop runs hourly)
        enabled=True,
        category="system",
        safety=JobSafety(timeout_seconds=300),
        config={},
    ),

    # --- Hive run records (docs/design/2026-10-29-hive-run-record-and-worker-role.md §4.5) ---
    # Removes a session's record untouched for 30 days and stale temporary files.
    # No-op on an instance with no Hive/records/.
    Job(
        id="records-sweep",
        name="Hive run record sweep",
        type="records_sweep",
        schedule="30 5 * * 1-5",        # weekdays UTC 05:30 = ICT 13:30 (job window policy)
        enabled=True,
        category="system",
        safety=JobSafety(timeout_seconds=300),
        config={},
    ),

    # --- Daily log patrol (design chat-transcript-single-source S5) ---
    # New or doubling WARNING/ERROR kinds, 4xx/5xx routes and gates timing out
    # (failing open), compared with the previous 7 days; one Slack message.
    Job(
        id="log-patrol",
        name="Daily log patrol",
        type="log_patrol",
        schedule="0 6 * * 1-5",         # weekdays UTC 06:00 = ICT 13:00 (job window policy)
        enabled=True,
        category="system",
        safety=JobSafety(timeout_seconds=300),
        config={},
    ),

    # --- Library mount freshness (a mounted folder's index follows its files) ---
    # Re-probe each registered mount's source (exists + edited-after-index), persist
    # health, and re-index in a child every mount whose files changed since its last
    # index (or whose kind, judged again, changed). No LLM. The probe is a stat walk
    # of each mount; an unchanged mount costs nothing more.
    Job(
        id="library-freshness",
        name="Library Mount Freshness — re-index mounts whose files changed",
        type="library_freshness",
        schedule="*/30 * * * *",        # interval (the scheduler loop runs hourly)
        enabled=True,
        category="system",
        safety=JobSafety(timeout_seconds=120),
        config={},
    ),

    # --- Library Health (keep the Native store from rotting into a graveyard) ---
    # Weekly: moves the JobResults logs older than 90 days into
    # Archives/ by itself (reversible) and writes .library-health.json with what it
    # moved (the Library overlay's Recent tab says so). No LLM, zero token; it never
    # deletes (delete stays a confirm-gated API action).
    Job(
        id="library-health",
        name="Library Health — archive old raw logs in Knowledge/",
        type="library_health",
        schedule="0 7 * * 1",           # Monday UTC 07:00 = ICT 15:00 (weekly, weekday)
        enabled=True,
        category="system",
        safety=JobSafety(timeout_seconds=120),
        config={},
    ),

    # --- Online eval scoring (jobs/handlers/session_quality.py) ---
    # Weekly: checks the owner's sessions from the last 7 days (O1–O3 over every
    # session, the judge on at most 10) and writes the state the Online tab and the
    # follow-ups read. Friday, end of week (no weekend clock jobs: test_job_schedules
    # test_no_clock_job_runs_on_weekend; weekend runs go unmonitored).
    Job(
        id="session-quality",
        name="Online eval scoring — check the last 7 days of sessions",
        type="session_quality",
        schedule="0 8 * * 5",          # Friday UTC 08:00 = ICT 16:00 (end-of-week, weekday-only policy)
        enabled=True,
        category="system",
        safety=JobSafety(timeout_seconds=1800),
        config={},
    ),

    # --- CDF health (core/ingestion/health.py; design 2026-10-31-cdf-health) ---
    # Each weekday: the cognition system's deterministic checks, a judge's score of a
    # sample of new entries per destination, and its reading. Scores only, never edits.
    Job(
        id="cdf-health",
        name="CDF health — check the pipeline and score new entries",
        type="cdf_health",
        schedule="0 9 * * 1-5",        # weekday UTC 09:00 = ICT 17:00, after the day's sessions
        enabled=True,
        category="system",
        safety=JobSafety(timeout_seconds=1800),
        config={},
    ),

    # --- Brain setup resume: a New Brain whose setup session ended is continued as one
    # agent task (jobs/handlers/brain_setup_resume; nothing waits for "Continue setup") ---
    Job(
        id="brain-setup-resume",
        name="Continue Brain Setups",
        type="brain_setup_resume",
        schedule="15 5-10 * * 1-5",     # hourly in the weekday job window (UTC 5-10); stalled = idle 2 hours
        enabled=True,
        category="system",
        config={},
    ),

    # --- DDD Auto-Refresh (detect stale project docs, generate proposals) ---
    Job(
        id="ddd-refresh",
        name="DDD Auto-Refresh",
        type="ddd_refresh",
        schedule="30 5 * * 1",         # Monday UTC 05:30 = ICT 13:30
        enabled=True,
        category="system",
        config={},
    ),

    # --- Brain Mistake Audit (monthly; design 2026-10-30-brain-driven §2.8) ---
    # For each real mistake of the previous month: was the knowledge in the brain
    # beforehand, was it delivered, was it stale. Read-only; writes only its report to
    # Knowledge/JobResults/. Never re-judges or edits the brain.
    Job(
        id="brain-mistake-audit",
        name="Brain Mistake Audit",
        type="brain_mistake_audit",
        schedule="0 6 1 * *",           # 1st of the month UTC 06:00 = ICT 14:00 (afternoon window)
        enabled=True,
        category="system",
        config={},
    ),

    # (The SwarmAI Monthly Report job was removed: an MBR of SwarmAI's own subsystems
    # is not a product job. Design 2026-10-24-swarmai-usable-by-anyone P7.)

    # --- Skill Proposer (reads health_findings.json, proposes skills for gaps) ---
    # Decoupled from memory-health: health_findings.json is populated by
    # ContextHealthHook (every session) AND memory-health (weekly LLM).
    # Skill proposer works fine with stale/partial data — no reason to block
    # on memory-health success.
    Job(
        id="skill-proposer",
        name="Skill Proposer",
        type="skill_proposer",
        schedule="45 5 * * 1",          # Monday UTC 05:45 = ICT 13:45
        enabled=True,
        category="system",
        config={},
    ),

    # (Todo Resolution job removed run_50db230a — it auto-resolved/cancelled todos,
    # incl. user manual ones (git-keyword + staleness layers had no source filter).
    # The ToDo card is now a pure user-planning surface: the user owns their todos'
    # lifecycle, the system neither writes nor auto-cancels them.)

    # --- Loops Health (7-dimension self-maintenance scan) ---
    # Scans context files, DailyActivity, Knowledge/, Projects/, Evolution state,
    # git backup, and infrastructure health (31 checks). Auto-fixes safe mechanical
    # issues. Reports Found/Fixed/Pending with a health score (0-100).
    # Script uses Path.home()/.swarm-ai — works in daemon context without shell env.
    # NOTE: directory has hyphen (s_loops-health) so `py -m` doesn't work;
    # run the script file the program ships (bundled_path).
    Job(
        id="loops-health",
        name="Self-Loops Health Monitor",
        type="script",
        schedule="0 10 * * 1",         # Monday UTC 10:00 = ICT 18:00 (staggered off weekly-maintenance)
        enabled=True,
        category="system",
        config={
            "command": _py(
                str(bundled_path("skills", "s_loops-health", "scripts", "loops_health_check.py")),
                "--auto-fix",
                "--output-dir", str(get_swarmws() / "Knowledge" / "JobResults"),
            ),
        },
        safety=JobSafety(timeout_seconds=300),
    ),

    # --- Code Intelligence refresh (hourly) ---
    # Every owned graph is brought to its repo's HEAD (core.code_intel.build.ensure_fresh):
    # an unmoved repo costs one `git rev-parse`; up to 100 changed files are updated in
    # this process; more start a detached rebuild child. Runs as a job, never in the
    # daemon (design 2026-10-29-code-intel-unification §0.2). The scheduler loop runs
    # hourly, so this is at most once an hour.
    Job(
        id="code-intel-reindex",
        name="Code Intelligence Refresh",
        type="script",
        schedule="*/30 * * * *",       # interval (the scheduler loop runs hourly)
        enabled=True,
        category="system",
        config={
            "command": _py("-m", "jobs.handlers.code_intel_reindex"),
        },
        safety=JobSafety(timeout_seconds=120),
    ),
]

SYSTEM_JOB_IDS: set[str] = {j.id for j in SYSTEM_JOBS}


def get_all_system_jobs() -> list[Job]:
    """Return a copy of all system job definitions."""
    return list(SYSTEM_JOBS)
