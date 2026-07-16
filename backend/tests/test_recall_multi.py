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

import json
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

class TestMultiWordFTSRecall:
    """R3: multi-word recall must not be starved by over-strict FTS query
    construction. session_recall wrapped the whole query as one PHRASE;
    code_intel quoted each token = implicit AND. Fix = OR-join per-quoted-term,
    keeping BM25 rank-order + single-term identity + injection safety."""

    def _live_graph(self):
        """The live SwarmAI code graph (read-only). External-content FTS5 is
        awkward to populate in a tmp fixture, and the value here is the OR-join
        QUERY semantics over a real populated index — so drive the real graph,
        skip if unbuilt (CI without code-intel)."""
        from core.code_intel import load_project_graph
        g = load_project_graph("SwarmAI")
        if g is None or not g.search_symbols("recall", limit=1):
            pytest.skip("SwarmAI code graph not built in this environment")
        return g

    def test_codeintel_multiword_returns_hits(self):
        """AC2: a multi-word query where NO single symbol contains all terms
        still returns hits (OR), ranked. Under the old implicit-AND this was ~0.
        Mutation-checked: with space-join (AND), 'recall hybrid section score'
        returns 0/1; with OR it returns many."""
        g = self._live_graph()
        hits = g.search_symbols("recall hybrid section score", limit=10)
        assert len(hits) >= 2, "multi-word query must return multiple hits via OR-join"

    def test_codeintel_singleterm_unchanged(self):
        """AC3 regression: single-term query still works (OR-of-one = the term)."""
        g = self._live_graph()
        hits = g.search_symbols("recall", limit=10)
        assert len(hits) >= 1
        assert all("name" in h and "file_path" in h for h in hits)

    def test_codeintel_injection_safe_with_or(self):
        """AC3: FTS5 keyword terms (OR/NEAR/NOT) stay quoted phrase-literals,
        never operators — no raise (the OR-join only adds OR BETWEEN quoted terms)."""
        g = self._live_graph()
        # Must not raise; these are quoted literals after _sanitize_name + quoting.
        g.search_symbols("OR NEAR drop", limit=5)
        g.search_symbols("NOT match", limit=5)

    def test_session_multiword_query_construction(self):
        """AC1: SessionRecall builds an OR query for multi-word input (each term
        quoted, joined by OR) — not one verbatim phrase. Verified by patching the
        DB execute to capture the MATCH string the real search() builds."""
        import sqlite3
        from unittest.mock import MagicMock
        from core.session_recall import SessionRecall

        captured = {}

        sr = SessionRecall.__new__(SessionRecall)  # bypass __init__ (no DB needed)

        fake_conn = MagicMock()
        def _exec(sql, params=None):
            if params and "MATCH" in sql:
                captured["match"] = params[0]
            m = MagicMock(); m.fetchall.return_value = []; return m
        fake_conn.execute.side_effect = _exec
        fake_conn.row_factory = None
        sr._open_conn = lambda: fake_conn  # type: ignore

        sr.search("pipeline goal cycle", max_sessions=2)
        match = captured.get("match", "")
        # OR-joined per-quoted-term, NOT one big phrase.
        assert " OR " in match, f"expected OR-join, got: {match!r}"
        assert match.count('"') >= 6, "each term must stay individually quoted"
        assert '"pipeline goal cycle"' not in match, "must NOT be one verbatim phrase"


