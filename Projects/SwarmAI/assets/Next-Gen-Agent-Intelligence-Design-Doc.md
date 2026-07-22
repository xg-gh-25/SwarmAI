---
title: "Next-Gen Agent Intelligence — Technical Design Document"
date: 2026-04-15
author: XG (architecture), Swarm (synthesis)
status: PE-review-ready
audience: AWS Internal PEs, Technical Architects
tags: [self-evolution, memory-safety, user-modeling, skills, agent-intelligence]
toc: true
toc-depth: 3
numbersections: true
geometry: margin=1in
fontsize: 11pt
---

# Next-Gen Agent Intelligence

## Executive Summary

SwarmAI wraps Claude's Agent SDK inside a **harness** — a structured layer of context management, persistent memory, self-evolution, and safety controls that transforms a stateless large language model into a persistent, evolving personal AI agent.

This document describes the four intelligence subsystems that make that evolution real: **self-evolution** (the agent improves its own skill instructions), **memory safety** (every write path is guarded against secrets and injection), **user modeling** (the agent learns how the user works), and **skill management** (61 skills with lazy/always tiering and autonomous improvement).

The design draws from Hermes Agent (NousResearch, 41K GitHub stars) for the optimizer architecture, MemPalace (21.6K stars) for verbatim memory validation, and gstack (Garry Tan, YC CEO) for sequential pipeline orchestration — but every module is built from scratch to leverage SwarmAI's existing architectural strengths (DDD judgment, 11-file context chain, filesystem-as-repository).

**Key metrics (April 2026):**

| Metric | Value |
|--------|-------|
| Intelligence modules | 19 files, 9,026 lines |
| Test coverage | 291+ tests, 0 regressions |
| Skills | 61 (15 always, 46 lazy) |
| Evolution pipeline | 4-phase (MINE->ASSESS->ACT->AUDIT), confidence-gated |
| Memory guard coverage | 100% of MEMORY.md write paths |
| Skill fitness dimensions | 3 (correctness 30%, procedure 30%, containment 40%) |

**Design principle:** Power over token budget. Every design decision optimizes for maximum recall, deepest understanding, and strongest capability — never for token savings. SwarmAI has a 1M context window; the primary goal is powerful function.

---

## 1. The Problem: Open-Loop Evolution

Before this work, SwarmAI's evolution was **open-loop**:

```
Detect gap -> Record in EVOLUTION.md -> Human reads -> Human fixes
                                       ↑ break here
```

The agent could detect problems (4 trigger types: reactive, proactive, stuck, correction), record them (EVOLUTION.md entries with VFM scoring), and even propose fixes. But the loop was open — detected problems required human review before any improvement could be applied.

Additionally:

| Problem | Consequence |
|---------|-------------|
| Memory writes unguarded on 4 of 5 paths | Secrets could be persisted; injection possible |
| No observation of user behavior | User re-explains preferences every session |
| Skill quality unmeasured | No data on which skills need improvement |
| No trust boundary for skills | Agent-created skills treated same as built-in |
| Session context lost between conversations | Every session starts from scratch |

**The insight from Hermes Agent:** Evolution that reads execution traces to understand WHY things fail, then makes targeted mutations — not random search. The key is closing the loop: observe -> assess -> act -> audit.

---

## 2. Architecture Overview

![Four-Phase Architecture](diagrams/01-four-phase-architecture.png){width=100%}

The system is organized into four implementation phases, each delivering independently useful value:

| Phase | Name | Modules | Purpose |
|-------|------|---------|---------|
| **1** | Safety + Observability | MemoryGuard, SkillMetrics, SectionCaps, EntryRefs | Guardrails before automation |
| **2** | Understanding + Recall | UserObserver, SessionRecall, SkillRegistry, SkillGuard | Know the user, find context |
| **3** | Autonomous Evolution | SessionMiner, SkillFitness, EvolutionOptimizer, RetentionPolicies | Close the loop |
| **4** | E2E Hardening | 13-file cross-cutting fix | Wire everything correctly |

