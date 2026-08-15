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



def _gold_titles(gold) -> tuple[str, ...]:
    """Normalize a gold spec to a tuple of section titles.

    gold may be a single section title (str) — the common case — OR an
    ACCEPTABLE-SET (list/tuple of titles) for a genuinely cross-cutting query.
    run_79de25f8: a production task query ("修 context_files 召回") legitimately
    maps to more than one section (Guidelines OR Pitfalls OR Corrections); forcing
    a single "primary" gold would be arbitrary and make the case brittle. The
    acceptable-set is labeled INTENT-FIRST (from the query, before running recall)
    and a hit = ANY member appears in top-K. This is the M3-skeptic-endorsed model:
    a single primary section is not well-defined for cross-cutting task queries.
    """
    if isinstance(gold, (list, tuple)):
        return tuple(gold)
    return (gold,)


def _rank_of_gold_context_files(query: str, corpus: str, gold, k: int) -> int:
    """Return the 1-based rank of the FIRST acceptable gold section among
    recall_context's top-K sections, or 0 if none present. gold = a section title
    (str) or an acceptable-set (list). Pinned: parses the passed-in corpus, no disk."""
    from core.context_recall import recall_context
    res = recall_context("MEMORY.md", query, memory_content=corpus, max_sections=k)
    if not res.allowed:
        return 0
    acceptable = set(_gold_titles(gold))
    for i, sec in enumerate(res.sections[:k], start=1):
        if sec in acceptable:
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
# domain "context_files" → doc = MEMORY.md ONLY (.context/). KNOWLEDGE.md is NOT
#   a served recall path (recall_context only serves MEMORY.md) — see the Gate-2
#   note at the KNOWLEDGE exclusion below.
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
    # ── context_files · NAME-SIGNAL class (category-browse) ──────────────────
    # run_79de25f8: these were the ORIGINAL self-authored context_files cases. They
    # are category-browse queries ("what X are recorded") — a REAL but LOW-PRODUCTION
    # shape: a repro over 500+ real user messages found 0.0% are this shape (production
    # recall is triggered by task-shaped chat, not category browsing). They are KEPT
    # (not deleted — M3 skeptic synthesis) as a labeled 'name-signal' class that guards
    # against regression in section-title/name-signal recall (memory_index.py:985
    # section_name_signal). They score LOW by design (most are name-matched-then-
    # slicer-dropped, a Gate-1-adjudicated head-position-bias tradeoff — NOT a bug,
    # see Knowledge/Designs/2026-07-19-recall-synonym-gap-decision.md). Reported
    # SEPARATELY from 'task' so a low name-signal number is visible as a known-miss
    # class, never averaged into a scary headline.
    ("what cognitive principles govern judgment", "context_files", "MEMORY.md", "Principles", "name-signal"),
    ("what past corrections were captured", "context_files", "MEMORY.md", "Corrections", "name-signal"),
    ("what open threads are being tracked", "context_files", "MEMORY.md", "Open Threads", "name-signal"),
    ("what decisions have been recorded", "context_files", "MEMORY.md", "Decisions", "name-signal"),
    ("what standing guidelines does the agent follow", "context_files", "MEMORY.md", "Guidelines", "name-signal"),
    ("what recurring mistakes keep happening to the agent", "context_files", "MEMORY.md", "Pitfalls", "name-signal"),
    ("what post-incident reviews exist", "context_files", "MEMORY.md", "COE Registry", "name-signal"),
    ("what standing user preferences are on record", "context_files", "MEMORY.md", "Standing Preferences", "name-signal"),
    # ── context_files · TASK class (production-shaped, sampled VERBATIM) ─────────
    # run_79de25f8: queries taken WORD-FOR-WORD from the production transcript DB
    # (the shape recall actually sees). gold is labeled INTENT-FIRST (what section
    # SHOULD answer this, reasoned from the query text with recall NOT run) — a TIGHT
    # single title, or a 2-set ONLY where genuinely cross-cutting (see _gold_titles).
    # ⚠️ Gate-2 (run_79de25f8) caught my FIRST attempt: the golds were reverse-tuned
    # to recall's output (acceptable_union ⊇ everything recall returns → task recall
    # =1.00, the exact circularity this run exists to KILL — the authorship trap).
    # These are the CORRECTED tight golds: the union INCLUDES sections recall does
    # NOT return (e.g. Corrections for the test-theater query, which MISSES) so the
    # set cannot be a superset of the ranker output. Structurally guarded by
    # test_task_golds_not_superset_of_recall_output. Honest number: 7/8 = 0.88.
    ("我们recall 怎么这么多问题 到底还剩什么", "context_files", "MEMORY.md", "Open Threads", "task"),
    ("frontend reconcile race 复发 streaming tab", "context_files", "MEMORY.md", ["Open Threads", "Pitfalls"], "task"),
    ("session 资源仲裁 多 tab 隔离", "context_files", "MEMORY.md", "Open Threads", "task"),
    ("为什么最近跑的几个 pipeline 都没生效", "context_files", "MEMORY.md", ["Pitfalls", "COE Registry"], "task"),
    ("修 restore 超时 消除死锁窗口", "context_files", "MEMORY.md", ["Pitfalls", "COE Registry"], "task"),
    ("起 bugfix pipeline 修 partial-DB freshness-lockout", "context_files", "MEMORY.md", "Pitfalls", "task"),
    ("ddd recall leg 对宽泛查询被 PRODUCT 营销段挤占", "context_files", "MEMORY.md", ["Pitfalls", "Guidelines"], "task"),
    ("adversarial gate 抓到 test-theater", "context_files", "MEMORY.md", "Corrections", "task"),
    # NOTE (Gate-2, run_a616dc6b): KNOWLEDGE.md is DELIBERATELY excluded from the
    # context_files domain. recall_context / production _recall_context_files ONLY
    # ever serve MEMORY.md (recall_multi.py:443 "MEMORY is the canonical one"); the
    # scorer is MEMORY-index-shaped, so feeding KNOWLEDGE text yields an EMPTY index
    # → guaranteed rank 0 for ANY query. Benchmarking KNOWLEDGE here would score a
    # NONEXISTENT recall path as "failure" and falsely damn the number. If KNOWLEDGE
    # recall is ever wanted, it needs its own served path first (a separate run).
]

