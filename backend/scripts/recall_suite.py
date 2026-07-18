"""recall-suite — a mechanical recall@K / MRR benchmark over the recall READ path.

Run 1 of the §24.1.1 benchmark subsystem (run_50fad0fb). Answers, with a NUMBER,
the question that has had no answer since the vector leg was removed
(pure-filesystem keyword/FTS5, MOD03): **how good is recall now?**

Design (Gate-1 x2, all source-verified):
- **Mechanical, non-circular:** recall@K = "did the gold section appear in the
  top-K of the recall result?" — a deterministic rank check, NO LLM judge, so it
  cannot be gamed by answer phrasing (unlike judge scoring).
- **Scoped to the ##-section domains** (context_files + ddd) where a stable
  section key exists. session (one text[:500] blob) + codeintel (symbol ids) have
  no section key and are DEFERRED to a follow-up run (documented, not dropped).
  This is also exactly the domain the removed vector leg touched (only
  context_files ever had a vector path — recall_multi.py:278-279), so a
  context_files+ddd recall@K meaningfully answers "did removing the vector leg
  hurt recall?".
- **PINNED corpus:** each case snapshots the queried content, so the score is
  reproducible (a live MEMORY.md edit can't move it). context_files pins via
  recall_context(memory_content=...); ddd pins via _ddd_section_scores_multi /
  _ddd_entry_hits (they take docs_text) — NOT recall_all, which reads live disk.
- **gold key** = section TITLE (context_files) or (doc, section) pair (ddd —
  PRODUCT.md and TECH.md can both have an "Architecture" section).

Aggregation lives HERE (mean recall@K + MRR), NOT in eval_runner.compute_scores,
which is status-count-only and would discard the metric.

⚠️ Honor-checkpoint (unenforced by the gate): gold sections must be chosen
INTENT-FIRST — "what a user asking this query SHOULD get" — never by observing
what recall currently returns (that would tune the set to current behavior and
make it blind to regression).
"""

from __future__ import annotations

from typing import Optional


def _rank_of_gold_context_files(query: str, corpus: str, gold: str, k: int) -> int:
    """Return the 1-based rank of the gold section title among recall_context's
    top-K sections, or 0 if absent. Pinned: parses the passed-in corpus, no disk."""
    from core.context_recall import recall_context
    res = recall_context("MEMORY.md", query, memory_content=corpus, max_sections=k)
    if not res.allowed:
        return 0
    for i, sec in enumerate(res.sections[:k], start=1):
        if sec == gold:
            return i
    return 0


def _rank_of_gold_ddd(query: str, corpus: dict, gold_doc: str, gold_section: str, k: int) -> int:
    """Return the 1-based rank of (gold_doc, gold_section) among the top-K DDD
    section hits, or 0 if absent. Pinned: scores the passed-in docs_text via the
    lower-level shared-corpus scorer (recall_all reads live disk — not used here)."""
    from core.recall_multi import _ddd_section_scores_multi
    scored = _ddd_section_scores_multi(query, corpus)  # sorted [(doc, section, score)]
    for i, (doc, section, _score) in enumerate(scored[:k], start=1):
        if doc == gold_doc and section == gold_section:
            return i
    return 0


def score_recall_case(verification: dict) -> dict:
    """Score one recall case mechanically. Returns
    {status, recall_at_k, reciprocal_rank, rank, notes}.

    verification = {domain, query, gold, k, corpus}. domain ∈ {context_files, ddd}.
    gold = section title (context_files) OR [doc, section] (ddd).
    """
    domain = verification.get("domain")
    query = verification.get("query")
    gold = verification.get("gold")
    corpus = verification.get("corpus")
    k = int(verification.get("k", 5))

    if not query or gold is None or corpus is None:
        return {"status": "error", "recall_at_k": 0, "reciprocal_rank": 0.0, "rank": 0,
                "notes": "misconfigured recall case (missing query/gold/corpus)"}

    if domain == "context_files":
        if not isinstance(corpus, str):
            return {"status": "error", "recall_at_k": 0, "reciprocal_rank": 0.0, "rank": 0,
                    "notes": "context_files corpus must be a string"}
        rank = _rank_of_gold_context_files(query, corpus, gold, k)
    elif domain == "ddd":
        if not isinstance(corpus, dict) or not isinstance(gold, (list, tuple)) or len(gold) != 2:
            return {"status": "error", "recall_at_k": 0, "reciprocal_rank": 0.0, "rank": 0,
                    "notes": "ddd needs corpus dict{doc:text} + gold [doc, section]"}
        rank = _rank_of_gold_ddd(query, corpus, gold[0], gold[1], k)
    else:
        return {"status": "error", "recall_at_k": 0, "reciprocal_rank": 0.0, "rank": 0,
                "notes": f"domain {domain!r} not in recall-suite scope (context_files|ddd); "
                         "session/codeintel deferred"}

    hit = 1 if rank > 0 else 0
    return {
        "status": "passed" if hit else "failed",
        "recall_at_k": hit,
        "reciprocal_rank": (1.0 / rank) if rank > 0 else 0.0,
        "rank": rank,
        "notes": f"gold {'@rank ' + str(rank) if rank else 'absent'} in top-{k} ({domain})",
    }


