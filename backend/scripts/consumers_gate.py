"""consumers_gate — every reader of a changed contract is migrated or declared, by code.

A replay of past Gate-2 findings found the most frequent class to be a reader of a
changed contract left as it was — usually inside a file the change already touched, so
a file-level check passed it, and the same miss recurred in the fix round of the same
run. A prose rule ("grep all consumers", ``stages/ref/build/pre-checks.md``) had been in
context every time.

The PLAN declares each changed contract under ``implementation_constraints.consumers``::

    {"id": "K1", "contract": "<what changed>", "pattern": "<git grep -E regex>",
     "paths": ["backend"], "readers": [{"at": "backend/x.py::fn", "status": "migrated"},
                                       {"at": "backend/y.py::g", "status": "unaffected",
                                        "reason": "<why it is not affected>"}]}

``check`` then, in the run's repo:

1. searches each pattern with ``git grep`` (argument list: the pattern follows ``-e``,
   the paths follow ``--``; no shell) over tracked and untracked files;
2. maps every hit to its UNIT: the innermost enclosing ``def`` / ``class`` of a ``.py``
   file as ``Outer.inner`` (``<module>`` outside any), a ``.py`` that does not parse or
   any other file as ``<file>``;
3. maps the run's diff (``git diff -U0 <base>`` plus the untracked new files) to units
   the same way, both sides of every hunk: added lines are units of the current file,
   removed lines units of the file at the base (so a function the run deleted from a
   file that still exists is changed, and its neighbour is not);
4. requires every hit unit to be CHANGED by the diff, or declared ``unaffected`` with a
   reason; and every unit declared ``migrated`` to be changed itself — a change to
   another unit of the same file credits nothing; a deleted file (``path::<deleted>``)
   credits every unit it held (a declaration does not stand in for the edit).

The declarations are ``core/contract_readers.resolve``'s, the same entries the run's
DESIGN_SPEC.md shows: one per unit, the latest (PLAN readers, then run.json
``consumer_readers`` in order) deciding. A bare path is ``path::<file>``; module-level
code of a ``.py`` is ``path::<module>``.

A pattern that matches nothing at the base and nothing now is refused (a typo checks
nothing); one that matched at the base and matches nothing now passes (every reader was
removed). Paths must be tree roots (one segment, ``.`` allowed): a deep path is the
author choosing the files to search, which re-imports the blind spot the search exists
to remove (the same rule as ``check_migration_class.validate_enumeration_cmd``). Pathspec
magic (a leading ':') and glob characters are refused, and every git call runs with the
global ``--literal-pathspecs``, so a path can never exclude files from the search.

Infra failures (git missing, the base not resolvable) are returned as warnings that say
the readers were NOT verified; they are never a silent pass and never a refusal that
would lock the run on a machine problem.

Public: ``check``, ``units_of``, ``root_path`` (also design_spec's PLAN check), ``MAX_HITS_SHOWN``.

Known limit: a pure-deletion hunk is credited to the unit of the line before it, so
deleting the last lines of one function credits that function, never the next one.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

if str(Path(__file__).resolve().parents[1]) not in sys.path:   # standalone load: core lives in backend/
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import contract_readers  # noqa: E402
from core.run_worktree import LITERAL_PATHSPECS  # noqa: E402

GIT_TIMEOUT_S = 60  # hang guard for one git call; the calls finish in well under a second
MAX_HITS_SHOWN = 30
PATTERN_MAX = 500
_REASON_MIN = 10
_PATHSPEC_MAGIC = set("*?[]\\!")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class _GitError(Exception):
    pass


def _git(repo: Path, *args: str) -> "subprocess.CompletedProcess":
    try:
        # a global option, before the subcommand: every path is read literally, never
        # as pathspec magic (':!x', ':(exclude)…') or a glob
        return subprocess.run(["git", "-C", str(repo), LITERAL_PATHSPECS, *args],
                              capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        raise _GitError(f"git {args[0]} could not run: {type(exc).__name__}: {exc}") from exc


# ── paths ──────────────────────────────────────────────────────────────────────

def root_path(p: str) -> "str | None":
    """A tree-root search path ('.', 'backend', 'desktop/'), or None. Pathspec magic
    (a leading ':') and glob characters are refused: git would read them as a pattern
    that can exclude files from the search."""
    raw = str(p).strip()
    if raw.startswith(":") or _PATHSPEC_MAGIC & set(raw):
        return None
    q = os.path.normpath(raw).replace(os.sep, "/") if raw else ""
    if not q or q.startswith("..") or os.path.isabs(q):
        return None
    return q if (q == "." or "/" not in q) else None


# ── units ──────────────────────────────────────────────────────────────────────

def _spans(source: str) -> "list[tuple[int, int, str]] | None":
    """(first line incl. decorators, last line, qualname) of every def / class, or None
    when the source does not parse."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    out: "list[tuple[int, int, str]]" = []

    def walk(node, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}.{child.name}" if prefix else child.name
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                out.append((start, getattr(child, "end_lineno", child.lineno) or child.lineno, name))
                walk(child, name)
            else:
                walk(child, prefix)
    walk(tree, "")
    return out


