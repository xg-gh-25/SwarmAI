"""Knowledge Library Indexing — scan, chunk, and index Knowledge/ files.

Provides searchable FTS5 + sqlite-vec index over the entire Knowledge/ directory
(DailyActivity, Designs, Notes, AIDLC, Signals, Library, etc.). Delta-sync via
content_hash ensures only changed chunks are re-embedded.

This module is the Phase 1 foundation for the Recall Engine (Phase 2).
MEMORY.md (Brain) stays source of truth for semantic memory — this indexes
the 730K tokens of episodic memory in Knowledge/ (Library).

Public symbols:

- ``KnowledgeStore``          — SQLite store for chunks + FTS5 + vec
- ``chunk_markdown``          — Split markdown by heading into chunks
- ``sync_knowledge_index``    — Top-level: scan dir, chunk, delta-sync
"""

import hashlib
import json
import logging
import re
import struct
import sqlite3
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

EMBEDDING_DIM = 1024  # Bedrock Titan v2

# Directories to skip when scanning Knowledge/
_SKIP_DIRS = {"Archives", "__pycache__", ".git", ".artifacts"}

# Heading regex: ## or ### (not #, which is the file title)
_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+)$", re.MULTILINE)


# ── Chunking ──────────────────────────────────────────────────────────


def chunk_markdown(
    content: str,
    source_file: str,
) -> list[dict]:
    """Split markdown content into chunks by ## headings.

    Each chunk includes the heading as context. Files without headings
    produce a single chunk with the entire content.

    Args:
        content: Raw markdown text.
        source_file: Relative path (e.g. "DailyActivity/2026-04-01.md").

    Returns:
        List of chunk dicts with keys: source_file, chunk_index, heading,
        content, content_hash.
    """
    if not content or not content.strip():
        return []

    # Find all ## and ### headings
    matches = list(_HEADING_RE.finditer(content))

    if not matches:
        # No headings — single chunk with full content
        text = content.strip()
        return [{
            "source_file": source_file,
            "chunk_index": 0,
            "heading": None,
            "content": text,
            "content_hash": hashlib.sha256(text.encode()).hexdigest(),
        }]

    chunks: list[dict] = []

    # Content before first heading (intro/frontmatter)
    pre_content = content[:matches[0].start()].strip()
    if pre_content and len(pre_content) > 20:
        chunks.append({
            "source_file": source_file,
            "chunk_index": len(chunks),
            "heading": None,
            "content": pre_content,
            "content_hash": hashlib.sha256(pre_content.encode()).hexdigest(),
        })

    # Each heading → next heading (or end)
    for i, match in enumerate(matches):
        heading = match.group(2).strip()
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)

        text = content[start:end].strip()
        if not text:
            continue

        chunks.append({
            "source_file": source_file,
            "chunk_index": len(chunks),
            "heading": heading,
            "content": text,
            "content_hash": hashlib.sha256(text.encode()).hexdigest(),
        })

    return chunks


# ── KnowledgeStore ────────────────────────────────────────────────────


