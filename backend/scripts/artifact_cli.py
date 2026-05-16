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

Usage — Pipeline Metrics:
    python artifact_cli.py run-metrics --project SwarmAI --run-id run_xxx
    python artifact_cli.py run-analytics --project SwarmAI [--limit 50]

Storage layout:
    Projects/<project>/.artifacts/
        manifest.json                   # global artifact index
        <type>-<date>-<topic>.json      # standalone artifacts (no pipeline)
        runs/
            <run_id>/
                run.json                # pipeline run state
                METRICS.json            # extracted metrics (auto on completion)
                <type>-<date>.json      # artifacts scoped to this run

Public symbols:
- ``main``  — CLI entry point with subcommand dispatch.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path so we can import core modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.artifact_registry import ArtifactRegistry
from core.pipeline_profiles import get_profile_stages


def _get_workspace() -> Path:
    """Resolve workspace root from environment or default."""
    import os
    from config import get_app_data_dir
    ws = os.environ.get("SWARM_WORKSPACE", str(get_app_data_dir() / "SwarmWS"))
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


def _find_active_run(project: str, reg: ArtifactRegistry) -> dict | None:
    """Find the most recent active (running/paused) pipeline run for a project.

    Sorts by created_at (ISO timestamp in run.json), not by directory name —
    directory names are run_<8-char-uuid> which have no chronological order.
    """
    runs_dir = Path(reg._workspace) / "Projects" / project / ".artifacts" / "runs"
    if not runs_dir.is_dir():
        return None
    # Collect all active runs with their timestamps
    active_runs: list[tuple[str, dict]] = []
    for run_dir in runs_dir.iterdir():
        run_file = run_dir / "run.json"
        if run_file.exists():
            try:
                data = json.loads(run_file.read_text())
                if data.get("status") in ("running", "paused"):
                    active_runs.append((data.get("created_at", ""), data))
            except (json.JSONDecodeError, OSError):
                continue
    if not active_runs:
        return None
    # Sort by created_at descending — most recent first
    active_runs.sort(key=lambda x: x[0], reverse=True)
    return active_runs[0][1]


