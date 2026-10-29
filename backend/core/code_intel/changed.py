"""The callers of the code a change touches, for a reviewer (``s_expert-review``).

A contract change breaks where the changed function is called, and the reviewer of a
diff sees the diff, not its callers. This reads the diff, finds the functions, methods
and classes whose lines it touches in the repo's code graph, and lists each one's
callers, with the code files that name it (and its module or class) without a call the
graph saw — a function passed as a value, a test that mocks it by its dotted path — for
the reviewer to grep. What can break outside the change comes first; symbols called
only inside it fold into one line, and those of files new in the change are counted.
A call on a value whose type the code does not state is linked by the method name
alone (``impact.is_by_name``); those files are listed apart, as a lead, not a caller.
It is deterministic: the same diff and graph give the same report, so a review step can
run it instead of relying on someone remembering to ask.

    "$SWARM_BACKEND_BIN" py -m core.code_intel.changed --repo <checkout> [--base REF] [--json]

*--repo* is the checkout being reviewed (a session worktree is fine: its graph is the
one of the checkout it belongs to). The change is the working tree against *--base*
(default ``HEAD``: what is not committed yet), or a tree object (``tree=``, what a
pipeline review reviews, untracked files included). Runs out of the daemon: the graph
is brought up to its repo's HEAD first (``build.ensure_fresh``), unless a caller with a
time budget (the pipeline's REVIEW) asks it not to.

Bounded in work, not only in output: at most ``MAX_EXAMINED`` touched symbols are
looked up (signature changes first), and a caller's *budget_s* stops the lookups when
it runs out; what was not looked up is counted. Every report appends one line of
counts to ``<app data>/logs/code_intel_usage.jsonl`` (no code, no path but the repo's
name; a pipeline's REVIEW adds a line with the repo files its findings and the reports
had in common) so its use is measured without anyone collecting it (design
2026-10-29-code-intel-auto D5).

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
import logging
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_SYMBOLS = 25
MAX_CALLERS = 12
MAX_MENTIONS = 8
MAX_EXAMINED = 80          # touched symbols whose callers are looked up, at most
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
    unconfirmed: int = 0    # files outside the change linked to it by the method name alone
    guessed: list[str] = field(default_factory=list)


@dataclass
class Report:
    repo: str
    project: str | None = None
    graph_repo: str | None = None
    indexed_commit: str | None = None
    freshness: str = ""
    files: list[str] = field(default_factory=list)
    symbols: list[Changed] = field(default_factory=list)   # called or named from outside
    more_symbols: int = 0
    quiet: list[str] = field(default_factory=list)  # touched; no caller or name outside
    new: int = 0                                    # symbols of files new in this change
    unexamined: int = 0      # touched symbols not looked up (MAX_EXAMINED or the budget)
    unexamined_signature: int = 0   # of those, signature changes
    note: str = ""


def _git(repo: Path, *args: str) -> str | None:
    try:
        from .local_roots import GIT_SAFE
        if args and args[0] == "diff":     # never a configured diff or textconv program
            args = ("diff", "--no-ext-diff", "--no-textconv", *args[1:])
        out = subprocess.run(["git", *GIT_SAFE, "-C", str(repo), *args], capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def filter_programs(repo: Path) -> list[str]:
    """The filter programs the repo's config names (``filter.<driver>.clean`` /
    ``.process`` / ``.smudge``): a diff against the working tree would run a clean
    filter on a dirty file, so the report does not diff a working tree that has one."""
    out = _git(repo, "config", "--get-regexp", r"^filter\..*\.(clean|process|smudge)$")
    return [line.split(" ", 1)[0] for line in (out or "").splitlines() if line.strip()]


def main_checkout(repo: Path) -> Path | None:
    """The checkout *repo* belongs to (itself, or the main work tree of a linked one)."""
    common = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return None
    common_dir = Path(common.strip())
    return common_dir.parent.resolve() if common_dir.name == ".git" else common_dir.resolve()


def changed_files(repo: Path, base: str, tree: str | None = None) -> list[str]:
    """Files changed from *base* to the working tree, or to *tree* when given."""
    out = _git(repo, "diff", "--name-only", "--no-renames", base, *([tree] if tree else []), "--")
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
                  base: str = "HEAD", tree: str | None = None) -> dict[str, list[tuple[int, int]]]:
    """The lines of *files* at *commit* (the graph's) that the change touches, in
    *commit*'s coordinates, whatever the checkout's HEAD is.

    Working tree (*tree* None): lines *commit* changed since *base* (the new side of
    ``base..commit``) plus lines the working tree changed since *commit* (the old side
    of ``commit..worktree``). *tree* (a pipeline review): exactly the lines of *tree*
    that differ from *base* (the new side of ``base..tree``), carried back to
    *commit* through ``commit..tree`` — so an incremental review, whose *base* is the
    tree the last review saw, gets only what changed since, and a graph commit ahead of
    the fork base adds nothing of its own."""
    out: dict[str, list[tuple[int, int]]] = {}
    if tree is not None:
        change = _git(repo, "diff", "-U0", "--no-renames", "--no-color", base, tree, "--", *files)
        through = _hunks(_git(repo, "diff", "-U0", "--no-renames", "--no-color", commit, tree,
                              "--", *files) or "")
        for f, rs in _hunk_ranges(change or "", "new").items():
            out[f] = [_to_commit(r, through.get(f, [])) for r in rs]
        return out
    committed = _git(repo, "diff", "-U0", "--no-renames", "--no-color", base, commit, "--", *files)
    working = _git(repo, "diff", "-U0", "--no-renames", "--no-color", commit, "--", *files)
    for ranges in (_hunk_ranges(committed or "", "new"), _hunk_ranges(working or "", "old")):
        for f, rs in ranges.items():
            out.setdefault(f, []).extend(rs)
    return out


def _hunks(diff: str) -> dict[str, list[tuple[int, int, int, int]]]:
    """Per file, each ``-U0`` hunk as (old start, old count, new start, new count)."""
    out: dict[str, list[tuple[int, int, int, int]]] = {}
    old_name = current = None
    for line in diff.splitlines():
        if line.startswith("--- "):
            old_name = line[6:] if line.startswith("--- a/") else None
        elif line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else old_name
        elif current and (m := _HUNK.match(line)):
            out.setdefault(current, []).append((
                int(m.group(1)), int(m.group(2)) if m.group(2) is not None else 1,
                int(m.group(3)), int(m.group(4)) if m.group(4) is not None else 1))
    return out


def _line_to_commit(n: int, hunks: list[tuple[int, int, int, int]]) -> tuple[int, int]:
    """Tree line *n* in commit coordinates: the line itself shifted by the hunks before
    it, or, inside a hunk, that hunk's commit lines (``(l, -l)``: between l and l+1 when
    the hunk adds lines the commit does not have)."""
    offset = 0
    for os_, ol, ns, nl in sorted(hunks, key=lambda h: h[2]):
        if nl == 0:                       # lines the tree does not have, after line ns
            if n > ns:
                offset += ol
                continue
            break
        if n < ns:
            break
        if n <= ns + nl - 1:
            return (os_, os_ + ol - 1) if ol else (os_, -os_)
        offset += ol - nl
    return (n + offset, n + offset)


def _to_commit(rng: tuple[int, int], hunks: list[tuple[int, int, int, int]]) -> tuple[int, int]:
    """A tree range (``_hunk_ranges`` form) in commit coordinates."""
    a, b = rng
    if b < 0:                             # between tree lines a and a+1
        end = abs(_line_to_commit(a, hunks)[1]) if a > 0 else 0
        return (end, -end)
    lo, hi = _line_to_commit(a, hunks), _line_to_commit(b, hunks)
    if lo[1] < 0 and hi[1] < 0 and lo[0] == hi[0]:
        return lo
    return (lo[0], max(abs(hi[1]), lo[0]))


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


def _code_pathspecs() -> list[str]:
    from .parser import LANGUAGE_MAP
    return [f"*{ext}" for ext in sorted(LANGUAGE_MAP)]


def _anchors(c: "Changed") -> list[str]:
    """A word a file that really refers to *c* also carries: for a method its class,
    otherwise its module's name (a package's ``__init__`` by its directory)."""
    symbol = c.id.split("::", 1)[-1]
    if "." in symbol:
        words = [symbol.split(".")[0]]
    else:
        stem = Path(c.file).stem
        words = [Path(c.file).parent.name if stem in ("__init__", "index", "mod") else stem]
    return [w for w in words if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", w or "")]


def _anchored(repo: Path, c: "Changed", files: list[str]) -> set[str]:
    """The subset of *files* that carry one of *c*'s anchors as a word."""
    anchors = _anchors(c)
    if not anchors or not files:
        return set()
    args = ["grep", "-l", "-w", "-I"]
    for w in anchors:
        args += ["-e", w]
    out = _git(repo, *args, "--", *sorted(set(files)))
    return {f for f in (out or "").splitlines() if f}


