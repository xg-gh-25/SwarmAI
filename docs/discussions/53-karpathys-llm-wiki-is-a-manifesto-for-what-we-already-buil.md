---
title: "Karpathy's LLM Wiki Is a Manifesto for What We Already Built — And the One Gap We Just Closed"
created: 2026-05-29
updated: 2026-05-29
status: published
---
<!-- GitHub Discussion #53: https://github.com/xg-gh-25/SwarmAI/discussions/53 -->
## Karpathy's LLM Wiki Is a Manifesto for What We Already Built

> "The wiki is a persistent, compounding artifact. The cross-references are already there. The contradictions have already been flagged. The synthesis already reflects everything you've read."
> — [Andrej Karpathy, LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)

Karpathy published his LLM Wiki pattern this week. It describes exactly what SwarmAI has been running in production for 3 months — and names the one gap we hadn't closed yet.

---

## The Pattern (30-second version)

Instead of RAG (stateless retrieval on every query), the LLM **builds and maintains a persistent wiki** — structured, interlinked markdown. Knowledge compiled once, kept current. Three layers:

| Layer | What | Who owns it |
|-------|------|-------------|
| Raw sources | Immutable inputs | Human curates |
| Wiki | Structured, cross-referenced pages | LLM writes & maintains |
| Schema | Rules for how the wiki works | Human + LLM co-evolve |

Three operations: **Ingest** (source → wiki pages), **Query** (ask → synthesize → answer), **Lint** (health-check contradictions, orphans, gaps).

---

## How SwarmAI Already Implements This

| Karpathy's Concept | SwarmAI Equivalent | Status |
|--------------------|--------------------|--------|
| Raw sources | `Knowledge/` (72 DailyActivity + Notes + Learned + Reports) | ✅ Production 3 months |
| Wiki (LLM-maintained) | `MEMORY.md` + `EVOLUTION.md` + DDD docs (4 docs × 8 projects) | ✅ Production |
| Schema | 11 context files (AGENT/SOUL/STEERING/TOOLS/...) | ✅ Production |
| Ingest | Post-session hooks + DDD Cultivation Engine (event-driven, 6 sources) | ✅ Production |
| Query | Smart memory injection + FTS5 recall + session resume enrichment | ✅ Production |
| Lint | `s_loops-health` (31 checks) + `context_health_hook` | ✅ Production |
| Code graph | Code Intelligence (AST parser + graph_store + REST API) | ✅ Production |
| Evolution | L0-L3 governance + Evolution Pipeline v2 | ✅ Production |

We go further than the manifesto in several dimensions:

- **Self-evolution** — the wiki doesn't just accumulate, it upgrades its own rules (SOUL.md, AGENT.md)
- **Autonomous pipeline** — 8-stage TDD + adversarial review driven by wiki knowledge (DDD)
- **Proactive intelligence** — push-based briefing, not just pull-based query
- **Multi-project isolation** — each project has its own 4-doc knowledge substrate
- **Mechanical gates** — hooks enforce wiki maintenance (not honor-system discipline)

---

## The One Gap: Query Result Backflow

Karpathy's key insight we hadn't implemented:

> "Good answers can be filed back into the wiki as new pages. A comparison you asked for, an analysis, a connection you discovered — these are valuable and shouldn't disappear into chat history."

We had everything EXCEPT this. Session insights lived in chat history and DailyActivity logs but never flowed back as first-class Knowledge pages.

**Shipped today:** `KnowledgeBackflowHook` — a post-session hook that:
1. Scans all assistant messages for high-value outputs (>500 prose words + structural analysis markers)
2. Auto-captures qualifying outputs as `Knowledge/Notes/YYYY-MM-DD-<slug>-<hash>.md`
3. Atomic writes, YAML-safe frontmatter, content-hash dedup, max 3 per session
4. Fires between DailyActivity extraction and auto-commit (gets committed automatically)

---

## Bonus: Patterns Borrowed from Graphify + ΩmegaWiki

