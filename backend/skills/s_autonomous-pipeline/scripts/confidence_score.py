#!/usr/bin/env python3
"""Deterministic confidence scoring for pipeline delivery gate.

Two modes:
1. --run-dir: reads run.json + co-located artifact JSONs from a directory
2. --evaluation/--changeset/--review/--test-report: explicit artifact file paths

Mode 2 is the primary path — artifacts live in the global .artifacts/ registry,
not co-located with run.json. The agent discovers them via artifact_cli.py.

Usage:
    # Mode 1: all artifacts in one directory (tests, simple cases)
    python scripts/confidence_score.py --run-dir /path/to/run_dir/

    # Mode 2: explicit paths (production — agent discovers via artifact_cli)
    python scripts/confidence_score.py --run-dir /path/to/run_dir/ \
        --evaluation /path/to/evaluation.json \
        --changeset /path/to/changeset.json \
        --review /path/to/review.json \
        --test-report /path/to/test_report.json

Output (JSON):
    {
        "score": 9,
        "max_possible": 10,
        "flag_for_review": false,
        "breakdown": [
            {"rule": "acceptance_criteria_tested", "points": 3, "detail": "3/3 criteria have tests"}
        ],
        "penalties": [
            {"rule": "no_probes", "points": -2, "detail": "new endpoint but probes == 0"}
        ]
    }
"""
import argparse
import json
import os
import sys


