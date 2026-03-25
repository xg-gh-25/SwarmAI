#!/usr/bin/env python3
"""
Job Manager — CLI tool for CRUD operations on user jobs.

Used by the s_job-manager skill to create, list, edit, pause, resume,
and delete user-scheduled jobs. System jobs (jobs.yaml) are read-only.

Usage:
    python job_manager.py list                          # List all jobs with status
    python job_manager.py create --json '{...}'         # Create a user job
    python job_manager.py edit JOB_ID --json '{...}'    # Edit a user job
    python job_manager.py pause JOB_ID                  # Disable a job
    python job_manager.py resume JOB_ID                 # Enable a job
    python job_manager.py delete JOB_ID                 # Delete a user job
    python job_manager.py show JOB_ID                   # Show full job details
    python job_manager.py validate-cron "0 9 * * 1-5"   # Validate a cron expression
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from .cron_utils import is_cron_due
from .paths import USER_JOBS_FILE, STATE_FILE
from .system_jobs import SYSTEM_JOB_IDS, get_all_system_jobs


# ── Helpers ──────────────────────────────────────────────────────────────

def _load_yaml(path: Path) -> dict:
    """Load a YAML file, return empty dict if missing."""
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _save_yaml(path: Path, data: dict) -> None:
    """Write YAML with readable formatting."""
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def _load_state() -> dict:
    """Load state.json."""
    if not STATE_FILE.exists():
        return {}
    with open(STATE_FILE) as f:
        return json.load(f)


def _get_all_system_job_ids() -> set[str]:
    """Return IDs of system jobs (read-only, defined in code)."""
    return set(SYSTEM_JOB_IDS)


def _get_user_jobs() -> list[dict]:
    """Return user job definitions."""
    data = _load_yaml(USER_JOBS_FILE)
    return data.get("jobs", [])


def _save_user_jobs(jobs: list[dict]) -> None:
    """Write user jobs back to file."""
    _save_yaml(USER_JOBS_FILE, {"jobs": jobs})


def _validate_cron(expr: str) -> tuple[bool, str]:
    """Validate a cron expression. Returns (valid, description)."""
    if expr.startswith("after:"):
        return True, f"Dependency-based: runs after {expr[6:]}"
    try:
        # Use is_cron_due with a synthetic last_run to validate parsing.
        # If the expression is invalid, it raises ValueError.
        synthetic_last_run = datetime(2020, 1, 1, tzinfo=timezone.utc)
        is_cron_due(expr, synthetic_last_run, datetime.now(timezone.utc))
        return True, f"Valid cron: {expr}"
    except ValueError as e:
        return False, f"Invalid cron expression: {e}"


def _generate_id(name: str) -> str:
    """Generate a job ID from a name."""
    slug = name.lower().replace(" ", "-")
    # Keep only alphanumeric + hyphens
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    slug = slug.strip("-")[:40]
    return f"uj-{slug}"


# ── Commands ─────────────────────────────────────────────────────────────

def cmd_list() -> None:
    """List all jobs (system + user) with status."""
    state = _load_state()
    job_states = state.get("jobs", {})

    user_jobs = _get_user_jobs()

    all_jobs = []
    for j in get_all_system_jobs():
        all_jobs.append({
            "id": j.id, "name": j.name, "type": j.type,
            "schedule": j.schedule, "enabled": j.enabled,
            "category": j.category, "_source": "system",
        })
    for j in user_jobs:
        j["_source"] = "user"
        all_jobs.append(j)

    result = []
    for job in all_jobs:
        jid = job["id"]
        js = job_states.get(jid, {})
        result.append({
            "id": jid,
            "name": job.get("name", jid),
            "type": job.get("type", "unknown"),
            "schedule": job.get("schedule", ""),
            "enabled": job.get("enabled", True),
            "category": job.get("category", job["_source"]),
            "source": job["_source"],
            "last_run": js.get("last_run"),
            "last_status": js.get("last_status", "never"),
            "total_runs": js.get("total_runs", 0),
            "consecutive_failures": js.get("consecutive_failures", 0),
        })

    print(json.dumps(result, indent=2, default=str))


def cmd_create(job_json: str) -> None:
    """Create a new user job from JSON definition."""
    try:
        spec = json.loads(job_json)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON: {e}"}))
        sys.exit(1)

    # Required fields
    name = spec.get("name")
    schedule = spec.get("schedule")
    job_type = spec.get("type", "agent_task")

    if not name:
        print(json.dumps({"error": "Missing required field: name"}))
        sys.exit(1)
    if not schedule:
        print(json.dumps({"error": "Missing required field: schedule"}))
        sys.exit(1)

    # Validate schedule
    valid, msg = _validate_cron(schedule)
    if not valid:
        print(json.dumps({"error": msg}))
        sys.exit(1)

    # Generate ID, check for conflicts
    job_id = spec.get("id") or _generate_id(name)
    system_ids = _get_all_system_job_ids()
    user_jobs = _get_user_jobs()
    existing_ids = system_ids | {j["id"] for j in user_jobs}

    if job_id in existing_ids:
        # Append timestamp to make unique
        job_id = f"{job_id}-{int(datetime.now(timezone.utc).timestamp()) % 100000}"

    # Build job definition
    job_def = {
        "id": job_id,
        "name": name,
        "type": job_type,
        "schedule": schedule,
        "enabled": spec.get("enabled", True),
        "category": "user",
        "config": spec.get("config", {}),
    }

    # Add safety config for agent_task jobs
    if job_type == "agent_task":
        safety = spec.get("safety", {})
        job_def["safety"] = {
            "max_budget_usd": safety.get("max_budget_usd", 0.20),
            "timeout_seconds": safety.get("timeout_seconds", 180),
            "allowed_tools": safety.get("allowed_tools", []),
        }

    # Add prompt to config for agent_task
    if job_type == "agent_task" and "prompt" in spec:
        job_def["config"]["prompt"] = spec["prompt"]

    user_jobs.append(job_def)
    _save_user_jobs(user_jobs)

    # Calculate next run
    _, next_info = _validate_cron(schedule)

    print(json.dumps({
        "status": "created",
        "job": job_def,
        "next_run": next_info,
    }, indent=2, default=str))


def cmd_edit(job_id: str, job_json: str) -> None:
    """Edit an existing user job."""
    if job_id in _get_all_system_job_ids():
        print(json.dumps({"error": f"Cannot edit system job '{job_id}'. System jobs are managed in jobs.yaml."}))
        sys.exit(1)

    try:
        updates = json.loads(job_json)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON: {e}"}))
        sys.exit(1)

    user_jobs = _get_user_jobs()
    found = False
    for job in user_jobs:
        if job["id"] == job_id:
            # Validate schedule if being changed
            if "schedule" in updates:
                valid, msg = _validate_cron(updates["schedule"])
                if not valid:
                    print(json.dumps({"error": msg}))
                    sys.exit(1)

            # Merge updates (shallow for top-level, deep for config/safety)
            for key in ("name", "schedule", "type", "enabled"):
                if key in updates:
                    job[key] = updates[key]
            if "config" in updates:
                job.setdefault("config", {}).update(updates["config"])
            if "safety" in updates:
                job.setdefault("safety", {}).update(updates["safety"])
            if "prompt" in updates:
                job.setdefault("config", {})["prompt"] = updates["prompt"]

            found = True
            _save_user_jobs(user_jobs)
            print(json.dumps({"status": "updated", "job": job}, indent=2, default=str))
            break

    if not found:
        print(json.dumps({"error": f"User job '{job_id}' not found"}))
        sys.exit(1)


def cmd_pause(job_id: str) -> None:
    """Disable a job."""
    _set_enabled(job_id, False)


def cmd_resume(job_id: str) -> None:
    """Enable a job."""
    _set_enabled(job_id, True)


def _set_enabled(job_id: str, enabled: bool) -> None:
    """Toggle enabled state for a user job."""
    if job_id in _get_all_system_job_ids():
        print(json.dumps({"error": f"Cannot modify system job '{job_id}'. Edit jobs.yaml directly."}))
        sys.exit(1)

    user_jobs = _get_user_jobs()
    for job in user_jobs:
        if job["id"] == job_id:
            job["enabled"] = enabled
            _save_user_jobs(user_jobs)
            action = "resumed" if enabled else "paused"
            print(json.dumps({"status": action, "job_id": job_id, "enabled": enabled}))
            return

    print(json.dumps({"error": f"User job '{job_id}' not found"}))
    sys.exit(1)


def cmd_delete(job_id: str) -> None:
    """Delete a user job."""
    if job_id in _get_all_system_job_ids():
        print(json.dumps({"error": f"Cannot delete system job '{job_id}'. Edit jobs.yaml directly."}))
        sys.exit(1)

    user_jobs = _get_user_jobs()
    original_count = len(user_jobs)
    user_jobs = [j for j in user_jobs if j["id"] != job_id]

    if len(user_jobs) == original_count:
        print(json.dumps({"error": f"User job '{job_id}' not found"}))
        sys.exit(1)

    _save_user_jobs(user_jobs)
    print(json.dumps({"status": "deleted", "job_id": job_id}))


def cmd_show(job_id: str) -> None:
    """Show full details of a job including state."""
    state = _load_state()
    job_states = state.get("jobs", {})

    # Search system jobs (defined in code)
    for j in get_all_system_jobs():
        if j.id == job_id:
            result = j.model_dump()
            result["_source"] = "system"
            result["_state"] = job_states.get(job_id, {})
            print(json.dumps(result, indent=2, default=str))
            return

    # Search user jobs
    for j in _get_user_jobs():
        if j["id"] == job_id:
            j["_source"] = "user"
            j["_state"] = job_states.get(job_id, {})
            print(json.dumps(j, indent=2, default=str))
            return

    print(json.dumps({"error": f"Job '{job_id}' not found"}))
    sys.exit(1)


def cmd_validate_cron(expr: str) -> None:
    """Validate a cron expression and show next run time."""
    valid, msg = _validate_cron(expr)
    print(json.dumps({"valid": valid, "message": msg}))
    if not valid:
        sys.exit(1)


def cmd_pipeline(args_ns: argparse.Namespace) -> None:
    """Create a pipeline job — convenience wrapper around agent_task.

    Creates a user job that runs the s_autonomous-pipeline orchestrator skill
    as a headless Claude CLI task. The prompt instructs the agent
    to execute the full pipeline for the given requirement.

    Usage:
        python job_manager.py pipeline \\
          --project SwarmAI \\
          --requirement "Add retry logic to payment API" \\
          [--schedule "0 9 * * 1-5"] \\
          [--profile full] \\
          [--budget 2.00] \\
          [--one-shot]
    """
    project = args_ns.project
    requirement = args_ns.requirement
    profile = args_ns.profile or "full"
    budget = args_ns.budget
    schedule = args_ns.schedule
    one_shot = args_ns.one_shot

    # Build the prompt that triggers s_autonomous-pipeline behavior
    prompt = (
        f"Run the full pipeline for project {project}:\n\n"
        f"{requirement}\n\n"
        f"Use pipeline profile: {profile}.\n"
        f"Follow the s_autonomous-pipeline skill instructions exactly.\n"
        f"Use artifact_cli.py for all state operations.\n"
        f"Checkpoint on L2 BLOCK or budget limits.\n"
        f"Present the completion summary when done."
    )

    # Build job spec
    job_spec = {
        "name": f"Pipeline: {requirement[:50]}",
        "type": "agent_task",
        "schedule": schedule,
        "prompt": prompt,
        "config": {
            "prompt": prompt,
            "pipeline_project": project,
            "pipeline_requirement": requirement,
            "pipeline_profile": profile,
        },
        "safety": {
            "max_budget_usd": budget,
            "timeout_seconds": 600,  # 10 min for pipeline execution
            "allowed_tools": [],     # Pipeline uses bash (artifact_cli), file read/write
        },
    }

    if one_shot:
        # One-shot: schedule far future, disable after first run
        job_spec["schedule"] = "0 0 1 1 *"  # Jan 1 — effectively "run once manually"
        job_spec["config"]["one_shot"] = True

    # Delegate to cmd_create
    cmd_create(json.dumps(job_spec))


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Swarm Job Manager")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List all jobs with status")

    p_create = sub.add_parser("create", help="Create a user job")
    p_create.add_argument("--json", required=True, help="Job definition as JSON")

    p_edit = sub.add_parser("edit", help="Edit a user job")
    p_edit.add_argument("job_id", help="Job ID to edit")
    p_edit.add_argument("--json", required=True, help="Fields to update as JSON")

    p_pause = sub.add_parser("pause", help="Pause (disable) a job")
    p_pause.add_argument("job_id")

    p_resume = sub.add_parser("resume", help="Resume (enable) a job")
    p_resume.add_argument("job_id")

    p_delete = sub.add_parser("delete", help="Delete a user job")
    p_delete.add_argument("job_id")

    p_show = sub.add_parser("show", help="Show full job details")
    p_show.add_argument("job_id")

    p_validate = sub.add_parser("validate-cron", help="Validate a cron expression")
    p_validate.add_argument("expression")

    # Pipeline convenience command (v3)
    p_pipeline = sub.add_parser("pipeline", help="Create a pipeline background job")
    p_pipeline.add_argument("--project", required=True, help="Project name")
    p_pipeline.add_argument("--requirement", required=True, help="What to build")
    p_pipeline.add_argument("--schedule", default="0 9 * * 1-5", help="Cron schedule (default: weekdays 9am)")
    p_pipeline.add_argument("--profile", default="full", help="Pipeline profile: full/trivial/research/docs/bugfix")
    p_pipeline.add_argument("--budget", type=float, default=2.00, help="Max spend per run in USD")
    p_pipeline.add_argument("--one-shot", action="store_true", help="Run once (not recurring)")

    args = parser.parse_args()

    if args.command == "list":
        cmd_list()
    elif args.command == "create":
        cmd_create(args.json)
    elif args.command == "edit":
        cmd_edit(args.job_id, args.json)
    elif args.command == "pause":
        cmd_pause(args.job_id)
    elif args.command == "resume":
        cmd_resume(args.job_id)
    elif args.command == "delete":
        cmd_delete(args.job_id)
    elif args.command == "show":
        cmd_show(args.job_id)
    elif args.command == "validate-cron":
        cmd_validate_cron(args.expression)
    elif args.command == "pipeline":
        cmd_pipeline(args)


if __name__ == "__main__":
    main()
