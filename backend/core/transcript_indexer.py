"""Transcript Semantic Indexing — parse, chunk, index JSONL transcripts.

Provides searchable FTS5 + sqlite-vec index over Claude Code session
transcripts (JSONL). Delta-sync via content_hash ensures only new
sessions are indexed. Follows the same pattern as knowledge_store.py.

Core insight (MemPalace, April 2026): raw verbatim storage + semantic search
scores 96.6% on LongMemEval R@5 — 12.4% higher than LLM-summarized storage.
"Intelligence at read time, not write time."

Public symbols:

- ``TranscriptStore``          — SQLite store for transcript chunks + FTS5 + vec
- ``chunk_transcript``         — Split JSONL turns into conversation-pair chunks
- ``parse_transcript``         — Parse JSONL file into turn records
- ``sync_transcript_index``    — Top-level: scan dir, chunk, delta-sync
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
DEFAULT_MAX_TOKENS = 500  # tokens per chunk
_CHARS_PER_TOKEN = 4  # rough estimate

# Patterns for metadata extraction
_FILE_PATTERN = re.compile(r'[\w/.-]+\.(?:py|ts|tsx|js|md|json|yaml|toml|sql|sh|rs)')
_TOOL_PATTERN = re.compile(r'\b(?:Read|Edit|Write|Bash|Grep|Glob|Agent|WebFetch)\b')


# ── Parsing ──────────────────────────────────────────────────────────


def parse_transcript(path: Path) -> list[dict]:
    """Parse a JSONL transcript file into user/assistant turn records.

    Each record is a dict with keys: role, content.
    Skips malformed lines and non-user/assistant types.

    Args:
        path: Path to .jsonl transcript file.

    Returns:
        List of turn dicts with ``role`` and ``content`` keys.
    """
    records: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("Malformed JSON at %s:%d, skipping", path.name, line_num)
                    continue

                rtype = record.get("type", "")
                if rtype not in ("user", "assistant"):
                    continue

                msg = record.get("message", {})
                content = msg.get("content", "")
                if isinstance(content, list):
                    # Handle content blocks (text, tool_use, etc.)
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                            elif block.get("type") == "tool_use":
                                text_parts.append(f"[tool: {block.get('name', '?')}]")
                            elif block.get("type") == "tool_result":
                                text_parts.append(f"[tool_result]")
                        elif isinstance(block, str):
                            text_parts.append(block)
                    content = "\n".join(text_parts)

                if content:
                    records.append({
                        "role": msg.get("role", rtype),
                        "content": content,
                    })
    except (OSError, IOError) as exc:
        logger.warning("Failed to read transcript %s: %s", path, exc)

    return records


# ── Chunking ─────────────────────────────────────────────────────────


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars/token for mixed content."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _extract_metadata(content: str) -> str:
    """Extract structured metadata from chunk content as JSON string."""
    files = list(set(_FILE_PATTERN.findall(content)))[:10]
    tools = list(set(_TOOL_PATTERN.findall(content)))
    has_code = "```" in content or "def " in content or "import " in content
    has_error = any(kw in content.lower() for kw in ("error", "traceback", "exception", "failed"))

    meta = {
        "files_mentioned": files,
        "tools_used": tools,
        "has_code": has_code,
        "has_error": has_error,
    }
    return json.dumps(meta)


def chunk_transcript(
    turns: list[dict],
    source_file: str,
    session_id: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[dict]:
    """Split conversation turns into chunks, preserving user+assistant pairs.

    Chunks at ``max_tokens`` boundaries, keeping user+assistant exchanges
    together when possible. Each chunk gets a content_hash for delta sync.

    Args:
        turns: List of turn dicts from parse_transcript().
        source_file: Relative path to source JSONL.
        session_id: Session identifier.
        max_tokens: Target maximum tokens per chunk.

    Returns:
        List of chunk dicts with keys: source_file, session_id, chunk_index,
        role, content, content_hash, metadata.
    """
    if not turns:
        return []

    chunks: list[dict] = []
    current_parts: list[str] = []
    current_tokens = 0
    chunk_index = 0

    def _flush():
        nonlocal chunk_index
        if not current_parts:
            return
        text = "\n\n".join(current_parts)
        # Determine role: mixed if both user and assistant
        has_user = any("[user]" in p for p in current_parts)
        has_assistant = any("[assistant]" in p for p in current_parts)
        if has_user and has_assistant:
            role = "mixed"
        elif has_user:
            role = "user"
        elif has_assistant:
            role = "assistant"
        else:
            role = "mixed"

        chunks.append({
            "source_file": source_file,
            "session_id": session_id,
            "chunk_index": chunk_index,
            "role": role,
            "content": text,
            "content_hash": hashlib.sha256(text.encode()).hexdigest(),
            "metadata": _extract_metadata(text),
        })
        chunk_index += 1

    for turn in turns:
        role = turn.get("role", "unknown")
        content = turn.get("content", "")
        part = f"[{role}] {content}"
        part_tokens = _estimate_tokens(part)

        # If adding this turn would exceed budget and we have content, flush
        if current_tokens + part_tokens > max_tokens and current_parts:
            _flush()
            current_parts = []
            current_tokens = 0

        # If a single turn exceeds max_tokens, split it
        if part_tokens > max_tokens and not current_parts:
            # Split by lines
            lines = part.split("\n")
            for line in lines:
                line_tokens = _estimate_tokens(line)
                if current_tokens + line_tokens > max_tokens and current_parts:
                    _flush()
                    current_parts = []
                    current_tokens = 0
                current_parts.append(line)
                current_tokens += line_tokens
            if current_parts:
                _flush()
                current_parts = []
                current_tokens = 0
            continue

        current_parts.append(part)
        current_tokens += part_tokens

    # Flush remaining
    _flush()

    return chunks


# ── TranscriptStore ──────────────────────────────────────────────────


def _serialize_f32(vec: list[float]) -> bytes:
    """Serialize float list to little-endian f32 bytes for sqlite-vec."""
    return struct.pack(f"<{len(vec)}f", *vec)


class TranscriptStore:
    """SQLite store for transcript chunks with FTS5 + sqlite-vec indexes.

    Follows the same pattern as KnowledgeStore — separate tables for
    chunks, vectors, and full-text search. Delta-sync via content_hash.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def ensure_tables(self) -> None:
        """Create transcript tables if they don't exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS transcript_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                source_file TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT 'mixed',
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_transcript_session_chunk
            ON transcript_chunks(session_id, chunk_index);
        """)

        # FTS5 virtual table (external content pattern)
        try:
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5(
                    content, source_file,
                    content=transcript_chunks, content_rowid=id
                )
            """)
        except sqlite3.OperationalError:
            logger.debug("transcript_fts already exists or FTS5 unavailable")

        # sqlite-vec virtual table
        try:
            self._conn.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS transcript_vec USING vec0(
                    id INTEGER PRIMARY KEY,
                    embedding float[{EMBEDDING_DIM}]
                )
            """)
        except sqlite3.OperationalError:
            logger.debug("transcript_vec already exists or sqlite-vec unavailable")

    def upsert_chunk(
        self,
        session_id: str,
        source_file: str,
        chunk_index: int,
        role: str,
        content: str,
        content_hash: str,
        metadata: str = "{}",
        embedding: Optional[list[float]] = None,
    ) -> int:
        """Insert or update a transcript chunk. Returns the row id."""
        # Check if exists
        existing = self._conn.execute(
            "SELECT id, content_hash FROM transcript_chunks "
            "WHERE session_id = ? AND chunk_index = ?",
            (session_id, chunk_index),
        ).fetchone()

        if existing:
            row_id, old_hash = existing
            if old_hash == content_hash:
                return row_id  # Unchanged
            # Update
            self._conn.execute(
                "UPDATE transcript_chunks SET source_file=?, role=?, content=?, "
                "content_hash=?, metadata=?, created_at=datetime('now') "
                "WHERE id=?",
                (source_file, role, content, content_hash, metadata, row_id),
            )
        else:
            cursor = self._conn.execute(
                "INSERT INTO transcript_chunks "
                "(session_id, source_file, chunk_index, role, content, content_hash, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, source_file, chunk_index, role, content, content_hash, metadata),
            )
            row_id = cursor.lastrowid

        # Sync FTS5
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO transcript_fts(rowid, content, source_file) "
                "VALUES (?, ?, ?)",
                (row_id, content, source_file),
            )
        except sqlite3.OperationalError:
            pass  # FTS5 not available

        # Sync vector
        if embedding:
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO transcript_vec(id, embedding) VALUES (?, ?)",
                    (row_id, _serialize_f32(embedding)),
                )
            except sqlite3.OperationalError:
                pass  # sqlite-vec not available

        return row_id

    def commit(self) -> None:
        """Commit pending changes. Call after batch upserts."""
        self._conn.commit()

    def fts5_search(self, query: str, limit: int = 20) -> list[dict]:
        """Search transcript chunks via FTS5 keyword matching."""
        if not query or not query.strip():
            return []

        results: list[dict] = []
        try:
            # Tokenize into individual words, sanitize FTS5 metacharacters
            words = query.split()
            clean_words = []
            for w in words:
                w = re.sub(r'["\(\)\{\}\^\*\-\+]', '', w).strip()
                if w and len(w) > 1:
                    clean_words.append(w)
            if not clean_words:
                return results
            safe_query = " OR ".join(f'"{w}"' for w in clean_words)
            rows = self._conn.execute(
                "SELECT tc.id, tc.session_id, tc.source_file, tc.content, tc.metadata, "
                "rank AS fts_rank "
                "FROM transcript_fts tf "
                "JOIN transcript_chunks tc ON tc.id = tf.rowid "
                "WHERE transcript_fts MATCH ? "
                "ORDER BY rank "
                "LIMIT ?",
                (safe_query, limit),
            ).fetchall()

            for row in rows:
                results.append({
                    "id": row[0],
                    "session_id": row[1],
                    "source_file": row[2],
                    "heading": "",  # Transcripts don't have headings
                    "content": row[3],
                    "metadata": row[4],
                    "fts_rank": row[5],
                })
        except sqlite3.OperationalError as exc:
            logger.debug("FTS5 search failed: %s", exc)

        return results

    def vector_search(
        self,
        query_embedding: list[float],
        top_k: int = 20,
    ) -> list[dict]:
        """Search transcript chunks via sqlite-vec vector similarity."""
        results: list[dict] = []
        try:
            rows = self._conn.execute(
                "SELECT v.id, v.distance, tc.session_id, tc.source_file, tc.content, tc.metadata "
                "FROM transcript_vec v "
                "JOIN transcript_chunks tc ON tc.id = v.id "
                "WHERE v.embedding MATCH ? AND k = ? "
                "ORDER BY v.distance",
                (_serialize_f32(query_embedding), top_k),
            ).fetchall()

            for row in rows:
                results.append({
                    "id": row[0],
                    "vector_score": 1.0 - row[1],  # distance → similarity
                    "session_id": row[2],
                    "source_file": row[3],
                    "heading": "",  # Transcripts don't have headings
                    "content": row[4],
                    "metadata": row[5],
                })
        except sqlite3.OperationalError as exc:
            logger.debug("Vector search failed: %s", exc)

        return results

    def get_indexed_sessions(self) -> set[str]:
        """Return set of session_ids already in the index."""
        rows = self._conn.execute(
            "SELECT DISTINCT session_id FROM transcript_chunks"
        ).fetchall()
        return {row[0] for row in rows}

    def remove_session(self, session_id: str) -> None:
        """Remove all chunks for a session from all tables."""
        # Get row IDs first
        rows = self._conn.execute(
            "SELECT id FROM transcript_chunks WHERE session_id = ?",
            (session_id,),
        ).fetchall()

        for (row_id,) in rows:
            try:
                self._conn.execute("DELETE FROM transcript_fts WHERE rowid = ?", (row_id,))
            except sqlite3.OperationalError:
                pass
            try:
                self._conn.execute("DELETE FROM transcript_vec WHERE id = ?", (row_id,))
            except sqlite3.OperationalError:
                pass

        self._conn.execute(
            "DELETE FROM transcript_chunks WHERE session_id = ?",
            (session_id,),
        )
        self._conn.commit()


