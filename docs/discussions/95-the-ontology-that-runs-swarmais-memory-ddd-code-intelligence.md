---
title: "The Ontology that runs SwarmAI's Memory, DDD & Code Intelligence (🏷️Classification + 🕸️Relations, no Neo4j)"
created: 2026-07-08
updated: 2026-07-19
status: published
---
<!-- GitHub Discussion #95: https://github.com/xg-gh-25/SwarmAI/discussions/95 -->
> 🌐 English | 中文版 → #96 · Related: #20 No Neo4j · #59 DDD Governance · #37 Memory

> **In one line**: What powers SwarmAI's *Memory, DDD, and Code Intelligence* is a single, ultra-lightweight Ontology — just two layers of rules: 🏷️**Classification** + 🕸️**Relations**. No Neo4j, no OWL — yet it lets the AI retrieve precisely and keep the whole picture in view across ~90K words of memory, 9 project knowledge bases, and 18K functions.

This post explains three things: what an Ontology actually is, how we apply it to *knowledge*, and how we apply the same idea to *code*.

---

## Page 1 · What an Ontology Is

![What an Ontology is](https://github.com/xg-gh-25/SwarmAI/raw/main/docs/assets/discussions/ontology-page1.png)

Don't let the word scare you. An Ontology holds no content — it just sets the **rules** so that a mountain of content can be found. The easiest analogy is your **phone contacts**, and the rules have two layers:

- **🏷️ Layer 1: Classification** — Contacts tag people as "family / colleague / friend." Knowledge works the same: classify first. We sort every knowledge entry into one of 7 types: `principle · correction · decision · mental-model · guideline · pitfall · process`. The payoff is **retrieve on demand**: when coding I only pull "pitfalls" and "guidelines"; when deciding whether to build something I only pull "decisions" — no reading the whole notebook cover to cover.
- **🕸️ Layer 2: Relations** — Contacts record "Zhang is Li's boss." Knowledge entries link too, and each link is a triple: `this guideline —applies_to→ session_unit.py`, `new decision B —supersedes→ old decision A`. The payoff is **follow the thread**: ask "what will I hit if I change this file?" and I follow the links to surface the relevant pitfalls — even ones whose title never mentions that filename.

> A database analogy: **Ontology = the schema (which fields exist, how they relate)**, **Knowledge = the actual rows you fill in**. Remember just these two layers — 🏷️Classification + 🕸️Relations.

### Why exactly these seven types? (Not arbitrary — a cognitive hierarchy)

A fair question: I get classification, but **why exactly seven types — not five, not ten**? Because these seven form a **MECE (mutually exclusive, collectively exhaustive) three-layer cognitive structure**. Which layer an entry belongs to directly determines two things: **how fast it should be forgotten** and **at which stage of the work it gets read**.

| Cognitive layer | Types | The question it answers | Lifecycle |
|---|---|---|---|
| **Meta-cognitive** | 🔴 principle · correction | "how should I think / how was I wrong before" | **never forgotten** (evergreen, top attention) |
| **Cognitive** | 🟡 decision · mental-model | "what did I decide / how do I understand a thing" | **fades** by current relevance |
| **Operational** | 🟢 guideline · pitfall · process | "how do I actually do the work" | **fades fastest** (a workaround from 6 months ago is likely an anti-pattern now) |

Three "why"s chain into the whole design:

1. **Why MECE** — every entry has exactly one home, so storing and retrieving are unambiguous. Once the type is fixed, the injection strategy and retrieval path are fixed too — no re-deciding each time.
2. **Why layers, not seven flat tags** — because **layer = lifecycle**. Principles and corrections are hard-won lessons that should never expire; operational guidelines/pitfalls are the most time-sensitive and should fade with disuse. The layer directly drives the *speed* of Darwinian fade (see Page 2).
3. **Why this classification powers "stage-based injection"** — different stages read different types: deciding whether to build → read `decision + principle`; writing code → read `guideline + pitfall + correction`; delivery/wrap-up → read `process + principle`. The classification *is* a routing table for "what to look at when."

> In one line: **seven types = a MECE split of three cognitive layers (meta-cognitive / cognitive / operational)**. It simultaneously defines *attention priority*, *forgetting speed*, and *stage routing* — which is why it's seven: one more would be redundant, one fewer would miss a distinction. (Source: `MEMORY_SECTIONS` in `backend/core/ddd_entry_lifecycle.py`.)

---

## Page 2 · Applied to *Knowledge*: One Engine, Two Consumers

![Applied to knowledge](https://github.com/xg-gh-25/SwarmAI/raw/main/docs/assets/discussions/ontology-page2.png)

Key insight: **Memory and DDD are two clients of the same logic — they differ only in the "how fast to forget" parameter.** Those two layers of rules live in one engine — `backend/core/ddd_entry_lifecycle.py` (🏷️7 types + 🕸️relation graph in `.knowledge-graph.yaml`).

**💛 Consumer 1: my long-term memory** (`MEMORY.md`, cross-session, ~90K words)
- `never forget` — principles · corrections · post-mortems (hard-won lessons, always injected)
- `fades` — decisions · mental models (selected by relevance to the current topic)
- `fades fastest` — guidelines · pitfalls (drop out after long disuse)
- When memory is too big to fit → **selective injection by type + relevance**, with VIPs (principles/corrections) always pinned to the top.

**💚 Consumer 2: each project's knowledge base** (`Projects/*/`, four docs each)
- lessons auto-metabolize (used → +1 stronger; unused → dormant → archived)
- staleness detection (code changed but docs didn't → auto-flag)
- cross-project relation index ("all projects' failure lessons" in one string)
- fixed four-doc structure (`PRODUCT / TECH / IMPROVEMENT / PROJECT`) — the structure *is* the classification

**🚫 What we deliberately DON'T do (this is what "lightweight" means):** Big companies do ontology with heavy graph databases (Neo4j / OWL). We don't, for three reasons — ① too much maintenance tax (every code change means hand-syncing a pile of edges); ② not worth it (in a 1M context, just injecting it and letting me reason is cheaper than building a query system); ③ it never "forgets" (a graph DB keeps a node forever; we want Darwinian fade → forget).

---

## Page 3 · Applied to *Code*: A Relationship Map of the Code Itself

![Applied to code](https://github.com/xg-gh-25/SwarmAI/raw/main/docs/assets/discussions/ontology-page3.png)

Same 🏷️Classification + 🕸️Relations — but this time the objects aren't lessons, they're **the code itself**. Plainly: turn the whole codebase into a **subway map** — every function is a station, "who calls whom" is a line.

The engine is `backend/core/code_intel/graph_store.py` (a **separate engine**, not shared with the knowledge side):
- **🏷️ Nodes = code symbols** (class · function · method, ~18K)
- **🕸️ Edges = call relations** ("who calls whom," ~24K), stored in each project's own `code_intel.db`, auto-updated by a watcher on every code change.

**A real call chain** (pulled from the live graph, not made up):
```
_maintenance_loop ─calls▸ _check_ttl ─calls▸ SessionUnit.kill 🔥(44 callers) ─calls▸ _force_kill / _cleanup_internal
```
That's the value of "relations": ask me to change the signature of `SessionUnit.kill` — one graph query shows **44 call sites** (cleanup, TTL, memory pressure, shutdown…) that all have to change with it. **Without the graph I can only change it and pray; with it, I know before I touch anything.**

Same graph, different question, different capability: count **in-edges** (who calls me) → more = riskier (`SessionUnit.kill` has 44 = high-risk node); count **out-edges** = 0 with no callers → dead code, safe to delete. "What will this break," "is this still used," "how risky is this change" — all answered by a graph query, no human reading code.

**Why a separate engine, not shared with the knowledge side?** The knowledge side cares "**should this be forgotten?**" (hence fade rules); the code side cares "**who is connected to whom right now?**" (a code change invalidates old edges immediately — no "fade"). They share the ontology *idea* (nodes + edges + classification) but have opposite lifecycle needs, so they're two engines.

---

## Appendix · Formal-ontology view (aligns with the design doc §3 L2)

The two layers of rules above (🏷️ classification + 🕸️ relations), in semantic-web terms, are the **schema layer of an ontology** — we just deliberately skip OWL/RDF and implement it as two flat, grep-able, context-loadable structures:

| Ontology element | Semantic-web formalism | Our implementation |
|---|---|---|
| **Classes** (what kinds of knowledge exist) | OWL classes | 7 types × 3 cognitive layers (`MEMORY_SECTIONS`) |
| **Relations** (how entries connect) | RDF triples | 10 relation types in `.knowledge-graph.yaml` |
| **Constraints** (rules the schema enforces) | SHACL / axioms | layer → lifecycle: evergreen sections never decay; only operational entries (guideline/pitfall/process) are ever reclaimed — the keep-set `{principle, correction, decision, model}` is permanent |
| **Query** | SPARQL / Cypher | plain text loaded into context + keyword/FTS recall — the agent *is* the query engine |

**One thing that's easy to conflate: decay ≠ reclaim.** Any non-evergreen entry *decays* active → dormant → archived by age (so even decisions/models fade); but *physical reclaim* (actually deleting noise) touches only the operational layer — because the keep-set permanently protects both the meta-cognitive and cognitive layers.

**Same ontology, three views** (the design doc completes it to three — one more than this post's original two: the Entity Index):

| View | Nodes | Edges | Governance |
|---|---|---|---|
| **Memory / DDD** | 7-type entries | 10 relation types | Darwinian decay (fade → forget) |
| **Code Intelligence** | code symbols | call / import / dependency | rebuilt on change (no fade) |
| **Entity Index** | domain concepts | concept → project/doc/section routes | refreshed by the Code-Intel channel |

> Full design: [DDD Cultivation Engine HLD §3 L2 + §9b](https://github.com/xg-gh-25/SwarmAI/blob/main/docs/DDD-Cultivation-Engine-HLD.md).

---

## Three-Page Summary

| | Applied to | Engine | Lifecycle |
|---|---|---|---|
| **Knowledge** | memory + 9 project KBs | `ddd_entry_lifecycle.py` (shared) | Darwinian **fade → forget** |
| **Code** | call graph of 18K functions | `code_intel/graph_store.py` (separate) | **invalidate + rebuild** on change, no fade |

**One idea (🏷️Classification + 🕸️Relations), three places it lands, same goal — let the AI retrieve precisely and keep the whole picture in view.**

We use the ontology *idea* (schema + relations + reasoning) but reject its heavy *implementation* (graph DB + formal standards). That's what we mean by "lightweight ontology."

---

_Related: [#19 Your AI Agent Doesn't Need Neo4j — Darwinian Knowledge Management](https://github.com/xg-gh-25/SwarmAI/discussions/19) · [#59 DDD Knowledge Governance — 7 MECE types + lifecycle](https://github.com/xg-gh-25/SwarmAI/discussions/59)_
