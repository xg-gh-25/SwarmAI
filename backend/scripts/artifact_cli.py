#!/usr/bin/env python3
"""CLI for artifact registry operations.

Called by the agent via bash to discover upstream artifacts and publish
new artifacts.  Follows the same pattern as ``locked_write.py`` —
a standalone script with no FastAPI dependency.

Usage:
    # Discover artifacts for a skill
    python artifact_cli.py discover --project SwarmAI --types research,alternatives

    # Publish a new artifact
    python artifact_cli.py publish --project SwarmAI --type evaluation \\
        --producer s_evaluate --summary "GO: ROI 3.2" --data '{"roi": 3.2}'

    # Get pipeline state
    python artifact_cli.py state --project SwarmAI

    # Advance pipeline state
    python artifact_cli.py advance --project SwarmAI --state think

    # List all projects with pipeline status
    python artifact_cli.py projects

Public symbols:
- ``main``  — CLI entry point with subcommand dispatch.
"""

import argparse
import json
import sys
from pathlib import Path

# Add parent directory to path so we can import core modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.artifact_registry import ArtifactRegistry


def _get_workspace() -> Path:
    """Resolve workspace root from environment or default."""
    import os
    ws = os.environ.get("SWARM_WORKSPACE", str(Path.home() / ".swarm-ai" / "SwarmWS"))
    return Path(ws).expanduser().resolve()


def cmd_discover(args, reg: ArtifactRegistry) -> None:
    """Discover active artifacts of given types."""
    types = [t.strip() for t in args.types.split(",") if t.strip()]
    artifacts = reg.discover(args.project, *types)

    if not artifacts:
        print(json.dumps({"artifacts": [], "count": 0}))
        return

    result = []
    for a in artifacts:
        entry = {
            "id": a.id,
            "type": a.type,
            "producer": a.producer,
            "summary": a.summary,
            "file": a.file,
        }
        # Optionally load full data
        if args.full:
            artifact_dir = (
                _get_workspace() / "Projects" / args.project / ".artifacts"
            )
            data_file = artifact_dir / a.file
            if data_file.exists():
                try:
                    entry["data"] = json.loads(
                        data_file.read_text(encoding="utf-8")
                    )
                except (json.JSONDecodeError, OSError):
                    pass
        result.append(entry)

    print(json.dumps({"artifacts": result, "count": len(result)}, indent=2))


def cmd_publish(args, reg: ArtifactRegistry) -> None:
    """Publish a new artifact."""
    try:
        data = json.loads(args.data)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON data: {e}"}), file=sys.stderr)
        sys.exit(1)

    try:
        artifact_id = reg.publish(
            project=args.project,
            artifact_type=args.type,
            data=data,
            producer=args.producer,
            summary=args.summary,
            topic=args.topic or "",
        )
        print(json.dumps({"artifact_id": artifact_id, "project": args.project}))
    except (ValueError, FileNotFoundError) as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def cmd_state(args, reg: ArtifactRegistry) -> None:
    """Get pipeline state for a project."""
    state = reg.get_pipeline_state(args.project)
    print(json.dumps({"project": args.project, "pipeline_state": state}))


def cmd_advance(args, reg: ArtifactRegistry) -> None:
    """Advance pipeline state."""
    try:
        reg.advance_pipeline(args.project, args.state)
        print(json.dumps({"project": args.project, "pipeline_state": args.state}))
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def cmd_learn(args, reg: ArtifactRegistry) -> None:
    """Record outcome of a pipeline run for learning feedback."""
    lessons = [l.strip() for l in (args.lessons or "").split(";") if l.strip()]
    reg.record_outcome(
        project=args.project,
        evaluation_id=args.evaluation_id,
        outcome=args.outcome,
        actual_effort=args.actual_effort,
        lessons=lessons or None,
    )
    print(json.dumps({
        "project": args.project,
        "evaluation_id": args.evaluation_id,
        "outcome": args.outcome,
        "recorded": True,
    }))


