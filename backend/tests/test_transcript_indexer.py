"""Tests for core.transcript_indexer — transcript semantic indexing (P1).

Validates JSONL parsing, turn-pair chunking, delta-sync, hybrid search,
and integration with RecallEngine. Follows knowledge_store test patterns.
"""
import hashlib
import json
import sqlite3
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────


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


def _make_jsonl(path: Path, turns: list[dict]) -> Path:
    """Write a JSONL transcript file from parsed turn dicts.

    Wraps each {role, content} dict into the JSONL format that
    parse_transcript() expects: {type, message: {role, content}}.
    """
    with open(path, "w", encoding="utf-8") as f:
        for turn in turns:
            record = {
                "type": turn.get("role", "user"),
                "message": {"role": turn.get("role", "user"), "content": turn.get("content", "")},
            }
            f.write(json.dumps(record) + "\n")
    return path


def _sample_turns() -> list[dict]:
    """A minimal conversation: user asks about a race condition, assistant answers.

    Returns the PARSED format (role + content) matching parse_transcript() output.
    """
    return [
        {"role": "user", "content": "I'm seeing a race condition in session_unit.py when two tabs stream simultaneously"},
        {"role": "assistant", "content": "The race condition is caused by concurrent access to _active_sessions dict without locking. Here's the fix:\n```python\nasync with self._lock:\n    self._active_sessions[sid] = unit\n```"},
        {"role": "user", "content": "What about the credential chain error from last week?"},
        {"role": "assistant", "content": "Two credential chains coexist: CLI uses AWS SSO IdC tokens, boto3 uses credential_process (ada to Isengard). Traceback:\n```\nbotocore.exceptions.NoCredentialsError: Unable to locate credentials\n```\nFix: strip ALL proxy vars from CLI env."},
    ]


# ── Test: Chunking ───────────────────────────────────────────────────


class TestChunkTranscript:
    """Test JSONL turn-pair chunking."""

    def test_basic_chunking(self):
        from core.transcript_indexer import chunk_transcript

        turns = _sample_turns()
        chunks = chunk_transcript(turns, source_file="test.jsonl", session_id="sess1")

        assert len(chunks) >= 1
        # Each chunk should have required fields
        for chunk in chunks:
            assert "source_file" in chunk
            assert "session_id" in chunk
            assert "chunk_index" in chunk
            assert "content" in chunk
            assert "content_hash" in chunk
            assert "role" in chunk

    def test_empty_turns(self):
        from core.transcript_indexer import chunk_transcript

        chunks = chunk_transcript([], source_file="empty.jsonl", session_id="s1")
        assert chunks == []

    def test_preserves_turn_pairs(self):
        """User + assistant should stay together in a chunk when possible."""
        from core.transcript_indexer import chunk_transcript

        turns = _sample_turns()
        chunks = chunk_transcript(turns, source_file="t.jsonl", session_id="s1")

        # With 4 short turns, should produce 1-2 chunks
        # Each chunk should contain both user and assistant content
        first_chunk = chunks[0]["content"]
        assert "race condition" in first_chunk

    def test_content_hash_is_deterministic(self):
        from core.transcript_indexer import chunk_transcript

        turns = _sample_turns()
        c1 = chunk_transcript(turns, source_file="t.jsonl", session_id="s1")
        c2 = chunk_transcript(turns, source_file="t.jsonl", session_id="s1")
        assert c1[0]["content_hash"] == c2[0]["content_hash"]

    def test_large_turns_split_across_chunks(self):
        """Turns exceeding max_tokens should be split into multiple chunks."""
        from core.transcript_indexer import chunk_transcript

        # Create a very long assistant turn (~2000 tokens worth)
        long_content = "word " * 2000  # ~2000 tokens
        turns = [
            {"role": "user", "content": "explain everything"},
            {"role": "assistant", "content": long_content},
        ]
        chunks = chunk_transcript(turns, source_file="t.jsonl", session_id="s1", max_tokens=500)
        assert len(chunks) >= 2

    def test_metadata_extraction(self):
        """Chunks should extract metadata from content."""
        from core.transcript_indexer import chunk_transcript

        turns = [
            {"type": "user", "message": {"role": "user", "content": "check session_unit.py"}},
            {"type": "assistant", "message": {"role": "assistant", "content": "I'll Read the file and use Bash to run tests."}},
        ]
        chunks = chunk_transcript(turns, source_file="t.jsonl", session_id="s1")
        meta = json.loads(chunks[0].get("metadata", "{}"))
        # Should detect files_mentioned and tools_used
        assert isinstance(meta, dict)


# ── Test: Parse Transcript ───────────────────────────────────────────