def _mentions(repo: Path, c: "Changed", skip: set[str]) -> list[str]:
    """Code files that name *c* without a call the graph saw: the file contains the
    name as a word AND one of its anchors (``_anchors``), so another module's own
    ``take`` or ``_now`` does not count. Docs and prose are not code files."""
    name = c.name
    if len(name) < 4 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return []
    anchors = _anchors(c)
    if not anchors:
        return []
    out = _git(repo, "grep", "-l", "-w", "-I", "-e", name, "--", *_code_pathspecs())
    named = [f for f in (out or "").splitlines() if f and f not in skip]
    return sorted(_anchored(repo, c, named))


def _project_graph_for(checkout: Path, project: str | None = None):
    """(project name, project dir, ref, graph) of the graph built from *checkout*: of
    *project* when given (a pipeline run knows its Brain), else the one Brain that reads
    it (``local_roots`` allows one per root; a bind-managed clone is one Brain's)."""
    from jobs.paths import PROJECTS_DIR
    from . import load_project_graphs, owned_graphs
    if not PROJECTS_DIR.is_dir():
        return None
    for project_dir in sorted(PROJECTS_DIR.iterdir()):
        if not project_dir.is_dir() or (project is not None and project_dir.name != project):
            continue
        refs = [r for r in owned_graphs(project_dir)
                if r.repo_root is not None and Path(r.repo_root).resolve() == checkout]
        if not refs:
            continue
        for ref, graph in load_project_graphs(project_dir.name):
            if ref.db_path == refs[0].db_path:
                return project_dir.name, project_dir, ref, graph
    return None