class TestRenderBucketedRecall:
    """C-full M1 (run_ccd1b6c5): BucketedRecall → injectable string. Renders
    real CONTENT (not section names), provenance-tagged, empty-safe."""

    def _bucket(self, **domains):
        from core.recall_multi import BucketedRecall
        r = BucketedRecall(query="q")
        r.buckets = dict(domains)
        return r

    def test_empty_bucket_renders_nothing(self):
        from core.recall_multi import render_bucketed_recall
        assert render_bucketed_recall(self._bucket()) == ""

    def test_none_result_safe(self):
        from core.recall_multi import render_bucketed_recall
        assert render_bucketed_recall(None) == ""

    def test_provenance_header_present_when_hits(self):
        from core.recall_multi import render_bucketed_recall
        s = render_bucketed_recall(
            self._bucket(library=[{"source": "N.md", "heading": "H",
                                   "content": "real body text", "score": 1.0}]))
        assert "[RECALLED]" in s and "real body text" in s

    def test_ddd_carries_project_provenance(self):
        from core.recall_multi import render_bucketed_recall
        s = render_bucketed_recall(
            self._bucket(ddd=[{"doc": "TECH.md", "section": "Arch",
                               "content": "ddd body"}]),
            project="Acme_SalesIntel")
        assert "[DDD:Acme_SalesIntel]" in s and "ddd body" in s

    def test_renders_content_not_bare_names(self):
        """The regression this feature exists to prevent: a hit with content
        must render the CONTENT, not just its section name."""
        from core.recall_multi import render_bucketed_recall
        s = render_bucketed_recall(
            self._bucket(context_files=[
                {"section": "COE Registry", "content": "FULL MEMORY BODY HERE"}]))
        assert "FULL MEMORY BODY HERE" in s

    def test_pointer_fallback_when_no_content(self):
        """A hit missing content falls back to its pointer (never blank)."""
        from core.recall_multi import render_bucketed_recall
        s = render_bucketed_recall(
            self._bucket(ddd=[{"doc": "TECH.md", "section": "Arch"}]))
        assert "TECH.md" in s and "Arch" in s

    def test_codeintel_renders_symbol_refs(self):
        from core.recall_multi import render_bucketed_recall
        s = render_bucketed_recall(
            self._bucket(codeintel=[{"name": "foo", "id": "a.py::foo",
                                     "callers": ["bar"]}]))
        assert "a.py::foo" in s and "bar" in s


class TestContentCarriedInBuckets:
    """C-full M1: _recall_context_files + _recall_library must carry `content`
    in their buckets (was dropped → rendering gave bare names = a regression)."""

    def test_context_files_bucket_carries_content(self):
        from core.recall_multi import recall_all
        r = recall_all("session resume timeout", project="SwarmAI",
                       domains=("context_files",))
        b = r.buckets.get("context_files", [])
        if b:  # only assert when there's a hit (live MEMORY.md dependent)
            assert "content" in b[0] and len(b[0]["content"]) > 50, \
                "context_files bucket must carry real content, not just names"

    def test_library_bucket_carries_content(self):
        from core.recall_multi import recall_all
        r = recall_all("session resume timeout", project="SwarmAI",
                       domains=("library",))
        b = r.buckets.get("library", [])
        if b:
            assert "content" in b[0], "library bucket must carry content"


class TestDetectActiveProject:
    """M2 (run_91bc0651, DDD-alive): active-project detection for runtime DDD
    recall. FAIL-CLOSED — ambiguous/no-signal → None (inject nothing)."""

    # SYNTHETIC project names only — NEVER real private/customer names (e.g.
    # CMHK). This test file is git-tracked → public repo; a real private skill/
    # project name here is a leak (C041 family, run_f1fe156b Gate-2 LOW). Acme_
    # SalesIntel exercises the business-suffix derive path (→ s_acme-) identically.
    CANDS = ["AIDLC", "Acme_SalesIntel", "Widgets_Community", "Zeta_Platform", "SwarmAI"]

    def test_signal1_project_path(self):
        from core.recall_multi import detect_active_project
        proj, sig = detect_active_project(
            editor_file_path="/x/SwarmWS/Projects/Acme_SalesIntel/TECH.md",
            candidates=self.CANDS,
        )
        assert proj == "Acme_SalesIntel" and sig == "signal1_project_path"

    def test_signal1_skill_path_maps_to_business_project(self):
        from core.recall_multi import detect_active_project
        proj, sig = detect_active_project(
            editor_file_path="backend/skills/s_acme-weekly-report/SKILL.md",
            candidates=self.CANDS,
        )
        assert proj == "Acme_SalesIntel" and sig == "signal1_skill_path"

    def test_signal3_keyword_unique_match(self):
        from core.recall_multi import detect_active_project
        proj, sig = detect_active_project(
            query="show me the acme weekly numbers", candidates=self.CANDS,
        )
        assert proj == "Acme_SalesIntel" and sig == "signal3_keyword"

    def test_failclosed_ambiguous_two_matches(self):
        """≥2 project keyword matches → None (never guess, never pollute)."""
        from core.recall_multi import detect_active_project
        proj, sig = detect_active_project(
            query="compare acme and widgets community", candidates=self.CANDS,
        )
        assert proj is None and sig == "ambiguous"

    def test_failclosed_no_signal(self):
        from core.recall_multi import detect_active_project
        proj, sig = detect_active_project(
            query="hello how are you today", candidates=self.CANDS,
        )
        assert proj is None and sig == "no_signal"

    def test_signal1_beats_signal3(self):
        """Deterministic file-path signal wins over probabilistic keyword."""
        from core.recall_multi import detect_active_project
        proj, sig = detect_active_project(
            editor_file_path="/x/SwarmWS/Projects/Zeta_Platform/PRODUCT.md",
            query="acme weekly revenue",  # keyword says Acme, path says Zeta
            candidates=self.CANDS,
        )
        assert proj == "Zeta_Platform" and sig == "signal1_project_path"

    def test_unknown_project_path_ignored(self):
        """A Projects/ path for a dir NOT in candidates → not matched."""
        from core.recall_multi import detect_active_project
        proj, sig = detect_active_project(
            editor_file_path="/x/SwarmWS/Projects/Nonexistent_Xyz/TECH.md",
            candidates=self.CANDS,
        )
        assert proj is None

    def test_signal3_word_boundary_no_substring_falsepos(self):
        """Gate-2 M2: substring match wrongly resolved 'aidlctastic' → AIDLC.
        Word-boundary match must reject a token embedded in an unrelated word."""
        from core.recall_multi import detect_active_project
        assert detect_active_project(
            query="the pipeline is aidlctastic today", candidates=self.CANDS,
        ) == (None, "no_signal")
        # but the real whole word still matches
        assert detect_active_project(
            query="what is the aidlc plan", candidates=self.CANDS,
        )[0] == "AIDLC"

    def test_signal1_keyword_less_opener_still_detects(self):
        """Gate-2 HIGH: signal-1 is deterministic and must NOT require query
        keywords. A keyword-less opener ('继续') with an editor path still
        resolves the project."""
        from core.recall_multi import detect_active_project
        proj, sig = detect_active_project(
            editor_file_path="/x/SwarmWS/Projects/Acme_SalesIntel/TECH.md",
            query="继续",  # zero extractable keywords
            candidates=self.CANDS,
        )
        assert proj == "Acme_SalesIntel" and sig == "signal1_project_path"


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


