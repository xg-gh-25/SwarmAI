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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .models import Feed, FeedType, Job, JobSafety, SchedulerDefaults, SchedulerState
from .executor import execute_job
from .cron_utils import is_cron_due
from .paths import (
    STATE_FILE, CONFIG_FILE, USER_JOBS_FILE, LOG_DIR, SWARMWS,
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
    """Persist state to JSON."""
    STATE_FILE.write_text(state.model_dump_json(indent=2))


def load_user_context() -> str:
    """Read user context from MEMORY.md + PROJECTS.md for relevance scoring."""
    context_parts = []
    for filename in ("MEMORY.md", "PROJECTS.md"):
        path = SWARMWS / ".context" / filename
        if path.exists():
            content = path.read_text()[:2000]  # cap to keep prompt small
            context_parts.append(f"## {filename}\n{content}")
    return "\n\n".join(context_parts) if context_parts else ""


def is_job_due(job: Job, state: SchedulerState) -> bool:
    """Check if a job should run now based on its cron schedule.

    For dependency-based scheduling (after:X), runs once per dependency
    execution — regardless of whether the dependency succeeded or failed.
    The dependent job is responsible for handling missing/partial data.
    The time-based gate (my_last_run >= dep_last_run) prevents re-running.

    Skipped dependencies (circuit breaker, disabled) don't update last_run,
    so the dependent job correctly stays dormant until the dep actually executes.
    """
    if not job.enabled:
        return False

    # Retry auth_failed jobs on next scheduler tick (same calendar day UTC).
    # When MCP auth expires (SSO, token revocation), all tool-dependent jobs
    # fail but the agent itself ran fine.  After the user restores auth, the
    # next hourly scheduler tick automatically retries these jobs.
    job_state = state.jobs.get(job.id)
    if (
        job_state
        and job_state.last_status == "auth_failed"
        and job_state.last_run
        and job_state.last_run.date() == datetime.now(timezone.utc).date()
    ):
        return True

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
            cooldown = datetime.now(timezone.utc) - job_state.last_run
            if cooldown > timedelta(hours=24):
                logger.info(
                    f"Circuit breaker reset for '{job.id}' "
                    f"(24h cooldown elapsed, was {job_state.consecutive_failures} failures)"
                )
                job_state.consecutive_failures = 0
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
            save_state(state)
            # Print JSON result for --run-now callers
            print(json.dumps(result.model_dump(), default=str))
        else:
            logger.info(f"[DRY RUN] Would execute: {job.id} ({job.type})")
        return

    # Evaluate which jobs are due
    due_jobs: list[Job] = []
    for job in jobs:
        if is_job_due(job, state) and check_circuit_breaker(job, state):
            due_jobs.append(job)

    if not due_jobs:
        logger.info("No jobs due")
        return

    logger.info(f"{len(due_jobs)} jobs due: {[j.id for j in due_jobs]}")

    if dry_run:
        for job in due_jobs:
            logger.info(f"[DRY RUN] Would execute: {job.id} ({job.type})")
        return

    # Execute due jobs in order
    results = []
    for job in due_jobs:
        result = execute_job(job, state, feeds, user_context, defaults, all_job_ids)
        results.append(result)
        logger.info(f"  {job.id}: {result.status} — {result.summary}")

    save_state(state)

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
