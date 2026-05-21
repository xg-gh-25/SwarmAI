# DDD Cultivation — domain knowledge that grows from work and cannot go stale

> 📎 [View on GitHub](https://github.com/xg-gh-25/SwarmAI/discussions/9) | Category: General | Published: 2026-05-18

---

> "维护成本 > 感知收益 — 文档必死。唯一解法：维护成本 → 零。"

## The Problem with AI + Domain Knowledge

Every AI coding tool has the same failure mode:

```
User: "Add a revenue report for CMHK"
AI: [generates technically correct code that violates every domain rule]
  - Wrong date filters (must partition by month_sequence, not calendar date)
  - Missing mandatory WHERE clauses (sh_l1='GCR' always required)
  - Wrong column names (revenue table has no 'territory_owner' field)
```

The AI is smart. It just doesn't know your project.

## Why RAG Doesn't Fix This

| Approach | Failure mode |
|----------|-------------|
| RAG (vector search) | Finds "related" docs but misses mandatory constraints |
| CLAUDE.md (single file) | Gets stale. Nobody maintains 5000 lines of instructions |
| Fine-tuning | Expensive, doesn't adapt, can't explain its knowledge |
| "Just paste context" | Works for 1 session. Gone by session 2 |

The common failure: **these approaches treat domain knowledge as static content to be retrieved, not as living infrastructure to be maintained.**

## DDD Cultivation — A Different Architecture

### Layer 1: Interface (4 documents per project)

```
PRODUCT.md    → "Should we?"  (strategy, priorities, non-goals)
TECH.md       → "How?"        (architecture, conventions, constraints)
IMPROVEMENT.md → "What failed?" (lessons, anti-patterns, won't-repeat)
PROJECT.md    → "What now?"   (status, decisions, who owns what)
```

These aren't docs. They're **judgment axes**. The AI reads them before every task and makes domain-correct decisions on the first attempt.

### Layer 2: Intelligence

- **Health scoring** — detect stale sections, contradictions, missing coverage
- **Maturity tracking** — how rich is each document? what's thin?
- **Code graph connection** — link docs to actual code symbols

### Layer 3: Orchestration (the key innovation)

**8 feed channels** that auto-grow DDD from normal work:

| Channel | Source | Target |
|---------|--------|--------|
| Code Changes | git commits | TECH.md |
| Pipeline REFLECT | post-delivery lessons | IMPROVEMENT.md |
| Pollinate REFLECT | content lessons | IMPROVEMENT.md |
| External Learning | articles, research | PRODUCT/TECH |
| Industry Signals | daily signal feed | PRODUCT.md |
| Conversation | chat decisions | PROJECT.md |
| Corrections | agent mistakes | Any (highest priority) |
| Code Intelligence | AST analysis | TECH.md |

**Zero extra human effort.** Every channel captures signals you're already producing.

## The Compound Flywheel

```
Normal work → 8 channels capture → Cultivation proposes → 30s approval → DDD richer → Next task smarter → Loop
```

No external energy needed. The flywheel is powered by work you'd do anyway.

## Results After 3 Months

| Metric | Before DDD | After (Session 100+) |
|--------|-----------|---------------------|
| Domain-correct on first attempt | ~40% | >85% |
| Repeated mistakes per month | 3-5 | ~0% |
| Cold-start time (new task) | 5-10 min context | Instant (DDD loaded) |
| Cross-project knowledge reuse | None | >60% |

## The Key Insight

> **CLAUDE.md is the starting point. DDD is the endpoint.**

A single instructions file is where everyone begins. But it doesn't scale (who maintains it?), doesn't grow (how does it learn?), and doesn't compound (where do lessons go?).

DDD Cultivation is the answer to: "How do you make domain documentation that literally cannot go stale?"

## Questions

- How do you balance "auto-growing" knowledge with quality? (Our answer: gate-based approval — 80% auto, 20% human review)
- Is 4 documents the right number? (We tried 6, tried 2 — 4 maps to the 4 questions any engineer asks before working)
- Can this work for teams, not just solo developers? (Untested. Hypothesis: shared DDD + per-person MEMORY)

---

*Full poster: [DDD Cultivation d5](https://xg-gh-25.github.io/swarm-content/content/posters/2026-05-16-ddd-cultivation-d5.png)*

![DDD Cultivation](https://raw.githubusercontent.com/xg-gh-25/swarm-content/main/content/posters/2026-05-16-ddd-cultivation-d5.png)