# ── Sync ─────────────────────────────────────────────────────────────


def sync_transcript_index(
    store: TranscriptStore,
    transcripts_dir: Path,
    embed_fn: Optional[Callable[[str], Optional[list[float]]]] = None,
    max_age_days: int = 180,
) -> dict:
    """Scan transcripts directory and incrementally index new files.

    Skips sessions that are already indexed (delta-sync by session_id).
    Only processes files modified within max_age_days.

    Args:
        store: TranscriptStore instance with tables ensured.
        transcripts_dir: Directory containing .jsonl transcript files.
        embed_fn: Optional embedding function (Bedrock Titan).
        max_age_days: Skip files older than this many days.

    Returns:
        Stats dict with files_indexed, files_skipped, chunks_added counts.
    """
    import time

    stats = {"files_indexed": 0, "files_skipped": 0, "chunks_added": 0, "errors": 0}

    if not transcripts_dir.is_dir():
        logger.debug("Transcripts dir does not exist: %s", transcripts_dir)
        return stats

    indexed = store.get_indexed_sessions()
    now = time.time()
    cutoff = now - (max_age_days * 86400)

    for jsonl_path in sorted(transcripts_dir.glob("*.jsonl")):
        # Session ID from filename
        session_id = jsonl_path.stem

        # Skip already indexed
        if session_id in indexed:
            stats["files_skipped"] += 1
            continue

        # Skip old files
        try:
            mtime = jsonl_path.stat().st_mtime
            if mtime < cutoff:
                stats["files_skipped"] += 1
                continue
        except OSError:
            stats["errors"] += 1
            continue

        # Parse and chunk
        try:
            turns = parse_transcript(jsonl_path)
            if not turns:
                stats["files_skipped"] += 1
                continue

            chunks = chunk_transcript(
                turns,
                source_file=jsonl_path.name,
                session_id=session_id,
            )

            for chunk in chunks:
                embedding = None
                if embed_fn:
                    embedding = embed_fn(chunk["content"])

                store.upsert_chunk(
                    session_id=chunk["session_id"],
                    source_file=chunk["source_file"],
                    chunk_index=chunk["chunk_index"],
                    role=chunk["role"],
                    content=chunk["content"],
                    content_hash=chunk["content_hash"],
                    metadata=chunk.get("metadata", "{}"),
                    embedding=embedding,
                )
                stats["chunks_added"] += 1

            store.commit()  # Batch commit per file, not per chunk
            stats["files_indexed"] += 1

        except Exception as exc:
            logger.warning("Failed to index transcript %s: %s", jsonl_path.name, exc)
            stats["errors"] += 1

    return stats