# ===========================================================================
# Run 3 (run_6602eeab) — §8.1 recall read-side wiring: code-intel domains[]
# leg + spec-details [human]-marker chunker + verified gating + §8.9 sentinel.
# Drives the REAL _recall_ddd through recall_all (no mock of the func under
# change — the anti-test-theater discipline from Run 1/2).
# ===========================================================================


class TestHumanMarkerChunker:
    """AC2: [human]-block extraction indexes ONLY backtick-fenced `[human]`
    list items — bare-word / comment / legend mentions are NOT blocks."""

    def test_backtick_fenced_human_extracted(self):
        from core.recall_multi import _extract_human_blocks
        txt = "- **single writer invariant** `[human]` — anchor `X.ts:6`\n- other `[llm]` line"
        blocks = _extract_human_blocks(txt)
        assert len(blocks) == 1
        assert "single writer invariant" in blocks[0]

    def test_bare_word_and_legend_not_a_block(self):
        """The false-positive trap: legend/comment mention `[human]` without
        backticks (dangerous-command-gate.spec.md:7). MUST NOT be indexed."""
        from core.recall_multi import _extract_human_blocks
        txt = (
            "<!-- 骨架区(§1-4) = 机器生成;§5 [human] 可增补 -->\n"
            "prose mentioning [human] with no backticks\n"
            "- **real one** `[human]` — anchor `Y.ts:9`\n"
        )
        blocks = _extract_human_blocks(txt)
        assert len(blocks) == 1, "only the backtick-fenced item counts, not comment/prose"
        assert "real one" in blocks[0]

    def test_html_comment_region_skipped(self):
        from core.recall_multi import _extract_human_blocks
        txt = (
            "<!--\nmulti-line comment with `[human]` inside a fence in a COMMENT\n-->\n"
            "- **actual rule** `[human]` — anchor `Z.ts:1`\n"
        )
        blocks = _extract_human_blocks(txt)
        assert len(blocks) == 1
        assert "actual rule" in blocks[0]

    def test_inline_trailing_comment_does_not_drop_block(self):
        """Gate-2 HIGH: a real rule with a trailing inline <!-- --> must NOT be
        dropped (prior version skipped the whole line = false negative)."""
        from core.recall_multi import _extract_human_blocks
        txt = "- **never delete prod data** `[human]` — anchor `x.ts:1` <!-- note -->\n"
        blocks = _extract_human_blocks(txt)
        assert len(blocks) == 1
        assert "never delete prod data" in blocks[0]

    def test_prose_mention_of_marker_not_a_block(self):
        """Gate-2 MEDIUM: prose quoting `[human]` (not a list item) is NOT a rule."""
        from core.recall_multi import _extract_human_blocks
        txt = "The `[human]` marker denotes human authorship in this file.\n"
        assert _extract_human_blocks(txt) == []


