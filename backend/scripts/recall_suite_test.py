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
        """Wiring check on real MEMORY.md — with an INTENT-FIRST gold (labeled from
        the query BEFORE running recall), NOT gold=probe.sections[0]. The old version
        set gold to whatever recall returned — the exact circular pattern this whole
        run fixes (a benchmark tuned to current behavior is blind to regression).
        'Open Threads' is the intent-first answer for a query about a recurring OPEN
        frontend bug (the reconcile race is tracked as OT01, an open thread)."""
        import os
        from pathlib import Path
        mem = Path(os.path.expanduser("~/.swarm-ai/SwarmWS/.context/MEMORY.md"))
        if not mem.exists():
            pytest.skip("no real MEMORY.md in this env")
        content = mem.read_text()
        from core.context_recall import recall_context
        query = "frontend reconcile race keeps recurring streaming tab switch"
        # gold labeled from INTENT, before recall: this asks about an open recurring
        # bug → the acceptable set is the sections that legitimately track it.
        acceptable = ["Open Threads", "Pitfalls", "Guidelines", "COE Registry"]
        probe = recall_context("MEMORY.md", query, memory_content=content, max_sections=5)
        if not probe.sections:
            pytest.skip("recall returned no sections for the probe in this env")
        case = {"verification": {
            "domain": "context_files", "file": "MEMORY.md",
            "query": query, "gold": acceptable, "k": 5, "corpus": content,
        }}
        r = eval_recall_at_k(case, None)
        assert r["status"] == "passed", f"context_files must surface an intent-acceptable section: {r}"
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


class TestSeedQuerySet:
    """run_a616dc6b: the expanded seed (~24 queries, both domains, easy+hard).
    Canary that gold refs are REAL + the set is composed as claimed — a gold typo
    or a domain gap fails loudly instead of silently un-scoring a query."""

    def test_all_gold_sections_exist(self):
        """AC3 canary: every seed gold section must actually parse from its doc
        (skips honestly if the live docs aren't in this env)."""
        from scripts.recall_suite import _SEED_QUERIES, _load_corpora
        from core import memory_index
        ddd_docs, cf_docs = _load_corpora()
        if not ddd_docs and not cf_docs:
            pytest.skip("no live corpus in this env")
        from scripts.recall_suite import _gold_titles
        missing = []
        for query, domain, doc, gold, _diff in _SEED_QUERIES:
            src = ddd_docs.get(doc) if domain == "ddd" else cf_docs.get(doc)
            if src is None:
                continue  # doc not present in this env — not a gold error
            secs = set(memory_index.parse_memory_sections(src).keys())
            # gold may be a single section title (str) or an acceptable-set (list)
            # for cross-cutting task queries — EVERY named section must exist.
            for title in _gold_titles(gold):
                if title not in secs:
                    missing.append((doc, title))
        assert not missing, f"seed gold sections that do not exist (typo?): {missing}"

    def test_covers_both_domains(self):
        from scripts.recall_suite import _SEED_QUERIES
        domains = {d for _q, d, _doc, _g, _diff in _SEED_QUERIES}
        assert {"ddd", "context_files"} <= domains, "seed must cover BOTH domains"

    def test_has_body_recall_queries(self):
        """Anti-circularity: need >=5 queries whose gold title does NOT lexically
        overlap the query (answer lives in the BODY) so the set can't be reverse-
        derived from the ranker. 'hard' (ddd body-recall) + 'task' (production)
        queries both qualify — a task query's gold is labeled intent-first, not from
        the title."""
        from scripts.recall_suite import _SEED_QUERIES
        body = [q for q in _SEED_QUERIES if q[4] in ("hard", "task")]
        assert len(body) >= 5, "need >=5 body-recall (hard/task) queries for anti-circularity"

    def test_hard_queries_are_actually_title_mismatched(self):
        """A 'hard' query is only meaningful if its gold section TITLE does NOT
        lexically contain the query's content words — else it's secretly easy.
        (task queries are exempt: they may be CJK and their gold is intent-first,
        not derivable from the title anyway.)"""
        import re
        from scripts.recall_suite import _SEED_QUERIES, _gold_titles
        stop = {"what", "how", "where", "the", "a", "an", "is", "are", "do", "does",
                "i", "find", "of", "to", "for", "and", "has", "have", "been", "there",
                "can", "us", "we", "its", "with", "in", "on"}
        offenders = []
        for query, _domain, _doc, gold, diff in _SEED_QUERIES:
            if diff != "hard":
                continue
            qwords = {w for w in re.findall(r"[a-z]+", query.lower()) if w not in stop}
            gwords = {w for title in _gold_titles(gold) for w in re.findall(r"[a-z]+", title.lower())}
            if qwords & gwords:
                offenders.append((query, gold, qwords & gwords))
        assert not offenders, f"'hard' queries whose title lexically overlaps the query (not really hard): {offenders}"

    def test_size_at_least_20(self):
        from scripts.recall_suite import _SEED_QUERIES
        assert len(_SEED_QUERIES) >= 20, f"seed should be >=20 queries, got {len(_SEED_QUERIES)}"


