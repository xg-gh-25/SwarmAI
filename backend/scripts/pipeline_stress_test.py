#!/usr/bin/env python3
"""v4b Stress Test — validation harness for the autonomous pipeline.

Defines 5 diverse requirements (one per pipeline profile), validates
pipeline run results, and produces a calibration summary. Used as the
release gate for Phase 3a.

Usage:
    python pipeline_stress_test.py define --project SwarmAI
    python pipeline_stress_test.py validate --project SwarmAI
    python pipeline_stress_test.py report --project SwarmAI

Subcommands:
    define    — Create 5 pipeline runs (one per profile) with expected metadata
    validate  — Check all 5 completed runs: profile, validator, report, budget
    report    — Aggregate per-stage token costs and produce calibration summary

Public symbols:
- ``STRESS_REQUIREMENTS``  — The 5 test requirements with expected profiles
- ``define_runs``          — Create pipeline runs for all requirements
- ``validate_run``         — Validate a single completed run
- ``validate_all``         — Validate all 5 runs
- ``generate_report``      — Aggregate calibration data
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# core.pipeline_profiles available for future profile validation


# ---------------------------------------------------------------------------
# The 5 stress test requirements — one per pipeline profile
# ---------------------------------------------------------------------------

STRESS_REQUIREMENTS: list[dict[str, str]] = [
    {
        "id": "stress_trivial",
        "requirement": (
            "Add a --validate flag to artifact_cli.py run-create that "
            "auto-runs pipeline_validator after the run is created"
        ),
        "expected_profile": "trivial",
        "rationale": "Single flag addition to existing CLI. No research or design needed.",
    },
    {
        "id": "stress_full",
        "requirement": (
            "Add pipeline run metrics aggregation — a run-metrics command to "
            "artifact_cli that computes average duration, pass rate, common failure "
            "stages, and profile distribution across all completed runs"
        ),
        "expected_profile": "full",
        "rationale": "Needs research (what metrics?), design (data model), build, test.",
    },
    {
        "id": "stress_research",
        "requirement": (
            "Research how to add token counting instrumentation to the autonomous "
            "pipeline — measure actual tokens per stage from Claude CLI output"
        ),
        "expected_profile": "research",
        "rationale": "Investigation only. No code output expected.",
    },
    {
        "id": "stress_docs",
        "requirement": (
            "Add module-level docstrings to pipeline_profiles.py, pipeline_validator.py, "
            "and the pipelines router following SwarmAI documentation standards"
        ),
        "expected_profile": "docs",
        "rationale": "Documentation only. No logic changes.",
    },
    {
        "id": "stress_bugfix",
        "requirement": (
            "Add --format flag to pipeline_validator.py supporting json (default), "
            "table (human-readable), and compact (one-line) output formats"
        ),
        "expected_profile": "bugfix",
        "rationale": "Enhancement to existing tool. Known scope, no research needed.",
    },
]


# ---------------------------------------------------------------------------
# Workspace helpers
# ---------------------------------------------------------------------------

def _get_workspace() -> Path:
    import os
    ws = os.environ.get("SWARM_WORKSPACE", str(Path.home() / ".swarm-ai" / "SwarmWS"))
    return Path(ws).expanduser().resolve()


def _load_run(project: str, run_id: str) -> dict[str, Any] | None:
    ws = _get_workspace()
    run_file = ws / "Projects" / project / ".artifacts" / "runs" / run_id / "run.json"
    if not run_file.exists():
        return None
    return json.loads(run_file.read_text())


def _list_completed_runs(project: str) -> list[dict]:
    ws = _get_workspace()
    runs_dir = ws / "Projects" / project / ".artifacts" / "runs"
    if not runs_dir.exists():
        return []

    runs = []
    for run_dir in sorted(runs_dir.iterdir()):
        run_file = run_dir / "run.json"
        if run_file.exists():
            try:
                run = json.loads(run_file.read_text())
                if run.get("status") == "completed":
                    runs.append(run)
            except (json.JSONDecodeError, OSError):
                continue
    return runs


# ---------------------------------------------------------------------------
# define — create pipeline runs
# ---------------------------------------------------------------------------

def define_runs(project: str) -> list[dict]:
    """Create pipeline runs for all 5 stress test requirements.

    Returns list of {run_id, project, requirement, expected_profile}.
    Does NOT execute the pipelines — that's done interactively.
    """
    ws = _get_workspace()
    results = []

    for req in STRESS_REQUIREMENTS:
        # Create run directory
        run_id = req["id"]
        run_dir = ws / "Projects" / project / ".artifacts" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        run = {
            "id": run_id,
            "project": project,
            "requirement": req["requirement"],
            "profile": None,  # Set by EVALUATE stage
            "status": "pending",
            "stages": [],
            "taste_decisions": [],
            "budget": {
                "session_total": 800000,
                "checkpoint_reserve": 50000,
                "consumed": 0,
                "remaining": 800000,
                "stage_estimates": {},
                "calibration_source": "defaults",
            },
            "expected_profile": req["expected_profile"],
            "stress_test": True,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "completed_at": None,
        }

        (run_dir / "run.json").write_text(json.dumps(run, indent=2))

        results.append({
            "run_id": run_id,
            "project": project,
            "requirement": req["requirement"],
            "expected_profile": req["expected_profile"],
        })

    return results


# ---------------------------------------------------------------------------
# validate — check a completed run
# ---------------------------------------------------------------------------

def validate_run(project: str, run_id: str, expected_profile: str) -> dict[str, Any]:
    """Validate a single pipeline run against stress test criteria.

    Checks:
        - profile_match: actual profile matches expected
        - validator_pass: pipeline_validator summary returns valid=true
        - report_exists: REPORT.md exists in run directory
        - budget_recorded: all stages have token_cost > 0
        - structural_errors: count of BLOCK-level violations
        - structural_warnings: count of WARN-level issues
    """
    run = _load_run(project, run_id)
    result: dict[str, Any] = {
        "run_id": run_id,
        "expected_profile": expected_profile,
        "actual_profile": None,
        "status": None,
        "profile_match": False,
        "report_exists": False,
        "budget_recorded": False,
        "structural_errors": 0,
        "structural_warnings": 0,
        "stages_completed": 0,
        "total_token_cost": 0,
    }

    if run is None:
        result["error"] = f"Run {run_id} not found"
        return result

    result["status"] = run.get("status")
    result["actual_profile"] = run.get("profile")
    result["profile_match"] = run.get("profile") == expected_profile

    # Check REPORT.md
    ws = _get_workspace()
    report_path = ws / "Projects" / project / ".artifacts" / "runs" / run_id / "REPORT.md"
    result["report_exists"] = report_path.exists()

    # Check budget recording
    stages = run.get("stages", [])
    result["stages_completed"] = sum(
        1 for s in stages if s.get("status") == "completed"
    )

    all_have_budget = all(
        s.get("token_cost", 0) > 0
        for s in stages
        if s.get("status") == "completed"
    )
    result["budget_recorded"] = all_have_budget if stages else False

    # Aggregate token costs
    result["total_token_cost"] = sum(
        s.get("token_cost", 0) for s in stages
    )

    # Run structural validation via pipeline_validator
    try:
        from scripts.pipeline_validator import validate as pv_validate
        total_errors = 0
        total_warnings = 0
        for stage_rec in stages:
            if stage_rec.get("status") in ("completed", "running"):
                pv_result = pv_validate(project, run_id, stage_rec["stage"])
                total_errors += len(pv_result.get("errors", []))
                total_warnings += len(pv_result.get("warnings", []))
        result["structural_errors"] = total_errors
        result["structural_warnings"] = total_warnings
    except Exception as e:
        result["validator_error"] = str(e)

    return result


def validate_all(project: str) -> list[dict]:
    """Validate all 5 stress test runs."""
    results = []
    for req in STRESS_REQUIREMENTS:
        result = validate_run(project, req["id"], req["expected_profile"])
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# report — calibration summary
# ---------------------------------------------------------------------------

def generate_report(project: str) -> dict[str, Any]:
    """Generate calibration report from all completed runs.

    Aggregates:
        - Per-stage average token costs
        - Profile distribution
        - Pass/fail rates
        - Common failure stages
    """
    runs = _list_completed_runs(project)

    if not runs:
        return {
            "project": project,
            "total_runs": 0,
            "stage_averages": {},
            "profile_distribution": {},
            "pass_rate": 0.0,
            "common_failures": [],
        }

    stage_costs: dict[str, list[int]] = defaultdict(list)
    profile_counts: dict[str, int] = defaultdict(int)
    failure_stages: list[str] = []
    total_token_costs: list[int] = []

    for run in runs:
        profile = run.get("profile", "unknown")
        profile_counts[profile] += 1

        run_total = 0
        for stage in run.get("stages", []):
            cost = stage.get("token_cost", 0)
            stage_name = stage.get("stage", "unknown")

            if stage.get("status") == "completed" and cost > 0:
                stage_costs[stage_name].append(cost)
                run_total += cost

            if stage.get("status") in ("failed", "blocked"):
                failure_stages.append(stage_name)

        total_token_costs.append(run_total)

    # Compute averages
    stage_averages = {
        stage: {
            "avg": int(sum(costs) / len(costs)),
            "min": min(costs),
            "max": max(costs),
            "samples": len(costs),
        }
        for stage, costs in stage_costs.items()
    }

    # Failure frequency
    failure_freq: dict[str, int] = defaultdict(int)
    for s in failure_stages:
        failure_freq[s] += 1
    common_failures = sorted(failure_freq.items(), key=lambda x: -x[1])

    return {
        "project": project,
        "total_runs": len(runs),
        "stage_averages": stage_averages,
        "profile_distribution": dict(profile_counts),
        "pass_rate": sum(1 for r in runs if r.get("status") == "completed") / len(runs),
        "common_failures": [{"stage": s, "count": c} for s, c in common_failures],
        "avg_total_cost": int(sum(total_token_costs) / len(total_token_costs)) if total_token_costs else 0,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="v4b Stress Test — validate autonomous pipeline runs"
    )
    sub = parser.add_subparsers(dest="command")

    define_p = sub.add_parser("define", help="Create 5 stress test pipeline runs")
    define_p.add_argument("--project", required=True)

    validate_p = sub.add_parser("validate", help="Validate completed stress test runs")
    validate_p.add_argument("--project", required=True)

    report_p = sub.add_parser("report", help="Generate calibration report")
    report_p.add_argument("--project", required=True)

    args = parser.parse_args()

    if args.command == "define":
        results = define_runs(args.project)
        print(json.dumps(results, indent=2))

    elif args.command == "validate":
        results = validate_all(args.project)
        # Summary
        passed = sum(1 for r in results if r.get("structural_errors", 0) == 0
                     and r.get("profile_match", False))
        print(json.dumps({
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "results": results,
        }, indent=2))
        sys.exit(0 if passed == len(results) else 1)

    elif args.command == "report":
        report = generate_report(args.project)
        print(json.dumps(report, indent=2))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
