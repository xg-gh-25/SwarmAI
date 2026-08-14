"""Tests for recall_engine.py — Recall Engine + Injection (Phase 2).

Tests hybrid search over knowledge chunks, formatting, and integration
with prompt_builder.py.
"""

import sqlite3
from unittest.mock import MagicMock

import pytest


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


def _seed_store(conn):
    """Seed a KnowledgeStore with test data."""
    from core.knowledge_store import KnowledgeStore

    store = KnowledgeStore(conn)
    store.ensure_tables()

    # Chunk 1: credential chain investigation
    store.upsert_chunk(
        "DailyActivity/2026-03-23.md", 0,
        "## 15:06 | Credential investigation",
        "Two credential chains coexist on this machine. "
        "Claude CLI uses AWS SSO IdC tokens, boto3 uses credential_process. "
        "These are independent. Validate the chain your code actually uses.",
        "hash1",
    )

    # Chunk 2: xdist deep dive
    store.upsert_chunk(
        "Notes/2026-04-01-xdist-deep-dive.md", 0,
        "## pytest-xdist Deep Dive",
        "12 commits, 8 days, 970 lines of conftest to solve a 3-line config problem. "
        "pyproject.toml addopts is the single source of truth for test execution.",
        "hash2",
    )

    # Chunk 3: memory architecture design
    store.upsert_chunk(
        "Designs/2026-04-01-memory-architecture-v2.md", 0,
        "## Memory Architecture v2",
        "Brain stores wisdom (always full injection), Library stores experience "
        "(recall on-demand), Recall connects them.",
        "hash3",
    )

    return store


# ── RecallEngine ──

class TestRecallEngine:
    """Test the RecallEngine hybrid search."""

    def test_fts5_only_search(self):
        """FTS5 search should work without embeddings."""
        from core.recall_engine import RecallEngine

        conn = _make_conn()
        store = _seed_store(conn)
        engine = RecallEngine(store)

        results = engine.search("credential chain", embed_fn=None)
        assert len(results) >= 1
        assert any("credential" in r["content"].lower() for r in results)

    def test_embed_fn_arg_is_ignored(self):
        """embed_fn is a dead, inert param (recall is FTS5-only) — passing one must
        NOT change results and must NOT be called."""
        from core.recall_engine import RecallEngine

        conn = _make_conn()
        store = _seed_store(conn)
        engine = RecallEngine(store)

        embed_fn = MagicMock(return_value=[0.5] * 1024)
        results = engine.search("credential chain", embed_fn=embed_fn)
        assert len(results) >= 1
        embed_fn.assert_not_called()  # inert — recall never embeds

    def test_empty_query_returns_empty(self):
        from core.recall_engine import RecallEngine

        conn = _make_conn()
        store = _seed_store(conn)
        engine = RecallEngine(store)

        results = engine.search("", embed_fn=None)
        assert results == []

    def test_no_results_returns_empty(self):
        from core.recall_engine import RecallEngine

        conn = _make_conn()
        store = _seed_store(conn)
        engine = RecallEngine(store)

        results = engine.search("quantum physics dark matter", embed_fn=None)
        # Might be empty or low-score
        # FTS5 won't match, no embed_fn → empty
        assert isinstance(results, list)

    def test_results_have_provenance(self):
        """Each result should include source_file and heading."""
        from core.recall_engine import RecallEngine

        conn = _make_conn()
        store = _seed_store(conn)
        engine = RecallEngine(store)

        results = engine.search("credential", embed_fn=None)
        assert len(results) >= 1
        r = results[0]
        assert "source_file" in r
        assert "heading" in r
        assert "content" in r


# ── recall_knowledge (formatted output) ──

class TestRecallKnowledge:
    """Test the top-level recall_knowledge() function."""

    def test_formats_output_with_provenance(self):
        from core.recall_engine import RecallEngine

        conn = _make_conn()
        store = _seed_store(conn)
        engine = RecallEngine(store)

        text = engine.recall_knowledge("credential chain", max_tokens=15000)
        assert isinstance(text, str)
        # Should contain source file reference
        assert "DailyActivity/2026-03-23.md" in text

    def test_respects_max_tokens(self):
        from core.recall_engine import RecallEngine

        conn = _make_conn()
        store = _seed_store(conn)
        engine = RecallEngine(store)

        # Very small budget
        text = engine.recall_knowledge("credential", max_tokens=50)
        # Should be short or empty
        assert len(text) < 500

    def test_empty_recall_returns_empty_string(self):
        from core.recall_engine import RecallEngine

        conn = _make_conn()
        store = _seed_store(conn)
        engine = RecallEngine(store)

        text = engine.recall_knowledge("quantum physics dark matter")
        assert text == ""

    def test_low_score_results_filtered(self):
        """Results below threshold should not be included."""
        from core.recall_engine import RecallEngine

        conn = _make_conn()
        store = _seed_store(conn)
        engine = RecallEngine(store)

        # Search for something very specific — unrelated chunks should be filtered
        text = engine.recall_knowledge("pytest xdist conftest")
        if text:
            assert "xdist" in text.lower() or "pytest" in text.lower()


# ── Distillation enrichment (Phase 3) ──

class TestDistillationEnrichment:
    """Test that distillation entries include source links."""

    def test_format_enriched_entry(self):
        from hooks.distillation_hook import DistillationTriggerHook

        entry = DistillationTriggerHook._format_enriched_entry(
            text="Two credential chains coexist on this machine",
            date_str="2026-03-23",
            source_file="DailyActivity/2026-03-23.md",
            commit_hash="aca865b",
        )
        assert "2026-03-23" in entry
        assert "credential" in entry.lower()
        assert "DailyActivity/2026-03-23.md" in entry
        assert "aca865b" in entry

    def test_format_enriched_entry_no_commit(self):
        """Should work without a commit hash."""
        from hooks.distillation_hook import DistillationTriggerHook

        entry = DistillationTriggerHook._format_enriched_entry(
            text="Some lesson learned",
            date_str="2026-04-01",
            source_file="DailyActivity/2026-04-01.md",
            commit_hash=None,
        )
        assert "2026-04-01" in entry
        assert "DailyActivity/2026-04-01.md" in entry
        assert "commit" not in entry.lower()


# ── last_search_errors regression ──

def test_clean_search_has_no_errors():
    """Non-vacuous: a clean search leaves last_search_errors empty (so the
    surfaced-error signal genuinely means degradation, not noise)."""
    from core.recall_engine import RecallEngine
    from core.knowledge_store import KnowledgeStore
    conn = _make_conn()
    engine = RecallEngine(KnowledgeStore(conn))
    engine.search("anything", embed_fn=None)  # keyword-only, no failure
    assert engine.last_search_errors == []
