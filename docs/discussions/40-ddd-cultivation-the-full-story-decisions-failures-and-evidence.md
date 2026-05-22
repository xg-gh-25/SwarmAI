# DDD Cultivation — The Full Story: Decisions, Failures, and Evidence

> GitHub: https://github.com/xg-gh-25/SwarmAI/discussions/40

---

Most AI agent "knowledge" is either a dumped README that rots in 2 weeks, or a RAG pipeline that retrieves fragments without judgment. We built a third option: **structured domain knowledge that grows from normal work, feeds multiple delivery engines, and never goes stale** — because the system that uses it is also the system that maintains it.

## TL;DR

| Aspect | What We Did |
|--------|-------------|
| **Problem** | AI agents are domain-blind. They code without knowing your business rules, produce content without knowing your strategy |
| **Solution** | 4 structured docs per project × 3-layer engine (Interface + Intelligence + Orchestration) × 7 feed channels |
| **Scale** | 5 projects, 5,100 lines active knowledge, 2,863 LOC implementation, 80 entries auto-cultivated per week |
| **Key insight** | Knowledge that grows AS A SIDE EFFECT of doing work is the only kind that survives |
| **Biggest failure** | Keyword classifier passed all tests but was 100% wrong on production data |
| **What we rejected** | RAG, knowledge graphs, fine-tuning, silent automated writing, single CLAUDE.md |

---

## The Problem: Domain-Blind AI

Every AI coding agent faces the same gap: the model knows programming but not **your** domain.

```
User: "Add the CMHK monthly report"
Agent without DDD: Generic report. Wrong scope. Wrong hierarchy. Wrong filters.
Agent with DDD: Reads TECH.md → knows 5 hierarchy levels, 17 data tables, mandatory partition filters.
                Reads IMPROVEMENT.md → knows previous report bugs (territory_owner column doesn't exist).
                Reads PRODUCT.md → knows CEO view vs GM view distinction.
                → Produces correct report on first attempt.
```

This isn't hypothetical. We have 9 CMHK skills that use DDD context. Before DDD was populated, each skill took 3-5 iterations to get the SQL right. After: first-attempt accuracy for domain-specific logic.

**Why existing approaches fail:**

| Approach | Why It Fails for Judgment |
|----------|--------------------------|
| **Single CLAUDE.md** | Unstructured → 5K-line dump; no lifecycle; rots after writing |
| **RAG** | Retrieves fragments without judgment context; "found a relevant chunk" ≠ "knows what to do with it" |
| **Fine-tuning** | Static snapshot; expensive to update; opaque; can't audit what changed |
| **Knowledge Graph** | Requires infrastructure; no human-readable path; complex query language; overkill at project scale |

---

## The Solution: 3-Layer Architecture

### Layer 1: Interface (What Humans See)

4 markdown documents per project, each answering one judgment axis:

| Document | Judgment Question | Example Content |
|----------|------------------|-----------------|
| **PRODUCT.md** | Should we build this? | Vision, priorities, non-goals, audience |
| **TECH.md** | Can we build this? | Architecture, conventions, API contracts, runtime traps |
| **IMPROVEMENT.md** | Have we tried this before? | What worked, what failed, anti-patterns, security history |
| **PROJECT.md** | Should we do this now? | Current focus, blockers, recent decisions, open items |

**Why exactly 4?** We tested with 2 (too sparse — agent lacked judgment context), 6 (too fragmented — context switching cost), and settled on 4 because they map cleanly to the decision tree an agent traverses: desirability → feasibility → history → timing.

### Layer 2: Intelligence (What Machines Maintain)

| Component | What It Does | LOC |
|-----------|-------------|-----|
| **Health Scoring** | 5-dimension scoring (staleness, completeness, usage, decay, contradiction). Produces trust levels: full → high → moderate → low | 379 |
| **Maturity Tracking** | Per-section lifecycle: sparse → growing → mature → evergreen. Evidence: source count, production-verified, used-in-decision | 488 |
| **Entry Lifecycle** | Per-entry decay: active → dormant → archived at 30/90 days no-reference. 5 type tags: guideline, pitfall, decision, model, process | 665 |

**Key insight:** Health drives AI trust, not human action. Stale content doesn't generate tasks — it's marked `[!]` in session briefings so the agent knows "trust this section less." Zero human maintenance burden.

### Layer 3: Orchestration (What Runs Automatically)

