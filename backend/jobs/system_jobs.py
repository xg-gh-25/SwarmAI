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

from .models import Job, JobSafety


def _get_swarmai_root() -> str:
    """Resolve the swarmai source tree root for script job cwd.

    Works in both contexts:
    - Dev: __file__ is inside swarmai/backend/jobs/ → parents[2] works
    - Daemon binary: __file__ resolves to daemon/_internal/ → parents[2] is WRONG

    Resolution order:
    1. SWARMAI_SOURCE env var (explicit override, used by loops_health_check.py too)
    2. Path.home() / "Desktop/SwarmAI-Workspace/swarmai" (canonical dev location)
    3. __file__-based fallback (only correct in dev, kept for compatibility)
    """
    # 1. Env var (highest priority — works in all contexts)
    env_source = os.environ.get("SWARMAI_SOURCE")
    if env_source and Path(env_source).is_dir():
        return env_source

    # 2. Canonical locations (platform-aware)
    candidates = [
        Path.home() / "Desktop" / "SwarmAI-Workspace" / "swarmai",  # macOS dev
        Path.home() / "swarmai",                                      # Hive EC2
        Path("/opt/swarmai"),                                         # Hive alternate
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return str(candidate)

    # 3. __file__-based (only correct in dev, but better than nothing)
    file_based = str(Path(__file__).resolve().parents[2])
    return file_based


# swarmai/ root — used as cwd for script jobs that need `python -m backend.jobs.*`
_SWARMAI_ROOT = _get_swarmai_root()

# All times in UTC
SYSTEM_JOBS: list[Job] = [
    # --- Signal Pipeline ---
    Job(
        id="signal-fetch",
        name="Fetch Signals",
        type="signal_fetch",
        schedule="0 6,9 * * 1-5",    # weekday 2x: ICT 14:00, 17:00 (UTC 6,9)
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
        type="self_tune",
        schedule="0 5 * * 1-5",        # weekday ICT 13:00 (UTC 5)
        enabled=True,
        category="system",
        config={},
    ),

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
        safety=JobSafety(max_budget_usd=0, timeout_seconds=3600),
        config={},
    ),

    # --- Library mount freshness (keep the overlay's 🟢/🟡/🔴 dots accurate) ---
    # Light read-only sweep: re-probe each registered mount's source (exists +
    # edited-after-index) and persist health. No LLM, sub-second for a handful of
    # mounts. Weekday morning (weekday-only policy — no weekend clock jobs).
    Job(
        id="library-freshness",
        name="Library Mount Freshness — re-probe mount health (🟢/🟡/🔴)",
        type="library_freshness",
        schedule="0 7 * * 1-5",         # weekday UTC 07:00 = ICT 15:00
        enabled=True,
        category="system",
        safety=JobSafety(max_budget_usd=0, timeout_seconds=120),
        config={},
    ),

    # --- Library Health (keep the Native store from rotting into a graveyard) ---
    # Weekly heuristic scan of Knowledge/ for cleanup candidates (old raw-logs,
    # empty files, oversized categories) → writes .library-health.json for the
    # overlay's health section + one-click actions. No LLM, zero token, read-only
    # (the job never mutates knowledge; actions run on explicit user click).
    Job(
        id="library-health",
        name="Library Health — scan Knowledge/ for cleanup candidates",
        type="library_health",
        schedule="0 7 * * 1",           # Monday UTC 07:00 = ICT 15:00 (weekly, weekday)
        enabled=True,
        category="system",
        safety=JobSafety(max_budget_usd=0, timeout_seconds=120),
        config={},
    ),

    # --- Session Quality (layer②③: score real sessions → harvest golden drafts) ---
    # Weekly low-frequency batch (N=10/week): samples real desktop sessions
    # (with-correction OR turn-anomalous), scores each on goal+tool axes via the
    # eval judge, records low scores to correction_tracker (drift radar), and
    # harvests a golden DRAFT from each low-score session (human ratifies at
    # promote — NEVER auto-promoted). Friday, end-of-week harvest (was Sunday —
    # moved off the weekend per the no-weekend-clock-job policy, test_job_schedules
    # test_no_clock_job_runs_on_weekend; weekend runs go unmonitored).
    Job(
        id="session-quality",
        name="Session Quality — score real sessions + harvest golden drafts (layer②③)",
        type="session_quality",
        schedule="0 8 * * 5",          # Friday UTC 08:00 = ICT 16:00 (end-of-week, weekday-only policy)
        enabled=True,
        category="system",
        safety=JobSafety(max_budget_usd=0, timeout_seconds=1800),
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

    # --- DDD Weekly Report (summarizes cultivation activity across all projects) ---
    # Runs after ddd-refresh (which proposes updates). This report surfaces
    # what was auto-applied, what needs escalation, and DDD health per project.
    # Output: Knowledge/Reports/YYYY-MM-DD-ddd-weekly.md
    Job(
        id="ddd-weekly-report",
        name="DDD Weekly Report",
        type="ddd_weekly_report",
        schedule="0 7 * * 1",           # Monday UTC 07:00 = ICT 15:00 (after ddd-refresh)
        enabled=True,
        category="system",
        config={"window_days": 7},
    ),

    # --- DDD Self-Audit (per-project LLM semantic-drift REVIEW across ALL projects) ---
    # The real mechanism for SEMANTIC drift (run_b2e85d61 proved it is NOT a mechanizable
    # grep-detector — prose-truth needs JUDGMENT). Loops every DDD project, runs a bounded
    # READ-ONLY (Read/Grep) review subprocess per project (domain-aware: code-backed =
    # prose-vs-code, non-code = internal-contradiction), and surfaces drift as Radar todos
    # + a report. DETECT-ONLY: the agent has no Write/Edit — the fix is human via s_persist.
    # In-process loop (~8 projects) blocks the serial scheduler ~20-30min → scheduled as a
    # LATE Monday slot (after the light AM jobs, before eval-scheduled's 10:30 heavy slot).
    Job(
        id="ddd-self-audit",
        name="DDD Self-Audit",
        type="ddd_self_audit",
        schedule="0 9 * * 1",           # Monday UTC 09:00 = ICT 17:00 (trailing, pre-eval)
        enabled=True,
        category="system",
        config={
            "create_todos": True,
            "todo_source_type": "ai_detected",
            "todo_priority": "medium",
            "todo_max": 40,
        },
        safety=JobSafety(
            # max_budget_usd=0: NO per-job dollar cap (matches sibling jobs). Cost is
            # governed centrally by the scheduler's global monthly budget, not by a
            # per-job/per-call dollar number. timeout_seconds is the ONLY real control
            # here — it bounds a genuine hang of the outer job (the per-project inner
            # spawns each carry their own _PER_PROJECT_TIMEOUT_S hang guard).
            timeout_seconds=2400,
            max_budget_usd=0,
            allowed_tools=["Read", "Grep", "Glob"],
        ),
    ),

    # --- SwarmAI Monthly Report (comprehensive health + progress MBR) ---
    # Covers all 12 subsystems. Runs 1st of month after all weekly jobs have
    # populated their data for the prior month.
    Job(
        id="swarmai-monthly-report",
        name="SwarmAI Monthly Report",
        type="swarmai_monthly_report",
        schedule="0 5 1 * *",           # 1st of month, UTC 05:00 = ICT 13:00 (day-of-month pinned)
        enabled=True,
        category="system",
        config={},  # Uses previous month by default
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
        schedule="45 5 * * 1",          # Monday UTC 05:45 = ICT 13:45
        enabled=True,
        category="system",
        config={},
    ),

    # --- Signal Digest → Slack Notification ---
    # Fires after each digest, reads signal_digest.json, sends top items as Slack DM.
    # Requires ~/.swarm-ai/notify-channels.yaml with slack.enabled=true.
    # Handler pre-flight skips gracefully (status="skipped") when config missing
    # — won't trigger circuit breaker.
    Job(
        id="signal-notify-slack",
        name="Signal Digest → Slack",
        type="notify",
        schedule="after:signal-digest",
        enabled=True,
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
        schedule="0 5 * * 1",          # Monday UTC 05:00 = ICT 13:00 — fresh weekly rollup
        enabled=True,
        category="system",
        config={"window_days": 7},
    ),

    # (Todo Resolution job removed run_50db230a — it auto-resolved/cancelled todos,
    # incl. user manual ones (git-keyword + staleness layers had no source filter).
    # The ToDo card is now a pure user-planning surface: the user owns their todos'
    # lifecycle, the system neither writes nor auto-cancels them.)

    # --- Evolution Cycle (SOLE trigger) ---
    # This scheduled job is the ONLY trigger for the mine→score→optimize cycle.
    # The old per-session hook trigger (evolution_maintenance_hook._maybe_run_evolution)
    # was REMOVED (run_6ac3fc0b): a ~5-min job (mine 3629 transcripts + Bedrock)
    # ran synchronously on the 180s-budget session-close hook, timed out before it
    # could advance .evolution_last_run, and re-triggered every session (59x/day)
    # while spawning uncancellable zombie threads. Resilience for a laptop that is
    # off at the scheduled time is provided by cron_utils.is_cron_due, which catches
    # up a missed weekly slot on the next scheduler tick after wake (7-day window).
    # run_evolution.py writes .evolution_last_run on success; the SCHEDULER's own
    # job_state.last_run (advanced on every run, success or fail) owns re-fire cadence.
    #
    # timeout_seconds=1800 (30 min) is DELIBERATE, not a copy of the 300s default:
    # the measured cycle is ~293s and the transcript corpus only grows. The default
    # 300s left a 7s margin — the first slow run would time out, never write state,
    # and after 3 consecutive Thursday timeouts the circuit breaker would disable
    # evolution entirely (Gate-1 FLAW-1, run_6ac3fc0b). A weekly deterministic
    # script has no reason to be capped near its own runtime.
    Job(
        id="evolution-cycle",
        name="Evolution Cycle",
        type="script",
        schedule="0 7 * * 4",          # Thursday UTC 07:00 = ICT 15:00
        enabled=True,
        category="system",
        # max_budget_usd=0: script jobs never consult max_budget_usd (only
        # agent_task does), matching sibling script jobs — the Bedrock spend of
        # the cycle is not capped by the job system on this path. timeout_seconds
        # is the real control (see comment above).
        safety=JobSafety(max_budget_usd=0, timeout_seconds=1800),
        config={
            "command": "python -m backend.jobs.run_evolution",
            "cwd": _SWARMAI_ROOT,
        },
    ),

    # --- Loops Health (7-dimension self-maintenance scan) ---
    # Scans context files, DailyActivity, Knowledge/, Projects/, Evolution state,
    # git backup, and infrastructure health (31 checks). Auto-fixes safe mechanical
    # issues. Reports Found/Fixed/Pending with a health score (0-100).
    # Script uses Path.home()/.swarm-ai — works in daemon context without shell env.
    # NOTE: directory has hyphen (s_loops-health) so python -m doesn't work;
    # use direct script path relative to _SWARMAI_ROOT (cwd).
    Job(
        id="loops-health",
        name="Self-Loops Health Monitor",
        type="script",
        schedule="0 10 * * 1",         # Monday UTC 10:00 = ICT 18:00 (staggered off weekly-maintenance)
        enabled=True,
        category="system",
        config={
            "command": (
                "python backend/skills/s_loops-health/scripts/loops_health_check.py"
                " --auto-fix"
                " --output-dir ${HOME}/.swarm-ai/SwarmWS/Knowledge/JobResults"
                " --alert-threshold 70"
            ),
            "cwd": _SWARMAI_ROOT,
        },
        safety=JobSafety(max_budget_usd=0, timeout_seconds=300),
    ),

    # --- Pipeline Retention (scheduled garbage-run purge) ---
    # THE scheduled retention job the purge_garbage_runs SSOT was always meant to have
    # (its docstring referenced "the scheduled retention job" — this Job makes that true).
    # Recoverably trashes UNTRACKED garbage run dirs (abandoned / crash-residue, never
    # delivered) older than 30d across all projects, so they stop polluting on-disk
    # clutter (the analytics endpoints already EXCLUDE garbage from stats at read time).
    # --apply (act, not dry-run); NO --include-tracked: a tracked delete is an unattended
    # git-rm on the PUBLIC repo (purge_garbage_runs' own Gate-2 guard) — off-limits for a
    # scheduled job. type="script" reuses the sanctioned CLI entrypoint (no new JobType —
    # cmd_purge_garbage is already documented as the scheduled retention entrypoint).
    # ⚠️ SCOPE (adversarial-review MED, run_a65f2d6c): because tracked runs are skipped,
    # this reaps ONLY UNTRACKED-project garbage (AIDLC/CMHK/IVTHub/… local-only runs). The
    # SwarmAI project's own runs are git-tracked (public repo) → intentionally NEVER purged
    # here; that store is user-owned and only a manual `--include-tracked` (reviewed) touches
    # it. So this is the retention control for local-only project garbage, NOT for SwarmAI's
    # run store — it can legitimately purge 0 until untracked-project pipelines crash + age.
    Job(
        id="pipeline-retention",
        name="Pipeline Retention (garbage-run purge)",
        type="script",
        schedule="30 8 * * 1-5",        # weekdays UTC 08:30 = ICT 16:30 (light AM slot; no-weekend policy — retention needn't run 7d)
        enabled=True,
        category="system",
        config={
            "command": "python backend/scripts/purge_garbage_runs.py --apply",
            "cwd": _SWARMAI_ROOT,
        },
        safety=JobSafety(max_budget_usd=0, timeout_seconds=300),
    ),

    # --- Code Intelligence Reindex (event-driven) ---
    # Triggered by git_commit events emitted from auto_commit_hook.
    # Runs incremental reindex on projects with code_intel.db.
    # Also triggered by code_intel_full_reindex for >50 stale files.
    Job(
        id="code-intel-reindex",
        name="Code Intelligence Reindex",
        type="script",
        schedule="on:git_commit",
        enabled=True,
        category="system",
        config={
            "command": "python -m backend.jobs.handlers.code_intel_reindex",
            "cwd": _SWARMAI_ROOT,
        },
        safety=JobSafety(max_budget_usd=0, timeout_seconds=120),
    ),

    # --- Code Intelligence Full Reindex (event-driven) ---
    # Triggered when context_health_hook detects >50 stale files.
    # Does a full rebuild rather than incremental.
    Job(
        id="code-intel-full-reindex",
        name="Code Intelligence Full Reindex",
        type="script",
        schedule="on:code_intel_full_reindex",
        enabled=True,
        category="system",
        config={
            "command": "python -m backend.jobs.handlers.code_intel_reindex --full",
            "cwd": _SWARMAI_ROOT,
        },
        # 300s (was 120): the AGGREGATE full-reindex (fans out over ALL projects
        # in SwarmWS/Projects/ — parse+clear+insert+export each) measured 81-114.5s
        # for the current project set, hugging the old 120s wall and occasionally
        # exceeding it → false "Script timed out" failures (run_ca79d3ef). 300 is a
        # floor, not a cure: because runtime scales with (project count × repo size),
        # this headroom ERODES as projects grow — if a reindex approaches 300s,
        # re-benchmark and raise, don't assume it's a hang.
        safety=JobSafety(max_budget_usd=0, timeout_seconds=300),
    ),
]

SYSTEM_JOB_IDS: set[str] = {j.id for j in SYSTEM_JOBS}


def get_all_system_jobs() -> list[Job]:
    """Return a copy of all system job definitions."""
    return list(SYSTEM_JOBS)
