#!/usr/bin/env python3
"""ci_eval_gate.py — the git-bound eval gate (run_69b1c644 Cycle 4).

A PURE CHECK: it does NOT run eval. It verifies the deciding eval report (the newest,
by trigger time, whose probes' negative checks ran; a newer report on the same code
can still veto with a red line or a red BVT) is (a) FRESH — its code_digest matches the current eval-relevant code+golden_set,
and (b) RELEASABLE — its release decision (core/eval_release: red line, then gate
cases per dimension, then the denominator) says release, and its bvt block is green.
Exit 0 = pass (safe to push/build), exit 1 = blocked (stale, a failing check, or
red), exit 2 = no report / cannot verify (also a report written before the release
decision existed). There is no score and no score drift: a regression in a gate case
already fails the release decision, and a watch case never blocks.

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

def _trigger_time(report: dict) -> float:
    """When the run was triggered (``triggered_at``), with the eval service's rules
    (core.eval_service._run_time): a naive value is UTC, an unreadable or missing one
    sorts last. Not the file's mtime: that moves on a copy or a touch."""
    from datetime import datetime, timezone

    try:
        d = datetime.fromisoformat(str(report.get("triggered_at")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return float("-inf")
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def _reports_newest_first(root: Path) -> list[dict]:
    """All parseable reports, newest first by trigger time; latest = result[0]."""
    hist = _eval_history_dir(root)
    reports: list[dict] = []
    for p in hist.glob("*.json"):
        try:
            reports.append(json.loads(p.read_text()))
        except Exception:
            continue
    reports.sort(key=_trigger_time, reverse=True)
    return reports


def _latest_report(root: Path) -> dict | None:
    reports = _reports_newest_first(root)
    return reports[0] if reports else None


def check_gate(root: Path) -> tuple[int, str]:
    """Returns (exit_code, message). 0=pass, 1=blocked, 2=cannot-verify."""
    reports = _reports_newest_first(root)  # read history ONCE; reports[0] = latest
    if not reports:
        return 2, "GATE: no eval report found — run `eval_runner.py run` locally first."
    current_digest = compute_code_digest(root)

    # The gate decides on the newest report whose probes' negatives ran: only such a
    # report shows each probe can fail (a probe that passes whatever the code does
    # would pass here too). Run Canary and the post-deploy check skip negatives (the
    # sync route is deadline-bound), so they never decide. A newer report on the
    # same code still vetoes: a red line or a red BVT it saw blocks.
    strong_at = next((i for i, r in enumerate(reports) if r.get("negatives_ran") is True), None)
    for newer in reports[:strong_at]:
        if newer.get("code_digest") != current_digest:
            continue
        if (newer.get("redline") or {}).get("violated"):
            ids = ", ".join(v.get("id", "?") for v in newer["redline"].get("violations", []))
            return 1, (f"GATE BLOCKED (RED-LINE): a newer run ({newer.get('triggered_by', '?')}) "
                       f"saw zero-tolerance case(s) fail [{ids}] on this code.")
        nb = newer.get("bvt")
        if isinstance(nb, dict) and nb.get("green") is False:
            return 1, (f"GATE BLOCKED (red): a newer run ({newer.get('triggered_by', '?')}) "
                       f"on this code has a red BVT: passed={nb.get('passed')} "
                       f"failed={nb.get('failed')} error={nb.get('error')} "
                       f"timeout={nb.get('timeout')}.")
    if strong_at is None:
        return 2, ("GATE: no report ran the probes' negative checks (Run Canary and the "
                   "post-deploy check skip them) — run `eval_runner.py run` (negatives on "
                   "by default) or a full run.")
    report = reports[strong_at]

    bvt = report.get("bvt")
    if not bvt:
        return 2, ("GATE: the deciding report predates the gate (no bvt block) — "
                   "re-run eval to produce a gate-readable report.")

    # Freshness: does the report's digest match the CURRENT inputs?
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
                   f"NO-GO whatever the other cases do. "
                   f"Fix before push/build; red-line cases cannot be waived.")

    # Green: bvt must be a non-empty, all-pass set.
    if not bvt.get("green"):
        return 1, (f"GATE BLOCKED (red): bvt total={bvt.get('total')} "
                   f"passed={bvt.get('passed')} failed={bvt.get('failed')} "
                   f"error={bvt.get('error')} — fix failing cases before push/build.")

    # A report written before the release decision existed cannot be read for one.
    # Checked after the red-line veto and the BVT, so a red line or a red BVT still
    # blocks (exit 1) whatever else the report lacks.
    release = report.get("release")
    if not isinstance(release, dict) or not release.get("verdict"):
        return 2, ("GATE: the deciding report predates the release gate (no release block) — "
                   "re-run eval to produce a report with a release decision.")

    # The release decision, recomputed over EVERY red-line and gate case of the
    # current golden set against this report's results: a run limited to some cases
    # (--cases / --tags) cannot stand in for the rest, because a case it did not run
    # counts as missing in the denominator. The first failing check blocks and is
    # named. (Freshness above guarantees the golden set is the one the report ran.)
    from core.eval_release import decide_release, failing_check, tier_of
    from scripts.eval_runner import load_merged_golden_set

    gating = [c for c in load_merged_golden_set(root).get("cases", [])
              if tier_of(c) in ("red", "gate")]
    release = decide_release(gating, report.get("cases") or []) if gating else release
    if release.get("verdict") != "release":
        ck = failing_check(release) or {}
        return 1, (f"GATE BLOCKED (release {release.get('verdict')} at check "
                   f"{ck.get('id', '?')}): {release.get('reason', '')}")

    return 0, (f"GATE PASS (eval = push-gate #3/3): fresh (digest={report_digest}) "
               f"+ release ({release.get('reason', '')}) "
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