def cmd_projects(args, reg: ArtifactRegistry) -> None:
    """List all projects with pipeline status."""
    statuses = reg.list_projects()
    result = [
        {
            "project": s.project,
            "pipeline_state": s.pipeline_state,
            "artifact_count": s.artifact_count,
            "active_artifact_count": s.active_artifact_count,
            "latest_artifact": s.latest_artifact,
        }
        for s in statuses
    ]
    print(json.dumps({"projects": result, "count": len(result)}, indent=2))


# ── Pipeline run management ──────────────────────────────────────────

# Default token budget estimates per stage (conservative).
# Historical calibration overrides these when data is available.
DEFAULT_STAGE_BUDGETS = {
    "evaluate": 10_000,
    "think": 40_000,
    "plan": 30_000,
    "build": 60_000,
    "review": 25_000,
    "test": 40_000,
    "deliver": 15_000,
    "reflect": 10_000,
}
SESSION_BUDGET = 800_000       # 80% of 1M context window
CHECKPOINT_RESERVE = 50_000    # Reserve for checkpoint handoff


def _pipeline_runs_dir(project: str) -> Path:
    """Get the directory for pipeline run state files."""
    return _get_workspace() / "Projects" / project / ".artifacts"


def _gen_run_id() -> str:
    import uuid
    return f"run_{uuid.uuid4().hex[:8]}"


def _load_completed_runs(project: str, limit: int = 10) -> list[dict]:
    """Load completed pipeline runs for historical calibration."""
    run_dir = _pipeline_runs_dir(project)
    if not run_dir.exists():
        return []
    runs = []
    for f in sorted(run_dir.glob("pipeline-run-*.json"), reverse=True):
        try:
            state = json.loads(f.read_text(encoding="utf-8"))
            if state.get("status") == "completed" and state.get("stages"):
                runs.append(state)
                if len(runs) >= limit:
                    break
        except (json.JSONDecodeError, KeyError):
            continue
    return runs


def _calibrated_stage_budget(project: str, stage: str) -> int:
    """Get calibrated token budget for a stage from historical data.
    Falls back to DEFAULT_STAGE_BUDGETS if no history."""
    runs = _load_completed_runs(project, limit=5)
    costs = []
    for r in runs:
        for s in r.get("stages", []):
            if s.get("stage") == stage and s.get("token_cost", 0) > 0:
                costs.append(s["token_cost"])
    if costs:
        avg = sum(costs) / len(costs)
        return int(avg * 1.2)  # 20% buffer over historical average
    return DEFAULT_STAGE_BUDGETS.get(stage, 30_000)


def _estimate_session_budget(project: str) -> dict:
    """Build a full budget estimate for a pipeline run."""
    stage_estimates = {}
    for stage in DEFAULT_STAGE_BUDGETS:
        stage_estimates[stage] = _calibrated_stage_budget(project, stage)

    return {
        "session_total": SESSION_BUDGET,
        "checkpoint_reserve": CHECKPOINT_RESERVE,
        "consumed": 0,
        "remaining": SESSION_BUDGET,
        "stage_estimates": stage_estimates,
        "calibration_source": "historical" if _load_completed_runs(project, 1) else "defaults",
    }


