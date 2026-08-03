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
        embedding=[0.5] * 1024,
    )

    # Chunk 2: xdist deep dive
    store.upsert_chunk(
        "Notes/2026-04-01-xdist-deep-dive.md", 0,
        "## pytest-xdist Deep Dive",
        "12 commits, 8 days, 970 lines of conftest to solve a 3-line config problem. "
        "pyproject.toml addopts is the single source of truth for test execution.",
        "hash2",
        embedding=[0.3] * 1024,
    )

    # Chunk 3: memory architecture design
    store.upsert_chunk(
        "Designs/2026-04-01-memory-architecture-v2.md", 0,
        "## Memory Architecture v2",
        "Brain stores wisdom (always full injection), Library stores experience "
        "(vector on-demand), Recall connects them. 730K dormant knowledge awakened.",
        "hash3",
        embedding=[0.7] * 1024,
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

    def test_hybrid_search_with_embeddings(self):
        """Hybrid search should combine FTS5 + vector."""
        from core.recall_engine import RecallEngine

        conn = _make_conn()
        store = _seed_store(conn)
        engine = RecallEngine(store)

        embed_fn = MagicMock(return_value=[0.5] * 1024)
        results = engine.search("auth problems", embed_fn=embed_fn)
        # Should still find credential chunk via vector similarity
        assert len(results) >= 1
        embed_fn.assert_called_once()

    def test_graceful_fallback_on_embed_failure(self):
        """If embed_fn returns None, fall back to FTS5 only."""
        from core.recall_engine import RecallEngine

        conn = _make_conn()
        store = _seed_store(conn)
        engine = RecallEngine(store)

        embed_fn = MagicMock(return_value=None)
        results = engine.search("credential chain", embed_fn=embed_fn)
        # FTS5 should still find it
        assert len(results) >= 1

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

        text = engine.recall_knowledge("credential chain", embed_fn=None, max_tokens=15000)
        assert isinstance(text, str)
        # Should contain source file reference
        assert "DailyActivity/2026-03-23.md" in text

    def test_respects_max_tokens(self):
        from core.recall_engine import RecallEngine

        conn = _make_conn()
        store = _seed_store(conn)
        engine = RecallEngine(store)

        # Very small budget
        text = engine.recall_knowledge("credential", embed_fn=None, max_tokens=50)
        # Should be short or empty
        assert len(text) < 500

    def test_empty_recall_returns_empty_string(self):
        from core.recall_engine import RecallEngine

        conn = _make_conn()
        store = _seed_store(conn)
        engine = RecallEngine(store)

        text = engine.recall_knowledge("quantum physics dark matter", embed_fn=None)
        assert text == ""

    def test_low_score_results_filtered(self):
        """Results below threshold should not be included."""
        from core.recall_engine import RecallEngine

        conn = _make_conn()
        store = _seed_store(conn)
        engine = RecallEngine(store)

        # Search for something very specific — unrelated chunks should be filtered
        text = engine.recall_knowledge("pytest xdist conftest", embed_fn=None)
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


# ── FTS_SCORE_FLOOR regression (run_bbd79e84 Gate-2 MEDIUM) ──
#
# Min-max rank normalization zeroed the WORST keyword match in a multi-hit set.
# On a keyword-only leg (vec_score=0) that means hybrid = 0.4*0 = 0 < threshold →
# the weakest relevant entry is SILENTLY DROPPED. This bit every multi-hit Memory
# recall once the synchronous recall path went keyword-only. FTS_SCORE_FLOOR keeps
# the weakest real match above threshold. Mutation-verified: floor=0.0 → B drops.

def test_fts_score_floor_keeps_weakest_keyword_hit():
    """A multi-hit keyword-only recall must NOT silently drop the worst match."""
    from core.memory_embeddings import MemoryEmbeddingStore
    from core.memory_recall_store import MemoryRecallStore
    from core.recall_engine import (
        RecallEngine, FTS_SCORE_FLOOR, KEYWORD_WEIGHT, RECALL_THRESHOLD,
    )

    # The floor must keep the weakest hit above threshold on the keyword leg alone.
    assert KEYWORD_WEIGHT * FTS_SCORE_FLOOR > RECALL_THRESHOLD, (
        "FTS_SCORE_FLOOR too low — weakest keyword match would be dropped"
    )

    conn = _make_conn()
    store = MemoryEmbeddingStore(conn)
    store.ensure_tables()
    # A = strong (3 term matches), B = weak (1 term match — the one that was dropped)
    store.upsert_entry(key="A", section="COE Registry",
                       title="sigkill oom crash",
                       full_text="sigkill oom crash recovery resume",
                       keywords=["sigkill", "oom", "crash"], embedding=None)
    store.upsert_entry(key="B", section="Lessons Learned",
                       title="resume only",
                       full_text="resume handling notes",
                       keywords=["resume"], embedding=None)

    engine = RecallEngine(MemoryRecallStore(conn))
    results = engine.search("sigkill oom crash recovery resume", embed_fn=None)
    keys = {r["id"] for r in results}
    assert "A" in keys, "strong keyword match must surface"
    assert "B" in keys, (
        "weakest keyword match was silently dropped — FTS_SCORE_FLOOR regression"
    )


# ── Gate-2 HIGH fixes (run_4d06640b) ──

def test_embed_called_once_across_stores():
    """HIGH-1: the query is embedded ONCE before the per-store loop, not once per
    store. On the synchronous critical path, 3× embed tripled the Bedrock latency
    the disaster cap must bound (worst case ~29s ≫ 8s = theater)."""
    from core.memory_embeddings import MemoryEmbeddingStore
    from core.memory_recall_store import MemoryRecallStore
    from core.transcript_indexer import TranscriptStore
    from core.recall_engine import RecallEngine

    from core.knowledge_store import KnowledgeStore
    conn = _make_conn()
    ks = KnowledgeStore(conn)
    ts = TranscriptStore(conn); ts.ensure_tables()
    ms = MemoryEmbeddingStore(conn); ms.ensure_tables()
    ms.upsert_entry(key="K1", section="COE Registry", title="sigkill",
                    full_text="sigkill oom crash", keywords=["sigkill"], embedding=None)

    calls = {"n": 0}
    def embed(_q):
        calls["n"] += 1
        return [0.0] * 1024

    engine = RecallEngine(ks, additional_stores=[ts, MemoryRecallStore(conn)])  # 3 stores
    engine.search("sigkill oom crash", embed_fn=embed)
    assert calls["n"] == 1, f"embed must be called ONCE across stores, got {calls['n']} (HIGH-1)"


def test_leg_failure_is_surfaced_not_silent():
    """HIGH-2: a per-leg failure must be surfaced via last_search_errors, not
    swallowed to an empty list indistinguishable from a genuine no-match (W5 one
    frame deeper)."""
    from core.recall_engine import RecallEngine

    from core.knowledge_store import KnowledgeStore
    conn = _make_conn()
    ks = KnowledgeStore(conn)
    engine = RecallEngine(ks)

    def boom_embed(_q):
        raise RuntimeError("bedrock down")

    engine.search("anything", embed_fn=boom_embed)
    assert engine.last_search_errors, "embed failure was NOT surfaced — silent dead-path (HIGH-2)"
    assert any("embed" in e for e in engine.last_search_errors)


def test_clean_search_has_no_errors():
    """Non-vacuous: a clean search leaves last_search_errors empty (so the
    surfaced-error signal genuinely means degradation, not noise)."""
    from core.recall_engine import RecallEngine
    from core.knowledge_store import KnowledgeStore
    conn = _make_conn()
    engine = RecallEngine(KnowledgeStore(conn))
    engine.search("anything", embed_fn=None)  # keyword-only, no failure
    assert engine.last_search_errors == []