Phase 1 was built first because safety must precede autonomy. Phase 4 was a critical lesson: 206 unit tests couldn't catch 3 critical wiring gaps that only E2E caller->callee tracing revealed.

---

## 3. Phase 1 — Safety + Observability

### 3.1 MemoryGuard (`core/memory_guard.py`, 179 lines)

Every write to MEMORY.md, USER.md, EVOLUTION.md, and DailyActivity passes through the MemoryGuard scanner.

**Scan categories:**

| Pattern | Action | Examples |
|---------|--------|----------|
| Secrets | Auto-redact with `[REDACTED]` | `AKIA[0-9A-Z]{16}`, `sk-[a-zA-Z0-9]{20,}`, `-----BEGIN PRIVATE KEY-----` |
| Prompt injection | Reject write + log warning | `ignore previous instructions`, `you are now`, `<\|special_token\|>` |
| Role hijack | Reject write + log warning | `act as if you are`, `new identity` |
| Exfiltration | Reject write + log warning | `curl` with secrets, `scp` with keys |
| Invisible chars | Silently strip | Zero-width characters (U+200B–U+200F, U+FEFF) |

**Integration architecture:**

The primary chokepoint is `locked_write.py` — the flock-based concurrent write protection module. MemoryGuard is called before every `locked_write()` invocation.

But four bypass paths exist where modules use their own flock-and-write pattern to avoid nested-lock deadlock:

1. **Distillation hook** — promotes DailyActivity entries to MEMORY.md
2. **Context health hook** — refreshes MEMORY.md index
3. **Memory health job** — weekly Brain audit
4. **Evolution maintenance hook** — updates EVOLUTION.md

Each bypass path has an inline `MemoryGuard().sanitize()` call added during the E2E hardening phase (Phase 4). This was a critical finding — a chokepoint only works if ALL traffic goes through it.

### 3.2 SkillMetrics (`core/skill_metrics.py` + `hooks/skill_metrics_hook.py`, 468 lines)

Post-session hook that scans the message history for skill invocations and user corrections.

**Detection logic:**

1. Scan assistant messages for `Using Skill:` patterns -> record invocation
2. Scan subsequent user messages for correction patterns (`don't`, `instead`, `wrong`, `fix`) -> record correction
3. Persist to `skill_metrics` SQLite table: `{skill_name, session_id, outcome, duration, user_satisfaction}`

**Key method:** `get_evolution_candidates()` returns skills where:
- `correction_rate > 30%` AND `invocation_count >= 5`, OR
- `success_rate < 70%` AND `invocation_count >= 5`

This feeds the evolution pipeline's skill selection.

### 3.3 SectionCaps (embedded in `hooks/distillation_hook.py`, ~120 lines)

Enforces entry limits per MEMORY.md section to prevent unbounded growth:

| Section | Max Entries | Overflow Action |
|---------|-------------|----------------|
| Recent Context | 30 | Archive oldest to `Knowledge/Archives/MEMORY-archive-YYYY-MM.md` |
| Key Decisions | 30 | Archive oldest (flag if referenced by Open Thread) |
| Lessons Learned | 25 | Merge similar lessons before archival (keyword intersection >60%) |
| COE Registry | 15 | Never archive — each prevents a class of incidents |
| Open Threads | 10 | Resolved threads -> archive after 7 days |

**Merge-before-archive:** When archiving Lessons Learned, the distillation hook checks for semantic overlap. If two entries share >60% keyword intersection, they're merged into a single entry with combined keywords. This keeps the most information in the smallest footprint.

Archive format preserves full entry text in `Knowledge/Archives/`. MEMORY.md gets a one-line reference: `[Archived] See Knowledge/Archives/MEMORY-archive-2026-04.md`.

### 3.4 EntryRefs (embedded in `core/memory_index.py`, ~80 lines)

Cross-references between MEMORY.md entries, extracted during index generation:

```
- [KD15] 2026-03-19 No regressions from re-architecture | refs: COE02, RC15
```

