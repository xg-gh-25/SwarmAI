#!/usr/bin/env python3
"""Measure DDD recall by purpose against a labelled case set (run-knowledge §3.6.6).

Each case is a request, a purpose and the entries that request should find (a
substring of each entry). For every case this compares:

- ``before``: what the run got before §3.6 -- the 4 docs' entries ranked in ONE shared
  corpus (top 6, the knowledge.md / THINK view; top 5 for review), as the retired
  ``recall_ddd_entries`` did;
- ``after``: ``recall_ddd(query, project, purpose)`` at each ``--weights`` value.

and prints hits per purpose, the PRODUCT / PROJECT hits of the requirement cases, and
recall time. READ-only, keyword-only: it reads the project's DDD docs and nothing else
(no data.db). The case file is the operator's (it names their project's entries), so
it lives in their workspace, not in this repo.

Usage: ddd_recall_eval.py --cases <file.json> [--project P] [--weights 1.0,1.2,1.5] [--json]
Case file: {"project": "P", "cases": [{"id", "request", "purpose", "expect": [..]}]}
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BEFORE_TOP = {"review": 5}


def _found(expect: list[str], hits: list[dict]) -> list[str]:
    texts = [str(h.get("content") or "") for h in hits]
    return [e for e in expect if any(e in t for t in texts)]


def _timed(fn):
    t = time.perf_counter()
    out = fn()
    return out, (time.perf_counter() - t) * 1000


def evaluate(cases: list[dict], project: str, weights: list[float]) -> dict:
    from core.recall_filter import extract_query_keywords
    from core.recall_multi import _ddd_entry_hits, _load_ddd_docs_text, recall_ddd

    def before(query: str, top: int) -> list[dict]:
        _base, docs = _load_ddd_docs_text(project)
        return _ddd_entry_hits(query, docs, top) if docs and query.strip() else []
    rows, times = [], {"before": [], **{f"after@{w}": [] for w in weights}}
    for case in cases:
        query = extract_query_keywords(case["request"])
        expect = list(case["expect"])
        top = BEFORE_TOP.get(case["purpose"], 6)
        hits, ms = _timed(lambda: before(query, top))
        times["before"].append(ms)
        row = {"id": case["id"], "purpose": case["purpose"], "expect": len(expect),
               "before": _found(expect, hits)}
        for w in weights:
            hits, ms = _timed(lambda: recall_ddd(query, project, case["purpose"], weight=w))
            times[f"after@{w}"].append(ms)
            row[f"after@{w}"] = _found(expect, hits)
        rows.append(row)
    keys = ["before", *(f"after@{w}" for w in weights)]
    summary = {}
    for purpose in sorted({r["purpose"] for r in rows}):
        rs = [r for r in rows if r["purpose"] == purpose]
        summary[purpose] = {"expect": sum(r["expect"] for r in rs),
                            **{k: sum(len(r[k]) for r in rs) for k in keys}}
    return {"rows": rows, "summary": summary,
            "median_ms": {k: round(statistics.median(v), 1) for k, v in times.items() if v}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cases", required=True)
    ap.add_argument("--project")
    ap.add_argument("--weights", default="1.0,1.2,1.5")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    data = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    project = args.project or data["project"]
    weights = [float(w) for w in args.weights.split(",") if w.strip()]
    out = evaluate(data["cases"], project, weights)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    for r in out["rows"]:
        cells = " ".join(f"{k}={len(v)}" for k, v in r.items() if isinstance(v, list))
        print(f"{r['id']:<28} {r['purpose']:<11} expect={r['expect']} {cells}")
    print("\nper purpose (entries found / expected):")
    for purpose, s in out["summary"].items():
        print(f"  {purpose:<11} " + " ".join(f"{k}={v}/{s['expect']}" if k != "expect" else ""
                                           for k, v in s.items()).strip())
    print("median recall ms:", out["median_ms"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