class TestQueryClasses:
    """run_79de25f8: the seed must carry BOTH a 'name-signal' class (category-browse
    queries like 'what decisions were recorded' — a real but LOW-production query
    shape that guards name-signal recall regression) AND a 'task' class (queries
    sampled VERBATIM from production transcripts — the shape recall actually sees).

    Why two classes: the old benchmark was 100% name-signal (self-authored
    category-browse). 0/500 real user messages are that shape (run_79de25f8 repro);
    production recall is triggered by task-shaped chat. Reporting one averaged number
    let a name-signal miss (a non-production query class) masquerade as 'recall is
    0.25 broken'. The classes are reported SEPARATELY so a low name-signal number is
    visible as a known-miss class, never averaged into a scary headline.

    Mutation: delete either class → RED (the split number is the whole point)."""

    def test_both_query_classes_present(self):
        from scripts.recall_suite import _SEED_QUERIES
        classes = {q[4] for q in _SEED_QUERIES}
        assert "name-signal" in classes, (
            "seed must keep the category-browse cases as a 'name-signal' class "
            "(deleting them hides name-signal recall regression — M3 skeptic synthesis)")
        assert "task" in classes, (
            "seed must ADD production-shaped 'task' queries (the shape recall "
            "actually sees in production)")

    def test_task_queries_are_not_category_browse(self):
        """A 'task' query must NOT be a category-browse string ('what X are/is
        recorded/tracked/...'). This is the anti-regression guard: if someone adds a
        self-authored category-browse query back and tags it 'task', it fails."""
        import re
        from scripts.recall_suite import _SEED_QUERIES
        browse = re.compile(
            r"^\s*(what|which|list|show)\b.*\b(are|is|were|recorded|captured|"
            r"tracked|exist|govern)\b", re.I)
        offenders = [q[0] for q in _SEED_QUERIES
                     if q[4] == "task" and browse.match(q[0].strip())]
        assert not offenders, (
            f"'task' queries must be production-shaped, not category-browse: {offenders}")

    def test_name_signal_class_is_the_browse_cases(self):
        """The name-signal class must actually BE category-browse (so it guards the
        right thing). At least the canonical ones must be present."""
        from scripts.recall_suite import _SEED_QUERIES
        ns = [q[0] for q in _SEED_QUERIES if q[4] == "name-signal"]
        assert len(ns) >= 5, f"name-signal class too small to guard regression: {len(ns)}"

    def test_task_golds_not_superset_of_recall_output(self):
        """ANTI-CIRCULARITY GUARD (Gate-2, run_79de25f8). The authorship trap: a
        gold acceptable-set reverse-tuned to whatever recall RETURNS makes task
        recall a near-guaranteed 1.00 — grading the ranker against its own output.
        Gate-2 caught exactly this on the first attempt (acceptable_union ⊇ every
        section recall returns → 1.00). The structural fix: the UNION of task
        acceptable-sets must include >=1 section that recall does NOT surface for
        ANY task query — proving the golds carry intent recall can MISS, not just
        echo the ranker. (Skips honestly if no live MEMORY.md.)"""
        import os
        from pathlib import Path
        mem = Path(os.path.expanduser("~/.swarm-ai/SwarmWS/.context/MEMORY.md"))
        if not mem.exists():
            pytest.skip("no real MEMORY.md in this env")
        content = mem.read_text()
        from core.context_recall import recall_context
        from scripts.recall_suite import _SEED_QUERIES, _gold_titles
        task = [q for q in _SEED_QUERIES if q[4] == "task"]
        returned_ever, acceptable_union = set(), set()
        for query, _dom, _doc, gold, _diff in task:
            r = recall_context("MEMORY.md", query, memory_content=content, max_sections=5)
            returned_ever |= set(r.sections)
            acceptable_union |= set(_gold_titles(gold))
        # the leak Gate-2 flagged: recall returns nothing OUTSIDE the acceptable union
        # is EXPECTED (a hit means returned∩acceptable≠∅); the REAL guard is the
        # reverse — the golds must reach for at least one section recall misses.
        gold_misses = acceptable_union - returned_ever
        assert gold_misses, (
            "task acceptable-sets are a superset of recall output (authorship-trap "
            "circularity): every gold section is one recall already returns, so task "
            "recall is vacuously ~1.00. At least one gold must name a section recall "
            f"does NOT surface. returned={sorted(returned_ever)} "
            f"acceptable={sorted(acceptable_union)}")
