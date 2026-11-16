#!/usr/bin/env python3
"""cjk_recall_probe — does a Chinese query find what its English twin finds in a Brain?

Runs a set of query pairs against one Brain's ``find`` (``core.addressing.find``, what
``cognition find --project P --in ddd`` prints) and reports, for each pair, the rank
of the pair's target entry for the Chinese and the English query, in two modes:

- ``base``: the scorer as it was before CJK phrase weighting (every query term weight
  1) -- the "before" column, from the same run;
- ``weighted``: the bigrams of one Chinese phrase share one term's weight
  (``memory_index._bm25_query_weights``).

The pairs file is JSON: ``{"pairs": [{"id", "title", "en", "zh", "set"?}, ...]}``.
``title`` is a substring of the target entry's text that occurs in no other entry;
the target's rank is the position of the first unit of the result holding it.
``set`` (optional) groups pairs in the summary, e.g. a ``probe`` set and a
``heldout`` set that was not looked at.

Usage::

    "$SWARM_BACKEND_BIN" py -m scripts.cjk_recall_probe --project SwarmAI \\
        --pairs pairs.json [--top 8] [--json out.json]

Exit 1 when a query ranks its target lower (or loses it) in ``weighted`` than in
``base``, so the run is also a regression check; 0 otherwise. For an English query that
is a sanity check (a query with no CJK is not weighted); for a Chinese one it catches
the weighting pushing a target down.
Read-only: it reads the Brain and writes only ``--json``.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path
from typing import Iterator, Optional
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

MODES = ("base", "weighted")
LANGS = ("zh", "en")


@contextlib.contextmanager
def mode(name: str) -> Iterator[None]:
    """Score as *name* says while the block runs (see the module docstring)."""
    from core import memory_index
    with contextlib.ExitStack() as stack:
        if name == "base":
            def unweighted(query: str) -> dict[str, float]:
                return {t: 1.0 for t in memory_index._bm25_tokenize(query)}
            stack.enter_context(mock.patch.object(memory_index, "_bm25_query_weights", unweighted))
        yield


def rank_of(project: str, query: str, title: str, top: int) -> Optional[int]:
    """1-based rank of the first unit of *project*'s find for *query* holding *title*,
    or None when it is not in the top *top*."""
    from core import addressing
    found = addressing.find(project, query, top=top, budget=10 ** 9)
    for i, unit in enumerate(found.units + found.omitted, 1):
        if title in unit.text:
            return i
    return None


def run_probe(project: str, pairs: list[dict], *, top: int = 8) -> list[dict]:
    """One row per pair: ``{"id", "set", "<mode>": {"zh": rank, "en": rank}}``."""
    rows = [{"id": p.get("id", str(i)), "set": p.get("set", "all")} for i, p in enumerate(pairs)]
    for name in MODES:
        with mode(name):
            for row, pair in zip(rows, pairs):
                row[name] = {lang: rank_of(project, pair[lang], pair["title"], top) for lang in LANGS}
    return rows


def regressions(rows: list[dict]) -> list[str]:
    """Each query whose target ranks lower (or drops out) in ``weighted`` than in
    ``base``."""
    out = []
    for r in rows:
        for lang in LANGS:
            before = r["base"][lang]
            if before is None:
                continue
            for m in ("weighted",):
                after = r[m][lang]
                if after is None or after > before:
                    out.append(f"{r['id']}: {lang} rank {before} -> {after} ({m})")
    return out


def summary(rows: list[dict], top: int) -> list[str]:
    lines = []
    for group in sorted({r["set"] for r in rows}):
        sel = [r for r in rows if r["set"] == group]
        counts = "  ".join(
            f"{m}: zh {sum(1 for r in sel if r[m]['zh'])}/{len(sel)}"
            f" en {sum(1 for r in sel if r[m]['en'])}/{len(sel)}" for m in MODES)
        lines.append(f"[{group}] targets in the top {top} — {counts}")
    return lines


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--pairs", required=True, type=Path)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args(argv)
    pairs = json.loads(args.pairs.read_text(encoding="utf-8"))["pairs"]
    rows = run_probe(args.project, pairs, top=args.top)
    def cell(v: Optional[int]) -> str:
        return "-" if v is None else str(v)
    print("id    set      " + "  ".join(f"{m:>8} zh/en" for m in MODES))
    for r in rows:
        print(f"{r['id']:<5} {r['set']:<8} " + "  ".join(
            f"{cell(r[m]['zh']):>8}/{cell(r[m]['en']):<4}" for m in MODES))
    for line in summary(rows, args.top):
        print(line)
    bad = regressions(rows)
    for line in bad:
        print(f"REGRESSION {line}")
    if args.json:
        args.json.write_text(json.dumps({"top": args.top, "rows": rows}, indent=1), encoding="utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
