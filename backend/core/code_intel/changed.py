"""The callers of the code a change touches, for a reviewer (``s_expert-review``).

A contract change breaks where the changed function is called, and the reviewer of a
diff sees the diff, not its callers. This reads the diff, finds the functions, methods
and classes whose lines it touches in the repo's code graph, and lists each one's
callers, with the files that name it without a call the graph saw (a function passed as
a value, a test that mocks it by its dotted path, prose) for the reviewer to grep. It is
deterministic: the same diff and graph give the same report, so a review step can run
it instead of relying on someone remembering to ask.

    "$SWARM_BACKEND_BIN" py -m core.code_intel.changed --repo <checkout> [--base REF] [--json]

*--repo* is the checkout being reviewed (a session worktree is fine: its graph is the
one of the checkout it belongs to). The change is the working tree against *--base*
(default ``HEAD``: what is not committed yet). Runs out of the daemon: the graph is
brought up to its repo's HEAD first (``build.ensure_fresh``).

How lines map onto the graph: the graph's symbols carry line spans at the commit it was
indexed at (G). The files the change touches come from ``git diff <base>``; the touched
lines are, in G's coordinates, what G changed since *base* plus what the working tree
changed since G (``touched_lines``). When G holds commits the checkout does not (the
main checkout moved on), their changes to those files are listed too: more callers,
never fewer. A function deleted before G is not in the graph and is not listed.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

MAX_SYMBOLS = 25
MAX_CALLERS = 12
MAX_MENTIONS = 8
_SYMBOL_TYPES = ("function", "method", "class")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Changed:
    id: str
    name: str
    file: str
    line: int
    signature: bool                       # the change touches its first lines
    outside: list[dict] = field(default_factory=list)   # callers not in this change
    inside: list[dict] = field(default_factory=list)    # callers in files it changes
    mentions: list[str] = field(default_factory=list)   # named, no call seen
    more_callers: int = 0
    more_mentions: int = 0


@dataclass
class Report:
    repo: str
    project: str | None = None
    graph_repo: str | None = None
    indexed_commit: str | None = None
    freshness: str = ""
    files: list[str] = field(default_factory=list)
    symbols: list[Changed] = field(default_factory=list)
    more_symbols: int = 0
    note: str = ""


def _git(repo: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def main_checkout(repo: Path) -> Path | None:
    """The checkout *repo* belongs to (itself, or the main work tree of a linked one)."""
    common = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return None
    common_dir = Path(common.strip())
    return common_dir.parent.resolve() if common_dir.name == ".git" else common_dir.resolve()


def changed_files(repo: Path, base: str) -> list[str]:
    out = _git(repo, "diff", "--name-only", "--no-renames", base, "--")
    return sorted({f for f in (out or "").splitlines() if f})


def _hunk_ranges(diff: str, side: str) -> dict[str, list[tuple[int, int]]]:
    """Line ranges per file from a ``-U0`` diff, on its old (``-``) or new (``+``)
    side. A hunk with no line on that side (a pure insertion seen from the old side, a
    pure deletion seen from the new) sits between lines l and l+1: ``(l, -l)``."""
    ranges: dict[str, list[tuple[int, int]]] = {}
    current = None
    marker = "--- a/" if side == "old" else "+++ b/"
    for line in (diff or "").splitlines():
        if line.startswith(("--- ", "+++ ")):
            if line.startswith(marker[:4]):
                current = line[len(marker):] if line.startswith(marker) else None
            continue
        m = _HUNK.match(line)
        if m and current:
            g = (1, 2) if side == "old" else (3, 4)
            start = int(m.group(g[0]))
            count = int(m.group(g[1]) if m.group(g[1]) is not None else 1)
            ranges.setdefault(current, []).append(
                (start, start + count - 1) if count else (start, -start))
    return ranges


def touched_lines(repo: Path, commit: str, files: list[str],
                  base: str = "HEAD") -> dict[str, list[tuple[int, int]]]:
    """The lines of *files* at *commit* (the graph's) that the change touches: lines
    *commit* changed since *base* (the new side of ``base..commit``) and lines the
    working tree changed since *commit* (the old side of ``commit..worktree``). Both
    sides are in *commit*'s coordinates, whatever the checkout's HEAD is."""
    out: dict[str, list[tuple[int, int]]] = {}
    committed = _git(repo, "diff", "-U0", "--no-renames", "--no-color", base, commit, "--", *files)
    working = _git(repo, "diff", "-U0", "--no-renames", "--no-color", commit, "--", *files)
    for ranges in (_hunk_ranges(committed or "", "new"), _hunk_ranges(working or "", "old")):
        for f, rs in ranges.items():
            out.setdefault(f, []).extend(rs)
    return out


def _touches(node_start: int, node_end: int, rng: tuple[int, int]) -> bool:
    a, b = rng
    if b < 0:                              # insertion between lines a and a+1
        return node_start <= a and node_end >= a + 1
    return node_start <= b and node_end >= a


def symbols_touched(graph, ranges: dict[str, list[tuple[int, int]]]) -> list[Changed]:
    """The graph's functions, methods and classes whose span a range touches. An outer
    symbol (a class) is dropped when only a symbol inside it was touched, unless its
    own first lines were; the order is: signature touched first, then file, line."""
    found: list[Changed] = []
    for file, rngs in ranges.items():
        rows = graph._conn.execute(
            "SELECT id, name, node_type, line_start, line_end FROM code_nodes "
            f"WHERE file_path = ? AND node_type IN ({','.join('?' * len(_SYMBOL_TYPES))})",
            (file, *_SYMBOL_TYPES)).fetchall()
        hit = []
        for nid, name, _t, start, end in rows:
            touched = [r for r in rngs if _touches(start, end, r)]
            if not touched:
                continue
            head = (start, start + 1)
            sig = any(_touches(head[0], head[1], r) for r in touched)
            hit.append((nid, name, start, end, sig))
        for nid, name, start, end, sig in hit:
            inner = any(s2 >= start and e2 <= end and (s2, e2) != (start, end)
                        for _n, _m, s2, e2, _s in hit)
            if inner and not sig:
                continue
            found.append(Changed(id=nid, name=name, file=file, line=start, signature=sig))
    found.sort(key=lambda c: (not c.signature, c.file, c.line))
    return found


def _mentions(repo: Path, name: str, skip: set[str]) -> list[str]:
    """Files that contain *name* as a word but where the graph saw no call. Names too
    short to grep meaningfully give none."""
    if len(name) < 4 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return []
    out = _git(repo, "grep", "-l", "-w", "-I", "-e", name, "--")
    return sorted(f for f in (out or "").splitlines() if f and f not in skip)


def _project_graph_for(checkout: Path):
    """(project name, project dir, ref, graph) of the graph built from *checkout*."""
    from jobs.paths import PROJECTS_DIR
    from . import load_project_graphs, owned_graphs
    if not PROJECTS_DIR.is_dir():
        return None
    for project_dir in sorted(PROJECTS_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        refs = [r for r in owned_graphs(project_dir)
                if r.repo_root is not None and Path(r.repo_root).resolve() == checkout]
        if not refs:
            continue
        for ref, graph in load_project_graphs(project_dir.name):
            if ref.db_path == refs[0].db_path:
                return project_dir.name, project_dir, ref, graph
    return None


def report(repo: str | Path, *, base: str = "HEAD", refresh: bool = True) -> Report:
    from .build import ensure_fresh
    from .impact import _callers
    repo = Path(repo).resolve()
    rep = Report(repo=str(repo))
    rep.files = changed_files(repo, base)
    if not rep.files:
        rep.note = f"no change against {base}"
        return rep
    checkout = main_checkout(repo)
    found = _project_graph_for(checkout) if checkout else None
    if found is None:
        rep.note = ("this checkout has no code graph (bind the repo, or propose it with "
                    "`artifact_cli bind --local` and confirm it in Brain Hub): grep the "
                    "names of the changed functions instead")
        return rep
    rep.project, project_dir, ref, graph = found
    rep.graph_repo = ref.repo
    if refresh:
        fr = ensure_fresh(project_dir, ref, graph)
        if not fr.current:
            rep.freshness = fr.message or fr.state
    commit = graph.get_meta("last_indexed_commit")
    rep.indexed_commit = commit
    if not commit or _git(repo, "cat-file", "-e", f"{commit}^{{commit}}") is None:
        rep.note = ("the graph's commit is not in this checkout, so its lines cannot be "
                    "matched to the diff: grep the names of the changed functions instead")
        return rep
    changed = symbols_touched(graph, touched_lines(repo, commit, rep.files, base))
    rep.symbols, rep.more_symbols = changed[:MAX_SYMBOLS], max(0, len(changed) - MAX_SYMBOLS)
    in_change = set(rep.files)
    for c in rep.symbols:
        callers = [{"file": x.file, "symbol": x.symbol, "line": x.line}
                   for x in _callers(graph, [c.id], 1)]
        outside = [x for x in callers if x["file"] not in in_change]
        c.inside = [x for x in callers if x["file"] in in_change][:MAX_CALLERS]
        c.outside, c.more_callers = outside[:MAX_CALLERS], max(0, len(outside) - MAX_CALLERS)
        seen = {x["file"] for x in callers} | {c.file}
        mentions = _mentions(repo, c.name, seen)
        c.mentions, c.more_mentions = mentions[:MAX_MENTIONS], max(0, len(mentions) - MAX_MENTIONS)
    return rep


def render(rep: Report) -> str:
    lines = ["## Callers of the code this change touches"]
    if rep.note and not rep.symbols:
        lines.append(f"{len(rep.files)} files changed; {rep.note}.")
        return "\n".join(lines)
    where = rep.project + (f" · repo {rep.graph_repo}" if rep.graph_repo else "")
    lines.append(f"Code graph of {where}, indexed at {(rep.indexed_commit or '?')[:9]}"
                 + (f" ({rep.freshness})" if rep.freshness else "") + ".")
    if not rep.symbols:
        lines.append(f"{len(rep.files)} files changed; no function, method or class the "
                     "graph holds is touched.")
        return "\n".join(lines)
    for c in rep.symbols:
        what = "signature or first lines changed" if c.signature else "body changed"
        lines.append(f"- `{c.id}` ({what})")
        if c.outside:
            lines.append(f"  - callers outside this change ({len(c.outside) + c.more_callers}):")
            lines += [f"    - {x['file']}: {x['symbol']}" + (f" (L{x['line']})" if x["line"] else "")
                      for x in c.outside]
            if c.more_callers:
                lines.append(f"    - … {c.more_callers} more (run `core.code_intel.impact {c.id}`)")
        else:
            lines.append("  - no caller outside this change in the graph")
        if c.inside:
            lines.append("  - callers inside this change: "
                         + ", ".join(f"{x['file']}: {x['symbol']}" for x in c.inside))
        if c.mentions:
            more = f" (+{c.more_mentions} more)" if c.more_mentions else ""
            lines.append("  - named without a call the graph saw (passed as a value, mocked "
                         "by its dotted path, or prose): " + ", ".join(c.mentions) + more)
    if rep.more_symbols:
        lines.append(f"- … {rep.more_symbols} more changed symbols not shown")
    lines.append("The graph sees direct calls. Check the \"named without a call\" files with "
                 "grep, and dynamic dispatch (getattr, registries, string routes) by hand.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.code_intel.changed",
                                 description="Callers of the code a change touches.")
    ap.add_argument("--repo", default=".", help="the checkout under review")
    ap.add_argument("--base", default="HEAD",
                    help="compare the working tree with this ref (default HEAD)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-refresh", action="store_true")
    args = ap.parse_args(argv)
    rep = report(args.repo, base=args.base, refresh=not args.no_refresh)
    if args.json:
        print(json.dumps(asdict(rep), ensure_ascii=False, indent=1))
    else:
        print(render(rep))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
