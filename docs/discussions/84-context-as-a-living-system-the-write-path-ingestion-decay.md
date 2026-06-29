---
title: "Context as a Living System — The WRITE Path: Ingestion, Decay, Archive"
created: 2026-06-29
updated: 2026-06-29
status: published
---
<!-- GitHub Discussion #84: https://github.com/xg-gh-25/SwarmAI/discussions/84 -->
> 🌐 English | 中文版 → #85 · Series: #86 Quality Convergence · #88 Cognitive Evolution

# Context as a Living System — The WRITE Path

> Sibling to our Recall Architecture posts (#79/#80), which deliberately covered
> only the READ path. This one is the other half: how knowledge gets *in*, how it
> *decays*, and how it gets *archived* — without a human curator and without a
> vector database.

## The thing nobody writes about

Every "AI memory" post is about retrieval. Embeddings, rerankers, hybrid scoring,
RAG. That's the READ path — and it's the easy half. The hard half is the WRITE
path: **what earns a place in memory, what loses it, and what gets swept out.**
A memory system with a great retriever and no decay discipline becomes a landfill
with a good search bar. Within weeks every query returns 40 "relevant" hits, 35 of
which are stale, and the signal drowns.

We run a self-evolving agent OS 24/7. Its memory has grown for months. Here's the
WRITE path that keeps it from rotting.

## Three stages: Ingestion → Decay → Archive

### 1. Ingestion — confident-only, never speculative

The first gate is **what gets written at all.** Our rule: knowledge is extracted
only from *confident, verified* outcomes — never from a "this might be a pattern"
hunch. A correction that recurred 3× is a pattern; a one-off is noise. The
extraction hook is biased toward **False > Stale > Imperfect**: we'd rather drop a
real insight than admit a fabricated one, because a wrong entry in long-term memory
poisons every downstream decision that trusts it.

Each entry is typed (one of 7: principle / correction / decision / guideline /
pitfall / process / model) and carries inline metadata as an HTML comment:

```
<!-- ref:3 | last:2026-06-28 | decay:active | source:auto -->
```

No database. The markdown file *is* the store. The metadata travels with the text.

### 2. Decay — Darwinian, per-section, automatic

Entries age. `ref_count` bumps when an entry is actually referenced; `days_idle`
grows when it isn't. The decay state machine:

| State | Trigger |
|-------|---------|
| active | referenced recently, or < 30d old (immune) |
| dormant | 90d idle (180d if ref_count ≥ 10) |
| archived | 180d idle |

The subtlety we learned the hard way: **decay rate must be per-section, not
global.** MEMORY.md churns fast (session-to-session context) — it ages at **45
days**. Domain knowledge churns slow — **90 days**. One global threshold either
keeps MEMORY bloated or prematurely archives stable knowledge. (commit `2ece119d`)

Decay is **weighting, not deletion.** A dormant entry still exists; it just sinks
in recall ranking and stops competing for the context-window budget.

### 3. Archive — shrink, with proof

At 180d idle an entry moves to a dated archive file. The active document shrinks;
history is never lost, just relocated. We gate this with an **E2E shrink proof** —
a test that drives a real entry through decay→archive and asserts the active doc
actually got smaller (commit `35c73555`). "It should shrink" is a hypothesis;
"the test proves it shrank" is the contract. (We've been burned enough times by
recovery paths that *looked* wired but never executed.)

## The plot twist: we deleted the vector database

Here's the part that'll be controversial. Last week we **tore the vector embedding
leg out of recall entirely** (commit `6540970e`). Titan embeddings, sqlite-vec, the
`0.6·vector + 0.4·BM25` hybrid — all gone. Recall is now **pure keyword/FTS5 over
markdown.**

Why? Three reasons, all measured, not guessed:

1. **The vector leg cost more than it returned.** Empirical probe: warm embed
   latency was fine (~0.4s), but it added a Bedrock network dependency on the
   cold-start critical path and a write-side cost (every entry embedded) — for a
   corpus that fits in the context window.
2. **When the corpus fits, the context window IS the database.** Under ~100K
   tokens, full injection beats RAG. You don't retrieve-then-reason; you just
   reason over everything. The vector index was solving a scale problem we don't
   have.
3. **A dead leg lies.** Half the "hybrid" was already unwired in the injection path
   and nobody noticed — the code comments still claimed `allow_embed=True` and
   "both legs" long after they were inert (we killed that lying comment in
   `50024c79`). Legibility decayed faster than anyone read it. Simpler is more
   honest.

This is the WRITE-path lesson stated as a principle: **every mechanism you add to
the write path is a mechanism you must keep honest.** A vector index you're not
load-bearing on is not a feature — it's a second source of truth waiting to drift.

## What this composes into

```
        WRITE                          READ
  ┌──────────────────┐         ┌──────────────────┐
  │ Ingestion gate   │         │ Keyword / FTS5    │
  │ (confident-only) │         │ over markdown     │
  └────────┬─────────┘         └─────────┬─────────┘
           │ typed entry + inline meta    │ entry-level BM25
           ▼                              ▼
  ┌──────────────────┐         ┌──────────────────┐
  │ Decay (per-sect) │ ──────► │ ranking weight    │
  │ 45d / 90d / 180d │         │ (dormant sinks)   │
  └────────┬─────────┘         └──────────────────┘
           ▼
  ┌──────────────────┐
  │ Archive (shrink, │
  │  proof-gated)    │
  └──────────────────┘
```

The WRITE path is what makes the READ path stay sharp over time. Retrieval quality
is a *lagging indicator* of write discipline. If recall feels noisy, don't tune the
retriever — audit what you let in and what you refuse to let go.

## Takeaways

- Memory without decay is a landfill with search. Decay is the feature.
- Make decay **per-section** — different knowledge churns at different rates.
- Decay = down-weighting, not deletion. Archive = relocation, not loss.
- Bias ingestion toward **False > Stale > Imperfect.** A wrong entry poisons trust.
- If your corpus fits the context window, you may not need a vector DB at all — and
  the one you have may be quietly lying to you.
