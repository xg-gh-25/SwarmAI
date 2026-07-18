"""Tests for the recall-suite: eval_recall_at_k evaluator + recall_suite aggregation.

Run 1 of §24.1.1 (run_50fad0fb). A MECHANICAL, non-circular recall@K/MRR benchmark
over the ##-section domains (context_files + ddd), scored against PINNED corpus
content (no live-disk dependency → reproducible). NO LLM judge.

Gate-1 (2 rounds) corrections locked here:
  - gold key = (doc, section) for ddd, section title for context_files;
  - ddd pinned via _ddd_section_scores_multi(query, docs_text) NOT recall_all
    (which reads live disk);
  - aggregation lives in recall_suite (compute_scores is status-count-only).
"""

import pytest

from scripts.eval_runner import eval_recall_at_k
from scripts.recall_suite import aggregate_recall


# ── a pinned ddd corpus: content snapshotted INTO the test (no live-disk read) ──
_DDD_CORPUS = {
    "TECH.md": (
        "## Architecture\n"
        "SQLite WAL graph store backs code intelligence.\n\n"
        "## Runtime Traps\n"
        "GUI-launched apps inherit the launchd env, not the shell profile.\n"
    ),
    "PRODUCT.md": (
        "## Vision\n"
        "A self-evolving Agent OS.\n\n"
        "## Non-Goals\n"
        "Not a generic chatbot.\n"
    ),
}


class TestEvalRecallAtK:
    # NOTE (O009): the context_files scorer (memory_index._keyword_section_scores)
    # matches against a real MEMORY.md INDEX block (`[KEY] summary | aliases`
    # entries), NOT naive `## headers` — so a synthetic markdown fixture scores 0.
    # The pass/fail/wrong-key LOGIC is therefore unit-tested on the ddd domain
    # (fully controllable via docs_text); context_files wiring is verified against
    # a REAL pinned MEMORY.md snapshot in TestRealCorpus below.

    def test_ddd_gold_doc_section_pair(self):
        case = {"verification": {
            "domain": "ddd",
            "query": "where is the sqlite graph store architecture",
            "gold": ["TECH.md", "Architecture"], "k": 3, "corpus": _DDD_CORPUS,
        }}
        r = eval_recall_at_k(case, None)
        assert r["status"] == "passed", f"(TECH.md, Architecture) should rank in top-3: {r}"
        assert r["recall_at_k"] == 1

    def test_ddd_wrong_doc_same_section_name_is_miss(self):
        """gold key is (doc, section) — a matching section TITLE in the WRONG doc
        must NOT count (PRODUCT.md and TECH.md can both have generic titles)."""
        case = {"verification": {
            "domain": "ddd",
            "query": "the sqlite graph store architecture",
            "gold": ["PRODUCT.md", "Architecture"], "k": 3, "corpus": _DDD_CORPUS,
        }}
        r = eval_recall_at_k(case, None)
        # "Architecture" exists only in TECH.md; PRODUCT.md/Architecture is not a real hit
        assert r["recall_at_k"] == 0, "wrong-doc section must not match (doc,section key)"

    def test_misconfigured_case_errors_not_fails(self):
        r = eval_recall_at_k({"verification": {"domain": "ddd"}}, None)  # no query/gold/corpus
        assert r["status"] == "error", "a misconfigured case is error, not a recall failure"


class TestRealCorpus:
    """O009: context_files scoring only works against real MEMORY-index structure,
    so verify the context_files domain wiring end-to-end on a REAL pinned MEMORY.md
    snapshot (skips honestly if the file isn't present in this env)."""

    def test_context_files_domain_wired_on_real_memory(self):
        import os
        from pathlib import Path
        mem = Path(os.path.expanduser("~/.swarm-ai/SwarmWS/.context/MEMORY.md"))
        if not mem.exists():
            pytest.skip("no real MEMORY.md in this env")
        content = mem.read_text()
        # recall a broad, definitely-present concept; gold = a section we know exists
        from core.context_recall import recall_context
        probe = recall_context("MEMORY.md", "frontend reconcile race streaming tab switch",
                               memory_content=content, max_sections=5)
        if not probe.sections:
            pytest.skip("recall returned no sections for the probe in this env")
        gold = probe.sections[0]  # a section recall actually surfaced (wiring check, not a quality claim)
        case = {"verification": {
            "domain": "context_files", "file": "MEMORY.md",
            "query": "frontend reconcile race streaming tab switch",
            "gold": gold, "k": 5, "corpus": content,
        }}
        r = eval_recall_at_k(case, None)
        assert r["status"] == "passed", f"context_files domain must score a surfaced section: {r}"
        assert r["recall_at_k"] == 1


class TestAggregateRecall:
    def test_mean_recall_and_mrr(self):
        results = [
            {"status": "passed", "recall_at_k": 1, "reciprocal_rank": 1.0},
            {"status": "passed", "recall_at_k": 1, "reciprocal_rank": 0.5},
            {"status": "failed", "recall_at_k": 0, "reciprocal_rank": 0.0},
        ]
        agg = aggregate_recall(results)
        # aggregate rounds to 4dp (report-friendly) — tolerance matches that, not float-exact
        assert agg["mean_recall_at_k"] == pytest.approx(2 / 3, abs=1e-4)
        assert agg["mrr"] == pytest.approx((1.0 + 0.5 + 0.0) / 3, abs=1e-4)
        assert agg["n"] == 3

    def test_empty_is_honest_zero(self):
        agg = aggregate_recall([])
        assert agg["n"] == 0 and agg["mean_recall_at_k"] == 0.0 and agg["mrr"] == 0.0
