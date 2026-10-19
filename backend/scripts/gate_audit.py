#!/usr/bin/env python3
"""Count gate decisions per gate per day, for the gate audit.

Reads ``~/.swarm-ai/logs/gate_decisions.jsonl`` (written by
``core.gate_decision_log``) and prints one row per gate: total decisions,
force bypasses, and the per-day counts, so a gate whose denials do not fall,
or that never fires, is visible at a glance.

Usage: python scripts/gate_audit.py [--since YYYY-MM-DD] [--gate NAME] [--samples N]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.gate_decision_log import log_path  # noqa: E402


def load(path: Path, since: str | None):
    for p in (path.with_suffix(path.suffix + ".1"), path):
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if since and str(rec.get("ts", ""))[:10] < since:
                continue
            yield rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since")
    ap.add_argument("--gate")
    ap.add_argument("--samples", type=int, default=0,
                    help="print N sample inputs per gate (to judge false positives)")
    ns = ap.parse_args(argv)
    per_day: dict[str, Counter] = defaultdict(Counter)
    forced: Counter = Counter()
    samples: dict[str, list] = defaultdict(list)
    for rec in load(log_path(), ns.since):
        gate = rec.get("gate", "?")
        if ns.gate and gate != ns.gate:
            continue
        if rec.get("decision") == "force_bypass":
            forced[gate] += 1
            continue
        per_day[gate][str(rec.get("ts", ""))[:10]] += 1
        if len(samples[gate]) < ns.samples:
            samples[gate].append(rec.get("input", "")[:300])
    if not per_day and not forced:
        print(f"no decisions in {log_path()}")
        return 0
    for gate in sorted(set(per_day) | set(forced), key=lambda g: -sum(per_day[g].values())):
        days = " ".join(f"{d[5:]}:{n}" for d, n in sorted(per_day[gate].items()))
        print(f"{gate:32} total={sum(per_day[gate].values()):5} force={forced[gate]:3}  {days}")
        for s in samples[gate]:
            print(f"    | {s!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
