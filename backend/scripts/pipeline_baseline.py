"""Build the Pipeline v1 speed / token reference lines from completed runs (fixing spec D2).

The reference line of a profile is the p95 of its completed runs (design §E9.3): a line
to compare against, never a timeout. Wall time is created_at -> completed_at of each
completed run.json; tokens are taken only from a measured v1 run audit
(``run_audit.json`` summary.token_cost.total_tokens) — legacy runs carry estimates, not
measurements, so they give no token line (the audit then shows "no baseline").
Profiles with fewer than ``MIN_RUNS`` completed runs get null.

Usage: python backend/scripts/pipeline_baseline.py [--workspace DIR] [--out FILE]
Read-only over the runs; writes only --out (default core/run_protocol/baselines.json).
After 10 v1 runs, re-run it so the lines come from v1's own data (XG 2026-10-22).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
MIN_RUNS = 5
PROFILES = ("full", "bugfix", "trivial", "direct", "research")


def _epoch(value) -> "float | None":
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def p95(values: list[float]) -> "float | None":
    """Nearest-rank p95; None for fewer than MIN_RUNS values."""
    if len(values) < MIN_RUNS:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def collect(workspace: Path) -> dict:
    seconds: dict[str, list[float]] = {p: [] for p in PROFILES}
    tokens: dict[str, list[float]] = {p: [] for p in PROFILES}
    for path in workspace.glob("Projects/*/.artifacts/runs/run_*/run.json"):
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        profile = run.get("profile")
        if profile not in seconds or run.get("status") != "completed":
            continue
        # a terminal v1 projection's updated_at is its end
        start = _epoch(run.get("created_at"))
        end = _epoch(run.get("updated_at"))
        if start is not None and end is not None and end > start:
            seconds[profile].append(end - start)
        try:
            audit = json.loads((path.parent / "run_audit.json").read_text(encoding="utf-8"))
            cost = audit["summary"]["token_cost"]
            total = cost["total_tokens"]
            # only a whole-session measurement (calls known), never expert-only totals
            if isinstance(total, (int, float)) and total > 0 \
                    and cost.get("total_calls") is not None:
                tokens[profile].append(float(total))
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return {"schema": 1, "statistic": "p95", "min_runs": MIN_RUNS,
            "profiles": {p: {"budget_seconds": (round(v) if (v := p95(seconds[p])) else None),
                             "budget_tokens": (round(v) if (v := p95(tokens[p])) else None),
                             "runs_seconds": len(seconds[p]), "runs_tokens": len(tokens[p])}
                         for p in PROFILES}}


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline_baseline.py")
    ap.add_argument("--workspace", default=str(Path.home() / ".swarm-ai" / "SwarmWS"))
    ap.add_argument("--out", default=str(_BACKEND / "core" / "run_protocol" / "baselines.json"))
    ns = ap.parse_args(argv)
    value = collect(Path(ns.workspace))
    Path(ns.out).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(value["profiles"], sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