def report(repo: str | Path, *, base: str = "HEAD", refresh: bool = True,
           tree: str | None = None, project: str | None = None,
           budget_s: float | None = None, caller: str = "native") -> Report:
    """The callers report of the change in *repo* from *base* (to the working tree, or
    to *tree*). *project*: look only at that Brain's graph. *budget_s*: stop looking up
    callers when it runs out (the rest is counted in ``unexamined``). *caller*: who
    asked, for the usage log."""
    rep = _report(Path(repo).resolve(), base=base, refresh=refresh, tree=tree,
                  project=project, budget_s=budget_s)
    _log_usage(rep, caller)
    return rep


def _report(repo: Path, *, base: str, refresh: bool, tree: str | None,
            project: str | None, budget_s: float | None) -> Report:
    import time
    from .build import ensure_fresh
    from .impact import _callers
    deadline = None if budget_s is None else time.monotonic() + budget_s
    rep = Report(repo=str(repo))
    if tree is None and filter_programs(repo):
        # a tree-to-commit diff (the pipeline's) runs no filter; a working tree may
        rep.note = ("the repo's git config names a filter program, so its working tree is "
                    "not diffed here: grep the names of the changed functions instead")
        return rep
    rep.files = changed_files(repo, base, tree)
    if not rep.files:
        rep.note = f"no change against {base}"
        return rep
    checkout = main_checkout(repo)
    found = _project_graph_for(checkout, project) if checkout else None
    if found is None and checkout and project is not None:
        found = _project_graph_for(checkout)     # read by another Brain: its graph
    if found is None:
        rep.note = ("this checkout has no code graph (it is not read by any Brain; "
                    "`artifact_cli bind --local` adds it): grep the names of the changed "
                    "functions instead")
        return rep
    rep.project, project_dir, ref, graph = found
    rep.graph_repo = ref.repo
    if refresh:
        fr = ensure_fresh(project_dir, ref, graph)
        if not fr.current:
            rep.freshness = fr.message or fr.state
    else:
        from .build import parser_outdated
        from .freshness import head_stamp
        if parser_outdated(graph):
            rep.freshness = "built by an older parser; it is being rebuilt"
        elif head_stamp(graph)[0]:
            rep.freshness = "behind its repo's HEAD"
    commit = graph.get_meta("last_indexed_commit")
    rep.indexed_commit = commit
    if not commit or _git(repo, "cat-file", "-e", f"{commit}^{{commit}}") is None:
        rep.note = ("the graph's commit is not in this checkout, so its lines cannot be "
                    "matched to the diff: grep the names of the changed functions instead")
        return rep
    changed = symbols_touched(graph, touched_lines(repo, commit, rep.files, base, tree))
    in_change = set(rep.files)
    # A file new in this change: nothing outside it can call what it defines yet.
    new_files = {f for f in {c.file for c in changed}
                 if _git(repo, "cat-file", "-e", f"{base}:{f}") is None}
    shown: list[Changed] = []
    rep.new = sum(1 for c in changed if c.file in new_files)
    todo = [c for c in changed if c.file not in new_files]   # signature changes first
    for i, c in enumerate(todo):
        if i >= MAX_EXAMINED or (deadline is not None and time.monotonic() > deadline):
            rep.unexamined = len(todo) - i
            rep.unexamined_signature = sum(1 for x in todo[i:] if x.signature)
            break
        found = _callers(graph, [c.id], 1)
        callers = [{"file": x.file, "symbol": x.symbol, "line": x.line, "how": x.how}
                   for x in found if not x.by_name]
        guessed = sorted({x.file for x in found if x.by_name and x.file not in in_change})
        c.unconfirmed, c.guessed = len(guessed), guessed[:MAX_MENTIONS]
        outside = [x for x in callers if x["file"] not in in_change]
        c.inside = [x for x in callers if x["file"] in in_change][:MAX_CALLERS]
        c.outside, c.more_callers = outside[:MAX_CALLERS], max(0, len(outside) - MAX_CALLERS)
        mentions = _mentions(repo, c, {x.file for x in found} | in_change)
        c.mentions, c.more_mentions = mentions[:MAX_MENTIONS], max(0, len(mentions) - MAX_MENTIONS)
        if c.outside or c.mentions or c.unconfirmed:
            shown.append(c)
        else:
            rep.quiet.append(c.id)
    # What can break outside this change first: most outside callers, then names.
    shown.sort(key=lambda c: (-(len(c.outside) + c.more_callers),
                              -(len(c.mentions) + c.more_mentions), not c.signature, c.file, c.line))
    rep.symbols, rep.more_symbols = shown[:MAX_SYMBOLS], max(0, len(shown) - MAX_SYMBOLS)
    return rep