# Run-0 single-source rule: never hardcode the canonical-4 tuple (order-independent
# detector in test_ddd_canonical_docs_single_source). Used only as dict keys below
# (_load_corpora), so tuple order is irrelevant. core.* resolves on the same sys.path
# this module already relies on for its deferred core.context_recall import.
from core.project_registry import DDD_CANONICAL_DOCS as _DDD_DOCS
# context_files recall serves MEMORY.md ONLY (recall_multi._recall_context_files);
# KNOWLEDGE.md has no served recall path, so it's not a benchmarkable doc here.
_CONTEXT_DOCS = ("MEMORY.md",)


def _load_corpora(project: str = "SwarmAI") -> tuple[dict, dict]:
    """Return (ddd_docs, context_docs) as {doc: text}, pinned from live files."""
    import os
    from pathlib import Path
    from core.ddd_paths import ddd_path  # 2-understanding/ post-ad7f6623
    base = Path(os.path.expanduser(f"~/.swarm-ai/SwarmWS/Projects/{project}"))
    ctx = Path(os.path.expanduser("~/.swarm-ai/SwarmWS/.context"))
    ddd = {d: ddd_path(base, d).read_text() for d in _DDD_DOCS if ddd_path(base, d).exists()}
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


def run_knockout(project: str = "SwarmAI") -> tuple[bool, str]:
    """Mutation-proof teeth: prove the recall scorer DISCRIMINATES.

    Scores a real, easy query against a DELIBERATELY WRONG gold section that does
    not exist. A working scorer MUST return rank 0 (the wrong gold is absent from
    top-K). Returns (discriminates, message):
      • discriminates=True  → wrong gold scored rank 0 (scorer has teeth) → the
        POSITIVE outcome. Prints nothing special; caller exits 0.
      • discriminates=False → the scorer "found" a nonexistent section (rank>0),
        i.e. it is vacuous/broken → emits RECALL_TEETH_FAIL, caller exits 1.

    This is the target of a recall case's ``negative_command`` (gate_teeth): a
    non-vacuous knockout that goes RED (RECALL_TEETH_FAIL) exactly when the
    measurement stops discriminating — so the gate cannot silently rot into
    always-green (the DDD 2-tooth eval policy: "a case must WORK, not just PASS").
    """
    ddd_docs, _cf = _load_corpora(project)
    if "TECH.md" not in ddd_docs:
        return False, (f"RECALL_TEETH_FAIL: cannot load TECH.md corpus for "
                       f"{project!r} — knockout cannot run")
    # A real easy query, paired with a gold section that does NOT exist.
    res = score_recall_case({
        "domain": "ddd",
        "query": "how does the autonomous pipeline work — its stages",
        "gold": ["TECH.md", "This Section Does Not Exist In Any Doc"],
        "k": 5,
        "corpus": ddd_docs,
    })
    if res["rank"] == 0 and res["status"] == "failed":
        return True, (f"knockout OK: wrong gold scored rank 0 "
                      f"(scorer discriminates) — {res['notes']}")
    return False, (f"RECALL_TEETH_FAIL: wrong gold scored rank={res['rank']} "
                   f"status={res['status']} — the scorer 'found' a nonexistent "
                   f"section, so it is NOT discriminating: {res['notes']}")