7 independent feed channels, fault-isolated (one crash ≠ all fail):

| Channel | Source | What It Produces |
|---------|--------|-----------------|
| 1 | DDD staleness detection | `[!]` warnings, refresh proposals |
| 2 | Pipeline REFLECT + corrections | Auto-apply lessons to IMPROVEMENT.md |
| 3 | DDD → KNOWLEDGE bridge | Promotes stable DDD content to Knowledge index |
| 4 | KNOWLEDGE staleness | Reference freshness detection |
| 5 | Entity Index validation | Cross-project routing table integrity |
| 6 | Signal pipeline | External insights → DDD proposals |
| 7 | Code Intelligence | Codebase drift → TECH.md sync |

**Time budget:** 25 seconds total, 5 proposals max per session, mtime filter (30 days). These limits came from Failure #2 below.

---

## Key Decisions (Why We Chose What We Chose)

### D1: Judgment Substrate, Not Knowledge Base

DDD answers "what helps AI **judge** better?" — not "what's interesting?" This filter rejects activity logs, status updates, raw metrics. Only judgment-shaping content enters DDD.

**Why:** Information saturation kills agents faster than information scarcity. A 10,000-line TECH.md where 80% is status updates makes the 20% that matters invisible.

### D2: Reuse Existing Extraction, Don't Build New

The memory pipeline already produces StructuredSummary (decisions, lessons, corrections) from every session. DDD Cultivation consumes the SAME output — zero new LLM calls.

**Why:** Every new extraction pipeline is a maintenance liability. Reusing means DDD quality improves as memory quality improves — compound, not additive.

### D3: Tiered Autonomy (Additive Auto-Apply + Risky Escalation)

Original design: binary "propose everything, never write silently." Problem: proposal fatigue. Nobody reviews 50 proposals.

Revised design:
- **ADDITIVE** changes (new lesson, new pattern) → auto-apply + log
- **RISKY** changes (modify, delete, contradict existing) → escalation queue

**Why:** Additive-only changes have zero risk. A new entry in IMPROVEMENT.md "What Failed" can't corrupt existing content. Modifications can — those need judgment.

### D4: Entity Index as Text, Not Graph Database

At 5 projects / ~120 entities, a flat text routing table in PROJECTS.md is sufficient. Agent reads it directly from system prompt — no query language, no database, no infrastructure.

**Scalability trigger:** Reconsider at ~500 entities or when cross-project routing errors exceed 3%.

### D5: All Filesystem, No SQLite

Proposal volume: ~90 pending max. Git auditability matters more than query speed.

**Scalability trigger:** If volume exceeds ~500 proposals (5+ months with no approvals), migrate to SQLite.

### D6: Intentional Duplication Across Tiers

The same fact can exist in DailyActivity (ephemeral, raw), MEMORY.md (agent-scoped, curated), and DDD (project-scoped, structured). Different consumers, different lifecycles, allowed overlap.

**Why:** Suppressing DDD proposals because "it's in MEMORY.md" loses the project-scoped perspective. A fact in MEMORY says "I learned X." The same fact in TECH.md says "when working on this project, X matters."

### D7: Progressive Loading (Section-Level, Not Document-Level)

Large TECH.md files (97K for SwarmAI) never load fully. Agent reads section TOC, pulls only relevant sections (~500 tokens each).

**Budget impact:** Active project ~5K + entity index ~2K + cross-project pulls ~1.5K = ~8.5K additional tokens worst-case. At 91K effective budget, DDD adds 9% overhead.

---

## What Actually Failed

### Failure 1: T2 Keyword Classifier — 100% False Negative on Production

**What happened:** Built a classifier to route corrections to the right DDD doc (TECH vs IMPROVEMENT). Tested with 29 synthetic corrections containing magic words ("daemon", "subprocess", "nc -z"). All tests green. Adversarial clean.

**Reality:** 5/5 real corrections from production returned `None`.

**Root cause:** Test data crafted by the author who wrote the regex. Real corrections are narrative behavioral ("Agent opened DMG instead of installing") with zero keyword overlap.

**Fix:** PE-1 fallback (bypass keyword gate entirely). Added RP31+RP32 to pipeline REVIEW patterns.

**Lesson:** Mock data written by the same person who wrote the matcher will ALWAYS pass. Test with real production data or don't test at all.

