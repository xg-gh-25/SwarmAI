"""Knowledge Library Indexing — scan, chunk, and index Knowledge/ files.

Provides a searchable FTS5 keyword index over the entire Knowledge/ directory
(DailyActivity, Designs, Notes, Signals, Library, etc.). Delta-sync via
content_hash ensures only changed chunks are re-indexed. Recall is pure
FTS5+BM25 — the sqlite-vec vector leg was removed 2026-08-14 (see ensure_tables).

This module is the Phase 1 foundation for the Recall Engine (Phase 2).
MEMORY.md (Brain) stays source of truth for curated memory — this indexes
the episodic memory in Knowledge/ (Library) for keyword recall.

Public symbols:

- ``KnowledgeStore``          — SQLite store for chunks + FTS5 (keyword index)
- ``chunk_markdown``          — Split markdown by heading into chunks
- ``sync_knowledge_index``    — Top-level: scan dir, chunk, delta-sync
"""

import hashlib
import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Directories to skip when scanning Knowledge/
# Archives is NO LONGER skipped (pure-filesystem recall design §3.2/§5.8,
# 2026-06-28): long-term archived cognition (*-archive*.md — MEMORY + EVOLUTION +
# any future family) must be reachable by FTS5 recall — it was the real gap
# (recall could never see archived memory).
_SKIP_DIRS = {"__pycache__", ".git", ".artifacts"}

# Skip job/signal FLOW-LOG dirs — they are time-series dumps (channel-monitor
# logs, job results), not memory; indexing them floods FTS5 with noise (design
# §5.8). Cognitive archives (*-archive*.md, loose .md) ARE indexed.
# NOTE: this matches a `JobResults*` dir at ANY level — both the top-level
# `Knowledge/JobResults/` (131 flow-log files, previously indexed as noise) AND
# nested `Archives/JobResults-*` — because the part-walk below includes the
# top-level subdir name. This top-level exclusion is INTENTIONAL (same noise
# class, design §5.8 spirit), not an accident — adversarial-review-confirmed.
_SKIP_NESTED_DIRS = {"JobResults-2026-May", "JobResults-2026Q1"}
_SKIP_NESTED_PREFIXES = ("JobResults",)  # any JobResults* dir = flow log, skip

# Heading regex: ## or ### (not #, which is the file title)
_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+)$", re.MULTILINE)