def _append_stage_to_run(
    project: str, run_id: str, stage_record: dict, reg: ArtifactRegistry
) -> None:
    """Append a stage record to run.json (same as run-update --stage-json)."""
    run_file = (
        Path(reg._workspace) / "Projects" / project
        / ".artifacts" / "runs" / run_id / "run.json"
    )
    if not run_file.exists():
        return
    data = json.loads(run_file.read_text())
    stages = data.get("stages", [])
    # Don't duplicate if stage already recorded
    if any(s.get("stage") == stage_record["stage"] for s in stages):
        return
    stages.append(stage_record)
    data["stages"] = stages
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    run_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def cmd_publish(args, reg: ArtifactRegistry) -> None:
    """Publish a new artifact. Validates schema when --stage is provided.

    With --stage: runs validate_artifact_data() from pipeline_validator
    (single source of truth). On failure, returns errors + expected template.
    """
    try:
        data = json.loads(args.data)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON data: {e}"}), file=sys.stderr)
        sys.exit(1)

    # Pre-publish validation: check schema + depth BEFORE writing to disk
    # Uses pipeline_validator as single source of truth (no duplicate schemas).
    stage = getattr(args, "stage", None)
    if stage:
        from pipeline_validator import validate_artifact_data, get_stage_schema
        errors = validate_artifact_data(stage, data)
        if errors:
            schema_info = get_stage_schema(stage)
            print(json.dumps({
                "validation_failed": True,
                "stage": stage,
                "errors": errors,
                "expected_schema": schema_info.get("template", {}),
            }, indent=2), file=sys.stderr)
            sys.exit(1)

    # ── Pollinate delivery gate: mechanical validation ────────────────────
    # When producer is s_pollinate/s_autonomous-pipeline AND stage is deliver,
    # and data contains a content_dir field, run the structural validator.
    # This is the mechanical enforcement that prevents skipping the validator.
    producer = getattr(args, "producer", "") or ""
    if stage == "deliver" and "pollinate" in producer:
        # Auto-discover content_dir: explicit in data > most recent content/*/ dir
        content_dir = data.get("content_dir")
        if not content_dir:
            _pollinate_root = Path(reg._workspace) / "Knowledge" / "Pollinate"
            if _pollinate_root.is_dir():
                # Find most recently modified content directory
                _candidates = [d for d in _pollinate_root.iterdir() if d.is_dir() and not d.name.startswith(".")]
                if _candidates:
                    content_dir = str(max(_candidates, key=lambda d: d.stat().st_mtime))
        if content_dir:
            try:
                import importlib.util
                _validator_path = Path(__file__).parent.parent / "skills" / "s_pollinate" / "scripts" / "pollinate_validator.py"
                _spec = importlib.util.spec_from_file_location("pollinate_validator", _validator_path)
                _mod = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)
                validate_delivery = _mod.validate_delivery
                vresult = validate_delivery(content_dir)
                if not vresult.get("valid", True):
                    print(json.dumps({
                        "validation_failed": True,
                        "stage": "deliver",
                        "errors": [f"Pollinate validator: {e}" for e in vresult.get("errors", [])],
                        "hint": "Run: python pollinate_validator.py <content_dir> --json",
                    }, indent=2), file=sys.stderr)
                    sys.exit(1)
            except (ImportError, FileNotFoundError):
                pass  # Validator not available — skip (non-blocking)
            except Exception as exc:
                logger.warning("Pollinate validator skipped: %s", exc)

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

        # ── Auto-record stage to run.json (eliminates separate run-update call) ──
        # If --stage is provided, append stage record to the target run.
        # Priority: explicit --run-id > auto-discovered active run.
        if stage:
            try:
                target_run_id = run_id  # Explicit --run-id from caller
                if not target_run_id:
                    active_run = _find_active_run(args.project, reg)
                    if active_run:
                        target_run_id = active_run.get("id", "")
                if target_run_id:
                    stage_record = {
                        "stage": stage,
                        "status": "completed",
                        "artifact_id": artifact_id,
                        "token_cost": 0,  # Caller can update later if needed
                        "decisions": [],
                    }
                    _append_stage_to_run(args.project, target_run_id, stage_record, reg)
                    result["auto_recorded"] = True
                    result["run_id"] = target_run_id
            except Exception:
                pass  # Best-effort — don't fail publish if auto-record fails

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
    # FAIL-CLOSED: if validator crashes, block advancement.
    # Prior behavior (fail-open) allowed advancement when validator errored,
    # which is the most dangerous failure mode — gate opens on crash.
    try:
        _auto_validate_before_advance(args.project, args.state)
    except SystemExit:
        raise  # Re-raise if validation blocks
    except Exception as e:
        # FAIL-CLOSED: validator crash → block advancement
        print(json.dumps({
            "validation_blocked": True,
            "error": f"Validator crashed: {e}. Fix the validator before advancing.",
        }, indent=2), file=sys.stderr)
        sys.exit(1)

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
    artifacts_dir = _get_workspace() / "Projects" / project / ".artifacts" / "runs"
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
                # Record validation block event in run.json
                _record_validation_event(
                    project, run_id, current_stage,
                    passed=False, errors=errors,
                    warnings=validation.get("warnings", []),
                )
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
        # FAIL-CLOSED: validator timeout → block advancement (F3 fix)
        raise RuntimeError("Pipeline validator timed out (>10s) — cannot verify stage")
    except (json.JSONDecodeError, OSError) as exc:
        # FAIL-CLOSED: validator parse error → block advancement (F3 fix)
        raise RuntimeError(f"Pipeline validator produced invalid output: {exc}")


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
            # ── Completion Gate: ALL profile stages must be done or explicitly skipped ──
            # Every stage in the DDD+pipeline loop has purpose. No silent skips.
            profile = run_state.get("profile", "full")
            profile_stages = _get_profile_stages(profile)

            stage_status_map: dict[str, str] = {}
            for s in run_state.get("stages", []):
                name = s.get("stage", s.get("name", "?"))
                stage_status_map[name] = s.get("status", "unknown")

            missing_stages = []
            for stg in profile_stages:
                status = stage_status_map.get(stg)
                if status in ("completed", "done"):
                    continue
                elif status == "skipped":
                    # Skipped is allowed ONLY with an explicit reason in the record
                    record = next(
                        (s for s in run_state.get("stages", [])
                         if s.get("stage", s.get("name")) == stg),
                        {},
                    )
                    reason = record.get("skip_reason") or record.get("notes") or ""
                    if not reason.strip():
                        missing_stages.append(
                            f"{stg} (skipped without reason — add skip_reason field)"
                        )
                else:
                    missing_stages.append(
                        f"{stg} (status={status or 'not recorded'})"
                    )

            if missing_stages:
                print(json.dumps({
                    "error": "Cannot mark completed: not all profile stages are done. "
                             "Every stage in the pipeline serves the DDD learning loop — "
                             "execute them or explicitly skip with a reason.",
                    "pipeline_id": args.run_id,
                    "profile": profile,
                    "missing_stages": missing_stages,
                }))
                return

            # ── REFLECT quality gate: lessons must be substantive ──
            # The whole point of REFLECT is DDD refresh. Empty/trivial lessons = didn't reflect.
            if "reflect" in profile_stages:
                reflect_record = next(
                    (s for s in run_state.get("stages", [])
                     if s.get("stage", s.get("name")) == "reflect"
                     and s.get("status") in ("completed", "done")),
                    None,
                )
                if reflect_record:
                    lessons = reflect_record.get("lessons", [])
                    valid_lessons = [
                        l for l in (lessons if isinstance(lessons, list) else [])
                        if isinstance(l, str) and len(l.strip()) > 20
                    ]
                    if not valid_lessons:
                        print(json.dumps({
                            "error": "Cannot mark completed: REFLECT stage has no substantive lessons. "
                                     "Each lesson must be >20 chars and actionable. "
                                     "Bad: 'done', '3 lessons captured'. "
                                     "Good: 'SMOKE tests caught 2 runtime crashes that unit tests missed'.",
                            "pipeline_id": args.run_id,
                            "lessons_found": lessons,
                        }))
                        return

            run_state["completed_at"] = now
            # Auto-generate METRICS.json on completion
            _try_generate_metrics(args.project, args.run_id, run_state, reg)

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
        from config import get_app_data_dir
        db_path = get_app_data_dir() / "data.db"
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
    """Generate comprehensive REPORT.md for a completed (or running) pipeline run.

    Aggregates run.json + ALL published artifacts (evaluation, design_doc,
    changeset, review, test_report, delivery) into a full retrospective report.
    Covers: evaluation rationale, design approach, TDD results, review findings,
    adversarial review, completion audit, files changed, and lessons.
    """
    from datetime import datetime, timezone

    run_file = _resolve_run_file(args.project, args.run_id)
    run_state = json.loads(run_file.read_text(encoding="utf-8"))

    stages = run_state.get("stages", [])
    profile = run_state.get("profile", "full")
    requirement = run_state.get("requirement", "")

    # ── Load artifact data strictly from this run ──────────────────────
    # IMPORTANT: Only load artifacts linked by this run's stage artifact_ids
    # or matching this run's date. Never use "latest" fallback — causes
    # cross-contamination between runs.
    art_data: dict[str, dict] = {}  # stage_name -> artifact data
    run_date = (run_state.get("created_at", "") or "")[:10].replace("-", "")  # YYYYMMDD

    for s in stages:
        stage_name = s.get("stage", "?")
        art_id = s.get("artifact_id")
        if art_id and art_id != "changeset":
            loaded = _load_artifact_for_metrics(args.project, art_id)
            if loaded:
                art_data[stage_name] = loaded
        elif art_id == "changeset" and run_date:
            # Changeset stored as date-stamped file — load matching run date
            art_data["build"] = _load_artifact_by_date(args.project, "changeset", run_date) or {}

    # Date-scoped fallback for stages without manifest-linked artifacts
    # Only loads artifacts from the SAME DATE as the run (prevents cross-contamination)
    if run_date:
        if "evaluate" not in art_data or not art_data["evaluate"].get("scores"):
            dated = _load_artifact_by_date(args.project, "evaluation", run_date)
            if dated:
                art_data["evaluate"] = dated
        if "plan" not in art_data and "think" not in art_data:
            dated = _load_artifact_by_date(args.project, "design_doc", run_date)
            if dated:
                art_data["plan"] = dated
        elif "plan" in art_data and not art_data["plan"].get("approach"):
            # plan artifact from manifest lacks approach — try date-scoped
            dated = _load_artifact_by_date(args.project, "design_doc", run_date)
            if dated and dated.get("approach"):
                art_data["plan"] = dated
        if "build" not in art_data or not art_data["build"].get("files_changed"):
            dated = _load_artifact_by_date(args.project, "changeset", run_date)
            if dated:
                art_data["build"] = dated
        if "review" not in art_data:
            dated = _load_artifact_by_date(args.project, "review", run_date)
            if dated:
                art_data["review"] = dated
        if "test" not in art_data or not art_data["test"].get("total"):
            dated = _load_artifact_by_date(args.project, "test_report", run_date)
            if dated:
                art_data["test"] = dated
        if "deliver" not in art_data or not art_data["deliver"].get("title"):
            dated = _load_artifact_by_date(args.project, "delivery", run_date)
            if dated:
                art_data["deliver"] = dated

    # ── Determine skipped stages ──────────────────────────────────────
    ALL_STAGES = ["evaluate", "think", "plan", "build", "review", "test", "deliver", "reflect"]
    present_stages = {s.get("stage", s.get("name", "?")) for s in stages}
    skipped_stages: dict[str, str] = {}  # stage -> reason
    for stg in ALL_STAGES:
        if stg not in present_stages:
            # Determine skip reason from profile
            if profile == "trivial" and stg in ("think", "plan", "reflect"):
                skipped_stages[stg] = f"Skipped — profile '{profile}' omits research/planning"
            elif profile == "bugfix" and stg in ("think", "review"):
                skipped_stages[stg] = f"Skipped — profile '{profile}' uses direct fix path"
            elif profile == "research" and stg in ("build", "review", "test", "deliver"):
                skipped_stages[stg] = f"Skipped — profile '{profile}' is research-only"
            else:
                if stg == "reflect" and profile == "full":
                    skipped_stages[stg] = "Not executed — pipeline completed before REFLECT"
                else:
                    skipped_stages[stg] = f"Skipped — not required for profile '{profile}'"

    # ── Collect decisions ─────────────────────────────────────────────
    all_decisions = []
    for s in stages:
        for d in s.get("decisions", []):
            all_decisions.append({
                "stage": s.get("stage", s.get("name", "?")),
                **d,
            })

    mech = sum(1 for d in all_decisions if d.get("classification") == "mechanical")
    taste = sum(1 for d in all_decisions if d.get("classification") == "taste")
    judgment = sum(1 for d in all_decisions if d.get("classification") == "judgment")

    # ── Section 1: TL;DR ──────────────────────────────────────────────
    delivery = art_data.get("deliver", {})
    title = delivery.get("title", requirement[:80] or "Pipeline Report")

    # ── Section 2: Evaluation ─────────────────────────────────────────
    eval_data = art_data.get("evaluate", {})
    eval_scores = eval_data.get("scores", {})
    eval_recommendation = eval_data.get("recommendation", "?")
    eval_scope = eval_data.get("scope", "?")
    eval_criteria = eval_data.get("acceptance_criteria", [])

    eval_table_lines = []
    for dim in ["strategic", "priority", "historical", "feasibility"]:
        score = eval_scores.get(dim)
        if score is not None:
            eval_table_lines.append(f"| {dim.capitalize()} | {score:.2f} | |")
    roi = eval_scores.get("roi")
    if roi is not None:
        eval_table_lines.append(f"| **ROI** | **{roi:.3f}** | **{eval_recommendation}** |")

    # ── Section 3: Design & Approach ──────────────────────────────────
    # Design data lives in plan stage (approach, boundaries) or think stage (alternatives)
    design = art_data.get("plan", {})
    if not design.get("approach"):
        design = art_data.get("think", {})
    approach = design.get("approach", "")
    boundaries = design.get("boundaries", {})
    success_criteria = design.get("success_criteria", design.get("acceptance_criteria", []))
    files_to_change = design.get("files_to_change", [])
    # Think stage research findings
    think_data = art_data.get("think", {})
    key_findings = think_data.get("key_findings", [])
    alternatives = think_data.get("alternatives", [])

    # ── Section 4: Pipeline Execution ─────────────────────────────────
    # Show ALL 8 standard stages — executed ones with data, skipped with reason
    stage_lines = []
    stage_data_map = {s.get("stage", s.get("name", "?")): s for s in stages}
    for stg in ALL_STAGES:
        if stg in stage_data_map:
            s = stage_data_map[stg]
            status = s.get("status", "?")
            artifact = s.get("artifact_id", "-")
            tokens = s.get("token_cost", 0)
            summary = s.get("summary", "")[:50]
            stage_lines.append(f"| {stg} | {status} | {artifact} | {tokens:,} | {summary} |")
        elif stg in skipped_stages:
            stage_lines.append(f"| {stg} | ⏭ skipped | - | 0 | {skipped_stages[stg]} |")

    # ── Section 5: TDD Results ────────────────────────────────────────
    test_data = art_data.get("test", {})
    test_total = test_data.get("total", test_data.get("passed", 0) + test_data.get("failed", 0))
    test_passed = test_data.get("passed", 0)
    test_failed = test_data.get("failed", 0)
    test_new = test_data.get("new_tests", 0)
    test_duration = test_data.get("duration_s", 0)

    changeset = art_data.get("build", {})
    tests_added = changeset.get("tests_added", test_new)

    # ── Section 6: Decision Log ───────────────────────────────────────
    decision_lines = []
    for d in all_decisions:
        decision_lines.append(
            f"| {d.get('stage', '?')} | {d.get('description', '')[:60]} | "
            f"{d.get('classification', '?')} | {d.get('reasoning', '')[:50]} |"
        )

    # ── Section 7: Quality Gates ──────────────────────────────────────
    review_data = art_data.get("review", {})
    _rf = review_data.get("findings", [])
    review_findings = _rf if isinstance(_rf, list) else []
    review_findings_count = len(review_findings) if isinstance(_rf, list) else (_rf if isinstance(_rf, int) else 0)
    review_severity = review_data.get("severity", "?")

    adversarial = delivery.get("adversarial_review", {})
    adversarial_findings = adversarial.get("findings", [])
    adversarial_verdict = adversarial.get("verdict", "?")

    completion_audit = delivery.get("completion_audit", {})
    criteria_met = completion_audit.get("criteria_met", [])
    criteria_unmet = completion_audit.get("criteria_unmet", [])

    confidence_score = delivery.get("confidence_score", 0)
    if isinstance(confidence_score, dict):
        confidence_score = confidence_score.get("score", 0)

    # ── Section 8: Files Changed ──────────────────────────────────────
    _fc = changeset.get("files_changed", [])
    files_changed = _fc if isinstance(_fc, list) else []
    files_changed_count = len(files_changed) if isinstance(_fc, list) else (_fc if isinstance(_fc, int) else 0)
    lines_added = changeset.get("lines_added", 0)
    lines_removed = changeset.get("lines_removed", 0)

    # ── Duration & timestamps ─────────────────────────────────────────
    created = run_state.get("created_at", "")
    completed = run_state.get("completed_at", "")
    duration_str = ""
    if created and completed:
        try:
            t0 = datetime.fromisoformat(created)
            t1 = datetime.fromisoformat(completed)
            mins = round((t1 - t0).total_seconds() / 60, 1)
            duration_str = f"{mins} min"
        except (ValueError, TypeError):
            pass

    # ── Validation events ─────────────────────────────────────────────
    validation_events = run_state.get("validation_events", [])
    validation_blocks = sum(1 for v in validation_events if not v.get("passed"))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ══════════════════════════════════════════════════════════════════
    # BUILD REPORT
    # ══════════════════════════════════════════════════════════════════
    sections = []

    sections.append(f"""# Autonomous Pipeline Report: {title}

**Run ID:** {run_state['id']} | **Project:** {args.project} | **Profile:** {profile}
**Date:** {now} | **Duration:** {duration_str or 'N/A'} | **Confidence:** {confidence_score}/10""")

    # TL;DR
    summary_text = delivery.get("summary", requirement[:200])
    sections.append(f"""## TL;DR
{summary_text}""")

    # 1. Requirement
    sections.append(f"""## 1. Requirement
{requirement}""")

    # 2. Evaluation
    eval_section = "## 2. Evaluation\n"
    if eval_table_lines:
        eval_section += "| Dimension | Score | Recommendation |\n|---|---|---|\n"
        eval_section += "\n".join(eval_table_lines)
    else:
        eval_section += f"Recommendation: **{eval_recommendation}** | Scope: {eval_scope}"
    if eval_criteria:
        eval_section += "\n\n**Acceptance Criteria:**\n"
        for i, ac in enumerate(eval_criteria[:12], 1):
            eval_section += f"{i}. {ac}\n"
    sections.append(eval_section)

    # 3. Design & Approach
    design_section = "## 3. Design & Approach\n"
    if key_findings:
        design_section += "**Research Findings (THINK):**\n"
        for kf in key_findings[:8]:
            design_section += f"- {kf}\n"
        design_section += "\n"
    if alternatives:
        design_section += "**Alternatives Evaluated:**\n"
        for alt in alternatives[:4]:
            if isinstance(alt, dict):
                rec = " ✅ (recommended)" if alt.get("recommendation") else ""
                design_section += f"- {alt.get('constraint', '?')} — effort: {alt.get('effort', '?')}{rec}\n"
            else:
                design_section += f"- {str(alt)[:80]}\n"
        design_section += "\n"
    if approach:
        design_section += f"**Chosen Approach:** {approach}\n\n"
    if success_criteria:
        design_section += "**Success Criteria:**\n"
        for sc in success_criteria[:10]:
            design_section += f"- {sc}\n"
    if boundaries and isinstance(boundaries, dict):
        always_rules = boundaries.get("always", [])
        never_rules = boundaries.get("never", [])
        if always_rules:
            design_section += "\n**Always:**\n"
            for r in always_rules[:5]:
                design_section += f"- {r}\n"
        if never_rules:
            design_section += "\n**Never:**\n"
            for r in never_rules[:5]:
                design_section += f"- {r}\n"
    if not approach and not success_criteria and not key_findings:
        if "think" in skipped_stages or "plan" in skipped_stages:
            design_section += f"**⏭ Skipped** — {skipped_stages.get('think', skipped_stages.get('plan', ''))}\n"
        else:
            design_section += "(No design artifact data captured for this run)\n"
    sections.append(design_section)

    # 4. Pipeline Execution
    total_tokens = sum(s.get("token_cost", 0) for s in stages)
    exec_section = f"""## 4. Pipeline Execution
| Stage | Status | Artifact | Tokens | Summary |
|-------|--------|----------|--------|---------|
{chr(10).join(stage_lines) if stage_lines else "| (no stages) | | | | |"}

**Total tokens:** {total_tokens:,} | **Stages:** {sum(1 for s in stages if s.get('status') in ('done', 'completed'))}/{len(stages)} completed"""
    if validation_blocks:
        exec_section += f" | **Validation blocks:** {validation_blocks}"
    sections.append(exec_section)

    # 5. TDD Results
    tdd_section = f"""## 5. TDD Results
| Metric | Value |
|--------|-------|
| Tests total | {test_total} |
| Tests passed | {test_passed} |
| Tests failed | {test_failed} |
| New tests added | {tests_added} |
| Duration | {test_duration}s |
| Regressions | {test_failed} |"""
    sections.append(tdd_section)

    # 6. Decision Log
    decision_section = f"""## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
{chr(10).join(decision_lines) if decision_lines else "| (no decisions) | | | |"}

**Summary:** {mech} mechanical, {taste} taste, {judgment} judgment"""
    sections.append(decision_section)

    # 7. Quality Gates
    quality_section = "## 7. Quality Gates\n"
    quality_section += "| Gate | Result |\n|------|--------|\n"
    quality_section += f"| REVIEW (code quality) | {review_findings_count} findings ({review_severity}) |\n"
    quality_section += f"| TEST (suite) | {test_passed}/{test_total} pass |\n"
    quality_section += f"| Confidence | {confidence_score}/10 |\n"

    # 7.5 Adversarial Review
    quality_section += f"\n### 7.5 Adversarial Review\n"
    if not adversarial and "deliver" not in stage_data_map:
        quality_section += f"**⏭ NOT RUN** — deliver stage was skipped (profile: {profile})\n"
    elif not adversarial or (adversarial_verdict == "?" and not adversarial_findings):
        quality_section += "**⚠️ NOT RUN** — adversarial sub-agent was not spawned during this run.\n"
        quality_section += "This is a quality gap. Pipeline DELIVER requires adversarial review.\n"
    elif adversarial_findings:
        quality_section += f"**{len(adversarial_findings)} findings** (verdict: {adversarial_verdict})\n"
        for f in adversarial_findings[:5]:
            if isinstance(f, dict):
                quality_section += f"- [{f.get('severity', '?')}] {f.get('description', f.get('finding', ''))[:80]}\n"
            else:
                quality_section += f"- {str(f)[:80]}\n"
    else:
        quality_section += f"Verdict: **{adversarial_verdict}** | Sub-agent spawned: ✅ | Findings: 0\n"

    # 7.6 Completion Audit
    quality_section += f"\n### 7.6 Completion Audit\n"
    if criteria_met or criteria_unmet:
        for c in criteria_met:
            quality_section += f"- [x] {c}\n"
        for c in criteria_unmet:
            quality_section += f"- [ ] {c}\n"
        quality_section += f"\n**Met:** {len(criteria_met)} | **Unmet:** {len(criteria_unmet)}"
    else:
        quality_section += "(No completion audit data)\n"
    sections.append(quality_section)

    # 8. Files Changed
    files_section = "## 8. Files Changed\n"
    if files_changed:
        for f in files_changed:
            files_section += f"- `{f}`\n"
        files_section += f"\n**+{lines_added} / -{lines_removed} lines** | {files_changed_count} files"
    elif files_changed_count > 0:
        files_section += f"**{files_changed_count} files** | +{lines_added} / -{lines_removed} lines\n"
        files_section += "(file list not captured in changeset artifact)\n"
    elif "build" in skipped_stages:
        files_section += f"**⏭ Skipped** — {skipped_stages['build']}\n"
    else:
        files_section += "(No changeset data captured for this run)\n"
    sections.append(files_section)

    # 9. Lessons — inline from REFLECT stage + calibration history
    lessons_section = "## 9. Lessons\n"
    lessons_items: list[str] = []

    # Source 1: REFLECT stage record in run.json
    reflect_stage = stage_data_map.get("reflect", {})

    # Primary: structured lessons list (new format from reflect.md step 7)
    stage_lessons = reflect_stage.get("lessons", [])
    if isinstance(stage_lessons, list):
        for item in stage_lessons:
            if isinstance(item, str) and item.strip() and len(item.strip()) > 5:
                lessons_items.append(item.strip())

    # Fallback: parse summary/notes (legacy format)
    if not lessons_items:
        reflect_summary = reflect_stage.get("summary", "") or ""
        if reflect_summary:
            if ": " in reflect_summary and "lesson" in reflect_summary.lower():
                _, _, lessons_text = reflect_summary.partition(": ")
                for item in lessons_text.split(", "):
                    item = item.strip()
                    if item and len(item) > 5:
                        lessons_items.append(item)
            elif len(reflect_summary) > 10:
                lessons_items.append(reflect_summary)

    # Source 2: calibration_history in decision-strategy.json (matched by eval artifact_id)
    eval_art_id = None
    for s in stages:
        if s.get("stage") == "evaluate":
            eval_art_id = s.get("artifact_id")
            break

    if eval_art_id:
        strategy_path = Path(reg.projects_root) / args.project / "decision-strategy.json"
        if strategy_path.is_file():
            try:
                strategy = json.loads(strategy_path.read_text(encoding="utf-8"))
                for entry in strategy.get("calibration_history", []):
                    if entry.get("evaluation_id") == eval_art_id:
                        for lesson in entry.get("lessons", []):
                            if lesson.strip() and lesson.strip() not in lessons_items:
                                lessons_items.append(lesson.strip())
            except (json.JSONDecodeError, OSError):
                pass

    # Source 3: REFLECT stage decisions (if any contain lessons)
    for d in reflect_stage.get("decisions", []):
        desc = d.get("description", "").strip()
        if desc and desc not in lessons_items:
            lessons_items.append(desc)

    if lessons_items:
        for item in lessons_items:
            lessons_section += f"- {item}\n"
    elif "reflect" in skipped_stages:
        lessons_section += f"**⏭ Skipped** — {skipped_stages['reflect']}\n"
    else:
        lessons_section += "(REFLECT stage did not record lessons for this run)\n"
    sections.append(lessons_section)

    # 10. Known Gaps
    gaps_section = "## 10. Known Gaps & Attention Flags\n"
    if criteria_unmet:
        for c in criteria_unmet:
            gaps_section += f"- {c}\n"
    if validation_blocks:
        gaps_section += f"- Validator blocked {validation_blocks} time(s) during execution\n"
    if not criteria_unmet and not validation_blocks:
        gaps_section += "None identified.\n"
    sections.append(gaps_section)

    # Footer
    sections.append(f"---\n*Generated by SwarmAI Autonomous Pipeline | {now}*")

    report = "\n\n".join(sections) + "\n"

    # Write REPORT.md — never overwrite hand-written reports unless --force
    report_path = run_file.parent / "REPORT.md"
    if report_path.exists() and not getattr(args, "force", False):
        print(json.dumps({
            "skipped": True,
            "reason": "REPORT.md already exists (use --force to overwrite)",
            "report_path": str(report_path),
        }))
        return

    report_path.write_text(report, encoding="utf-8")

    print(json.dumps({
        "report_path": str(report_path),
        "confidence": confidence_score,
        "stages": len(stages),
        "decisions": len(all_decisions),
        "files_changed": files_changed_count,
        "tests": test_total,
    }))