def cmd_run_create(args, reg: ArtifactRegistry) -> None:
    """Create a new pipeline run state file."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    run_id = _gen_run_id()

    # Load historical calibration for budget estimate
    budget = _estimate_session_budget(args.project)

    run_state = {
        "id": run_id,
        "project": args.project,
        "requirement": args.requirement,
        "profile": args.profile or None,
        "status": "running",
        "stages": [],
        "taste_decisions": [],
        "budget": budget,
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
    }

    run_dir = _pipeline_runs_dir(args.project)
    run_dir.mkdir(parents=True, exist_ok=True)
    run_file = run_dir / f"pipeline-run-{run_id}.json"
    run_file.write_text(json.dumps(run_state, indent=2), encoding="utf-8")

    print(json.dumps({"pipeline_id": run_id, "project": args.project, "file": str(run_file)}))


def cmd_run_update(args, reg: ArtifactRegistry) -> None:
    """Update a pipeline run's stage record or status."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    run_dir = _pipeline_runs_dir(args.project)
    run_file = run_dir / f"pipeline-run-{args.run_id}.json"
    if not run_file.exists():
        print(json.dumps({"error": f"Pipeline run {args.run_id} not found"}), file=sys.stderr)
        sys.exit(1)

    run_state = json.loads(run_file.read_text(encoding="utf-8"))

    if args.status:
        run_state["status"] = args.status
        if args.status == "completed":
            run_state["completed_at"] = now

    if args.stage_json:
        stage_record = json.loads(args.stage_json)
        # Replace existing stage record or append
        existing_idx = next(
            (i for i, s in enumerate(run_state["stages"]) if s["stage"] == stage_record["stage"]),
            None,
        )
        if existing_idx is not None:
            run_state["stages"][existing_idx] = stage_record
        else:
            run_state["stages"].append(stage_record)

    if args.taste_decision:
        decision = json.loads(args.taste_decision)
        run_state["taste_decisions"].append(decision)

    if args.profile:
        run_state["profile"] = args.profile

    run_state["updated_at"] = now
    run_file.write_text(json.dumps(run_state, indent=2), encoding="utf-8")

    print(json.dumps({"pipeline_id": args.run_id, "updated": True}))


def cmd_run_get(args, reg: ArtifactRegistry) -> None:
    """Get a pipeline run's current state."""
    run_dir = _pipeline_runs_dir(args.project)

    if args.run_id:
        run_file = run_dir / f"pipeline-run-{args.run_id}.json"
        if not run_file.exists():
            print(json.dumps({"error": f"Pipeline run {args.run_id} not found"}), file=sys.stderr)
            sys.exit(1)
        run_state = json.loads(run_file.read_text(encoding="utf-8"))
        print(json.dumps(run_state, indent=2))
        return

    # List all pipeline runs for this project
    if not run_dir.exists():
        print(json.dumps({"runs": [], "count": 0}))
        return

    runs = []
    for f in sorted(run_dir.glob("pipeline-run-*.json"), reverse=True):
        try:
            state = json.loads(f.read_text(encoding="utf-8"))
            runs.append({
                "id": state["id"],
                "requirement": state["requirement"][:80],
                "status": state["status"],
                "profile": state.get("profile"),
                "stages_completed": sum(
                    1 for s in state.get("stages", []) if s.get("status") == "completed"
                ),
                "created_at": state["created_at"],
            })
        except (json.JSONDecodeError, KeyError):
            continue

    print(json.dumps({"runs": runs, "count": len(runs)}, indent=2))