We also studied two implementations that took Karpathy's idea in different directions:

| Project | Stars | What it does | What we borrowed |
|---------|-------|-------------|------------------|
| [**Graphify**](https://github.com/safishamsi/graphify) | 55.7K | Code → knowledge graph (18 platforms) | **P3: Git hook auto-rebuild** — incremental re-index on commit |
| [**ΩmegaWiki**](https://github.com/skyllwt/AutoSci) | 874 | Research lifecycle platform (24 skills) | **P2: Anti-repetition memory** — failed experiments block similar approaches |

### P2: Anti-Repetition Check (from ΩmegaWiki)

ΩmegaWiki treats failed experiments as "anti-repetition memory" — not just archived, but actively blocking similar future directions.

We added this to our Pipeline EVALUATE stage: before recommending GO, the stage now explicitly cross-references `IMPROVEMENT.md "What Failed"` for structurally similar approaches. Must cite the failed entry and explain why this attempt is different — or REJECT.

### P3: Code Intel Auto-Rebuild (from Graphify)

Graphify uses git hooks to rebuild the knowledge graph after every commit (AST-only, zero API cost). Fresh graph with zero human effort.

We extended our existing `CodeChangeFeed` post-session hook to also re-index changed `.py/.ts` files into `code_intel.db`. The graph stays fresh automatically.

---

## What We Explicitly Did NOT Borrow

| Feature | From | Why we skipped |
|---------|------|----------------|
| Wiki-from-graph generation | Graphify `--wiki` | Our TECH.md (104K) already IS the architecture wiki. Two systems = drift. |
| 9 entity types | ΩmegaWiki | Over-classification. DDD 4-doc is more flexible. |
| Neo4j export | Graphify | SQLite graph sufficient at our scale. |
| Multi-platform support (18 IDEs) | Graphify | We're one integrated system, not a tool library. |
| LaTeX paper pipeline | ΩmegaWiki | We don't write papers. |

---

## The Fundamental Difference

| | Karpathy's Manifesto | Graphify | ΩmegaWiki | **SwarmAI** |
|---|---|---|---|---|
| Knowledge form | Flat markdown wiki | JSON graph + viz | 9-type entities + edges | DDD 4-doc × N projects |
| Automation | None (manual) | Git hook rebuild | GitHub Actions cron | Daemon + jobs + hooks (24/7) |
| **Self-evolution** | ❌ | ❌ | ❌ | ✅ L0-L3 governance |
| **Quality gates** | Lint (passive) | None | Review LLM | Pipeline + adversarial + convergence |

**SwarmAI is the only system where the wiki improves its own rules.** Others are stationary. We are non-stationary.

---

## Related Discussions

- [#19 — AI Agent 不需要 Neo4j: 知识管理的达尔文主义](https://github.com/xg-gh-25/SwarmAI/discussions/19) — the ontology philosophy behind our knowledge lifecycle
- [#51 — AI-Ready Repo Standard (EN)](https://github.com/xg-gh-25/SwarmAI/discussions/51) — the `.ai-context/` convention for making repos AI-consumable
- [#52 — AI-Ready Repo Standard (CN)](https://github.com/xg-gh-25/SwarmAI/discussions/52) — 中文版
- [#31 — The Personality Trap](https://github.com/xg-gh-25/SwarmAI/discussions/31) — why governance > personality for agent compliance
- [#32 — Multi-Specialist Adversarial Review](https://github.com/xg-gh-25/SwarmAI/discussions/32) — the review system that caught 3 HIGH bugs in today's "trivial" hook

---

## Source References

- Karpathy's LLM Wiki gist: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
- Graphify (55.7K ⭐): https://github.com/safishamsi/graphify
- ΩmegaWiki / AutoSci (874 ⭐): https://github.com/skyllwt/AutoSci

---

*The manifesto describes first principles. The tools implement subsets. SwarmAI runs the full loop — and the loop improves itself.*