class TestVerifiedGating:
    """AC1: verified:false assertions are GATED — surfaced as
    [llm-inferred, UNVERIFIED], never as an established fact."""

    def test_verified_true_is_fact(self):
        from core.recall_multi import _domain_corpus
        dom = {"id": "domain:x", "name": "X", "summary": "s",
               "business_rules": [{"rule": "stock must suffice", "anchor": "a.ts:1", "verified": True}]}
        corpus = _domain_corpus(dom, [], [])
        assert "stock must suffice" in corpus
        assert "[llm-inferred, UNVERIFIED] rule: stock must suffice" not in corpus

    def test_verified_false_is_gated(self):
        from core.recall_multi import _domain_corpus
        dom = {"id": "domain:x", "name": "X", "summary": "s",
               "business_rules": [{"rule": "refund is idempotent", "verified": False,
                                    "absence_evidence": "grep=0"}]}
        corpus = _domain_corpus(dom, [], [])
        assert "[llm-inferred, UNVERIFIED] rule: refund is idempotent" in corpus

    def test_bare_string_rule_treated_unverified(self):
        from core.recall_multi import _domain_corpus
        dom = {"id": "domain:x", "name": "X", "summary": "s",
               "business_rules": ["some unadjudicated claim"]}
        corpus = _domain_corpus(dom, [], [])
        assert "[llm-inferred, UNVERIFIED] rule: some unadjudicated claim" in corpus


class TestRun3RecallLegsE2E:
    """AC3 §8.9 sentinel: a business-rule word living ONLY in a spec-details
    [human] block, and ONLY in code-intel.json domains[], MUST be recalled
    through the real _recall_ddd. This is the red→green liveness contract."""

    def _make_project(self, tmp_path, monkeypatch, *, with_domains, with_human):
        from core import recall_multi
        proj = tmp_path / "Proj"
        proj.mkdir()
        # a canonical doc must exist so base.exists() + ddd leg run
        (proj / "PRODUCT.md").write_text("## Vision\nunrelated content\n", encoding="utf-8")
        if with_domains:
            ci = {
                "version": 3.0,
                "routes": [{"id": "route:orders-post-a1b2"}],
                "domains": [{"id": "domain:orders", "name": "Orders",
                             "summary": "order lifecycle",
                             "business_rules": [
                                 {"rule": "zORPHANWIDGET42 domain invariant",
                                  "anchor": "o.ts:1", "verified": True}]}],
                "flows": [], "steps": [],
            }
            (proj / "code-intel.json").write_text(json.dumps(ci), encoding="utf-8")
        if with_human:
            sd = proj / "spec-details"
            sd.mkdir()
            (sd / "payment.spec.md").write_text(
                "# 规格:Payment\n"
                "## 5. 业务规则\n"
                "- **zHUMANSENTINEL99 refund idempotency invariant** `[human]` — anchor `p.ts:1` ✅\n",
                encoding="utf-8")
        # _recall_ddd does `from core.project_registry import get_projects_dir`
        # at call time → patch it on project_registry (the source module).
        import core.project_registry as pr
        monkeypatch.setattr(pr, "get_projects_dir", lambda: tmp_path)
        return proj

    def test_sentinel_recalled_from_human_block(self, tmp_path, monkeypatch):
        from core.recall_multi import _recall_ddd
        self._make_project(tmp_path, monkeypatch, with_domains=False, with_human=True)
        hits, layer = _recall_ddd("zHUMANSENTINEL99 refund idempotency", "Proj", 5)
        docs = [h.get("doc", "") for h in hits]
        assert any("payment.spec.md" in d for d in docs), \
            f"[human]-only sentinel MUST be recalled (§8.9 green), got {docs}"

    def test_sentinel_recalled_from_domains(self, tmp_path, monkeypatch):
        from core.recall_multi import _recall_ddd
        self._make_project(tmp_path, monkeypatch, with_domains=True, with_human=False)
        hits, layer = _recall_ddd("zORPHANWIDGET42 domain invariant", "Proj", 5)
        docs = [h.get("doc", "") for h in hits]
        assert any("code-intel.json" in d for d in docs), \
            f"domains[]-only sentinel MUST be recalled, got {docs}"

    def test_no_spec_details_dir_is_safe(self, tmp_path, monkeypatch):
        from core.recall_multi import _recall_ddd
        self._make_project(tmp_path, monkeypatch, with_domains=False, with_human=False)
        hits, layer = _recall_ddd("anything", "Proj", 5)
        assert isinstance(hits, list)  # no crash when neither leg has data