def cmd_run_checkpoint(args, reg: ArtifactRegistry) -> None:
    """Atomic checkpoint: pause run + publish checkpoint artifact + create Radar todo."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    run_dir = _pipeline_runs_dir(args.project)
    run_file = run_dir / f"pipeline-run-{args.run_id}.json"
    if not run_file.exists():
        print(json.dumps({"error": f"Pipeline run {args.run_id} not found"}), file=sys.stderr)
        sys.exit(1)

    run_state = json.loads(run_file.read_text(encoding="utf-8"))

    # 1. Pause the run
    run_state["status"] = "paused"
    run_state["updated_at"] = now

    # Store checkpoint metadata in the run state
    completed_stages = [s["stage"] for s in run_state["stages"] if s.get("status") == "completed"]
    checkpoint_meta = {
        "reason": args.reason,
        "stage": args.stage,
        "checkpointed_at": now,
        "completed_stages": completed_stages,
        "taste_decisions_pending": len(run_state.get("taste_decisions", [])),
    }
    run_state["checkpoint"] = checkpoint_meta
    run_file.write_text(json.dumps(run_state, indent=2), encoding="utf-8")

    # 2. Publish checkpoint artifact to the registry
    checkpoint_data = {
        "pipeline_id": args.run_id,
        "project": args.project,
        "requirement": run_state["requirement"],
        "completed_stages": [
            {
                "stage": s["stage"],
                "artifact_id": s.get("artifact_id"),
                "notes": s.get("notes"),
            }
            for s in run_state["stages"]
            if s.get("status") == "completed"
        ],
        "next_stage": args.stage,
        "reason": args.reason,
        "taste_decisions": run_state.get("taste_decisions", []),
        "budget": run_state.get("budget"),
    }
    try:
        artifact_id = reg.publish(
            project=args.project,
            artifact_type="checkpoint",
            data=checkpoint_data,
            producer="s_pipeline",
            summary=f"Pipeline paused at {args.stage}: {args.reason}",
            topic=args.run_id,
        )
    except (ValueError, FileNotFoundError):
        artifact_id = None

    # 3. Create Radar todo for visibility and resume
    todo_result = _create_checkpoint_todo(
        project=args.project,
        run_id=args.run_id,
        requirement=run_state["requirement"],
        stage=args.stage,
        reason=args.reason,
        completed_stages=completed_stages,
    )

    result = {
        "pipeline_id": args.run_id,
        "status": "paused",
        "checkpoint_artifact": artifact_id,
        "radar_todo": todo_result,
        "reason": args.reason,
        "next_stage": args.stage,
    }
    print(json.dumps(result, indent=2))


def _create_checkpoint_todo(
    project: str,
    run_id: str,
    requirement: str,
    stage: str,
    reason: str,
    completed_stages: list[str],
) -> dict | None:
    """Create a Radar todo for a pipeline checkpoint.

    Uses todo_db.py directly (same pattern as s_radar-todo skill).
    Returns the todo info or None if DB not available.
    """
    import sqlite3
    import uuid as _uuid

    db_path = Path.home() / ".swarm-ai" / "data.db"
    if not db_path.exists():
        return None

    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        todo_id = str(_uuid.uuid4())

        title = f"Pipeline paused: {requirement[:60]}"
        description = (
            f"Pipeline {run_id} for {project} paused at {stage.upper()} stage.\n"
            f"Reason: {reason}\n"
            f"Completed: {', '.join(completed_stages) if completed_stages else 'none'}\n"
            f"Resume: resolve the issue, then 'resume pipeline for {project}'"
        )
        linked_context = json.dumps({
            "pipeline_id": run_id,
            "project": project,
            "pipeline_stage": stage,
            "completed_stages": completed_stages,
            "reason": reason,
            "next_step": f"Resolve '{reason}', then resume pipeline for {project}",
            "files": [f"Projects/{project}/.artifacts/pipeline-run-{run_id}.json"],
        })

        with sqlite3.connect(str(db_path), timeout=5.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """INSERT INTO todos (id, workspace_id, title, description, source,
                   source_type, status, priority, due_date, linked_context, task_id,
                   created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, NULL, ?, ?)""",
                (
                    todo_id, "swarmws", title, description,
                    f"pipeline:{run_id}",
                    "ai_detected",
                    "high",  # pipeline checkpoints are high priority
                    None,
                    linked_context,
                    now, now,
                ),
            )
            conn.commit()
        return {"todo_id": todo_id, "title": title}
    except (sqlite3.Error, OSError) as e:
        return {"error": str(e)}


def cmd_run_history(args, reg: ArtifactRegistry) -> None:
    """Show historical token costs per stage from completed pipeline runs.

    Used for calibration: the agent reads this to know how many tokens
    each stage actually consumes, not just the default estimates.
    """
    runs = _load_completed_runs(args.project, limit=args.limit)
    if not runs:
        print(json.dumps({
            "project": args.project,
            "message": "No completed pipeline runs found",
            "stage_averages": {},
            "calibration": "defaults",
        }))
        return

    # Aggregate token costs per stage
    stage_costs: dict[str, list[int]] = {}
    for r in runs:
        for s in r.get("stages", []):
            name = s.get("stage", "unknown")
            cost = s.get("token_cost", 0)
            if cost > 0:
                stage_costs.setdefault(name, []).append(cost)

    averages = {}
    for stage, costs in sorted(stage_costs.items()):
        avg = sum(costs) / len(costs)
        averages[stage] = {
            "avg_tokens": int(avg),
            "min_tokens": min(costs),
            "max_tokens": max(costs),
            "samples": len(costs),
            "calibrated_estimate": int(avg * 1.2),  # +20% buffer
        }

    # Pipeline-level stats
    run_totals = []
    for r in runs:
        total = sum(s.get("token_cost", 0) for s in r.get("stages", []))
        if total > 0:
            run_totals.append(total)

    pipeline_stats = {}
    if run_totals:
        pipeline_stats = {
            "avg_total_tokens": int(sum(run_totals) / len(run_totals)),
            "min_total_tokens": min(run_totals),
            "max_total_tokens": max(run_totals),
            "runs_analyzed": len(run_totals),
            "fits_single_session": int(sum(run_totals) / len(run_totals)) < SESSION_BUDGET,
        }

    print(json.dumps({
        "project": args.project,
        "stage_averages": averages,
        "pipeline_stats": pipeline_stats,
        "calibration": "historical",
    }, indent=2))


def cmd_run_budget(args, reg: ArtifactRegistry) -> None:
    """Check budget status for an active pipeline run.

    Reports consumed tokens, remaining budget, and whether the next
    stage fits within the budget. Used by the agent to decide when
    to checkpoint.
    """
    run_dir = _pipeline_runs_dir(args.project)
    run_file = run_dir / f"pipeline-run-{args.run_id}.json"
    if not run_file.exists():
        print(json.dumps({"error": f"Pipeline run {args.run_id} not found"}), file=sys.stderr)
        sys.exit(1)

    run_state = json.loads(run_file.read_text(encoding="utf-8"))
    budget = run_state.get("budget", _estimate_session_budget(args.project))

    # Calculate consumed from completed stages
    consumed = sum(s.get("token_cost", 0) for s in run_state.get("stages", []))
    remaining = budget["session_total"] - consumed
    usable = remaining - budget["checkpoint_reserve"]

    # Determine next stage
    completed_stages = {s["stage"] for s in run_state.get("stages", []) if s.get("status") == "completed"}
    profile_stages = _get_profile_stages(run_state.get("profile", "full"))
    next_stage = None
    for s in profile_stages:
        if s not in completed_stages:
            next_stage = s
            break

    next_stage_estimate = budget.get("stage_estimates", DEFAULT_STAGE_BUDGETS).get(next_stage, 30_000) if next_stage else 0
    should_checkpoint = next_stage is not None and usable < next_stage_estimate

    # Quality check: checkpoint if >70% consumed (context degradation)
    pct_consumed = consumed / budget["session_total"] if budget["session_total"] > 0 else 0
    if pct_consumed > 0.7 and next_stage:
        should_checkpoint = True

    result = {
        "pipeline_id": args.run_id,
        "budget_total": budget["session_total"],
        "consumed": consumed,
        "remaining": remaining,
        "usable": usable,
        "pct_consumed": round(pct_consumed * 100, 1),
        "next_stage": next_stage,
        "next_stage_estimate": next_stage_estimate,
        "should_checkpoint": should_checkpoint,
        "reason": (
            f"Budget insufficient for {next_stage} (need {next_stage_estimate}, have {usable})"
            if should_checkpoint and usable < next_stage_estimate
            else f"Context quality degradation (>{int(pct_consumed*100)}% consumed)"
            if should_checkpoint
            else "Budget OK"
        ),
        "calibration_source": budget.get("calibration_source", "defaults"),
    }
    print(json.dumps(result, indent=2))


def _get_profile_stages(profile: str | None) -> list[str]:
    """Get the ordered stage list for a pipeline profile."""
    profiles = {
        "full": ["evaluate", "think", "plan", "build", "review", "test", "deliver", "reflect"],
        "trivial": ["evaluate", "build", "review", "test", "deliver", "reflect"],
        "research": ["evaluate", "think", "reflect"],
        "docs": ["evaluate", "think", "plan", "deliver", "reflect"],
        "bugfix": ["evaluate", "plan", "build", "review", "test", "deliver", "reflect"],
    }
    return profiles.get(profile or "full", profiles["full"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Artifact registry CLI for SwarmAI pipeline"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # discover
    p_discover = sub.add_parser("discover", help="Discover artifacts by type")
    p_discover.add_argument("--project", required=True)
    p_discover.add_argument("--types", required=True, help="Comma-separated types")
    p_discover.add_argument("--full", action="store_true", help="Include full artifact data")

    # publish
    p_publish = sub.add_parser("publish", help="Publish a new artifact")
    p_publish.add_argument("--project", required=True)
    p_publish.add_argument("--type", required=True)
    p_publish.add_argument("--producer", required=True)
    p_publish.add_argument("--summary", required=True)
    p_publish.add_argument("--data", required=True, help="JSON data string")
    p_publish.add_argument("--topic", default="")

    # state
    p_state = sub.add_parser("state", help="Get pipeline state")
    p_state.add_argument("--project", required=True)

    # advance
    p_advance = sub.add_parser("advance", help="Advance pipeline state")
    p_advance.add_argument("--project", required=True)
    p_advance.add_argument("--state", required=True)

    # learn
    p_learn = sub.add_parser("learn", help="Record pipeline outcome for learning")
    p_learn.add_argument("--project", required=True)
    p_learn.add_argument("--evaluation-id", required=True, help="ID of evaluation artifact")
    p_learn.add_argument("--outcome", required=True, choices=["success", "partial", "failure", "cancelled"])
    p_learn.add_argument("--actual-effort", default=None, help="Actual effort (T-shirt or sessions)")
    p_learn.add_argument("--lessons", default=None, help="Semicolon-separated lessons")

    # projects
    sub.add_parser("projects", help="List all projects")

    # run-create
    p_run_create = sub.add_parser("run-create", help="Create a new pipeline run")
    p_run_create.add_argument("--project", required=True)
    p_run_create.add_argument("--requirement", required=True, help="Requirement text")
    p_run_create.add_argument("--profile", default=None, help="Pipeline profile: full/trivial/research/docs/bugfix")

    # run-update
    p_run_update = sub.add_parser("run-update", help="Update a pipeline run")
    p_run_update.add_argument("--project", required=True)
    p_run_update.add_argument("--run-id", required=True, help="Pipeline run ID")
    p_run_update.add_argument("--status", default=None, help="New status: running/paused/completed/failed/cancelled")
    p_run_update.add_argument("--stage-json", default=None, help="Stage record JSON to add/update")
    p_run_update.add_argument("--taste-decision", default=None, help="Taste decision JSON to append")
    p_run_update.add_argument("--profile", default=None, help="Pipeline profile override")

    # run-get
    p_run_get = sub.add_parser("run-get", help="Get pipeline run state")
    p_run_get.add_argument("--project", required=True)
    p_run_get.add_argument("--run-id", default=None, help="Specific run ID (omit for list)")

    # run-checkpoint
    p_run_cp = sub.add_parser("run-checkpoint", help="Checkpoint: pause + artifact + Radar todo")
    p_run_cp.add_argument("--project", required=True)
    p_run_cp.add_argument("--run-id", required=True, help="Pipeline run ID")
    p_run_cp.add_argument("--stage", required=True, help="Stage where pipeline paused")
    p_run_cp.add_argument("--reason", required=True, help="Why the pipeline paused")

    # run-history
    p_run_hist = sub.add_parser("run-history", help="Historical token costs for calibration")
    p_run_hist.add_argument("--project", required=True)
    p_run_hist.add_argument("--limit", type=int, default=10, help="Max completed runs to analyze")

    # run-budget
    p_run_bgt = sub.add_parser("run-budget", help="Check budget status for active pipeline")
    p_run_bgt.add_argument("--project", required=True)
    p_run_bgt.add_argument("--run-id", required=True, help="Pipeline run ID")

    args = parser.parse_args()
    reg = ArtifactRegistry(_get_workspace())

    handlers = {
        "discover": cmd_discover,
        "publish": cmd_publish,
        "learn": cmd_learn,
        "state": cmd_state,
        "advance": cmd_advance,
        "projects": cmd_projects,
        "run-create": cmd_run_create,
        "run-update": cmd_run_update,
        "run-get": cmd_run_get,
        "run-checkpoint": cmd_run_checkpoint,
        "run-history": cmd_run_history,
        "run-budget": cmd_run_budget,
    }
    handlers[args.command](args, reg)


if __name__ == "__main__":
    main()