### Failure 2: Auto-Cultivation Hook — O(n) on No-Op Path

**What happened:** Hook passed all quality gates (TDD 7/7, adversarial HIGH fixed). Shipped.

**Reality:** Every session scanned ALL 141 `run.json` files to find... zero uncultivated runs (99% of invocations do nothing).

**Root cause:** Review stage focused on action-path correctness. Nobody analyzed the no-op path — "what happens when there's nothing to do?"

**Fix:** Added RP30 pattern rule ("hook no-op path scaling"). Time budget + mtime filter implemented.

**Lesson:** The no-op path is the most-executed path. If it's O(n), you have a systematic waste bug that's invisible to unit tests.

### Failure 3: v1 Batch-on-Close Timing Gap

**What happened:** v1 only cultivated at session close. A multi-session feature produced knowledge across 3 sessions. Session 2 and 3 lacked the context from Session 1's cultivation (hadn't run yet).

**Root cause:** Batch-on-close means knowledge is always one session behind.

**Fix:** v2 moved to event-driven. Channel 2 fires on pipeline REFLECT (immediate). Session N's output is available to Session N+1 within seconds of session close.

### Failure 4: Silent REVIEW Skip in 1029-Line Changeset

**What happened:** DDD cultivation hook extension (17 tests, 4 commits, 1029-line diff). Pipeline REVIEW stage completed via `run-update` without reading a single line.

**Root cause:** EVALUATE and PLAN were thorough, creating false confidence. REVIEW felt "already validated."

**Fix:** Check 8d added — review effort must be proportional to changeset size.

**Lesson:** Thoroughness in early stages creates a cognitive trap that makes shortcuts in later stages feel justified.

---

## Real Metrics (Measured, Not Projected)

### Weekly Output (2026-05-18)

| Metric | Value |
|--------|-------|
| Lessons auto-cultivated | 80 (36× IMPROVEMENT, 44× TECH) |
| Proposals escalated (need human) | 12 |
| Projects covered | 5 |
| False positive rate (noise entries) | ~5% (4/80 rejected on review) |
| Auto-apply accuracy | 95% (76/80 correct without intervention) |

### DDD Health (Live Dashboard)

| Project | Lines | Trust | Stale Sections |
|---------|------:|:-----:|:--------------:|
| SwarmAI | 2,090 | Full | 0 |
| CMHK_BIZ | 1,694 | Full | 0 |
| AIDLC | 888 | Full | 0 |
| GitHub_Community | 398 | High | 0 |
| PhysicalAI | 262 | Moderate | 1 (31d stale) |

### Implementation Size

| Module | Lines | Responsibility |
|--------|------:|---------------|
| ddd_orchestrator.py | 785 | 7-channel fault-isolated orchestration |
| ddd_entry_lifecycle.py | 665 | Per-entry decay + type classification |
| ddd_cultivation.py | 546 | Tiered autonomy filter + auto-apply |
| ddd_maturity.py | 488 | Section-level maturity tracking |
| ddd_health.py | 379 | 5-dimension health scoring |
| **Total** | **2,863** | |

---

## Anti-Patterns (Don't Do This)

| Anti-Pattern | Why It Fails | Do This Instead |
|-------------|-------------|-----------------|
| **Dump everything into TECH.md** | Information saturation. 10K lines where 80% is status makes the 20% invisible | Filter for judgment-worthiness: "does this help the agent DECIDE?" |
| **Auto-write without tiering** | Silent semantic edits corrupt existing knowledge. Deletion = data loss | Additive auto-apply, risky escalation. Never auto-delete |
| **Test with synthetic data only** | Author bias: you'll write data that matches your matcher | Must include ≥3 production examples in every test suite |
| **Optimize the no-op path last** | It's the MOST executed path. O(n) scans on 99% of calls = invisible waste | Time-budget every hook. Mtime filter before content scan |
| **Share DDD across team without ownership** | Without clear doc owners, contradictions accumulate silently | One owner per doc. Others propose, owner approves |
| **Build the graph DB first** | At <500 entities, the infrastructure cost > benefit. Text routing works | Start with text Entity Index. Migrate only when routing errors exceed 3% |

---

## When This Approach Breaks Down

DDD Cultivation is designed for **one builder + AI** or small teams. Honest limitations:

| Scenario | Breakpoint | What You'd Need Instead |
|----------|-----------|------------------------|
| **>500 entities** | Text routing table becomes unwieldy | Entity store with fuzzy matching |
| **>10 concurrent writers** | Git conflicts on shared docs | Section-level locking or CRDTs |
| **>50 proposals/day** | Queue fatigue, nobody reviews | ML-based auto-approve with confidence gating |
| **Real-time knowledge** | DDD updates are session-bounded (~seconds) | Event stream with sub-second propagation |
| **Cross-org knowledge sharing** | Single git repo assumption | Federation protocol between DDD instances |

We're at 5 projects, 1 builder, ~80 proposals/week. Nowhere near these limits. The design is intentionally simple for our current scale, with clear triggers for when to upgrade each dimension.

---

## The Compound Effect

DDD isn't valuable in isolation. Its value comes from feeding multiple engines simultaneously:

```
                    ┌────────────────────────────────────────┐
                    │         DDD (4 docs × 5 projects)      │
                    │    PRODUCT · TECH · IMPROVEMENT · PROJECT│
                    └───────┬──────────┬──────────┬──────────┘
                            │          │          │
                ┌───────────▼──┐  ┌────▼─────┐  ┌▼────────────┐
                │  Pipeline    │  │ Pollinate │  │  Evolution  │
                │ (code delivery)│ │(content)  │  │(self-improve)│
                └───────┬──────┘  └────┬─────┘  └┬────────────┘
                        │              │          │
                        └──────────────┴──────────┘
                                       │
                              REFLECT feeds back
                              → DDD grows
                              → next cycle smarter
```

**What Pipeline learns → makes Pollinate smarter.**
Example: Pipeline delivers a CMHK monthly report, discovers `territory_owner` column doesn't exist (TECH.md pitfall). Next week, Pollinate generates GTM content for the same BU — it won't reference territory_owner data because TECH.md now says it doesn't exist.

**What Pollinate learns → makes Pipeline safer.**
Example: Pollinate produces brand content, discovers a non-goal ("not multi-tenant SaaS" in PRODUCT.md). Pipeline won't build SaaS features because the same PRODUCT.md gates its EVALUATE stage.

This cross-pollination happens WITHOUT the engines communicating. They share a substrate. The substrate grows. Both get smarter. That's the compound flywheel.

---

## Starting From Zero (If You Want to Build This)

**Phase 1 (1 hour):** Create 4 empty docs per project. Write PRODUCT.md (vision, priorities, non-goals). Fill TECH.md with architecture decisions you already know. Leave IMPROVEMENT.md and PROJECT.md minimal.

**Phase 2 (automatic):** Work normally. After each significant session, extract 2-3 lessons into IMPROVEMENT.md ("What Failed" or "What Worked"). This is the only human effort required.

**Phase 3 (when ready):** Build the Intelligence layer. Health scoring tells you what's stale. Maturity tracking tells you what's trusted. Entry lifecycle tells you what to archive.

**Phase 4 (optional):** Build Orchestration. 7 channels automate extraction. Tiered autonomy handles the routine. You only review escalations.

Most teams will get 80% of the value from Phase 1-2 alone. The machine layers (3-4) are for when you want zero-maintenance knowledge that stays fresh indefinitely.

---

## Summary: Why DDD Cultivation Works

| Property | Why It Matters |
|----------|---------------|
| **Grows from work** | No documentation sprints. Knowledge accumulates as side effect |
| **Structured for judgment** | Not "what happened" but "what should the agent DECIDE" |
| **Self-maintaining** | Health scores detect decay. Auto-cultivation prevents it |
| **Cross-engine** | Pipeline + Pollinate + Evolution all consume the same substrate |
| **Evidence-backed trust** | Maturity tracking: sparse → growing → mature → evergreen. Agent trusts proportionally |
| **Graceful degradation** | Stale knowledge is marked, not deleted. Low trust ≠ error |
| **Zero infrastructure** | Markdown + git. No database, no graph, no vector store |

From 28 DDD sections (2026-03-24) to 110+ (2026-05-16). Zero documentation sprints. All from normal work — 8 automated channels + 1 manual habit (extract 2-3 lessons after significant sessions). The knowledge that survives is the knowledge that costs nothing to maintain.

---

*Published from SwarmAI — where 5,100 lines of living knowledge feed every decision, and the system that uses it is the same system that grows it. [Source](https://github.com/xg-gh-25/SwarmAI)*
