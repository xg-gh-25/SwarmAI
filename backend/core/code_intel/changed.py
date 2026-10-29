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

What breaks, not only who calls (``core.code_intel.interface``): the base and the new
version of each changed Python / TS / JS file are compared symbol by symbol. A symbol
changed only in comments or its docstring is folded into one count and never looked
up; a removed symbol, a removed or now-required parameter, a dropped ``*args`` /
``**kwargs`` or a removed field comes first; and each outside call site is checked
against what the change removed or required — "passes ``dry_run=``, a parameter this
change removes" — so the definite breaks are named, not left for the reviewer to find
among the callers. Code files that name a symbol count only when the name is a code
token (or a mock's dotted path), never a comment or a docstring.

    "$SWARM_BACKEND_BIN" py -m core.code_intel.changed --repo <checkout> [--base REF] [--json]

*--repo* is the checkout being reviewed (a session worktree is fine: its graph is the
one of the checkout it belongs to). The change is the working tree against *--base*
(default ``HEAD``: what is not committed yet), or a tree object (``tree=``, what a
pipeline review reviews, untracked files included). Runs out of the daemon: the graph
is brought up to its repo's HEAD first (``build.ensure_fresh``), unless a caller with a
time budget (the pipeline's REVIEW) asks it not to.

Bounded in work, not only in output: at most ``MAX_EXAMINED`` touched symbols are
looked up (removals and interface changes first), and a caller's *budget_s* stops the lookups when
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
MAX_DELTA_FILES = 300      # changed files whose base and new versions are compared
MAX_PARSED = 40            # files parsed per symbol for call sites / mentions / field reads
_SYMBOL_TYPES = ("function", "method", "class")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Changed:
    id: str
    name: str
    file: str
    line: int
    signature: bool                       # the interface changed (or, for a language
                                          # without one, the change touches its first lines)
    delta: str = ""                       # what changed in the interface, in words
    removed: bool = False
    outside: list[dict] = field(default_factory=list)   # callers not in this change
    inside: list[dict] = field(default_factory=list)    # callers in files it changes
    mentions: list[str] = field(default_factory=list)   # named, no call seen
    more_callers: int = 0
    more_mentions: int = 0
    unconfirmed: int = 0    # files outside the change linked to it by the method name alone
    guessed: list[str] = field(default_factory=list)
    builtin_named: int = 0  # … more such files where the name is a builtin type's method
    breaking: int = 0       # outside call sites this change breaks (each caller's "breaks")
    field_reads: list[dict] = field(default_factory=list)   # reads of a removed field


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
    comments_only: list[str] = field(default_factory=list)  # changed only in comments
    breaks: int = 0                  # outside call sites this change breaks
    note: str = ""


def _git(repo: Path, *args: str) -> str | None:
    try:
        from .local_roots import GIT_SAFE
        if args and args[0] == "diff":     # never a configured diff or textconv program
            args = ("diff", "--no-ext-diff", "--no-textconv", *args[1:])
        # a source file need not be UTF-8 (a latin-1 file with a coding cookie)
        out = subprocess.run(["git", *GIT_SAFE, "-C", str(repo), *args], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=30)
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


def _mentions(repo: Path, c: "Changed", skip: set[str], texts: "_Texts") -> list[str]:
    """Code files that name *c* without a call the graph saw: the file contains the
    name as a word AND one of its anchors (``_anchors``), so another module's own
    ``take`` or ``_now`` does not count, and — for a language the interface reader
    knows — the name is a code token or a mock's dotted path, never only a comment or a
    docstring (``interface.names_in_code``). Docs and prose are not code files."""
    from .interface import names_in_code
    name = c.name
    if len(name) < 4 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return []
    anchors = _anchors(c)
    if not anchors:
        return []
    out = _git(repo, "grep", "-l", "-w", "-I", "-e", name, "--", *_code_pathspecs())
    named = sorted(_anchored(repo, c, [f for f in (out or "").splitlines() if f and f not in skip]))
    kept = []
    for i, f in enumerate(named):
        if i >= MAX_PARSED:
            kept.extend(named[i:])           # not read: kept as grep found them
            break
        lang, text, tree = texts.get(f)
        verdict = names_in_code(text or "", lang, name, anchors=anchors + [c.name],
                                suffix=Path(f).suffix, tree=tree) if text is not None else None
        if verdict is not False:
            kept.append(f)
    return kept


class _Texts:
    """Files read once per report, at one revision (None: the checkout's working
    tree), with their language and parse tree."""

    def __init__(self, repo: Path, rev: str | None = None):
        self.repo, self.rev = repo, rev
        self._cache: dict[str, tuple[str | None, str | None, object]] = {}

    def get(self, rel: str):
        hit = self._cache.get(rel)
        if hit is not None:
            return hit
        from .interface import _parse
        from .parser import LANGUAGE_MAP
        lang = LANGUAGE_MAP.get(Path(rel).suffix)
        if self.rev is None:
            try:
                text = (self.repo / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = None
        else:
            text = _git(self.repo, "show", f"{self.rev}:{rel}")
        tree = _parse(text, lang, Path(rel).suffix) if text is not None and lang else None
        hit = (lang, text, tree)
        self._cache[rel] = hit
        return hit


def _deltas(repo: Path, base: str, tree: str | None,
            files: list[str]) -> dict[str, tuple[dict, dict, set]]:
    """Per changed file of a language ``interface`` reads: (base interfaces, new ones,
    the names the new version still binds at module level)."""
    from .interface import LANGS, bound_names, interfaces
    from .parser import LANGUAGE_MAP
    out: dict[str, tuple[dict, dict, set]] = {}
    for f in files[:MAX_DELTA_FILES]:
        lang = LANGUAGE_MAP.get(Path(f).suffix)
        if lang not in LANGS:
            continue
        old = _git(repo, "show", f"{base}:{f}")
        if tree is not None:
            new = _git(repo, "show", f"{tree}:{f}")
        else:
            try:
                new = (repo / f).read_text(encoding="utf-8", errors="replace")
            except OSError:
                new = None
        out[f] = (interfaces(old, lang, Path(f).suffix) if old is not None else {},
                  interfaces(new, lang, Path(f).suffix) if new is not None else {},
                  bound_names(new, lang, Path(f).suffix) if new is not None else set())
    return out


def _still_reachable(key: str, new_ifs: dict, new_names: set) -> bool:
    """Whether a symbol gone from its definitions can still be reached as before: a
    function still bound at module level (re-exported, ``from b import f``, assigned),
    a method its class may still inherit (a base defines it, or a base this file does
    not define). Then it was moved, not removed: no caller is judged."""
    if "." not in key:
        return key in new_names
    cls, member = key.split(".", 1)
    seen: set[str] = set()
    todo = [cls]
    while todo:
        c = todo.pop()
        if c in seen:
            continue
        seen.add(c)
        iface = new_ifs.get(c)
        if iface is None:
            return c != cls or cls in new_names     # a base from elsewhere may define it
        if c != cls and (member in iface.methods or f"{c}.{member}" in new_ifs):
            return True
        todo.extend(b for b in iface.bases if b not in ("object", "Generic", "Protocol"))
    return False


def _edge_rows(graph, target: str) -> list[tuple[str, int | None, str, float]]:
    return [(r[0], r[1], r[2], r[3]) for r in graph._conn.execute(
        "SELECT source_id, line_number, edge_type, confidence FROM code_edges "
        "WHERE target_id = ?", (target,))]


def _to_new_lines(n: int, hunks: list[tuple[int, int, int, int]]) -> list[int]:
    """Graph-commit line *n* in the reviewed version: the line itself shifted, the new
    lines of the hunk that rewrote it, or none when the hunk deleted it."""
    offset = 0
    for os_, ol, ns, nl in sorted(hunks):
        if ol == 0:                       # lines inserted after os_
            if n > os_:
                offset += nl
                continue
            break
        if n < os_:
            break
        if n <= os_ + ol - 1:
            return list(range(ns, ns + nl))
        offset += nl - ol
    return [n + offset]


class _Breaks:
    """Definite breaks of one report: the caller files as the graph saw them (its edge
    lines) for a caller outside the change, and as the reviewed version has them for a
    caller in a file the change edits (a call the author left as it was)."""

    def __init__(self, repo: Path, graph, commit: str, tree: str | None, in_change: set[str]):
        self.repo, self.graph, self.commit, self.tree = repo, graph, commit, tree
        self.in_change = in_change
        self.old = _Texts(repo, commit)
        self.new = _Texts(repo, tree)
        self._hunks: dict[str, list] = {}

    def _lines_now(self, file: str, line: int) -> list[int]:
        if file not in self._hunks:
            diff = _git(self.repo, "diff", "-U0", "--no-renames", "--no-color", self.commit,
                        *([self.tree] if self.tree else []), "--", file)
            self._hunks[file] = _hunks(diff or "").get(file, [])
        return _to_new_lines(line, self._hunks[file])

    def of(self, c: "Changed", d, new_iface) -> dict[str, list[str]]:
        """Caller id → why its call sites break under *d* (only definite reasons: a
        removed symbol, a removed keyword, too many positionals, a new required
        parameter not passed; never a name-only link)."""
        from .impact import is_by_name
        from .interface import breaks, call_sites
        out: dict[str, list[str]] = {}
        key = c.id.split("::", 1)[-1]
        is_class = (new_iface is not None and new_iface.kind == "class") or (
            d.removed and "." not in key and c.name[:1].isupper())
        is_method = "." in key and not is_class
        owner = key.split(".")[0] if is_method else None

        def add(src: str, why: str) -> None:
            out.setdefault(src, [])
            if why not in out[src]:
                out[src].append(why)

        for src, line, edge_type, conf in _edge_rows(self.graph, c.id):
            file = src.split("::", 1)[0]
            if is_by_name(edge_type, conf) or file == c.file:
                continue
            inside = file in self.in_change
            if d.removed and not inside:
                add(src, "calls it, and this change removes it" if edge_type == "calls"
                    else "names it, and this change removes it")
                continue
            if line is None:
                continue
            texts = self.new if inside else self.old
            lang, text, tree = texts.get(file)
            if text is None:
                continue
            lines = self._lines_now(file, line) if inside else [line]
            found = [(n, site) for n in lines
                     for site in call_sites(text, lang or "", n, c.name,
                                            suffix=Path(file).suffix, class_name=owner, tree=tree)]
            if len(found) != 1:
                # none, or more than one call of that name where the graph saw one
                # (`d.get(k) or get(k)`, a rewritten hunk): which is this edge's is not
                # known, so none is judged
                continue
            n, site = found[0]
            if d.removed and edge_type == "calls":
                add(src, f"L{n}: still calls it, and this change removes it")
                continue
            for why in breaks(d, new_iface, site, is_method=is_method, language=lang or ""):
                add(src, f"L{n}: {why}")
        return out


def _field_reads(graph, repo: Path, c: "Changed", fields: list[str], texts: "_Texts",
                 in_change: set[str]) -> list[dict]:
    """Outside code that reads a field *c* (a class) no longer has, by name: files with
    an edge into the class or its members, or importing its file."""
    from .interface import reads_attribute
    rows = graph._conn.execute(
        "SELECT DISTINCT source_id FROM code_edges WHERE target_id = ? "
        "OR target_id LIKE ? ESCAPE '\\'",
        (c.id, c.id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + ".%")).fetchall()
    files = {r[0].split("::", 1)[0] for r in rows}
    files |= {r[0] for r in graph._conn.execute(
        "SELECT DISTINCT src_file FROM code_imports WHERE dst_file = ?", (c.file,))}
    out: list[dict] = []
    for f in sorted(files - in_change)[:MAX_PARSED]:
        lang, text, tree = texts.get(f)
        if text is None:
            continue
        for fld in fields:
            for line in reads_attribute(text, lang or "", fld, suffix=Path(f).suffix, tree=tree):
                out.append({"file": f, "line": line, "field": fld})
    return out


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
    rep.new = sum(1 for c in changed if c.file in new_files)
    from .interface import delta as _delta
    deltas = _deltas(repo, base, tree, sorted({c.file for c in changed} - new_files))
    # A class whose fields went away in a method's body (``self.x = …`` removed from
    # ``__init__``) is not touched in its own first lines: add it, so its readers are
    # looked up (a removed field is an interface change of the class).
    have = {c.id for c in changed}
    for f, (old_ifs, new_ifs, _names) in deltas.items():
        for key, oi in old_ifs.items():
            cid = f"{f}::{key}"
            if oi.kind != "class" or cid in have:
                continue
            if _delta(oi, new_ifs.get(key)).removed_fields:
                row = graph._conn.execute("SELECT name, line_start FROM code_nodes WHERE id = ?",
                                          (cid,)).fetchone()
                if row:
                    changed.append(Changed(id=cid, name=row[0], file=f, line=row[1],
                                           signature=True))
                    have.add(cid)
    plans: list[tuple[int, Changed, object, object]] = []
    for c in changed:
        if c.file in new_files:
            continue
        d = new_iface = None
        if c.file in deltas:
            old_ifs, new_ifs, new_names = deltas[c.file]
            key = c.id.split("::", 1)[-1]
            if key in old_ifs or key in new_ifs:
                d, new_iface = _delta(old_ifs.get(key), new_ifs.get(key)), new_ifs.get(key)
                if d.removed and _still_reachable(key, new_ifs, new_names):
                    d.removed = False                 # moved: callers are not judged
                    c.delta = "moved: still reachable from here (re-exported or inherited)"
        if d is not None and d.comments_only:
            rep.comments_only.append(c.id)
            continue
        if d is not None:
            c.signature, c.removed = d.interface or d.signature, d.removed
            c.delta = c.delta or d.describe()
        rank = (0 if d is not None and d.removed else 1 if d is not None and d.interface
                else 2 if c.signature else 3)
        plans.append((rank, c, d, new_iface))
    plans.sort(key=lambda p: (p[0], p[1].file, p[1].line))  # what can break first
    texts_w = _Texts(repo, tree)         # the reviewed tree: names and field reads
    # 1. Every interface change is checked for definite breaks (cheap: the graph's edges
    #    and the callers' call sites), however large the change; 2. the full listing
    #    (callers, names, leads) then runs in this order: breaks first, up to the cap.
    broken_by: dict[str, dict[str, list[str]]] = {}
    checker = _Breaks(repo, graph, commit, tree, in_change)
    for rank, c, d, new_iface in plans:
        if d is None or not d.interface:
            continue
        if deadline is not None and time.monotonic() > deadline:
            break
        b = checker.of(c, d, new_iface)
        if b:
            broken_by[c.id] = b
    plans.sort(key=lambda p: (-len(broken_by.get(p[1].id, {})), p[0], p[1].file, p[1].line))
    must = sum(1 for p in plans if p[1].id in broken_by)
    shown: list[Changed] = []
    for i, (rank, c, d, new_iface) in enumerate(plans):
        if i >= max(MAX_EXAMINED, must) or (
                i >= must and deadline is not None and time.monotonic() > deadline):
            rep.unexamined = len(plans) - i
            rep.unexamined_signature = sum(1 for p in plans[i:] if p[0] <= 2)
            break
        found = _callers(graph, [c.id], 1)
        broken = broken_by.get(c.id, {})
        callers = [{"file": x.file, "symbol": x.symbol, "line": x.line, "how": x.how,
                    "breaks": broken.get(f"{x.file}::"
                                         f"{'<module>' if x.symbol == '(module level)' else x.symbol}", [])}
                   for x in found if not x.by_name]
        guessed = sorted({x.file for x in found if x.by_name and not x.builtin_name
                          and x.file not in in_change})
        c.unconfirmed, c.guessed = len(guessed), guessed[:MAX_MENTIONS]
        c.builtin_named = len({x.file for x in found if x.builtin_name and x.file not in in_change})
        outside = [x for x in callers if x["file"] not in in_change]
        outside.sort(key=lambda x: (not x["breaks"], x["file"], x["line"] or 0))
        inside = [x for x in callers if x["file"] in in_change]
        inside.sort(key=lambda x: (not x["breaks"], x["file"], x["line"] or 0))
        c.breaking = sum(1 for x in outside + inside if x["breaks"])
        c.inside = inside[:max(MAX_CALLERS, sum(1 for x in inside if x["breaks"]))]
        cap = max(MAX_CALLERS, c.breaking)            # every break is listed
        c.outside, c.more_callers = outside[:cap], max(0, len(outside) - cap)
        if d is not None and d.removed_fields:
            c.field_reads = _field_reads(graph, repo, c, d.removed_fields, texts_w, in_change)
        mentions = _mentions(repo, c, {x.file for x in found} | in_change, texts_w)
        c.mentions, c.more_mentions = mentions[:MAX_MENTIONS], max(0, len(mentions) - MAX_MENTIONS)
        if c.breaking or c.outside or c.mentions or c.unconfirmed or c.field_reads:
            shown.append(c)
        else:
            rep.quiet.append(c.id)
    rep.breaks = sum(c.breaking for c in shown)
    # What breaks first, then what can break: most outside callers, then names.
    shown.sort(key=lambda c: (-c.breaking, not c.removed, not c.signature,
                              -(len(c.outside) + c.more_callers),
                              -(len(c.mentions) + c.more_mentions), c.file, c.line))
    cap = max(MAX_SYMBOLS, sum(1 for c in shown if c.breaking))   # every break is shown
    rep.symbols, rep.more_symbols = shown[:cap], max(0, len(shown) - cap)
    return rep


def listed_files(rep: Report) -> list[str]:
    """Every file the report names as a caller, a mention or a name-only lead (repo
    paths taken from the graph, never from model text)."""
    out = set()
    for c in rep.symbols:
        out.update(x["file"] for x in c.outside)
        out.update(c.mentions)
        out.update(c.guessed)
        out.update(r["file"] for r in c.field_reads)
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
                "breaks": rep.breaks, "comments_only": len(rep.comments_only),
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
    rep.comments_only = [_clean(q) for q in rep.comments_only]
    for c in rep.symbols:
        c.id, c.file = _clean(c.id), _clean(c.file)
        for row in (*c.outside, *c.inside):
            row["file"], row["symbol"] = _clean(row["file"]), _clean(row["symbol"])
            row["breaks"] = [_clean(b) for b in row.get("breaks", [])]
        for row in c.field_reads:
            row["file"], row["field"] = _clean(row["file"]), _clean(row["field"])
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
    if not (rep.symbols or rep.quiet or rep.new or rep.unexamined or rep.comments_only):
        lines.append(f"{len(rep.files)} files changed; no function, method or class the "
                     "graph holds is touched.")
        return "\n".join(lines)
    if rep.breaks:
        lines.append(f"**{rep.breaks} caller{'s' if rep.breaks != 1 else ''} will break** "
                     "(listed first under each symbol, with the reason).")
    if not rep.symbols and not rep.unexamined:
        lines.append("No changed function, method or class is called or named outside this change.")
    for c in rep.symbols:
        what = ("removed" if c.removed else f"interface: {c.delta}" if c.delta
                else "signature or first lines changed" if c.signature else "body changed")
        lines.append(f"- `{c.id}` ({what})")
        breaking = [x for x in c.outside if x.get("breaks")]
        breaking_in = [x for x in c.inside if x.get("breaks")]
        rest = [x for x in c.outside if not x.get("breaks")]
        if breaking or breaking_in:
            lines.append(f"  - will break ({len(breaking) + len(breaking_in)}):")
            lines += [f"    - {x['file']}: {x['symbol']} — " + "; ".join(x["breaks"])
                      for x in breaking]
            lines += [f"    - {x['file']}: {x['symbol']} (a file this change edits) — "
                      + "; ".join(x["breaks"]) for x in breaking_in]
        if rest:
            total = len(rest) + c.more_callers
            label = "other callers outside this change" if breaking else "callers outside this change"
            lines.append(f"  - {label} ({total}):")
            lines += [f"    - {x['file']}: {x['symbol']}" + (f" (L{x['line']})" if x["line"] else "")
                      + ("" if x.get("how", "call") == "call" else f" [{x['how']}]")
                      for x in rest]
            if c.more_callers:
                lines.append(f"    - … {c.more_callers} more (run `core.code_intel.impact {c.id}`)")
        elif not breaking:
            lines.append("  - no caller outside this change in the graph")
        if c.field_reads:
            fields = sorted({r["field"] for r in c.field_reads})
            lines.append(f"  - reads {', '.join(f'`.{f}`' for f in fields)} (removed here), by "
                         "name — check the object is this class: "
                         + ", ".join(f"{r['file']} (L{r['line']})" for r in c.field_reads[:MAX_CALLERS])
                         + (f" (+{len(c.field_reads) - MAX_CALLERS} more)"
                            if len(c.field_reads) > MAX_CALLERS else ""))
        if c.unconfirmed:
            more = f" (+{c.unconfirmed - len(c.guessed)} more)" if c.unconfirmed > len(c.guessed) else ""
            lines.append(f"  - linked by the method name alone (a call on a value whose type "
                         f"the code does not state; may be another object's): "
                         + ", ".join(c.guessed) + more)
        quiet_in = [x for x in c.inside if not x.get("breaks")]
        if quiet_in:
            lines.append("  - callers inside this change: "
                         + ", ".join(f"{x['file']}: {x['symbol']}" for x in quiet_in))
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
    if rep.comments_only:
        listed = ", ".join(f"`{q}`" for q in rep.comments_only[:10])
        more = f" (+{len(rep.comments_only) - 10} more)" if len(rep.comments_only) > 10 else ""
        lines.append(f"- {len(rep.comments_only)} changed symbols changed only in comments or "
                     f"docstrings (not looked up): {listed}{more}")
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