**`_extract_refs()` logic:**
- Pattern match for entry IDs in text: `COE[0-9]+`, `KD[0-9]+`, `RC[0-9]+`, `LL[0-9]+`, `OT[0-9]+`
- When L1 injection loads an entry, also load all `refs:` targets (1-hop graph traversal)
- Capped at 3 referenced sections to control token budget
- Ensures related context travels together — a COE and its resolution are always co-loaded

---

## 4. Phase 2 — Understanding + Recall

### 4.1 UserObserver (`core/user_observer.py` + `hooks/user_observer_hook.py`, 355 lines)

Post-session hook that extracts user behavioral patterns from the conversation.

**Observation categories:**

| Category | Subcategory | Example |
|----------|-------------|---------|
| `preferences` | `communication` | "User switches to Chinese when discussing org politics" |
| `preferences` | `workflow` | "User always tests after coding, commits manually" |
| `preferences` | `technical` | "User favors functional programming patterns" |
| `expertise` | `domains` | "Expert in distributed systems" |
| `expertise` | `tools` | "Deep pytest expertise — catches xdist edge cases" |
| `expertise` | `languages` | "Python daily, TypeScript occasionally, Go for CLI tools" |
| `corrections` | — | "Don't use bullet point recaps at the end" |

**Two-tier storage:**

| Tier | Location | System Prompt | User Can Edit |
|------|----------|---------------|---------------|
| **Public profile** | `.context/USER.md` | Yes (injected) | Yes (edit directly) |
| **Raw observations** | `.context/user_observations.jsonl` | No (privacy) | Yes ("forget X") |

**Update flow:**
1. Post-session: hook extracts observations with confidence scores
2. When 5+ observations converge on a category: write suggestions to `.context/user_suggestions.md`
3. Prompt builder injects suggestions into system prompt at session start
4. User approves/rejects — never auto-writes to USER.md without asking

### 4.2 SessionRecall (`core/session_recall.py`, 293 lines)

FTS5-based full-text search across all past session messages in the SQLite database.

**Ranking algorithm:** `score = density × recency × richness`

- **Density:** How many query terms appear in the session window
- **Recency:** Exponential decay — recent sessions rank higher
- **Richness:** Sessions with tool_use blocks and code rank higher than pure text

**Key design decision:** SessionRecall runs *alongside* keyword-matched memory sections, not as a fallback. The original design (Phase 2) gated recall behind `if not sections_to_load` — meaning recall only fired when keyword matching failed. E2E review (Phase 4, finding F7) fixed this: supplementary conversational context from past sessions is always valuable, even when MEMORY.md keyword matching already finds relevant sections.

**Capped at 2 session snippets** per injection to control token budget. Word-boundary regex (`\b`) for precise topic matching. Module-level singleton cache avoids per-call connection churn.

### 4.3 SkillRegistry (`core/skill_registry.py`, 220 lines)

Scans `s_*/SKILL.md` directories at startup, reads tier (lazy/always) from manifest.yaml or SKILL.md frontmatter, generates a compact markdown index for system prompt injection.

