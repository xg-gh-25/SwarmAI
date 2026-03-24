---
title: "AIDLC Phase 3: AI-Management — High-Level Design"
date: 2026-03-24
updated: 2026-03-24
author: XG (direction), Swarm (synthesis + implementation)
status: shipped-pilot
tags: [aidlc, phase3, ai-management, ddd, sdd, tdd, autonomous, escalation, pipeline]
consolidates:
  - 2026-03-23-aidlc-three-phase-definition.md
  - 2026-03-20-aidlc-ddd-investigation.md
  - 2026-03-23-skill-lifecycle-pipeline-design.md (Knowledge/Designs)
  - 2026-03-24-escalation-protocol-design.md (Knowledge/Designs)
  - 2026-03-24-pipeline-orchestrator-design.md (Knowledge/Designs)
prior_art: gstack (garrytan/gstack) — sequential orchestration, decision classification, phase-transition verification
---

# AIDLC Phase 3: AI-Management

## Executive Summary

Phase 3 is the target state of the AI-Driven Development Lifecycle: **AI makes autonomous decisions and self-evolves. Humans step in when needed and at triage.**

SwarmAI implements Phase 3 through five integrated systems, all shipped and E2E-verified:

| System | Role | Status |
|---|---|---|
| **DDD Knowledge Layer** | The brain — 4 documents per project provide autonomous judgment | Shipped |
| **Skill Lifecycle Pipeline** | The hands — 8-stage execution from intake to delivery | Shipped |
| **Pipeline Orchestrator** | The spine — drives stages sequentially, checkpoints on blocks | Shipped (v1-v3) |
| **Escalation Protocol** | The safety net — 3-level HITL enables safe autonomy | Shipped |
| **Self-Improving Feedback Loop** | The learning — outcomes calibrate future decisions | Shipped |

**Goal:** 6x throughput over Phase 1 baseline. 30%+ of all intake handled autonomously.

---

## 1. The Three Phases

```
Phase 1: AI-Assistant       Human drives, AI assists              AI = tool
Phase 2: AI-Driven/First    AI plans & executes, Human decides    AI = executor
Phase 3: AI-Management      AI autonomous, Human when needed      AI = manager
```

Phase 3 is NOT "AI does everything." It's a **spectrum** — well-defined domains with mature DDD knowledge run autonomously; complex/novel projects stay in Phase 2. The boundary shifts as domain knowledge accumulates and trust builds.

### Phase 2 + Phase 3 Coexistence

From Q3 2026 onward, both phases coexist. Project complexity determines assignment:

| Phase 2 (Human Decides) | Phase 3 (AI Autonomous) |
|---|---|
| Novel domains, no DDD knowledge | Mature domains with rich DDD docs |
| High-risk architectural changes | Well-tested, pattern-rich codebases |
| Cross-team coordination | Incremental features, bug fixes |

### Three Diagnostic Questions

1. **Do specs and tests come before code?** Code-first = Phase 2.
2. **Are senior engineers designing domain models — not writing code?** Coder to domain architect is the Phase 3 signal.
3. **Is judgment encoded in specs, or spent reviewing code by hand?** Manual review = Phase 2. Spec-governed execution = Phase 3.

---

## 2. DDD Knowledge Layer — The Brain

### Document-as-Bounded-Context

Traditional DDD assumes classes and objects. AI agents process text. The paradigm migration maps DDD's strategic principles into the document-centric world:

| Traditional DDD | Document-as-Bounded-Context |
|---|---|
| Bounded Context = Code module | Bounded Context = `.md` file |
| Aggregate Root = Entry Entity | Each `.md` file IS its own Aggregate Root |
| Repository = Database | Filesystem IS the Repository |
| Domain Event = Event bus | Task outcome = Learn feedback loop |

### The 4 Pillars of Autonomous Judgment

Every project maintains 4 DDD documents. Together they answer the 4 questions an autonomous agent must resolve:

| Document | Question | Owns |
|---|---|---|
| **PRODUCT.md** | Should we do this? | Strategic alignment, roadmap, priorities, non-goals |
| **TECH.md** | Can we do this? | Architecture, conventions, cost estimation, constraints |
| **IMPROVEMENT.md** | Have we tried this? | Historical patterns, lessons, what worked/failed |
| **PROJECT.md** | Should we do it now? | Current focus, open items, sprint context |

