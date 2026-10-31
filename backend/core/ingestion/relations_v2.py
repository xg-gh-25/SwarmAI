"""What a producing agent says about existing entries, and the check that holds it to them.

Design docs/design/2026-10-30-cdf-agent-decides.md §3 D1: the agent with the context
decides whether its new unit is new, the same as an entry, adds to one, or (for a
correction) repeats an earlier correction. Code never judges that; it does two things:

- ``candidates(...)`` — retrieval only: for one unit, the entries it may be related to
  (the BM25 best of the every-turn files, the General Brain and the project's DDD, the
  earlier units of the same session, and every captured correction), each shown as ``<id> | <first line>``
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
# BM25 candidates shown per unit: enough for a change to find every entry it makes untrue
TOP = 10
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


_GENERAL_CACHE: dict[str, tuple[tuple[int, int], list["Entry"] | None]] = {}


def _general_brain(workspace: Path) -> list[tuple[Path, list["Entry"]]]:
    """The General Brain documents CDF writes into (``route_general``) with their entries:
    the top-level Library documents that hold a CDF entry. A Library document no unit was
    ever written to (an org chart, a standard) and a reference CDF wrote (its header
    names the one candidate it came from) are not part of it. Parsed once per version of
    a file (its mtime and size)."""
    lib = Path(workspace) / "Knowledge" / "Library"
    if not lib.is_dir():
        return []
    out: list[tuple[Path, list[Entry]]] = []
    seen = set()
    for path in sorted(lib.glob("*.md")):
        try:
            st = path.stat()
        except OSError:
            continue
        key, stamp = str(path), (st.st_mtime_ns, st.st_size)
        seen.add(key)
        hit = _GENERAL_CACHE.get(key)
        if hit is None or hit[0] != stamp:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            brain = "<!-- cand:" in text and _is_brain_document(text)
            hit = (stamp, _entries_of(path, workspace) if brain else None)
            _GENERAL_CACHE[key] = hit
        if hit[1] is not None:
            out.append((path, hit[1]))
    for key in [k for k in _GENERAL_CACHE if k.startswith(str(lib)) and k not in seen]:
        del _GENERAL_CACHE[key]                       # a file that is gone
    return out


def general_brain_files(workspace: Path) -> list[Path]:
    """The General Brain documents (see ``_general_brain``): a main cognition file."""
    return [path for path, _ in _general_brain(workspace)]


def _general_entries(workspace: Path) -> list["Entry"]:
    return [entry for _, entries in _general_brain(workspace) for entry in entries]


def _is_brain_document(text: str) -> bool:
    """A General Brain document (many units, ``writer`` writes ``ingestion_candidate:
    n/a (multi-unit)`` in its header), not one reference (its header names the one
    candidate it came from)."""
    return "\ningestion_candidate: cand-" not in text


def pool(workspace: Path, project: str | None) -> list[Entry]:
    """Every entry a unit may be declared against: the every-turn files, the General
    Brain documents CDF writes into (read in every project, so a lesson filed there is
    found again from any project) and the main docs of *project*'s DDD (archives are not
    in force)."""
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
    return out + _general_entries(workspace)


TIMELINE_FILE = ".context/TIMELINE.md"


def timeline_entries(workspace: Path) -> list[Entry]:
    """The TIMELINE events: what a new event is declared against (an event is compared
    with events only; the shared pool stays the every-turn files and the DDD docs)."""
    path = Path(workspace) / TIMELINE_FILE
    return _entries_of(path, Path(workspace)) if path.is_file() else []


def withdrawn_entries(records) -> list[Entry]:
    """The completed withdrawals (``withdrawn.records``) as entries a unit may be declared
    against: one the user withdrew is refused when declared the same, one closed or done
    is a recurrence (pipeline_v2)."""
    from core.ddd_entry_lifecycle import unit_id
    return [Entry(unit_id(r.text), r.anchor, r.path, f"withdrawn:{r.status}", r.text) for r in records]


def candidates(text: str, entries: list[Entry], *, top: int = TOP,
               also: Iterable[Entry] = ()) -> list[Entry]:
    """The *top* entries of *entries* closest to *text* (BM25), plus *also* (the closest of
    the session's earlier units, the captured corrections), each once."""
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
    """The captured corrections, so a repeat of one is found: EVOLUTION's Corrections
    Captured, and the General Brain's ``[correction]`` entries (where a correction no user
    words back is kept, under its topic's section, e.g. "What Failed")."""
    return [e for e in entries
            if (e.path.endswith("EVOLUTION.md") and "Correction" in e.section)
            or (e.path.startswith("Knowledge/Library/")
                and e.text.lstrip().startswith("- [correction]"))]


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
