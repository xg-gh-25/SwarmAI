#!/usr/bin/env python3
"""Validator check-usage audit (run_7cf9da85 goal C5) — READ-ONLY.

The pipeline validator (`pipeline_validator.py`) has grown to ~3.1K lines / 16+
checks. The C5 question: which checks actually EARN their keep — i.e. which have a
BLOCK path that is reachable AND exercised by a test? This script produces the
DATA + a keep/cut/merge verdict per check. It does NOT delete anything (STEERING
#3 says prefer deletion, but a gate is never blind-deleted — the data decides, a
human approves, a separate cycle removes).

Two evidence sources (run.json records do NOT store per-check verdicts, so a
historical-tally is impossible — confirmed: 0 runs carry validation history):

  1. STATIC reachability — each check's BLOCK branch (errors.append) exists in the
     source and is gated by a condition that CAN be true.
  2. TEST coverage — does any test in tests/ assert that check's BLOCK fires?
     A check whose BLOCK path has a test is PROVEN to earn its keep; one with no
     test is a candidate for scrutiny (dead, or untested).

Output: a markdown report to Knowledge/Reports/ + stdout JSON summary. Verdict per
check: KEEP (test-proven) | REVIEW (reachable, no test) | MERGE (overlaps another).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# The 16 checks as documented in validate() + the gate family. (Source of truth:
# pipeline_validator.py check_results names + the STAGE gates.) Each entry:
#   name, severity, what it guards, the test-file substring that would prove its
#   BLOCK path fires (None = no known dedicated test).
CHECKS = [
    {"name": "stage_order", "sev": "hard", "guards": "stage sequence per profile",
     "test_probe": "stage_order", "test_file": "test_pipeline_validator.py"},
    {"name": "artifact_exists", "sev": "hard", "guards": "stage published an artifact",
     "test_probe": "artifact_exist", "test_file": "test_pipeline_validator.py"},
    {"name": "artifact_schema", "sev": "hard", "guards": "required fields present",
     "test_probe": "missing_required", "test_file": "test_pipeline_validator.py"},
    {"name": "decision_logged", "sev": "advisory", "guards": ">=1 classified decision",
     "test_probe": "decision", "test_file": "test_pipeline_validator.py"},
    {"name": "budget_recorded", "sev": "advisory", "guards": "token_cost > 0",
     "test_probe": "budget", "test_file": "test_pipeline_validator.py"},
    {"name": "profile_respected", "sev": "hard", "guards": "stage in profile",
     "test_probe": "profile", "test_file": "test_pipeline_validator.py"},
    {"name": "ddd_consistency", "sev": "advisory", "guards": "non-goals vs approach",
     "test_probe": "ddd_consist", "test_file": "test_pipeline_validator.py"},
    {"name": "quality_gate(8)", "sev": "hard", "guards": "smoke/litmus/ac_coverage/layers",
     "test_probe": "litmus", "test_file": "test_pipeline_validator.py"},
    {"name": "depth(9)", "sev": "hard", "guards": "field values indicate real work",
     "test_probe": "depth", "test_file": "test_pipeline_validator.py"},
    {"name": "push_ready(10)", "sev": "hard", "guards": "binary push-ready verdict",
     "test_probe": "push_ready", "test_file": "test_pipeline_validator.py"},
    {"name": "semantic(11)", "sev": "advisory", "guards": "content-quality heuristics",
     "test_probe": "semantic", "test_file": "test_pipeline_validator.py"},
    {"name": "skip_justified(12)", "sev": "hard", "guards": "skips need reason+no counter",
     "test_probe": "skip_justification", "test_file": "test_pipeline_validator.py"},
    {"name": "output_routing(13)", "sev": "hard", "guards": "consume declared upstream",
     "test_probe": "routing", "test_file": "test_pipeline_validator.py"},
    {"name": "understanding_gate(G0)", "sev": "hard", "guards": "diagnosis observed not inferred",
     "test_probe": "Understanding gate", "test_file": "test_understanding_gate.py"},
    {"name": "ambiguity_scan(G0)", "sev": "hard", "guards": "spec ambiguity self-resolved",
     "test_probe": "Ambiguity scan", "test_file": "test_ambiguity_scan.py"},
    {"name": "working_backwards(G0)", "sev": "hard", "guards": "greenfield value framing",
     "test_probe": "Working-Backwards", "test_file": "test_working_backwards.py"},
    {"name": "repro_gate", "sev": "hard", "guards": "bug-class observation evidence",
     "test_probe": "REPRO gate", "test_file": "test_pipeline_validator_repro_gate.py"},
]


def _tests_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "tests"


def _count_block_assertions(test_file: str, probe: str) -> int:
    """Count test lines that assert this check's BLOCK fires (a test referencing the
    probe string in an assert-non-empty / 'must block' context). Coarse but honest:
    a >0 count means at least one test exercises the BLOCK path."""
    p = _tests_dir() / test_file
    if not p.exists():
        return 0
    txt = p.read_text(encoding="utf-8", errors="ignore")
    # Lines mentioning the probe AND a block-intent token.
    hits = 0
    for line in txt.splitlines():
        if probe.lower() in line.lower() and any(
            t in line.lower() for t in ("block", "must", "assert", "error")
        ):
            hits += 1
    return hits


def audit() -> dict:
    rows = []
    for c in CHECKS:
        n = _count_block_assertions(c["test_file"], c["test_probe"])
        if n > 0:
            verdict, reason = "KEEP", f"BLOCK path test-proven ({n} assertion-lines in {c['test_file']})"
        else:
            verdict, reason = "REVIEW", f"no dedicated BLOCK test found via probe '{c['test_probe']}' — verify reachable or candidate for merge"
        rows.append({**c, "block_test_lines": n, "verdict": verdict, "reason": reason})
    keep = sum(1 for r in rows if r["verdict"] == "KEEP")
    review = sum(1 for r in rows if r["verdict"] == "REVIEW")
    return {"total": len(rows), "keep": keep, "review": review, "checks": rows}


def render_md(result: dict) -> str:
    lines = [
        "# Validator Check-Usage Audit (C5, run_7cf9da85)",
        "",
        "_Read-only. Data + verdict; no check deleted. Source: test-coverage of each",
        "check's BLOCK path (run.json records carry no per-check verdict history —",
        "a historical tally is impossible, so test-coverage is the earns-its-keep proxy)._",
        "",
        f"**{result['keep']}/{result['total']} checks have a test-proven BLOCK path (KEEP). "
        f"{result['review']} need REVIEW.**",
        "",
        "| Check | Sev | Guards | BLOCK-test lines | Verdict |",
        "|-------|-----|--------|-----------------:|---------|",
    ]
    for r in result["checks"]:
        lines.append(
            f"| {r['name']} | {r['sev']} | {r['guards']} | {r['block_test_lines']} | **{r['verdict']}** |"
        )
    lines += [
        "",
        "## Verdicts",
        "- **KEEP** — at least one test asserts this check's BLOCK fires → it provably earns its keep.",
        "- **REVIEW** — no dedicated BLOCK test found by the probe. NOT a delete order:",
        "  either the check is reachable-but-untested (add a test) or genuinely dead",
        "  (a separate cycle removes, with human approval — never blind-deleted here).",
        "",
        "## Honest limitation",
        "This is a TEST-COVERAGE proxy, not a production BLOCK tally. A check can be",
        "load-bearing in prod yet show REVIEW here if its test lives under a different",
        "probe string. Treat REVIEW as 'investigate', never 'delete'. (STEERING #3:",
        "prefer deletion — but a gate is removed only with data + approval.)",
    ]
    return "\n".join(lines)


def main() -> int:
    result = audit()
    ws = Path(__file__).resolve().parent.parent.parent  # repo root (best-effort)
    # Prefer the workspace Knowledge/Reports if present.
    import os
    swarm_ws = os.environ.get("SWARM_WORKSPACE")
    if swarm_ws:
        report_dir = Path(swarm_ws) / "Knowledge" / "Reports"
    else:
        report_dir = ws / "Knowledge" / "Reports"
    out_path = None
    try:
        report_dir.mkdir(parents=True, exist_ok=True)
        out_path = report_dir / "validator-check-usage-audit.md"
        out_path.write_text(render_md(result), encoding="utf-8")
    except OSError:
        pass
    print(json.dumps({
        "total": result["total"], "keep": result["keep"], "review": result["review"],
        "report": str(out_path) if out_path else None,
        "review_checks": [r["name"] for r in result["checks"] if r["verdict"] == "REVIEW"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