Documents cannot cross boundaries. TECH.md never judges business severity. PRODUCT.md never estimates technical cost. This is DDD's single-ownership principle enforced at the document level.

### Stage-Scoped Loading

Each pipeline stage reads only the documents it needs — information isolation prevents context pollution:

| Stage | DDD Docs Loaded | Decision Scope |
|---|---|---|
| Evaluate | PRODUCT, TECH, IMPROVEMENT, PROJECT | ROI scoring, go/defer/reject |
| Think | PRODUCT, IMPROVEMENT | Strategic research direction |
| Plan | PRODUCT, PROJECT | Design aligned with priorities |
| Build | TECH, PROJECT | Code generation with conventions |
| Review | TECH, IMPROVEMENT | Known issues, past security bugs |
| Test | TECH, IMPROVEMENT | Test commands, flaky test history |
| Deliver | PROJECT | Status update, attention flags |
| Reflect | IMPROVEMENT (write) | Lesson extraction and writeback |

### decision-strategy.json — Persistent Calibration

Per-project scoring weights that survive across sessions. The Learn loop auto-nudges weights based on predicted-vs-actual outcomes:

```json
{
  "weights": {
    "strategic_alignment": 0.35,
    "priority_urgency": 0.25,
    "historical_leverage": 0.15,
    "feasibility": 0.25
  },
  "thresholds": { "go": 3.5, "defer": 2.0 },
  "calibration_history": []
}
```

### Progressive DDD Maturity

No mandatory setup. No front-loading. DDD grows from the work:

```
Day 1:   Project created → templates with placeholders. Everything works at L0.
Day 3:   First QA run → Swarm prompts to fill TECH.md with test commands.
Day 7:   QA finds recurring bug → auto-writes to IMPROVEMENT.md.
Day 14:  Design decision → Swarm reads PRODUCT.md for strategic alignment.
Day 30:  Enough lessons → Phase 3 pilot feasible for this project.
```

**The rule:** No capability requires DDD to function. DDD makes everything smarter; absence of DDD never makes anything non-functional.

---

## 3. Methodology Stack — DDD + SDD + TDD

### The Three Pillars

| Methodology | What It Does | Why Non-Negotiable |
|---|---|---|
| **DDD** | Gives agents business understanding | Without DDD, agents guess at intent |
| **SDD** | Specs are the single source of truth | Without SDD, no contract between intent and code |
| **TDD** | Binary pass/fail quality gate | Without TDD, nobody catches when agents guess wrong |

Together they form a closed loop:

```
DDD  → "What should we build?"     → PRODUCT.md, TECH.md
SDD  → "Here's the spec"           → design_doc with acceptance criteria
TDD  → "Here's proof we built it"  → acceptance tests (binary pass/fail)
```

### Why TDD Changes Everything for Autonomous Agents

Traditional development follows: human writes code → human writes tests → human reviews both. Phase 2 changes the *who* (AI writes, human reviews) but not the *sequence*. Code still comes first. Tests verify after the fact. The human remains the quality gate.

**Phase 3 breaks this sequence.** When no human reviews every line, TDD is the only automated mechanism that guarantees delivery quality:

```
Phase 2 (AI-Driven):
  Human says: "build X"
  AI writes code
  AI writes tests
  Human reviews both              ← human is the quality gate
  Ship or fix

Phase 3 (AI-Management):
  Human says: "build X"
  Pipeline generates acceptance criteria      ← from EVALUATE + PLAN
  Pipeline generates tests from criteria      ← tests ARE the spec
  Pipeline generates code to pass the tests   ← code targets the tests
  Tests verify: built what was requested      ← tests are the quality gate
  Human reviews at delivery gate only         ← human at triage, not every line
```

The **red-green cycle** adapted for autonomous agents:

```
1. RED    — Generate tests from acceptance criteria. All fail. (Nothing built yet.)
2. GREEN  — Write code until all tests pass. (Implementation matches spec.)
3. VERIFY — Run full test suite (generated + existing). No regressions.
4. SHIP   — Delivery gate: human reviews taste decisions only.
5. LEARN  — Reflect: lessons to IMPROVEMENT.md. Next run is smarter.
```

**The key constraint:** fix code, not tests. If a generated test fails after BUILD, the code is wrong — not the test. Tests are derived from the accepted design doc. Changing tests means changing the spec, which requires going back to PLAN. This discipline makes TDD work: **tests are the authority.**