def aggregate_recall(results: list[dict]) -> dict:
    """Mean recall@K + MRR over scored recall cases (the SUITE number).

    Only counts cases with a definitive recall_at_k (skips 'error'). Empty → honest
    zeros, never a crash.
    """
    scored = [r for r in results if r.get("status") in ("passed", "failed")]
    n = len(scored)
    if n == 0:
        return {"mean_recall_at_k": 0.0, "mrr": 0.0, "n": 0}
    return {
        "mean_recall_at_k": round(sum(r["recall_at_k"] for r in scored) / n, 4),
        "mrr": round(sum(r["reciprocal_rank"] for r in scored) / n, 4),
        "n": n,
    }


def run_suite(cases: list[dict]) -> dict:
    """Run every recall case + aggregate. cases = [{verification: {...}}, ...].
    Returns {per_case, aggregate}. This is the standalone suite path (does NOT
    route through eval_runner — no double-run)."""
    per_case = [score_recall_case(c.get("verification", {})) for c in cases]
    return {"per_case": per_case, "aggregate": aggregate_recall(per_case)}


# ── seed query set (intent-first gold; ddd + context_files domains) ───────
# Each record: (query, domain, doc, gold_section, difficulty).
#   • gold is EXPLICIT (the real section that answers the query) — NOT auto-picked
#     from title-keyword hints, so a HARD query whose gold title does NOT contain
#     the query words is possible (run_a616dc6b).
#   • difficulty: "easy" = the gold section TITLE lexically overlaps the query
#     (title lookup); "hard" = it does NOT (answer lives in the BODY — this tests
#     real body recall AND structurally defeats circularity: gold cannot be
#     reverse-derived from the query text, so the set can't be tuned to the ranker).
#   • gold sections are asserted to exist by a canary test — a typo fails loudly.
# Corpus is pinned at run time from the live docs into a snapshot (internally
# consistent per run; a frozen fixture for a versioned baseline is a later run).
#
# domain "ddd"           → doc ∈ {TECH,PRODUCT,IMPROVEMENT,PROJECT}.md (project DDD)
# domain "context_files" → doc ∈ {MEMORY,KNOWLEDGE}.md (.context/)
_SEED_QUERIES = [
    # ── ddd · easy (title overlaps query) ──
    ("how does the autonomous pipeline work — its stages", "ddd", "TECH.md", "Architecture", "easy"),
    ("what are the runtime traps and gotchas", "ddd", "TECH.md", "Runtime Traps", "easy"),
    ("what tech stack does the project use", "ddd", "TECH.md", "Stack", "easy"),
    ("what are the coding conventions", "ddd", "TECH.md", "Conventions", "easy"),
    ("what is the product vision", "ddd", "PRODUCT.md", "Vision", "easy"),
    ("what are the non-goals", "ddd", "PRODUCT.md", "Non-Goals", "easy"),
    ("what are the strategic priorities", "ddd", "PRODUCT.md", "Strategic Priorities", "easy"),
    ("what approaches failed before", "ddd", "IMPROVEMENT.md", "What Failed", "easy"),
    ("what has worked well", "ddd", "IMPROVEMENT.md", "What Worked", "easy"),
    ("what are the current known issues", "ddd", "IMPROVEMENT.md", "Known Issues", "easy"),
    ("what is the current focus", "ddd", "PROJECT.md", "Current Focus", "easy"),
    ("what is blocking progress", "ddd", "PROJECT.md", "Blocked By", "easy"),
    # ── ddd · hard (gold title does NOT contain the query terms; answer in body) ──
    ("how do I start the app locally and rebuild it", "ddd", "TECH.md", "Dev Commands", "hard"),
    ("how is the daemon and session lifecycle structured", "ddd", "TECH.md", "Key Subsystems", "hard"),
    ("how does the app differentiate from a chatbot", "ddd", "PRODUCT.md", "What Makes SwarmAI Different", "hard"),
    ("who is this built for, which people use it", "ddd", "PRODUCT.md", "Target Users", "hard"),
    ("what past data leaks or breaches happened", "ddd", "IMPROVEMENT.md", "Security History", "hard"),
    # ── context_files · easy ──
    ("what cognitive principles govern judgment", "context_files", "MEMORY.md", "Principles", "easy"),
    ("what past corrections were captured", "context_files", "MEMORY.md", "Corrections", "easy"),
    ("what open threads are being tracked", "context_files", "MEMORY.md", "Open Threads", "easy"),
    ("how does the hook system work", "context_files", "KNOWLEDGE.md", "Hook System [model]", "easy"),
    ("what is the database schema", "context_files", "KNOWLEDGE.md", "Database Schema [model]", "easy"),
    # ── context_files · hard (title mismatch) ──
    ("what recurring mistakes keep happening to the agent", "context_files", "MEMORY.md", "Pitfalls", "hard"),
    ("how is the React UI component tree organized", "context_files", "KNOWLEDGE.md", "Frontend Architecture [model]", "hard"),
    ("what undocumented model limits can silently truncate output", "context_files", "KNOWLEDGE.md", "Claude Code CLI Hidden Defaults [constraint]", "hard"),
]

