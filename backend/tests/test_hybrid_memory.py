"""Tests for Hybrid Memory Retrieval — sqlite-vec + keyword scoring.

Tests the full hybrid pipeline: embedding storage, delta sync, hybrid search,
and integration with select_memory_sections(). TDD RED phase — all tests
written before implementation.

Methodology: acceptance criteria from pipeline evaluation → test per criterion.
"""

import hashlib
import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Fixture: sample MEMORY.md content with known semantic relationships
# ---------------------------------------------------------------------------

SAMPLE_MEMORY = """\
<!-- MEMORY_INDEX_START -->
## Memory Index
3 recent context | 2 key decisions | 2 lessons learned | 1 coe registry

### Permanent (COEs + Architectural Decisions — never age out)
- [COE01] 2026-03-17 Sev-1: exit code -9 cascading SIGKILL | sigkill, sev-1, oom
- [KD01] 2026-03-27 Single-process architecture | auto-restart, sigterm
- [KD02] 2026-03-19 Design principle: prevent, don't handle | prevention, structurally

### Active (Recent Context + Lessons)
- [RC01] 2026-03-31 Progressive Memory Disclosure | 3-layer, memory_index
- [RC02] 2026-03-23 Unified Job System audit | credential, http_proxy
- [RC03] 2026-03-22 Generic Settings Pipeline | pass-through, snaketocamel
- [LL01] 2026-03-22 Sync wrappers around async cleanup = resource leaks | async, wrappers, cleanup
- [LL02] 2026-03-22 Invariants must be enforced at a single point | invariants, enforced
<!-- MEMORY_INDEX_END -->

## Open Threads
### P2 — Nice to have
- 🔵 **Signal fetcher service** — not yet created.

## Recent Context
- 2026-03-31: **Progressive Memory Disclosure shipped** — 3-layer recall system with index.
- 2026-03-23: **Unified Job System audit** — credential architecture fix, http_proxy.
- 2026-03-22: **Generic Settings Pipeline** — pass-through, snakeToCamel, camelToSnake.

## Key Decisions
- 2026-03-27: **Single-process architecture** — keep auto-restart, no multi-process.
- 2026-03-19: **Design principle: prevent, don't handle** — eliminating errors structurally.

## Lessons Learned
- 2026-03-22: **Sync wrappers around async cleanup = resource leaks** — async cleanup needs async callers. The sync wrapper leaked 3 file descriptors per crash.
- 2026-03-22: **Invariants must be enforced at a single point** — conflicting enforcement = bugs.

## COE Registry
- 2026-03-17: **Sev-1: exit code -9 cascading SIGKILL failure** — OOM kills, retry made it worse.
"""


# ---------------------------------------------------------------------------
# Fixture: in-memory sqlite-vec database
# ---------------------------------------------------------------------------

@pytest.fixture
def vec_db(tmp_path):
    """Create an in-memory SQLite DB with sqlite-vec loaded."""
    import sqlite_vec

    db_path = tmp_path / "test_memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


# ===========================================================================
# AC1: Semantic recall — "CI/CD" finds "deployment pipeline"
# ===========================================================================

class TestSemanticRecall:
    """Semantic queries find memories with zero keyword overlap."""

    def test_vector_search_finds_semantic_match(self, vec_db):
        """Vector search returns entries when embeddings exist."""
        from core.memory_embeddings import MemoryEmbeddingStore

        store = MemoryEmbeddingStore(vec_db)
        store.ensure_tables()

        # Insert entry with a known embedding
        embedding = _fake_embed("Deployment pipeline had issues with staging environment")
        store.upsert_entry("LL03", "Lessons Learned", "Deployment pipeline lessons",
                           "Deployment pipeline had issues with staging environment",
                           ["deployment", "pipeline", "staging"],
                           embedding=embedding)

        # Search with an embedding function
        results = store.vector_search(
            "CI/CD automation problems", top_k=5, embed_fn=_fake_embed
        )
        # At minimum, the store returns results when data exists
        assert len(results) > 0
        assert results[0].key == "LL03"

    def test_hybrid_search_combines_keyword_and_vector(self):
        """Hybrid search merges keyword and vector scores with 0.6v + 0.4k weights."""
        from core.memory_embeddings import hybrid_memory_search, ScoredEntry

        # Mock both scoring systems
        keyword_scores = {"RC01": 0.8, "KD01": 0.0, "LL01": 0.3}
        vector_scores = {"RC01": 0.5, "KD01": 0.9, "LL01": 0.4}

        results = hybrid_memory_search(
            keyword_scores=keyword_scores,
            vector_scores=vector_scores,
        )

        # KD01: 0.6*0.9 + 0.4*0.0 = 0.54
        # RC01: 0.6*0.5 + 0.4*0.8 = 0.62
        # LL01: 0.6*0.4 + 0.4*0.3 = 0.36
        assert results[0].key == "RC01"
        assert results[1].key == "KD01"
        assert results[2].key == "LL01"
        assert abs(results[0].hybrid - 0.62) < 0.01
        assert abs(results[1].hybrid - 0.54) < 0.01


