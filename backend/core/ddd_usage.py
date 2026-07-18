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

"USED" PROXY — SOUNDNESS + KNOWN LIMITATION (Gate-2 meta-review)
----------------------------------------------------------------
The signal is "recall surfaced this entry", not "the agent cited it". A weaker
proxy than MEMORY's [ID]-citation model — but MEMORY's model is unavailable here
(DDD entries have no stable IDs). Why it is still sound, NOT an "immortal entry"
hazard: recall's `_recall_ddd` grafts only the SINGLE best-BM25 entry (`entry_best`,
score>0) into the injected bucket per query — so at most ONE entry is bumped per
session, and it is the single MOST-RELEVANT lesson to what the user is actually
working on. To stay alive across the 60-day dormant window an entry must be the
top match across many DIFFERENT sessions' queries — which is the definition of a
genuinely load-bearing lesson, exactly what we WANT to keep. A broadly-keyworded
but useless entry does not survive: it rarely wins top-1 against a more specific
match. KNOWN LIMITATION (accepted, not mitigated by a mechanism — over-engineering
a frequency-cap/citation-gate would be premature): a lesson that is persistently
the top match for a recurring real workstream stays active as long as that work
continues, then decays normally once it stops being recalled. That is correct
access-decay, not a leak. (If future data shows a specific entry bumped every
session for months while provably irrelevant, revisit — do not pre-build for it.)

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

# TRACKABLE-ENTRY SHAPE GATE — single source of truth shared with the decay
# engine. recall's _ddd_entry_hits surfaces ANY `^- ` line, but the decay engine
# (ddd_entry_lifecycle.parse_entries, default include_prose=False) ONLY tracks
# entries shaped `- [type] **Title** ...` / `- **Title** ...`. An entry the decay
# engine can't parse can NEVER be bumped — so anchoring it would only write a
# dead key that fills the cap and evicts a real one. We import parse_entries'
# OWN regex so "anchorable ⟺ decay-trackable" stays true by construction, not by
# a hand-copied duplicate that could drift (SMOKE run_644bfea6: 40/64 recall
# entry-hits were non-bold lines that would never match on read).
from core.ddd_entry_lifecycle import _ENTRY_RE as _TRACKABLE_ENTRY_RE  # noqa: E402


def entry_anchor_text(text: str) -> str:
    """The SINGLE shared normalizer — MUST be called identically on both the
    write side (recall content, WITH metadata) and the read side (raw_text,
    WITHOUT metadata) so the anchors match.

    Returns ``""`` for content that is NOT a decay-trackable entry (i.e. not
    matching ``ddd_entry_lifecycle._ENTRY_RE`` — no ``**Title**``). The write
    side skips empty anchors (record_ddd_hit no-ops on ""), so a recall hit on a
    non-bold line is never written as a dead key. The read side only ever passes
    ``EntryMetadata.raw_text`` (already a parsed bold entry), so it is unaffected.

    For a trackable entry: strips the ``<!-- ref:... -->`` metadata comment and
    the trailing ``(date, run_id)`` stamp, collapses whitespace, lowercases, and
    returns the first ``_ANCHOR_PREFIX`` characters. Deterministic + pure.
    """
    if not text:
        return ""
    # Shape gate: the FIRST line must be a trackable entry (bold title). recall
    # content and raw_text both start with the `- [type] **Title**` line.
    first_line = text.lstrip().split("\n", 1)[0]
    if not _TRACKABLE_ENTRY_RE.match(first_line):
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


def _usage_path(project: str) -> Optional[Path]:
    try:
        return get_projects_dir() / project / _USAGE_FILENAME
    except Exception:
        return None


def record_ddd_hit(
    project: str,
    anchor_text: str,
    hit_date: date,
) -> None:
    """Record that a DDD entry (identified by its content anchor) was surfaced
    by recall on ``hit_date``. best-effort: NEVER raises — recall must not be
    blocked by usage bookkeeping.

    ``anchor_text`` is the OUTPUT of ``entry_anchor_text()`` and IS the key —
    the anchor is a content fingerprint (proven 0 collisions across the real
    IMPROVEMENT.md, run_644bfea6), so doc/section are NOT part of the key. This
    is deliberate: recall's _ddd_entry_hits and parse_entries assign sub-section
    entries to DIFFERENT section names (parent vs sub-header), so keying on
    section caused silent read/write mismatches. Anchor-only is divergence-proof.
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
        key = anchor_text
        iso = hit_date.isoformat()
        with md_lock(path) as _got:  # blocking → always True
            data, load_ok = _load_raw_checked(path)
            # Don't-clobber-on-corruption (Gate-2 adversarial HIGH): if the file
            # EXISTS but failed to parse (load_ok=False), overwriting it with a
            # single fresh entry would silently discard the whole usage history.
            # A usage cache self-heals over time, but a distinguishable-corrupt
            # file must not be wiped by a bump — skip the write, let it be
            # repaired/aged out rather than truncated. (A cleanly-empty/absent
            # file has load_ok=True with {} → normal first write proceeds.)
            if not load_ok:
                return
            data[key] = iso
            if len(data) > _USAGE_CAP:
                data = _evict_oldest(data, _USAGE_CAP)
            _atomic_write(path, data)
    except Exception:
        # best-effort: swallow everything (recall_multi.py:24 principle).
        return


def load_ddd_usage(project: str) -> dict[str, date]:
    """Return ``{key: last_hit_date}`` for a project. best-effort: returns ``{}``
    on any error. Key = the entry content anchor (see record_ddd_hit).
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
    """Load the raw ``{key: iso_date_str}`` dict; ``{}`` on any error.

    Used by the READ path (load_ddd_usage) where a corrupt/absent file simply
    means "no usage data" → no bumps this cycle, which is safe. The WRITE path
    uses _load_raw_checked instead, because there ``{}`` on corruption would
    cause a destructive overwrite (see record_ddd_hit).
    """
    return _load_raw_checked(path)[0]


def _load_raw_checked(path: Path) -> tuple[dict[str, str], bool]:
    """Load the raw dict AND report whether the load was clean.

    Returns ``(data, ok)``:
    - absent file      → ``({}, True)``   — a clean empty state (first write OK)
    - valid JSON dict  → ``(data, True)``
    - valid JSON non-dict / parse error / read error → ``({}, False)`` —
      the file exists but is unusable; callers that would OVERWRITE must NOT
      (don't clobber a corrupt-but-present file with a single fresh entry).
    """
    try:
        if not path.is_file():
            return {}, True  # absent = clean empty, safe to write fresh
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data, True
        return {}, False  # present but wrong shape → corrupt, don't clobber
    except Exception:
        return {}, False  # present but unparseable/unreadable → don't clobber


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