def listed_files(rep: Report) -> list[str]:
    """Every file the report names as a caller, a mention or a name-only lead (repo
    paths taken from the graph, never from model text)."""
    out = set()
    for c in rep.symbols:
        out.update(x["file"] for x in c.outside)
        out.update(c.mentions)
        out.update(c.guessed)
    return sorted(out)


def _log_usage(rep: Report, caller: str) -> None:
    """One line of counts per report (D5). Never fails the report."""
    try:
        from datetime import datetime, timezone
        state = ("none" if rep.project is None else
                 "unmatched" if rep.note else
                 "behind" if rep.freshness else "current")
        line = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "kind": "report", "caller": caller,
                "repo": rep.graph_repo or Path(rep.repo).name, "graph": state,
                "files": len(rep.files),
                "symbols": len(rep.symbols) + rep.more_symbols + len(rep.quiet)
                + rep.new + rep.unexamined,
                "with_outside_callers": sum(1 for c in rep.symbols if c.outside),
                "callers_listed": sum(len(c.outside) for c in rep.symbols),
                "name_only_files": sum(c.unconfirmed for c in rep.symbols),
                "unexamined": rep.unexamined}
        log_usage(line)
    except Exception as exc:  # noqa: BLE001 -- measuring must never break the report
        logger.warning("code_intel.changed: usage line not written: %s", exc)


def log_usage(line: dict) -> None:
    """Append *line* to ``<app data>/logs/code_intel_usage.jsonl``."""
    from config import get_app_data_dir
    path = get_app_data_dir() / "logs" / "code_intel_usage.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


_UNSAFE = re.compile(r"[\x00-\x1f\x7f]")


def _clean(text: str) -> str:
    """A repo path or symbol id on one line: control characters (a newline that would
    start a heading in a prompt) become spaces."""
    return _UNSAFE.sub(" ", str(text))