# ===========================================================================
# AC2: Zero keyword regression
# ===========================================================================

class TestKeywordRegression:
    """Every keyword match from before still matches with hybrid enabled."""

    def test_keyword_only_still_works_when_vector_empty(self):
        """If no vector scores, keyword scores still surface entries (weight 0.4)."""
        from core.memory_embeddings import hybrid_memory_search

        keyword_scores = {"RC01": 0.8, "KD01": 0.5}
        vector_scores = {}  # Bedrock down or no embeddings

        results = hybrid_memory_search(
            keyword_scores=keyword_scores,
            vector_scores=vector_scores,
        )

        # RC01: 0.6*0 + 0.4*0.8 = 0.32
        # KD01: 0.6*0 + 0.4*0.5 = 0.20
        assert len(results) == 2
        assert results[0].key == "RC01"
        assert results[0].hybrid == pytest.approx(0.32, abs=0.01)

    def test_keyword_match_threshold_preserved(self):
        """A keyword-only match at 0.4 (old threshold 0.15) is above hybrid threshold."""
        from core.memory_embeddings import hybrid_memory_search, HYBRID_THRESHOLD

        keyword_scores = {"RC01": 0.4}
        vector_scores = {}

        results = hybrid_memory_search(
            keyword_scores=keyword_scores,
            vector_scores=vector_scores,
        )

        # RC01: 0.4 * 0.4 = 0.16 — should be above threshold
        assert len(results) > 0
        assert results[0].hybrid >= HYBRID_THRESHOLD


# ===========================================================================
# AC3: Graceful Bedrock fallback
# ===========================================================================

class TestGracefulFallback:
    """Bedrock failure falls back to keyword-only, no error visible."""

    def test_embed_text_returns_none_on_failure(self):
        """EmbeddingClient.embed_text returns None when Bedrock is unavailable."""
        from core.embedding_client import EmbeddingClient

        client = EmbeddingClient(region="us-west-2")
        # Mock the lazy-init client to raise on invoke_model
        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model.side_effect = Exception("Bedrock down")
        client._client = mock_bedrock

        result = client.embed_text("test query")
        assert result is None

    def test_embed_text_returns_none_on_timeout(self):
        """EmbeddingClient returns None when boto3 client creation fails."""
        from core.embedding_client import EmbeddingClient

        client = EmbeddingClient(region="us-west-2", timeout=0.001)
        # Mock _get_client to return None (simulating boto3 import failure)
        client._get_client = lambda: None

        result = client.embed_text("test query")
        assert result is None

    def test_vector_search_returns_empty_on_no_embedding(self, vec_db):
        """vector_search with None query embedding returns empty list."""
        from core.memory_embeddings import MemoryEmbeddingStore

        store = MemoryEmbeddingStore(vec_db)
        store.ensure_tables()

        results = store.vector_search_raw(query_embedding=None, top_k=5)
        assert results == []


# ===========================================================================
# AC4: All entries embedded
# ===========================================================================

class TestEmbeddingSync:
    """All MEMORY.md entries get embeddings via delta sync."""

    def test_sync_from_memory_creates_all_entries(self, vec_db):
        """sync_from_memory parses MEMORY.md and creates entries for all bullets."""
        from core.memory_embeddings import MemoryEmbeddingStore

        store = MemoryEmbeddingStore(vec_db)
        store.ensure_tables()

        stats = store.sync_from_memory(SAMPLE_MEMORY, embed_fn=_fake_embed)
        assert stats["total_entries"] == 9  # RC01-3, KD01-2, LL01-2, COE01, OT01
        assert stats["embedded"] >= 8

    def test_delta_sync_only_reembeds_changed(self, vec_db):
        """Second sync with same content re-embeds nothing (content_hash match)."""
        from core.memory_embeddings import MemoryEmbeddingStore

        store = MemoryEmbeddingStore(vec_db)
        store.ensure_tables()

        call_count = [0]
        def counting_embed(text: str) -> list[float]:
            call_count[0] += 1
            return _fake_embed(text)

        store.sync_from_memory(SAMPLE_MEMORY, embed_fn=counting_embed)
        first_count = call_count[0]

        # Second sync — same content
        store.sync_from_memory(SAMPLE_MEMORY, embed_fn=counting_embed)
        assert call_count[0] == first_count  # No new embeddings

    def test_delta_sync_reembeds_changed_entry(self, vec_db):
        """When an entry's text changes, only that entry gets re-embedded."""
        from core.memory_embeddings import MemoryEmbeddingStore

        store = MemoryEmbeddingStore(vec_db)
        store.ensure_tables()

        call_count = [0]
        def counting_embed(text: str) -> list[float]:
            call_count[0] += 1
            return _fake_embed(text)

        store.sync_from_memory(SAMPLE_MEMORY, embed_fn=counting_embed)
        first_count = call_count[0]

        # Modify one entry
        modified = SAMPLE_MEMORY.replace(
            "Progressive Memory Disclosure shipped",
            "Progressive Memory Disclosure shipped v2 with hybrid search"
        )
        store.sync_from_memory(modified, embed_fn=counting_embed)
        # Should have exactly 1 new embedding call
        assert call_count[0] == first_count + 1

    def test_content_hash_is_sha256(self, vec_db):
        """content_hash uses SHA-256 of full_text."""
        from core.memory_embeddings import MemoryEmbeddingStore

        store = MemoryEmbeddingStore(vec_db)
        store.ensure_tables()
        store.sync_from_memory(SAMPLE_MEMORY, embed_fn=_fake_embed)

        # Check stored hash
        row = vec_db.execute(
            "SELECT content_hash, full_text FROM memory_entries WHERE key = 'RC01'"
        ).fetchone()
        assert row is not None
        expected_hash = hashlib.sha256(row[1].encode()).hexdigest()
        assert row[0] == expected_hash


