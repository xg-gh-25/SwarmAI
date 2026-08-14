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

    def test_keyword_miss_no_longer_escalates_to_hybrid(self):
        """RETIRED→REWRITTEN (pure-filesystem design §3.3/§5.4, 2026-06-28): the
        hybrid-on-miss escalation was REMOVED — recall is keyword-only, no vector.
        A keyword miss now simply returns no sections (the agent re-searches with
        synonyms instead — agentic safety net, §3.4). The vector leg must NEVER
        fire even on a miss."""
        from unittest.mock import patch
        from core.context_recall import recall_context

        # Zero keyword overlap → keyword miss. With the vector leg gone, _hybrid
        # is never consulted (it's an inert stub returning {} anyway).
        with patch("core.memory_index._hybrid_section_scores") as mock_hybrid:
            mock_hybrid.return_value = {"COE Registry": 0.8}  # would-be vector hit
            res = recall_context(
                "MEMORY.md", "application abruptly terminates at boot",
                memory_content=_RECALL_MEMORY,
            )
            # Pure-filesystem: hybrid is NOT called on a keyword miss anymore.
            mock_hybrid.assert_not_called()
            assert res.allowed is True
            # No keyword overlap → no section surfaces (agent re-greps; §3.4).
            assert res.sections == ()

    def test_keyword_only_surfaces_live_sections(self):
        """Replaces the stale-index hybrid guard: keyword scoring only ever ranks
        sections parsed from the LIVE passed string, so a section absent from the
        live content can never surface (no DB-ranked stale names — the whole
        stale-index hazard is gone because there is no DB-backed vector leg)."""
        from core.context_recall import recall_context

        res = recall_context(
            "MEMORY.md", "exit code sigkill oom",
            memory_content=_RECALL_MEMORY,
        )
        assert res.allowed is True
        # Only a section that exists in the live string can surface.
        assert "COE Registry" in res.sections
        assert "Deleted Section" not in res.sections


# ===========================================================================
# Follow-up F1: BM25 normalization — degenerate set must NOT over-promote to 1.0
# (run_aba4f77a, Gate-2 MEDIUM). Saturation s/(s+K1) on RAW bm25, applied at the
# scorer-output layer; _minmax_normalize stays a generic [0,1] normalizer.
# ===========================================================================

class TestBM25Normalization:
    """A single-candidate or all-equal BM25 set must map by SATURATION of the
    raw score (a weak lone match → small, a strong lone match → near-1), NOT
    blanket 1.0. Multi-candidate spread still min-max normalizes."""

    def test_single_weak_candidate_not_promoted_to_one(self):
        """F1 core: a lone candidate with a SMALL raw BM25 score must get a
        small normalized score (saturation), not 1.0."""
        from core.memory_index import _normalize_bm25_scores, BM25_K1

        raw = {"A": 0.3}  # single weak candidate
        out = _normalize_bm25_scores(raw)
        # saturation: 0.3/(0.3+1.5) = 0.1667 — NOT 1.0
        assert out["A"] == pytest.approx(0.3 / (0.3 + BM25_K1), abs=0.001)
        assert out["A"] < 0.5, "a weak lone candidate must not get full weight"

    def test_single_strong_candidate_approaches_one(self):
        """A lone candidate with a LARGE raw score saturates toward 1.0."""
        from core.memory_index import _normalize_bm25_scores, BM25_K1

        raw = {"A": 30.0}  # single strong candidate
        out = _normalize_bm25_scores(raw)
        assert out["A"] == pytest.approx(30.0 / (30.0 + BM25_K1), abs=0.001)
        assert out["A"] > 0.9, "a strong lone candidate should be near full weight"

    def test_all_equal_candidates_use_saturation(self):
        """All-equal (hi<=lo) set → saturation of the shared raw value, not 1.0."""
        from core.memory_index import _normalize_bm25_scores, BM25_K1

        raw = {"A": 0.5, "B": 0.5}
        out = _normalize_bm25_scores(raw)
        expected = 0.5 / (0.5 + BM25_K1)
        assert out["A"] == pytest.approx(expected, abs=0.001)
        assert out["B"] == pytest.approx(expected, abs=0.001)
        assert out["A"] < 1.0

    def test_multi_candidate_spread_minmax_normalizes(self):
        """A real spread (hi>lo) still min-max normalizes: top→1.0, bottom→0.0."""
        from core.memory_index import _normalize_bm25_scores

        raw = {"A": 5.0, "B": 1.0, "C": 3.0}
        out = _normalize_bm25_scores(raw)
        assert out["A"] == pytest.approx(1.0, abs=0.001)
        assert out["B"] == pytest.approx(0.0, abs=0.001)
        assert out["C"] == pytest.approx(0.5, abs=0.001)

    def test_empty_returns_empty(self):
        from core.memory_index import _normalize_bm25_scores
        assert _normalize_bm25_scores({}) == {}


# ===========================================================================
# Follow-up F4: temporal down-weight on the BM25 leg — superseded entries must
# be down-weighted in _hybrid_section_scores (run_aba4f77a, Gate-2 MEDIUM).
# Driven through the REAL scorer (GUI32/PIT13: no mocking the function under
# change — only the Bedrock network boundary is stubbed).
# ===========================================================================

def _onehot(idx: int, dim: int = 1024) -> list[float]:
    v = [0.0] * dim
    v[idx] = 1.0
    return v


class _FixedEmbedder:
    """Stub ONLY the Bedrock network boundary — returns a fixed query vector."""

    def __init__(self, query_vec):
        self._q = query_vec

    def embed_text(self, text):  # noqa: ARG002 — fixed by design
        return self._q


class TestTemporalDownweightKeyword:
    """NEW ARCHITECTURE (2026-08-14): the index-based _keyword_section_scores was
    DELETED. Superseded handling now lives in the body-BM25 scorer
    (_section_body_scores), which STRIPS superseded entries from each section body
    BEFORE scoring (stronger than the old 0.1x down-weight). This REWRITES the F4
    test to drive the surviving body-BM25 path."""

    def test_superseded_entry_stripped_from_body_scoring(self):
        """F4 (body path): a section whose only query-matching entry is SUPERSEDED
        scores 0 (the entry is stripped before BM25); the active peer still scores."""
        from core.memory_index import _section_body_scores

        # Two sections, each a single entry sharing the discriminative keyword.
        # GUI01 (Guidelines) active; PIT01 (Pitfalls) is the superseded peer.
        sections = {
            "Guidelines": "- [GUI01] **zephyr quartz active guideline** — detail\n",
            "Pitfalls": "- [PIT01] **zephyr quartz old superseded** — detail\n",
        }
        active = _section_body_scores("zephyr quartz", sections, superseded_keys=set(),
                                      include_evergreen=True)
        stripped = _section_body_scores("zephyr quartz", sections, superseded_keys={"PIT01"},
                                        include_evergreen=True)
        assert active.get("Pitfalls", 0.0) > 0.0, "precondition: PIT01 matches when active"
        # Superseded PIT01 stripped → Pitfalls no longer matches; Guidelines still does.
        assert stripped.get("Pitfalls", 0.0) == 0.0, "superseded entry must be stripped"
        assert stripped.get("Guidelines", 0.0) > 0.0, "active peer still scores"


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
