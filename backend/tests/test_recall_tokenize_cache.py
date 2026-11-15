"""Tests for the recall doc-tokenize cache.

WHAT IS TESTED: `memory_index._bm25_tokenize_doc` — a bounded, content-keyed
memoization of per-DOC BM25 tokenization — and that wiring it into
`_bm25_scores` leaves scoring BYTE-IDENTICAL while cutting the repeated
query-independent tokenize cost.

WHY: recall's ddd leg re-tokenized the whole DDD corpus (~260K tokens, CJK
bigram expansion ≈376ms) on EVERY first-message recall, because `_bm25_scores`
tokenizes `docs` fresh each call. Doc tokenization is a pure function of the
doc CONTENT (query-independent), so it is safely cacheable; the QUERY
tokenization at `_bm25_scores`'s `_bm25_query_weights(query)` must stay
UNCACHED (varies per query, cheap).

KEY INVARIANTS (the properties, not the implementation):
  - cached == uncached: the cache changes HOW doc tokens are produced, never
    WHAT they are, so BM25 scores are identical to the pre-cache path.
  - content-keyed → auto-invalidates: a doc whose text changes gets a fresh
    tokenization (no stale recall — the FR-2 "no stale" guarantee).
  - bounded: the cache evicts oldest beyond a cap (memory bound only); eviction
    never changes a result, only cache-hit rate (STEERING #2 — not a cost cap).
  - query path uncached: `_bm25_tokenize` itself (the query tokenizer + the
    public contract pinned by test_cjk_encoding.py) is UNCHANGED.
"""
import importlib
import logging

import pytest

from core import memory_index


