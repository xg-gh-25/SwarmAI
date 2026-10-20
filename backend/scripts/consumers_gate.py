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
   the same way;
4. requires every hit unit to be CHANGED by the diff, or declared ``unaffected`` with a
   reason; and every unit declared ``migrated`` to be changed (a declaration does not
   stand in for the edit).

A pattern that matches nothing at the base and nothing now is refused (a typo checks
nothing); one that matched at the base and matches nothing now passes (every reader was
removed). Paths must be tree roots (one segment, ``.`` allowed): a deep path is the
author choosing the files to search, which re-imports the blind spot the search exists
to remove (the same rule as ``check_migration_class.validate_enumeration_cmd``).

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
from pathlib import Path
from typing import Iterable

GIT_TIMEOUT_S = 60  # hang guard for one git call; the calls finish in well under a second
MAX_HITS_SHOWN = 30
PATTERN_MAX = 500
_STATUSES = ("migrated", "unaffected")
_REASON_MIN = 10
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class _GitError(Exception):
    pass


def _git(repo: Path, *args: str) -> "subprocess.CompletedProcess":
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                              text=True, timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        raise _GitError(f"git {args[0]} could not run: {type(exc).__name__}: {exc}") from exc


# ── paths ──────────────────────────────────────────────────────────────────────

def _rel(path: str, repo: Path, roots: Iterable[Path]) -> "str | None":
    """*path* as a repo-relative POSIX path, or None when it is outside every root."""
    p = str(path).strip()
    if not p:
        return None
    if os.path.isabs(p):
        for root in [repo, *roots]:
            r = os.path.realpath(str(root)) if os.path.exists(str(root)) else str(root)
            for cand in {str(root).rstrip(os.sep), r.rstrip(os.sep)}:
                if p == cand or p.startswith(cand + os.sep):
                    p = os.path.relpath(p, cand)
                    break
            else:
                continue
            break
        else:
            return None
    p = os.path.normpath(p).replace(os.sep, "/")
    if p.startswith("../") or p == ".." or os.path.isabs(p):
        return None
    return p


def root_path(p: str) -> "str | None":
    """A tree-root search path ('.', 'backend', 'desktop/'), or None."""
    q = os.path.normpath(str(p).strip()).replace(os.sep, "/") if str(p).strip() else ""
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
    deleted: "set[str]" = set()
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
        elif ln.startswith("@@") and cur is not None:
            m = _HUNK.match(ln)
            if m:
                start, count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
                lines_by[cur] += (list(range(start, start + count)) if count
                                  else [max(start, 1)])  # pure deletion: the line it follows
    out: "set[str]" = set()
    for rel, lines in lines_by.items():
        src = _read(repo, rel)
        units = units_of(rel, src, lines)
        out |= units | {f"{rel}::<file>"}
    for rel in new_files or []:
        r = _rel(rel, repo, [])
        if r:
            out |= _all_units(repo, r)
    out |= {f"{rel}::<file>" for rel in deleted}
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

def _readers(consumer: dict, extra: Iterable[dict], repo: Path, roots, errors: list) -> "list[tuple[str, str, str]]":
    """(unit, status, reason) of every declared reader of *consumer*."""
    cid = str(consumer.get("id") or "?")
    raw = [r for r in (consumer.get("readers") or []) if isinstance(r, dict)]
    raw += [r for r in (extra or []) if isinstance(r, dict) and str(r.get("id") or "") == cid]
    out = []
    for r in raw:
        at = str(r.get("at") or "").strip()
        path, sep, unit = at.partition("::")
        rel = _rel(path, repo, roots)
        if rel is None:
            errors.append(f"{cid}: reader '{at}' is outside the repo {repo} — name it repo-relative "
                          "(path::function)")
            continue
        status = str(r.get("status") or "").strip().lower()
        if status not in _STATUSES:
            errors.append(f"{cid}: reader '{at}' has status '{status}' — use migrated or unaffected")
            continue
        out.append((f"{rel}::{unit}" if sep else rel, status, str(r.get("reason") or "").strip()))
    return out


def _covered(unit: str, declared: "list[tuple[str, str, str]]") -> "tuple[str, str] | None":
    """The (status, reason) declared for *unit*; a bare path covers its <module> / <file>."""
    path, _, q = unit.partition("::")
    for u, status, reason in declared:
        if u == unit or (u == path and q in ("<module>", "<file>")):
            return status, reason
    return None


def check(consumers: "list[dict] | None", extra_readers: "list[dict] | None", *, repo,
          base: str, new_files: "Iterable[str] | None" = None,
          roots: "Iterable[Path] | None" = None) -> "tuple[list[str], list[str]]":
    """(errors, warnings) for the declared *consumers* against the change from *base*."""
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
                          f"'desktop'), not {raw_paths!r} — search the tree, not the files you expect")
            continue
        try:
            hits = _hits(repo, pattern, paths)
        except ValueError as exc:
            errors.append(f"{cid}: pattern rejected by git grep: {exc}")
            continue
        except _GitError as exc:
            warnings.append(f"consumers gate: {cid} readers NOT verified — {exc}")
            continue
        declared = _readers(c, extra_readers or [], repo, roots, errors)
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
            got = _covered(u, declared)
            if got and got[0] == "unaffected" and len(got[1]) >= _REASON_MIN:
                continue
            missing.append((u, got))
        for u, got in missing[:MAX_HITS_SHOWN]:
            if got and got[0] == "unaffected":
                errors.append(f"{cid}: {u} is declared unaffected without a reason (>= {_REASON_MIN} chars)")
            elif got and got[0] == "migrated":
                errors.append(f"{cid}: {u} is declared migrated but the change does not touch it")
            else:
                errors.append(f"{cid}: {u} matches {pattern!r} but the change does not touch it and "
                              f"no reader declares it unaffected — migrate it, or run-update "
                              f"--add-consumer-reader '{cid}|{u}|unaffected|<reason>'")
        if len(missing) > MAX_HITS_SHOWN:
            errors.append(f"{cid}: {len(missing) - MAX_HITS_SHOWN} more unit(s) not shown")
        for u, status, _reason in declared:
            if status == "migrated" and u not in changed and u not in {m[0] for m in missing} \
                    and f"{u.partition('::')[0]}::<file>" not in changed:
                errors.append(f"{cid}: {u} is declared migrated but the change does not touch it")
    return errors, warnings
