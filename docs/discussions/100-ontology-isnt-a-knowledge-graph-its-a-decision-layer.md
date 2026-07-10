---
title: "Ontology isn't a knowledge graph — it's a decision layer: how one lightweight ontology runs an entire self-evolving Agent OS"
created: 2026-07-10
updated: 2026-07-10
status: published
---
<!-- GitHub Discussion #100: https://github.com/xg-gh-25/SwarmAI/discussions/100 -->
> 🌐 English (full version) | 中文版 → #99 · Related: #95/#96 Ontology skeleton (classification + relations) · #19/#20 No Neo4j · #59 DDD Governance · #36 AI Agent for Data

> **In one line:** Everyone's talking about Ontology right now, but almost nobody explains where its value actually comes from. So here it is, straight: **Ontology's value isn't "knowing more" — it's "being able to make the call."** The noun layer (what things are, how they connect) lets an AI **find things**; the verb layer (what you can do to the world, who owns the outcome) lets an AI **make decisions**. SwarmAI's entire OS — Memory, DDD, Knowledge, Code Intelligence, Pipeline — is one complete grounding of this two-layer philosophy. No Neo4j, no OWL, anywhere.

This post covers six things: ① what Ontology gets mistaken for; ② its two real layers (noun vs verb); ③ a "4-rung ruler" laid across the whole SwarmAI OS; ④ our design philosophy and a few key decisions; ⑤ plain-language examples of how and where it's actually used; ⑥ the honest boundaries — where we don't oversell.

---

## 1 · Ontology gets talked to death, but never explained

You've probably scrolled past a pile of "Ontology is AI's next operating system" / "Palantir is worth billions because of its Ontology" posts lately. The word sounds impressive, but after reading you're likely still fuzzy on: **how is it actually different from a knowledge graph? How is it different from just building a database?**

Let's kill the biggest misconception first:

> **❌ Ontology = one giant knowledge graph, the more stuff the better.**
> **✅ Ontology = a set of rules that lets "understanding" turn into "action."**

An Ontology holds **no content of its own**. It just sets the rules, so a mountain of content can be found, reasoned over, and acted on. The most down-to-earth analogy: **your phone's contacts**.

- Contacts don't store "people," they store **rules**: how to classify people (family / colleague / friend), and who relates to whom (Zhang is Li's boss).
- Because of those rules, you can "find on demand" and "follow the thread" across thousands of contacts.

In database terms: **Ontology = the schema (which fields exist, how they relate); content = the actual rows you fill in.** That's the first level of understanding — and where most articles stop. But **the real value is the next step.**

---

## 2 · Ontology's two layers: the noun camp vs the verb camp

