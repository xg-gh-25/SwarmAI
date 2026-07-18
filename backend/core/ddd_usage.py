"""DDD entry usage tracking — the access-decay signal for IMPROVEMENT.md lessons.

WHY THIS EXISTS
---------------
MEMORY.md entries carry stable IDs ([RC04]) so `.memory-usage.json` can record
which ones a session actually used, and the decay engine keeps used entries
alive. DDD/IMPROVEMENT.md entries have NO stable ID, so before this module they
decayed on AGE ALONE — a lesson recall surfaced every session still rotted at
the 60-day dormant threshold (`ddd_entry_lifecycle.assess_decay`).

This module is the DDD analogue of `.memory-usage.json`, keyed by a
content-derived ANCHOR instead of an ID:

  WRITE (recall):   session_router._inject_ddd_for_active_project, when it
                    surfaces an entry-level DDD hit, calls record_ddd_hit().
  READ  (decay):    ddd_orchestrator._ch_entry_lifecycle, before assess_decay,
                    calls load_ddd_usage() and bumps each matching entry's
                    last_referenced to the recorded hit date.

THE LOAD-BEARING INVARIANT (Gate-1 Blocker-A)
---------------------------------------------
The WRITE side sees recall's `entry-hit['content']`, which INCLUDES the trailing
`<!-- ref:N | last:... | decay:... -->` metadata line. The READ side sees
`EntryMetadata.raw_text`, which EXCLUDES it (parse_entries strips metadata). If
the anchor is not metadata-invariant, the two sides compute different keys, the
lookup misses every time, and the whole feature is a SILENT no-op.

`entry_anchor_text()` is the SINGLE shared normalizer both sides MUST call. It
strips the metadata comment line + the trailing `(YYYY-MM-DD, run_id)` stamp +
collapses whitespace, then takes a bounded prefix. See test_ddd_usage.py
::test_anchor_equal_across_metadata (the regression guard).

DESIGN CONSTRAINTS
------------------
- Pure filesystem + keyword anchor. Zero embeddings, zero Bedrock (recall is
  pure-filesystem since 2026-06-28).
- best-effort: every public write is wrapped so a failure NEVER propagates —
  recall must not be blocked by usage bookkeeping (recall_multi.py:24 principle).
- Bounded: `.ddd-usage.json` is capped at `_USAGE_CAP` anchors (evict-oldest by
  recorded date) so it cannot grow without bound.
- Concurrency-safe: read-modify-write of `.ddd-usage.json` holds the sibling
  `.ddd-usage.json.lock` (utils.file_lock.md_lock) so concurrent sessions don't
  lose writes.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Optional

from core.project_registry import get_projects_dir
from utils.file_lock import md_lock

# Max anchors kept in a project's .ddd-usage.json. Evict-oldest (by recorded
# date) beyond this. A disaster-recovery bound on file size, not a business
# knob — the log is a cache of "recently used", stale anchors are worthless.
_USAGE_CAP = 500

# Bounded prefix length for the anchor. Long enough to distinguish entries by
# their opening, short enough that a late edit to an entry's tail doesn't move
# the anchor. First-N-chars of the normalized entry.
_ANCHOR_PREFIX = 120

_USAGE_FILENAME = ".ddd-usage.json"

# Strip a full metadata comment line: "<!-- ref:N | last:X | decay:Y ... -->".
# Matches whether or not it is on its own line (the content side has it after a
# newline; be liberal).
_META_LINE_RE = re.compile(r"\s*<!--\s*ref:.*?-->\s*", re.DOTALL)
# Strip a trailing "(YYYY-MM-DD, run_xxx[, source])" provenance stamp — it is
# not part of the entry's identity and it differs across re-cultivations.
_TRAILING_STAMP_RE = re.compile(r"\(\d{4}-\d{2}-\d{2}[^)]*\)\s*$")
_WS_RE = re.compile(r"\s+")


def entry_anchor_text(text: str) -> str:
    """The SINGLE shared normalizer — MUST be called identically on both the
    write side (recall content, WITH metadata) and the read side (raw_text,
    WITHOUT metadata) so the anchors match.

    Strips: the ``<!-- ref:... -->`` metadata comment, the trailing
    ``(date, run_id)`` stamp, then collapses whitespace and lowercases. Returns
    the first ``_ANCHOR_PREFIX`` characters. Deterministic + pure.
    """
    if not text:
        return ""
    t = _META_LINE_RE.sub(" ", text)
    t = t.strip()
    # Remove trailing provenance stamp (possibly multiple, e.g. after metadata
    # strip left it at the tail).
    prev = None
    while prev != t:
        prev = t
        t = _TRAILING_STAMP_RE.sub("", t).strip()
    t = _WS_RE.sub(" ", t).strip().lower()
    return t[:_ANCHOR_PREFIX]


def _make_key(doc: str, section: str, anchor: str) -> str:
    return f"{doc}|{section}|{anchor}"


def _usage_path(project: str) -> Optional[Path]:
    try:
        return get_projects_dir() / project / _USAGE_FILENAME
    except Exception:
        return None


def record_ddd_hit(
    project: str,
    doc: str,
    section: str,
    anchor_text: str,
    hit_date: date,
) -> None:
    """Record that a DDD entry (identified by its content anchor) was surfaced
    by recall on ``hit_date``. best-effort: NEVER raises — recall must not be
    blocked by usage bookkeeping.

    ``anchor_text`` is the OUTPUT of ``entry_anchor_text()`` (callers normalize
    before calling, so the write and read sides share one normalization point).
    """
    try:
        if not anchor_text:
            return
        path = _usage_path(project)
        if path is None:
            return
        proj_dir = path.parent
        if not proj_dir.is_dir():
            # Do not create project dirs from here — a missing project is a
            # no-op, not an error to surface.
            return
        key = _make_key(doc, section, anchor_text)
        iso = hit_date.isoformat()
        with md_lock(path) as _got:  # blocking → always True
            data = _load_raw(path)
            data[key] = iso
            if len(data) > _USAGE_CAP:
                data = _evict_oldest(data, _USAGE_CAP)
            _atomic_write(path, data)
    except Exception:
        # best-effort: swallow everything (recall_multi.py:24 principle).
        return


def load_ddd_usage(project: str) -> dict[str, date]:
    """Return ``{key: last_hit_date}`` for a project. best-effort: returns ``{}``
    on any error. Key format: ``"<doc>|<section>|<anchor>"``.
    """
    try:
        path = _usage_path(project)
        if path is None or not path.is_file():
            return {}
        raw = _load_raw(path)
        out: dict[str, date] = {}
        for k, v in raw.items():
            try:
                out[k] = date.fromisoformat(v)
            except (ValueError, TypeError):
                continue
        return out
    except Exception:
        return {}


# ── internal helpers ────────────────────────────────────────────────────────
def _load_raw(path: Path) -> dict[str, str]:
    """Load the raw ``{key: iso_date_str}`` dict; ``{}`` on any error."""
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _evict_oldest(data: dict[str, str], cap: int) -> dict[str, str]:
    """Keep the ``cap`` most-recent anchors by ISO date (lexicographic on
    YYYY-MM-DD == chronological)."""
    # Sort by date descending, keep top `cap`.
    items = sorted(data.items(), key=lambda kv: kv[1], reverse=True)
    return dict(items[:cap])


def _atomic_write(path: Path, data: dict[str, str]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