### Why Now — LLMs Change the Economics

DDD and TDD aren't new. Teams skipped them for decades because:
- **DDD was expensive:** maintaining detailed domain models alongside hand-written code doubled the documentation burden.
- **TDD was slow:** writing tests before code felt like extra work when a human was writing both.

LLMs flip both trade-offs:
- **DDD becomes cheap.** The domain model IS the input. LLMs generate code from specs. The cost of keeping models detailed and current drops to near-zero.
- **TDD becomes the fastest path.** Test generation is nearly free. Binary pass/fail eliminates subjective human evaluation. Agents generate → test → adjust → iterate at machine speed.

The discipline developers resisted for years now makes them faster than ever. DDD tells the agent what to build. TDD tells the agent when it's done. Without either, an autonomous agent is just generating code and hoping for the best.

### The Role Shift

Senior engineers stop writing code and start writing acceptance criteria and domain models. Their judgment encodes into:
- **DDD documents** → what matters, what to avoid, what's been tried
- **Acceptance criteria** → what "done" looks like, in binary terms
- **Test specifications** → the spec in executable form

They become **domain architects** — not code reviewers. Their time goes to the 20% of decisions that require human judgment (taste + judgment escalations), not the 80% that an agent can handle mechanically.

---

## 4. Skill Lifecycle Pipeline — The Hands

### Pipeline Overview

One-sentence requirement in, PR-ready delivery out:

```
EVALUATE --> Think --> Plan --> Build --> Review --> Test --> DELIVER --> Reflect
    ^                                                             |
    |_________________ ESCALATE (any stage) ______________________|
```

### 8 Stages, 7 Artifact Types

| Stage | What Happens | Artifact | Key Behavior |
|---|---|---|---|
| **EVALUATE** | ROI scoring against DDD docs | `evaluation` | go/defer/reject/escalate. Classifies scope, selects pipeline profile. |
| **THINK** | Research + 3-approach alternatives | `research` | Minimal/Ideal/Creative with tradeoffs. DDD-enriched at L2. |
| **PLAN** | Design doc with acceptance criteria | `design_doc` | Carries forward from chosen alternative. Edge cases, API contract. |
| **BUILD** | Code generation guided by TECH.md | `changeset` | Completeness bias. Atomic commits. Acceptance criteria coverage. |
| **REVIEW** | Confidence-gated code + security review | `review` | 10 false-positive exclusions. Exploit scenario required. |
| **TEST** | Diff-aware QA with WTF gate | `test_report` | Atomic fix-and-verify loop. Halts if fixes get risky (wtf_score >= 5). |
| **DELIVER** | Artifact bundle, PR desc, decision log | `delivery` | Delivery Gate: batch-reviews all taste decisions before finalizing. |
| **REFLECT** | Lesson extraction | (DDD updates) | Writes to IMPROVEMENT.md + MEMORY.md. Records outcome for calibration. |

### 5 Pipeline Profiles

The evaluate stage selects the right profile based on scope:

| Profile | Stages | When |
|---|---|---|
| **full** | all 8 | Standard features, complex work |
| **trivial** | evaluate → build → review → test → deliver → reflect | Config flags, typo fixes, thin wrappers |
| **research** | evaluate → think → reflect | Investigation without implementation |
| **docs** | evaluate → think → plan → deliver → reflect | Documentation, specs, design docs |
| **bugfix** | evaluate → plan → build → review → test → deliver → reflect | Known root cause, skip research |

### Three Operating Levels

| Level | State | What Works |
|---|---|---|
| **L0** | No project | All skills work standalone. Output stays in chat. |
| **L1** | Project exists | + Artifact chaining via `.artifacts/`. Pipeline state tracked. |
| **L2** | Full DDD setup | + DDD context injection. Smarter scoping. Strategic alignment. |

### Artifact Registry — Per-Run Storage

Each pipeline run gets its own directory. Self-contained, portable, git-diffable:

```
Projects/<project>/.artifacts/
  manifest.json                          # Global artifact index
  research-20260323-gstack.json          # Standalone artifacts (no pipeline)

  runs/
    run_f7fe7f14/                        # One folder per pipeline run
      run.json                           # Run state (status, stages, decisions, budget)
      evaluation-20260324.json           # Scoped to this run
      research-20260324.json
      design_doc-20260324.json
      changeset-20260324.json
      review-20260324.json
      test_report-20260324.json
      delivery-20260324.json
```

