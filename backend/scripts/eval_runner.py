#!/usr/bin/env python3
"""
SwarmAI OS Eval Runner — Execute golden set cases and produce eval reports.

Reads Projects/SwarmAI/golden_set.yaml, runs programmatic evaluators,
outputs JSON to Projects/SwarmAI/EvalHistory/{date}_{trigger}.json.

Usage:
    python backend/scripts/eval_runner.py run --trigger manual
    python backend/scripts/eval_runner.py run --trigger weekly
    python backend/scripts/eval_runner.py run --trigger steering_edit --cases GS001,GS002
    python backend/scripts/eval_runner.py validate  # schema check only

Evaluator types supported in P1 (programmatic):
    - canary_pass: run shell command, check output contains expected string
    - file_contains: grep a file for expected content
    - keyword_match: placeholder (requires agent response, skipped in P1)

Deferred to P3 (llm_judge):
    - goal_success: LLM judges assertions against agent behavior
    - quality_score: LLM rates on 0-1 scale
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    # Fallback: try to find yaml in the backend venv
    sys.exit("PyYAML required. Install: pip install pyyaml")


# ─── Paths ────────────────────────────────────────────────────────────────────

def _find_workspace_root() -> Path:
    """Find SwarmWS root (has Projects/ directory)."""
    candidates = [
        Path.home() / ".swarm-ai" / "SwarmWS",
        Path.cwd(),
        Path(__file__).resolve().parent.parent.parent,  # backend/scripts/ → repo root → SwarmWS
    ]
    for c in candidates:
        if (c / "Projects" / "SwarmAI").is_dir():
            return c
    raise FileNotFoundError("Cannot locate SwarmWS with Projects/SwarmAI/")


def _find_swarmai_repo() -> Path:
    """Find swarmai codebase root (has backend/ directory)."""
    candidates = [
        Path(__file__).resolve().parent.parent.parent,  # backend/scripts/ → backend/ → swarmai/
        Path.home() / "Desktop" / "SwarmAI-Workspace" / "swarmai",
        Path.cwd(),
    ]
    for c in candidates:
        if (c / "backend" / "core").is_dir():
            return c
    raise FileNotFoundError("Cannot locate swarmai repo with backend/core/")


def _golden_set_path(root: Path) -> Path:
    return root / "Projects" / "SwarmAI" / "golden_set.yaml"


def _eval_history_dir(root: Path) -> Path:
    d = root / "Projects" / "SwarmAI" / "EvalHistory"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ─── Load & Validate ─────────────────────────────────────────────────────────

def load_golden_set(path: Path) -> dict:
    """Parse golden_set.yaml and validate basic structure."""
    if not path.exists():
        raise FileNotFoundError(f"Golden set not found: {path}")

    with open(path) as f:
        data = yaml.safe_load(f)

    # Basic schema validation
    assert data.get("version") == 2, f"Expected version 2, got {data.get('version')}"
    assert "cases" in data, "Missing 'cases' key"
    assert len(data["cases"]) > 0, "No cases defined"

    for case in data["cases"]:
        assert "id" in case, f"Case missing 'id': {case.get('title', '?')}"
        assert "evaluators" in case, f"Case {case['id']} missing 'evaluators'"
        assert "affected_by" in case, f"Case {case['id']} missing 'affected_by'"

    return data


# ─── Evaluators (Programmatic) ────────────────────────────────────────────────

def eval_canary_pass(case: dict, root: Path) -> dict:
    """Run a command and check output contains expected string."""
    verification = case.get("verification", {})
    command = verification.get("command", "")
    expected = verification.get("expected_contains", "")

    if not command:
        return {"status": "failed", "notes": "No command specified in verification"}

    try:
        repo_root = _find_swarmai_repo()
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=10, cwd=str(repo_root)
        )
        output = result.stdout + result.stderr

        if expected and expected in output:
            return {"status": "passed", "notes": f"Output contains '{expected}'"}
        elif result.returncode == 0 and not expected:
            return {"status": "passed", "notes": "Command exited 0"}
        else:
            return {
                "status": "failed",
                "notes": f"Expected '{expected}' not found in output. Exit code: {result.returncode}. Output: {output[:200]}"
            }
    except subprocess.TimeoutExpired:
        return {"status": "failed", "notes": "Command timed out (10s)"}
    except Exception as e:
        return {"status": "failed", "notes": f"Error: {str(e)[:200]}"}


def eval_file_contains(case: dict, root: Path) -> dict:
    """Check if a file contains expected content."""
    verification = case.get("verification", {})
    file_path = verification.get("file", "")
    grep_pattern = verification.get("grep", "")
    expected = verification.get("expected_contains", "")

    if not file_path:
        return {"status": "failed", "notes": "No file specified in verification"}

    # Resolve relative to swarmai repo or workspace
    repo_root = _find_swarmai_repo()
    full_path = repo_root / file_path
    if not full_path.exists():
        full_path = root / file_path
    if not full_path.exists():
        return {"status": "failed", "notes": f"File not found: {file_path}"}

    try:
        content = full_path.read_text(errors="replace")

        if grep_pattern and grep_pattern in content:
            if expected and expected in content:
                return {"status": "passed", "notes": f"File contains '{grep_pattern}' and '{expected}'"}
            elif not expected:
                return {"status": "passed", "notes": f"File contains '{grep_pattern}'"}
            else:
                return {"status": "failed", "notes": f"Found '{grep_pattern}' but not '{expected}'"}
        elif grep_pattern:
            return {"status": "failed", "notes": f"'{grep_pattern}' not found in {file_path}"}
        elif expected and expected in content:
            return {"status": "passed", "notes": f"File contains '{expected}'"}
        else:
            return {"status": "failed", "notes": f"Expected content not found in {file_path}"}
    except Exception as e:
        return {"status": "failed", "notes": f"Error reading file: {str(e)[:200]}"}


# ─── Case Dispatch ────────────────────────────────────────────────────────────

PROGRAMMATIC_EVALUATORS = {"canary_pass", "file_contains", "keyword_match",
                           "trajectory_exact", "trajectory_in_order", "trajectory_any_order"}
LLM_EVALUATORS = {"goal_success", "quality_score"}


def evaluate_case(case: dict, root: Path) -> dict:
    """Dispatch case to appropriate evaluator. Returns result dict."""
    evaluators = case.get("evaluators", [])
    case_id = case["id"]

    start = time.time()

    # Determine which evaluator to use (first match)
    for ev in evaluators:
        if ev == "canary_pass":
            result = eval_canary_pass(case, root)
            result["evaluator"] = "canary_pass"
            result["duration_ms"] = int((time.time() - start) * 1000)
            return result
        elif ev == "file_contains":
            result = eval_file_contains(case, root)
            result["evaluator"] = "file_contains"
            result["duration_ms"] = int((time.time() - start) * 1000)
            return result
        elif ev in LLM_EVALUATORS:
            # Deferred to P3 — mark as skipped
            return {
                "status": "skipped",
                "evaluator": ev,
                "notes": f"LLM evaluator '{ev}' not implemented (P3)",
                "duration_ms": 0
            }

    return {
        "status": "skipped",
        "evaluator": "none",
        "notes": f"No supported evaluator for case {case_id}",
        "duration_ms": 0
    }


# ─── Score Computation ────────────────────────────────────────────────────────

def compute_scores(cases: list, results: list[dict]) -> dict:
    """Compute overall score and per-dimension scores."""
    # Only count non-skipped cases
    scored = [(c, r) for c, r in zip(cases, results) if r["status"] != "skipped"]

    if not scored:
        return {"overall": 0.0, "dimensions": {}, "scored_count": 0, "skipped_count": len(results)}

    passed = sum(1 for _, r in scored if r["status"] == "passed")
    overall = round(passed / len(scored) * 100, 1) if scored else 0.0

    # Per-dimension
    dim_scores = {}
    for case, result in scored:
        dim = case.get("dimension", "unknown")
        if dim not in dim_scores:
            dim_scores[dim] = {"passed": 0, "total": 0}
        dim_scores[dim]["total"] += 1
        if result["status"] == "passed":
            dim_scores[dim]["passed"] += 1

    dimensions = {
        dim: round(s["passed"] / s["total"] * 100, 1) if s["total"] > 0 else 0.0
        for dim, s in dim_scores.items()
    }

    return {
        "overall": overall,
        "dimensions": dimensions,
        "scored_count": len(scored),
        "skipped_count": len(results) - len(scored),
    }


# ─── Run Orchestration ────────────────────────────────────────────────────────

def run_eval(golden_set: dict, trigger: str, case_filter: list[str] | None, root: Path) -> dict:
    """Execute eval run. Returns full run result dict."""
    cases = golden_set["cases"]

    # Filter if specific cases requested
    if case_filter:
        cases = [c for c in cases if c["id"] in case_filter]

    results = []
    for case in cases:
        result = evaluate_case(case, root)
        result["id"] = case["id"]
        results.append(result)

    scores = compute_scores(cases, results)
    now = datetime.now(timezone.utc)

    run_result = {
        "run_id": f"eval_{now.strftime('%Y%m%d')}_{trigger}",
        "triggered_by": trigger,
        "triggered_at": now.isoformat(),
        "status": "completed",
        "overall_score": scores["overall"],
        "dimensions": scores["dimensions"],
        "cases": [
            {
                "id": r["id"],
                "status": r["status"],
                "evaluator": r.get("evaluator", ""),
                "duration_ms": r.get("duration_ms", 0),
                "notes": r.get("notes", ""),
            }
            for r in results
        ],
        "total_cases": len(cases),
        "cases_passed": sum(1 for r in results if r["status"] == "passed"),
        "cases_failed": sum(1 for r in results if r["status"] == "failed"),
        "cases_skipped": sum(1 for r in results if r["status"] == "skipped"),
        "scored_count": scores["scored_count"],
        "duration_seconds": round(sum(r.get("duration_ms", 0) for r in results) / 1000, 2),
    }

    return run_result


def write_run(run_result: dict, root: Path) -> Path:
    """Save run result to EvalHistory/."""
    hist_dir = _eval_history_dir(root)
    trigger = run_result["triggered_by"]
    date = datetime.now().strftime("%Y-%m-%d")
    filename = f"{date}_{trigger}.json"
    path = hist_dir / filename

    with open(path, "w") as f:
        json.dump(run_result, f, indent=2)

    return path


# ─── CLI ──────────────────────────────────────────────────────────────────────

def cmd_run(args):
    """Execute eval and write results."""
    root = _find_workspace_root()
    gs_path = _golden_set_path(root)

    golden_set = load_golden_set(gs_path)
    print(f"Loaded {len(golden_set['cases'])} cases from {gs_path.name}")

    case_filter = args.cases.split(",") if args.cases else None
    run_result = run_eval(golden_set, args.trigger, case_filter, root)

    out_path = write_run(run_result, root)
    print(f"\n{'='*60}")
    print(f"  OS Health Score: {run_result['overall_score']}%")
    print(f"  Passed: {run_result['cases_passed']} | Failed: {run_result['cases_failed']} | Skipped: {run_result['cases_skipped']}")
    print(f"  Dimensions: {json.dumps(run_result['dimensions'], indent=None)}")
    print(f"  Duration: {run_result['duration_seconds']}s")
    print(f"  Output: {out_path}")
    print(f"{'='*60}")

    # Also print to stdout as JSON for programmatic consumption
    if args.json:
        print(json.dumps(run_result, indent=2))


def cmd_validate(args):
    """Validate golden_set.yaml schema only."""
    root = _find_workspace_root()
    gs_path = _golden_set_path(root)

    try:
        data = load_golden_set(gs_path)
        print(f"Valid: {len(data['cases'])} cases, version {data['version']}")
        for case in data["cases"]:
            evs = case.get("evaluators", [])
            tier = case.get("tier", "active")
            print(f"  {case['id']:6} [{tier:6}] {','.join(evs):20} {case['title'][:50]}")
    except Exception as e:
        print(f"INVALID: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="SwarmAI OS Eval Runner")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Execute eval cases")
    run_p.add_argument("--trigger", required=True, help="Trigger type: manual|weekly|monthly|steering_edit|model_change")
    run_p.add_argument("--cases", help="Comma-separated case IDs to run (default: all)")
    run_p.add_argument("--json", action="store_true", help="Print full JSON to stdout")

    sub.add_parser("validate", help="Validate golden_set.yaml schema")

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    elif args.command == "validate":
        cmd_validate(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
