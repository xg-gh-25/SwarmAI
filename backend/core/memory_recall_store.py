"""MemoryRecallStore — adapter exposing MEMORY.md entries to RecallEngine.

Why this exists (run_bbd79e84, Gate-1 finding 3):
    RecallEngine.search() calls each store's ``fts5_search(query, limit)``
    and consumes dicts with keys ``id / source_file / heading / content /
    fts_rank``. This adapter gives Memory a REAL keyword leg — memory has no
    FTS5 table, so keyword search is a LIKE scan over ``memory_entries``
    (title + keywords + full_text). For a ~400-entry table this is
    sub-millisecond and is the genuine recall value.

    NOTE (run_2f621986, design 2026-06-28 §3): the vector leg is GONE. This
    store once also implemented ``vector_search`` over memory_vec, but the
    pure-filesystem READ-line finalize removed every recall vector path (no
    recall query embeds). RecallEngine only calls a store's vector_search when
    query_embedding is not None, and every production caller passes
    allow_embed=False → embed_fn=None → that branch never fires. Recall is
    keyword/FTS5 only. fts5_search below is the sole live method.
"""

from __future__ import annotations

import logging
import re
import sqlite3

logger = logging.getLogger(__name__)

_SOURCE_FILE = "MEMORY.md"
# Match RecallEngine's FTS5 rank convention: LOWER rank = better. We synthesize a
# rank from negative match count so more matches sort first under "ORDER BY rank".
_WORD_RE = re.compile(r"[A-Za-z0-9一-鿿]+")


class MemoryRecallStore:
    """RecallEngine-compatible store over MEMORY.md entries (memory_entries table).

    Implements the keyword leg (fts5_search) RecallEngine.search() calls,
    returning the dict contract it consumes. No FTS5 table required — keyword
    leg is a LIKE scan. (The vector leg was removed — see module docstring.)
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def _table_exists(self) -> bool:
        try:
            row = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_entries'"
            ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    def fts5_search(self, query: str, limit: int = 20) -> list[dict]:
        """Keyword search over memory_entries (LIKE, no FTS5 table needed).

        Returns dicts in RecallEngine's contract:
            {id, source_file, heading, content, fts_rank}
        ``fts_rank`` is synthesized (negative match-count) so more matches sort
        first under RecallEngine's "lower rank = better" convention.
        """
        if not query or not query.strip() or not self._table_exists():
            return []

        # Distinct content words (>1 char), capped to keep the LIKE scan tight.
        terms = [w for w in {m.lower() for m in _WORD_RE.findall(query)} if len(w) > 1]
        if not terms:
            return []
        terms = terms[:15]

        results: list[dict] = []
        try:
            rows = self._conn.execute(
                "SELECT key, section, title, full_text, keywords FROM memory_entries"
            ).fetchall()
        except sqlite3.Error as exc:
            logger.debug("MemoryRecallStore.fts5_search query failed: %s", exc)
            return []

        for key, section, title, full_text, keywords in rows:
            haystack = f"{title or ''} {keywords or ''} {full_text or ''}".lower()
            match_count = sum(1 for t in terms if t in haystack)
            if match_count == 0:
                continue
            results.append({
                "id": key,
                "source_file": _SOURCE_FILE,
                "heading": section or "",
                "content": (full_text or title or "").strip(),
                # Lower rank = better → negative match count. Ties broken by
                # longer match coverage already captured in the count.
                "fts_rank": float(-match_count),
            })

        # Best (most negative rank) first; cap to limit.
        results.sort(key=lambda r: r["fts_rank"])
        return results[:limit]

    # NOTE: vector_search REMOVED (pure-filesystem READ-line finalize, run_2f621986,
    # design 2026-06-28 §3). The memory vector leg was dead in production —
    # RecallEngine only calls vector_search when query_embedding is not None, and
    # every production caller passes allow_embed=False → embed_fn=None → no
    # embedding → vector_search never reached. This store's LIVE value is the
    # keyword leg (fts5_search above): MEMORY.md has no FTS5 table, so the LIKE
    # scan is its only real keyword recall. That stays. RecallEngine never reaches
    # this store's vector leg: search() only calls store.vector_search when
    # query_embedding is not None, and the call is wrapped in try/except — so a
    # store lacking the method is doubly safe (short-circuit + caught AttributeError).