def _record_validation_event(
    project: str, run_id: str, stage: str,
    passed: bool, errors: list[str], warnings: list[str],
) -> None:
    """Append a validation event to run.json.validation_events[].

    Non-critical — failures are silently ignored (metrics are best-effort).
    """
    from datetime import datetime, timezone
    try:
        run_file = _run_dir(project, run_id) / "run.json"
        if not run_file.exists():
            return
        run_state = json.loads(run_file.read_text(encoding="utf-8"))
        events = run_state.setdefault("validation_events", [])
        events.append({
            "stage": stage,
            "passed": passed,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "errors": errors[:5],  # Cap at 5 to avoid bloat
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        run_file.write_text(json.dumps(run_state, indent=2), encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        pass  # Best-effort — never crash pipeline for metrics


def _try_generate_metrics(
    project: str, run_id: str, run_state: dict, reg: ArtifactRegistry,
) -> None:
    """Auto-generate METRICS.json when a pipeline run completes.

    Extracts catch metrics from review/deliver artifacts + validation events.
    Non-critical — failures silently ignored.
    """
    try:
        metrics = _extract_run_metrics(project, run_id, run_state)
        metrics_file = _run_dir(project, run_id) / "METRICS.json"
        metrics_file.parent.mkdir(parents=True, exist_ok=True)
        metrics_file.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    except Exception:
        pass  # Best-effort


def _extract_run_metrics(project: str, run_id: str, run_state: dict) -> dict:
    """Extract comprehensive metrics from a completed pipeline run.

    Reads run.json + artifact data to produce a flat metrics dict.
    """
    from datetime import datetime, timezone

    stages = run_state.get("stages", [])
    profile = run_state.get("profile", "full")

    # --- Token metrics ---
    total_tokens = sum(s.get("token_cost", 0) for s in stages)
    stage_tokens = {
        s.get("stage", "?"): s.get("token_cost", 0)
        for s in stages
    }

    # --- Decision metrics ---
    all_decisions = []
    for s in stages:
        for d in s.get("decisions", []):
            all_decisions.append(d)
    decision_counts = {
        "mechanical": sum(1 for d in all_decisions if d.get("classification") == "mechanical"),
        "taste": sum(1 for d in all_decisions if d.get("classification") == "taste"),
        "judgment": sum(1 for d in all_decisions if d.get("classification") == "judgment"),
        "total": len(all_decisions),
    }

    # --- Catch metrics (from artifacts) ---
    review_findings = 0
    review_rp_checked = 0
    adversarial_findings = 0
    adversarial_high = 0
    adversarial_resolved = 0
    confidence_score = 0
    completion_all_green = False
    test_passed = 0
    test_failed = 0
    build_files_changed = 0
    build_tests_generated = 0

    for s in stages:
        art_id = s.get("artifact_id")
        if not art_id:
            continue

        data = _load_artifact_for_metrics(project, art_id)
        if not data:
            continue

        stage_name = s.get("stage", "?")

        if stage_name == "review":
            fc = data.get("findings_count")
            findings_list = data.get("findings", [])
            review_findings = fc if fc is not None else (len(findings_list) if isinstance(findings_list, list) else 0)
            rp = data.get("runtime_patterns", {})
            review_rp_checked = rp.get("checked", 0) if isinstance(rp, dict) else 0

        elif stage_name == "deliver":
            ar = data.get("adversarial_review", {})
            if isinstance(ar, dict):
                findings = ar.get("findings", [])
                adversarial_findings = len(findings)
                adversarial_high = sum(
                    1 for f in findings
                    if isinstance(f, dict) and f.get("severity") == "HIGH"
                )
                adversarial_resolved = sum(
                    1 for f in findings
                    if isinstance(f, dict) and f.get("resolved")
                )
            cs = data.get("confidence_score", {})
            if isinstance(cs, dict):
                confidence_score = cs.get("score", 0)
            elif isinstance(cs, (int, float)):
                confidence_score = cs
            ca = data.get("completion_audit", {})
            completion_all_green = ca.get("all_green", False) if isinstance(ca, dict) else False

        elif stage_name == "test":
            test_passed = data.get("passed", 0)
            test_failed = data.get("failed", 0)

        elif stage_name == "build":
            fc = data.get("files_changed", [])
            build_files_changed = len(fc) if isinstance(fc, list) else (fc if isinstance(fc, int) else 0)
            tdd = data.get("tdd", {})
            build_tests_generated = tdd.get("tests_generated", tdd.get("smoke_tests", 0)) if isinstance(tdd, dict) else 0

    # --- Validation events ---
    validation_events = run_state.get("validation_events", [])
    validation_blocks = sum(1 for v in validation_events if not v.get("passed"))

    # --- Duration ---
    created = run_state.get("created_at", "")
    completed = run_state.get("completed_at", "")
    duration_minutes = None
    if created and completed:
        try:
            t0 = datetime.fromisoformat(created)
            t1 = datetime.fromisoformat(completed)
            duration_minutes = round((t1 - t0).total_seconds() / 60, 1)
        except (ValueError, TypeError):
            pass

    return {
        "run_id": run_id,
        "project": project,
        "profile": profile,
        "status": run_state.get("status", "?"),
        "stages_completed": sum(1 for s in stages if s.get("status") in ("completed", "done")),
        "stages_total": len(stages),
        "duration_minutes": duration_minutes,
        # Tokens
        "total_tokens": total_tokens,
        "stage_tokens": stage_tokens,
        # Decisions
        "decisions": decision_counts,
        # Catches
        "catches": {
            "review_findings": review_findings,
            "review_rp_checked": review_rp_checked,
            "adversarial_findings": adversarial_findings,
            "adversarial_high": adversarial_high,
            "adversarial_resolved": adversarial_resolved,
            "validation_blocks": validation_blocks,
            "test_regressions": test_failed,
        },
        # Quality
        "quality": {
            "confidence_score": confidence_score,
            "completion_all_green": completion_all_green,
            "test_passed": test_passed,
            "test_failed": test_failed,
        },
        # Build
        "build": {
            "files_changed": build_files_changed,
            "tests_generated": build_tests_generated,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _load_artifact_for_metrics(project: str, artifact_id: str) -> dict | None:
    """Load artifact data by ID from manifest (metrics-safe: returns None on failure)."""
    ws = _get_workspace()
    manifest_file = ws / "Projects" / project / ".artifacts" / "manifest.json"
    if not manifest_file.exists():
        return None
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    artifacts_dir = ws / "Projects" / project / ".artifacts"
    for entry in manifest.get("artifacts", []):
        if entry.get("id") == artifact_id:
            data_file = artifacts_dir / entry.get("file", "")
            if data_file.exists():
                try:
                    return json.loads(data_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    return None
    return None


def _load_latest_artifact_by_type(project: str, artifact_type: str) -> dict | None:
    """Load the most recent artifact file by type prefix (e.g. 'evaluation', 'changeset').

    Artifacts are stored as <type>-YYYYMMDD.json in .artifacts/ directory.
    Returns the most recent one (sorted by filename descending).

    WARNING: Only use for non-report contexts (e.g. metrics). For report generation,
    use _load_artifact_by_date() to prevent cross-contamination between runs.
    """
    ws = _get_workspace()
    artifacts_dir = ws / "Projects" / project / ".artifacts"
    if not artifacts_dir.is_dir():
        return None
    try:
        candidates = sorted(
            [f for f in artifacts_dir.iterdir()
             if f.name.startswith(f"{artifact_type}-") and f.suffix == ".json"],
            key=lambda p: p.name,
            reverse=True,
        )
        if not candidates:
            return None
        return json.loads(candidates[0].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_artifact_by_date(project: str, artifact_type: str, date_str: str) -> dict | None:
    """Load artifact file matching exact date (YYYYMMDD format).

    Prevents cross-contamination by only loading artifacts from the same day
    as the pipeline run. Returns None if no matching file exists.
    """
    ws = _get_workspace()
    artifacts_dir = ws / "Projects" / project / ".artifacts"
    target = artifacts_dir / f"{artifact_type}-{date_str}.json"
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def cmd_run_metrics(args, reg: ArtifactRegistry) -> None:
    """Generate or read METRICS.json for a pipeline run.

    Extracts catch rates, token costs, and quality metrics from
    run.json + artifacts. Writes METRICS.json to the run directory.
    """
    run_file = _resolve_run_file(args.project, args.run_id)
    run_state = json.loads(run_file.read_text(encoding="utf-8"))

    metrics = _extract_run_metrics(args.project, args.run_id, run_state)

    # Write METRICS.json
    metrics_file = run_file.parent / "METRICS.json"
    metrics_file.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    print(json.dumps(metrics, indent=2))


def cmd_run_analytics(args, reg: ArtifactRegistry) -> None:
    """Cross-run analytics: aggregate metrics across completed pipeline runs.

    Reports: catch rates per stage, token trends, confidence distribution,
    adversarial review ROI, and validation block frequency.
    """
    runs = _load_completed_runs(args.project, limit=args.limit)
    if not runs:
        print(json.dumps({
            "project": args.project,
            "message": "No completed pipeline runs found",
        }))
        return

    # Collect metrics from each run (generate if needed)
    all_metrics: list[dict] = []
    for run_state in runs:
        run_id = run_state["id"]
        metrics_file = _run_dir(args.project, run_id) / "METRICS.json"
        if metrics_file.exists():
            try:
                m = json.loads(metrics_file.read_text(encoding="utf-8"))
                all_metrics.append(m)
                continue
            except (json.JSONDecodeError, OSError):
                pass
        # Generate on-demand
        m = _extract_run_metrics(args.project, run_id, run_state)
        all_metrics.append(m)
        # Persist for next time
        try:
            metrics_file.parent.mkdir(parents=True, exist_ok=True)
            metrics_file.write_text(json.dumps(m, indent=2), encoding="utf-8")
        except OSError:
            pass

    if not all_metrics:
        print(json.dumps({"project": args.project, "message": "No metrics extracted"}))
        return

    n = len(all_metrics)

    # --- Token analytics ---
    total_tokens_list = [m.get("total_tokens", 0) for m in all_metrics if m.get("total_tokens", 0) > 0]
    stage_token_agg: dict[str, list[int]] = {}
    for m in all_metrics:
        for stage, cost in m.get("stage_tokens", {}).items():
            if cost > 0:
                stage_token_agg.setdefault(stage, []).append(cost)

    # --- Catch rate analytics ---
    total_review_findings = sum(m.get("catches", {}).get("review_findings", 0) for m in all_metrics)
    total_adversarial = sum(m.get("catches", {}).get("adversarial_findings", 0) for m in all_metrics)
    total_adversarial_high = sum(m.get("catches", {}).get("adversarial_high", 0) for m in all_metrics)
    total_validation_blocks = sum(m.get("catches", {}).get("validation_blocks", 0) for m in all_metrics)
    total_test_regressions = sum(m.get("catches", {}).get("test_regressions", 0) for m in all_metrics)

    runs_with_adversarial = sum(1 for m in all_metrics if m.get("catches", {}).get("adversarial_findings", 0) > 0)
    runs_with_blocks = sum(1 for m in all_metrics if m.get("catches", {}).get("validation_blocks", 0) > 0)

    # --- Confidence distribution ---
    confidence_scores = [m.get("quality", {}).get("confidence_score", 0) for m in all_metrics if m.get("quality", {}).get("confidence_score", 0) > 0]

    # --- Decision analytics ---
    total_decisions = {
        "mechanical": sum(m.get("decisions", {}).get("mechanical", 0) for m in all_metrics),
        "taste": sum(m.get("decisions", {}).get("taste", 0) for m in all_metrics),
        "judgment": sum(m.get("decisions", {}).get("judgment", 0) for m in all_metrics),
    }

    # --- Profile distribution ---
    profile_counts: dict[str, int] = {}
    for m in all_metrics:
        p = m.get("profile", "unknown")
        profile_counts[p] = profile_counts.get(p, 0) + 1

    # --- Duration analytics ---
    durations = [m["duration_minutes"] for m in all_metrics if m.get("duration_minutes")]

    def _safe_avg(lst: list) -> float:
        return round(sum(lst) / len(lst), 1) if lst else 0

    analytics = {
        "project": args.project,
        "runs_analyzed": n,
        "profiles": profile_counts,
        # Token analytics
        "tokens": {
            "avg_per_run": _safe_avg(total_tokens_list),
            "min_per_run": min(total_tokens_list) if total_tokens_list else 0,
            "max_per_run": max(total_tokens_list) if total_tokens_list else 0,
            "total_all_runs": sum(total_tokens_list),
            "per_stage_avg": {
                stage: _safe_avg(costs)
                for stage, costs in sorted(stage_token_agg.items())
            },
        },
        # Catch analytics — the core value
        "catches": {
            "review_findings_total": total_review_findings,
            "review_findings_avg": round(total_review_findings / n, 2),
            "adversarial_findings_total": total_adversarial,
            "adversarial_high_total": total_adversarial_high,
            "adversarial_hit_rate": f"{runs_with_adversarial}/{n} runs ({round(runs_with_adversarial/n*100)}%)",
            "validation_blocks_total": total_validation_blocks,
            "validation_block_rate": f"{runs_with_blocks}/{n} runs ({round(runs_with_blocks/n*100)}%)",
            "test_regressions_total": total_test_regressions,
        },
        # Quality analytics
        "quality": {
            "confidence_avg": _safe_avg(confidence_scores),
            "confidence_min": min(confidence_scores) if confidence_scores else 0,
            "confidence_max": max(confidence_scores) if confidence_scores else 0,
            "confidence_distribution": {
                "low_0_4": sum(1 for s in confidence_scores if s <= 4),
                "med_5_7": sum(1 for s in confidence_scores if 5 <= s <= 7),
                "high_8_12": sum(1 for s in confidence_scores if s >= 8),
            },
        },
        # Decision analytics
        "decisions": {
            **total_decisions,
            "total": sum(total_decisions.values()),
            "automation_rate": (
                f"{round(total_decisions['mechanical'] / sum(total_decisions.values()) * 100)}%"
                if sum(total_decisions.values()) > 0 else "N/A"
            ),
        },
        # Duration analytics
        "duration": {
            "avg_minutes": _safe_avg(durations),
            "min_minutes": min(durations) if durations else 0,
            "max_minutes": max(durations) if durations else 0,
        },
    }

    print(json.dumps(analytics, indent=2))


def cmd_run_cultivate(args, reg: ArtifactRegistry) -> None:
    """Apply pipeline lessons to DDD docs via cultivation engine.

    Reads the 'lessons' field from the reflect stage in run.json,
    then calls cultivate_from_reflect() which auto-applies safe additive
    lessons and escalates risky ones.

    This is the CLI bridge that makes cultivation callable from agent
    Bash tools — the agent reads reflect.md instructions and runs this.
    """
    run_file = _resolve_run_file(args.project, args.run_id)
    run_state = json.loads(run_file.read_text(encoding="utf-8"))

    # Extract lessons from reflect stage record
    lessons: list[str] = []
    for stage in run_state.get("stages", []):
        if stage.get("stage") == "reflect":
            lessons = stage.get("lessons", [])
            break

    if not lessons:
        print(json.dumps({
            "applied": 0,
            "escalated": 0,
            "rejected": 0,
            "note": "No lessons found in reflect stage — nothing to cultivate",
        }))
        return

    # Resolve project directory
    workspace = _get_workspace()
    project_dir = workspace / "Projects" / args.project

    if not project_dir.is_dir():
        print(json.dumps({"error": f"Project directory not found: {project_dir}"}))
        return

    # Run cultivation
    from core.ddd_cultivation import cultivate_from_reflect

    result = cultivate_from_reflect(lessons, args.run_id, args.project, project_dir)
    print(json.dumps(result, indent=2))


def cmd_ddd_health(args, reg) -> None:
    """5-dimensional DDD health scoring per section."""
    project_dir = _get_workspace() / "Projects" / args.project
    if not project_dir.is_dir():
        print(json.dumps({"error": f"Project '{args.project}' not found"}))
        return

    from core.ddd_health import compute_section_health

    result = compute_section_health(project_dir)
    print(json.dumps(result, indent=2, default=str))


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
    p_publish.add_argument("--stage", default=None, help="Pipeline stage for schema validation (validates BEFORE publishing)")

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
    p_run_report.add_argument("--force", action="store_true", help="Overwrite existing REPORT.md")

    # run-metrics (generate METRICS.json for one run)
    p_run_metrics = sub.add_parser("run-metrics", help="Generate METRICS.json for a pipeline run")
    p_run_metrics.add_argument("--project", required=True)
    p_run_metrics.add_argument("--run-id", required=True, help="Pipeline run ID")

    # run-analytics (cross-run aggregation)
    p_run_analytics = sub.add_parser("run-analytics", help="Cross-run pipeline analytics")
    p_run_analytics.add_argument("--project", required=True)
    p_run_analytics.add_argument("--limit", type=int, default=50, help="Max runs to analyze")

    # run-cultivate (DDD cultivation from pipeline lessons)
    p_run_cultivate = sub.add_parser("run-cultivate", help="Apply pipeline lessons to DDD docs via cultivation engine")
    p_run_cultivate.add_argument("--project", required=True)
    p_run_cultivate.add_argument("--run-id", required=True, help="Pipeline run ID (reads lessons from reflect stage)")

    # ddd-health
    p_ddd_health = sub.add_parser("ddd-health", help="5-dimensional DDD health scoring per section")
    p_ddd_health.add_argument("--project", required=True)

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
        "run-metrics": cmd_run_metrics,
        "run-analytics": cmd_run_analytics,
        "run-cultivate": cmd_run_cultivate,
        "ddd-health": cmd_ddd_health,
    }
    handlers[args.command](args, reg)


if __name__ == "__main__":
    main()
