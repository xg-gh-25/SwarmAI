"""Tests for knowledge_store.py — Library Indexing (Phase 1).

Tests chunking, delta-sync, FTS5 search, and table management for the
Knowledge/ directory indexing system.
"""

import hashlib
import sqlite3
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Helper: create an in-memory SQLite connection with sqlite-vec loaded ──

def _make_conn():
    """Create an in-memory SQLite conn with sqlite-vec loaded."""
    conn = sqlite3.connect(":memory:")
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (ImportError, AttributeError):
        pytest.skip("sqlite-vec not installed")
    return conn


# ── chunk_markdown ──

class TestChunkMarkdown:
    """Test markdown chunking by heading."""

    def test_single_section(self):
        from core.knowledge_store import chunk_markdown

        md = textwrap.dedent("""\
        # Title

        Some content here.
        More content.
        """)
        chunks = chunk_markdown(md, "test.md")
        assert len(chunks) >= 1
        assert chunks[0]["content"].strip() != ""

    def test_multiple_h2_sections(self):
        from core.knowledge_store import chunk_markdown

        md = textwrap.dedent("""\
        # Main Title

        Intro text.

        ## Section One

        Content of section one.

        ## Section Two

        Content of section two.
        """)
        chunks = chunk_markdown(md, "test.md")
        # Should produce at least 2 chunks (one per ## heading)
        assert len(chunks) >= 2
        headings = [c["heading"] for c in chunks if c.get("heading")]
        assert any("Section One" in h for h in headings)
        assert any("Section Two" in h for h in headings)

    def test_daily_activity_format(self):
        """DailyActivity files use ## HH:MM | session_id format."""
        from core.knowledge_store import chunk_markdown

        md = textwrap.dedent("""\
        ## 15:06 | abc12345 | Working on memory system

        **What happened:**
        - Built knowledge_store.py
        - Added FTS5 support

        ## 16:30 | def67890 | Fixed a bug

        **What happened:**
        - Fixed the delta sync
        """)
        chunks = chunk_markdown(md, "DailyActivity/2026-04-01.md")
        assert len(chunks) >= 2

    def test_preserves_source_file(self):
        from core.knowledge_store import chunk_markdown

        chunks = chunk_markdown("# Hello\nWorld", "Notes/test.md")
        assert all(c["source_file"] == "Notes/test.md" for c in chunks)

    def test_content_hash_deterministic(self):
        from core.knowledge_store import chunk_markdown

        chunks1 = chunk_markdown("# Foo\nBar", "test.md")
        chunks2 = chunk_markdown("# Foo\nBar", "test.md")
        assert chunks1[0]["content_hash"] == chunks2[0]["content_hash"]

    def test_empty_file_returns_empty(self):
        from core.knowledge_store import chunk_markdown

        chunks = chunk_markdown("", "empty.md")
        assert chunks == []


# ── KnowledgeStore table management ──

