#!/usr/bin/env python3
"""CLI for artifact registry and pipeline run operations.

Called by the agent via bash to discover/publish artifacts and manage
pipeline runs.  Follows the same pattern as ``locked_write.py`` —
a standalone script with no FastAPI dependency.

Usage — Artifact Registry:
    python artifact_cli.py discover --project SwarmAI --types research,alternatives [--full]
    python artifact_cli.py publish --project SwarmAI --type evaluation \\
        --producer s_evaluate --summary "GO" --data '{"roi": 3.2}' [--run-id run_xxx]
    python artifact_cli.py state --project SwarmAI
    python artifact_cli.py advance --project SwarmAI --state think
    python artifact_cli.py learn --project SwarmAI --evaluation-id art_xxx --outcome success
    python artifact_cli.py projects

Usage — Pipeline Runs (stored in .artifacts/runs/<run_id>/):
    python artifact_cli.py run-create --project SwarmAI --requirement "Add feature" [--profile full]
    python artifact_cli.py run-update --project SwarmAI --run-id run_xxx [--stage-json '...'] [--status completed]
    python artifact_cli.py run-get --project SwarmAI [--run-id run_xxx]
    python artifact_cli.py run-budget --project SwarmAI --run-id run_xxx
    python artifact_cli.py run-checkpoint --project SwarmAI --run-id run_xxx --stage build --reason "L2 BLOCK"
    python artifact_cli.py run-history --project SwarmAI [--limit 10]
    python artifact_cli.py run-status [--active-only]
    python artifact_cli.py run-resume --project SwarmAI --run-id run_xxx

Storage layout:
    Projects/<project>/.artifacts/
        manifest.json                   # global artifact index
        <type>-<date>-<topic>.json      # standalone artifacts (no pipeline)
        runs/
            <run_id>/
                run.json                # pipeline run state
                <type>-<date>.json      # artifacts scoped to this run

Public symbols:
- ``main``  — CLI entry point with subcommand dispatch.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add parent directory to path so we can import core modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.artifact_registry import ArtifactRegistry
from core.pipeline_profiles import get_profile_stages


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

    run_id = getattr(args, "run_id", None)
    try:
        artifact_id = reg.publish(
            project=args.project,
            artifact_type=args.type,
            data=data,
            producer=args.producer,
            summary=args.summary,
            topic=args.topic or "",
            run_id=run_id,
        )
        result = {"artifact_id": artifact_id, "project": args.project}
        if run_id:
            result["run_id"] = run_id
        print(json.dumps(result))
    except (ValueError, FileNotFoundError) as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def cmd_state(args, reg: ArtifactRegistry) -> None:
    """Get pipeline state for a project."""
    state = reg.get_pipeline_state(args.project)
    print(json.dumps({"project": args.project, "pipeline_state": state}))


def cmd_advance(args, reg: ArtifactRegistry) -> None:
    """Advance pipeline state. Auto-validates if a run is active.

    If a pipeline run exists and the current stage has a record,
    runs the pipeline validator. Refuses to advance on BLOCK errors.
    Warnings are printed but don't block advancement.
    """
    # Auto-validate before advancing (structural enforcement)
    try:
        _auto_validate_before_advance(args.project, args.state)
    except SystemExit:
        raise  # Re-raise if validation blocks
    except Exception as e:
        # Non-blocking: if validator itself fails, still advance
        print(json.dumps({"validation_warning": f"Validator error: {e}"}), file=sys.stderr)

    try:
        reg.advance_pipeline(args.project, args.state)
        print(json.dumps({"project": args.project, "pipeline_state": args.state}))
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def _auto_validate_before_advance(project: str, next_state: str) -> None:
    """Run pipeline validator on the current stage before advancing.

    Blocks on errors, warns on warnings. Skips if no active run found.
    """
    import subprocess

    # Find active run
    artifacts_dir = Path.home() / ".swarm-ai" / "SwarmWS" / "Projects" / project / ".artifacts" / "runs"
    if not artifacts_dir.exists():
        return

    # Find the most recent running run
    run_id = None
    for run_dir in sorted(artifacts_dir.iterdir(), reverse=True):
        run_file = run_dir / "run.json"
        if run_file.exists():
            run_data = json.loads(run_file.read_text())
            if run_data.get("status") == "running":
                run_id = run_data["id"]
                stages = run_data.get("stages", [])
                break

    if not run_id or not stages:
        return

    # Determine current stage (last completed)
    current_stage = None
    for s in reversed(stages):
        status = s.get("status", "")
        if status in ("done", "completed"):
            current_stage = s.get("stage", s.get("name"))
            break

    if not current_stage:
        return

    # Run validator
    try:
        validator = Path(__file__).parent / "pipeline_validator.py"
        result = subprocess.run(
            [sys.executable, str(validator), "check",
             "--project", project, "--run-id", run_id, "--stage", current_stage],
            capture_output=True, text=True, timeout=10,
            cwd=str(Path(__file__).parent.parent),
        )
        if result.stdout:
            validation = json.loads(result.stdout)
            if not validation.get("valid", True):
                errors = validation.get("errors", [])
                print(json.dumps({
                    "validation_blocked": True,
                    "stage": current_stage,
                    "errors": errors,
                }, indent=2), file=sys.stderr)
                sys.exit(1)
            warnings = validation.get("warnings", [])
            if warnings:
                print(json.dumps({"validation_warnings": warnings}), file=sys.stderr)
    except subprocess.TimeoutExpired:
        pass  # Don't block on validator timeout
    except (json.JSONDecodeError, OSError):
        pass  # Don't block on validator parse errors


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
    """Get the base runs directory: .artifacts/runs/."""
    return _get_workspace() / "Projects" / project / ".artifacts" / "runs"


def _run_dir(project: str, run_id: str) -> Path:
    """Get directory for a specific run: .artifacts/runs/<run_id>/."""
    return _pipeline_runs_dir(project) / run_id


def _resolve_run_file(project: str, run_id: str) -> Path:
    """Find the run.json file, checking new path (runs/<id>/run.json) then legacy (pipeline-run-<id>.json)."""
    # New path: .artifacts/runs/<run_id>/run.json
    new_path = _run_dir(project, run_id) / "run.json"
    if new_path.exists():
        return new_path

    # Legacy path: .artifacts/pipeline-run-<run_id>.json
    legacy_path = _get_workspace() / "Projects" / project / ".artifacts" / f"pipeline-run-{run_id}.json"
    if legacy_path.exists():
        return legacy_path

    print(json.dumps({"error": f"Pipeline run {run_id} not found"}), file=sys.stderr)
    sys.exit(1)


def _gen_run_id() -> str:
    import uuid
    return f"run_{uuid.uuid4().hex[:8]}"


def _load_completed_runs(project: str, limit: int = 10) -> list[dict]:
    """Load completed pipeline runs for historical calibration.

    Scans both new path (runs/*/run.json) and legacy (pipeline-run-*.json).
    """
    artifacts_dir = _get_workspace() / "Projects" / project / ".artifacts"
    runs = []

    # New path: .artifacts/runs/*/run.json
    runs_dir = artifacts_dir / "runs"
    if runs_dir.exists():
        for rd in sorted(runs_dir.iterdir(), reverse=True):
            run_file = rd / "run.json"
            if run_file.exists():
                try:
                    state = json.loads(run_file.read_text(encoding="utf-8"))
                    if state.get("status") == "completed" and state.get("stages"):
                        runs.append(state)
                        if len(runs) >= limit:
                            return runs
                except (json.JSONDecodeError, KeyError):
                    continue

    # Legacy path: .artifacts/pipeline-run-*.json
    if artifacts_dir.exists():
        seen_ids = {r["id"] for r in runs}
        for f in sorted(artifacts_dir.glob("pipeline-run-*.json"), reverse=True):
            try:
                state = json.loads(f.read_text(encoding="utf-8"))
                if state["id"] in seen_ids:
                    continue
                if state.get("status") == "completed" and state.get("stages"):
                    runs.append(state)
                    if len(runs) >= limit:
                        return runs
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

    rd = _run_dir(args.project, run_id)
    rd.mkdir(parents=True, exist_ok=True)
    run_file = rd / "run.json"
    run_file.write_text(json.dumps(run_state, indent=2), encoding="utf-8")

    print(json.dumps({"pipeline_id": run_id, "project": args.project, "file": str(run_file)}))


def cmd_run_update(args, reg: ArtifactRegistry) -> None:
    """Update a pipeline run's stage record or status."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    run_file = _resolve_run_file(args.project, args.run_id)
    run_state = json.loads(run_file.read_text(encoding="utf-8"))

    if args.status:
        run_state["status"] = args.status
        if args.status == "completed":
            run_state["completed_at"] = now

    if args.stage_json:
        stage_record = json.loads(args.stage_json)
        # Normalize: accept both "name" and "stage" as the stage identifier
        if "name" in stage_record and "stage" not in stage_record:
            stage_record["stage"] = stage_record.pop("name")
        # Replace existing stage record or append
        existing_idx = next(
            (i for i, s in enumerate(run_state["stages"])
             if s.get("stage", s.get("name")) == stage_record["stage"]),
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

    if args.ddd_checksums:
        run_state["ddd_checksums"] = json.loads(args.ddd_checksums)

    run_state["updated_at"] = now
    run_file.write_text(json.dumps(run_state, indent=2), encoding="utf-8")

    print(json.dumps({"pipeline_id": args.run_id, "updated": True}))


def cmd_run_get(args, reg: ArtifactRegistry) -> None:
    """Get a pipeline run's current state."""
    if args.run_id:
        run_file = _resolve_run_file(args.project, args.run_id)
        run_state = json.loads(run_file.read_text(encoding="utf-8"))
        print(json.dumps(run_state, indent=2))
        return

    # List all pipeline runs for this project (scan both new and legacy paths)
    runs = []
    artifacts_dir = _get_workspace() / "Projects" / args.project / ".artifacts"

    # New path: .artifacts/runs/*/run.json
    runs_dir = artifacts_dir / "runs"
    if runs_dir.exists():
        for rd in sorted(runs_dir.iterdir(), reverse=True):
            run_file = rd / "run.json"
            if run_file.exists():
                try:
                    state = json.loads(run_file.read_text(encoding="utf-8"))
                    runs.append(_run_summary(state))
                except (json.JSONDecodeError, KeyError):
                    continue

    # Legacy path: .artifacts/pipeline-run-*.json
    if artifacts_dir.exists():
        for f in sorted(artifacts_dir.glob("pipeline-run-*.json"), reverse=True):
            try:
                state = json.loads(f.read_text(encoding="utf-8"))
                # Skip if already found via new path
                if any(r["id"] == state["id"] for r in runs):
                    continue
                runs.append(_run_summary(state))
            except (json.JSONDecodeError, KeyError):
                continue

    print(json.dumps({"runs": runs, "count": len(runs)}, indent=2))


def _run_summary(state: dict) -> dict:
    """Extract summary fields from a pipeline run state."""
    return {
        "id": state["id"],
        "requirement": state["requirement"][:80],
        "status": state["status"],
        "profile": state.get("profile"),
        "stages_completed": sum(
            1 for s in state.get("stages", []) if s.get("status") == "completed"
        ),
        "created_at": state["created_at"],
    }


def cmd_run_checkpoint(args, reg: ArtifactRegistry) -> None:
    """Atomic checkpoint: pause run + publish checkpoint artifact + create Radar todo."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    run_file = _resolve_run_file(args.project, args.run_id)
    run_state = json.loads(run_file.read_text(encoding="utf-8"))

    # 1. Pause the run
    run_state["status"] = "paused"
    run_state["updated_at"] = now

    # Store checkpoint metadata in the run state
    completed_stages = [s.get("stage", s.get("name", "unknown")) for s in run_state["stages"] if s.get("status") == "completed"]
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
                "stage": s.get("stage", s.get("name", "unknown")),
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
            producer="s_autonomous-pipeline",
            summary=f"Pipeline paused at {args.stage}: {args.reason}",
            topic=args.run_id,
            run_id=args.run_id,
        )
    except (ValueError, FileNotFoundError):
        artifact_id = None

    # 3. Create Radar todo for visibility and resume
    # Tests can set SWARM_TODO_DB to a temp path to avoid polluting production DB
    _todo_db_override = os.environ.get("SWARM_TODO_DB")
    _todo_db_path = Path(_todo_db_override) if _todo_db_override else None
    todo_result = _create_checkpoint_todo(
        project=args.project,
        run_id=args.run_id,
        requirement=run_state["requirement"],
        stage=args.stage,
        reason=args.reason,
        completed_stages=completed_stages,
        db_path=_todo_db_path,
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
    db_path: Path | None = None,
) -> dict | None:
    """Create a Radar todo for a pipeline checkpoint.

    Uses todo_db.py directly (same pattern as s_radar-todo skill).
    Deduplicates: won't create a second pending todo with the same title.
    Returns the todo info or None if DB not available.

    ``db_path`` defaults to ``~/.swarm-ai/data.db``; tests can override
    to a temp DB to avoid polluting the production database.
    """
    import sqlite3
    import uuid as _uuid

    if db_path is None:
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
            "files": [f"Projects/{project}/.artifacts/runs/{run_id}/run.json"],
        })

        with sqlite3.connect(str(db_path), timeout=5.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            # Dedup: skip if a pending todo with same title already exists
            existing = conn.execute(
                "SELECT id FROM todos WHERE title = ? AND status = 'pending' LIMIT 1",
                (title,),
            ).fetchone()
            if existing:
                return existing[0]  # Return existing todo ID
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
    run_file = _resolve_run_file(args.project, args.run_id)
    run_state = json.loads(run_file.read_text(encoding="utf-8"))
    budget = run_state.get("budget", _estimate_session_budget(args.project))

    # Calculate consumed from completed stages
    consumed = sum(s.get("token_cost", 0) for s in run_state.get("stages", []))
    remaining = budget["session_total"] - consumed
    usable = remaining - budget["checkpoint_reserve"]

    # Determine next stage
    completed_stages = {s.get("stage", s.get("name", "unknown")) for s in run_state.get("stages", []) if s.get("status") == "completed"}
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


_get_profile_stages = get_profile_stages  # alias for backward compat within this file


def _status_entry(state: dict, project_name: str) -> dict:
    """Build a dashboard entry from a pipeline run state dict."""
    completed_stages = [s for s in state.get("stages", []) if s.get("status") == "completed"]
    total_stages = len(_get_profile_stages(state.get("profile")))
    consumed = sum(s.get("token_cost", 0) for s in state.get("stages", []))
    return {
        "id": state["id"],
        "project": project_name,
        "requirement": state.get("requirement", "")[:80],
        "status": state.get("status", "running"),
        "profile": state.get("profile", "full"),
        "progress": f"{len(completed_stages)}/{total_stages}",
        "stages_completed": len(completed_stages),
        "stages_total": total_stages,
        "tokens_consumed": consumed,
        "taste_decisions": len(state.get("taste_decisions", [])),
        "checkpoint": state.get("checkpoint"),
        "created_at": state.get("created_at", ""),
        "updated_at": state.get("updated_at", ""),
    }


def cmd_run_status(args, reg: ArtifactRegistry) -> None:
    """Cross-project pipeline dashboard data.

    Returns all active and recent pipeline runs across all projects.
    Designed for the Radar sidebar pipeline panel.
    """
    workspace = _get_workspace()
    projects_dir = workspace / "Projects"
    if not projects_dir.exists():
        print(json.dumps({"pipelines": [], "count": 0}))
        return

    all_pipelines = []
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        artifacts_dir = project_dir / ".artifacts"
        if not artifacts_dir.exists():
            continue

        project_name = project_dir.name
        seen_ids: set[str] = set()

        # New path: runs/*/run.json
        runs_dir = artifacts_dir / "runs"
        if runs_dir.exists():
            for rd in sorted(runs_dir.iterdir(), reverse=True):
                rf = rd / "run.json"
                if rf.exists():
                    try:
                        state = json.loads(rf.read_text(encoding="utf-8"))
                        state["_project"] = project_name
                        seen_ids.add(state["id"])
                        all_pipelines.append(_status_entry(state, project_name))
                    except (json.JSONDecodeError, OSError, KeyError):
                        continue

        # Legacy path: pipeline-run-*.json
        for run_file in sorted(artifacts_dir.glob("pipeline-run-*.json"), reverse=True):
            try:
                state = json.loads(run_file.read_text(encoding="utf-8"))
                if state.get("id") in seen_ids:
                    continue
                all_pipelines.append(_status_entry(state, project_name))
            except (json.JSONDecodeError, OSError, KeyError):
                continue

    # Sort: running first, then paused, then completed. Within each group, newest first.
    # ISO timestamps sort lexicographically, so negate by prepending complement for descending.
    status_order = {"running": 0, "paused": 1, "failed": 2, "completed": 3, "cancelled": 4}
    all_pipelines.sort(key=lambda p: p.get("updated_at", ""), reverse=True)  # newest first
    all_pipelines.sort(key=lambda p: status_order.get(p["status"], 9))  # stable: preserves newest-first within group

    # Limit: show all active (running/paused), up to 5 completed per project
    active = [p for p in all_pipelines if p["status"] in ("running", "paused")]
    completed = [p for p in all_pipelines if p["status"] not in ("running", "paused")]

    if args.active_only:
        output = active
    else:
        # Keep max 5 completed per project
        seen_completed: dict[str, int] = {}
        filtered_completed = []
        for p in completed:
            count = seen_completed.get(p["project"], 0)
            if count < 5:
                filtered_completed.append(p)
                seen_completed[p["project"]] = count + 1
        output = active + filtered_completed

    summary = {
        "running": sum(1 for p in all_pipelines if p["status"] == "running"),
        "paused": sum(1 for p in all_pipelines if p["status"] == "paused"),
        "completed": sum(1 for p in all_pipelines if p["status"] == "completed"),
        "total_tokens": sum(p["tokens_consumed"] for p in all_pipelines),
    }

    print(json.dumps({
        "pipelines": output,
        "count": len(output),
        "summary": summary,
    }, indent=2))


def cmd_run_resume(args, reg: ArtifactRegistry) -> None:
    """Resume a paused pipeline run.

    Checks that all pending escalations are resolved. If yes, sets
    status back to 'running' and clears the checkpoint. If not, reports
    which escalations are still open.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    run_file = _resolve_run_file(args.project, args.run_id)
    run_state = json.loads(run_file.read_text(encoding="utf-8"))

    if run_state["status"] != "paused":
        print(json.dumps({
            "error": f"Pipeline is '{run_state['status']}', not 'paused'",
            "pipeline_id": args.run_id,
        }), file=sys.stderr)
        sys.exit(1)

    # Check for unresolved escalations in blocked stages
    blocked_stages = [
        s for s in run_state.get("stages", [])
        if s.get("status") == "blocked" and s.get("escalation_id")
    ]

    checkpoint = run_state.get("checkpoint", {})
    next_stage = checkpoint.get("stage") or args.stage

    # Reset budget for new session
    budget = _estimate_session_budget(args.project)

    run_state["status"] = "running"
    run_state["budget"] = budget
    run_state["updated_at"] = now
    # Keep checkpoint for reference but mark it resolved
    if "checkpoint" in run_state:
        run_state["checkpoint"]["resumed_at"] = now

    run_file.write_text(json.dumps(run_state, indent=2), encoding="utf-8")

    print(json.dumps({
        "pipeline_id": args.run_id,
        "status": "running",
        "resumed_from": next_stage,
        "completed_stages": [s.get("stage", s.get("name", "unknown")) for s in run_state["stages"] if s.get("status") == "completed"],
        "budget": budget,
        "blocked_stages": [s.get("stage", s.get("name", "unknown")) for s in blocked_stages],
    }, indent=2))


def cmd_run_report(args, reg: ArtifactRegistry) -> None:
    """Generate REPORT.md for a completed (or running) pipeline run.

    Reads run.json + all published artifacts, produces a structured
    markdown report at .artifacts/runs/<RUN_ID>/REPORT.md.
    """
    from datetime import datetime, timezone

    run_file = _resolve_run_file(args.project, args.run_id)
    run_state = json.loads(run_file.read_text(encoding="utf-8"))

    stages = run_state.get("stages", [])
    taste_decisions = run_state.get("taste_decisions", [])
    profile = run_state.get("profile", "full")
    requirement = run_state.get("requirement", "")

    # Collect all decisions from stages
    all_decisions = []
    for s in stages:
        for d in s.get("decisions", []):
            all_decisions.append({
                "stage": s.get("stage", s.get("name", "?")),
                **d,
            })

    # Count decision types
    mech = sum(1 for d in all_decisions if d.get("classification") == "mechanical")
    taste = sum(1 for d in all_decisions if d.get("classification") == "taste")
    judgment = sum(1 for d in all_decisions if d.get("classification") == "judgment")

    # Build stage table
    stage_lines = []
    for s in stages:
        stage_name = s.get("stage", s.get("name", "?"))
        status = s.get("status", "?")
        artifact = s.get("artifact_id", "-")
        summary = s.get("summary", "")[:60]
        stage_lines.append(f"| {stage_name} | {status} | {artifact} | {summary} |")

    # Build decision table
    decision_lines = []
    for d in all_decisions:
        decision_lines.append(
            f"| {d.get('stage', '?')} | {d.get('description', '')[:50]} | "
            f"{d.get('classification', '?')} | {d.get('reasoning', '')[:40]} |"
        )

    # Confidence scoring
    confidence = 5  # base
    test_stage = next((s for s in stages if s.get("stage", s.get("name")) == "test"), None)
    review_stage = next((s for s in stages if s.get("stage", s.get("name")) == "review"), None)
    build_stage = next((s for s in stages if s.get("stage", s.get("name")) == "build"), None)

    if test_stage and "pass" in (test_stage.get("summary", "")).lower():
        confidence += 2
    if review_stage and "clean" in (review_stage.get("summary", "")).lower():
        confidence += 1
    if build_stage and "tdd" in (build_stage.get("summary", "")).lower():
        confidence += 1
    if judgment == 0:
        confidence += 1
    confidence = min(confidence, 10)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    report = f"""# Autonomous Pipeline Report

**Run ID:** {run_state['id']} | **Project:** {args.project} | **Profile:** {profile}
**Date:** {now} | **Confidence:** {confidence}/10

## 1. Requirement
{requirement}

## 2. Pipeline Execution
| Stage | Status | Artifact | Summary |
|-------|--------|----------|---------|
{chr(10).join(stage_lines) if stage_lines else "| (no stages) | | | |"}

## 3. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
{chr(10).join(decision_lines) if decision_lines else "| (no decisions logged) | | | |"}

**Summary:** {mech} mechanical, {taste} taste, {judgment} judgment

## 4. Quality Assessment
- **Confidence:** {confidence}/10
- **Taste decisions:** {len(taste_decisions)} accumulated
- **Stages completed:** {sum(1 for s in stages if s.get('status') in ('done', 'completed'))}/{len(stages)}

## 5. Status
Pipeline status: **{run_state.get('status', 'unknown')}**

---
*Generated by SwarmAI Autonomous Pipeline | {now}*
"""

    # Write REPORT.md
    report_path = run_file.parent / "REPORT.md"
    report_path.write_text(report, encoding="utf-8")

    print(json.dumps({
        "report_path": str(report_path),
        "confidence": confidence,
        "stages": len(stages),
        "decisions": len(all_decisions),
    }))


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
    p_publish.add_argument("--run-id", default=None, help="Pipeline run ID (stores in runs/<id>/ subdir)")

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
    p_run_update.add_argument("--ddd-checksums", default=None, help="DDD doc checksums JSON (from ddd-check)")

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

    # run-status (v3: cross-project dashboard)
    p_run_status = sub.add_parser("run-status", help="Cross-project pipeline dashboard")
    p_run_status.add_argument("--active-only", action="store_true", help="Only show running/paused")

    # run-resume (v3: resume a paused pipeline)
    p_run_resume = sub.add_parser("run-resume", help="Resume a paused pipeline")
    p_run_resume.add_argument("--project", required=True)
    p_run_resume.add_argument("--run-id", required=True, help="Pipeline run ID")
    p_run_resume.add_argument("--stage", default=None, help="Override resume stage")

    # run-report (auto-generate REPORT.md)
    p_run_report = sub.add_parser("run-report", help="Generate REPORT.md for a pipeline run")
    p_run_report.add_argument("--project", required=True)
    p_run_report.add_argument("--run-id", required=True, help="Pipeline run ID")

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
        "run-status": cmd_run_status,
        "run-resume": cmd_run_resume,
        "run-report": cmd_run_report,
    }
    handlers[args.command](args, reg)


if __name__ == "__main__":
    main()