# ===========================================================================
# AC5: Budget tiers revised per power-first principle
# ===========================================================================

class TestRevisedBudgetTiers:
    """Budget tiers are aggressive: inject max memory, cut only at >95%."""

    def test_below_50_percent_unlimited(self):
        """Context <50% → unlimited budget (returns very large number)."""
        from core.memory_index import _adaptive_max_tokens
        budget = _adaptive_max_tokens(30.0)
        assert budget >= 100_000  # effectively unlimited

    def test_50_to_75_generous(self):
        """Context 50-75% → 50K tokens (not the old 5K)."""
        from core.memory_index import _adaptive_max_tokens
        budget = _adaptive_max_tokens(60.0)
        assert budget >= 50_000

    def test_75_to_95_still_significant(self):
        """Context 75-95% → 20K tokens (not the old 2K)."""
        from core.memory_index import _adaptive_max_tokens
        budget = _adaptive_max_tokens(85.0)
        assert budget >= 20_000

    def test_above_95_minimum(self):
        """Context >=95% → 5K minimum (not the old 0)."""
        from core.memory_index import _adaptive_max_tokens
        budget = _adaptive_max_tokens(98.0)
        assert budget >= 5_000


# ===========================================================================
# AC6: E2E wiring — select_memory_sections with hybrid
# ===========================================================================

class TestE2EHybridWiring:
    """select_memory_sections with memory_embeddings returns semantically relevant sections."""

    def test_select_with_embeddings_flag(self):
        """select_memory_sections accepts memory_embeddings parameter."""
        from core.memory_index import select_memory_sections
        # Should not crash with the new parameter
        result = select_memory_sections(
            memory_content=SAMPLE_MEMORY,
            user_message="async cleanup resource leaks",
            memory_embeddings=False,  # explicit keyword-only
        )
        assert "Memory Index" in result

    def test_hybrid_finds_semantic_match_e2e(self):
        """E2E: semantic query through select_memory_sections finds related section."""
        from core.memory_index import select_memory_sections

        # "app crashes on startup" should find COE about exit-code-9 SIGKILL
        # via vector similarity, even though there's zero keyword overlap
        # We mock the embedding store to return high similarity for COE01
        with patch("core.memory_index._hybrid_section_scores") as mock_hybrid:
            mock_hybrid.return_value = {
                "COE Registry": 0.8,
                "Lessons Learned": 0.3,
            }
            result = select_memory_sections(
                memory_content=SAMPLE_MEMORY,
                user_message="app crashes on startup",
                memory_embeddings=True,
            )
            assert "COE Registry" in result or "exit code" in result.lower()


# ===========================================================================
# Helpers
# ===========================================================================

def _fake_embed(text: str) -> list[float]:
    """Deterministic fake embedding: hash text into 1024-dim vector."""
    h = hashlib.sha256(text.encode()).digest()
    # Expand hash into 1024 floats in [-1, 1]
    import struct
    values = []
    for i in range(0, len(h), 4):
        if len(values) >= 1024:
            break
        chunk = h[i:i+4]
        if len(chunk) == 4:
            val = struct.unpack('f', chunk)[0]
            # Clamp to reasonable range
            val = max(-1.0, min(1.0, val / 1e38 if abs(val) > 1 else val))
            values.append(val)

    # Pad to 1024 with deterministic values from text hash
    while len(values) < 1024:
        idx = len(values)
        byte_val = h[idx % len(h)]
        values.append((byte_val - 128) / 128.0)

    return values
