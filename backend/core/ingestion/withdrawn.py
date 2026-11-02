"""What was withdrawn from the main cognition files, read from the records (design
docs/design/2026-10-30-brain-driven.md §2.2).

A withdrawal removes an entry from its main file; its original text, status and reason stay
in the write journal (``ingestion_writes`` op ``user_redact``: ``anchors_absent`` holds the
original, ``relations`` the redaction anchor, reason and status). Readers that need that
history read it here, never from the main files:

- re-ingestion: a unit that restates a withdrawn entry (status ``withdrawn``, or
  ``overridden`` by a newer declared decision) is refused, so what was taken out does not come back (it did once: a withdrawn lesson was written
  again into another file the next day). An exact restatement is refused before the judge
  (``restates``); a near one is shown to the judge with the unit's other hits
  (``nearest``), and a "same" or "adds" against it refuses the unit.
- recurrence: a closed gap or a done item (status ``closed`` / ``done``) that comes back is
  written again, as a new entry; it is never refused as a duplicate.
- undone: a withdrawal whose entry was put back (status ``restored``) is no longer a
  withdrawal: it is left out of ``records`` (``UNDONE``).

Public: ``Record``, ``records``, ``normalize``, ``restates``, ``nearest``, ``ANCHOR_PREFIX``,
``REFUSING``.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ANCHOR_PREFIX = "withdrawn:"
# a withdrawal that refuses a restatement; closed / done are history a recurrence may repeat
REFUSING = frozenset({"withdrawn", "overridden"})
# a withdrawal that was undone: its entry is back in the file
UNDONE = "restored"
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_LEAD = re.compile(r"^\s*-\s*\[[^\]]*\]\s*")
_MARKUP = re.compile(r"[*`_>#]+")
_SPACE = re.compile(r"\s+")
MIN_RESTATED = 40        # characters of a unit's body that must appear verbatim


@dataclass(frozen=True)
class Record:
    op_id: str
    path: str
    text: str
    status: str
    reason: str

    @property
    def anchor(self) -> str:
        return ANCHOR_PREFIX + self.op_id


def normalize(text: str) -> str:
    """Comparable form of an entry or a unit: no comments, no list lead or type label, no
    markdown markup, one space, lower case."""
    out = _COMMENT.sub(" ", text or "")
    out = "\n".join(_LEAD.sub("", line) for line in out.splitlines())
    return _SPACE.sub(" ", _MARKUP.sub(" ", out)).strip().lower()


def records(conn: sqlite3.Connection) -> list[Record]:
    """Every completed withdrawal, oldest first."""
    out: list[Record] = []
    for op_id, path, absent, relations in conn.execute(
            "SELECT op_id, target_path, anchors_absent, relations FROM ingestion_writes "
            "WHERE op_kind='user_redact' AND step='done' ORDER BY created_at"):
        try:
            texts = [t for t in json.loads(absent or "[]") if isinstance(t, str)]
            items = [r for r in json.loads(relations or "[]") if isinstance(r, dict)]
        except (TypeError, ValueError):
            continue
        if not texts:
            continue
        item = items[0] if items else {}
        if item.get("status") == UNDONE:
            continue
        out.append(Record(str(op_id), str(path or ""), texts[0], str(item.get("status") or "withdrawn"),
                          str(item.get("reason") or "")))
    return out


def _unit_body(unit: dict) -> str:
    parts = [str(unit.get(k) or "") for k in ("title", "what", "text", "why", "how")]
    return normalize(" ".join(p for p in parts if p))


def restates(unit: dict, withdrawn: list[Record]) -> Record | None:
    """The refusing withdrawal whose original contains the unit's body verbatim (after
    ``normalize``), or None. A body shorter than ``MIN_RESTATED`` is never matched."""
    body = normalize(str(unit.get("what") or unit.get("text") or ""))
    if len(body) < MIN_RESTATED:
        return None
    return next((r for r in withdrawn if r.status in REFUSING and body in normalize(r.text)), None)


def said_by(unit: dict, entries) -> bool:
    """Whether an entry in force (``relations_v2.Entry``) holds the unit's body verbatim
    (after ``normalize``), with the same body and length floor as ``restates``."""
    body = normalize(str(unit.get("what") or unit.get("text") or ""))
    if len(body) < MIN_RESTATED:
        return False
    return any(body in normalize(e.text) for e in entries)


def nearest(unit: dict, withdrawn: list[Record], k: int = 2) -> list[Record]:
    """The *k* withdrawals closest to the unit by keyword (BM25), best first; none that share
    no word with it."""
    if not withdrawn:
        return []
    from core.memory_index import _bm25_scores
    scores = _bm25_scores(_unit_body(unit), {str(i): normalize(r.text) for i, r in enumerate(withdrawn)})
    ranked = sorted((s, int(i)) for i, s in scores.items() if s > 0)[::-1]
    return [withdrawn[i] for _s, i in ranked[:k]]
