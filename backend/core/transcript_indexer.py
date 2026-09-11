"""Transcript Keyword Indexing — parse, chunk, index JSONL transcripts.

Provides a searchable FTS5 keyword index over Claude Code session
transcripts (JSONL). Delta-sync via content_hash ensures only new
sessions are indexed. Follows the same pattern as knowledge_store.py.
Recall is pure FTS5+BM25 — the sqlite-vec vector leg was removed 2026-08-14.

Core insight (MemPalace, April 2026): raw verbatim storage + keyword search
scores 96.6% on LongMemEval R@5 — 12.4% higher than LLM-summarized storage.
"Intelligence at read time, not write time."

Public symbols:

- ``TranscriptStore``          — SQLite store for transcript chunks + FTS5 (keyword index)
- ``chunk_transcript``         — Split JSONL turns into conversation-pair chunks
- ``parse_transcript``         — Parse JSONL file into turn records
- ``sync_transcript_index``    — Top-level: scan dir, chunk, delta-sync
"""

import hashlib
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Optional

from .cjk_index import expand_cjk, expand_cjk_query

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

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


class TranscriptStore:
    """SQLite store for transcript chunks with an FTS5 keyword index.

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

        # CJK segmentation columns (run_4ed75215) — see core/cjk_index. The FTS
        # index points at THESE, so 'rebuild', DELETE FROM <fts>, and any future
        # trigger all re-derive the already-expanded text instead of raw text
        # (indexing expanded text over a raw source corrupts the index).
        for _seg_col in ("content_seg", "source_file_seg"):
            try:
                self._conn.execute(
                    f"ALTER TABLE transcript_chunks ADD COLUMN {_seg_col} TEXT")
            except sqlite3.OperationalError:
                pass  # already present

        # FTS5 virtual table (external content pattern)
        try:
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5(
                    content_seg, source_file_seg,
                    content=transcript_chunks, content_rowid=id
                )
            """)
        except sqlite3.OperationalError:
            logger.debug("transcript_fts already exists or FTS5 unavailable")
        # Deliberately OUTSIDE the try above. That handler exists for "FTS5 is
        # unavailable" on the CREATE, but it also swallowed any failure of the
        # migration — and the migration DROPs the index before recreating it, so a
        # failed CREATE left NO transcript_fts at all while logging the misleading
        # "already exists" at debug level. These two carry their own DatabaseError
        # handling and must report their own failures.
        self._migrate_fts_to_seg_columns()
        self._reconcile_fresh_index()

        # NOTE: the sqlite-vec `transcript_vec` virtual table was removed (2026-08-14)
        # — transcript recall is pure FTS5, the vector leg is dead.

    # Column set the index MUST have. A pre-run_4ed75215 database indexes the
    # raw columns; a virtual table cannot be ALTERed, so migrating is
    # DROP + CREATE + rebuild. Losing the index is safe — it is DERIVED from
    # transcript_chunks.
    _FTS_COLUMNS = ("content_seg", "source_file_seg")

    # (source column, segmented column) pairs this table maintains.
    _SEG_PAIRS = (('content', 'content_seg'), ('source_file', 'source_file_seg'))

    def _populate_seg_columns(self, chunk_size: int = 500) -> int:
        """Fill any NULL/stale ``*_seg`` value from its source column.

        Called before a rebuild so the index has something to derive from. The
        alternative — rebuild now, populate later via a script — leaves the index
        EMPTY for every pre-existing row, which silently kills historical search
        while ``integrity-check`` still reports OK.

        Idempotent by predicate (a row whose segmented value already equals the
        expansion is skipped), and COMMITTED PER CHUNK — not merely looped in
        chunks. Without the commit the chunking is cosmetic: all N updates plus the
        rebuild that follows sit in ONE transaction holding the write lock the
        daemon needs (measured 27.7s for 41K rows), and an interrupted upgrade
        discards every row. Per-chunk commits let a killed migration resume,
        because the predicate skips rows already done. Returns rows written.
        """
        from .cjk_index import expand_cjk

        src_cols = ", ".join(s for s, _ in self._SEG_PAIRS)
        seg_cols = ", ".join(g for _, g in self._SEG_PAIRS)
        set_clause = ", ".join(f"{g} = ?" for _, g in self._SEG_PAIRS)
        n = len(self._SEG_PAIRS)
        try:
            # Stream the cursor rather than fetchall(): this reads every row of the
            # table, and materializing the whole corpus (plus an expansion per row)
            # is a needless spike on a 636 MB database.
            cursor = self._conn.execute(
                f"SELECT rowid, {src_cols}, {seg_cols} FROM transcript_chunks")
        except sqlite3.DatabaseError:
            return 0  # columns not present yet — ensure_tables adds them first

        pending = []
        for row in cursor:
            wanted = [expand_cjk(str(v or "")) for v in row[1:1 + n]]
            if list(row[1 + n:1 + 2 * n]) != wanted:
                pending.append((wanted, row[0]))
        for i in range(0, len(pending), chunk_size):
            self._conn.commit()  # per chunk — see the docstring
            for wanted, rowid in pending[i:i + chunk_size]:
                self._conn.execute(
                    f"UPDATE transcript_chunks SET {set_clause} WHERE rowid = ?",
                    (*wanted, rowid))
        if pending:
            logger.info("transcript_chunks: populated %d segmentation column set(s)",
                        len(pending))
        return len(pending)

    def _reconcile_fresh_index(self) -> None:
        """Reconcile the index with its content source ONCE per database.

        A freshly CREATEd external-content index holds no postings while its source
        column already has a value for every row. Those two states disagree, and the
        disagreement is not cosmetic: ``integrity-check rank=1`` reports "database
        disk image is malformed", and on a trigger-backed table the next UPDATE
        fails outright — measured on a copy of the real database, where it made the
        segmentation backfill die and search return 0 for EVERY query, Chinese and
        English alike.

        GATED ON A STORED MARKER, not on probing. The obvious design — run
        ``integrity-check`` and rebuild if it fails — is what this replaced: that
        probe RE-DERIVES every token from the content source, so it costs the same
        as the rebuild it is deciding about (measured: ~100 ms per 3,000 rows,
        identical to a full rebuild), on every single startup. Probing to avoid
        work that costs exactly as much as the probe is not a guard, it is the work
        done twice. So the reconcile runs once, records that it ran, and is a
        single cheap SELECT thereafter.

        Rebuilding is free of data risk — the index is DERIVED from the base table.
        """
        try:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS transcript_chunks_index_state ("
                "  marker TEXT PRIMARY KEY,"
                "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
                ")")
            done = self._conn.execute(
                "SELECT 1 FROM transcript_chunks_index_state WHERE marker = ?",
                ("cjk_seg_v1",)).fetchone()
            if done:
                return
            self._conn.execute(
                "INSERT INTO transcript_fts(transcript_fts) VALUES('rebuild')")
            self._conn.execute(
                "INSERT OR IGNORE INTO transcript_chunks_index_state(marker) VALUES(?)",
                ("cjk_seg_v1",))
            logger.info(
                "transcript_fts: reconciled with its content source (one-time, marker %s)",
                "cjk_seg_v1")
        except sqlite3.DatabaseError as exc:
            # Leave the marker UNSET so the next startup retries. repair_fts_index
            # owns the nuclear path if even rebuild cannot read the shadow tables.
            logger.error(
                "transcript_fts: reconcile failed, will retry next startup: %s: %s",
                type(exc).__name__, exc)

    def _migrate_fts_to_seg_columns(self) -> None:
        """Re-point a pre-existing transcript_fts at the segmentation columns.

        Idempotent — reads the virtual table's real column list and returns
        immediately when already migrated.
        """
        try:
            cols = [r[1] for r in self._conn.execute(
                "PRAGMA table_info(transcript_fts)")]
        except sqlite3.DatabaseError:
            return  # unreadable — repair_fts_index owns that path
        if not cols or self._FTS_COLUMNS[0] in cols:
            return
        logger.info(
            "transcript_fts: migrating index from %s to CJK-segmented columns", cols)
        self._conn.execute("DROP TABLE IF EXISTS transcript_fts")
        self._conn.execute("""
            CREATE VIRTUAL TABLE transcript_fts USING fts5(
                content_seg, source_file_seg,
                content=transcript_chunks, content_rowid=id
            )
        """)
        # POPULATE BEFORE REBUILDING. A rebuild derives the index from the
        # content source, which is now the *_seg columns — and those are NULL for
        # every pre-existing row. Rebuilding first therefore produces an EMPTY
        # index and silently kills ALL historical search, English included
        # (reproduced: a chunk matching '"daemon"' before the migration returned
        # nothing after, while integrity-check still reported OK — so the health
        # probe cannot see the outage). Deferring this to an operator-run script
        # was the original design and it is not acceptable: this method runs
        # automatically on daemon start.
        self._populate_seg_columns()
        self._conn.execute(
            "INSERT INTO transcript_fts(transcript_fts) VALUES('rebuild')")

    def upsert_chunk(
        self,
        session_id: str,
        source_file: str,
        chunk_index: int,
        role: str,
        content: str,
        content_hash: str,
        metadata: str = "{}",
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
            # Reverse the OLD FTS5 posting lists BEFORE the UPDATE. CRITICAL:
            # transcript_fts is external-content (content=transcript_chunks), so
            # the 'delete' command must be given the OLD column values currently
            # stored for this rowid — it reverses the posting lists using them.
            # The previous `INSERT OR REPLACE` bound the NEW content, which leaves
            # the OLD tokens' postings in the index → progressive "database disk
            # image is malformed" (mirrors knowledge_store.py:185 / the
            # messages_fts trigger which uses old.content).
            # Read the OLD *_seg values — 'delete' reverses postings using the
            # values it is handed, and the index holds segmented text.
            _old = self._conn.execute(
                "SELECT content_seg, source_file_seg FROM transcript_chunks WHERE id = ?",
                (row_id,),
            ).fetchone()
            if _old is not None:
                try:
                    self._conn.execute(
                        "INSERT INTO transcript_fts(transcript_fts, rowid, content_seg, source_file_seg) "
                        "VALUES('delete', ?, ?, ?)",
                        (row_id, _old[0] or "", _old[1] or ""),
                    )
                except sqlite3.OperationalError:
                    pass  # FTS5 not available
            # Update
            self._conn.execute(
                "UPDATE transcript_chunks SET source_file=?, role=?, content=?, "
                "content_hash=?, metadata=?, content_seg=?, source_file_seg=?, "
                "created_at=datetime('now') "
                "WHERE id=?",
                (source_file, role, content, content_hash, metadata,
                 expand_cjk(content), expand_cjk(source_file), row_id),
            )
        else:
            cursor = self._conn.execute(
                "INSERT INTO transcript_chunks "
                "(session_id, source_file, chunk_index, role, content, content_hash, "
                " metadata, content_seg, source_file_seg) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, source_file, chunk_index, role, content, content_hash,
                 metadata, expand_cjk(content), expand_cjk(source_file)),
            )
            row_id = cursor.lastrowid

        # Sync FTS5 — plain INSERT (never INSERT OR REPLACE on an external-content
        # FTS5 table: the UPDATE branch above has already reversed the old
        # postings, and a brand-new rowid has none to reverse).
        try:
            self._conn.execute(
                "INSERT INTO transcript_fts(rowid, content_seg, source_file_seg) "
                "VALUES (?, ?, ?)",
                (row_id, expand_cjk(content), expand_cjk(source_file)),
            )
        except sqlite3.OperationalError:
            pass  # FTS5 not available

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
            # Tokenize into individual words, sanitize FTS5 metacharacters.
            # Also strip FTS5 boolean keywords (NEAR, NOT, AND, OR) which would
            # be interpreted as operators if passed unquoted.
            _FTS5_KEYWORDS = {"AND", "OR", "NOT", "NEAR"}
            words = query.split()
            clean_words = []
            for w in words:
                w = re.sub(r'["\(\)\{\}\^\*\-\+]', '', w).strip()
                if w and len(w) > 1 and w.upper() not in _FTS5_KEYWORDS:
                    clean_words.append(w)
            if not clean_words:
                return results
            # CJK-aware: a Chinese term is searched as the AND of its bigrams,
            # matching how it was indexed. No-CJK text keeps the quoted-OR form.
            safe_query = expand_cjk_query(" ".join(clean_words))
            if not safe_query:
                return results
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
                pass  # FTS5 unavailable — the base-table delete below still runs
            except sqlite3.DatabaseError as exc:
                # A malformed/corrupt index raises DatabaseError, NOT
                # OperationalError, so the narrower catch above let it escape
                # uncaught AND hid a genuine desync signal. Log it and continue:
                # the base rows must still be deleted, and repair_fts_index owns
                # the recovery.
                logger.error(
                    "transcript_fts: delete failed for rowid %s (index may need "
                    "repair): %s: %s", row_id, type(exc).__name__, exc)

        self._conn.execute(
            "DELETE FROM transcript_chunks WHERE session_id = ?",
            (session_id,),
        )
        self._conn.commit()

    def _fts_is_healthy(self) -> bool:
        """Probe: does transcript_fts answer a ranked query without a malformed/
        corrupt error? Returns False on DatabaseError (the corruption signal),
        True otherwise. Used by the maintenance layer (NOT the read path) to
        decide whether to repair. A no-data index is still 'healthy'.

        CRITICAL: the probe term must EXIST in the index so the query actually
        traverses the (possibly-corrupt) posting lists + rank structure. A
        no-match term short-circuits before touching them and would report a
        corrupt index as healthy (run_1d198980). So derive the probe term from a
        real stored chunk; if the store is empty, it is trivially healthy.
        Mirrors knowledge_store._fts_is_healthy.
        """
        row = self._conn.execute(
            "SELECT content FROM transcript_chunks "
            "WHERE content IS NOT NULL AND length(content) > 0 LIMIT 1"
        ).fetchone()
        if row is None:
            return True  # empty index — nothing to corrupt
        import re as _re
        m = _re.search(r"[A-Za-z0-9]{3,}", row[0])
        probe = m.group(0) if m else None
        if probe is None:
            return True
        try:
            self._conn.execute(
                "SELECT tc.id FROM transcript_fts fts "
                "JOIN transcript_chunks tc ON tc.id = fts.rowid "
                "WHERE transcript_fts MATCH ? ORDER BY rank LIMIT 1",
                (f'"{probe}"',),
            ).fetchall()
            return True
        except sqlite3.DatabaseError:
            return False

    def repair_fts_index(self) -> None:
        """Repair the external-content FTS5 index from transcript_chunks.

        Zero data loss — the content lives in transcript_chunks; the FTS index
        carries no unique data. Two-tier (mirrors knowledge_store.repair_fts_index):
          1. ``'rebuild'`` re-derives the index in place (fast, fixes a stale /
             mildly-desynced index).
          2. If 'rebuild' ITSELF raises malformed, DROP + recreate the virtual
             table and rebuild fresh — the nuclear option that always works
             because the source data is external.
        MUST be called from the maintenance layer (context_health_hook), not the
        recall read path — it takes a write lock + re-tokenizes every chunk.
        """
        try:
            self._conn.execute("INSERT INTO transcript_fts(transcript_fts) VALUES('rebuild')")
            self._conn.commit()
            return
        except sqlite3.DatabaseError as exc:
            # Only escalate to the destructive DROP path on genuine corruption.
            # A transient lock / disk error must NOT trigger a nuclear rebuild —
            # re-raise anything that isn't a malformed/corrupt signal so the
            # caller (best-effort health hook) logs + retries later.
            msg = str(exc).lower()
            if "malformed" not in msg and "corrupt" not in msg:
                raise
            self._conn.rollback()
        # Shadow tables too corrupt for in-place rebuild — drop & recreate, all
        # inside one transaction so a mid-repair crash cannot leave the table
        # missing (rolls back to the old — still-corrupt but present — table,
        # which the next health-hook pass re-probes and repairs). NOTE: 2-column
        # schema (content, source_file) — transcript_fts, NOT knowledge's 3-col.
        self._conn.execute("BEGIN")
        try:
            self._conn.execute("DROP TABLE IF EXISTS transcript_fts")
            self._conn.execute("""
                CREATE VIRTUAL TABLE transcript_fts USING fts5(
                    content_seg, source_file_seg,
                    content=transcript_chunks, content_rowid=id
                )
            """)
            self._conn.execute("INSERT INTO transcript_fts(transcript_fts) VALUES('rebuild')")
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise


# ── Sync ─────────────────────────────────────────────────────────────


def sync_transcript_index(
    store: TranscriptStore,
    transcripts_dir: Path,
    max_age_days: int = 180,
) -> dict:
    """Scan transcripts directory and incrementally index new files (FTS5-only).

    Skips sessions that are already indexed (delta-sync by session_id).
    Only processes files modified within max_age_days. Recall is pure FTS5+BM25
    (vector leg removed 2026-08-14 — PRI11), so there is no embedding step.

    Args:
        store: TranscriptStore instance with tables ensured.
        transcripts_dir: Directory containing .jsonl transcript files.
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

        # Parse and chunk — wrapped in an explicit transaction so that either
        # ALL chunks for a session are indexed, or NONE are (crash safety).
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

            # Use a savepoint for atomic per-file indexing.  Savepoints work
            # regardless of the connection's autocommit / isolation_level
            # setting (unlike bare BEGIN which conflicts with Python's
            # implicit transaction management).  If the process crashes or
            # an exception occurs mid-file, the savepoint is rolled back,
            # preventing partially indexed sessions.
            _sp = f"sp_idx_{hashlib.md5(session_id.encode()).hexdigest()[:8]}"
            store._conn.execute(f"SAVEPOINT {_sp}")
            try:
                for chunk in chunks:
                    store.upsert_chunk(
                        session_id=chunk["session_id"],
                        source_file=chunk["source_file"],
                        chunk_index=chunk["chunk_index"],
                        role=chunk["role"],
                        content=chunk["content"],
                        content_hash=chunk["content_hash"],
                        metadata=chunk.get("metadata", "{}"),
                    )
                    stats["chunks_added"] += 1

                store._conn.execute(f"RELEASE {_sp}")
            except Exception:
                store._conn.execute(f"ROLLBACK TO {_sp}")
                store._conn.execute(f"RELEASE {_sp}")
                raise

            store.commit()  # Persist after each successfully indexed file
            stats["files_indexed"] += 1

        except Exception as exc:
            logger.warning("Failed to index transcript %s: %s", jsonl_path.name, exc)
            stats["errors"] += 1

    return stats