# ─────────────────────────────────────────────────────────────────────────────
# AC2 — cached == uncached (byte-identical scoring)
# ─────────────────────────────────────────────────────────────────────────────
class TestCachedEqualsUncached:
    """The cache must not change any BM25 score, for any (query, corpus)."""

    def _uncached_scores(self, query, docs):
        """Recompute BM25 the pre-cache way: tokenize every doc inline with the
        UNcached `_bm25_tokenize`, mirroring the exact math of `_bm25_scores`
        before the cache (each query term weighted by `_bm25_query_weights`: 1 for a
        word, 1/n for each bigram of an n-bigram Chinese phrase). This is the
        independent oracle — NOT a copy of the production function, so it genuinely
        cross-checks it."""
        import math
        if not docs:
            return {}
        q_weight = memory_index._bm25_query_weights(query)
        if not q_weight:
            return {}
        doc_tokens = {k: memory_index._bm25_tokenize(text) for k, text in docs.items()}
        doc_len = {k: len(t) for k, t in doc_tokens.items()}
        n_docs = len(docs)
        avgdl = (sum(doc_len.values()) / n_docs) if n_docs else 0.0
        doc_term_sets = {k: set(t) for k, t in doc_tokens.items()}
        q_unique = set(q_weight)
        df = {term: sum(1 for s in doc_term_sets.values() if term in s) for term in q_unique}
        idf = {term: math.log(1.0 + (n_docs - df[term] + 0.5) / (df[term] + 0.5)) for term in q_unique}
        K1, B = memory_index.BM25_K1, memory_index.BM25_B
        scores = {}
        for key, toks in doc_tokens.items():
            if not toks:
                continue
            tf = {}
            for t in toks:
                if t in q_unique:
                    tf[t] = tf.get(t, 0) + 1
            if not tf:
                continue
            dl = doc_len[key]
            score = 0.0
            for term, f in tf.items():
                denom = f + K1 * (1.0 - B + B * (dl / avgdl if avgdl else 1.0))
                score += q_weight[term] * idf[term] * (f * (K1 + 1.0)) / denom
            if score > 0:
                scores[key] = score
        return scores

    @pytest.mark.parametrize("query,docs", [
        ("recall latency slow", {"a": "recall latency is slow on session start",
                                 "b": "latency unrelated doc about pipelines"}),
        ("配置文件", {"a": "配置文件 很重要 的 recall", "b": "竞品分析 与 配置 无关"}),
        ("mixed 中英 query", {"a": "mixed 中英 content here", "b": "纯中文内容文档",
                               "c": "pure english content"}),
        ("", {"a": "empty query returns nothing"}),            # empty query edge
        ("nohits", {}),                                        # empty corpus edge
        ("用goal-pipeline跑", {"a": "用goal-pipeline跑 一次", "b": "goal pipeline run once"}),
    ])
    def test_cached_matches_uncached(self, query, docs):
        memory_index._bm25_tokenize_doc.cache_clear() if hasattr(
            memory_index._bm25_tokenize_doc, "cache_clear") else None
        memory_index._reset_doc_token_cache()
        got = memory_index._bm25_scores(query, docs)
        want = self._uncached_scores(query, docs)
        assert got == want, f"cache changed scores for query={query!r}"

    def test_second_call_identical(self):
        """A warm (cache-hit) call returns the same scores as the cold call."""
        memory_index._reset_doc_token_cache()
        docs = {"a": "recall latency slow 配置文件", "b": "unrelated 竞品分析"}
        q = "recall 配置文件"
        cold = memory_index._bm25_scores(q, docs)
        warm = memory_index._bm25_scores(q, docs)   # same docs → cache hit
        assert cold == warm

    def test_second_call_does_zero_doc_tokenization(self, monkeypatch):
        """AC1 as a committed regression guard: the win is a cost DROP, so assert
        it directly — a warm `_bm25_scores` on the SAME corpus must incur ZERO
        underlying `_bm25_tokenize` doc-tokenizations (100% drop, well past the
        ≥70% bar). Counts the real tokenizer's invocations rather than measuring
        wall-clock (deterministic, non-flaky). Mutation-sound: if `_bm25_scores`
        were reverted to call the bare `_bm25_tokenize` for docs, the warm count
        would equal the cold count and this test goes RED.

        Note: the QUERY is tokenized every call (AC4 — uncached), by
        `_bm25_query_weights`, which does not go through `_bm25_tokenize`; so every
        counted call is a DOC tokenization."""
        memory_index._reset_doc_token_cache()
        docs = {f"d{i}": f"doc body number {i} 配置文件 recall" for i in range(8)}
        q = "recall 配置文件 latency"

        calls = {"n": 0}
        real = memory_index._bm25_tokenize

        def counting(text):
            calls["n"] += 1
            return real(text)

        monkeypatch.setattr(memory_index, "_bm25_tokenize", counting)
        # Cold: 8 doc tokenizations.
        calls["n"] = 0
        memory_index._bm25_scores(q, docs)
        cold_calls = calls["n"]
        # Warm (same docs → all 8 doc keys hit cache): none.
        calls["n"] = 0
        memory_index._bm25_scores(q, docs)
        warm_calls = calls["n"]

        cold_doc = cold_calls
        warm_doc = warm_calls
        assert cold_doc == len(docs), f"cold should tokenize all {len(docs)} docs, got {cold_doc}"
        assert warm_doc == 0, f"warm should tokenize ZERO docs (all cached), got {warm_doc}"
        # Drop in doc-tokenization cost = 100% ≥ 70% (AC1).


