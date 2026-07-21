#!/usr/bin/env python3
"""ci_eval_gate.py — the git-bound eval gate (run_69b1c644 Cycle 4).

A PURE CHECK: it does NOT run eval. It verifies the latest committed eval report
is (a) FRESH — its code_digest matches the current eval-relevant code+golden_set,
and (b) GREEN — its bvt block passed. Exit 0 = pass (safe to push/build), exit 1
= blocked (stale or red), exit 2 = no report / cannot verify.

Mounts:
- locally / CI: `python backend/scripts/ci_eval_gate.py` before push
- s_swarm-build Stage 1 (deferred — needs XG sign-off, touches deploy path)

Push gate (STEERING #5 — "Push 门禁是质量不是审批"): `git push origin main` is the
normal flow and needs no per-push user sign-off, BUT push is only allowed when the
working tree is fully green on THREE checks — any one unrun or red blocks the push:
  1. Build — `./prod.sh build` (backend changes) and/or
             `cd desktop && npm run build:all` (frontend changes), per what changed.
  2. Tests — at least the affected suites (pytest / vitest), wrapped per AGENT R9
             (`perl -e 'alarm'` / `gtimeout`).
  3. Eval  — THIS script: `cd backend && python scripts/ci_eval_gate.py`.
This module is gate #3. The gate is QUALITY, not approval: it does not ask "did the
user okay it?" but "did eval run against this exact code and pass?"

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

# Score-drift gate band (run_95d9acbc). A push is BLOCKED when the latest report's
# overall_score dropped MORE than this below the most-recent DIFFERENT-code baseline.
# Strict: a drop of exactly EPSILON passes; only > EPSILON blocks.
# NOTE the deliberate asymmetry with the scheduled job's alert band
# (eval_scheduled.py _DRIFT_TOLERANCE = 5.0): the ALERT is a noisy-band monitor
# (don't Slack-spam on judge noise); this GATE is the strict push-blocker (2.0).
# Different jobs, different jobs-to-be-done — the divergence is intentional.
SCORE_DRIFT_EPSILON = 2.0


def _reports_by_mtime(root: Path) -> list[dict]:
    """All parseable reports, NEWEST FIRST by mtime — the SAME ordering key as
    _latest_report (NOT eval_runner._load_history's filename sort, which can
    disagree when two filename formats coexist; Gate-1 run_95d9acbc). Returns the
    parsed dicts so latest = result[0]."""
    hist = _eval_history_dir(root)
    out: list[dict] = []
    for p in sorted(hist.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            continue
    return out


def _latest_report(root: Path) -> dict | None:
    reports = _reports_by_mtime(root)
    return reports[0] if reports else None


def _num(v) -> float | None:
    """A score is usable only if it's a real number — a malformed report with a
    string/None score must fail-OPEN (skip), never crash the gate (Gate-2 NIT#1)."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _score_drift(reports: list[dict]) -> tuple[bool, str]:
    """(blocked, message). `reports` is _reports_by_mtime() (newest-first); reports[0]
    is the latest (the gated report — same object check_gate validated, no re-read /
    no implicit [0]==latest coupling, Gate-2 NIT#7 + REVIEW LOW). Compare latest's
    score against the most-recent report with a DIFFERENT code_digest (a same-code
    re-run is judge noise, not drift — proven on real data: 100.0→91.7 on identical
    digest). Fail-OPEN (never block) when: <2 reports, latest has no numeric score,
    or no different-code baseline with a numeric score. Only a real, different-code
    regression beyond EPSILON blocks."""
    if len(reports) < 2:
        return False, ""  # no baseline — fail-open
    latest = reports[0]
    latest_score = _num(latest.get("overall_score"))
    if latest_score is None:
        return False, ""  # pre-score / malformed latest — can't judge, fail-open
    latest_digest = latest.get("code_digest", "")
    for rpt in reports[1:]:
        if rpt.get("code_digest", "") == latest_digest:
            continue  # same-code re-run — not a drift baseline
        base_score = _num(rpt.get("overall_score"))
        if base_score is None:
            continue
        if (base_score - latest_score) > SCORE_DRIFT_EPSILON:
            return True, (f"GATE BLOCKED (score drift): {base_score} → {latest_score} "
                          f"(drop {base_score - latest_score:.1f} > {SCORE_DRIFT_EPSILON} "
                          f"vs different-code baseline) — capability regressed, "
                          f"investigate before push.")
        return False, ""  # found a real baseline, within band → pass
    return False, ""  # no different-code baseline → fail-open


def check_gate(root: Path) -> tuple[int, str]:
    """Returns (exit_code, message). 0=pass, 1=blocked, 2=cannot-verify."""
    reports = _reports_by_mtime(root)  # read history ONCE; reports[0] = latest
    report = reports[0] if reports else None
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

    # Red-line veto (run_21490939): a zero-tolerance case that FAILED/ERRORED
    # blocks the push regardless of bvt.green or the aggregate %. This is the
    # SEVERITY-keyed gate bvt is not — bvt skips every eval_method=='llm' case, so
    # a semantic red-line (refusal/political/tone) is invisible to it. Checked
    # AFTER freshness (a stale report can't be trusted to assert a red-line either
    # way) and BEFORE bvt (a red-line violation is strictly more severe). Absent
    # block (report predates this gate) → fail-open, fall through to bvt.
    redline = report.get("redline")
    if redline and redline.get("violated"):
        ids = ", ".join(v.get("id", "?") for v in redline.get("violations", []))
        return 1, (f"GATE BLOCKED (RED-LINE): {len(redline.get('violations', []))} "
                   f"zero-tolerance case(s) failed [{ids}] — a red-line failure is "
                   f"NO-GO independent of the {report.get('overall_score', '?')}% score. "
                   f"Fix before push/build; red-line cases cannot be waived.")

    # Green: bvt must be a non-empty, all-pass set.
    if not bvt.get("green"):
        return 1, (f"GATE BLOCKED (red): bvt total={bvt.get('total')} "
                   f"passed={bvt.get('passed')} failed={bvt.get('failed')} "
                   f"error={bvt.get('error')} — fix failing cases before push/build.")

    # Score drift: capability regressed vs the most-recent different-code baseline.
    drifted, drift_msg = _score_drift(reports)
    if drifted:
        return 1, drift_msg

    return 0, (f"GATE PASS (eval = push-gate #3/3): fresh (digest={report_digest}) "
               f"+ green (bvt {bvt.get('passed')}/{bvt.get('total')}). "
               f"Confirm Build + Tests green too before `git push origin main`.")


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