def units_of(path: str, source: "str | None", lines: Iterable[int]) -> "set[str]":
    """The units (``path::qualname``) the given 1-based lines of *path* fall in."""
    if not path.endswith(".py") or source is None:
        return {f"{path}::<file>"}
    spans = _spans(source)
    if spans is None:
        return {f"{path}::<file>"}
    out: "set[str]" = set()
    for ln in lines:
        inside = [s for s in spans if s[0] <= ln <= s[1]]
        if inside:
            out.add(f"{path}::{max(inside, key=lambda s: s[0])[2]}")
        else:
            out.add(f"{path}::<module>")
    return out


def _read(repo: Path, rel: str) -> "str | None":
    try:
        return (repo / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _read_base(repo: Path, base: str, rel: str) -> "str | None":
    """*rel* as it was at *base*, or None (not there, or git failed)."""
    p = _git(repo, "show", f"{base}:{rel}")
    return p.stdout if p.returncode == 0 else None


def _all_units(repo: Path, rel: str) -> "set[str]":
    src = _read(repo, rel)
    if not rel.endswith(".py") or src is None:
        return {f"{rel}::<file>"}
    spans = _spans(src)
    if spans is None:
        return {f"{rel}::<file>"}
    return {f"{rel}::{s[2]}" for s in spans} | {f"{rel}::<module>", f"{rel}::<file>"}


def _changed_units(repo: Path, base: str, new_files: Iterable[str]) -> "set[str]":
    p = _git(repo, "diff", "-U0", "--no-renames", "--no-color", base, "--")
    if p.returncode != 0:
        raise _GitError(f"git diff {base[:12]} failed: {(p.stderr or p.stdout).strip()[:200]}")
    lines_by: "dict[str, list[int]]" = {}
    old_by: "dict[str, list[int]]" = {}
    deleted: "set[str]" = set()
    added: "set[str]" = set()
    cur: "str | None" = None
    gone: "str | None" = None
    for ln in p.stdout.splitlines():
        if ln.startswith("--- "):
            src = ln[4:].strip()
            gone = src[2:] if src.startswith("a/") else src
        elif ln.startswith("+++ "):
            tgt = ln[4:].strip()
            if tgt == "/dev/null":   # the run deleted the file: every unit in it changed
                cur = None
                if gone and gone != "/dev/null":
                    deleted.add(gone)
            else:
                cur = tgt[2:] if tgt.startswith("b/") else tgt
                lines_by.setdefault(cur, [])
                old_by.setdefault(cur, [])
                if gone == "/dev/null":   # a file the run added (staged or committed)
                    added.add(cur)
        elif ln.startswith("@@") and cur is not None:
            m = _HUNK.match(ln)
            if m:
                # both sides, each at its own grain: the lines the run removed are units of
                # the base source (a removed function is credited by its own lines), the
                # lines it added are units of the current file; a count of 0 adds nothing
                o_start, o_count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
                n_start, n_count = int(m.group(3)), int(m.group(4) if m.group(4) is not None else 1)
                old_by[cur] += list(range(o_start, o_start + o_count))
                lines_by[cur] += list(range(n_start, n_start + n_count))
    out: "set[str]" = set()
    for rel, lines in lines_by.items():
        # the units the changed lines fall in, and no other: a touched file credits
        # its <file> unit only when it has no finer one (not Python, or unparsable)
        if lines:
            out |= units_of(rel, _read(repo, rel), lines)
    for rel, lines in old_by.items():
        if lines and rel not in added:
            out |= units_of(rel, _read_base(repo, base, rel), lines)
    # a new file is every unit of it, the same whether it is untracked (new_files),
    # staged or already committed by an earlier cycle (a '--- /dev/null' diff)
    for rel in [*(new_files or []), *sorted(added)]:
        r = contract_readers.rel(rel, [repo])
        if r:
            out |= _all_units(repo, r)
    # a deleted file: every unit it held is gone, and <deleted> credits any of them
    out |= {f"{rel}::<deleted>" for rel in deleted}
    return out


# ── search ─────────────────────────────────────────────────────────────────────

def _hits(repo: Path, pattern: str, paths: "list[str]") -> "dict[str, list[int]]":
    p = _git(repo, "grep", "-n", "-I", "--null", "--untracked", "-E", "-e", pattern, "--", *paths)
    if p.returncode == 1:
        return {}
    if p.returncode != 0:
        raise ValueError((p.stderr or p.stdout).strip()[:200] or f"git grep exit {p.returncode}")
    out: "dict[str, list[int]]" = {}
    for rec in p.stdout.splitlines():
        parts = rec.split("\0")
        if len(parts) >= 2 and parts[1].isdigit():
            out.setdefault(parts[0], []).append(int(parts[1]))
    return out


def _hit_at_base(repo: Path, base: str, pattern: str, paths: "list[str]") -> bool:
    p = _git(repo, "grep", "-l", "-I", "-E", "-e", pattern, base, "--", *paths)
    return p.returncode == 0 and bool(p.stdout.strip())


# ── the check ──────────────────────────────────────────────────────────────────

def _credited(unit: str, changed: "set[str]") -> bool:
    """The change credits *unit*: that unit itself changed, or its file was deleted."""
    return unit in changed or f"{unit.partition('::')[0]}::<deleted>" in changed


def check(consumers: "list[dict] | None", extra_readers: "list[dict] | None", *, repo,
          base: str, new_files: "Iterable[str] | None" = None,
          roots: "Iterable[Path] | None" = None) -> "tuple[list[str], list[str]]":
    """(errors, warnings) for the declared *consumers* against the change from *base*.

    The declarations of each consumer are ``contract_readers.resolve``'s: one per unit,
    the latest (PLAN readers, then *extra_readers* in order) deciding — the same entries
    DESIGN_SPEC.md shows. *roots*: the absolute roots a declaration may be written
    under besides *repo* (``contract_readers.run_roots``)."""
    items = [c for c in (consumers or []) if isinstance(c, dict)]
    if not items:
        return [], []
    repo = Path(repo)
    roots = [Path(r) for r in (roots or [])]
    errors: "list[str]" = []
    warnings: "list[str]" = []
    try:
        changed = _changed_units(repo, base, new_files or [])
    except _GitError as exc:
        return [], [f"consumers gate: readers NOT verified — {exc}"]
    by_id, rejected = contract_readers.resolve(items, extra_readers, [repo, *roots])
    for c in items:
        cid = str(c.get("id") or "?")
        pattern = c.get("pattern")
        if not (isinstance(pattern, str) and pattern.strip() and len(pattern) <= PATTERN_MAX):
            errors.append(f"{cid}: pattern must be a non-empty git grep -E regex of at most "
                          f"{PATTERN_MAX} characters")
            continue
        raw_paths = c.get("paths") if isinstance(c.get("paths"), list) else []
        paths = [root_path(p) for p in raw_paths]
        if not raw_paths or any(p is None for p in paths):
            errors.append(f"{cid}: paths must be tree roots inside the repo ('.', 'backend', "
                          f"'desktop'; no pathspec magic or glob characters), not {raw_paths!r} — "
                          "search the tree, not the files you expect")
            continue
        try:
            hits = _hits(repo, pattern, paths)
        except ValueError as exc:
            errors.append(f"{cid}: pattern rejected by git grep: {exc}")
            continue
        except _GitError as exc:
            warnings.append(f"consumers gate: {cid} readers NOT verified — {exc}")
            continue
        entries = by_id.get(cid, [])
        errors += [r["error"] for r in rejected if r["id"] == cid]
        declared = {e["unit"]: e for e in entries}
        if not hits:
            try:
                had = _hit_at_base(repo, base, pattern, paths)
            except _GitError as exc:
                warnings.append(f"consumers gate: {cid} readers NOT verified — {exc}")
                continue
            if not had:
                errors.append(f"{cid}: pattern {pattern!r} matches nothing at the run's base or "
                              "now — it checks nothing; fix the pattern")
        hit_units: "set[str]" = set()
        for rel, lines in hits.items():
            hit_units |= units_of(rel, _read(repo, rel), lines)
        missing = []
        for u in sorted(hit_units):
            if u in changed:
                continue
            got = declared.get(u)
            if got and got["status"] == "unaffected" and len(got["reason"]) >= _REASON_MIN:
                continue
            missing.append((u, got))
        for u, got in missing[:MAX_HITS_SHOWN]:
            if got and got["status"] == "unaffected":
                errors.append(f"{cid}: {u} is declared unaffected without a reason (>= {_REASON_MIN} chars)")
            elif got and got["status"] == "migrated":
                errors.append(f"{cid}: {u} is declared migrated but the change does not touch it")
            else:
                path, _, q = u.partition("::")
                hint = (f" (a declaration of {path} is {path}::<file>; name it {path}::<module>)"
                        if q == "<module>" and f"{path}::<file>" in declared else "")
                errors.append(f"{cid}: {u} matches {pattern!r} but the change does not touch it and "
                              f"no reader declares it unaffected{hint} — migrate it, or run-update "
                              f"--add-consumer-reader '{cid}|{u}|unaffected|<reason>'")
        if len(missing) > MAX_HITS_SHOWN:
            errors.append(f"{cid}: {len(missing) - MAX_HITS_SHOWN} more unit(s) not shown")
        shown = {m[0] for m in missing}
        for u, e in declared.items():
            if e["status"] == "migrated" and u not in shown and not _credited(u, changed):
                path, _, q = u.partition("::")
                hint = (f" (a bare {path} is {path}::<file>, which a parsable .py never has; "
                        f"name the function or {path}::<module>)"
                        if q == "<file>" and path.endswith(".py") else "")
                errors.append(f"{cid}: {u} is declared migrated but the change does not touch it{hint}")
    return errors, warnings
