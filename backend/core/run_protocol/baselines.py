"""Speed / Token reference lines for a new run, from THIS install's own landed runs
(design 2026-10-26-pipeline-review-closure-and-visibility §6.2 point 1).

A reference line is the p95 of the same profile's most recent landed runs on this
machine: active seconds recomputed from each run's events with the audit's own rule,
and tokens (input + cache creation + output, cache reads apart) from runs that recorded
``cache_read_tokens`` (runs before it summed cache reads into input, which depends on
how long the host session already was). Fewer than ``MIN_RUNS`` runs: no line (the
audit then says N/A, not WARN). A line is a reference, never a limit (STEERING #2).
Nothing here ships per-machine numbers with the product.

Public: ``compute``, ``MIN_RUNS``.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

MIN_RUNS = 5
RECENT = 20        # the most recent landed runs of the profile that are used
SCAN = 200         # contracts read, newest first: reading does not grow with history


def _p95(values: list[float]) -> float | None:
    if len(values) < MIN_RUNS:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)], 3)


def _events(run_dir: Path) -> list[dict[str, Any]]:
    out = []
    try:
        with (run_dir / "events.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError:
        return []
    return out


def _landed(events: list[Mapping[str, Any]]) -> bool:
    return any(event.get("kind") == "land"
               and (event.get("payload") or {}).get("landing") == "landed" for event in events)


def compute(runs_root: Path, profile: str) -> dict[str, Any]:
    """``{budget_seconds, budget_tokens, baseline_runs}`` for a new *profile* run;
    ``baseline_runs`` is how many landed runs were read (the audit prints n/MIN_RUNS)."""
    from core.run_protocol.audit import active_seconds, token_totals
    try:
        dirs = [d for d in Path(runs_root).iterdir() if (d / "contract.json").is_file()]
    except OSError:
        dirs = []
    def mtime(d: Path) -> float:
        try:
            return (d / "contract.json").stat().st_mtime
        except OSError:
            return 0.0
    seconds: list[float] = []
    tokens: list[float] = []
    used = 0
    for run_dir in sorted(dirs, key=mtime, reverse=True)[:SCAN]:
        try:
            contract = json.loads((run_dir / "contract.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(contract, Mapping) or contract.get("profile") != profile:
            continue
        events = _events(run_dir)
        if not _landed(events):
            continue
        used += 1
        active = active_seconds(events)["active"]
        if isinstance(active, (int, float)):
            seconds.append(float(active))
        if any(event.get("kind") == "metric" and event.get("producer") == "cli"
               and (event.get("payload") or {}).get("name") == "cache_read_tokens"
               for event in events):
            spent, out = token_totals(events)
            if isinstance(spent, (int, float)) and isinstance(out, (int, float)):
                tokens.append(float(spent + out))
        if used >= RECENT:
            break
    return {"budget_seconds": _p95(seconds), "budget_tokens": _p95(tokens),
            "baseline_runs": used}
