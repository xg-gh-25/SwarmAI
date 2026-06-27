"""MemoryRecallStore — adapter exposing MEMORY.md entries to RecallEngine.

Why this exists (run_bbd79e84, Gate-1 finding 3):
    RecallEngine.search() calls each store as
        store.fts5_search(query, limit=top_k)        -> list[dict]
        store.vector_search(query_embedding, top_k)  -> list[dict]
    and consumes dicts with keys ``id / source_file / heading / content /
    fts_rank`` (keyword) and ``id / content / vector_score`` (vector).

    MemoryEmbeddingStore does NOT match that contract: it has no
    ``fts5_search`` at all, and its ``vector_search`` takes (text, embed_fn)
    and returns ``ScoredEntry`` objects — not the dict shape. Wiring it directly
    into ``additional_stores`` would be silently swallowed by RecallEngine's
    blanket try/except and contribute ZERO recall while *looking* wired (the
    exact "empty-but-looks-wired" half-product class).

    This adapter bridges the gap. It also gives Memory a REAL keyword leg —
    memory has no FTS5 table, so keyword search is a LIKE scan over
    ``memory_entries`` (title + keywords + full_text). For a ~400-entry table
    this is sub-millisecond and is the genuine value today (memory_vec is only
    ~5% populated, so the vector leg helps few queries until backfill).
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)

_SOURCE_FILE = "MEMORY.md"
# Match RecallEngine's FTS5 rank convention: LOWER rank = better. We synthesize a
# rank from negative match count so more matches sort first under "ORDER BY rank".
_WORD_RE = re.compile(r"[A-Za-z0-9一-鿿]+")


class MemoryRecallStore:
    """RecallEngine-compatible store over MEMORY.md entries (memory_entries table).

    Implements the two methods RecallEngine.search() calls, returning the dict
    contract it consumes. No FTS5 table required — keyword leg is a LIKE scan.
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

    def vector_search(self, query_embedding: list[float], top_k: int = 20) -> list[dict]:
        """Vector search over memory_vec, returned in RecallEngine's dict contract.

        Wraps MemoryEmbeddingStore.vector_search_raw (returns (key, distance))
        and joins memory_entries for content/heading. ``query_embedding`` is a
        raw vector (RecallEngine already embedded the query), matching the
        TranscriptStore signature — NOT MemoryEmbeddingStore.vector_search's
        (text, embed_fn) signature.
        """
        if query_embedding is None or not self._table_exists():
            return []

        try:
            from .memory_embeddings import MemoryEmbeddingStore
            raw = MemoryEmbeddingStore(self._conn).vector_search_raw(query_embedding, top_k)
        except Exception as exc:
            logger.debug("MemoryRecallStore.vector_search raw failed: %s", exc)
            return []

        if not raw:
            return []

        # Join entry metadata for the matched keys.
        keys = [k for k, _ in raw]
        placeholders = ",".join("?" * len(keys))
        meta: dict[str, tuple] = {}
        try:
            for key, section, title, full_text in self._conn.execute(
                f"SELECT key, section, title, full_text FROM memory_entries "
                f"WHERE key IN ({placeholders})",
                keys,
            ).fetchall():
                meta[key] = (section, title, full_text)
        except sqlite3.Error as exc:
            logger.debug("MemoryRecallStore.vector_search meta join failed: %s", exc)
            return []

        results: list[dict] = []
        for key, distance in raw:
            if key not in meta:
                continue
            section, title, full_text = meta[key]
            # sqlite-vec cosine distance = 2*(1-cos_sim) → similarity = 1 - dist/2.
            similarity = max(0.0, 1.0 - distance / 2.0)
            results.append({
                "id": key,
                "source_file": _SOURCE_FILE,
                "heading": section or "",
                "content": (full_text or title or "").strip(),
                "vector_score": similarity,
            })
        return results