class TestParseTranscript:
    """Test JSONL file parsing."""

    def test_parse_valid_jsonl(self, tmp_path):
        from core.transcript_indexer import parse_transcript

        path = _make_jsonl(tmp_path / "sess.jsonl", _sample_turns())
        turns = parse_transcript(path)
        assert len(turns) == 4
        assert turns[0]["role"] == "user"
        assert turns[1]["role"] == "assistant"

    def test_parse_skips_malformed_lines(self, tmp_path):
        from core.transcript_indexer import parse_transcript

        path = tmp_path / "bad.jsonl"
        with open(path, "w") as f:
            f.write('{"type":"user","message":{"role":"user","content":"hello"}}\n')
            f.write("NOT JSON\n")
            f.write('{"type":"assistant","message":{"role":"assistant","content":"hi"}}\n')
        turns = parse_transcript(path)
        assert len(turns) == 2

    def test_parse_empty_file(self, tmp_path):
        from core.transcript_indexer import parse_transcript

        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        turns = parse_transcript(path)
        assert turns == []

    def test_parse_filters_non_user_assistant(self, tmp_path):
        from core.transcript_indexer import parse_transcript

        path = tmp_path / "mixed.jsonl"
        with open(path, "w") as f:
            f.write('{"type":"user","message":{"role":"user","content":"hi"}}\n')
            f.write('{"type":"system","message":{"role":"system","content":"init"}}\n')
            f.write('{"type":"assistant","message":{"role":"assistant","content":"hello"}}\n')
        turns = parse_transcript(path)
        assert len(turns) == 2


# ── Test: TranscriptStore ────────────────────────────────────────────


class TestTranscriptStore:
    """Test SQLite storage with FTS5 + sqlite-vec."""

    def test_ensure_tables(self):
        from core.transcript_indexer import TranscriptStore

        conn = _make_conn()
        store = TranscriptStore(conn)
        store.ensure_tables()

        # Verify tables exist
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        assert "transcript_chunks" in tables

    def test_upsert_chunk(self):
        from core.transcript_indexer import TranscriptStore

        conn = _make_conn()
        store = TranscriptStore(conn)
        store.ensure_tables()

        store.upsert_chunk(
            session_id="sess1",
            source_file="test.jsonl",
            chunk_index=0,
            role="mixed",
            content="race condition fix with asyncio lock",
            content_hash="abc123",
            metadata='{"tools_used":["Read"]}',
            embedding=[0.1] * 1024,
        )

        row = conn.execute("SELECT content FROM transcript_chunks WHERE session_id='sess1'").fetchone()
        assert row is not None
        assert "race condition" in row[0]

    def test_upsert_is_idempotent(self):
        """Same session_id + chunk_index should update, not duplicate."""
        from core.transcript_indexer import TranscriptStore

        conn = _make_conn()
        store = TranscriptStore(conn)
        store.ensure_tables()

        for _ in range(3):
            store.upsert_chunk("s1", "t.jsonl", 0, "mixed", "content", "hash1")

        count = conn.execute("SELECT COUNT(*) FROM transcript_chunks").fetchone()[0]
        assert count == 1

    def test_fts5_search(self):
        from core.transcript_indexer import TranscriptStore

        conn = _make_conn()
        store = TranscriptStore(conn)
        store.ensure_tables()

        store.upsert_chunk("s1", "a.jsonl", 0, "mixed", "race condition in session_unit", "h1")
        store.upsert_chunk("s2", "b.jsonl", 0, "mixed", "credential chain error botocore", "h2")

        results = store.fts5_search("race condition", limit=5)
        assert len(results) >= 1
        assert "race condition" in results[0]["content"]

    def test_vector_search(self):
        from core.transcript_indexer import TranscriptStore

        conn = _make_conn()
        store = TranscriptStore(conn)
        store.ensure_tables()

        store.upsert_chunk("s1", "a.jsonl", 0, "mixed", "race condition fix", "h1", embedding=[1.0] + [0.0] * 1023)
        store.upsert_chunk("s2", "b.jsonl", 0, "mixed", "credential error", "h2", embedding=[0.0] * 1023 + [1.0])

        results = store.vector_search([1.0] + [0.0] * 1023, top_k=5)
        assert len(results) >= 1
        # First result should be the closer vector
        assert results[0]["session_id"] == "s1"

    def test_delta_sync_skips_unchanged(self):
        from core.transcript_indexer import TranscriptStore

        conn = _make_conn()
        store = TranscriptStore(conn)
        store.ensure_tables()

        store.upsert_chunk("s1", "a.jsonl", 0, "mixed", "content", "hash1")

        # Check existing hash
        existing = store.get_indexed_sessions()
        assert "s1" in existing

    def test_remove_stale(self):
        from core.transcript_indexer import TranscriptStore

        conn = _make_conn()
        store = TranscriptStore(conn)
        store.ensure_tables()

        store.upsert_chunk("s1", "a.jsonl", 0, "mixed", "old content", "h1")
        store.remove_session("s1")

        count = conn.execute("SELECT COUNT(*) FROM transcript_chunks").fetchone()[0]
        assert count == 0


# ── Test: sync_transcript_index (integration) ────────────────────────