_DDD_DOCS = ("TECH.md", "PRODUCT.md", "IMPROVEMENT.md", "PROJECT.md")
_CONTEXT_DOCS = ("MEMORY.md", "KNOWLEDGE.md")


def _load_corpora(project: str = "SwarmAI") -> tuple[dict, dict]:
    """Return (ddd_docs, context_docs) as {doc: text}, pinned from live files."""
    import os
    from pathlib import Path
    base = Path(os.path.expanduser(f"~/.swarm-ai/SwarmWS/Projects/{project}"))
    ctx = Path(os.path.expanduser("~/.swarm-ai/SwarmWS/.context"))
    ddd = {d: (base / d).read_text() for d in _DDD_DOCS if (base / d).exists()}
    cf = {d: (ctx / d).read_text() for d in _CONTEXT_DOCS if (ctx / d).exists()}
    return ddd, cf


def _build_seed_cases(project: str = "SwarmAI") -> list[dict]:
    """Build recall cases from the explicit seed. ddd cases carry the whole ddd
    docs dict as corpus (shared-corpus BM25); context_files cases carry that one
    file's text."""
    ddd_docs, cf_docs = _load_corpora(project)
    cases = []
    for query, domain, doc, gold, difficulty in _SEED_QUERIES:
        if domain == "ddd":
            if doc not in ddd_docs:
                continue
            corpus = ddd_docs
            gold_ref = [doc, gold]
        elif domain == "context_files":
            if doc not in cf_docs:
                continue
            corpus = cf_docs[doc]
            gold_ref = gold
        else:
            continue
        cases.append({"verification": {"domain": domain, "file": doc, "query": query,
                                       "gold": gold_ref, "k": 5, "corpus": corpus},
                      "difficulty": difficulty})
    return cases


if __name__ == "__main__":  # pragma: no cover — manual/scheduled invocation
    import json
    cases = _build_seed_cases()
    out = run_suite(cases)
    # Per-domain + per-difficulty breakout — a low context_files score must not
    # hide behind ddd, and the hard-query score is the honest body-recall signal.
    def _agg(subset):
        res = [score_recall_case(c["verification"]) for c in subset]
        return aggregate_recall(res)
    ddd = [c for c in cases if c["verification"]["domain"] == "ddd"]
    cf = [c for c in cases if c["verification"]["domain"] == "context_files"]
    hard = [c for c in cases if c["difficulty"] == "hard"]
    easy = [c for c in cases if c["difficulty"] == "easy"]
    report = {
        "overall": {"recall_at_5": out["aggregate"]["mean_recall_at_k"],
                    "mrr": out["aggregate"]["mrr"], "n": out["aggregate"]["n"]},
        "by_domain": {"ddd": _agg(ddd), "context_files": _agg(cf)},
        "by_difficulty": {"easy": _agg(easy), "hard": _agg(hard)},
        "scope": "seed query set — ddd + context_files domains (session/codeintel deferred)",
    }
    print(json.dumps(report, indent=2))
    for c, r in zip(cases, out["per_case"]):
        v = c["verification"]
        g = v["gold"] if isinstance(v["gold"], str) else "/".join(v["gold"])
        print(f"  {'✓' if r['recall_at_k'] else '✗'} rank={r['rank']} [{c['difficulty']:4}] {v['domain']:13} {g[:34]:34} ← \"{v['query'][:38]}\"")