# run_3cb6b9ae Cycle-4 (#4): an ENTRY-START within an archived section body — a
# canonical `- [type] **title**` bullet (or the legacy `- {date}: ...` / bare
# `[type]` form). Used ONLY for *-archive*.md files to sub-chunk a size-archived
# section into per-entry chunks, so an archived entry is recallable by its OWN
# content (FTS5) instead of collapsing into one coarse section-wide blob.
# Gate-2 MED (run_3cb6b9ae): the bare-`[` alternative is NARROW — a lowercase
# `[type]` tag only (entry types are lowercase), NOT any `[Letter`, so a col-0
# wrapped continuation like `[see also](url)` or `[NOTE] ...` cannot phantom-split.
_ARCHIVE_ENTRY_RE = re.compile(r"(?m)^(?=- \[|- \d{4}-\d{2}-\d{2}:|\[[a-z]+\])")


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

    # run_3cb6b9ae Cycle-4 (#4): archive files (*-archive*.md) sub-chunk each
    # section's body at ENTRY (bullet) granularity so an archived entry is
    # recallable by its own content, not only as a coarse section-wide blob.
    # Gate-2 MED: anchor on the ARCHIVE FILENAME (basename contains `-archive`
    # AND ends `.md`), not a bare substring — so an incidental path like
    # `Notes/design-archive-notes.md` or `foo-archived/live.md` does NOT trigger.
    _base = source_file.rsplit("/", 1)[-1]
    is_archive = "-archive" in _base and _base.endswith(".md")

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

        if is_archive:
            # Sub-chunk this section body at entry (bullet) granularity. Split the
            # body (below the heading line) on entry starts; each entry — with its
            # trailing metadata/continuation lines — becomes its own chunk, PREFIXED
            # with the section heading for recall context. A section with no
            # entry-shaped bullets falls back to one section chunk (below).
            heading_line = content[start:start + (content[start:].find("\n") + 1
                                    if "\n" in content[start:] else len(text))]
            body = content[start + len(heading_line):end]
            parts = [p for p in _ARCHIVE_ENTRY_RE.split(body) if p.strip()]
            entry_parts = [p for p in parts if p.lstrip().startswith(("- ", "["))]
            if entry_parts:
                for ep in entry_parts:
                    entry_text = f"{heading_line.strip()}\n{ep.strip()}"
                    chunks.append({
                        "source_file": source_file,
                        "chunk_index": len(chunks),
                        "heading": heading,
                        "content": entry_text,
                        "content_hash": hashlib.sha256(entry_text.encode()).hexdigest(),
                    })
                continue  # entries emitted; skip the whole-section chunk

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
    """SQLite store for knowledge chunks with an FTS5 keyword index.

    Recall is pure FTS5+BM25 (the sqlite-vec vector leg was removed 2026-08-14).

    Tables:
    - knowledge_chunks: structured chunk data with content_hash for delta sync
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

        # NOTE: the sqlite-vec `knowledge_vec` virtual table was removed (2026-08-14)
        # — recall is pure FTS5+BM25, the vector leg is dead. Creating it required the
        # vec0 module and made ensure_tables crash on any plain sqlite3 conn without
        # sqlite-vec loaded. FTS5 needs no such dependency.

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
            # Delete the OLD FTS5 entry before update. CRITICAL: external-content
            # FTS5 'delete' must be given the OLD column values currently stored
            # for this rowid — it reverses the posting lists using them. Binding
            # the NEW content here desyncs the index → progressive
            # "database disk image is malformed" (run_1d198980 root cause).
            # Mirrors remove_stale_chunks/remove_file_entries + the messages_fts
            # trigger (sqlite.py:1977 uses old.content).
            _old = self._conn.execute(
                "SELECT content, heading, source_file FROM knowledge_chunks WHERE id = ?",
                (rowid,),
            ).fetchone()
            if _old is not None:
                self._conn.execute(
                    "INSERT INTO knowledge_fts(knowledge_fts, rowid, content, heading, source_file) "
                    "VALUES('delete', ?, ?, ?, ?)",
                    (rowid, _old[0], _old[1] or "", _old[2]),
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

        self._conn.commit()
        return rowid

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

        self._conn.execute(
            "DELETE FROM knowledge_chunks WHERE source_file = ?",
            (source_file,),
        )
        self._conn.commit()
        return len(rows)

    def _fts_is_healthy(self) -> bool:
        """Probe: does the FTS5 index answer a ranked query without a malformed/
        corrupt error? Returns False on DatabaseError (the corruption signal),
        True otherwise. Used by the maintenance layer (NOT the read path) to
        decide whether to repair. A no-data index is still 'healthy'.

        CRITICAL: the probe term must EXIST in the index so the query actually
        traverses the (possibly-corrupt) posting lists + rank structure. A
        no-match term short-circuits before touching them and would report a
        corrupt index as healthy (observed run_1d198980). So derive the probe
        term from a real stored chunk; if the store is empty, it is trivially
        healthy.
        """
        row = self._conn.execute(
            "SELECT content FROM knowledge_chunks "
            "WHERE content IS NOT NULL AND length(content) > 0 LIMIT 1"
        ).fetchone()
        if row is None:
            return True  # empty index — nothing to corrupt
        # First alphanumeric token of a real chunk → guaranteed to exist.
        import re as _re
        m = _re.search(r"[A-Za-z0-9]{3,}", row[0])
        probe = m.group(0) if m else None
        if probe is None:
            return True
        try:
            self._conn.execute(
                "SELECT kc.id FROM knowledge_fts fts "
                "JOIN knowledge_chunks kc ON kc.id = fts.rowid "
                'WHERE knowledge_fts MATCH ? ORDER BY rank LIMIT 1',
                (f'"{probe}"',),
            ).fetchall()
            return True
        except sqlite3.DatabaseError:
            return False

    def repair_fts_index(self) -> None:
        """Repair the external-content FTS5 index from knowledge_chunks.

        Zero data loss — the content lives in knowledge_chunks; the FTS index
        carries no unique data. Two-tier:
          1. ``'rebuild'`` re-derives the index in place (fast, fixes a stale /
             mildly-desynced index).
          2. If 'rebuild' ITSELF raises malformed (the shadow tables are
             corrupt enough that even rebuild can't read them), DROP + recreate
             the virtual table and rebuild fresh — the nuclear option that
             always works because the source data is external.
        MUST be called from the maintenance layer (context_health_hook), not the
        recall read path — it takes a write lock + re-tokenizes every chunk.
        See run_1d198980.
        """
        try:
            self._conn.execute("INSERT INTO knowledge_fts(knowledge_fts) VALUES('rebuild')")
            self._conn.commit()
            return
        except sqlite3.DatabaseError as exc:
            # Only escalate to the destructive DROP path on genuine corruption.
            # A transient lock / disk error must NOT trigger a nuclear rebuild
            # (Gate-2 hardening): re-raise anything that isn't a malformed/corrupt
            # signal so the caller (best-effort health hook) logs + retries later.
            msg = str(exc).lower()
            if "malformed" not in msg and "corrupt" not in msg:
                raise
            self._conn.rollback()
        # Shadow tables too corrupt for in-place rebuild — drop & recreate, all
        # inside one transaction so a mid-repair crash cannot leave the table
        # missing (it rolls back to the old — still-corrupt but present — table,
        # which the next health-hook pass re-probes and repairs).
        self._conn.execute("BEGIN")
        try:
            self._conn.execute("DROP TABLE IF EXISTS knowledge_fts")
            self._conn.execute("""
                CREATE VIRTUAL TABLE knowledge_fts USING fts5(
                    content, heading, source_file,
                    content=knowledge_chunks, content_rowid=id
                )
            """)
            self._conn.execute("INSERT INTO knowledge_fts(knowledge_fts) VALUES('rebuild')")
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def fts5_search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict]:
        """Full-text search via FTS5. Returns chunks ranked by relevance."""
        if not query or not query.strip():
            return []

        # Escape special FTS5 characters and build query
        # Strip FTS5 operators and escape quotes/parens to prevent OperationalError.
        # Also strip FTS5 boolean keywords (NEAR, NOT, AND, OR) which would be
        # interpreted as operators if passed unquoted.
        _FTS5_KEYWORDS = {"AND", "OR", "NOT", "NEAR"}
        clean_words = []
        for word in query.split():
            if not word or word.startswith(("-", "+", "*")):
                continue
            # Strip FTS5 special chars: " ( ) { } ^
            cleaned = re.sub(r'["\(\)\{\}\^]', '', word)
            if cleaned and cleaned.upper() not in _FTS5_KEYWORDS:
                clean_words.append(cleaned)
        if not clean_words:
            return []
        # Use OR semantics: queries are typically focus keywords where
        # ANY matching term is relevant. AND is too restrictive —
        # "daemon crash SIGKILL OOM" matches zero chunks with AND but
        # 356 with OR. FTS5 rank still boosts chunks matching more terms.
        # Quote-wrap each term to prevent any residual operator interpretation.
        clean_query = " OR ".join(f'"{w}"' for w in clean_words)

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
    deadline: Optional[float] = None,
) -> dict:
    """Scan Knowledge/ directory, chunk, and delta-sync to the FTS5 store.

    Recall is pure FTS5+BM25 (the vector leg was removed 2026-08-14), so this
    is keyword-index-only — no embedding step.

    Args:
        store: KnowledgeStore instance (tables must be ensured).
        knowledge_dir: Path to Knowledge/ directory.
        deadline: Optional ``time.monotonic()`` wall-clock deadline. On a large
            changeset (first full index) this can overrun the caller's
            executor timeout, recording a spurious hook "timeout". When given,
            the per-file loop stops cleanly once the deadline passes, leaving
            the remaining files for the next session — the delta-sync is
            content_hash based, so deferral is safe and self-healing. The
            ``deferred`` stat reports how many files were skipped this way.

    Returns:
        Stats dict: files_scanned, chunks_added, chunks_skipped,
        chunks_removed, files_removed, deferred.
    """
    stats = {
        "files_scanned": 0,
        "chunks_added": 0,
        "chunks_skipped": 0,
        "chunks_removed": 0,
        "files_removed": 0,
        "deferred": 0,
    }

    if not knowledge_dir.is_dir():
        return stats

    # Scan all .md files
    current_files: dict[str, Path] = {}  # relative_path → full_path
    for subdir in sorted(knowledge_dir.iterdir()):
        if not subdir.is_dir() or subdir.name in _SKIP_DIRS:
            continue
        for md_file in sorted(subdir.rglob("*.md")):
            if not md_file.is_file():
                continue
            # Skip job/signal flow-log subdirs nested under Archives (or anywhere):
            # they are time-series dumps, not memory (design §5.8). Check every
            # path part so e.g. Archives/JobResults-2026Q1/*.md is excluded.
            rel_to_sub = md_file.relative_to(subdir)
            parts = (subdir.name, *rel_to_sub.parts[:-1])
            if any(
                p in _SKIP_NESTED_DIRS or p.startswith(_SKIP_NESTED_PREFIXES)
                for p in parts
            ):
                continue
            rel_path = f"{subdir.name}/{rel_to_sub}"
            current_files[rel_path] = md_file

    # ── Privacy-partition coverage (CYCLE 1' → widened STEP2): ALL cognitive
    # archives (MEMORY-archive*, EVOLUTION-archive*, …) live in the gitignored
    # .context/ (a SIBLING of Knowledge/), NOT in git-tracked Knowledge/Archives/.
    # Index them here so recall still reaches archived cognition after it moves out
    # of Knowledge/. The guiding principle (SwarmAI TECH.md § Architecture, "活/冷
    # 二分"): ACTIVE files (MEMORY.md / EVOLUTION.md / USER.md / STEERING.md /
    # TOOLS.md) are FULL-INJECTED into the system prompt → never FTS5-indexed;
    # every .context/ *-archive*.md (cold) → recall (FTS5+BM25).
    #
    # STRICT ALLOWLIST via the '*-archive*.md' glob: the '-archive' infix
    # FAIL-CLOSED-excludes the active docs (MEMORY.md/EVOLUTION.md/USER.md have no
    # '-archive' infix), so no active private doc can ever match. This is a
    # domain-neutral rule — a new archive family (e.g. a future KNOWLEDGE-archive)
    # is covered automatically, no code change.
    #
    # rel_path uses a DISTINCT ".context/Archives/" prefix — NOT a bare "Archives/".
    # A bare prefix would COLLIDE with a legacy Knowledge/Archives/MEMORY-archive-
    # YYYY-MM.md of the same basename (both map to the same current_files key → the
    # second insert silently drops the first from the index + causes chunk-thrash).
    # The synthetic prefix still contains the literal "Archives" so recall
    # source_file semantics + the memory_chain_probe "Archives in source_file"
    # invariant hold, while keeping the two physical files' keys disjoint.
    context_dir = knowledge_dir.parent / ".context"
    if context_dir.is_dir():
        for arch in sorted(context_dir.glob("*-archive*.md")):
            if arch.is_file():
                current_files[f".context/Archives/{arch.name}"] = arch

    # Remove entries for deleted files
    indexed_files = store.get_indexed_files()
    for old_file in indexed_files - set(current_files.keys()):
        store.remove_file_entries(old_file)
        stats["files_removed"] += 1

    # Process each file
    for rel_path, full_path in current_files.items():
        # Wall-clock budget: stop before the caller's executor timeout fires.
        # Checked at the top of the loop (before the per-chunk embed, the
        # expensive part) so we never start a file we can't afford. Remaining
        # files carry to the next session (delta-sync is content_hash based).
        if deadline is not None and time.monotonic() > deadline:
            stats["deferred"] = len(current_files) - stats["files_scanned"]
            break

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

            store.upsert_chunk(
                source_file=rel_path,
                chunk_index=idx,
                heading=chunk.get("heading"),
                content=chunk["content"],
                content_hash=chunk["content_hash"],
            )
            stats["chunks_added"] += 1

        # Remove chunks that no longer exist in this file
        removed = store.remove_stale_chunks(rel_path, new_indexes)
        stats["chunks_removed"] += removed

    return stats