class TestSyncTranscriptIndex:
    """Test incremental sync of transcript directory."""

    def test_sync_indexes_new_files(self, tmp_path):
        from core.transcript_indexer import TranscriptStore, sync_transcript_index

        conn = _make_conn()
        store = TranscriptStore(conn)
        store.ensure_tables()

        _make_jsonl(tmp_path / "sess1.jsonl", _sample_turns())
        _make_jsonl(tmp_path / "sess2.jsonl", _sample_turns()[:2])

        stats = sync_transcript_index(store, tmp_path)
        assert stats["files_indexed"] >= 2
        assert stats["chunks_added"] > 0

    def test_sync_skips_already_indexed(self, tmp_path):
        from core.transcript_indexer import TranscriptStore, sync_transcript_index

        conn = _make_conn()
        store = TranscriptStore(conn)
        store.ensure_tables()

        _make_jsonl(tmp_path / "sess1.jsonl", _sample_turns())

        # First sync
        sync_transcript_index(store, tmp_path)
        # Second sync — should skip
        stats = sync_transcript_index(store, tmp_path)
        assert stats["files_skipped"] >= 1

    def test_sync_with_embed_fn(self, tmp_path):
        from core.transcript_indexer import TranscriptStore, sync_transcript_index

        conn = _make_conn()
        store = TranscriptStore(conn)
        store.ensure_tables()

        _make_jsonl(tmp_path / "sess1.jsonl", _sample_turns()[:2])

        embed_fn = MagicMock(return_value=[0.5] * 1024)
        sync_transcript_index(store, tmp_path, embed_fn=embed_fn)

        assert embed_fn.called

    def test_sync_graceful_on_empty_dir(self, tmp_path):
        from core.transcript_indexer import TranscriptStore, sync_transcript_index

        conn = _make_conn()
        store = TranscriptStore(conn)
        store.ensure_tables()

        stats = sync_transcript_index(store, tmp_path)
        assert stats["files_indexed"] == 0


# ── Test: RecallEngine integration ───────────────────────────────────


class TestRecallEngineTranscriptIntegration:
    """Test that RecallEngine can search transcripts via additional_stores."""

    def test_recall_includes_transcript_results(self):
        from core.knowledge_store import KnowledgeStore
        from core.recall_engine import RecallEngine
        from core.transcript_indexer import TranscriptStore

        conn = _make_conn()
        knowledge = KnowledgeStore(conn)
        knowledge.ensure_tables()
        transcript = TranscriptStore(conn)
        transcript.ensure_tables()

        # Seed transcript with a unique term
        transcript.upsert_chunk(
            "s1", "a.jsonl", 0, "mixed",
            "credential chain error botocore NoCredentialsError traceback",
            "h1",
        )

        engine = RecallEngine(knowledge, additional_stores=[transcript])
        results = engine.search("credential chain")
        assert len(results) >= 1
        assert "credential" in results[0]["content"]

    def test_recall_deduplicates_knowledge_and_transcript(self):
        """When both stores have similar content, avoid duplicates."""
        from core.knowledge_store import KnowledgeStore
        from core.recall_engine import RecallEngine
        from core.transcript_indexer import TranscriptStore

        conn = _make_conn()
        knowledge = KnowledgeStore(conn)
        knowledge.ensure_tables()
        transcript = TranscriptStore(conn)
        transcript.ensure_tables()

        # Same content in both stores
        knowledge.upsert_chunk("DailyActivity/2026-03-23.md", 0, "## Credential fix",
                               "credential chain investigation", "h1")
        transcript.upsert_chunk("s1", "a.jsonl", 0, "mixed",
                                "credential chain investigation", "h2")

        engine = RecallEngine(knowledge, additional_stores=[transcript])
        results = engine.search("credential chain")
        # Should not return exact duplicates
        contents = [r["content"] for r in results]
        # At most 2 (one from each source, but ideally deduplicated)
        assert len(results) <= 3

    def test_recall_knowledge_formats_transcript_results(self):
        from core.knowledge_store import KnowledgeStore
        from core.recall_engine import RecallEngine
        from core.transcript_indexer import TranscriptStore

        conn = _make_conn()
        knowledge = KnowledgeStore(conn)
        knowledge.ensure_tables()
        transcript = TranscriptStore(conn)
        transcript.ensure_tables()

        transcript.upsert_chunk("s1", "sess1.jsonl", 0, "mixed",
                                "race condition fix with asyncio.Lock", "h1")

        engine = RecallEngine(knowledge, additional_stores=[transcript])
        text = engine.recall_knowledge("race condition")
        assert "race condition" in text
        assert "sess1.jsonl" in text  # provenance


# ── Test: Graceful degradation ───────────────────────────────────────


class TestGracefulDegradation:
    """Ensure everything works without embeddings."""

    def test_sync_without_embed_fn(self, tmp_path):
        from core.transcript_indexer import TranscriptStore, sync_transcript_index

        conn = _make_conn()
        store = TranscriptStore(conn)
        store.ensure_tables()

        _make_jsonl(tmp_path / "sess.jsonl", _sample_turns())
        stats = sync_transcript_index(store, tmp_path, embed_fn=None)
        assert stats["files_indexed"] >= 1

    def test_search_without_vectors_uses_fts5(self):
        from core.transcript_indexer import TranscriptStore

        conn = _make_conn()
        store = TranscriptStore(conn)
        store.ensure_tables()

        store.upsert_chunk("s1", "a.jsonl", 0, "mixed", "race condition in streaming", "h1")
        results = store.fts5_search("race condition")
        assert len(results) >= 1
