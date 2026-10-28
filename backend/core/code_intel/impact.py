"""Who calls this? — the callers of a file or a symbol, read from a project's code graph.

The question grep cannot answer by itself: before changing a function, a field or a
module's interface, which code elsewhere calls it, directly or one step removed. The
graph resolves calls across files (imports, aliases, methods, TypeScript modules),
so its callers are found by what the code does, not by the spelling grep matches.
It is not complete either: a dynamic call (getattr, a string-dispatched handler, a
call in a repo the project does not bind) has no edge. So the answer names its own
blind spots and the freshness of the graph, and grep stays the cross-check.

    python -m core.code_intel.impact <file|symbol> --project P [--repo R] [--depth 2] [--json]

A target is a repo-relative file path (every symbol in it; callers inside the same
file are left out) or a symbol: ``name``, ``Class.method`` or ``path::Class.method``.
Every graph of the project is searched unless ``--repo`` picks one. Pure read: it
writes nothing.

Exit codes: 0 answered (also with zero callers), 1 no graph or no such target, 2 usage.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .graph_store import GraphStore

MAX_DEPTH = 3
_MAX_SEEDS = 200          # matches of a short name read, before the cap below
_MAX_SYMBOL_MATCHES = 10  # more than this: ask for a qualified name


@dataclass
class Caller:
    file: str
    symbol: str
    line: int | None
    depth: int


@dataclass
class GraphImpact:
    repo: str | None
    target_kind: str                     # "file" | "symbol"
    seeds: list[str]
    callers: list[Caller] = field(default_factory=list)
    more_matches: int = 0                # symbol matches left out (ask to qualify)
    indexed_commit: str | None = None
    commits_behind: int | None = None
    changed_since_index: list[str] = field(default_factory=list)
    freshness: str = ""                  # "", or why the graph may be behind


def _seeds(graph: GraphStore, target: str) -> tuple[str, list[str], int]:
    """(kind, node ids, matches left out) for *target* in *graph*."""
    rows = graph._conn.execute(
        "SELECT id FROM code_nodes WHERE file_path = ? ORDER BY line_start",
        (target,)).fetchall()
    if rows:  # every symbol of the file: its own internal calls stay out (seed to seed)
        return "file", [r[0] for r in rows], 0
    rows = graph._conn.execute(
        "SELECT id FROM code_nodes WHERE id = ? OR id LIKE ? ESCAPE '\\' OR name = ? "
        "ORDER BY file_path, line_start LIMIT ?",
        (target, "%::" + target.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"),
         target, _MAX_SEEDS)).fetchall()
    ids = [r[0] for r in rows]
    exact = [i for i in ids if i == target]
    if exact:
        return "symbol", exact, 0
    return "symbol", ids[:_MAX_SYMBOL_MATCHES], max(0, len(ids) - _MAX_SYMBOL_MATCHES)


def _callers(graph: GraphStore, seeds: list[str], depth: int) -> list[Caller]:
    seed_set = set(seeds)
    best: dict[str, int] = {}
    for seed in seeds:
        for nid, d in graph.find_callers(seed, depth=depth):
            if nid in seed_set:
                continue
            if nid not in best or d < best[nid]:
                best[nid] = d
    out: list[Caller] = []
    for nid, d in best.items():
        row = graph._conn.execute(
            "SELECT file_path, line_start FROM code_nodes WHERE id = ?", (nid,)).fetchone()
        file_path, line = (row[0], row[1]) if row else (nid.split("::", 1)[0], None)
        symbol = nid.split("::", 1)[1] if "::" in nid else nid
        if symbol == "<module>":
            symbol = "(module level)"  # a call outside any function (parser's sentinel)
        out.append(Caller(file=file_path, symbol=symbol, line=line, depth=d))
    out.sort(key=lambda c: (c.depth, c.file, c.line or 0))
    return out


def impact(project: str, target: str, *, repo: str | None = None,
           depth: int = 2) -> list[GraphImpact] | None:
    """The callers of *target* in every graph of *project* that holds it (or the graph
    of *repo*). None when the project has no such graph; an empty list when no graph
    holds the target."""
    from . import load_project_graphs
    from .freshness import check_freshness
    depth = max(1, min(depth, MAX_DEPTH))
    graphs = load_project_graphs(project)
    if repo is not None:
        graphs = [(ref, g) for ref, g in graphs if ref.repo == repo]
    if not graphs:
        return None
    results: list[GraphImpact] = []
    for ref, graph in graphs:
        kind, seeds, more = _seeds(graph, target)
        if not seeds:
            continue
        res = GraphImpact(repo=ref.repo, target_kind=kind, seeds=seeds, more_matches=more,
                          callers=_callers(graph, seeds, depth),
                          indexed_commit=graph.get_meta("last_indexed_commit"))
        try:
            fresh = check_freshness(graph)
        except Exception as exc:  # noqa: BLE001 — the answer stands; say freshness is unknown
            res.freshness = f"freshness unknown: {type(exc).__name__}"
        else:
            if fresh.stale:
                res.commits_behind = fresh.commits_behind
                res.changed_since_index = list(fresh.changed_files)
                res.freshness = fresh.reason or "the graph is behind the repo"
        results.append(res)
    return results


# ── text form (what an agent reads) ──────────────────────────────────────────

_BLIND_SPOTS = ("Not in the graph: dynamic calls (getattr, string dispatch), calls from "
                "repos the project does not bind, and code the parser could not read. "
                "Cross-check with grep.")


def render(project: str, target: str, results: list[GraphImpact]) -> str:
    lines: list[str] = []
    for res in results:
        where = f"{project}" + (f" · repo {res.repo}" if res.repo else "")
        what = "file" if res.target_kind == "file" else (
            "symbol" if len(res.seeds) == 1 else f"{len(res.seeds)} symbols named")
        lines.append(f"Callers of {what} {target} ({where})")
        if res.target_kind == "symbol" and len(res.seeds) > 1:
            lines.append("  matches: " + ", ".join(res.seeds)
                         + (f" (+{res.more_matches} more: give file::name)" if res.more_matches else ""))
        if res.freshness:
            changed = res.changed_since_index
            hit = sorted({c.file for c in res.callers} & set(changed) | ({target} & set(changed)))
            behind = (f"{res.commits_behind} commits behind HEAD"
                      if res.commits_behind is not None else res.freshness)
            note = f"  graph: indexed at {(res.indexed_commit or '?')[:9]}, {behind}"
            if hit:
                note += "; changed since, in this answer: " + ", ".join(hit)
            elif changed:
                note += f"; {len(changed)} files changed since"
            lines.append(note)
        if not res.callers:
            lines.append("  no callers in the graph")
        for d in sorted({c.depth for c in res.callers}):
            at = [c for c in res.callers if c.depth == d]
            files = sorted({c.file for c in at})
            lines.append(f"  depth {d} ({'direct' if d == 1 else 'via a caller'}): "
                         f"{len(at)} callers in {len(files)} files")
            for f in files:
                syms = ", ".join(f"{c.symbol} (L{c.line})" if c.line else c.symbol
                                 for c in at if c.file == f)
                lines.append(f"    {f}: {syms}")
        lines.append("")
    lines.append(_BLIND_SPOTS)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m core.code_intel.impact",
        description="Who calls a file or a symbol, from the project's code graph.")
    ap.add_argument("target", help="repo-relative file path, or a symbol (name, Class.method, path::name)")
    ap.add_argument("--project", required=True, help="project name (Projects/<name>)")
    ap.add_argument("--repo", default=None, help="one bound repo's graph (default: every graph)")
    ap.add_argument("--depth", type=int, default=2, help=f"caller hops, 1-{MAX_DEPTH} (default 2)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)
    results = impact(args.project, args.target, repo=args.repo, depth=args.depth)
    if results is None:
        what = f"repo {args.repo!r}" if args.repo else "code graph"
        print(f"{args.project}: no {what} (bind a repo, or index the project's own)",
              file=sys.stderr)
        return 1
    if not results:
        print(f"{args.target}: no such file or symbol in {args.project}'s code graph "
              "(a file path is repo-relative)", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"project": args.project, "target": args.target,
                          "results": [asdict(r) for r in results],
                          "blind_spots": _BLIND_SPOTS}, indent=2, ensure_ascii=False))
    else:
        print(render(args.project, args.target, results))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