**How tier is determined (`_read_tier()`):**
1. Check `manifest.yaml` -> `tier` field (highest priority)
2. Check `SKILL.md` -> YAML frontmatter `tier` field
3. Default: `lazy` (conservative — don't consume prompt space without explicit opt-in)

**Module-level singleton:** `_get_skill_registry()` creates the registry once and caches it. SkillGuard scan runs at discovery time with content-hash cache — rescan only when SKILL.md changes.

### 4.4 SkillGuard (`core/skill_guard.py`, 164 lines)

Trust-level-based security scanner for all skill content:

| Trust Level | Source | Gate Policy |
|-------------|--------|-------------|
| `BUILTIN` (3) | Ships with SwarmAI | Always pass |
| `USER_CREATED` (2) | User manually created | Warn on findings, don't block |
| `AGENT_CREATED` (1) | Agent created via EvolutionOptimizer | Block on any medium+ finding |
| `EXTERNAL` (0) | Downloaded from external source | Block on any finding |

**Scan patterns:**

| Category | Patterns |
|----------|----------|
| Exfiltration | `curl`/`wget` with secrets, network calls to unknown hosts |
| Prompt injection | Role hijack, instruction override, special tokens |
| Destructive | `rm -rf`, `DROP TABLE`, `git push --force` |
| Persistence | `crontab`, `launchd`, startup items |
| Privilege escalation | `sudo`, `chmod 777`, `setuid` |

**Integration:** Guards both creation path (EvolutionOptimizer deploys) and discovery path (SkillRegistry startup scan). Content-hash caching ensures rescanning only on file change.

---

## 5. Phase 3 — Autonomous Evolution

### 5.1 SessionMiner (`core/session_miner.py`, 580 lines)

Mines Claude Code session transcripts (JSONL files) for skill-related eval examples.

**Data sources (ranked by value):**

| Priority | Source | Location | What It Contains |
|----------|--------|----------|-----------------|
| **P0** | Session transcripts | `~/.claude/projects/{project}/*.jsonl` | Complete conversations: prompts, responses, tool_use with args, subagent spawns |
| **P1** | SwarmAI DB | `~/.swarm-ai/data.db` messages table | Persisted messages — tool_use only stores `{name, summary}`, NOT input args |
| **P2** | DailyActivity | `Knowledge/DailyActivity/*.md` | Session summaries — good for patterns, lacks conversation detail |
| **P3** | CLI history | `~/.claude/history.jsonl` | User input only, no agent responses |

**Why P0 is the gold mine:** The JSONL transcripts have the exact sequence of tool calls, the agent's reasoning, user corrections mid-conversation, and the full context window at each turn. 1,500+ sessions × avg ~50K tokens/session = ~75M tokens of real-world agent behavior data.

**Per-skill eval extraction:**
1. For each skill, extract TRIGGER keywords from SKILL.md
2. Scan P0 transcripts for sessions where that skill was invoked (match tool names + skill keywords)
3. Two-stage filtering: keyword heuristic (cheap, scan all files) -> relevance scoring (expensive, top candidates only)
4. For each relevant session, extract: `{user_prompt, skill_invoked, agent_actions[1500 chars], user_correction_if_any, final_outcome}`
5. Output: per-skill JSONL files in `SkillEvals/` directory
6. MemoryGuard scrubs secrets from all extracted examples

**Critical bug fixed (April 12):** Original implementation used `glob("*.jsonl")` which only searched the immediate directory. Changed to `rglob("*.jsonl")` to traverse multi-project transcript directories. This increased transcript discovery from 0 to 1,559 files.

### 5.2 SkillFitness (`core/skill_fitness.py`, 156 lines)

Three-dimensional heuristic scoring:

| Dimension | Weight | Metric |
|-----------|--------|--------|
| **Correctness** | 30% | Jaccard overlap between expected and actual output tokens |
| **Procedure** | 30% | Bigram overlap — captures sequential instruction following |
| **Containment** | 40% | Ratio of expected terms found in actual output |

**Why three dimensions, not one:**

The earlier single-metric approach (pure Jaccard) clustered all scores between 0.3–0.5 — not enough signal to distinguish good from bad. Adding bigram overlap and containment ratio spreads scores across the full 0.0–1.0 range.

**Adaptive threshold:** Each skill gets its own baseline score from historical eval results. Improvement is measured relative to baseline, not a global threshold.

### 5.3 EvolutionOptimizer (`core/evolution_optimizer.py`, 1,410 lines)

![Evolution Pipeline v2](diagrams/02-evolution-pipeline-v2.png){width=100%}

The evolution pipeline runs on a 7-day cadence (session-end hook trigger + Thursday cron fallback). The v2 architecture (shipped April 12) replaced the monolithic `run_evolution_cycle()` with a 4-phase pipeline:

#### Phase 1: MINE

```python
SessionMiner.mine_all()
-> 1,500+ JSONL transcripts
-> Per-skill eval examples
-> Filter: >=5 examples OR ≥3 if SkillMetrics flags it
-> Output: Dict[skill_name, List[EvalExample]]
```

Cross-transcript deduplication prevents the same correction from being counted multiple times.

#### Phase 2: ASSESS

```python
SkillFitness.score_batch()
-> 3-dimensional heuristic (Jaccard + bigram + containment)
-> Adaptive threshold per skill
-> compute_confidence(evidence, need)
-> Classify: HIGH (>=0.7) | MED (0.3-0.7) | LOW (<0.3)
```

Confidence = `evidence × max(density, need)`:
- **Evidence:** 0–10+ corrections -> 0.0–1.0
- **Need:** Inverse of fitness score -> 0.1–1.0

#### Phase 3: ACT

Confidence-gated deployment:

| Confidence | Action | Safety |
|------------|--------|--------|
| **HIGH** (>=0.7) | Auto-deploy -> atomic write + verify + rollback | `.bak` backup, YAML parse check |
| **MED** (0.3–0.7) | Surface recommendation in session briefing | Visible in `**Skill health:**` section |
| **LOW** (<0.3) | Log to `skill_health.json` only | No user-visible action |

**Optimization strategy:** Correction-pattern heuristic, not ML:

```
"don't X"          -> remove X from SKILL.md
"use Y instead"    -> replace X with Y
"always Z first"   -> add Z to the beginning of the workflow
```

**Constraints before deployment:**

| Gate | Threshold | Action on Fail |
|------|-----------|---------------|
| Size limit | <= 15KB | Reject |
| Growth limit | <= 20% vs original | Reject |
| SkillGuard injection scan | No injection patterns | Reject + alert |
| YAML frontmatter parse | Parse success | Reject |
| Pre-check size | If already >15KB, skip LLM call entirely | Skip |

#### Phase 4: AUDIT

Post-deployment verification:
1. SKILL.md parse check (YAML frontmatter valid)
2. Rollback from `.bak` if verify fails
3. Log to `EVOLUTION.md` (K-entry + changelog)
4. Update `skill_health.json` with deployment record
5. Process-level `fcntl` lock released

**Key design: confidence gating.** With the current ~6% correction rate across 61 skills, the HIGH threshold (>=0.7) is unreachable for most skills by design. The pipeline safely accumulates observability data until correction evidence justifies deployment. MED-confidence recommendations surface in the session briefing's `**Skill health:**` section, where the user can review and approve.

This is the **separation of observation and actuation** — the data pipeline (MINE + ASSESS) is always safe to run (read-only + write JSON). The actuation pipeline (ACT) is gated on confidence. Any autonomous system that modifies user-visible files should follow this pattern.

### 5.4 RetentionPolicies (embedded in `hooks/context_health_hook.py`, ~120 lines)

| Content | TTL | Action at Expiry |
|---------|-----|-----------------|
| DailyActivity files | 90 days | Archive to `Knowledge/Archives/` |
| Archived DailyActivity | 365 days | Delete |
| Recent Context entries | No TTL (entry-count capped) | Overflow -> archive |
| Open Threads (resolved) | 7 days after resolution | Archive |
| MEMORY.md archive files | ∞ | Never delete |
| Embedding index | ∞ (delta-synced) | Re-embed on content change |

Enforced by `context_health_hook.py` at every session close.

### 5.5 Why Heuristic-First, Not DSPy/GEPA?

The original design (Section 1.3 of the detailed design doc) specified GEPA (Genetic-Pareto Prompt Evolution Algorithm) via DSPy for ML-grade optimization. We built extensible interfaces but shipped heuristics.

**Why:**

| Factor | Heuristic | GEPA/DSPy |
|--------|-----------|-----------|
| Dependencies | Zero | `pip install dspy`, Bedrock API calls |
| Cost per skill | ~$0 | ~$2-5 (20 LLM calls) |
| Handles common case | Yes — "don't X" -> remove X | Yes, plus subtle multi-dimensional |
| Min eval examples | 3 | 20+ for statistical significance |
| Time to ship | 1 session | 3+ sessions |

**The interface is designed for layering:** `optimizer.optimize_skill(skill_path, evals, strategy="gepa")` can be added without changing the pipeline. The data is accumulating (228 eval examples across 22 skills as of April 12). When the eval dataset grows large enough (>20 examples per skill) to justify ML-grade optimization, DSPy/GEPA can be layered on.

---

## 6. Phase 4 — E2E Hardening

Comprehensive E2E review (pipeline run `c94dea8` + `3ccfdac` + `ef4bebd`) found 12 gaps across all 3 phases. All fixed.

### 6.1 Critical Findings

| # | Finding | Root Cause | Fix |
|---|---------|-----------|-----|
| F1 | **MemoryGuard bypass** | Distillation, context health, and memory health jobs wrote to MEMORY.md via direct `write_text()` under their own flock | Added `MemoryGuard().sanitize()` inline in each bypass path |
| F2 | **UserObserver dead end** | `user_suggestions.md` written but nothing read it — not in prompt builder | Added injection in `prompt_builder._assemble_context_text()` |
| F3 | **SkillCreatorTool dead code** | 178-line module with zero production callers; production uses `skill_creator.py` | Deleted module + tests |

### 6.2 Important Findings

| # | Finding | Fix |
|---|---------|-----|
| F4 | SkillRegistry recreated per prompt build | Module-level singleton `_get_skill_registry()` |
| F5 | Transcript dir picks first-alphabetical | `_resolve_transcripts_dir()` picks most-recently-active |
| F6 | SkillGuard only guards creation | Scan during `_discover_skills()` with content-hash cache |
| F7 | SessionRecall only fires as fallback | Runs alongside keyword matching, not gated by empty results |
| F8 | EntryRefs write-only | 1-hop loading in `select_memory_sections()`, capped at 3 |

### 6.3 Additional Hardening (April 13-15)

Four more structural gaps fixed (commit `f3492fc`):

| Gap | Description | Fix |
|-----|-------------|-----|
| **G1** | MED-confidence recommendations invisible | Skill health injected in session briefing |
| **G2** | No real-data E2E tests | Evolution cycle integration tests with real transcripts |
| **G3** | Shadow recall broken | `session_recall.py` used plain `sqlite3` instead of the connection pool — vector search silently returned empty | Fixed to use proper connection |
| **G4** | Optimizer heuristic too aggressive | Correction-pattern regex anchored to sentence boundaries |

---

## 7. Skill Architecture — Lazy/Always Tiering

### 7.1 The Problem

61 skills × full SKILL.md descriptions ~ 7,000 tokens in system prompt. Most skills are invoked rarely. Token budget isn't the concern (design principle: power over budget) — **signal-to-noise** is. The agent makes better skill selections when frequently-used skills have richer descriptions.

### 7.2 Two-Tier System

![Skill Lifecycle Architecture](diagrams/03-skill-lifecycle.png){width=100%}

| Tier | Count | System Prompt Injection | On Invocation |
|------|-------|------------------------|---------------|
| **always** | 15 | Full SKILL.md instructions (~100 tokens each) | Direct execution |
| **lazy** | 46 | Minimal stub (~25 tokens: name + trigger only) | Agent reads INSTRUCTIONS.md via Read tool |

**Token savings:** ~3,650 tokens/session (49% reduction in skill listing).

### 7.3 The Manifest System

Complex skills (16 of 61) declare scripts and entry points via `manifest.yaml`:

```yaml
name: browser-agent
tier: always
scripts:
  - name: browser-agent.mjs
    path: browser-agent.mjs
    language: javascript
    description: DOM-based browser automation via Playwright CDP
```

**Key modules:**

| Module | File | Lines | Role |
|--------|------|-------|------|
| ManifestLoader | `core/manifest_loader.py` | 226 | Pydantic models (`SkillManifest`, `ScriptEntry`), cached YAML parser |
| SkillRegistry | `core/skill_registry.py` | 220 | `_read_tier()` utility, SkillGuard scanning at discovery |
| MigrateSkills | `migrate_skills.py` | — | One-time idempotent migration (ran on 61 skills) |

**Runtime flow:**

1. **Startup:** `ProjectionLayer.project_skills()` copies `backend/skills/s_*/` -> `.claude/skills/s_*/` (copytree, all files)
2. **SDK reads SKILL.md** -> injects stubs for lazy, full instructions for always
3. **Invocation:** Agent reads `INSTRUCTIONS.md` via Read tool directive (lazy only)
4. **Complex skills:** `INSTRUCTIONS.md` has "Scripts & Entry Points" section with script index from manifest

### 7.4 Skill Self-Improvement

Skills are not static. The evolution pipeline continuously evaluates fitness and proposes improvements:

1. **SessionMiner** scans transcripts for skill invocations and user corrections
2. **SkillFitness** scores each skill on correctness, procedure-following, and judgment quality
3. **EvolutionOptimizer** generates targeted prompt improvements based on correction patterns
4. **Confidence-gated deployment** ensures only high-evidence changes auto-deploy; others surface as recommendations

New skills can also be created from session patterns — **SkillifySession** extracts a repeatable workflow from a conversation and packages it as a reusable skill.

### 7.5 Pipeline-Critical Skills

| Pipeline Stage | Skill | Role |
|----------------|-------|------|
| EVALUATE | `s_evaluate` | ROI scoring, GO/DEFER/REJECT |
| THINK | `s_deep-research` | Multi-source research with citations |
| REVIEW | `s_code-review` | Structured quality + security review |
| TEST | `s_qa` | Diff-aware QA with WTF gate |
| DELIVER | `s_deliver` | Artifact bundle, PR description, report |
| Orchestrator | `s_autonomous-pipeline` | Full pipeline coordination |

---

## 8. The Complete Data Flow

### 8.1 Session Lifecycle

```
User sends message
  -> Session processes request
  -> SkillMetrics hook records skill invocations + corrections
  -> UserObserver hook detects behavioral patterns
  -> DailyActivity hook captures session summary
  -> DistillationTrigger checks for promotion to MEMORY.md
    -> MemoryGuard sanitizes all writes
    -> SectionCaps enforces size limits
    -> EntryRefs generates cross-references
  -> ContextHealthHook refreshes indexes, runs retention

Next session starts:
  -> PromptBuilder loads 11-file context chain (P0-P10)
  -> SkillRegistry injects compact index (lazy stubs + always full)
  -> UserObserver suggestions injected if available
  -> SessionRecall searches past sessions (FTS5, alongside keyword match)
  -> RecallEngine hybrid search over Knowledge library (vector + FTS5)
  -> Memory index selects sections via keyword + hybrid scoring
  -> EntryRefs 1-hop loading pulls in referenced sections
  -> Proactive briefing with health alerts + signal highlights
  -> Post-first-message: RecallEngine L2/L3 re-search with actual query
```

### 8.2 Weekly Evolution Cycle

```
MINE:    SessionMiner scans 1,500+ JSONL transcripts
         -> Per-skill eval examples with secret scrubbing
         -> Cross-transcript deduplication
ASSESS:  SkillFitness scores -> confidence classification
         -> HIGH (>=0.7) | MED (0.3-0.7) | LOW (<0.3)
ACT:     HIGH -> auto-deploy with .bak backup + verify + rollback
         MED -> recommend in session briefing (Skill health section)
         LOW -> log to skill_health.json only
AUDIT:   Verify deployment (YAML parse, size check)
         -> Rollback if verify fails
         -> Log to EVOLUTION.md (K-entry + changelog)
         -> Update skill_health.json
```

---

## 9. Key Design Decisions

### 9.1 Why Heuristic-First Optimizer, Not ML-Based?

The correction-pattern heuristic handles the common case ("don't X" -> remove, "use Y" -> add) at zero dependency cost. ML-based optimization (DSPy/GEPA) adds value for subtle, multi-dimensional optimization that pattern matching can't capture — but requires 20+ eval examples per skill and ~$2-5 per optimization run. The interface is designed for layering: `strategy="gepa"` can be added without changing the pipeline architecture.

### 9.2 Why Confidence Gates Instead of Auto-Deploy Everything?

**Observation is always safe; actuation must be gated.** The data pipeline (MINE + ASSESS) only reads transcripts and writes JSON — no user impact. The deployment pipeline (ACT) modifies SKILL.md files that change agent behavior. Separating observation from actuation is a structural safety strategy for any autonomous system that modifies user-visible files.

### 9.3 Why Guard at Chokepoint + Bypass Inline?

The ideal architecture has ONE chokepoint. But `locked_write.py` uses flock, and hooks also need flock to prevent corruption. Nested flock = deadlock. The pragmatic fix: guard at the chokepoint for the common path, inline guards at bypass paths. The E2E audit ensures no bypass path is missed.

### 9.4 Why Lazy/Always Tiering, Not N-Tier Progressive?

The original design proposed 3-tier progressive disclosure (metadata -> description -> full instructions). But the Claude SDK already handles instruction loading — we just need to control what goes into the system prompt. Two tiers (lazy/always) with stub+INSTRUCTIONS.md split is simpler and more effective. The SDK loads SKILL.md; the agent reads INSTRUCTIONS.md on demand. No new infrastructure needed.

---

## 10. Module Index

| Module | Phase | File | Lines | Tests |
|--------|-------|------|-------|-------|
| MemoryGuard | 1 | `core/memory_guard.py` | 179 | 15 |
| SkillMetrics | 1 | `core/skill_metrics.py` + hook | 468 | 18 |
| SectionCaps | 1 | `hooks/distillation_hook.py` (embed) | ~120 | 12 |
| EntryRefs | 1 | `core/memory_index.py` (embed) | ~80 | 8 |
| UserObserver | 2 | `core/user_observer.py` + hook | 355 | 14 |
| SessionRecall | 2 | `core/session_recall.py` | 293 | 11 |
| SkillRegistry | 2 | `core/skill_registry.py` | 220 | 8 |
| SkillGuard | 2 | `core/skill_guard.py` | 164 | 12 |
| SessionMiner | 3 | `core/session_miner.py` | 580 | 16 |
| SkillFitness | 3 | `core/skill_fitness.py` | 156 | 14 |
| EvolutionOptimizer | 3 | `core/evolution_optimizer.py` | 1,410 | 22 |
| ManifestLoader | — | `core/manifest_loader.py` | 226 | 8 |
| RetentionPolicies | 3 | `hooks/context_health_hook.py` (embed) | ~120 | 8 |
| **Total** | | **19 files** | **9,026** | **291+** |

---

## 11. Lessons Learned

1. **Chokepoint only works if ALL traffic goes through it.** MemoryGuard at `locked_write.py` was correctly placed but 4 bypass paths existed. A chokepoint audit must check every write path.

2. **206 unit tests, 3 critical wiring gaps.** Unit tests prove components work in isolation. Only E2E caller->callee trace proves they're wired into the system.

3. **Heuristic-first, layer ML later.** Ship the common case at zero cost; add sophistication when data justifies it.

4. **Backup before autonomous mutation.** `.bak` before every SKILL.md deployment. Cost: one write. Value: reversibility without git archaeology.

5. **Observe -> Recommend -> Act separation.** The confidence-gated deployment ensures safe accumulation. Observation is always safe; execution is gated.

6. **Autonomous pipeline output != production code.** 206 unit tests and 10/10 confidence; first real-data E2E run exposed 20 issues. Real data is the only validator.

7. **Lazy > progressive for skill loading.** Two-tier with stub+instructions split is simpler than N-tier progressive disclosure. The SDK already handles instruction loading.

8. **Module-level singletons for prompt-time services.** SkillRegistry and SessionRecall called during prompt assembly must be cached. Per-call instantiation wastes I/O.

---

## 12. Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Evolution optimizer produces worse skills | Medium | Low | Constraint gates + `.bak` + rollback |
| MemoryGuard false positives | Medium | Low | Allowlist for known patterns; log all rejections |
| UserObserver misreads signals | Medium | Low | Observations are suggestions, never auto-applied |
| SkillGuard blocks legitimate skills | Low | Medium | Trust levels — builtin always passes |
| Session mining exposes private data | Low | High | MemoryGuard secret scrubbing on all extracted examples |
| Correction rate too low for HIGH threshold | High | Low | By design — MED recommendations surface in briefing |

---

*Updated 2026-04-15. Source: Hermes Agent deep dive + 4-phase implementation + Evolution Pipeline v2 + Lazy Skill System.*