class KnowledgeStore:
    """SQLite store for knowledge chunks with FTS5 + sqlite-vec.

    Tables:
    - knowledge_chunks: structured chunk data with content_hash for delta sync
    - knowledge_vec: sqlite-vec virtual table for vector search
    - knowledge_fts: FTS5 virtual table for keyword search
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def ensure_tables(self) -> None:
        """Create tables if they don't exist."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                heading TEXT,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                metadata TEXT,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self._conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_chunk_source
            ON knowledge_chunks(source_file, chunk_index)
        """)

        # sqlite-vec virtual table
        self._conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_vec USING vec0(
                id INTEGER PRIMARY KEY,
                embedding float[{EMBEDDING_DIM}]
            )
        """)

        # FTS5 for keyword search — content-sync'd with knowledge_chunks
        # Using external content table pattern for FTS5
        self._conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                content, heading, source_file,
                content=knowledge_chunks, content_rowid=id
            )
        """)

        self._conn.commit()

    def upsert_chunk(
        self,
        source_file: str,
        chunk_index: int,
        heading: Optional[str],
        content: str,
        content_hash: str,
        metadata: Optional[dict] = None,
        embedding: Optional[list[float]] = None,
    ) -> int:
        """Insert or update a chunk. Returns the chunk rowid."""
        metadata_json = json.dumps(metadata) if metadata else None

        # Check if exists
        existing = self._conn.execute(
            "SELECT id FROM knowledge_chunks WHERE source_file = ? AND chunk_index = ?",
            (source_file, chunk_index),
        ).fetchone()

        if existing:
            rowid = existing[0]
            # Delete old FTS5 entry before update
            self._conn.execute(
                "INSERT INTO knowledge_fts(knowledge_fts, rowid, content, heading, source_file) "
                "VALUES('delete', ?, ?, ?, ?)",
                (rowid, content, heading or "", source_file),
            )
            # Update the chunk
            self._conn.execute(
                "UPDATE knowledge_chunks SET heading = ?, content = ?, content_hash = ?, "
                "metadata = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (heading, content, content_hash, metadata_json, rowid),
            )
        else:
            cursor = self._conn.execute(
                "INSERT INTO knowledge_chunks (source_file, chunk_index, heading, content, content_hash, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (source_file, chunk_index, heading, content, content_hash, metadata_json),
            )
            rowid = cursor.lastrowid

        # Insert FTS5 entry
        self._conn.execute(
            "INSERT INTO knowledge_fts(rowid, content, heading, source_file) VALUES(?, ?, ?, ?)",
            (rowid, content, heading or "", source_file),
        )

        # Optional: store vector embedding
        if embedding is not None:
            self._upsert_vec(rowid, embedding)

        self._conn.commit()
        return rowid

    def _upsert_vec(self, rowid: int, embedding: list[float]) -> None:
        """Insert or replace vector embedding for a chunk."""
        blob = struct.pack(f"{len(embedding)}f", *embedding)
        self._conn.execute("DELETE FROM knowledge_vec WHERE id = ?", (rowid,))
        self._conn.execute(
            "INSERT INTO knowledge_vec (id, embedding) VALUES (?, ?)",
            (rowid, blob),
        )

    def get_existing_hashes(self, source_file: str) -> dict[int, str]:
        """Get content_hash for all chunks of a file. Returns {chunk_index: hash}."""
        rows = self._conn.execute(
            "SELECT chunk_index, content_hash FROM knowledge_chunks WHERE source_file = ?",
            (source_file,),
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def remove_stale_chunks(self, source_file: str, keep_indexes: set[int]) -> int:
        """Remove chunks not in keep_indexes. Returns count removed."""
        rows = self._conn.execute(
            "SELECT id, chunk_index, content, heading FROM knowledge_chunks WHERE source_file = ?",
            (source_file,),
        ).fetchall()

        removed = 0
        for rowid, idx, content, heading in rows:
            if idx not in keep_indexes:
                # Delete from FTS5 first
                self._conn.execute(
                    "INSERT INTO knowledge_fts(knowledge_fts, rowid, content, heading, source_file) "
                    "VALUES('delete', ?, ?, ?, ?)",
                    (rowid, content, heading or "", source_file),
                )
                self._conn.execute("DELETE FROM knowledge_vec WHERE id = ?", (rowid,))
                self._conn.execute("DELETE FROM knowledge_chunks WHERE id = ?", (rowid,))
                removed += 1

        if removed:
            self._conn.commit()
        return removed

    def remove_file_entries(self, source_file: str) -> int:
        """Remove all chunks for a file. Returns count removed."""
        rows = self._conn.execute(
            "SELECT id, content, heading FROM knowledge_chunks WHERE source_file = ?",
            (source_file,),
        ).fetchall()

        for rowid, content, heading in rows:
            self._conn.execute(
                "INSERT INTO knowledge_fts(knowledge_fts, rowid, content, heading, source_file) "
                "VALUES('delete', ?, ?, ?, ?)",
                (rowid, content, heading or "", source_file),
            )
            self._conn.execute("DELETE FROM knowledge_vec WHERE id = ?", (rowid,))

        self._conn.execute(
            "DELETE FROM knowledge_chunks WHERE source_file = ?",
            (source_file,),
        )
        self._conn.commit()
        return len(rows)

    def fts5_search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict]:
        """Full-text search via FTS5. Returns chunks ranked by relevance."""
        if not query or not query.strip():
            return []

        # Escape special FTS5 characters and build query
        # Strip FTS5 operators and escape quotes/parens to prevent OperationalError
        clean_words = []
        for word in query.split():
            if not word or word.startswith(("-", "+", "*")):
                continue
            # Strip FTS5 special chars: " ( ) { } ^
            cleaned = re.sub(r'["\(\)\{\}\^]', '', word)
            if cleaned:
                clean_words.append(cleaned)
        clean_query = " ".join(clean_words)
        if not clean_query:
            return []

        try:
            rows = self._conn.execute(
                "SELECT kc.id, kc.source_file, kc.chunk_index, kc.heading, kc.content, "
                "rank "
                "FROM knowledge_fts fts "
                "JOIN knowledge_chunks kc ON kc.id = fts.rowid "
                "WHERE knowledge_fts MATCH ? "
                "ORDER BY rank "
                "LIMIT ?",
                (clean_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # FTS5 query syntax error — fall back to simpler query
            logger.debug("FTS5 query failed for '%s', trying individual terms", query)
            return self._fts5_fallback_search(query, limit)

        return [
            {
                "id": row[0],
                "source_file": row[1],
                "chunk_index": row[2],
                "heading": row[3],
                "content": row[4],
                "fts_rank": row[5],
            }
            for row in rows
        ]

    def _fts5_fallback_search(self, query: str, limit: int) -> list[dict]:
        """Fallback: search each word with OR."""
        words = [w for w in query.split() if len(w) > 2]
        if not words:
            return []

        or_query = " OR ".join(words)
        try:
            rows = self._conn.execute(
                "SELECT kc.id, kc.source_file, kc.chunk_index, kc.heading, kc.content, "
                "rank "
                "FROM knowledge_fts fts "
                "JOIN knowledge_chunks kc ON kc.id = fts.rowid "
                "WHERE knowledge_fts MATCH ? "
                "ORDER BY rank "
                "LIMIT ?",
                (or_query, limit),
            ).fetchall()
            return [
                {
                    "id": row[0],
                    "source_file": row[1],
                    "chunk_index": row[2],
                    "heading": row[3],
                    "content": row[4],
                    "fts_rank": row[5],
                }
                for row in rows
            ]
        except sqlite3.OperationalError:
            return []

    def vector_search(
        self,
        query_embedding: Optional[list[float]],
        top_k: int = 20,
    ) -> list[dict]:
        """Vector similarity search. Returns chunks with distance scores."""
        if query_embedding is None:
            return []

        blob = struct.pack(f"{len(query_embedding)}f", *query_embedding)
        try:
            rows = self._conn.execute(
                "SELECT id, distance FROM knowledge_vec "
                "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (blob, top_k),
            ).fetchall()
        except sqlite3.OperationalError:
            return []

        results = []
        for vec_id, distance in rows:
            # Fetch chunk metadata
            chunk = self._conn.execute(
                "SELECT source_file, chunk_index, heading, content "
                "FROM knowledge_chunks WHERE id = ?",
                (vec_id,),
            ).fetchone()
            if chunk:
                similarity = max(0.0, 1.0 - distance / 2.0)
                results.append({
                    "id": vec_id,
                    "source_file": chunk[0],
                    "chunk_index": chunk[1],
                    "heading": chunk[2],
                    "content": chunk[3],
                    "vector_score": similarity,
                })
        return results

    def get_indexed_files(self) -> set[str]:
        """Return the set of source_files currently indexed."""
        rows = self._conn.execute(
            "SELECT DISTINCT source_file FROM knowledge_chunks"
        ).fetchall()
        return {row[0] for row in rows}


# ── Top-level sync ────────────────────────────────────────────────────


def sync_knowledge_index(
    store: "KnowledgeStore",
    knowledge_dir: Path,
    embed_fn: Optional[Callable[[str], Optional[list[float]]]] = None,
) -> dict:
    """Scan Knowledge/ directory, chunk, and delta-sync to the store.

    Args:
        store: KnowledgeStore instance (tables must be ensured).
        knowledge_dir: Path to Knowledge/ directory.
        embed_fn: Optional embedding function. If None, skips vector indexing.

    Returns:
        Stats dict: files_scanned, chunks_added, chunks_skipped,
        chunks_removed, files_removed, embed_calls.
    """
    stats = {
        "files_scanned": 0,
        "chunks_added": 0,
        "chunks_skipped": 0,
        "chunks_removed": 0,
        "files_removed": 0,
        "embed_calls": 0,
    }

    if not knowledge_dir.is_dir():
        return stats

    # Scan all .md files
    current_files: dict[str, Path] = {}  # relative_path → full_path
    for subdir in sorted(knowledge_dir.iterdir()):
        if not subdir.is_dir() or subdir.name in _SKIP_DIRS:
            continue
        for md_file in sorted(subdir.rglob("*.md")):
            if md_file.is_file():
                rel_path = f"{subdir.name}/{md_file.relative_to(subdir)}"
                current_files[rel_path] = md_file

    # Remove entries for deleted files
    indexed_files = store.get_indexed_files()
    for old_file in indexed_files - set(current_files.keys()):
        store.remove_file_entries(old_file)
        stats["files_removed"] += 1

    # Process each file
    for rel_path, full_path in current_files.items():
        stats["files_scanned"] += 1

        try:
            content = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        chunks = chunk_markdown(content, rel_path)
        existing_hashes = store.get_existing_hashes(rel_path)

        new_indexes: set[int] = set()
        for chunk in chunks:
            idx = chunk["chunk_index"]
            new_indexes.add(idx)

            # Delta check
            if existing_hashes.get(idx) == chunk["content_hash"]:
                stats["chunks_skipped"] += 1
                continue

            # Embed if available
            embedding = None
            if embed_fn is not None:
                embedding = embed_fn(chunk["content"])
                stats["embed_calls"] += 1

            store.upsert_chunk(
                source_file=rel_path,
                chunk_index=idx,
                heading=chunk.get("heading"),
                content=chunk["content"],
                content_hash=chunk["content_hash"],
                embedding=embedding,
            )
            stats["chunks_added"] += 1

        # Remove chunks that no longer exist in this file
        removed = store.remove_stale_chunks(rel_path, new_indexes)
        stats["chunks_removed"] += removed

    return stats
