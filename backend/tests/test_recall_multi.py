"""Tests for the multi-domain READ-recall closure (run_4358cc95, goal B1).

Covers the 5 DoD criteria:
- AC5 (NEGATIVE/anti-scope): recall with allow_embed=False triggers NO Bedrock
  embed — the hardcoded EmbeddingClient.embed_text in _hybrid_section_scores must
  be suppressible by the caller. This is the Gate-1 blocker fix.
- AC1: generic ##-section keyword scorer drills DDD docs (no MEMORY-keyed index).
- AC2: _codeintel_recall buckets load_project_graph search results.
- AC3: recall_all fans 5 domains into ONE BucketedRecall.
- AC4: per-domain buckets + hit_layer exposed.

READ-only: only the Bedrock network boundary is ever patched; no embed/write.
"""

import sqlite3
import pytest


# ===========================================================================
# Cycle 1 — AC5: allow_embed=False provably suppresses the Bedrock embed
# ===========================================================================

_MEMORY_FIXTURE = """\
<!-- MEMORY_INDEX_START -->
## Memory Index
- [COE01] exit code -9 cascading SIGKILL | sigkill, oom
<!-- MEMORY_INDEX_END -->

## COE Registry
- 2026-03-17: **Sev-1 exit -9 SIGKILL** — OOM kills.
"""


class TestAntiScopeNoEmbed:
    """AC5 (the negative test): a keyword-MISS must NOT fall through to a
    Bedrock embed when the caller passes allow_embed=False."""

    def test_keyword_miss_with_allow_embed_false_does_not_embed(self, monkeypatch):
        """The core anti-scope guard: patch the embed boundary, force a keyword
        miss, assert embed_text is NEVER called when allow_embed=False."""
        from core import memory_index
        from core.context_recall import recall_context

        calls = {"n": 0}

        class _SpyEmbedder:
            def embed_text(self, text):  # noqa: ARG002
                calls["n"] += 1
                return None

        # If anything constructs/uses the embed client, this spy counts it.
        monkeypatch.setattr(memory_index, "_embedding_client_cache", _SpyEmbedder())

        # Query with zero keyword overlap → guaranteed keyword miss → would
        # normally escalate to _hybrid_section_scores (which embeds).
        res = recall_context(
            "MEMORY.md", "zzz totally unrelated qqq xyzzy",
            memory_content=_MEMORY_FIXTURE, allow_embed=False,
        )
        assert res.allowed is True
        assert calls["n"] == 0, "allow_embed=False must NOT trigger a Bedrock embed"
        assert res.hit_layer in ("keyword", "none")  # never "hybrid"

    def test_hybrid_section_scores_allow_embed_false_returns_empty(self, monkeypatch):
        """_hybrid_section_scores(allow_embed=False) returns {} without embedding,
        even when a vec DB with data exists."""
        from core import memory_index

        spy = {"n": 0}

        class _SpyEmbedder:
            def embed_text(self, text):  # noqa: ARG002
                spy["n"] += 1
                return [0.0] * 1024

        monkeypatch.setattr(memory_index, "_embedding_client_cache", _SpyEmbedder())

        out = memory_index._hybrid_section_scores("anything", allow_embed=False)
        assert out == {}
        assert spy["n"] == 0, "allow_embed=False must short-circuit before embed_text"

    def test_default_allow_embed_true_preserves_existing_behavior(self):
        """Regression guard: recall_context default (allow_embed=True) keeps the
        keyword-first behavior unchanged for a keyword HIT (no embed needed)."""
        from core.context_recall import recall_context

        res = recall_context(
            "MEMORY.md", "exit code sigkill oom",
            memory_content=_MEMORY_FIXTURE,
        )
        assert res.allowed is True
        assert res.hit_layer == "keyword"
        assert "COE Registry" in res.sections


# ===========================================================================
# Cycle 2 — AC1: generic ##-section scorer drills DDD docs
# ===========================================================================

_DDD_FIXTURE = """\
# TECH.md

## Architecture
The session spine has 7 components: router, unit, orchestrator.

## Runtime Traps
Subprocess spawn must be serialized via _spawn_lock to avoid races.

## Conventions
Use snake_case for Python, camelCase for TypeScript.
"""


class TestDDDGenericScorer:
    """AC1: a generic ##-section keyword scorer (NOT the MEMORY-keyed index
    scorer) drills DDD docs by query-term overlap on section text."""

    def test_ddd_section_drill_finds_relevant_section(self):
        """A query about subprocess locking drills the 'Runtime Traps' section."""
        from core.recall_multi import _ddd_section_scores

        scores = _ddd_section_scores("subprocess spawn lock race", _DDD_FIXTURE)
        assert "Runtime Traps" in scores
        # Runtime Traps should outrank Conventions for this query.
        assert scores["Runtime Traps"] > scores.get("Conventions", 0.0)

    def test_ddd_scorer_no_embed(self, monkeypatch):
        """AC5 extends to DDD: the generic scorer is pure keyword, never embeds."""
        from core import memory_index

        spy = {"n": 0}

        class _SpyEmbedder:
            def embed_text(self, text):  # noqa: ARG002
                spy["n"] += 1
                return None

        monkeypatch.setattr(memory_index, "_embedding_client_cache", _SpyEmbedder())
        from core.recall_multi import _ddd_section_scores
        _ddd_section_scores("anything at all", _DDD_FIXTURE)
        assert spy["n"] == 0


