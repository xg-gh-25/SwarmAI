"""Tests for the MEMORY recall semantic upgrade (run_1e2e663b, P0).

Ports MemPalace's hybrid recipe into the recall path:
- Okapi-BM25+IDF keyword leg (CJK-bigram aware) replacing token-overlap
- Commensurable legs: BM25 min-max normalized, vector kept absolute
- Missing-vector renorm (§3.6.1): un-embedded entries score on keyword alone
- DB-backed recall wiring (keyword-first / hybrid-on-miss) + stale-index guard
- Hit-log stdout surface (hit_layer, hit_section, drilled)

Design: Knowledge/Designs/2026-06-26-e2e-recall-architecture-design.md §3.6/DP-v/§3.6.1/§4.

These are ADDITIVE to test_hybrid_memory.py — the existing file's contract
(default-arg behavior of hybrid_memory_search) is preserved unchanged.
"""

import json
import sqlite3
import pytest


# ===========================================================================
# Change 3 (tracer bullet): Missing-vector renorm (§3.6.1)
# ===========================================================================

class TestMissingVectorRenorm:
    """An un-embedded but keyword-strong entry must NOT be rank-suppressed
    below embedded peers under the top-k cut. The renorm scores it on the
    keyword leg alone (hybrid = kw) instead of 0.6*0 + 0.4*kw."""

    def test_unembedded_entry_competes_on_keyword_alone(self):
        """GS_RCHAIN_MISSING_VECTOR core: un-embedded keyword-strong entry
        out-ranks an embedded weak-keyword peer once renorm is applied."""
        from core.memory_embeddings import hybrid_memory_search

        # E1 embedded (has vector), weak keyword. U1 un-embedded, strong keyword.
        keyword_scores = {"E1": 0.2, "U1": 0.8}
        vector_scores = {"E1": 0.5}  # only E1 has a vector
        embedded_keys = {"E1"}       # U1 is NOT embedded

        results = hybrid_memory_search(
            keyword_scores=keyword_scores,
            vector_scores=vector_scores,
            embedded_keys=embedded_keys,
        )
        ranked = {r.key: r.hybrid for r in results}

        # E1: 0.6*0.5 + 0.4*0.2 = 0.38  (embedded → normal merge)
        # U1: renorm → hybrid = kw = 0.8 (NOT 0.6*0 + 0.4*0.8 = 0.32)
        assert ranked["U1"] == pytest.approx(0.8, abs=0.01)
        assert ranked["E1"] == pytest.approx(0.38, abs=0.01)
        assert results[0].key == "U1", "renorm must lift un-embedded entry above embedded peer"

    def test_embedded_key_missing_from_topk_is_not_renormed(self):
        """Precision guard: an entry that IS embedded but absent from the
        vector top-k (vs defaults to 0) must keep the 0.6*0+0.4*kw merge —
        NOT be renorm'd. Renorm is for un-embedded only."""
        from core.memory_embeddings import hybrid_memory_search

        keyword_scores = {"E2": 0.8}
        vector_scores = {}            # E2 didn't make vector top-k this query
        embedded_keys = {"E2"}        # but E2 IS embedded

        results = hybrid_memory_search(
            keyword_scores=keyword_scores,
            vector_scores=vector_scores,
            embedded_keys=embedded_keys,
        )
        # E2 is embedded → normal merge: 0.6*0 + 0.4*0.8 = 0.32 (NOT 0.8)
        assert results[0].hybrid == pytest.approx(0.32, abs=0.01)

    def test_embedded_keys_none_preserves_legacy_behavior(self):
        """Backward compat: embedded_keys=None (default) → identical to the
        pre-upgrade merge. Protects the 3 existing test_hybrid_memory.py cases
        and the sole prod caller."""
        from core.memory_embeddings import hybrid_memory_search

        keyword_scores = {"RC01": 0.8, "KD01": 0.5}
        vector_scores = {}  # Bedrock down / no embeddings

        results = hybrid_memory_search(
            keyword_scores=keyword_scores,
            vector_scores=vector_scores,
        )  # no embedded_keys arg
        # RC01: 0.6*0 + 0.4*0.8 = 0.32 (legacy: NOT renorm'd to 0.8)
        assert results[0].key == "RC01"
        assert results[0].hybrid == pytest.approx(0.32, abs=0.01)


# ===========================================================================
# Change 1: Okapi-BM25+IDF keyword scorer (CJK-bigram aware)
# ===========================================================================

