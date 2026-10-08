#!/usr/bin/env python3
"""Compute SwarmAI's AI Autonomous Rate from pipeline run records (read-only).

Autonomous Rate (AIDLC canonical, see Projects/AIDLC/.../TECH.md § Key Metrics):
the share of DELIVERED pipeline runs that completed end-to-end WITHOUT a
human stepping in on a judgment-class decision or a code-fix. This is the
RETROSPECTIVE, run-grained metric — the authoritative source is the run record,
never a self-reported label on a commit (design §1-§4).

Definitions (design §4):
  delivered  = run.status == "completed" AND len(commits) >= 1
               (abandoned/failed/running excluded; a completed run that produced
                no commit is not a delivery; a manual commit outside any run is
                never in the denominator — it belongs to no run.json)
  autonomous = a delivered run with ZERO judgment-class interventions AND ZERO
               codefix interventions. unblock/approval are human touches but, per
               the AIDLC definition's clarification ②, only JUDGMENT-class
               interventions (and human code-fixes) subtract autonomy.
  rate       = autonomous / delivered  (None when delivered == 0 — 0/0 undefined)

Measurement floor (design §4.2 honesty clause): intervention capture is
checkpoint-primary + commit-files-cross-diff, so the reported rate is an UPPER
bound on true autonomy (reality <= reported, never >) — every unobserved
intervention inflates the rate, never deflates it. Four distinct holes make a
human intervention invisible, ALL biasing the same direction:
  1. a human edit overwritten AND never committed (no commit-cross-diff signal);
  2. a human edit confined to already-recorded files_touched (cross-diff sees
     nothing extra) with no checkpoint raised;
  3. a judgment pause whose free-text reason omits a trigger keyword
     ('judgment'/'l2'/'block'/'external') → classified systemic → not recorded;
  4. legacy runs predating the interventions[] field (no record at all).
This is SURFACED (the warning always prints — see main()), not hidden. The honest
reading: a reported rate R means "autonomy is AT MOST R".

Usage:
    python backend/scripts/autonomous_rate.py                 # all projects
    python backend/scripts/autonomous_rate.py --project SwarmAI
    python backend/scripts/autonomous_rate.py --json          # machine-readable
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

# Interventions that subtract autonomy (design §4): a human judgment decision,
# or a human code-fix. unblock/approval are logged but non-disqualifying.
_DISQUALIFYING_KINDS = frozenset({"judgment", "codefix"})


def _workspace_root() -> Path:
    """Resolve the SwarmWS workspace root (where Projects/ lives)."""
    env = os.environ.get("SWARM_WS") or os.environ.get("SWARMAI_WORKSPACE")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".swarm-ai" / "SwarmWS"


def _iter_run_files(runs_root: Path):
    """Yield each run.json under a runs root (one project's .artifacts/runs)."""
    if not runs_root.exists():
        return
    for run_dir in sorted(runs_root.iterdir()):
        rf = run_dir / "run.json"
        if rf.is_file():
            yield rf


def compute_autonomous_rate(runs_root: Path) -> dict:
    """Compute the autonomous rate over all run.json under one runs root.

    runs_root: a directory of <run_id>/run.json (e.g. Projects/X/.artifacts/runs).
    Returns a dict: {rate, delivered, autonomous, by_kind, by_avoidable,
    measurement_floor, delivered_total_runs_scanned}.
    """
    delivered = 0
    autonomous = 0
    scanned = 0
    legacy_no_field = 0  # delivered runs with NO interventions field (lower-bound)
    by_kind: dict[str, int] = {}
    by_avoidable = {"avoidable": 0, "unavoidable": 0}

    for rf in _iter_run_files(Path(runs_root)):
        try:
            d = json.loads(rf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        scanned += 1
        status = d.get("status")
        commits = d.get("commits") or []
        is_delivered = status == "completed" and len(commits) >= 1
        if not is_delivered:
            continue
        delivered += 1

        has_field = "interventions" in d
        interventions = d.get("interventions") or []
        if not has_field:
            legacy_no_field += 1

        disqualified = False
        for iv in interventions:
            if not isinstance(iv, dict):
                continue
            kind = iv.get("kind")
            if kind:
                by_kind[kind] = by_kind.get(kind, 0) + 1
            if iv.get("avoidable"):
                by_avoidable["avoidable"] += 1
            else:
                by_avoidable["unavoidable"] += 1
            if kind in _DISQUALIFYING_KINDS:
                disqualified = True
        if not disqualified:
            autonomous += 1

    rate = (autonomous / delivered) if delivered else None
    return {
        "rate": rate,
        "delivered": delivered,
        "autonomous": autonomous,
        "by_kind": by_kind,
        "by_avoidable": by_avoidable,
        "runs_scanned": scanned,
        "measurement_floor": {
            "note": "Reported rate is an UPPER bound (true <= reported). FOUR holes "
                    "all inflate (never deflate): (1) human edit overwritten & never "
                    "committed; (2) human edit confined to already-recorded "
                    "files_touched with no checkpoint; (3) judgment pause whose "
                    "free-text reason lacks a trigger keyword → classified systemic; "
                    "(4) legacy runs lacking the interventions field. A reported R "
                    "means autonomy is AT MOST R — this caveat holds for EVERY run, "
                    "not only legacy ones.",
            "delivered_runs_without_interventions_field": legacy_no_field,
        },
    }


def _project_runs_roots(project: "str | None") -> "list[tuple[str, Path]]":
    """Return [(project_name, runs_root)] for one project or all."""
    ws = _workspace_root()
    projects_dir = ws / "Projects"
    out: list[tuple[str, Path]] = []
    if project:
        out.append((project, projects_dir / project / ".artifacts" / "runs"))
    elif projects_dir.exists():
        for p in sorted(projects_dir.iterdir()):
            rr = p / ".artifacts" / "runs"
            if rr.exists():
                out.append((p.name, rr))
    return out


def _aggregate(project: "str | None") -> dict:
    """Compute per-project + combined autonomous rate."""
    per_project = {}
    tot_delivered = tot_autonomous = 0
    combined_kind: dict[str, int] = {}
    combined_avoid = {"avoidable": 0, "unavoidable": 0}
    combined_legacy = 0
    for name, rr in _project_runs_roots(project):
        res = compute_autonomous_rate(rr)
        if res["runs_scanned"] == 0:
            continue
        per_project[name] = res
        tot_delivered += res["delivered"]
        tot_autonomous += res["autonomous"]
        for k, v in res["by_kind"].items():
            combined_kind[k] = combined_kind.get(k, 0) + v
        combined_avoid["avoidable"] += res["by_avoidable"]["avoidable"]
        combined_avoid["unavoidable"] += res["by_avoidable"]["unavoidable"]
        combined_legacy += res["measurement_floor"]["delivered_runs_without_interventions_field"]
    combined_rate = (tot_autonomous / tot_delivered) if tot_delivered else None
    return {
        "combined": {
            "rate": combined_rate,
            "delivered": tot_delivered,
            "autonomous": tot_autonomous,
            "by_kind": combined_kind,
            "by_avoidable": combined_avoid,
            "delivered_runs_without_interventions_field": combined_legacy,
        },
        "per_project": per_project,
    }


def _fmt_rate(r) -> str:
    return "n/a (no delivered runs)" if r is None else f"{r * 100:.1f}%"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compute SwarmAI AI Autonomous Rate from run records.")
    ap.add_argument("--project", default=None, help="Scope to one project (default: all).")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = ap.parse_args(argv)

    agg = _aggregate(args.project)
    if args.json:
        print(json.dumps(agg, indent=2))
        return 0

    c = agg["combined"]
    print("═══ AI Autonomous Rate ═══")
    print(f"  Rate (UPPER bound): {_fmt_rate(c['rate'])}  ({c['autonomous']}/{c['delivered']} delivered runs)")
    if c["by_kind"]:
        print(f"  Interventions by kind:      {dict(sorted(c['by_kind'].items()))}")
        print(f"  Interventions by avoidable: {c['by_avoidable']}")
        # F5: surface unblock explicitly — a 100% rate can still involve human
        # unblocks (excluded from the headline by design: only judgment+codefix
        # subtract autonomy, per the AIDLC definition's clarification ②).
        unblocks = c["by_kind"].get("unblock", 0)
        if unblocks:
            print(f"  note: {unblocks} unblock(s) occurred (human cleared a block) — "
                  f"excluded from the headline rate by design, shown for honesty.")
    else:
        print("  Interventions: none recorded")
    # F3: the UPPER-bound caveat holds for EVERY run (overwritten-edit /
    # no-checkpoint / classifier-miss holes), not only legacy ones — ALWAYS print.
    legacy = c["delivered_runs_without_interventions_field"]
    print(f"  ⚠ measurement floor: reported rate is an UPPER bound (true ≤ reported) — "
          f"unobserved human intervention always inflates, never deflates.")
    if legacy:
        print(f"    ({legacy} of {c['delivered']} delivered run(s) predate the "
              f"interventions field → no record at all.)")
    if not args.project and agg["per_project"]:
        print("  ── per project ──")
        for name, res in agg["per_project"].items():
            print(f"    {name:20} {_fmt_rate(res['rate'])}  ({res['autonomous']}/{res['delivered']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
