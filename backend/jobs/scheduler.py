"""
Swarm Job Scheduler — Core Engine

Product-level scheduler that evaluates and executes due jobs.
Can be triggered by launchd (hourly), backend startup, or API call.

Usage (standalone CLI — backwards compatible):
    python -m backend.jobs.scheduler               # Normal run
    python -m backend.jobs.scheduler --dry-run     # Show what would run
    python -m backend.jobs.scheduler --run-now JOB # Force-run
    python -m backend.jobs.scheduler --status      # Show state
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import yaml

from .models import Feed, FeedType, Job, JobSafety, SchedulerDefaults, SchedulerState
from .executor import execute_job, _FAILURE_ALERT_THRESHOLD
from .cron_utils import is_cron_due
from .paths import (
    STATE_FILE, CONFIG_FILE, USER_JOBS_FILE, SWARMWS,
)
from .system_jobs import get_all_system_jobs, SYSTEM_JOB_IDS

# Logging — only configure if running standalone (not imported by backend)
logger = logging.getLogger("swarm.jobs.scheduler")


def load_config() -> dict:
    """Load config.yaml."""
    if not CONFIG_FILE.exists():
        logger.error(f"Config not found: {CONFIG_FILE}")
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f) or {}


def load_feeds(config: dict) -> list[Feed]:
    """Parse feed definitions from config."""
    feeds = []
    for fd in config.get("feeds", []):
        try:
            feeds.append(Feed(
                id=fd["id"],
                name=fd["name"],
                type=FeedType(fd["type"]),
                config=fd.get("config", {}),
                tags=fd.get("tags", []),
                enabled=fd.get("enabled", True),
                managed_by=fd.get("managed_by", "manual"),
            ))
        except Exception as e:
            logger.warning(f"Skipping invalid feed '{fd.get('id', '?')}': {e}")
    return feeds


def load_jobs() -> list[Job]:
    """Load system jobs (from code) + user jobs (from user-jobs.yaml).

    System jobs are defined in system_jobs.py (product-level code).
    User jobs live in SwarmWS/Services/swarm-jobs/user-jobs.yaml.
    Duplicate IDs across sources are rejected with a warning.
    """
    all_jobs: list[Job] = list(get_all_system_jobs())
    seen_ids: set[str] = set(SYSTEM_JOB_IDS)

    # Load user jobs from YAML
    if USER_JOBS_FILE.exists():
        try:
            with open(USER_JOBS_FILE) as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning(f"Failed to load user jobs: {e}")
            return all_jobs

        for jd in data.get("jobs", []):
            try:
                job_id = jd["id"]
                if job_id in seen_ids:
                    logger.warning(f"Duplicate job ID '{job_id}' in user-jobs.yaml — skipped")
                    continue

                safety = JobSafety(**jd["safety"]) if "safety" in jd else JobSafety()

                all_jobs.append(Job(
                    id=job_id,
                    name=jd["name"],
                    type=jd["type"],
                    schedule=jd["schedule"],
                    enabled=jd.get("enabled", True),
                    category=jd.get("category", "user"),
                    config=jd.get("config", {}),
                    safety=safety,
                ))
                seen_ids.add(job_id)
            except Exception as e:
                logger.warning(f"Skipping invalid user job '{jd.get('id', '?')}': {e}")

    return all_jobs


def load_state() -> SchedulerState:
    """Load persistent state from JSON, or create fresh."""
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            return SchedulerState.model_validate(data)
        except Exception as e:
            logger.warning(f"Corrupt state file, starting fresh: {e}")
    return SchedulerState()


def save_state(state: SchedulerState) -> None:
    """Persist state to JSON.

    NOTE: This is a raw write with no cross-process locking. Callers that
    may race with hooks emitting events (the scheduler's main loop) MUST use
    ``save_state_reconciled`` instead, which preserves hook-appended events.
    Direct ``save_state`` is safe only for callers that don't touch
    ``pending_events`` concurrently with hooks (CLI --run-now, tests).
    """
    STATE_FILE.write_text(state.model_dump_json(indent=2))


@contextmanager
def _state_lock():
    """Acquire the cross-process exclusive lock guarding state.json.

    Both ``emit_event_atomic`` (hooks) and ``save_state_reconciled``
    (scheduler) take this lock so their read-modify-write cycles on
    ``pending_events`` are serialized across processes. The lock file is
    ``state.lock`` alongside ``state.json``.

    Blocking acquire — hold time is tiny (a load+write of small JSON), and
    the only contenders are the scheduler's final save and event-emitting
    hooks, so contention is rare and brief.
    """
    import fcntl

    lock_file = STATE_FILE.with_suffix(".lock")
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lf = open(lock_file, "w")  # noqa: SIM115
    try:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lf.close()


def save_state_reconciled(
    state: SchedulerState, consumed_event_ids: set[str] | None = None
) -> None:
    """Persist scheduler state while preserving concurrently-appended events.

    Closes the lost-update race between the scheduler process (which holds
    state in memory across a multi-minute job run) and event-emitting hooks
    (``emit_event_atomic``) that append to ``pending_events`` on disk during
    that run.

    Under the shared state lock:
    1. Re-read the on-disk ``pending_events`` (authoritative — includes any
       events hooks appended during the run).
    2. Drop only the events this scheduler run actually consumed
       (``consumed_event_ids``).
    3. Write the scheduler's in-memory ``state`` (job statuses, spend, etc.)
       but with the reconciled ``pending_events``.

    This keeps scheduler-owned fields authoritative while never discarding a
    hook-emitted event that arrived mid-run.
    """
    consumed = consumed_event_ids or set()
    try:
        with _state_lock():
            disk = load_state()
            reconciled = [
                e for e in disk.pending_events
                if e.get("event_id") not in consumed
            ]
            # Cap to bound growth (newest kept), matching emit_event_atomic.
            if len(reconciled) > _MAX_PENDING_EVENTS:
                reconciled = reconciled[-_MAX_PENDING_EVENTS:]
            state.pending_events = reconciled
            save_state(state)
    except Exception as e:
        # Never let a state-save failure crash the scheduler — fall back to a
        # raw write so job-status updates aren't lost (events may be, rarely).
        logger.warning(f"save_state_reconciled failed, falling back to raw save: {e}")
        save_state(state)


def emit_event_atomic(event_name: str, data: dict | None = None) -> str:
    """Atomically emit an event into state without clobbering other fields.

    Unlike emit_event() which operates on an in-memory state object,
    this function does a targeted load→append→save cycle under the shared
    state lock. Use this from hooks and external callers that don't own the
    full scheduler state.

    This prevents the race condition where a hook loads stale state (with
    old job statuses) and saves it back, overwriting the scheduler's
    successful job updates — and, paired with ``save_state_reconciled``,
    ensures the scheduler's final save never drops a hook-emitted event.
    """
    import uuid

    event_id = str(uuid.uuid4())
    event_entry = {
        "event_id": event_id,
        "event_name": event_name,
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "data": data or {},
    }

    try:
        with _state_lock():
            # Load current state, append event, save — all under lock
            state = load_state()
            if len(state.pending_events) >= _MAX_PENDING_EVENTS:
                state.pending_events = state.pending_events[-(_MAX_PENDING_EVENTS // 2):]
            state.pending_events.append(event_entry)
            save_state(state)
    except Exception as e:
        logger.warning(f"emit_event_atomic failed: {e}")
        return ""

    logger.info(f"Event emitted (atomic): {event_name} (id={event_id[:8]})")
    return event_id


def load_user_context() -> str:
    """Build user context for signal relevance scoring.

    Combines three sources (priority order):
    1. config.yaml ``user_context`` — curated interests/projects/tech_stack
       from self_tune (highest signal, structured)
    2. USER.md — user profile (role, work context, preferences)
    3. MEMORY.md key decisions — recent focus areas (capped)

    The result is what the LLM uses to decide "is this signal relevant to
    this user?" — it must reflect actual interests, not internal metadata.
    """
    parts: list[str] = []

    # 1. Structured interests from config.yaml (self_tune maintained)
    try:
        config_path = SWARMWS / "Services" / "swarm-jobs" / "config.yaml"
        if config_path.exists():
            import yaml
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
            uc = config.get("user_context", {})
            interests = uc.get("interests", [])
            projects = uc.get("projects", [])
            tech_stack = uc.get("tech_stack", [])
            if interests or projects or tech_stack:
                lines = ["## Interests & Focus"]
                if interests:
                    lines.append(f"Topics: {', '.join(interests)}")
                if projects:
                    lines.append(f"Active projects: {', '.join(projects)}")
                if tech_stack:
                    lines.append(f"Tech stack: {', '.join(tech_stack)}")
                parts.append("\n".join(lines))
    except Exception:
        pass

    # 2. USER.md — role, work context (first 800 chars = Bio + Work Context)
    user_path = SWARMWS / ".context" / "USER.md"
    if user_path.exists():
        try:
            content = user_path.read_text()[:800]
            parts.append(f"## User Profile\n{content}")
        except Exception:
            pass

    # 3. MEMORY.md — only the Decisions section (recent focus, signal-dense).
    # Section is "Decisions" post-PRI01; the old "## Key Decisions" literal
    # matched nothing so this context was silently empty (R3 drift fix).
    mem_path = SWARMWS / ".context" / "MEMORY.md"
    if mem_path.exists():
        try:
            content = mem_path.read_text()
            kd_start = content.find("## Decisions")
            if kd_start >= 0:
                kd_end = content.find("\n## ", kd_start + 10)
                kd_section = content[kd_start:kd_end if kd_end > 0 else kd_start + 1500]
                parts.append(kd_section[:1000])
        except Exception:
            pass

    return "\n\n".join(parts) if parts else ""


_MAX_PENDING_EVENTS = 50  # Cap to prevent unbounded queue growth


def emit_event(state: SchedulerState, event_name: str, data: dict | None = None) -> str:
    """Emit an event into the scheduler's pending event queue.

    Events are consumed by jobs with schedule "on:<event_name>".
    Returns the event_id for tracking.

    NOTE: Caller must handle load_state/save_state atomicity.
    The append itself is safe (Python GIL), but the full
    load→emit→save cycle is NOT atomic across processes/threads.
    Hooks serialize via BackgroundHookExecutor, so in practice
    concurrent corruption is unlikely but not impossible.
    """
    import uuid
    event_id = str(uuid.uuid4())

    # Cap queue size: evict oldest events when at capacity
    if len(state.pending_events) >= _MAX_PENDING_EVENTS:
        state.pending_events = state.pending_events[-(_MAX_PENDING_EVENTS // 2):]
        logger.warning("Event queue at capacity — evicted oldest events")

    state.pending_events.append({
        "event_id": event_id,
        "event_name": event_name,
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "data": data or {},
    })
    logger.info(f"Event emitted: {event_name} (id={event_id[:8]})")
    return event_id


def consume_events_for_job(state: SchedulerState, schedule: str) -> None:
    """Remove pending events that match a job's on:<event> schedule.

    Called after the job executes successfully. Removes all pending
    events for that event type so the job doesn't re-fire next tick.
    """
    if not schedule.startswith("on:"):
        return
    event_name = schedule[3:]
    state.pending_events = [
        e for e in state.pending_events
        if e.get("event_name") != event_name
    ]


def is_job_due(job: Job, state: SchedulerState) -> bool:
    """Check if a job should run now based on its schedule.

    Supports three schedule types:
    - Cron expressions (5-field): time-based evaluation
    - "after:<job-id>": dependency-based, runs after parent completes
    - "on:<event_name>": event-driven, runs when matching event is pending

    For dependency-based scheduling (after:X), runs once per dependency
    execution — regardless of whether the dependency succeeded or failed.
    The dependent job is responsible for handling missing/partial data.
    The time-based gate (my_last_run >= dep_last_run) prevents re-running.

    Skipped dependencies (circuit breaker, disabled) don't update last_run,
    so the dependent job correctly stays dormant until the dep actually executes.
    """
    if not job.enabled:
        return False

    # Fast-retry auth_failed jobs on the next scheduler tick — but ONLY while the
    # auth failure still looks TRANSIENT (streak below the alert threshold). When
    # auth expires (SSO, token revocation), the agent itself ran fine; the next
    # hourly tick auto-retries once the user restores auth.
    #
    # The streak gate (not a time window) is what BOUNDS this: `last_run` is
    # refreshed on every run, so a `now - last_run < 24h` window would never expire
    # for a permanently-dead auth job — it would hot-loop hourly forever. Instead we
    # stop the fast-retry once consecutive_auth_failures crosses the threshold: at
    # that point _collect_jobs surfaces it as a BLOCKING "auth broken" card, so the
    # user is notified and the job falls back to its normal cron cadence (no more
    # hourly precheck churn) until auth is actually fixed — a real success clears
    # the streak and fast-retry resumes.
    job_state = state.jobs.get(job.id)
    if (
        job_state
        and job_state.last_status == "auth_failed"
        and (job_state.consecutive_auth_failures or 0) < _FAILURE_ALERT_THRESHOLD
    ):
        return True

    # Handle event-driven scheduling (on:<event_name>)
    if job.schedule.startswith("on:"):
        event_name = job.schedule[3:]
        return any(
            e.get("event_name") == event_name
            for e in state.pending_events
        )

    # Handle dependency-based scheduling (after:job-id)
    if job.schedule.startswith("after:"):
        dep_id = job.schedule[6:]
        dep_state = state.jobs.get(dep_id)
        if not dep_state or not dep_state.last_run:
            return False
        my_state = state.jobs.get(job.id)
        if my_state and my_state.last_run and my_state.last_run >= dep_state.last_run:
            return False  # Already ran after last dependency execution
        # Run after any execution (success, failed, partial).
        # "skipped" jobs don't update last_run, so they won't trigger this.
        return dep_state.last_status != "skipped"

    # Cron-based scheduling
    job_state = state.jobs.get(job.id)
    if not job_state or not job_state.last_run:
        return True  # Never run before

    try:
        return is_cron_due(job.schedule, job_state.last_run)
    except Exception as e:
        logger.error(f"Invalid cron for job '{job.id}': {e}")
        return False


def check_circuit_breaker(job: Job, state: SchedulerState) -> bool:
    """Skip jobs that have failed too many times consecutively.

    Auto-resets after 24h cooldown — gives transient issues (network,
    auth, DNS) a chance to resolve without manual state.json editing.
    """
    job_state = state.jobs.get(job.id)
    if job_state and job_state.consecutive_failures >= 3:
        # Auto-reset after 24h cooldown
        if job_state.last_run:
            # last_run may be naive (from datetime.now()) or aware (from
            # tests / future code).  Normalize both sides to UTC-aware.
            last = job_state.last_run
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            cooldown = datetime.now(timezone.utc) - last
            if cooldown > timedelta(hours=24):
                logger.info(
                    f"Circuit breaker reset for '{job.id}' "
                    f"(24h cooldown elapsed, was {job_state.consecutive_failures} failures)"
                )
                job_state.consecutive_failures = 0
                # last_error must stay in lockstep with consecutive_failures
                # everywhere (not just _update_job_state) — else a cooldown-reset
                # job shows 0 failures but a stale error in 🔔 diagnostics.
                job_state.last_error = None
                return True
        logger.warning(
            f"Circuit breaker: skipping '{job.id}' "
            f"({job_state.consecutive_failures} consecutive failures)"
        )
        return False
    return True


def load_defaults(config: dict) -> SchedulerDefaults:
    """Parse scheduler defaults from config.yaml."""
    raw = config.get("defaults", {})
    try:
        return SchedulerDefaults(**raw)
    except Exception as e:
        logger.warning(f"Invalid defaults in config, using built-in: {e}")
        return SchedulerDefaults()


def run_scheduler(dry_run: bool = False, force_job: str | None = None) -> None:
    """Main scheduler loop — evaluate and execute due jobs."""
    config = load_config()
    feeds = load_feeds(config)
    defaults = load_defaults(config)
    jobs = load_jobs()
    state = load_state()
    user_context = load_user_context()

    logger.info(f"Scheduler starting: {len(feeds)} feeds, {len(jobs)} jobs")

    all_job_ids = {j.id for j in jobs}

    if force_job:
        # Force-run a specific job
        job = next((j for j in jobs if j.id == force_job), None)
        if not job:
            logger.error(f"Job not found: {force_job}")
            sys.exit(1)
        if not job.enabled:
            logger.error(f"Job '{force_job}' is disabled")
            sys.exit(1)
        logger.info(f"Force-running job: {job.id}")
        if not dry_run:
            result = execute_job(job, state, feeds, user_context, defaults, all_job_ids)
            logger.info(f"Result: {result.status} — {result.summary}")
            # Reconciled save preserves any events hooks emitted during the
            # forced run (this path doesn't consume events).
            save_state_reconciled(state)
            # Print JSON result for --run-now callers
            print(json.dumps(result.model_dump(), default=str))
        else:
            logger.info(f"[DRY RUN] Would execute: {job.id} ({job.type})")
        return

    # Separate jobs into three categories by schedule type.
    # Execution order: time-based → dependency-based → event-triggered.
    # This ensures: (1) cron jobs update state for after:X deps,
    # (2) after:X jobs fire in same cycle as parent,
    # (3) event-triggered jobs consume pending events last.
    time_based_jobs: list[Job] = []
    dep_based_jobs: list[Job] = []
    event_based_jobs: list[Job] = []
    for job in jobs:
        if job.schedule.startswith("after:"):
            dep_based_jobs.append(job)
        elif job.schedule.startswith("on:"):
            event_based_jobs.append(job)
        else:
            time_based_jobs.append(job)

    # Phase 1: Evaluate and execute time-based jobs
    due_jobs: list[Job] = []
    for job in time_based_jobs:
        if is_job_due(job, state) and check_circuit_breaker(job, state):
            due_jobs.append(job)

    if due_jobs:
        logger.info(f"{len(due_jobs)} time-based jobs due: {[j.id for j in due_jobs]}")

    if dry_run:
        for job in due_jobs:
            logger.info(f"[DRY RUN] Would execute: {job.id} ({job.type})")
        # Still check deps for dry-run visibility
        for job in dep_based_jobs:
            if is_job_due(job, state) and check_circuit_breaker(job, state):
                logger.info(f"[DRY RUN] Would execute (dep): {job.id} ({job.type})")
        # Check event-triggered jobs for dry-run visibility
        for job in event_based_jobs:
            if is_job_due(job, state) and check_circuit_breaker(job, state):
                logger.info(f"[DRY RUN] Would execute (event): {job.id} ({job.type})")
        return

    results = []
    for job in due_jobs:
        result = execute_job(job, state, feeds, user_context, defaults, all_job_ids)
        results.append(result)
        logger.info(f"  {job.id}: {result.status} — {result.summary}")

    # Phase 2: Re-evaluate dependency-based jobs against updated state.
    # This ensures after:signal-fetch fires in the SAME cycle that
    # signal-fetch ran — not deferred to the next hourly tick.
    due_deps: list[Job] = []
    for job in dep_based_jobs:
        if is_job_due(job, state) and check_circuit_breaker(job, state):
            due_deps.append(job)

    if due_deps:
        logger.info(f"{len(due_deps)} dependency jobs now due: {[j.id for j in due_deps]}")
        for job in due_deps:
            result = execute_job(job, state, feeds, user_context, defaults, all_job_ids)
            results.append(result)
            logger.info(f"  {job.id}: {result.status} — {result.summary}")

    # Phase 3: Evaluate and execute event-triggered (on:<event>) jobs.
    # These consume pending events from state.pending_events.
    # Track the exact event IDs consumed so the final reconciled save drops
    # only these — preserving any events hooks appended during this run.
    consumed_event_ids: set[str] = set()
    if event_based_jobs and state.pending_events:
        due_events: list[Job] = []
        for job in event_based_jobs:
            if is_job_due(job, state) and check_circuit_breaker(job, state):
                due_events.append(job)

        if due_events:
            logger.info(f"{len(due_events)} event jobs triggered: {[j.id for j in due_events]}")
            for job in due_events:
                result = execute_job(job, state, feeds, user_context, defaults, all_job_ids)
                results.append(result)
                logger.info(f"  {job.id}: {result.status} — {result.summary}")
                # Only consume events on success — failed jobs should retry
                # on next tick (circuit breaker handles repeated failures)
                if result.status in ("success", "partial", "skipped"):
                    # Record IDs being consumed BEFORE removing them, so the
                    # reconciled save drops exactly these from disk.
                    event_name = job.schedule[3:] if job.schedule.startswith("on:") else None
                    if event_name:
                        consumed_event_ids.update(
                            e.get("event_id")
                            for e in state.pending_events
                            if e.get("event_name") == event_name and e.get("event_id")
                        )
                    consume_events_for_job(state, job.schedule)

    if not results:
        logger.info("No jobs due")
        return

    # Reconciled save: re-read disk under lock, drop only the events we
    # consumed, preserve hook-appended events that arrived during this run.
    save_state_reconciled(state, consumed_event_ids)

    # Summary
    ok = sum(1 for r in results if r.status in ("success", "skipped"))
    err = sum(1 for r in results if r.status == "failed")
    auth = sum(1 for r in results if r.status == "auth_failed")
    summary = f"Scheduler complete: {ok} ok, {err} errors"
    if auth:
        summary += f", {auth} auth_failed (will retry)"
    logger.info(summary)


def show_status() -> None:
    """Print current scheduler state."""
    state = load_state()
    jobs = load_jobs()

    print(f"\n{'='*60}")
    print(f"Swarm Job Scheduler — Status")
    print(f"{'='*60}")
    print(f"Monthly spend: ${state.monthly_spend_usd:.2f}")
    print(f"Monthly tokens used: {state.monthly_tokens_used} (legacy)")
    print(f"Buffered signals: {len(state.raw_signals)}")
    print(f"Dedup cache size: {len(state.dedup_cache)}")
    print()

    for job in jobs:
        js = state.jobs.get(job.id)
        status_icon = "✅" if (js and js.last_status == "success") else "⏳" if not js else "❌"
        last_run = js.last_run.strftime("%Y-%m-%d %H:%M") if (js and js.last_run) else "never"
        failures = js.consecutive_failures if js else 0
        total = js.total_runs if js else 0

        enabled = "🟢" if job.enabled else "🔴"
        print(f"  {enabled} {status_icon} {job.id:<25} last: {last_run}  runs: {total}  failures: {failures}")
        print(f"     schedule: {job.schedule}  type: {job.type}")

    print()


def list_jobs() -> None:
    """List all jobs with details (JSON output)."""
    jobs = load_jobs()
    state = load_state()

    result = []
    for job in jobs:
        js = state.jobs.get(job.id)
        result.append({
            "id": job.id,
            "name": job.name,
            "type": str(job.type),
            "schedule": job.schedule,
            "enabled": job.enabled,
            "category": job.category,
            "last_run": js.last_run.isoformat() if (js and js.last_run) else None,
            "last_status": js.last_status if js else "never",
            "total_runs": js.total_runs if js else 0,
            "consecutive_failures": js.consecutive_failures if js else 0,
        })

    print(json.dumps(result, indent=2, default=str))


def toggle_job(job_id: str, enabled: bool) -> None:
    """Enable or disable a job in jobs.yaml or user-jobs.yaml."""
    action = "enable" if enabled else "disable"

    for path in (JOBS_FILE, USER_JOBS_FILE):
        if not path.exists():
            continue
        with open(path) as f:
            data = yaml.safe_load(f) or {}

        for jd in data.get("jobs", []):
            if jd.get("id") == job_id:
                jd["enabled"] = enabled
                with open(path, "w") as f:
                    yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
                print(f"Job '{job_id}' {action}d in {path.name}")
                return

    print(f"Job '{job_id}' not found", file=sys.stderr)
    sys.exit(1)


def validate_config() -> None:
    """Validate config.yaml and jobs.yaml for errors."""
    errors = []

    # Validate config
    try:
        config = load_config()
        feeds = load_feeds(config)
        print(f"config.yaml: {len(feeds)} feeds loaded OK")
    except Exception as e:
        errors.append(f"config.yaml: {e}")

    # Validate jobs
    try:
        jobs = load_jobs()
        print(f"jobs.yaml + user-jobs.yaml: {len(jobs)} jobs loaded OK")

        # Check for invalid cron expressions
        for job in jobs:
            if job.schedule.startswith("after:"):
                dep_id = job.schedule[6:]
                dep_exists = any(j.id == dep_id for j in jobs)
                if not dep_exists:
                    errors.append(f"Job '{job.id}': dependency '{dep_id}' not found")
            else:
                try:
                    from cron_utils import is_cron_due
                    is_cron_due(job.schedule, datetime(2020, 1, 1, tzinfo=timezone.utc))
                except ValueError as e:
                    errors.append(f"Job '{job.id}': invalid cron: {e}")
    except Exception as e:
        errors.append(f"jobs.yaml: {e}")

    # Validate state
    try:
        state = load_state()
        print(f"state.json: {len(state.jobs)} job states, ${state.monthly_spend_usd:.2f} monthly spend")
    except Exception as e:
        errors.append(f"state.json: {e}")

    if errors:
        print(f"\n{len(errors)} errors found:")
        for err in errors:
            print(f"  ❌ {err}")
        sys.exit(1)
    else:
        print("\n✅ All configuration valid")


    # install_launchd removed — use install_scheduler.py instead


def main():
    parser = argparse.ArgumentParser(description="Swarm Job Scheduler")
    parser.add_argument("--dry-run", action="store_true", help="Show what would run")
    parser.add_argument("--run-now", type=str, metavar="JOB_ID", help="Force-run a specific job")
    parser.add_argument("--status", action="store_true", help="Show scheduler state")
    parser.add_argument("--list-jobs", action="store_true", help="List all jobs (JSON)")
    parser.add_argument("--enable", type=str, metavar="JOB_ID", help="Enable a job")
    parser.add_argument("--disable", type=str, metavar="JOB_ID", help="Disable a job")
    parser.add_argument("--validate", action="store_true", help="Validate config and jobs")
    parser.add_argument("--install", action="store_true", help="Generate and install launchd plist")
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.list_jobs:
        list_jobs()
    elif args.enable:
        toggle_job(args.enable, True)
    elif args.disable:
        toggle_job(args.disable, False)
    elif args.validate:
        validate_config()
    elif args.install:
        from .install_scheduler import install
        install()
    else:
        run_scheduler(dry_run=args.dry_run, force_job=args.run_now)


if __name__ == "__main__":
    main()