**Key properties:**
- Filesystem only — no DB, git tracks for free
- Per-run subdirectories keep intake isolated and archivable
- Standalone artifacts (no pipeline context) stay at top level
- Empty results are normal — every consumer handles "no artifacts" gracefully
- Backward compatible — legacy flat files still discovered

---

## 5. Pipeline Orchestrator — The Spine

The orchestrator drives the pipeline from requirement to delivery. It's a behavioral loop the agent executes — not a service.

### Core Loop

```
1. INIT     → parse requirement, detect project, create pipeline run
2. PROFILE  → select profile (full/trivial/research/docs/bugfix)
3. STAGE    → for each stage in profile:
               a. Gate check (budget, escalations, retries)
               b. Load stage context (DDD docs + upstream artifacts)
               c. Execute stage behavior inline
               d. Classify decisions (mechanical/taste/judgment)
               e. Verify output (artifact + schema check)
               f. Handle result (advance / retry / checkpoint)
4. DELIVER  → Delivery Gate: batch-review taste decisions
5. COMPLETE → summarize, reflect, record metrics
```

### Decision Classification (from gstack)

Every non-trivial decision during stage execution is classified:

| Classification | Meaning | Pipeline Impact |
|---|---|---|
| **Mechanical** | One correct answer, deterministic | L0 INFORM, auto-approve |
| **Taste** | Reasonable default, human might prefer differently | Accumulate, batch-review at Delivery Gate |
| **Judgment** | Genuinely ambiguous, needs human context | L2 BLOCK, checkpoint immediately |

### Delivery Gate

Before delivering, all accumulated taste decisions from all stages are presented as one batch:

```
DELIVERY GATE -- 3 taste decisions for review:

  1. [THINK]   Chose httpx built-in over tenacity (simpler, fewer deps)
  2. [BUILD]   Used sync retry instead of async (matches codebase style)
  3. [REVIEW]  Skipped type stub generation (low value for internal module)

  [Approve All]  [Override #1]  [Override #2]  [Discuss]
```

This batches low-urgency decisions into one review moment instead of interrupting at each stage.

### Budget Tracking + Historical Calibration

Each stage has a token budget estimate, calibrated from past completed runs:

- `run-budget` checks if the next stage fits in the remaining session budget
- `run-history` returns per-stage averages (avg * 1.2 buffer) from past runs
- If budget insufficient or >70% consumed → auto-checkpoint

### Checkpoint + Resume

When the pipeline pauses (L2 BLOCK, budget exhaustion, retry cap):

```bash
artifact_cli.py run-checkpoint --project <P> --run-id <R> --stage <S> --reason "..."
```

This atomically: pauses the run + publishes checkpoint artifact + creates Radar todo.

Resume from any trigger:
- Drag Radar todo into chat → agent loads checkpoint
- "Resume pipeline for SwarmAI" → reads run state
- Background job picks up resumed run on next scheduler cycle

### Background Execution

Pipelines can run as background jobs via the Swarm Job System:

```bash
job_manager.py pipeline --project SwarmAI --requirement "Add retry logic" --one-shot
```

Creates an `agent_task` job that spawns headless Claude CLI with s_pipeline prompt. Checkpoints create Radar todos visible in the sidebar even when the user isn't watching.

### Cross-Project Dashboard

All active and recent pipeline runs across all projects:

```bash
artifact_cli.py run-status [--active-only]
```

Also served via REST API: `GET /api/pipelines [?active=true]`

---

## 6. Escalation Protocol — The Safety Net

### Core Principle

```
Autonomy without escalation is recklessness.
Escalation without autonomy is just a chatbot.
The protocol makes it EASY to be autonomous AND SAFE to be wrong.
```

### Three Levels

| Level | Name | Pipeline | Timeout | Default |
|---|---|---|---|---|
| **L0** | INFORM | Continues | None | N/A |
| **L1** | CONSULT | Continues (reversible) | 24h | Accept Swarm's choice |
| **L2** | BLOCK | Pauses | 72h | Defer the task |

### 28 Trigger Conditions (across 8 stages + cross-cutting)

Every stage has specific conditions that fire escalation. Examples:

| Trigger | Level | Example |
|---|---|---|
| Routine decision | L0 | "Chose approach 2 based on PRODUCT.md alignment" |
| Non-obvious choice | L1 | "TECH.md says microservice, going monolith — override?" |
| Ambiguous scope | L2 | "Improve performance — of what?" |
| Conflicting DDD docs | L2 | "PRODUCT.md says real-time, TECH.md says no WebSocket" |
| WTF gate triggered | L2 | "Fix attempt #4 touching unrelated modules. Stopping." |
| Budget exceeded | L2 | "Pipeline has used $X in API calls. Continue?" |

### Learning from Escalations

Every resolved escalation is a training signal:
- **Timeout** → lower trigger sensitivity (human didn't care)
- **Override** → record pattern in IMPROVEMENT.md, adjust heuristic (Swarm was wrong)
- **Accepted** → increase confidence (Swarm was right)

The competence boundary expands over time.

---

## 7. Self-Improving Feedback Loop

| Source | Trigger | Target | What Changes |
|---|---|---|---|
| Task outcome | Post-execution | IMPROVEMENT.md | New pattern or lesson |
| Actual vs estimated effort | Post-execution | decision-strategy.json | Weight calibration |
| Escalation override | Human decision | IMPROVEMENT.md + weights | Pattern + adjustment |
| Escalation timeout | No response | decision-strategy.json | Sensitivity down |
| Pipeline token costs | Run completion | Historical calibration | Stage budget estimates |

The system is designed for convergence, not perfection from day one.

---

## 8. End-to-End Flow — Validated

### A Requirement Arrives

```
"Add /api/pipelines REST endpoint for the frontend Radar sidebar"
```

### Pipeline Execution (actual test-drive, 2026-03-24)

```
Pipeline: Add /api/pipelines endpoint (run_d8d488db)
Project: SwarmAI | Profile: trivial | Budget: 71K/800K (8.9%)

  [done] EVALUATE  GO: ROI 3.6, trivial scope                    12K tokens
  [skip] THINK     (trivial profile)
  [skip] PLAN      (trivial profile)
  [done] BUILD     3 new files + 1 modified (schema, router, main.py)  25K tokens
  [done] REVIEW    Clean, 1 minor hardening (ValueError guard)    8K tokens
  [done] TEST      10/10 pass, 1 bug fixed (field casing)        18K tokens
  [done] DELIVER   PR-ready, 0 attention flags                    5K tokens
  [done] REFLECT   3 lessons recorded to IMPROVEMENT.md           3K tokens

  Decisions: 3 mechanical, 0 taste, 0 judgment
  Escalations: 0
  Output: working /api/pipelines endpoint + 10 tests
```

**What the test-drive validated:**
- Full loop works end-to-end (INIT → EVALUATE → BUILD → REVIEW → TEST → DELIVER → REFLECT)
- Profile selection correctly skips irrelevant stages
- Artifact registry tracks state per-run in isolated subdirectories
- Budget tracking works (71K actual vs 230K estimate)
- Decision classification works (all mechanical for trivial scope)
- Produced real, shippable code — not a simulation

---

## 9. What's Shipped (as of 2026-03-24)

### Core Infrastructure

| Component | Lines | Tests | Status |
|---|---|---|---|
| DDD Project System (4 docs, auto-provision, templates) | ~300 | 152 | Shipped |
| Artifact Registry (filesystem, 8 types, per-run subdirs) | ~500 | 39 | Shipped |
| s_pipeline orchestrator (8 stages, 5 profiles, decision classification) | ~550 | 36 | Shipped |
| Pipeline CLI (13 commands: publish, discover, run-*, status, resume) | ~700 | 36 | Shipped |
| /api/pipelines REST endpoint (dashboard data) | ~160 | 10 | Shipped |
| s_evaluate (ROI scoring, 4 DDD questions) | ~300 | -- | Shipped |
| s_deliver (artifact bundle, PR desc, attention flags) | ~230 | -- | Shipped |
| s_qa (diff-aware QA, WTF gate, atomic commits) | ~250 | -- | Shipped |
| Escalation Protocol (3 levels, 28 triggers, decision packets) | -- | -- | Shipped |
| decision-strategy.json (per-project weights + learn loop) | -- | -- | Shipped |
| IMPROVEMENT.md writeback hook | -- | -- | Shipped |
| Auto-populate TECH.md from config files | -- | -- | Shipped |
| Background pipeline jobs (job_manager pipeline command) | ~70 | -- | Shipped |
| **Total** | **~3,060** | **85+** | |

### Remaining Gaps

| # | Gap | Priority | Effort |
|---|---|---|---|
| G1 | **Escalation UI** — EscalationBlock.tsx (in-chat rendering + action buttons) | P1 | 2-3 sessions |
| G2 | **Spec-first TDD loop** — spec gen → test suite gen → agent codes against tests | P1 | 3-4 sessions |
| G4 | **Pipeline Dashboard** — Radar sidebar panel (data API ready, needs React component) | P2 | 2-3 sessions |
| G5 | **Push notifications** — Slack DM / macOS notification for L2 blocks | P2 | 1 session |
| G6 | **Stage-selective context loading** — full document-scoped loading per stage | P2 | 1-2 sessions |

### Rollout Plan

```
Phase 3a — Pilot (NOW)
  SwarmAI itself running full pipeline. Human triggers, reviews output.
  Calibrating decision-strategy.json from real data.
  Goal: validate pipeline produces shippable output. ← VALIDATED

Phase 3b — Semi-Autonomous (Q2 2026)
  Orchestrator auto-advances stages. Human approves at EVALUATE + DELIVER.
  Escalation protocol handles mid-pipeline blocks.
  Goal: 50% of stages run without human intervention.

Phase 3c — Autonomous (Q3 2026+)
  Background jobs run pipelines. Human at triage only.
  Learn loop expands competence boundary over time.
  Goal: 30%+ intake handled fully autonomously.
```

---

## 10. Success Metrics

| Metric | Phase 2 (baseline) | Phase 3 Target |
|---|---|---|
| Throughput multiplier | 4x | 6x |
| Autonomous intake rate | 0% | 30%+ |
| Human review time per task | 100% | <30% (triage + escalations) |
| Pipeline stages requiring human input | All | <2 per run (avg) |
| Escalation override rate | N/A | <20% (good calibration) |
| Mean time from intake to CR-ready | Hours | Minutes |
| IMPROVEMENT.md entries per project/week | 0 | 3+ (auto-writeback) |

---

## 11. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| **DDD docs go stale** | Auto-writeback hook + Learn loop. Staleness detector. |
| **ROI miscalibration** | Learn loop auto-corrects. Calibration period with human oversight (Phase 3a). |
| **Escalation fatigue** | Taste decisions batch at Delivery Gate. Learning from timeouts lowers sensitivity. |
| **TDD coverage gaps** | WTF gate + coverage threshold. Minimum 80% for auto-merge. |
| **Context window exhaustion** | Token budget tracking + auto-checkpoint at 70%. Historical calibration. |
| **Autonomous mistakes compound** | Escalation at every stage. DELIVER checks for unresolved items. Reflect captures lessons. |

---

## Related Documents

These detailed design docs live in the SwarmWS workspace (`~/.swarm-ai/SwarmWS/`):

- **Three-Phase Evolution Model** (`Knowledge/AIDLC/2026-03-23-aidlc-three-phase-definition.md`) — Phase definitions and transitions
- **DDD Investigation: Document-as-Bounded-Context** (`Knowledge/AIDLC/2026-03-20-aidlc-ddd-investigation.md`) — Knowledge layer pattern
- **Skill Lifecycle Pipeline** (`Knowledge/Designs/2026-03-23-skill-lifecycle-pipeline-design.md`) — Stage definitions, artifact types, DDD enrichment
- **Escalation Protocol** (`Knowledge/Designs/2026-03-24-escalation-protocol-design.md`) — 3-level HITL, 28 triggers, decision packets
- **Pipeline Orchestrator** (`Knowledge/Designs/2026-03-24-pipeline-orchestrator-design.md`) — Orchestration loop, budget, checkpoint/resume, gstack patterns

Key implementation files in this repo:

- `backend/skills/s_pipeline/SKILL.md` — Pipeline orchestrator skill (550 lines)
- `backend/scripts/artifact_cli.py` — 13 CLI commands for artifacts + pipeline runs
- `backend/core/artifact_registry.py` — Filesystem-backed artifact storage
- `backend/core/escalation.py` — 3-level escalation protocol
- `backend/routers/pipelines.py` — `/api/pipelines` REST endpoint
- `backend/core/pipeline_profiles.py` — Shared pipeline profile definitions