class TestBM25Scorer:
    """A real Okapi-BM25 scorer with corpus-relative IDF over the candidate
    set, replacing the IDF-less token-overlap keyword leg."""

    def test_idf_rewards_discriminative_terms(self):
        """A query term appearing in 1 doc scores that doc higher than a term
        appearing in every doc (IDF down-weights common terms)."""
        from core.memory_index import _bm25_scores

        docs = {
            "A": "deadlock retry backoff",       # 'deadlock' is rare
            "B": "retry timeout pool",
            "C": "retry queue worker",
        }
        # 'deadlock' (df=1) should make A win over a query that also has the
        # ubiquitous 'retry' (df=3, near-zero IDF).
        scores = _bm25_scores("deadlock retry", docs)
        assert scores["A"] > scores["B"]
        assert scores["A"] > scores["C"]

    def test_cjk_bigram_partial_match(self):
        """CJK partial match survives: '竞品分析的结论' shares bigrams with
        '竞品分析陷阱' (竞品/品分/分析), so token-exact BM25 still matches."""
        from core.memory_index import _bm25_scores

        docs = {
            "A": "竞品分析陷阱",      # shares 竞品/品分/分析
            "B": "会话生命周期管理",  # unrelated CJK
        }
        scores = _bm25_scores("竞品分析的结论是什么", docs)
        assert scores.get("A", 0.0) > 0.0, "CJK bigram overlap must score"
        assert scores.get("A", 0.0) > scores.get("B", 0.0)

    def test_empty_query_and_empty_corpus(self):
        """Edge cases: empty query → all-zero; empty corpus → {}."""
        from core.memory_index import _bm25_scores
        assert _bm25_scores("", {"A": "anything"}) == {} or all(
            v == 0.0 for v in _bm25_scores("", {"A": "anything"}).values()
        )
        assert _bm25_scores("query", {}) == {}

    def test_scores_are_nonnegative(self):
        """BM25 with the ln(...+1) IDF form is always >= 0 (no negative IDF)."""
        from core.memory_index import _bm25_scores
        docs = {"A": "a a a a a", "B": "a b"}  # 'a' in all docs
        scores = _bm25_scores("a", docs)
        assert all(v >= 0.0 for v in scores.values())


# ===========================================================================
# Change 4: Recall wiring — keyword-first / hybrid-on-miss + stale-index guard
# ===========================================================================

_RECALL_MEMORY = """\
<!-- MEMORY_INDEX_START -->
## Memory Index
- [COE01] exit code -9 cascading SIGKILL failure | sigkill, oom, crash
- [LL01] Sync wrappers around async cleanup leak | async, cleanup, leak
<!-- MEMORY_INDEX_END -->

## COE Registry
- 2026-03-17: **Sev-1: exit code -9 cascading SIGKILL** — OOM kills, retry worse.

## Lessons Learned
- 2026-03-22: **Sync wrappers around async cleanup = resource leaks** — needs async callers.
"""


class TestRecallKeywordFirst:
    """recall_context stays keyword-first (no Bedrock on the hot path) and only
    escalates to hybrid when keyword finds nothing."""

    def test_keyword_hit_does_not_invoke_hybrid(self):
        """A keyword hit must NOT trigger the 2.65s/query embed path."""
        from unittest.mock import patch
        from core.context_recall import recall_context

        with patch("core.memory_index._hybrid_section_scores") as mock_hybrid:
            res = recall_context(
                "MEMORY.md", "exit code sigkill oom",
                memory_content=_RECALL_MEMORY,
            )
            assert res.allowed is True
            assert "COE Registry" in res.sections
            mock_hybrid.assert_not_called()  # keyword-first: no embed on hit

    def test_keyword_miss_escalates_to_hybrid(self):
        """A keyword miss escalates to the hybrid (semantic) path."""
        from unittest.mock import patch
        from core.context_recall import recall_context

        # Query with zero keyword overlap; hybrid 'finds' the COE semantically.
        with patch("core.memory_index._hybrid_section_scores") as mock_hybrid:
            mock_hybrid.return_value = {"COE Registry": 0.8}
            res = recall_context(
                "MEMORY.md", "application abruptly terminates at boot",
                memory_content=_RECALL_MEMORY,
            )
            mock_hybrid.assert_called_once()
            assert "COE Registry" in res.sections

    def test_stale_index_guard_never_silently_drops(self):
        """GS_RCHAIN_STALE_INDEX core: if hybrid (DB) ranks a section that is
        ABSENT from the live passed string (MEMORY.md edited after last embed
        sync), recall must NOT return an empty slice for it — it falls back so
        the live keyword result is what surfaces, never a silent drop."""
        from unittest.mock import patch
        from core.context_recall import recall_context

        # DB ranks a section name that no longer exists in the live string.
        with patch("core.memory_index._hybrid_section_scores") as mock_hybrid:
            mock_hybrid.return_value = {"Deleted Section": 0.9, "COE Registry": 0.5}
            res = recall_context(
                "MEMORY.md", "totally unrelated semantic query xyzzy",
                memory_content=_RECALL_MEMORY,
            )
            # "Deleted Section" is absent from _RECALL_MEMORY → must be dropped
            # from the RESULT (not returned as an empty block), and a real
            # section must still surface.
            assert "Deleted Section" not in res.sections
            # COE Registry IS in the live string → it survives the guard.
            assert "COE Registry" in res.sections


class TestRecallHitLog:
    """recall_context surfaces hit-log fields for the CLI / ingestion Darwin."""

    def test_result_carries_hit_layer(self):
        """RecallResult exposes hit_layer ∈ {keyword, hybrid, none}."""
        from core.context_recall import recall_context

        res = recall_context(
            "MEMORY.md", "exit code sigkill oom",
            memory_content=_RECALL_MEMORY,
        )
        assert res.hit_layer == "keyword"

    def test_miss_reports_none_layer(self):
        """A total miss reports hit_layer='none', drilled=False."""
        from unittest.mock import patch
        from core.context_recall import recall_context
        with patch("core.memory_index._hybrid_section_scores", return_value={}):
            res = recall_context(
                "MEMORY.md", "zzz no match anywhere qqq",
                memory_content=_RECALL_MEMORY,
            )
            assert res.hit_layer == "none"
            assert res.drilled is False
