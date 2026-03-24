#!/usr/bin/env python3
"""Pipeline stage validator — structural enforcement for the pipeline orchestrator.

Enforces 6 invariants after each pipeline stage to prevent behavioral drift
in the prompt-driven orchestrator. Called via bash after every stage:

    python pipeline_validator.py check \\
        --project SwarmAI --run-id run_xxx --stage evaluate

Returns JSON:
    {"valid": true, "stage": "evaluate", "errors": [], "warnings": [],
     "checks_passed": 6, "checks_total": 6}

Errors (BLOCK) prevent stage advancement. Warnings are informational —
they surface in the delivery report but don't block progress.

The 6 invariant checks:
    1. Stage order     — current stage follows the last completed stage per profile
    2. Artifact exists — stage published an artifact (except reflect)
    3. Artifact schema — required fields present in artifact JSON
    4. Decision logged — at least 1 decision classified in StageRecord
    5. Budget recorded — token_cost > 0 in stage record
    6. Profile respected — stage is in the selected profile

Public symbols:
- ``main``        — CLI entry point
- ``validate``    — Core validation logic (testable without CLI)
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Add parent directory for core imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.pipeline_profiles import get_profile_stages


# ---------------------------------------------------------------------------
# Stage artifact schemas — required fields produce BLOCK, recommended produce WARN
# ---------------------------------------------------------------------------

STAGE_SCHEMAS: dict[str, dict[str, list[str]]] = {
    "evaluate": {
        "required": ["recommendation", "scope"],
        "recommended": ["acceptance_criteria", "scores"],
    },
    "think": {
        "required": ["key_findings"],
        "recommended": ["alternatives", "sources"],
    },
    "plan": {
        "required": ["acceptance_criteria"],
        "recommended": ["approach", "data_model"],
    },
    "build": {
        "required": ["files_changed"],
        "recommended": ["commits", "diff_summary", "tdd"],
    },
    "review": {
        "required": ["approved"],
        "recommended": ["findings"],
    },
    "test": {
        "required": ["passed"],
        "recommended": ["failed", "fixed", "coverage"],
    },
    "deliver": {
        "required": ["title", "status"],
        "recommended": ["confidence_score", "decisions", "attention_flags", "report_path"],
    },
    # reflect has no artifact — skip schema check
}

# Stages that don't require an artifact
NO_ARTIFACT_STAGES = {"reflect"}

# Stages where decisions are optional (informational stages)
DECISION_OPTIONAL_STAGES = {"reflect", "deliver"}


# ---------------------------------------------------------------------------
# Workspace resolution
# ---------------------------------------------------------------------------

def _get_workspace() -> Path:
    import os
    ws = os.environ.get("SWARM_WORKSPACE", str(Path.home() / ".swarm-ai" / "SwarmWS"))
    return Path(ws).expanduser().resolve()


def _load_run(project: str, run_id: str) -> dict[str, Any] | None:
    """Load a pipeline run from .artifacts/runs/<run_id>/run.json."""
    ws = _get_workspace()
    run_file = ws / "Projects" / project / ".artifacts" / "runs" / run_id / "run.json"
    if not run_file.exists():
        return None
    return json.loads(run_file.read_text())


def _load_artifact_data(project: str, run_id: str, artifact_id: str) -> dict[str, Any] | None:
    """Load artifact data file from the run directory or top-level .artifacts/."""
    ws = _get_workspace()
    artifacts_dir = ws / "Projects" / project / ".artifacts"

    # Lookup via manifest (covers both run-scoped and top-level artifacts)
    manifest_file = artifacts_dir / "manifest.json"
    if not manifest_file.exists():
        return None

    try:
        manifest = json.loads(manifest_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    for entry in manifest.get("artifacts", []):
        if entry.get("id") == artifact_id:
            data_file = artifacts_dir / entry.get("file", "")
            if data_file.exists():
                try:
                    return json.loads(data_file.read_text())
                except (json.JSONDecodeError, OSError):
                    return None
    return None


# ---------------------------------------------------------------------------
# Core validation
# ---------------------------------------------------------------------------

def validate(project: str, run_id: str, stage: str) -> dict[str, Any]:
    """Validate a pipeline stage against 6 structural invariants.

    Returns a result dict with:
        valid: bool — False if any BLOCK errors
        stage: str — the validated stage
        errors: list[str] — BLOCK-level violations
        warnings: list[str] — informational issues
        checks_passed: int
        checks_total: int
    """
    errors: list[str] = []
    warnings: list[str] = []
    checks_total = 6
    checks_passed = 0

    # Load run state
    run = _load_run(project, run_id)
    if run is None:
        return {
            "valid": False,
            "stage": stage,
            "errors": [f"Pipeline run {run_id} not found for project {project}"],
            "warnings": [],
            "checks_passed": 0,
            "checks_total": checks_total,
        }

    profile = run.get("profile") or "full"
    stages_list = run.get("stages", [])

    # Find the stage record for the stage being validated
    stage_record = None
    for s in stages_list:
        if s.get("stage") == stage:
            stage_record = s

    if stage_record is None:
        return {
            "valid": False,
            "stage": stage,
            "errors": [f"No stage record found for '{stage}' in run {run_id}"],
            "warnings": [],
            "checks_passed": 0,
            "checks_total": checks_total,
        }

    # --- Check 1: Stage order ---
    if _check_stage_order(stage, profile, stages_list):
        checks_passed += 1
    else:
        profile_stages = get_profile_stages(profile)
        expected_idx = profile_stages.index(stage) if stage in profile_stages else -1
        if expected_idx > 0:
            expected_prev = profile_stages[expected_idx - 1]
            errors.append(
                f"Stage order violation: '{stage}' requires '{expected_prev}' "
                f"to be completed first (profile: {profile})"
            )
        else:
            errors.append(f"Stage order violation: '{stage}' position invalid in profile '{profile}'")

    # --- Check 2: Artifact exists ---
    if _check_artifact_exists(stage, stage_record):
        checks_passed += 1
    else:
        if stage not in NO_ARTIFACT_STAGES:
            errors.append(
                f"No artifact published for '{stage}' — "
                f"artifact_id is missing or empty in stage record"
            )
        else:
            # Shouldn't reach here, but be safe
            checks_passed += 1

    # --- Check 3: Artifact schema ---
    schema_result = _check_artifact_schema(stage, stage_record, project, run_id)
    if schema_result["passed"]:
        checks_passed += 1
    errors.extend(schema_result.get("errors", []))
    warnings.extend(schema_result.get("warnings", []))

    # --- Check 4: Decision logged (WARN only — doesn't block) ---
    if _check_decision_logged(stage, stage_record):
        checks_passed += 1
    else:
        checks_passed += 1  # Warnings don't reduce checks_passed
        if stage not in DECISION_OPTIONAL_STAGES:
            warnings.append(
                f"No decisions classified for '{stage}' — "
                f"classify at least one decision (mechanical/taste/judgment)"
            )

    # --- Check 5: Budget recorded (WARN only — doesn't block) ---
    if _check_budget_recorded(stage_record):
        checks_passed += 1
    else:
        checks_passed += 1  # Warnings don't reduce checks_passed
        warnings.append(
            f"token_cost is 0 for '{stage}' — "
            f"estimate the token cost for budget calibration"
        )

    # --- Check 6: Profile respected ---
    if _check_profile_respected(stage, profile):
        checks_passed += 1
    else:
        errors.append(
            f"Profile violation: '{stage}' is not in the '{profile}' profile. "
            f"Expected stages: {get_profile_stages(profile)}"
        )

    return {
        "valid": len(errors) == 0,
        "stage": stage,
        "errors": errors,
        "warnings": warnings,
        "checks_passed": checks_passed,
        "checks_total": checks_total,
    }


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_stage_order(stage: str, profile: str, stages_list: list[dict]) -> bool:
    """Check 1: Current stage follows the last completed stage per profile."""
    profile_stages = get_profile_stages(profile)

    if stage not in profile_stages:
        return False  # Not in profile at all — caught by check 6 too

    stage_idx = profile_stages.index(stage)

    if stage_idx == 0:
        return True  # First stage always valid

    # All prior stages in profile must be completed or skipped
    for i in range(stage_idx):
        prior_stage_name = profile_stages[i]
        prior_record = _find_stage_record(prior_stage_name, stages_list)
        if prior_record is None:
            return False  # Prior stage not even recorded
        if prior_record.get("status") not in ("completed", "skipped"):
            return False  # Prior stage not done

    return True


def _check_artifact_exists(stage: str, stage_record: dict) -> bool:
    """Check 2: Stage published an artifact (reflect is exempt)."""
    if stage in NO_ARTIFACT_STAGES:
        return True  # No artifact required

    artifact_id = stage_record.get("artifact_id")
    return bool(artifact_id and artifact_id.strip())


def _check_artifact_schema(
    stage: str, stage_record: dict, project: str, run_id: str
) -> dict[str, Any]:
    """Check 3: Required/recommended fields present in artifact data.

    Returns {"passed": bool, "errors": [...], "warnings": [...]}.
    """
    result: dict[str, Any] = {"passed": True, "errors": [], "warnings": []}

    if stage not in STAGE_SCHEMAS:
        return result  # No schema defined (e.g., reflect)

    artifact_id = stage_record.get("artifact_id")
    if not artifact_id:
        # No artifact — this is caught by check 2, skip schema check
        return result

    schema = STAGE_SCHEMAS[stage]
    artifact_data = _load_artifact_data(project, run_id, artifact_id)

    if artifact_data is None:
        result["passed"] = False
        result["errors"].append(
            f"Artifact {artifact_id} for '{stage}' could not be loaded — "
            f"file missing or corrupt"
        )
        return result

    # Check required fields (BLOCK)
    for field in schema.get("required", []):
        if field not in artifact_data:
            result["passed"] = False
            result["errors"].append(
                f"Schema violation: '{stage}' artifact missing required field '{field}'"
            )

    # Check recommended fields (WARN)
    for field in schema.get("recommended", []):
        if field not in artifact_data:
            result["warnings"].append(
                f"Schema note: '{stage}' artifact missing recommended field '{field}'"
            )

    return result


def _check_decision_logged(stage: str, stage_record: dict) -> bool:
    """Check 4: At least 1 decision classified in the stage record."""
    if stage in DECISION_OPTIONAL_STAGES:
        return True  # reflect and deliver don't require decisions

    decisions = stage_record.get("decisions", [])
    return len(decisions) > 0


def _check_budget_recorded(stage_record: dict) -> bool:
    """Check 5: token_cost > 0 in the stage record."""
    token_cost = stage_record.get("token_cost", 0)
    return token_cost > 0


def _check_profile_respected(stage: str, profile: str) -> bool:
    """Check 6: Stage is in the selected pipeline profile."""
    profile_stages = get_profile_stages(profile)
    return stage in profile_stages


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_stage_record(stage_name: str, stages_list: list[dict]) -> dict | None:
    """Find the most recent record for a given stage name."""
    for s in reversed(stages_list):
        if s.get("stage") == stage_name:
            return s
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pipeline stage validator — structural enforcement"
    )
    sub = parser.add_subparsers(dest="command")

    # check command
    check_p = sub.add_parser("check", help="Validate a pipeline stage")
    check_p.add_argument("--project", required=True, help="Project name")
    check_p.add_argument("--run-id", required=True, help="Pipeline run ID")
    check_p.add_argument("--stage", required=True, help="Stage to validate")

    # summary command — validate all completed stages in a run
    summary_p = sub.add_parser("summary", help="Validate all stages in a pipeline run")
    summary_p.add_argument("--project", required=True, help="Project name")
    summary_p.add_argument("--run-id", required=True, help="Pipeline run ID")

    args = parser.parse_args()

    if args.command == "check":
        result = validate(args.project, args.run_id, args.stage)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["valid"] else 1)

    elif args.command == "summary":
        run = _load_run(args.project, args.run_id)
        if run is None:
            print(json.dumps({"error": f"Run {args.run_id} not found"}))
            sys.exit(1)

        all_results = []
        total_errors = 0
        total_warnings = 0

        for stage_rec in run.get("stages", []):
            stage_name = stage_rec.get("stage")
            if stage_rec.get("status") in ("completed", "running"):
                result = validate(args.project, args.run_id, stage_name)
                all_results.append(result)
                total_errors += len(result["errors"])
                total_warnings += len(result["warnings"])

        summary = {
            "run_id": args.run_id,
            "project": args.project,
            "valid": total_errors == 0,
            "stages_checked": len(all_results),
            "total_errors": total_errors,
            "total_warnings": total_warnings,
            "results": all_results,
        }
        print(json.dumps(summary, indent=2))
        sys.exit(0 if total_errors == 0 else 1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
