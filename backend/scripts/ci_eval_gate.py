#!/usr/bin/env python3
"""ci_eval_gate.py — the git-bound eval gate (run_69b1c644 Cycle 4).

A PURE CHECK: it does NOT run eval. It verifies the latest committed eval report
is (a) FRESH — its code_digest matches the current eval-relevant code+golden_set,
and (b) GREEN — its bvt block passed. Exit 0 = pass (safe to push/build), exit 1
= blocked (stale or red), exit 2 = no report / cannot verify.

Mounts:
- locally / CI: `python backend/scripts/ci_eval_gate.py` before push
- s_swarm-build Stage 1 (deferred — needs XG sign-off, touches deploy path)

Why a CHECK not a RUN: zero Bedrock cost, runs anywhere, and the report is a
committed artifact (lock-file pattern). The freshness binding (code_digest over
INPUTS, not HEAD) is what makes "developer ran eval against THIS code" verifiable.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.eval_runner import (  # noqa: E402
    _eval_history_dir,
    _find_workspace_root,
    compute_code_digest,
)


def _latest_report(root: Path) -> dict | None:
    hist = _eval_history_dir(root)
    # Sort by mtime, NOT filename (Gate-2 H1): two filename formats coexist
    # ({date}_{trigger} and {date}_{time}_{trigger}) so lexical sort can rank a
    # stale same-day report above a fresher one. mtime is format-agnostic.
    reports = sorted(hist.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in reports:
        try:
            return json.loads(p.read_text())
        except Exception:
            continue
    return None


def check_gate(root: Path) -> tuple[int, str]:
    """Returns (exit_code, message). 0=pass, 1=blocked, 2=cannot-verify."""
    report = _latest_report(root)
    if report is None:
        return 2, "GATE: no eval report found — run `eval_runner.py run` locally first."

    bvt = report.get("bvt")
    if not bvt:
        return 2, ("GATE: latest report predates the gate (no bvt block) — "
                   "re-run eval to produce a gate-readable report.")

    # Freshness: does the report's digest match the CURRENT inputs?
    current_digest = compute_code_digest(root)
    report_digest = report.get("code_digest", "")
    if report_digest != current_digest:
        return 1, (f"GATE BLOCKED (stale): report code_digest={report_digest or 'none'} "
                   f"!= current={current_digest}. Eval-relevant code or golden_set "
                   f"changed since the last eval — re-run `eval_runner.py run`.")

    # Green: bvt must be a non-empty, all-pass set.
    if not bvt.get("green"):
        return 1, (f"GATE BLOCKED (red): bvt total={bvt.get('total')} "
                   f"passed={bvt.get('passed')} failed={bvt.get('failed')} "
                   f"error={bvt.get('error')} — fix failing cases before push/build.")

    return 0, (f"GATE PASS: fresh (digest={report_digest}) + green "
               f"(bvt {bvt.get('passed')}/{bvt.get('total')}).")


def main() -> int:
    ap = argparse.ArgumentParser(description="Git-bound eval gate (pure check).")
    ap.add_argument("--project", default="SwarmAI")  # reserved; single-project today
    args = ap.parse_args()
    try:
        root = _find_workspace_root()
    except Exception as e:
        print(f"GATE: cannot locate workspace: {e}", file=sys.stderr)
        return 2
    code, msg = check_gate(root)
    stream = sys.stderr if code else sys.stdout
    print(msg, file=stream)
    return code


if __name__ == "__main__":
    sys.exit(main())
