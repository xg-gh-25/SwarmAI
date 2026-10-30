"""What a producing agent says about existing entries, and the check that holds it to them.

Design docs/design/2026-10-30-cdf-agent-decides.md §3 D1: the agent with the context
decides whether its new unit is new, the same as an entry, adds to one, or (for a
correction) repeats an earlier correction. Code never judges that; it does two things:

- ``candidates(...)`` — retrieval only: for one unit, the entries it may be related to
  (the BM25 best of the every-turn files and the project's DDD, the earlier units of the
  same session, and every captured correction), each shown as ``<id> | <first line>``
  with its text, for the agent to declare against.
- ``resolve(ref, pool)`` — the fact check: a declaration ``"<8-character id> | <first
  line>"`` stands only when the id and the first line name exactly one entry of the pool
  (the same rule as an override, ``overrides.check``). A declaration that does not is
  ignored and recorded; the unit is then treated as new (nothing is lost).

Ids are ``ddd_entry_lifecycle.unit_id`` of the entry's text (what ``knowledge find``
prints for a DDD entry). ``restated`` finds the same text already written in the target
file (identity, not meaning). Public: ``Entry``, ``pool``, ``withdrawn_entries``,
``candidates``, ``corrections``, ``restated``, ``resolve``, ``MIN_HEAD``.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

MIN_HEAD = 12           # normalized characters of a declared first line that must match
TOP = 5                 # BM25 candidates shown per unit
EVERY_TURN_FILES = (".context/MEMORY.md", ".context/EVOLUTION.md", ".context/STEERING.md",
                    ".context/USER.md", ".context/TOOLS.md")


@dataclass(frozen=True)
class Entry:
    id: str
    anchor: str          # <path relative to the workspace>#<section>#<identity>
    path: str
    section: str
    text: str

    @property
    def head(self) -> str:
        return first_line(self.text)

    def shown(self) -> str:
        return f"{self.id} | {self.head}"


def first_line(text: str) -> str:
    clean = re.sub(r"<!--.*?-->", " ", text or "", flags=re.S)
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    return lines[0][:200] if lines else ""


def _norm(text: str) -> str:
    from .withdrawn import normalize
    return normalize(text)


def _entries_of(path: Path, workspace: Path) -> list[Entry]:
    from core.ddd_entry_lifecycle import unit_id
    from .units import parse_units
    try:
        units = parse_units(path, root=workspace)
        rel = path.resolve().relative_to(workspace.resolve()).as_posix()
    except (OSError, ValueError):
        return []
    out = []
    for unit in units:
        text = str(unit.text or "")
        body = [line for line in re.sub(r"<!--.*?-->", " ", text, flags=re.S).splitlines() if line.strip()]
        if not body or all(line.lstrip().startswith("#") for line in body):
            continue                                  # a heading alone is no entry
        out.append(Entry(unit_id(text), f"{rel}#{unit.section}#{unit.identity}", rel,
                         str(unit.section), text))
    return out


def pool(workspace: Path, project: str | None) -> list[Entry]:
    """Every entry a unit may be declared against: the every-turn files and the main
    docs of *project*'s DDD (archives are not in force)."""
    files = [workspace / rel for rel in EVERY_TURN_FILES]
    if project:
        from core.ddd_paths import ddd_path
        from core.project_registry import DDD_CANONICAL_DOCS
        root = workspace / "Projects" / project
        files += [ddd_path(root, doc) for doc in DDD_CANONICAL_DOCS]
    out: list[Entry] = []
    for path in files:
        if path.is_file():
            out += _entries_of(path, workspace)
    return out


def withdrawn_entries(records) -> list[Entry]:
    """The completed withdrawals (``withdrawn.records``) as entries a unit may be declared
    against: one the user withdrew is refused when declared the same, one closed or done
    is a recurrence (pipeline_v2)."""
    from core.ddd_entry_lifecycle import unit_id
    return [Entry(unit_id(r.text), r.anchor, r.path, f"withdrawn:{r.status}", r.text) for r in records]


def candidates(text: str, entries: list[Entry], *, top: int = TOP,
               also: Iterable[Entry] = ()) -> list[Entry]:
    """The *top* entries of *entries* closest to *text* (BM25), plus *also* (the
    session's earlier units, the captured corrections), each once."""
    from core.memory_index import _bm25_scores
    best: list[Entry] = []
    if (text or "").strip() and entries:
        scores = _bm25_scores(text, {str(i): e.text for i, e in enumerate(entries)})
        ranked = sorted(((s, int(i)) for i, s in scores.items() if s > 0), reverse=True)
        best = [entries[i] for _s, i in ranked[:top]]
    seen, out = set(), []
    for entry in [*best, *also]:
        if entry.anchor not in seen:
            seen.add(entry.anchor)
            out.append(entry)
    return out


def corrections(entries: list[Entry]) -> list[Entry]:
    """The captured corrections (EVOLUTION's Corrections Captured)."""
    return [e for e in entries if e.path.endswith("EVOLUTION.md") and "Correction" in e.section]


def restated(title: str, what: str, entries: list[Entry], path: str) -> Entry | None:
    """An entry of the file at *path* that holds the unit's title and *what* verbatim
    (after ``withdrawn.normalize``): the same text written again is the same entry, a fact,
    not a judgment. Text shorter than ``withdrawn.MIN_RESTATED`` is never matched."""
    from .withdrawn import MIN_RESTATED
    body = _norm(f"{title} {what}")
    if len(body) < MIN_RESTATED:
        return None
    found = [e for e in entries if e.path == path and body in _norm(e.text)]
    return found[0] if len(found) == 1 else None


def resolve(ref: str | None, entries: list[Entry]) -> tuple[Entry | None, str]:
    """(the entry *ref* names, "") or (None, why not)."""
    from .contracts_v2 import OVERRIDE_RE
    if not ref:
        return None, "none"
    m = OVERRIDE_RE.match(str(ref))
    if not m:
        return None, "malformed"
    uid, head = m.group(1), _norm(m.group(2))
    if len(head) < MIN_HEAD:
        return None, "head_too_short"
    found = [e for e in entries if e.id == uid and _norm(e.head).startswith(head)]
    if len({e.anchor for e in found}) != 1:
        return None, "not_found" if not found else "ambiguous"
    return found[0], ""