# ─────────────────────────────────────────────────────────────────────────────
# AC2/AC3 — the helper itself: identity vs _bm25_tokenize, content-invalidation
# ─────────────────────────────────────────────────────────────────────────────
class TestDocTokenizeHelper:
    def test_helper_equals_bare_tokenize(self):
        """`_bm25_tokenize_doc(text)` returns EXACTLY `_bm25_tokenize(text)` —
        it is a cache over the same function, never a different tokenizer."""
        memory_index._reset_doc_token_cache()
        for text in ["配置文件", "竞品分析", "recall engine bm25", "用goal-pipeline跑", ""]:
            assert memory_index._bm25_tokenize_doc(text) == memory_index._bm25_tokenize(text)

    def test_cache_hit_returns_same_value(self):
        memory_index._reset_doc_token_cache()
        a = memory_index._bm25_tokenize_doc("配置文件 recall")
        b = memory_index._bm25_tokenize_doc("配置文件 recall")
        assert a == b == ["配置", "置文", "文件", "recall"]

    def test_content_change_invalidates(self):
        """AC3: a different text content is NEVER served a prior text's tokens
        (content-keyed, so no stale recall)."""
        memory_index._reset_doc_token_cache()
        t1 = memory_index._bm25_tokenize_doc("abc def")
        t2 = memory_index._bm25_tokenize_doc("abc def ghi")   # content changed
        assert t1 == ["abc", "def"]
        assert t2 == ["abc", "def", "ghi"]
        assert t1 != t2

    def test_returned_list_mutation_does_not_corrupt_cache(self):
        """Gate-1 WARN (aliasing): even if a caller mutates the returned list,
        the NEXT call must still return correct tokens — the cache must not hand
        out a shared mutable list that a caller can corrupt."""
        memory_index._reset_doc_token_cache()
        first = memory_index._bm25_tokenize_doc("alpha beta gamma")
        first.append("CORRUPTED")                 # hostile caller mutates result
        second = memory_index._bm25_tokenize_doc("alpha beta gamma")
        assert second == ["alpha", "beta", "gamma"], "cache handed out a corrupted shared list"


# ─────────────────────────────────────────────────────────────────────────────
# AC5 — bounded LRU: evicts oldest, never changes a result
# ─────────────────────────────────────────────────────────────────────────────
class TestBoundedCache:
    def test_cache_is_bounded(self):
        memory_index._reset_doc_token_cache()
        cap = memory_index._DOC_TOKEN_CACHE_MAX
        for i in range(cap + 50):
            memory_index._bm25_tokenize_doc(f"unique doc number {i}")
        assert len(memory_index._DOC_TOKEN_CACHE) <= cap, "cache grew past its cap"

    def test_eviction_does_not_change_result(self):
        """An evicted entry recomputes correctly on next access (miss path),
        so eviction only costs a recompute — never a wrong answer."""
        memory_index._reset_doc_token_cache()
        cap = memory_index._DOC_TOKEN_CACHE_MAX
        first_text = "the very first evictable doc"
        expected = memory_index._bm25_tokenize(first_text)
        memory_index._bm25_tokenize_doc(first_text)
        # Flood past the cap so `first_text` is evicted (it is the oldest).
        for i in range(cap + 5):
            memory_index._bm25_tokenize_doc(f"flood doc {i}")
        # Re-access the evicted text — must recompute to the identical tokens.
        assert memory_index._bm25_tokenize_doc(first_text) == expected

    def test_saturation_warns_once_then_rearms(self, monkeypatch, caplog):
        """The thrash signal is observability, not a cost cap: WARN fires exactly
        ONCE per saturation episode (latched — never per-eviction spam), and
        re-arms after the cache drains back below cap. Mutation-sound: dropping
        the latch makes the first count > 1; dropping the re-arm kills the second
        warning."""
        monkeypatch.setattr(memory_index, "_DOC_TOKEN_CACHE_MAX", 5)
        memory_index._reset_doc_token_cache()
        with caplog.at_level(logging.WARNING, logger="core.memory_index"):
            # Flood past the small cap → enters saturation, warns once.
            for i in range(20):
                memory_index._bm25_tokenize_doc(f"episode-one doc {i}")
            first = [r for r in caplog.records if "saturated" in r.getMessage()]
            assert len(first) == 1, f"expected exactly 1 saturation warn, got {len(first)}"

            # Drain below cap WITHOUT resetting the latch flag, then insert one:
            # the miss-path re-arm branch must clear the latch (len < cap).
            memory_index._DOC_TOKEN_CACHE.clear()
            memory_index._bm25_tokenize_doc("re-arm trigger")  # len 1 < cap → re-arm
            # New saturation episode → a SECOND warning must fire.
            for i in range(20):
                memory_index._bm25_tokenize_doc(f"episode-two doc {i}")
            total = [r for r in caplog.records if "saturated" in r.getMessage()]
            assert len(total) == 2, f"re-arm failed: expected 2 warns across episodes, got {len(total)}"
        memory_index._reset_doc_token_cache()


