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


# ── seed query set (intent-first gold; ddd domain) ────────────────────────
# Each query is what a person SHOULD be able to recall; gold = the (doc, section)
# that genuinely answers it (verified to exist). Corpus is the LIVE DDD docs,
# pinned at run time into a snapshot dict so a single run is internally
# consistent. For a versioned regression baseline, freeze the docs into a fixture
# (Run 4). Intent-first hint terms locate the real section name per doc.
_SEED_QUERIES = [
    ("how does the autonomous pipeline work stages and gates", "TECH.md", ("pipeline", "autonomous", "architecture")),
    ("what are the runtime traps and gotchas", "TECH.md", ("runtime trap", "trap", "gotcha")),
    ("what is the product vision", "PRODUCT.md", ("vision",)),
    ("what are the non-goals", "PRODUCT.md", ("non-goal",)),
    ("what failed before past mistakes", "IMPROVEMENT.md", ("what failed", "failed")),
    ("what worked well", "IMPROVEMENT.md", ("what worked", "worked")),
    ("current focus and open items", "PROJECT.md", ("current focus", "open item", "focus")),
]


def _build_seed_cases(project: str = "SwarmAI") -> list[dict]:
    import os
    from pathlib import Path
    from core import memory_index
    base = Path(os.path.expanduser(f"~/.swarm-ai/SwarmWS/Projects/{project}"))
    docs: dict[str, str] = {}
    for d in ("TECH.md", "PRODUCT.md", "IMPROVEMENT.md", "PROJECT.md"):
        p = base / d
        if p.exists():
            docs[d] = p.read_text()

    def pick_gold(doc: str, hints: tuple) -> Optional[str]:
        secs = list(memory_index.parse_memory_sections(docs.get(doc, "")).keys())
        for s in secs:
            if any(h in s.lower() for h in hints):
                return s
        return secs[0] if secs else None

    cases = []
    for q, doc, hints in _SEED_QUERIES:
        g = pick_gold(doc, hints)
        if g:
            cases.append({"verification": {"domain": "ddd", "query": q,
                                           "gold": [doc, g], "k": 5, "corpus": docs}})
    return cases


if __name__ == "__main__":  # pragma: no cover — manual/scheduled invocation
    import json
    out = run_suite(_build_seed_cases())
    agg = out["aggregate"]
    # Tag the number by scope (Gate-2): this is ddd-section recall over the seed
    # set, NOT whole-system recall (session/codeintel deferred) — never let 0.71
    # be misread as system-wide recall quality.
    print(json.dumps({"ddd_recall_at_5": agg["mean_recall_at_k"], "ddd_mrr": agg["mrr"],
                      "n": agg["n"], "domains": ["ddd"], "scope": "seed query set, ddd domain only"},
                     indent=2))
    for r in out["per_case"]:
        print(f"  {'✓' if r['recall_at_k'] else '✗'} rank={r['rank']}  {r['notes']}")