def _cleaned(rep: Report) -> Report:
    """*rep* with every string from the repo on one line (``_clean``): the rendered
    report goes into a reviewer's prompt, and a file name may carry a newline."""
    import copy
    rep = copy.deepcopy(rep)
    rep.project = _clean(rep.project) if rep.project else rep.project
    rep.graph_repo = _clean(rep.graph_repo) if rep.graph_repo else rep.graph_repo
    rep.files = [_clean(f) for f in rep.files]
    rep.quiet = [_clean(q) for q in rep.quiet]
    for c in rep.symbols:
        c.id, c.file = _clean(c.id), _clean(c.file)
        for row in (*c.outside, *c.inside):
            row["file"], row["symbol"] = _clean(row["file"]), _clean(row["symbol"])
        c.mentions = [_clean(m) for m in c.mentions]
        c.guessed = [_clean(g) for g in c.guessed]
    return rep


def render(rep: Report) -> str:
    rep = _cleaned(rep)
    lines = ["## Callers of the code this change touches"]
    if rep.note and not rep.symbols:
        lines.append(f"{len(rep.files)} files changed; {rep.note}.")
        return "\n".join(lines)
    where = rep.project + (f" · repo {rep.graph_repo}" if rep.graph_repo else "")
    lines.append(f"Code graph of {where}, indexed at {(rep.indexed_commit or '?')[:9]}"
                 + (f" ({rep.freshness})" if rep.freshness else "") + ".")
    if not (rep.symbols or rep.quiet or rep.new or rep.unexamined):
        lines.append(f"{len(rep.files)} files changed; no function, method or class the "
                     "graph holds is touched.")
        return "\n".join(lines)
    if not rep.symbols and not rep.unexamined:
        lines.append("No changed function, method or class is called or named outside this change.")
    for c in rep.symbols:
        what = "signature or first lines changed" if c.signature else "body changed"
        lines.append(f"- `{c.id}` ({what})")
        if c.outside:
            lines.append(f"  - callers outside this change ({len(c.outside) + c.more_callers}):")
            lines += [f"    - {x['file']}: {x['symbol']}" + (f" (L{x['line']})" if x["line"] else "")
                      + ("" if x.get("how", "call") == "call" else f" [{x['how']}]")
                      for x in c.outside]
            if c.more_callers:
                lines.append(f"    - … {c.more_callers} more (run `core.code_intel.impact {c.id}`)")
        else:
            lines.append("  - no caller outside this change in the graph")
        if c.unconfirmed:
            more = f" (+{c.unconfirmed - len(c.guessed)} more)" if c.unconfirmed > len(c.guessed) else ""
            lines.append(f"  - linked by the method name alone (a call on a value whose type "
                         f"the code does not state; may be another object's): "
                         + ", ".join(c.guessed) + more)
        if c.inside:
            lines.append("  - callers inside this change: "
                         + ", ".join(f"{x['file']}: {x['symbol']}" for x in c.inside))
        if c.mentions:
            more = f" (+{c.more_mentions} more)" if c.more_mentions else ""
            lines.append("  - code files that name it with its module or class but no call "
                         "the graph saw (passed as a value, mocked by its dotted path): "
                         + ", ".join(c.mentions) + more)
    if rep.more_symbols:
        lines.append(f"- … {rep.more_symbols} more changed symbols called or named outside "
                     "this change (run with --json for all)")
    if rep.quiet:
        listed = ", ".join(f"`{q}`" for q in rep.quiet[:15])
        more = f" (+{len(rep.quiet) - 15} more)" if len(rep.quiet) > 15 else ""
        lines.append(f"- {len(rep.quiet)} more changed symbols have no caller and no mention "
                     f"outside this change: {listed}{more}")
    if rep.new:
        lines.append(f"- {rep.new} symbols are in files new in this change "
                     "(nothing outside can call them yet)")
    if rep.unexamined:
        sig = (f", {rep.unexamined_signature} of them signature changes"
               if rep.unexamined_signature else ", all body changes")
        lines.append(f"- {rep.unexamined} more changed symbols were not looked up{sig}: the "
                     "change is large; run `core.code_intel.impact <symbol>` for one")
    lines.append("The graph sees direct calls. Check the files that name a symbol without a "
                 "call, and dynamic dispatch (getattr, registries, string routes), by hand.")
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