if __name__ == "__main__":  # pragma: no cover — manual/scheduled invocation
    import json
    import sys as _sys
    if "--knockout" in _sys.argv[1:]:
        _ok, _msg = run_knockout()
        print(_msg)
        _sys.exit(0 if _ok else 1)
    cases = _build_seed_cases()
    out = run_suite(cases)
    # Per-domain + per-difficulty breakout — a low context_files score must not
    # hide behind ddd, and the hard-query score is the honest body-recall signal.
    def _agg(subset):
        res = [score_recall_case(c["verification"]) for c in subset]
        return aggregate_recall(res)
    ddd = [c for c in cases if c["verification"]["domain"] == "ddd"]
    cf = [c for c in cases if c["verification"]["domain"] == "context_files"]
    # Split context_files by CLASS — the whole point of run_79de25f8. name-signal
    # (category-browse, low-production) and task (production-shaped) are reported
    # SEPARATELY so a low name-signal number can't masquerade as "recall is broken".
    cf_name_signal = [c for c in cf if c["difficulty"] == "name-signal"]
    cf_task = [c for c in cf if c["difficulty"] == "task"]
    report = {
        "overall": {"recall_at_5": out["aggregate"]["mean_recall_at_k"],
                    "mrr": out["aggregate"]["mrr"], "n": out["aggregate"]["n"],
                    "warning": "overall AVERAGES name-signal (non-production browse) "
                               "into the number — read context_files_by_class instead"},
        "by_domain": {"ddd": _agg(ddd), "context_files": _agg(cf)},
        "context_files_by_class": {
            "task_PRODUCTION_SHAPED": _agg(cf_task),
            "name_signal_category_browse_LOW_PRODUCTION": _agg(cf_name_signal),
            "note": "task = the query shape production actually issues (recall's honest "
                    "number). name-signal = category-browse; 0/500 real msgs are this "
                    "shape, most are name-matched-then-slicer-dropped by design (NOT a bug).",
        },
        "scope": "seed query set — ddd + context_files domains (session/codeintel deferred)",
    }
    print(json.dumps(report, indent=2))
    for c, r in zip(cases, out["per_case"]):
        v = c["verification"]
        # ddd gold = [doc, section]; context_files gold = title (str) or acceptable-set (list)
        if v["domain"] == "ddd":
            g = "/".join(v["gold"])
        else:
            g = v["gold"] if isinstance(v["gold"], str) else "|".join(v["gold"])
        print(f"  {'✓' if r['recall_at_k'] else '✗'} rank={r['rank']} [{c['difficulty']:11}] {v['domain']:13} {g[:34]:34} ← \"{v['query'][:38]}\"")