def _load_json(path: str) -> dict | None:
    """Load a JSON file, return None if missing or invalid."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _has_frontend_files(files_changed: list[str]) -> bool:
    """Check if changeset includes frontend files."""
    frontend_exts = {".tsx", ".jsx", ".ts", ".js", ".css", ".html", ".svelte", ".vue"}
    return any(os.path.splitext(f)[1] in frontend_exts for f in files_changed)


def _has_backend_files(files_changed: list[str]) -> bool:
    """Check if changeset includes backend files."""
    backend_exts = {".py", ".go", ".rs", ".java"}
    return any(os.path.splitext(f)[1] in backend_exts for f in files_changed)


def calculate_score(
    run_dir: str,
    evaluation_path: str | None = None,
    changeset_path: str | None = None,
    review_path: str | None = None,
    test_report_path: str | None = None,
) -> dict:
    """Calculate confidence score from pipeline run artifacts.

    Artifacts are loaded from explicit paths (if given) or from co-located
    files in run_dir (fallback for tests and simple cases).
    """
    run = _load_json(os.path.join(run_dir, "run.json")) or {}
    evaluation = _load_json(evaluation_path) if evaluation_path else _load_json(os.path.join(run_dir, "evaluation.json"))
    changeset = _load_json(changeset_path) if changeset_path else _load_json(os.path.join(run_dir, "changeset.json"))
    review = _load_json(review_path) if review_path else _load_json(os.path.join(run_dir, "review.json"))
    test_report = _load_json(test_report_path) if test_report_path else _load_json(os.path.join(run_dir, "test_report.json"))

    breakdown = []
    penalties = []

    # --- Positive criteria ---

    # +3: all acceptance criteria have passing tests
    if evaluation and test_report:
        criteria_count = len(evaluation.get("acceptance_criteria", []))
        passed = test_report.get("passed", 0)
        if criteria_count > 0 and passed > 0:
            breakdown.append({
                "rule": "acceptance_criteria_tested",
                "points": 3,
                "detail": f"{criteria_count} criteria, {passed} tests pass",
            })

    # +2: review found 0 critical issues
    if review:
        findings = review.get("findings", [])
        critical = [f for f in findings if f.get("severity") in ("critical", "high")]
        if len(critical) == 0:
            breakdown.append({
                "rule": "review_clean",
                "points": 2,
                "detail": f"0 critical/high findings ({len(findings)} total)",
            })

    # +2: TDD red-green cycle completed cleanly
    tdd = (changeset or {}).get("tdd", {})
    if tdd.get("green_pass"):
        breakdown.append({
            "rule": "tdd_clean",
            "points": 2,
            "detail": "TDD green_pass == true",
        })

    # +1: no taste decisions overridden
    taste_decisions = run.get("taste_decisions", [])
    overridden = [t for t in taste_decisions if t.get("overridden")]
    if len(overridden) == 0:
        breakdown.append({
            "rule": "no_taste_overrides",
            "points": 1,
            "detail": f"{len(taste_decisions)} taste decisions, 0 overridden",
        })

    # +1: zero regressions
    regressions = tdd.get("regressions", 0)
    if regressions == 0:
        breakdown.append({
            "rule": "zero_regressions",
            "points": 1,
            "detail": "0 regressions on existing tests",
        })

    # +1: design_doc was available
    stages = run.get("stages", [])
    plan_done = any(
        s.get("name") == "plan" and s.get("status") == "complete" for s in stages
    )
    if plan_done:
        breakdown.append({
            "rule": "design_doc_available",
            "points": 1,
            "detail": "PLAN stage completed — design doc available",
        })

    # --- Penalties ---

    files_changed = (changeset or {}).get("files_changed", [])
    multi_file = len(files_changed) > 1

    # -2: acceptance criterion lacks test
    if evaluation and test_report:
        criteria_count = len(evaluation.get("acceptance_criteria", []))
        passed = test_report.get("passed", 0)
        if criteria_count > 0 and passed < criteria_count:
            penalties.append({
                "rule": "acceptance_gap",
                "points": -2,
                "detail": f"{passed} tests for {criteria_count} criteria",
            })

    # -2: WTF gate triggered
    if test_report and test_report.get("wtf_score", 0) >= 5:
        penalties.append({
            "rule": "wtf_triggered",
            "points": -2,
            "detail": f"WTF score = {test_report['wtf_score']}",
        })

    # -2: smoke_tests == 0 and files_changed > 1
    if multi_file and tdd.get("smoke_tests", 0) == 0:
        penalties.append({
            "rule": "no_smoke_tests",
            "points": -2,
            "detail": f"{len(files_changed)} files changed, 0 smoke tests",
        })

    # -2: user_path_traces == 0 and files_changed > 1
    if multi_file and tdd.get("user_path_traces", 0) == 0:
        penalties.append({
            "rule": "no_user_path_traces",
            "points": -2,
            "detail": f"{len(files_changed)} files changed, 0 user-path traces",
        })

    # -1: integration_trace.checked == 0
    it = (review or {}).get("integration_trace", {})
    if it.get("checked", 0) == 0:
        penalties.append({
            "rule": "no_integration_trace",
            "points": -1,
            "detail": "integration trace not run",
        })

    # -1: frontend changed but ux_review not triggered
    if _has_frontend_files(files_changed):
        ux = (review or {}).get("ux_review", {})
        if not ux.get("triggered", False):
            penalties.append({
                "rule": "no_ux_review",
                "points": -1,
                "detail": "frontend files changed, UX review not triggered",
            })

    # -1: runtime_patterns.checked == 0
    rp = (review or {}).get("runtime_patterns", {})
    if rp.get("checked", 0) == 0:
        penalties.append({
            "rule": "no_runtime_patterns",
            "points": -1,
            "detail": "runtime pattern checklist not run",
        })

    # -2: frontend+backend changed but wire_test == 0
    if _has_frontend_files(files_changed) and _has_backend_files(files_changed):
        wt = (review or {}).get("wire_test", {})
        if wt.get("boundaries", 0) == 0:
            penalties.append({
                "rule": "no_wire_test",
                "points": -2,
                "detail": "frontend+backend changed, 0 wire tests",
            })

    # -2: new endpoint + probes == 0 (only when frontend consumes it)
    if _has_frontend_files(files_changed) and _has_backend_files(files_changed):
        if tdd.get("probes", 0) == 0:
            penalties.append({
                "rule": "no_probes",
                "points": -2,
                "detail": "cross-layer change but 0 probes",
            })

    # Calculate final score
    positive = sum(item["points"] for item in breakdown)
    negative = sum(item["points"] for item in penalties)
    score = max(1, positive + negative)  # Clamp minimum to 1

    return {
        "score": score,
        "max_possible": 10,
        "flag_for_review": score < 7,
        "breakdown": breakdown,
        "penalties": penalties,
    }


def main():
    parser = argparse.ArgumentParser(description="Pipeline confidence scoring")
    parser.add_argument("--run-dir", required=True, help="Path to pipeline run directory")
    parser.add_argument("--evaluation", help="Path to evaluation artifact JSON")
    parser.add_argument("--changeset", help="Path to changeset artifact JSON")
    parser.add_argument("--review", help="Path to review artifact JSON")
    parser.add_argument("--test-report", help="Path to test_report artifact JSON")
    args = parser.parse_args()

    result = calculate_score(
        args.run_dir,
        evaluation_path=args.evaluation,
        changeset_path=args.changeset,
        review_path=args.review,
        test_report_path=args.test_report,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
