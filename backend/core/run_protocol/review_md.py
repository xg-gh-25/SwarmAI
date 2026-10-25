"""review_md — a repository's own review guidance, ``.swarmai/review.md`` (design
docs/design/2026-10-24-swarmai-usable-by-anyone.md §3.5.2, S6-4b).

The product carries no review checklist of its own beyond the expert prompts, and no
built-in rule about which paths or APIs are security-sensitive. A repository says it
in ``.swarmai/review.md``:

- free text for the agent and the experts (§3.7 injects the head of the file);
- optionally a pattern table: every table line with one ``match:`` token is a row,
  its first cell the row id (``| GO1 | **name** | trigger `match: …` | verify | example |``);
- optionally one line ``security: `match: <terms>` `` that dispatches security review.

``match:`` grammar: space-separated terms; ``glob:<pattern>`` is an fnmatch glob
against each changed path and its basename (``*`` crosses ``/``); ``kw:<text>`` is a
case-insensitive substring of the paths and the diff, with spacing next to
punctuation ignored on both sides (``kw:port=`` matches ``port = 1``); ``*`` alone
always applies. Every line starting with ``security:`` must be the declaration
(``security_line``); a malformed one is an error, and the pipeline then dispatches
security.

The pipeline reads the file at the run's BASE commit (``read_at``), so a run cannot
weaken the review it is subject to by editing the file in its own worktree.

Public: ``parse``, ``security_line``, ``read_at``, ``applies``, ``select``, ``specific_hit``,
``compact``, ``Guide``, ``Row``, ``GuideError``, ``PATH``.
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

PATH = ".swarmai/review.md"
_TOKEN = re.compile(r"`match: ([^`]+)`")
_SECURITY = re.compile(r"^security:\s*`match: ([^`]+)`\s*$")
_EXT_GLOB = re.compile(r"\*\.[a-z0-9]+")


class GuideError(ValueError):
    """The file's pattern table or security line cannot be parsed."""


@dataclass(frozen=True)
class Row:
    """One pattern row, or the security line (``id == "security"``)."""

    id: str
    line: str
    always: bool
    globs: tuple[str, ...]
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class Guide:
    text: str
    header: tuple[str, ...] = ()
    rows: tuple[Row, ...] = ()
    security: Row | None = None
    ids: frozenset[str] = field(default_factory=frozenset)


def _spec(rid: str, line: str, spec: str) -> Row:
    terms = spec.split()
    if not terms:
        raise GuideError(f"{rid}: empty `match:` spec")
    if terms == ["*"]:
        return Row(rid, line, True, (), ())
    globs, kws = [], []
    for term in terms:
        kind, sep, value = term.partition(":")
        if not sep or not value or kind not in ("glob", "kw"):
            raise GuideError(f"{rid}: bad match term {term!r} in `match: {spec}`")
        (globs if kind == "glob" else kws).append(value.lower())
    return Row(rid, line, False, tuple(globs), tuple(kws))


def security_line(text: str) -> Row | None:
    """The ``security:`` declaration of *text*, parsed on its own so that a broken
    pattern table cannot hide it. Every line that starts with ``security:`` must be
    the declaration; one that does not parse raises GuideError (the caller then
    dispatches security, never skips it)."""
    security = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("security:"):
            continue
        sec = _SECURITY.match(stripped)
        if not sec:
            raise GuideError(f"malformed `security:` line: {stripped[:80]}")
        if security is not None:
            raise GuideError("more than one `security:` line")
        security = _spec("security", stripped, sec.group(1).strip())
    return security


def parse(text: str) -> Guide:
    """The guide in *text*. Raises GuideError on a malformed row or security line, so
    a typo cannot silently turn a row into one that never applies."""
    security = security_line(text)
    lines = text.splitlines()
    header: tuple[str, ...] = ()
    rows: list[Row] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("security:"):
            continue
        if not stripped.startswith("|"):
            continue
        specs = _TOKEN.findall(stripped)
        if not specs:
            continue
        if len(specs) > 1:
            raise GuideError(f"{stripped[:40]}: {len(specs)} `match:` tokens, expected one")
        rid = stripped.split("|", 2)[1].strip()
        if not rid:
            raise GuideError(f"a pattern row has no id: {stripped[:60]}")
        if not header:
            # the table's header: the line before its |---| separator
            j = i - 1
            while j >= 1 and lines[j].strip().startswith("|"):
                if re.fullmatch(r"\|[\s:|-]+\|", lines[j].strip()):
                    header = (lines[j - 1].strip(), lines[j].strip())
                    break
                j -= 1
        rows.append(_spec(rid, stripped, specs[0].strip()))
    ids = [r.id for r in rows]
    if len(ids) != len(set(ids)):
        raise GuideError("two pattern rows share an id")
    return Guide(text, header, tuple(rows), security, frozenset(ids))