class TestKnowledgeStore:
    """Test KnowledgeStore table creation and sync."""

    def test_ensure_tables_creates_all(self):
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        # Verify tables exist
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
        ).fetchall()}
        assert "knowledge_chunks" in tables
        assert "knowledge_vec" in tables
        assert "knowledge_fts" in tables

    def test_upsert_chunk(self):
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        store.upsert_chunk(
            source_file="Notes/test.md",
            chunk_index=0,
            heading="## Test",
            content="Hello world",
            content_hash="abc123",
            metadata={"date": "2026-04-01"},
        )

        rows = conn.execute("SELECT * FROM knowledge_chunks").fetchall()
        assert len(rows) == 1

    def test_upsert_chunk_with_embedding(self):
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        embedding = [0.1] * 1024
        store.upsert_chunk(
            source_file="Notes/test.md",
            chunk_index=0,
            heading="## Test",
            content="Hello world",
            content_hash="abc123",
            embedding=embedding,
        )

        # Verify vector was stored via search (vec0 doesn't expose rowid directly)
        import struct
        query_blob = struct.pack(f"{1024}f", *([0.5] * 1024))
        vec_rows = conn.execute(
            "SELECT id, distance FROM knowledge_vec WHERE embedding MATCH ? LIMIT 1",
            (query_blob,),
        ).fetchall()
        assert len(vec_rows) == 1

    def test_delta_sync_skips_unchanged(self):
        """Delta sync should skip chunks with same content_hash."""
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        content_hash = hashlib.sha256(b"Hello world").hexdigest()
        store.upsert_chunk("test.md", 0, "## T", "Hello world", content_hash)

        # Get existing hashes
        existing = store.get_existing_hashes("test.md")
        assert existing.get(0) == content_hash

    def test_remove_stale_chunks(self):
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        # Insert 3 chunks
        for i in range(3):
            store.upsert_chunk("test.md", i, f"## S{i}", f"content {i}", f"hash{i}")

        # Remove all but chunk 0
        store.remove_stale_chunks("test.md", keep_indexes={0})
        rows = conn.execute("SELECT chunk_index FROM knowledge_chunks WHERE source_file = ?", ("test.md",)).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 0

    def test_fts5_search(self):
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        store.upsert_chunk("Notes/cred.md", 0, "## Credentials",
                          "Two credential chains coexist on this machine", "h1")
        store.upsert_chunk("Notes/other.md", 0, "## Weather",
                          "The weather is nice today", "h2")

        results = store.fts5_search("credential chains")
        assert len(results) >= 1
        assert results[0]["source_file"] == "Notes/cred.md"

    def test_fts5_search_no_results(self):
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        results = store.fts5_search("nonexistent query xyz")
        assert results == []

    def test_vector_search(self):
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        # Insert chunk with embedding
        emb = [0.5] * 1024
        store.upsert_chunk("test.md", 0, "## T", "credential chain", "h1", embedding=emb)

        # Search with similar embedding
        query_emb = [0.5] * 1024
        results = store.vector_search(query_emb, top_k=5)
        assert len(results) >= 1

    def test_vector_search_none_embedding_returns_empty(self):
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        results = store.vector_search(None, top_k=5)
        assert results == []

    def test_remove_file_entries(self):
        from core.knowledge_store import KnowledgeStore

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        store.upsert_chunk("delete_me.md", 0, "## D", "content", "h1")
        store.remove_file_entries("delete_me.md")
        rows = conn.execute("SELECT * FROM knowledge_chunks WHERE source_file = ?", ("delete_me.md",)).fetchall()
        assert len(rows) == 0


# ── sync_knowledge_index (integration-level) ──

class TestSyncKnowledgeIndex:
    """Test the top-level sync function with a real directory."""

    def test_sync_indexes_md_files(self, tmp_path):
        from core.knowledge_store import KnowledgeStore, sync_knowledge_index

        # Create a mini Knowledge/ dir
        knowledge_dir = tmp_path / "Knowledge"
        notes_dir = knowledge_dir / "Notes"
        notes_dir.mkdir(parents=True)
        (notes_dir / "test-note.md").write_text("# Test Note\n\nSome content about testing.")

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        stats = sync_knowledge_index(store, knowledge_dir, embed_fn=None)
        assert stats["files_scanned"] >= 1
        assert stats["chunks_added"] >= 1

    def test_sync_delta_skips_unchanged(self, tmp_path):
        from core.knowledge_store import KnowledgeStore, sync_knowledge_index

        knowledge_dir = tmp_path / "Knowledge"
        notes_dir = knowledge_dir / "Notes"
        notes_dir.mkdir(parents=True)
        (notes_dir / "test.md").write_text("# Test\n\nContent.")

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        # First sync
        stats1 = sync_knowledge_index(store, knowledge_dir, embed_fn=None)
        # Second sync (no changes)
        stats2 = sync_knowledge_index(store, knowledge_dir, embed_fn=None)
        assert stats2["chunks_skipped"] >= stats1["chunks_added"]
        assert stats2["chunks_added"] == 0

    def test_sync_removes_deleted_files(self, tmp_path):
        from core.knowledge_store import KnowledgeStore, sync_knowledge_index

        knowledge_dir = tmp_path / "Knowledge"
        notes_dir = knowledge_dir / "Notes"
        notes_dir.mkdir(parents=True)
        test_file = notes_dir / "test.md"
        test_file.write_text("# Test\n\nContent.")

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        sync_knowledge_index(store, knowledge_dir, embed_fn=None)
        # Delete file
        test_file.unlink()
        stats = sync_knowledge_index(store, knowledge_dir, embed_fn=None)
        assert stats["files_removed"] >= 1

    def test_sync_calls_embed_fn(self, tmp_path):
        from core.knowledge_store import KnowledgeStore, sync_knowledge_index

        knowledge_dir = tmp_path / "Knowledge"
        notes_dir = knowledge_dir / "Notes"
        notes_dir.mkdir(parents=True)
        (notes_dir / "test.md").write_text("# Test\n\nContent about embedding.")

        conn = _make_conn()
        store = KnowledgeStore(conn)
        store.ensure_tables()

        embed_fn = MagicMock(return_value=[0.1] * 1024)
        sync_knowledge_index(store, knowledge_dir, embed_fn=embed_fn)
        assert embed_fn.call_count >= 1
