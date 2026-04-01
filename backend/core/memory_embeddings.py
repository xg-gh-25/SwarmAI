"""Hybrid Memory Retrieval — sqlite-vec embedding store + hybrid search.

Provides vector-augmented memory recall alongside existing keyword matching.
MEMORY.md remains the source of truth; this module maintains a retrieval index
in SQLite (memory_entries + memory_vec tables) with delta sync via content_hash.

Standing principle: **Power over token budget.** This exists to find memories
that keyword matching misses — not to save tokens.

Public symbols:

- ``ScoredEntry``            — Dataclass for hybrid-scored memory entries
- ``MemoryEmbeddingStore``   — SQLite store for memory entries + vector embeddings
- ``hybrid_memory_search``   — Merge keyword + vector scores (0.6v + 0.4k)
- ``HYBRID_THRESHOLD``       — Minimum hybrid score to include an entry
- ``VECTOR_WEIGHT``          — Weight for vector score (0.6)
- ``KEYWORD_WEIGHT``         — Weight for keyword score (0.4)
"""

import hashlib
import json
import logging
import sqlite3
import struct
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

VECTOR_WEIGHT = 0.6
KEYWORD_WEIGHT = 0.4
HYBRID_THRESHOLD = 0.10  # Low threshold — power first, surface everything plausible
EMBEDDING_DIM = 1024  # Bedrock Titan v2


# ── Data Models ───────────────────────────────────────────────────────


@dataclass
class ScoredEntry:
    """A memory entry with hybrid retrieval scores."""

    key: str
    hybrid: float
    keyword: float = 0.0
    vector: float = 0.0
    section: str = ""


# ── Hybrid Search ─────────────────────────────────────────────────────


def hybrid_memory_search(
    keyword_scores: dict[str, float],
    vector_scores: dict[str, float],
    threshold: float = HYBRID_THRESHOLD,
) -> list[ScoredEntry]:
    """Merge keyword and vector scores into a ranked list.

    Formula: hybrid = 0.6 * vector + 0.4 * keyword

    Args:
        keyword_scores: Dict mapping entry key → keyword relevance (0-1).
        vector_scores: Dict mapping entry key → vector similarity (0-1).
        threshold: Minimum hybrid score to include.

    Returns:
        Sorted list of ScoredEntry (highest score first).
    """
    all_keys = set(keyword_scores) | set(vector_scores)
    results: list[ScoredEntry] = []

    for key in all_keys:
        ks = keyword_scores.get(key, 0.0)
        vs = vector_scores.get(key, 0.0)
        hybrid = VECTOR_WEIGHT * vs + KEYWORD_WEIGHT * ks

        if hybrid >= threshold:
            results.append(ScoredEntry(
                key=key, hybrid=hybrid, keyword=ks, vector=vs,
            ))

    results.sort(key=lambda e: e.hybrid, reverse=True)
    return results


# ── Embedding Store ───────────────────────────────────────────────────