def read_at(repo: str | Path, rev: str) -> str | None:
    """``.swarmai/review.md`` as of *rev* in *repo*; None when the file is not there.
    Any other git failure raises OSError (the caller reports it, never skips review)."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    probe = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{rev}:{PATH}"],
                           capture_output=True, text=True, timeout=30, env=env)
    if probe.returncode != 0:
        check = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{rev}^{{commit}}"],
                               capture_output=True, text=True, timeout=30, env=env)
        if check.returncode == 0:
            return None
        raise OSError(f"cannot read {PATH} at {rev}: {check.stderr.strip()}")
    shown = subprocess.run(["git", "-C", str(repo), "show", f"{rev}:{PATH}"],
                           capture_output=True, text=True, timeout=30, env=env)
    if shown.returncode != 0:
        raise OSError(f"cannot read {PATH} at {rev}: {shown.stderr.strip()}")
    return shown.stdout


def _norm(paths: Sequence[str]) -> list[str]:
    return [str(p).replace("\\", "/").lower() for p in paths]


# Spacing next to punctuation is not part of a keyword: `kw:port=` matches `port = 1`
# and `kw:eval(` matches `eval (x)`, as the old built-in `port\s*=` rule did. It can
# only widen a match (`report = x` holds `port=`), which errs toward more review.
_PUNCT_SPACE = re.compile(r"[ \t]*([^\w\s])[ \t]*")


def _squeeze(text: str) -> str:
    return _PUNCT_SPACE.sub(r"\1", text)


def _kw_hit(row: Row, norm: Sequence[str], diff_text: str) -> bool:
    hay = _squeeze("\n".join(norm) + "\n" + diff_text.lower())
    return any(_squeeze(k) in hay for k in row.keywords)


def applies(row: Row, paths: Sequence[str], diff_text: str = "") -> bool:
    """The row is always-on, a glob hits a path (or its basename), or a keyword occurs
    in a path or the diff."""
    if row.always:
        return True
    norm = _norm(paths)
    for p in norm:
        base = p.rsplit("/", 1)[-1]
        if any(fnmatch.fnmatchcase(p, g) or fnmatch.fnmatchcase(base, g) for g in row.globs):
            return True
    return _kw_hit(row, norm, diff_text)


def specific_hit(row: Row, paths: Sequence[str], diff_text: str = "") -> bool:
    """The row matched on evidence about THIS change: a keyword, or a glob narrower
    than a bare extension (``*.go`` fires on every Go file, so says nothing alone)."""
    if row.always:
        return False
    norm = _norm(paths)
    for g in row.globs:
        if _EXT_GLOB.fullmatch(g):
            continue
        if any(fnmatch.fnmatchcase(p, g) or fnmatch.fnmatchcase(p.rsplit("/", 1)[-1], g)
               for p in norm):
            return True
    return _kw_hit(row, norm, diff_text)


def select(guide: Guide, paths: Sequence[str], diff_text: str = "") -> list[Row]:
    return [row for row in guide.rows if applies(row, paths, diff_text)]


def _compact_line(row: Row) -> str:
    """``| id | name | trigger |`` without the match token or the long columns."""
    cells = row.line.split("|", 4)
    trigger = _TOKEN.sub("", cells[3]).strip() if len(cells) > 3 else ""
    return f"| {cells[1].strip()} | {cells[2].strip() if len(cells) > 2 else ''} | {trigger} |"


def compact(hits: Sequence[Row]) -> str:
    """The selected rows, one compact line each."""
    if not hits:
        return ""
    return "\n".join(["| id | Pattern | Trigger |", "|---|---|---|"]
                     + [_compact_line(row) for row in hits])