# ===========================================================================
# Cycle 3 — AC2: CodeIntel recall verb buckets graph results
# ===========================================================================

class TestCodeIntelRecall:
    """AC2: _codeintel_recall wraps load_project_graph().search_symbols +
    find_callers into a bucket. Unavailable project → empty bucket, not crash."""

    def test_codeintel_unavailable_returns_empty_bucket(self, monkeypatch):
        """When the project has no code graph, return an empty bucket (None-safe),
        NOT an auto-created empty DB."""
        from core import recall_multi

        monkeypatch.setattr(recall_multi, "load_project_graph", lambda p: None)
        bucket = recall_multi._codeintel_recall("session spawn", project="SwarmAI")
        assert bucket == []  # empty, no crash

    def test_codeintel_buckets_symbol_hits(self, monkeypatch):
        """A live graph returns symbol hits in the code-intel bucket shape."""
        from core import recall_multi

        class _FakeGraph:
            def search_symbols(self, query, limit=20):
                return [{"name": "spawn_session", "id": "n1",
                         "file_path": "core/session_unit.py", "rank": -1.5}]

            def find_callers(self, node_id, depth=1):
                return [("session_router.py::route", 1)]

        monkeypatch.setattr(recall_multi, "load_project_graph", lambda p: _FakeGraph())
        bucket = recall_multi._codeintel_recall("spawn", project="SwarmAI")
        assert len(bucket) >= 1
        assert bucket[0]["name"] == "spawn_session"
        assert "file_path" in bucket[0]


# ===========================================================================
# Cycle 4 — AC3+AC4: recall_all fans 5 domains into one BucketedRecall
# ===========================================================================

class TestRecallAll:
    """AC3/AC4: recall_all returns a BucketedRecall with per-domain buckets +
    per-domain hit_layer, fanning across all 5 domains, embed-free by default."""

    def test_recall_all_returns_bucketed_shape(self, monkeypatch):
        """recall_all produces a BucketedRecall with the expected domain keys."""
        from core import recall_multi

        # Stub the heavy domain readers to isolate the fan-out/bucketing logic.
        monkeypatch.setattr(recall_multi, "_codeintel_recall", lambda q, project=None: [])
        result = recall_multi.recall_all(
            "exit code sigkill", project="SwarmAI", allow_embed=False,
        )
        # AC4: per-domain buckets present.
        assert hasattr(result, "buckets")
        for domain in ("context_files", "ddd", "library", "session", "codeintel"):
            assert domain in result.buckets, f"missing domain bucket: {domain}"
        # AC4: per-domain hit_layer.
        assert hasattr(result, "hit_layers")

    def test_policy_excluded_context_files_returns_empty(self, monkeypatch):
        """PRIVACY (Gate-2 leak fix): when MEMORY.md is policy-excluded for the
        session (e.g. group_channel), the context_files bucket must be EMPTY —
        multi-domain recall must enforce the SAME gate as single-file recall.
        Without the fix, --domains leaked MEMORY to sessions --file denies."""
        from core import recall_multi

        monkeypatch.setattr(recall_multi, "_codeintel_recall", lambda q, project=None: [])
        # MEMORY.md excluded by policy → context_files bucket must be empty.
        result = recall_multi.recall_all(
            "exit code sigkill oom", project="SwarmAI", allow_embed=False,
            domains=("context_files",),
            policy_excluded_files=frozenset({"memory.md"}),
        )
        assert result.buckets["context_files"] == [], \
            "policy-excluded MEMORY must NOT leak through multi-domain recall"
        assert result.hit_layers["context_files"] == "none"

    def test_no_exclusion_allows_context_files(self, monkeypatch):
        """Counterpart: with NO exclusion, context_files recall works normally
        (proves the empty result above is the GATE, not an unrelated break)."""
        from core import recall_multi

        monkeypatch.setattr(recall_multi, "_codeintel_recall", lambda q, project=None: [])
        result = recall_multi.recall_all(
            "exit code sigkill oom", project="SwarmAI", allow_embed=False,
            domains=("context_files",),
            policy_excluded_files=frozenset(),  # nothing excluded
        )
        # Live MEMORY.md has a COE Registry section matching this query.
        assert result.buckets["context_files"], \
            "with no exclusion, context_files should return hits (gate is the discriminator)"

    def test_recall_all_embed_free_by_default(self, monkeypatch):
        """AC5 at the fan-out level: recall_all(allow_embed=False) never embeds."""
        from core import memory_index, recall_multi

        spy = {"n": 0}

        class _SpyEmbedder:
            def embed_text(self, text):  # noqa: ARG002
                spy["n"] += 1
                return None

        monkeypatch.setattr(memory_index, "_embedding_client_cache", _SpyEmbedder())
        monkeypatch.setattr(recall_multi, "_codeintel_recall", lambda q, project=None: [])
        recall_multi.recall_all("zzz unrelated qqq", project="SwarmAI", allow_embed=False)
        assert spy["n"] == 0, "recall_all default must be embed-free (AC5)"