# ─────────────────────────────────────────────────────────────────────────────
# Thread-safety — recall runs the cache from several io-pool / to_thread threads
# concurrently. The lock must prevent `move_to_end`/`popitem` races (KeyError into
# the recall path) and keep results correct under contention.
# ─────────────────────────────────────────────────────────────────────────────
class TestThreadSafety:
    def test_concurrent_access_no_crash_and_correct(self, monkeypatch):
        """Hammer the cache from 16 threads with a tiny cap so eviction churns
        constantly. Asserts the locked path stays crash-free AND result-correct
        under contention.

        HONEST SCOPE (not mutation-proven): removing the lock does NOT reliably
        make this RED, because under CPython the GIL already makes each individual
        OrderedDict C-method (get/move_to_end/popitem) atomic, so the cross-thread
        KeyError window is very narrow in practice. This is a concurrency SMOKE
        (exercises the lock path + checks correctness), not a race-regression guard.
        The lock is a defensive fix: it removes reliance on GIL-atomicity (which is
        an implementation detail, not a language contract — free-threaded/no-GIL
        CPython drops it), matches the recall_metrics._LOCK precedent, and closes
        the genuinely multi-bytecode flag race (`_doc_cache_saturated` double-set)."""
        import threading as _th

        monkeypatch.setattr(memory_index, "_DOC_TOKEN_CACHE_MAX", 8)
        memory_index._reset_doc_token_cache()

        # A pool of texts larger than the cap → constant eviction + re-insertion,
        # plus hot repeats to drive move_to_end concurrently with popitem.
        texts = [f"doc fragment {i} 配置文件 竞品分析" for i in range(40)]
        expected = {t: memory_index._bm25_tokenize(t) for t in texts}
        errors: list[BaseException] = []
        mismatches: list[str] = []
        barrier = _th.Barrier(16)

        def worker():
            try:
                barrier.wait()  # maximize simultaneous entry → real contention
                for _ in range(200):
                    for t in texts:
                        got = memory_index._bm25_tokenize_doc(t)
                        if got != expected[t]:
                            mismatches.append(t)
            except BaseException as e:  # noqa: BLE001 — capture ANY thread crash
                errors.append(e)

        threads = [_th.Thread(target=worker) for _ in range(16)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=30)

        assert not errors, f"cache raised under concurrency (lock missing/wrong?): {errors[:3]}"
        assert not mismatches, f"cache returned wrong tokens under contention: {mismatches[:3]}"
        memory_index._reset_doc_token_cache()


# ─────────────────────────────────────────────────────────────────────────────
# AC4 — query path stays UNCACHED / _bm25_tokenize contract unchanged
# ─────────────────────────────────────────────────────────────────────────────
class TestQueryPathUncachedAndContractIntact:
    def test_bm25_tokenize_contract_unchanged(self):
        """The public tokenizer that test_cjk_encoding.py pins is untouched."""
        assert memory_index._bm25_tokenize("配置文件") == ["配置", "置文", "文件"]
        assert memory_index._bm25_tokenize("竞品分析") == ["竞品", "品分", "分析"]
        assert memory_index._bm25_tokenize("recall engine bm25") == ["recall", "engine", "bm25"]

    def test_scores_uses_doc_cache_only_for_docs(self):
        """`_bm25_scores` must tokenize DOCS via the cached helper but the QUERY
        via the bare (uncached) tokenizer. We assert by call-routing: patch the
        cached helper to record its inputs; the query string must NEVER appear."""
        memory_index._reset_doc_token_cache()
        seen_doc_texts = []
        real = memory_index._bm25_tokenize_doc

        def spy(text):
            seen_doc_texts.append(text)
            return real(text)

        memory_index._bm25_tokenize_doc = spy
        try:
            memory_index._bm25_scores("THE_QUERY_STRING", {"d1": "doc one text", "d2": "doc two text"})
        finally:
            memory_index._bm25_tokenize_doc = real
        assert "THE_QUERY_STRING" not in seen_doc_texts, "query was routed through the doc cache"
        assert "doc one text" in seen_doc_texts and "doc two text" in seen_doc_texts
