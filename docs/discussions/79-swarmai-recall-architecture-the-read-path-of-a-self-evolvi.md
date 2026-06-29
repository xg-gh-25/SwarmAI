---
title: "SwarmAI Recall Architecture — The READ Path of a Self-Evolving Agent OS"
created: 2026-06-26
updated: 2026-06-26
status: published
---
<!-- GitHub Discussion #79: https://github.com/xg-gh-25/SwarmAI/discussions/79 -->
# SwarmAI Recall Architecture — The READ Path of a Self-Evolving Agent OS

> _A deep dive into how SwarmAI remembers, retrieves, and decides what to surface — the
> 5-subsystem recall fabric, the Memory/Knowledge/DDD positioning, the design philosophy
> behind "from hard drive to OS", and the external work that shaped it._
>
> Companion to [Discussion #59 — DDD Knowledge Governance (7-type MECE + Darwinian decay)](https://github.com/xg-gh-25/SwarmAI/discussions/59).
> Verified against live code 2026-06-27 (run_1d198980 follow-up). **Built vs Designed is marked throughout — we do not sell the roadmap as the product.**

---

## TL;DR

1. **Recall is not one system.** It is **5 independent recall subsystems** (Knowledge/Library, Memory, Session, Transcript, CodeIntel) — each owns its own storage + retrieval algorithm — plus **1 read-only aggregator** (`recall_multi`). They inject at **two distinct moments**, not one.
2. **The formula in the code is not always the behavior in production.** Knowledge/Library runs true hybrid (vector+keyword) live; Memory's identical hybrid formula exists but is *unwired* — keyword-only in prod. We say this out loud because conflating the two is the exact bug class (R16b) our own rules warn against.
3. **Memory ≠ Knowledge ≠ DDD.** Three layers with one routing question: *"换一个项目，这条经验还有用吗?"* Cross-project → MEMORY; reference fact → KNOWLEDGE; project judgment → DDD.
4. **The philosophy is Darwinian, not encyclopedic.** *"能遗忘的系统比只能记住的系统更强。"* Knowledge has `ref_count`, decay, and a `dormant→archived` lifecycle. 90 days unreferenced = auto-exit.
5. **The hard problem is not storage — it is APPLY.** *"It is a hard drive I rarely read, with a self-test that grades the wrong thing."* The recall fabric exists to close the loop between "recorded a lesson" and "the mistake stopped."

---

## 1. The Mental-Model Correction: Recall Is Not One System

The most common misconception — including ours, until we traced it — is that an agent has *"a memory."* SwarmAI has **five**, each with its own store, its own retrieval algorithm, and its own injection timing. Trying to reason about "the recall system" as a monolith produces wrong fixes (we shipped 33 patches at the wrong layer for a different recurring bug; the lesson generalizes).

```
                    SwarmAI Recall Fabric (the READ path)

  ┌──────────────── 5 INDEPENDENT SUBSYSTEMS ────────────────┐   ┌─ 1 AGGREGATOR ─┐
  │ Knowledge/Library   Memory     Session   Transcript  CodeIntel │   recall_multi   │
  │ FTS5+vector hybrid  hybrid*    msgs_fts  trans_fts   sym-graph  │   (read-only,    │
  │ (LIVE)              (*unwired) BM25      +vec        +1hop      │    bucketed)     │
  └───────────────────────────────────────────────────────────────┘   └──────────────┘
            ▲                                                    ▲
            │ injected at TWO moments (not one):                 │
   ① session start (assembled prompt)              ② after 1st user message
      • 11 context files (MEMORY selective)           (async, 150ms, desktop)
      • Resume context 20–150K                        • Knowledge hybrid recall (8K)
        └ calls NO recall subsystem —                   • on the REAL query, not a
          mechanical DB extract                           pre-guessed keyword set
```

Expanding the **two injection moments** on their own — this is the single most
misunderstood part:

```
                   ┌─────────────────────────────────────────────┐
                   │      RECALL's TWO injection moments           │
                   └─────────────────────────────────────────────┘

 Session start (system-prompt assembly)     After the 1st user message (150ms async)
 ──────────────────────────────────────     ────────────────────────────────────────
 • 11 context files                         • Knowledge Library recall (8K)
   └ MEMORY.md selective injection            └ KnowledgeStore (FTS5+vector)
 • Resume context (20-150K)                   └ TranscriptStore (verbatim turns)
   └ checkpoint + conclusions +               └ Knowledge Graph entity expansion
     tool results + last 30 turns            (recall on the REAL query, not
   (NOTE: resume calls NO recall               pre-guessed keywords)
    subsystem — mechanical extract
    straight from DB messages)
```

---

## 2. The Architecture Panorama

### 2.1 The five subsystems (and the wiring truth)

| Subsystem | Storage | Algorithm (in code) | **Wiring truth (in prod)** | Owner files |
|---|---|---|---|---|
| **Knowledge/Library** | `knowledge_chunks` + `knowledge_fts` (FTS5 external-content) + `knowledge_vec` (sqlite-vec, 1024-d Titan v2) | Hybrid `0.6·vector + 0.4·BM25`, threshold 0.05 | ✅ **Hybrid LIVE** — `session_router` passes `embed_fn`; vector active when Bedrock up, graceful FTS5-only fallback | `knowledge_store.py`, `recall_engine.py`, `embedding_client.py` |
| **Memory (MEMORY.md)** | `memory_entries` + `memory_vec` (sqlite-vec) + inline HTML-comment decay metadata | Hybrid `0.6·vector + 0.4·BM25` + decay weighting, threshold 0.10 | ⚠️ **Keyword-only** — caller omits `memory_embeddings` (defaults `False`); hybrid is *built-but-unwired*. And MEMORY.md ~15K < 30K threshold → **full injection**, so the section-selection scorer doesn't even run today | `memory_index.py`, `memory_embeddings.py`, `context_recall.py`, `memory_decay.py` |
| **Session (messages)** | `messages_fts` (FTS5 external-content) | FTS5 BM25 + mix-rank `density·0.4 + recency·0.35 + richness·0.25`, ±10-msg window | ✅ Live; filters `sent=0` so an undrained pending message is never injected as phantom context | `session_recall.py` |
| **Transcript (verbatim)** | `transcript_chunks` + `transcript_fts` (FTS5) + `transcript_vec` (sqlite-vec) | FTS5 + optional vector, delta-sync by `content_hash` | ✅ FTS5 live; vector when `embed_fn` passed | `transcript_indexer.py` |
| **CodeIntel** | code graph (symbol FTS) | symbol FTS + 1-hop caller enrichment | ✅ graph live | via `recall_multi._codeintel_recall` |

**Index inventory:** 4 independent FTS5 tables (`knowledge_fts`, `messages_fts`, `transcript_fts`, code-symbol FTS) + 3 sqlite-vec tables (`knowledge_vec`, `memory_vec`, `transcript_vec`).

### 2.2 The aggregator: `recall_multi`

A **read-only** facade that fans out across five domains and returns a *bucketed* result:

```python
recall_all(query, domains=["context_files","ddd","library","session","codeintel"])
  → BucketedRecall{ buckets, hit_layers }
```

Two load-bearing safety properties:
- **`allow_embed=False` by default** → zero Bedrock embeds, zero writes. (So Library degrades to FTS5-only when reached *via the aggregator* — the hybrid path is the direct `session_router` route.)
- **`policy_excluded_files` privacy gate propagates across ALL domains** — this closed a real leak where `--domains` could bypass the privacy that `--file` enforced (caught by an adversarial gate, run_4358cc95).

### 2.3 The injection asymmetry that surprises people

**Resume context calls no recall subsystem.** When a session resumes (20–150K tokens of context), `context_injector.build_resume_context()` mechanically extracts checkpoint / assistant-conclusions / key-tool-results / last-30-turns straight from the DB `messages` table. It does **not** call `session_recall`, `knowledge_store`, or `memory_index`. Recall is *augmentation*, never on the resume critical path. This is deliberate: resume must be deterministic and offline-safe; recall is best-effort and can fail without breaking continuity.

---

## 3. Positioning: Memory vs Knowledge vs DDD

Three layers, one routing question. From `s_persist`'s routing tree, the decisive test is:

> **"换一个项目，这条经验还有用吗?"**
> _If I switch projects, does this lesson still apply?_
> **YES → MEMORY.md** (cross-project cognitive knowledge) · **NO → Projects/<X>/...** (project-scoped DDD)

| Layer | What it is | Where | Purpose |
|---|---|---|---|
| **MEMORY** | Cross-session recall — *"what I did, what the user said, what worked last time"* | `MEMORY.md`, `DailyActivity/`, `EVOLUTION.md` | Working memory, session continuity, pattern detection |
| **KNOWLEDGE** | Reference facts — *"how things work, what I should know"* (not behavior-specific) | `KNOWLEDGE.md` + `Knowledge/` library | The textbook: architecture reference, domain facts, learned external material |
| **DDD** | Project expertise — *"what matters for THIS project"* | Per-project `PRODUCT.md` / `TECH.md` / `IMPROVEMENT.md` / `PROJECT.md` | Judgment ("should we?"), design ("how?"), context ("what's unique here?") |

### 3.1 The 7-type knowledge governance (MECE + Darwinian)

Every stored entry is one of **7 mutually-exclusive, collectively-exhaustive types** ([PRI01, Discussion #59](https://github.com/xg-gh-25/SwarmAI/discussions/59)):

`principle` · `correction` · `decision` · `guideline` · `pitfall` · `process` · `model`

The taxonomy isn't decoration — it drives *routing* (where a new entry lands) and *lifecycle* (how it decays). Critically, **WHERE and WHAT/lifespan are kept separate on purpose**:

> *"`persist_routing` and `ddd_entry_lifecycle` are TWO CORRECTLY-SEPARATE concerns... `persist_routing` = WHERE new knowledge goes. `ddd_entry_lifecycle` = WHAT a stored entry is (its 7-type) and how long it lives (ref_count → decay). Keeping them separate is CORRECT design, not a defect."_ — Ingestion Governance design (2026-06-26)

### 3.2 Darwinian decay: knowledge must eliminate itself

> **知識必須自己淘汰自己。積累不是智慧。** 達爾文進化的核心不是"記住更多"，是"淘汰不適應的"。我們的知識有 ref_count、有 decay、有 dormant→archived 生命週期。**90 天不被引用 = 自動退場。不靠人 maintain，靠使用頻率自然選擇。能遺忘的系統比只能記住的系統更強。** — PRODUCT.md, Design Philosophy

Mechanically: entries carry `<!-- ref:N | last:DATE | decay:STATE -->`. Decay follows an Ebbinghaus-style curve with Hebbian potentiation — `ref_count` and access *spacing* extend stability; idle entries slide `active → dormant → archived`. Superseded entries score `0.1×` in selection rather than being deleted. The design principle that makes it work:

> *"Mechanical > Aspirational. 'Every quarter review knowledge base' will be ignored. 'Every day auto-decay' won't be."_ — Lightweight Ontology writeup

---

## 4. Design Philosophy

### 4.1 From Hard Drive to OS

The framing that organizes the whole system:

> **認知是操作系統，知識是硬盤。硬盤滿了但 OS 有 bug = 輸出仍然錯。我們打的是 OS 補丁。**
> _Cognition is the operating system; knowledge is the hard drive. A full disk with a buggy OS still produces wrong output. We ship OS patches._ — PRODUCT.md

And the honest diagnosis that motivated the recall work:

> *"I have a rich self-knowledge corpus... but the corpus doesn't reach my working memory, the eval scores 100 while I repeat mistakes, and nothing closes the loop between 'recorded a lesson' and 'the mistake stopped.' **It is a hard drive I rarely read, with a self-test that grades the wrong thing.**"_ — Self-Knowledge Loop design (2026-06-25)

### 4.2 The 6-link circuit — recall is one link, not the goal

Recall (link ④) only matters if current flows all the way around with a *falling correction-count*:

```
 ① EXTRACT ─→ ② CLEAN ─→ ③ INJECT ─→ ④ RECALL ─→ ⑤ APPLY
 (work→lesson) (prune/    (→ prompt)  (right one,  (CHANGES the
                archive)              right time)   next action)
     ▲                                                  │
     └──────────────── ⑥ MEASURE (the gauge) ◄──────────┘

 LAWS:
 • A link you cannot MEASURE is assumed BROKEN.
 • Mechanical signals are PRIMARY; LLM-judgment is ADVISORY; a human anchor calibrates both.
 • Proof of "I use the knowledge" is MECHANICAL (gate-fire logs, falling
   correction-count, replayed-case pass) — never a verbal promise.
```

The empirically broken link is **⑤ APPLY** — not storage, not retrieval. A lesson can be in context and still not change behavior. That is why SwarmAI leans on *mechanical gates* over reminders.

### 4.3 Reversible, never drop

A principle we adopted explicitly, and a mistake we watched someone else make and retire:

> *"Reversible, never drop: Headroom retired score-and-drop because silent loss erodes trust + breaks caches. We keep the original in the store; the model gets a handle and can recall the full output on demand."_ — Context Economy design (2026-06-26)

Corollary for retrieval quality: **recall must never summarize** — *"NO summarization — that is silent 降智 (intelligence-degradation), forbidden."* Recall returns a **reversible exact slice**, scoped to a `## section`, queried by content (never by a stale offset that distillation would invalidate).

### 4.4 Measurement is reality

> **測量不了的，等於沒造。** 沒有度量的"自我改進"是故事。不聲稱收斂——用 git 裡的數據證明。
> _What you can't measure, you didn't build. "Self-improvement" without metrics is a story. We don't claim convergence — we prove it with data in git._ — PRODUCT.md

---

## 5. Methodology — How We Decide What to Recall

### 5.1 The recall chain (target architecture, partially built)

```
1. INDEX-FIRST   — read the resident cache+index (Karpathy LLM-wiki pattern)
2. DRILL         — progressive, on-demand, multi-domain parallel; unit = ## section
3. RETRIEVE      — reversible EXACT-SLICE (verbatim; never paraphrase/summarize)
4. PRESENT       — per-domain buckets; cross-domain associations noted
5. EMIT HIT-LOG  — {query, hit-layer(hot|index|drill), section, domain, drilled?}
```

### 5.2 Cross-domain ranking is BUCKETED, never globally mixed

> *"Per-domain relevance scores are not comparable (different magnitudes / corpora) — a global mixed rank lets one keyword-dense domain drown a more relevant one... equal per-domain quota (ceiling / N_active), buckets ordered by activation order, NOT by any cross-domain score."_ — E2E Recall design

### 5.3 Keyword and vector are COMPLEMENTARY — the lever is commensurability, not the ratio

We ran a spike (12 synonym/CJK-shifted queries against the live `memory_vec`) before tuning anything:

> *"Decisive conclusion: keyword and vector are COMPLEMENTARY, neither dominates. Exact-term queries → keyword wins; conceptual/CJK queries → vector wins; no single leg is adequate. **The lever is making the two legs commensurable, NOT tuning the ratio.**"_ — E2E Recall design

And a cargo-cult guard, because borrowing a magic number without its machinery is meaningless:

> *"⚠️ The 0.6/0.4 number is ONLY valid bundled with the three things MemPalace validated it WITH — port all three or the ratio is meaningless: (1) real Okapi-BM25+IDF keyword leg, (2) min-max normalize ONLY the BM25 leg; keep the vector leg ABSOLUTE, (3) missing vector → renorm to the available leg, NOT score 0."_

That last rule — **a missing vector renormalizes to the available leg, never scores 0** — is what makes *lazy embedding* safe.

### 5.4 Lazy per-surface embedding, not a big-bang index

> *"LAZY PER-SURFACE EMBEDDING on the read path... A DDD section or KNOWLEDGE entry is embedded the first time recall drills/hits it (signal = the hit-log) — not by a batch index-everything job. The corpus of embedded units grows along the access frontier, so semantic coverage expands exactly where queries actually land, never as an up-front sweep. This is the same Darwinian 'warm what's used' principle as the L1 cache."_ — E2E Recall design

### 5.5 The justification is capability, NOT context savings

We were explicit about *not* solving a problem we don't have:

> *"❌ NOT context-window savings. We run 1M context at ~50% utilization; truncation has never fired in production. Any 'compress to save context' rationale is the trap of adopting [a tool's] solution for a problem we don't have. ✅ The real reasons: cross-domain capability (absent today), retrieval-quality ceiling (keyword-only has no synonym/semantic recall), DDD legibility ('被用的时候就应该很容易提取')."_ — E2E Recall design

### 5.6 Compress at produce-time, never retroactively

For tool-output compression (a sibling READ-path concern), the cache-safety rule:

> *"PostToolUse compression happens BEFORE the output is ever cached — the compressed form is what gets written the first time. There is no cached prefix to invalidate → zero cache penalty. The design must compress at produce-time, never retroactively."_ — Context Economy design

---

## 6. Built vs Designed (the honesty section)

We refuse to present the roadmap as the product.

| Capability | Status |
|---|---|
| Knowledge/Library hybrid recall (FTS5 + vector), live `embed_fn`, graceful fallback | ✅ **Built + deployed** |
| `knowledge_fts` external-content corruption root-fix + auto-heal probe | ✅ **Built + deployed** (run_1d198980, this is what triggered this writeup) |
| Session / Transcript FTS5 recall; CodeIntel symbol graph | ✅ **Built** |
| `recall_multi` read-only 5-domain aggregator + privacy gate | ✅ **Built** |
| `recall_context` reversible section-scoped recall for excluded MEMORY sections | ✅ **Built** |
| Memory selective-injection **hybrid** (vector leg) | ⚠️ **Built but UNWIRED** — keyword-only in prod; also dormant because MEMORY.md < 30K → full injection |
| Cross-domain *semantic* recall everywhere; lazy per-surface embedding; persisted hit-log driving Darwinian decay | 📐 **Designed, partial** — the e2e design exists; full wiring is the open work |
| Tool-output compression (Read+Bash, produce-time, reversible) | 📐 **Designed** (spike done, BUILD verdict, not yet shipped) |

**Two known debts, flagged not hidden:**
1. `transcript_indexer.upsert_chunk` carries the **same external-content write-bug class** that corrupted `knowledge_fts` (FTS5 `'delete'` must bind OLD stored values). Separate table → separate run.
2. **Two independent implementations of the same `0.6·vector + 0.4·keyword` hybrid + min-max renorm** (Knowledge in `recall_engine.py`, Memory in `memory_embeddings.py`). Duplicate logic → merge candidate.

---

## 7. Reference Sources — What Shaped This

| Source | What it is | Link | We borrowed | We rejected / differ |
|---|---|---|---|---|
| **Karpathy — LLM Wiki** | Incrementally build & maintain a persistent, interlinked wiki instead of RAG-per-query. Three layers: raw sources → wiki → schema. Lineage: Vannevar Bush's **Memex** (1945). | [gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) | "index-first → drill-on-demand → file-back"; persistent-compound > RAG; the 3-layer mapping (Knowledge/ → MEMORY+EVOLUTION+DDD → AGENT/SOUL/STEERING) | — |
| **MemPalace** | Validated hybrid rank: `0.6 vector / 0.4 keyword` with Okapi-BM25+IDF, min-max on the BM25 leg only, missing-vector renorm. | _(recipe ported)_ | The **full recipe**, not just the number (see §5.3) | Porting the ratio without its 3 machinery pieces (explicit cargo-cult guard) |
| **Headroom** | Local-first context-compression layer; entropy preservation; cache-aligner; **retired score-and-drop** in favor of reversible live-zone compression. | [github](https://github.com/headroomlabs-ai/headroom) | "Reversible, never drop"; entropy-preservation (never split `run_*` / SHAs / paths); cache-stable prefix | Compression itself — we run 1M @ ~50%, no context pressure (adopting their fix would be solving a problem we don't have) |
| **Lightweight Ontology** (internal) | Darwin-vs-Encyclopedia knowledge model; MECE type schema + decay lifecycle + relation layer, in ~1000 lines, no graph DB. | _(internal writeup)_ | The type schema + decay lifecycle rules; "Mechanical > Aspirational" | Neo4j/Neptune overhead — YAML relations + Markdown schema instead |
| **Ontology vs Knowledge Graph** | Ontology = schema/rules layer; KG = data/instance layer (DDL vs DML analogy). | [article](https://www.toutiao.com/article/7618030452531610164/) | Schema-before-data; DDD-4-doc ≈ lightweight ontology | Formal OWL / SPARQL complexity |
| **Amazon Quick — Desktop KG** | Personal knowledge graph from Slack/Email/files in local SQLite (not Neo4j); 10 entity types; PageRank for hierarchy. | [aws docs](https://docs.aws.amazon.com/quick/latest/userguide/knowledge-graph-desktop.html) | SQLite-over-graph-DB at small scale; "Defined Term" auto-glossary idea | Multi-person org graphs / passive ingest — different problem class (read-only enrichment vs read-write-evolve) |

---

## 8. Open Questions (we'd genuinely like input on)

1. **Is keyword-only recall actually missing real queries?** Our own gate is allowed to return NO: build the semantic-everywhere path *only if* ≥20% of real queries show a keyword false-negative that hybrid correctly catches, with hybrid false-positive < 10%. Below that → keyword is adequate, don't build. How would you measure this on your own corpus?
2. **Should the two hybrid implementations merge** before or after the semantic-everywhere wiring? (Merge-first is cleaner but touches a deployed path.)
3. **Where does the hit-log live**, and does a persisted hit-log driving decay risk a feedback loop (popular entries get more embedded → more recalled → more popular)?

---

_Built in the open. The architecture above is the READ path; the WRITE path (ingestion governance, 7-type routing, Darwinian decay) is [Discussion #59](https://github.com/xg-gh-25/SwarmAI/discussions/59). Recall is one link in a 6-link circuit — and a link you can't measure is assumed broken._

🐝 SwarmAI — Your AI Team, 24/7. Human directs, AI delivers.