class MemoryEmbeddingStore:
    """SQLite store for memory entry metadata + vector embeddings.

    Uses sqlite-vec for vector search. Tables live in the provided
    SQLite connection (typically ~/.swarm-ai/data.db).

    The store is a **retrieval index** — MEMORY.md is the source of truth.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def ensure_tables(self) -> None:
        """Create tables if they don't exist."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_entries (
                key TEXT PRIMARY KEY,
                section TEXT NOT NULL,
                title TEXT NOT NULL,
                full_text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                keywords TEXT,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # sqlite-vec virtual table
        self._conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0(
                key TEXT PRIMARY KEY,
                embedding float[{EMBEDDING_DIM}]
            )
        """)
        self._conn.commit()

    def upsert_entry(
        self,
        key: str,
        section: str,
        title: str,
        full_text: str,
        keywords: list[str],
        embedding: Optional[list[float]] = None,
    ) -> None:
        """Insert or update a memory entry + optional embedding."""
        content_hash = hashlib.sha256(full_text.encode()).hexdigest()

        self._conn.execute("""
            INSERT INTO memory_entries (key, section, title, full_text, content_hash, keywords)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                section = excluded.section,
                title = excluded.title,
                full_text = excluded.full_text,
                content_hash = excluded.content_hash,
                keywords = excluded.keywords,
                updated_at = datetime('now')
        """, (key, section, title, full_text, content_hash, json.dumps(keywords)))

        if embedding is not None:
            self._upsert_vec(key, embedding)

        self._conn.commit()

    def _upsert_vec(self, key: str, embedding: list[float]) -> None:
        """Insert or replace vector embedding."""
        blob = struct.pack(f"{len(embedding)}f", *embedding)

        # sqlite-vec: delete then insert (no upsert on virtual tables)
        self._conn.execute("DELETE FROM memory_vec WHERE key = ?", (key,))
        self._conn.execute(
            "INSERT INTO memory_vec (key, embedding) VALUES (?, ?)",
            (key, blob),
        )

    def vector_search_raw(
        self,
        query_embedding: Optional[list[float]],
        top_k: int = 20,
    ) -> list[tuple[str, float]]:
        """Raw vector search. Returns list of (key, distance).

        Returns empty list if query_embedding is None (graceful fallback).
        """
        if query_embedding is None:
            return []

        blob = struct.pack(f"{len(query_embedding)}f", *query_embedding)

        rows = self._conn.execute(
            "SELECT key, distance FROM memory_vec "
            "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (blob, top_k),
        ).fetchall()

        return [(row[0], row[1]) for row in rows]

    def vector_search(
        self,
        query_text: str,
        top_k: int = 20,
        embed_fn: Optional[Callable[[str], Optional[list[float]]]] = None,
    ) -> list[ScoredEntry]:
        """Vector search with text input. Embeds query, then searches.

        Args:
            query_text: Natural language query.
            top_k: Max results.
            embed_fn: Function to embed text → vector. If None or returns None,
                      returns empty list (graceful fallback).

        Returns:
            List of ScoredEntry with vector scores only.
        """
        if embed_fn is None:
            return []

        query_embedding = embed_fn(query_text)
        if query_embedding is None:
            return []

        raw_results = self.vector_search_raw(query_embedding, top_k)

        results = []
        for key, distance in raw_results:
            # Convert cosine distance to similarity (0-1).
            # sqlite-vec returns distance = 2*(1 - cos_sim) for normalized vectors,
            # ranging 0 (identical) to 2 (opposite). So cos_sim = 1 - distance/2.
            similarity = max(0.0, 1.0 - distance / 2.0)
            results.append(ScoredEntry(
                key=key, hybrid=similarity, vector=similarity,
            ))

        return results

    def sync_from_memory(
        self,
        memory_content: str,
        embed_fn: Callable[[str], list[float]],
    ) -> dict:
        """Parse MEMORY.md and sync entries with delta detection.

        Only re-embeds entries whose content_hash has changed.

        Args:
            memory_content: Full MEMORY.md content.
            embed_fn: Function to convert text → embedding vector.

        Returns:
            Dict with sync stats: total_entries, embedded, skipped, removed.
        """
        from .memory_index import parse_memory_sections, _parse_entries, _extract_keywords
        from .memory_index import SECTION_KEY_PREFIX

        sections = parse_memory_sections(memory_content)
        stats = {"total_entries": 0, "embedded": 0, "skipped": 0, "removed": 0, "embed_failed": 0}

        # Get existing hashes
        existing_hashes: dict[str, str] = {}
        try:
            rows = self._conn.execute(
                "SELECT key, content_hash FROM memory_entries"
            ).fetchall()
            existing_hashes = {row[0]: row[1] for row in rows}
        except sqlite3.OperationalError:
            pass  # Table doesn't exist yet

        seen_keys: set[str] = set()

        for sec_name, prefix in SECTION_KEY_PREFIX.items():
            sec_content = sections.get(sec_name, "")
            entries = _parse_entries(sec_content)

            for i, entry in enumerate(entries, 1):
                key = f"{prefix}{i:02d}"
                seen_keys.add(key)
                stats["total_entries"] += 1

                full_text = entry["full_text"]
                title = entry.get("title", full_text[:60])
                keywords = _extract_keywords(full_text)
                content_hash = hashlib.sha256(full_text.encode()).hexdigest()

                # Delta check: skip if hash unchanged
                if existing_hashes.get(key) == content_hash:
                    stats["skipped"] += 1
                    continue

                # Embed and upsert (embedding=None is safe — metadata
                # still stored for keyword search, vector skipped)
                embedding = embed_fn(full_text)
                self.upsert_entry(key, sec_name, title, full_text, keywords, embedding)
                if embedding is not None:
                    stats["embedded"] += 1
                else:
                    stats["embed_failed"] += 1

        # Remove entries no longer in MEMORY.md
        for old_key in set(existing_hashes.keys()) - seen_keys:
            self._conn.execute("DELETE FROM memory_entries WHERE key = ?", (old_key,))
            self._conn.execute("DELETE FROM memory_vec WHERE key = ?", (old_key,))
            stats["removed"] += 1

        self._conn.commit()
        return stats

    def get_entry_sections(self) -> dict[str, str]:
        """Return mapping of entry key → section name."""
        rows = self._conn.execute(
            "SELECT key, section FROM memory_entries"
        ).fetchall()
        return {row[0]: row[1] for row in rows}
