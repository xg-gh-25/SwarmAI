"""brain_graph — the entries of MEMORY.md and EVOLUTION.md for the C&M overlay's 记忆 tab.

The tab shows, for each file, how many entries it holds by type (a split bar with a
legend that filters the list) and the entries newest first. This module serves both:

- ``build_brain_graph(MEMORY.md)``: the 7-type ontology (principle / correction /
  decision / model / guideline / pitfall / process) from ``parse_entries``.
- ``build_evolution_graph(EVOLUTION.md)``: the ``### `` block entries, by kind
  (correction ``### Cnnn``, competence ``### Knnn`` under "## Competence Learned",
  class / data-point / … for older shapes).

Response shape (both): ``{nodes, drill, entries, total}``.

- ``nodes``: ``[{type, count}]`` in display-priority order, only the types that have
  entries (a zero-count type would be a legend chip with nothing behind it).
- ``drill``: ``{type: [entry]}`` for every type in the display order (an empty list for
  an absent one), ALL entries of the type, newest first.
- ``entries``: every entry, newest first (the tab's list; filtering by type is the
  frontend picking ``type``).
- ``total``: the entry count, equal to the sum of the node counts.

An entry is ``{title, meta, type, date}``; an evolution entry also has ``id`` (``C103``)
and ``label`` (its short title). ``date`` is ``YYYY-MM-DD`` or None: for MEMORY the
entry's trailing stamp ``(2026-11-02, run_…)``, else the first date in its line, else
its last-referenced date; for EVOLUTION the date in the header (``### C103 | 2026-11-24``).
Newest first sorts by that date, an undated entry last, keeping document order among
equal dates. Never raises on malformed content: empty-but-valid instead.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date as _date
from typing import Optional

from core.ddd_entry_lifecycle import VALID_TYPES, parse_entries

__all__ = [
    "build_brain_graph", "build_evolution_graph",
    "MEMORY_DISPLAY_ORDER", "EVOLUTION_DISPLAY_ORDER",
]

# DISPLAY-priority ordering. Node ORDER follows the cognitive priority, NOT the
# VALID_TYPES tuple order (9 callers depend on that tuple; it is left untouched).
# MEMORY_DISPLAY_ORDER MUST be a permutation of VALID_TYPES (a missing type would
# silently drop its entries from the nodes; an extra would be a phantom).
MEMORY_DISPLAY_ORDER = (
    "principle", "correction",      # meta-cognitive
    "decision", "model",            # cognitive
    "guideline", "pitfall", "process",  # operational
)

# EVOLUTION has its own vocabulary. Priority: recurring structural patterns (class),
# corrections, what the agent learned it can do (competence), then the supporting
# kinds; 'entry' is the catch-all fallback, LAST.
EVOLUTION_DISPLAY_ORDER = (
    "class", "correction", "competence", "meta-correction", "root-cause",
    "data-point", "directive", "failed-evolution", "entry",
)

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# A trailing stamp: "(2026-11-02)" / "(2026-08-25, run_f291ad72)".
_PAREN_DATE_RE = re.compile(r"\((\d{4}-\d{2}-\d{2})\b[^()]*\)")
# The leading id of an evolution header: C103 / K007 / F001.
_EVO_ID_RE = re.compile(r"^([A-Za-z]\d+)\b")
# The body's first bold after a field label: "- **Correction**: **Assert a state …**".
_BODY_BOLD_RE = re.compile(r"^\s*-\s*\*\*[^*]+\*\*\s*:\s*\*\*(.+?)\*\*")
# Leading bracket tags in a title: "[C-bias] Next-step proposals …".
_TAGS_RE = re.compile(r"^(?:\[[^\]]*\]\s*)+")
# Where a long bold title turns into its explanation.
_TITLE_CUTS = (": ", " — ", "; ")


def _valid_date(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    try:
        return _date.fromisoformat(s).isoformat()
    except ValueError:
        return None


def _newest_first(items: list[dict]) -> list[dict]:
    # sorted(reverse=True) is stable: equal dates keep document order; "" (undated) last
    return sorted(items, key=lambda it: it.get("date") or "", reverse=True)


def _memory_entry_date(e) -> Optional[str]:
    stamps = _PAREN_DATE_RE.findall(e.raw_text or "")
    for s in reversed(stamps):
        if _valid_date(s):
            return _valid_date(s)
    if e.created_date:
        return e.created_date.isoformat()
    if e.last_referenced:
        return e.last_referenced.isoformat()
    return None


def build_brain_graph(memory_content: str) -> dict:
    """The type counts and the entries of MEMORY.md (see the module docstring)."""
    try:
        entries = parse_entries(memory_content)
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).debug("build_brain_graph: suppressed exception on a fail-safe path", exc_info=True)
        entries = []

    items: list[dict] = []
    for e in entries:
        # parse_entries coerces unknown types to the default, so entry_type is always
        # in VALID_TYPES; guard anyway so total == sum(node counts) always holds
        if e.entry_type not in VALID_TYPES:
            continue
        items.append({
            "title": e.title,
            # meta: a short human hint (last-referenced date if known, else section)
            "meta": (e.last_referenced.isoformat() if e.last_referenced else e.section or ""),
            "type": e.entry_type,
            "date": _memory_entry_date(e),
        })
    items = _newest_first(items)
    counts = Counter(it["type"] for it in items)
    return {
        "nodes": [{"type": t, "count": counts[t]} for t in MEMORY_DISPLAY_ORDER if counts[t]],
        "drill": {t: [it for it in items if it["type"] == t] for t in MEMORY_DISPLAY_ORDER},
        "entries": items,
        "total": len(items),
    }


def _evo_label(header: str, ident: Optional[str], body: list[str]) -> str:
    """The short title of an evolution entry: the header's own text after its id and
    date ("C093 | 2026-11-01 · CLASS B: observe before asserting"), else the body's first
    bold, cut where it turns into its explanation; "" when there is neither (the header
    is only an id and a date: the id alone says it, not the header again)."""
    rest = header
    if ident:
        rest = rest[len(ident):]
    rest = _DATE_RE.sub("", rest)
    rest = _TAGS_RE.sub("", rest.strip(" |·-—\t")).strip(" |·-—\t")
    if rest:
        return rest
    for line in body:
        m = _BODY_BOLD_RE.match(line)
        if not m:
            continue
        text = _TAGS_RE.sub("", m.group(1).strip()).strip()
        for cut in _TITLE_CUTS:
            i = text.find(cut)
            if i >= 12:
                text = text[:i]
        return text.strip().rstrip(".")
    return ""


def build_evolution_graph(evolution_content: str) -> dict:
    """The kind counts and the entries of EVOLUTION.md (see the module docstring).

    EVOLUTION.md's entries are ``### `` blocks, which parse_entries cannot parse (it
    reads ``## `` sections and ``- `` bullets), so this scans the headers; a block runs
    to the next ``###`` or ``##``. Structural section markers ("Core Principle", …)
    are filtered by archive_browse.is_real_evolution_entry; a kind outside
    EVOLUTION_DISPLAY_ORDER folds into 'entry' so total == sum(node counts)."""
    from core.archive_browse import _evolution_kind, is_real_evolution_entry

    items: list[dict] = []
    try:
        blocks: list[tuple[str, list[str]]] = []
        current: Optional[tuple[str, list[str]]] = None
        for line in evolution_content.splitlines():
            if line.startswith("### "):
                current = (line[4:].strip(), [])
                blocks.append(current)
            elif line.startswith("## ") or line.startswith("# "):
                current = None
            elif current is not None:
                current[1].append(line)
        for header, body in blocks:
            if not is_real_evolution_entry(header):
                continue
            kind = _evolution_kind(header)
            if kind not in EVOLUTION_DISPLAY_ORDER:
                kind = "entry"
            m = _EVO_ID_RE.match(header)
            ident = m.group(1).upper() if m else None
            dm = _DATE_RE.search(header)
            items.append({
                "title": header,
                "meta": "",
                "type": kind,
                "date": _valid_date(dm.group(0) if dm else None),
                "id": ident,
                "label": _evo_label(header, ident, body),
            })
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).debug("build_evolution_graph: suppressed exception on a fail-safe path", exc_info=True)
        items = []

    items = _newest_first(items)
    counts = Counter(it["type"] for it in items)
    return {
        "nodes": [{"type": k, "count": counts[k]} for k in EVOLUTION_DISPLAY_ORDER if counts[k]],
        "drill": {k: [it for it in items if it["type"] == k] for k in EVOLUTION_DISPLAY_ORDER},
        "entries": items,
        "total": len(items),
    }