![Noun camp vs verb camp](https://github.com/xg-gh-25/SwarmAI/raw/main/docs/assets/discussions/ontology-decision-en-page1.png)

This is the spine of the whole piece. Same word, two camps using it, an order of magnitude apart in meaning.

### 🏷️ The noun camp: Ontology = Classification + Relations → lets you «find it»

This is where most people's understanding of Ontology stops — two layers of rules:

- **Classification:** every thing goes into one class. (Contacts: family / colleague)
- **Relations:** things link to each other, each link a triple. (Zhang —is boss of→ Li)

What this layer buys you is **retrieval**: pull on demand, follow the thread. Knowledge graphs live here — they **can look up, but can't act**. This is exactly the SwarmAI Ontology skeleton we covered in #95/#96, so I won't repeat it — one sentence and a link.

### ⚡ The verb camp: Ontology = what you can DO + who owns the outcome → lets you «make the call»

Palantir keeps insisting Ontology is a **decision layer**, and the essence isn't in the first two layers — it's in the next three primitives:

- **Action:** what you can do to the real world (place an order, change a config, kill a process).
- **Function:** decision rules, constraints, tradeoffs.
- **Interface / Proposal:** AI proposes → human approves → system executes → **fully auditable**.

**What this layer buys you is decision-making:** not just "knowing," but "making a call under uncertainty, with tradeoffs, where someone owns the outcome and it can be traced afterward."

> The distinction in one line: **the noun camp lets you see the world; the verb camp lets you change it. A pure knowledge graph stops at the noun layer — can look up, can't act; a real decision layer walks all the way to the verb layer.**

---

## 3 · One "4-rung ruler," laid across the whole SwarmAI OS

![The 4-rung ruler × SwarmAI subsystems](https://github.com/xg-gh-25/SwarmAI/raw/main/docs/assets/discussions/ontology-decision-en-page2.png)

How do you tell which layer an AI system actually stops at? Borrow a ruler from decision science — four words that **a lot of people conflate**:

| Rung | The question | In plain words |
|---|---|---|
| **Prediction** | what will happen | the report says revenue drops 8% this month |
| **Inference** | why | because a major customer churned |
| **Simulation** | what if… (multi-path what-if) | cut price vs change tactics — what happens under each |
| **Decision** | which path, own the outcome | make the call under uncertainty, and bear the result |

**Most "data capabilities" stop at prediction**, mistaking "having a report" for "being able to decide." Wrong — prediction is only the *input* to a decision. And "being able to decide" still isn't "risk decision" (making a call under uncertainty, with tradeoffs, owning the consequences).

Now lay SwarmAI's subsystems along this ruler — **each subsystem is exactly one rung:**

### Rungs 1–2: Prediction / Inference — Memory / DDD / Knowledge

This is the "know what happened, and why" material layer, and the place where the noun-camp Ontology lands most concretely. **Many people think this layer is just "storing docs." Wrong — every design choice here is Ontology's classification (🏷️), relations (🕸️), and lifecycle (⏳) at work.** Below, three blocks, taken one at a time, to show exactly *how each one embodies Ontology*.

One architectural fact first: **Memory and DDD aren't two separate things — they're two clients of the same engine**, sharing `backend/core/ddd_entry_lifecycle.py` — same classification rules, same relation graph, same forgetting algorithm, differing only in the "how fast to forget" parameter. That itself is the Ontology idea winning: one schema, many consumers.

#### 💛 Memory (my long-term, cross-session memory) — all three Ontology moves live here

**① Classification (🏷️): not 7 flat tags — 3 cognitive layers.** Every memory entry goes into a section, and behind it is a **MECE (mutually exclusive, collectively exhaustive) three-layer cognitive structure** — this is the key to the entire design:

| Cognitive layer | Type (prefix) | The question it answers | Forgetting policy |
|---|---|---|---|
| **🔴 Meta-cognitive** | principle (PRI) · correction (COR) | "how should I think / how was I wrong before" | **evergreen, never forgotten**, top attention |
| **🟡 Cognitive** | decision (DEC) · model (MOD) | "what did I decide / how do I understand a thing" | **fades** by current relevance |
| **🟢 Operational** | guideline (GUI) · pitfall (PIT) · process (PRC) | "how do I actually do the work" | **fades fastest** |

Why exactly this split? Because **layer = lifecycle = attention priority = when it gets read** — four things locked at once. An operational workaround from six months ago (GUI/PIT) is probably an anti-pattern today, so it should fade; a principle is a hard-won way of thinking that never expires. Classification isn't for tidiness — **the classification itself is a routing table for "how fast to forget, when to read."** (Live distribution right now: principle 9 · correction 5 · decision 44 · model 9 · guideline 225 · pitfall 201 · process 1 — the operational layer is the biggest and most perishable, exactly matching "fades fastest.")

**② Relations (🕸️): entries link to each other — follow the thread.** No memory is an island; they're connected by triples, stored in `.knowledge-graph.yaml`. Live: **142 relations, 9 predicates**:
```
COE05    —applies_to→   session_unit.py   (this lesson applies to this file)
new dec  —supersedes→   old decision      (decision B replaces decision A)
KD16     —serves_thesis→ T1               (this knowledge supports a core thesis)
```
(Predicate distribution: applies_to 122 · supersedes 4 · addresses / extends / motivated_by / serves_thesis a handful each.) The payoff is **following the thread**: I ask "what will I hit if I change `session_unit.py`?" and the system follows the `applies_to` edges to surface the relevant lessons — **even ones whose title never mentions that filename.** Keyword search can't find those; a relation graph can.

**③ Lifecycle (⏳): Darwinian forgetting — an algorithm, not a slogan.** This is the most-overlooked part of noun-camp Ontology, and the one we take most seriously. Every entry has a decay state machine, with parameters that are real, measured values:
```
active ──(60 days unreferenced)──▸ dormant ──(150 days total)──▸ archived (out of main memory, still searchable)
```
- **New entries are immune for 30 days** (grace period) — something just written down isn't condemned immediately.
- **The more it's referenced, the longer it lives**: high-value entries with ref_count ≥ 10 get double the threshold (120 days before dormancy). Use it or lose it, literally.
- When memory is too big to fit the current context → **selective injection by type + relevance**, with the meta-cognitive layer (principles/corrections) always pinned to the top, and long-idle operational entries yielding first.

> **How does Memory embody Ontology?** Classification decides where it belongs, how fast it forgets, when it's read; relations let it be found by following the thread; lifecycle makes it metabolize. **The three together are what make ~90K words of memory not a junk heap, but a cognitive organ that gets *cleaner* the more it's used.**

#### 💚 DDD (each project's knowledge base, 9 projects) — structure IS classification

DDD (Domain-Driven Design) is Memory's "project edition" — same engine, different consumer. How does it embody Ontology?

**① Classification: the four-doc structure is itself the schema.** Every project has a fixed four docs — not arbitrary, but a **MECE split answering four kinds of question**:

| Doc | Answers | Ontology analogy |
|---|---|---|
| **PRODUCT.md** | why build it, for whom, non-goals | the domain's "intent layer" |
| **TECH.md** | how it's built, architecture, conventions, traps | the domain's "implementation layer" |
| **IMPROVEMENT.md** | what worked, what failed, what to watch | the domain's "experience layer" |
| **PROJECT.md** | what's happening now, recent decisions | the domain's "state layer" |

**Which doc you put knowledge into IS the act of classifying.** A "this will blow up" lesson auto-goes to IMPROVEMENT.md's What Failed; an architecture decision goes to TECH.md. Structure is classification — no extra tagging needed.

**② Relations: cross-project index + cross-references.** The 9 projects' knowledge isn't siloed — the system builds a cross-project relation index (e.g. "all projects' failure lessons" pooled in one place), so a pit one project fell into, another can see by following the relations.

**③ Lifecycle: auto-metabolism + staleness detection.**
- Lessons **use-it-or-lose-it**: referenced → strengthened (+1), long idle → dormant → archived.
- **Staleness detection**: code changed but the doc didn't → the system auto-flags it stale. This is another use of "relations" — there's an implicit link between a doc and the code it describes; when the link breaks, it alarms.

> **How does DDD embody Ontology?** It structures "one project's domain knowledge" into a classifiable, linkable, self-updating ontology — instead of a pile of ever-messier, unmaintained READMEs.

#### 📚 Knowledge (the searchable knowledge store) — classification decides "can it be found at all"

Knowledge is the third consumer: reference material that doesn't belong to any one project but is worth keeping long-term (articles read, domain facts, external specs), stored in the `Knowledge/` directory and indexed into a full-text-searchable (FTS5) store.

How does it embody Ontology? **A counter-lesson makes the meaning of classification vivid:** we once tripped on this — wrote reference material into `KNOWLEDGE.md` (an *index* file that's always injected into context), and although it was "in the context," it was **completely invisible to search** — because search only scans the `Knowledge/` directory, not that index file. **Same content, wrong classification location, equals not stored at all.** This is a bloody proof of the primacy of noun-camp Ontology: **classification isn't a tidiness fetish — it directly decides whether a piece of knowledge "can be found when you need it."** Right class → recallable; wrong class → stored yet lost.

> **Looking at the three together:** Memory (cross-session cognition), DDD (project domain knowledge), Knowledge (searchable reference) — **three different consumers sharing one Ontology idea: 🏷️ classify first (deciding belonging, forgetting, when-to-read), 🕸️ then relate (so isolated knowledge can be followed by thread), ⏳ and metabolize (use it or lose it, cleaner the more it's used).** This whole layer is the "prediction/inference" material rung — it lets the AI "know what happened and why." But remember: **however strong, this is still just the noun layer. It lets you find things; it doesn't let you make the call.** The real leap is in the next two rungs.

### Rung 3: Simulation — CodeIntel/CodeGraph + the Pipeline's THINK

Now it gets interesting — this is **"if I change this, what happens?"** — counterfactual simulation. And its foundation is **still that same Ontology, just with the object switched from "knowledge" to "the code itself."**

- **CodeGraph — the Ontology of code (nodes = classification 🏷️, edges = relations 🕸️):** turn the whole codebase into a **subway map** — every function is a station, "who calls whom" is a line. Note this is **the exact same ontology idea landing a second time**:
  - **🏷️ Nodes = classification of code symbols**: class / function / method, measured (SwarmAI project) at **18,034 nodes**.
  - **🕸️ Edges = relations between code**: measured **28,445 call edges** (all `calls`-type), stored in each project's own `code_intel.db`, auto-rebuilt by a watcher on every code change.
  - This is the hardcore form of "simulation": ask me to change some high-traffic function, and **one graph query tells me how many call sites will blow up together.** For example, `_stage_record` has a measured **278 callers** — without the graph I can only "change it and pray"; with it, **I know before I touch anything which 278 places get hit.** That's the noun camp's "follow the thread," moved onto code.
  - Same graph, different question, different capability: count **in-edges** (who calls me) = more is riskier (278 = a high-risk node, handle with care); count **out-edges = 0 with no callers** = dead code, safe to delete. "What will this break," "is this still used," "how risky is this change" — all answered by a graph query, no human reading code.
  - **But here a key fork appears: the Ontology of code and the Ontology of knowledge have *opposite* lifecycles** — knowledge wants to "fade and forget," code wants to "invalidate and rebuild the instant it changes" (a stale call relation can't survive even a second). So they share the idea but are **two separate engines** (see Decision 3 below).
- **The Pipeline's THINK stage — turning "simulation" into a mandatory act:** CodeGraph lets me *see* "what will blow up if I change this"; the THINK stage then *forces* me, before touching anything, to **lay out 3 approaches**, each with **explicit constraints + tradeoffs**, and **stress-test each** before landing. That's decision science's multi-path what-if — not "think of one approach and charge," but "lay out several roads, what happens on each, which costs least." **Ontology provides the facts (what blows up), the Pipeline provides the discipline (you must simulate several roads first).**

### Rung 4: Decision — adversarial gate + human-in-the-loop approval + full audit

The last rung, and the one Palantir keeps stressing that most agent tools can't reach. SwarmAI's Pipeline genuinely walks to here, and every step is demonstrable:

- **Adversarial Gate:** the DELIVER stage spawns a **fresh-context adversarial sub-agent** whose only job is to attack the work, **NON-NEGOTIABLE**. This is "self-attack before you make the call."
- **AskUserQuestion human-in-the-loop approval:** for decisions that are both uncertain *and* hard to reverse → stop and ask a human, who makes the call in the loop.
- **Dangerous-action gate:** `dangerous_command_gate` / `_is_irreversible_external_op` — irreversible external actions like changing GitHub repo visibility, force-push, deleting a repo are structurally intercepted and require a human signature. (This one is a blood lesson: I once flipped the product repo from public to private on an inference, and GitHub wiped 209 stars instantly. After that, all such actions went behind a hard gate.)
- **Fully auditable:** every stage of every run, every gate verdict, written to disk.

**These four layers together are precisely an isomorph of Palantir's Proposal workflow (AI proposes → human reviews → system executes → audit).** We aren't a BI tool stuck at the prediction layer — we're an Agent OS that reaches the risk-decision layer.

---

## 4 · Design philosophy and key decisions

Having covered "where it's used," now the **"why it's designed this way"** — the part others can't copy.

### Decision 1: keep the noun layer deliberately light, invest heavily in the verb layer

Big companies do Ontology with heavy graph databases (Neo4j) and formal standards (OWL). **We deliberately don't**, for three reasons:

1. **Maintenance tax too high** — every code change means hand-syncing a pile of edges. Not worth it.
2. **Bad economics** — in a 1M context, **"inject the knowledge and let me reason" is cheaper than "build a query system."** This is a counter-intuitive but crucial call: in the large-context era, a lot of problems that "should" get an index / graph DB are cheaper to just stuff in and let the model reason over.
3. **It never forgets** — a graph DB keeps a node forever; we **want Darwinian forgetting** (use it or lose it).

**But the verb layer — gates, adversarial review, human-in-the-loop, audit — we invest heavily in.** Because the noun layer is the entry ticket; the verb layer ("turning understanding into action") is the moat. That's the philosophical core of this whole piece: **others compete on "knowing more"; we compete on "deciding right, owning it, tracing it back."**

### Decision 2: classification MUST be MECE (no overlap, no gaps), or the whole ontology collapses

The specifics of the three cognitive layers and seven types were covered in Part 3; here I only add the **why-it-must-be-designed-this-way** principle — because it's the bedrock of whether noun-camp Ontology holds up at all.

**MECE = mutually exclusive, collectively exhaustive.** Why die on this hill? Because the moment classification **overlaps** or has **gaps**, everything downstream breaks:
- **Overlap (one piece of knowledge fits two classes)** → where do you store it? where do you query it? you re-decide every time → classification loses its "auto-routing" meaning and degrades into a random sticky label.
- **Gap (some knowledge has no home)** → it gets forced into the nearest class, polluting that class's semantics, so "inject by type" pulls back a bunch of irrelevant stuff.

So our rule is: **every piece of knowledge has exactly one home.** Once its type is fixed, its injection strategy, forgetting speed, and reading timing are all locked with it — no per-entry re-deciding. That's why "how many classes" isn't a guess but strict MECE — **one more or one fewer breaks the downstream automation of "how fast to forget, when to read, what to pull."** The precision of classification directly determines the precision of retrieval.

### Decision 3: knowledge and code use two engines, not one

![Knowledge side vs code side — two engines](https://github.com/xg-gh-25/SwarmAI/raw/main/docs/assets/discussions/ontology-decision-en-page3.png)

Same "classification + relations," but opposite lifecycle needs, so two engines:

- **The knowledge side** cares "**should this be forgotten?**" → it has fade rules.
- **The code side** cares "**who's connected to whom right now?**" → the moment code changes, old edges are instantly invalid; no "fade," only **invalidate-and-rebuild**.

They share the **idea** of Ontology (nodes + edges + classification), but because their lifecycles are opposite, they're split into two.

### Decision 4: Darwinian forgetting — forgetting is a feature, not a bug

Most memory systems only ever store more. We let knowledge **metabolize**: referenced → +1 stronger; long idle → dormant → archived. Why? Because **a system that gets "bigger" the more it's used isn't the same as one that gets "smarter"** — noise drowns signal. Forgetting exists so that every injected context is the most-relevant-right-now.

---

## 5 · Plain language: how and where it's actually used

Three real scenarios (all things I run daily):

**Scenario A — I need to change a core function, and I'm scared it'll break something.**
Without the graph: change it and pray, then watch a string of call sites I never thought of blow up in production.
With the graph (CodeGraph): `_stage_record` → all 278 callers listed at once — cleanup logic, TTL, memory pressure, shutdown flow… all on the list. **I know the blast radius before I touch it.** That's the value of "simulation."

**Scenario B — I need to judge "should we even build this feature?"**
The system gives me only two classes to read: `decision + principle` (not a cover-to-cover reread of 90K words of notes) — how similar decisions went before, whether there's a principled pit we already hit, right there. That's "routing by class."

**Scenario C — I need to ship a change.**
The Pipeline forces: THINK lays out 3 approaches + tradeoffs (simulation) → BUILD → the DELIVER stage spawns a fresh agent to attack it (adversarial) → hit an irreversible external action, stop and get a human signature (risk decision) → everything logged (audit). **From requirement-in to PR-ready-out, it's a full chain with a risk decision in it — not "generated, done."**

---

## 6 · The honest boundaries (where we don't oversell)

An article about Ontology that only reports good news isn't trustworthy. Two things, said straight:

- **On the general knowledge layer, we deliberately stop at "prose level."** SwarmAI's own DDD/Memory is natural language written for humans/AI to read, **not a programmatically-reasonable typed graph** (no computable axioms, no inference engine) — and this is a **deliberate choice, not a regret**: the core world is one person, one workspace, semantically consistent by nature, so a heavy formal ontology has low ROI, and in a 1M context "inject it and let me reason" is cheaper. So strictly speaking, on the general side we practice "the *idea* of lightweight ontology" and **don't pretend to be a formal ontology.**
- **But in a genuinely fragmented data world, we've already made the ontology a contract with teeth — not prose.** An ontology's value density correlates with how fragmented the world is. The data skills we run for our customer (CMHK) — many tables' definitions, hierarchical permissions, cross-system mismatches — are exactly the "data scattered across systems, definitions fighting each other" case. **Here we've already shipped a layer of "SQL ontology":** a `catalog` (binding definitions to **concepts**, not tables) + a `validate_sql` contract gate, distributed to every query of 7 data skills — **a query that violates the data contract structurally cannot run** (fail-closed). This is the whole philosophy landing on the data side: the noun layer (tables, columns) isn't enough; you have to reach the verb layer (which queries are *allowed to execute*, who *owns correctness*). Prose → executable contract is what "find it → make the call" looks like on data. (For how this layer was built and the pits along the way, see #36 and its sequel.)

---

## Closing

Ontology isn't mystical. **It's the set of rules that lets "understanding" turn into "action."**

- The noun layer (classification + relations) lets the AI **find things** — we keep this layer extremely light, no Neo4j.
- The verb layer (what you can do + who owns the outcome + auditable) lets the AI **make the call** — we invest heavily here; it's the moat.
- The entire SwarmAI OS is that "prediction → inference → simulation → decision" 4-rung ruler, laid out one subsystem per rung.

> **In one line: other AIs help you *see clearly*; ours helps you *make the call* — with tradeoffs, ownership, and traceability.**

_(All numbers in this post are measured against the running system on 2026-07-10: memory's 7-type distribution, CodeGraph's 18,034 nodes / 28,445 edges, 9 project knowledge bases, relation-predicate counts — all taken from live code/databases, not estimates.)_

_Related: [#95](https://github.com/xg-gh-25/SwarmAI/discussions/95)/[#96](https://github.com/xg-gh-25/SwarmAI/discussions/96) Ontology skeleton (classification + relations, no Neo4j) · [#59](https://github.com/xg-gh-25/SwarmAI/discussions/59) DDD 7-type governance · [#36](https://github.com/xg-gh-25/SwarmAI/discussions/36) AI Agent for Data_
