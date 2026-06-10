---
title: "AI-Ready-Repo Engine — Making Any Codebase Agent-Ready"
created: 2026-05-29
updated: 2026-06-01
tags: [ai-ready, ddd, code-intel, delivery-engine]
project: SwarmAI
status: approved
---

# AI-Ready-Repo Engine — Making Any Codebase Agent-Ready

## Summary

AI-Ready-Repo Engine is a SwarmAI Delivery Engine that transforms any codebase (with optional docs/signals) into DDD-structured artifacts that make AI agents truly understand a project — not just navigate code, but comprehend purpose, architecture, history, and current state.

**Output targets**: Kiro IDE, Claude Code, Codex, and future agent-powered IDEs.

**Core output format**: DDD 4-file structure (PRODUCT.md, TECH.md, IMPROVEMENT.md, PROJECT.md) + AGENTS.md entry point + Code Intelligence graph + Review Report.

---

## Problem

AI coding agents (Claude Code, Kiro, Codex) face a **cold-start problem** on every existing codebase. They can parse syntax but cannot read your team's mind.

### Evidence of demand

| Signal | Source | Date |
|--------|--------|------|
| "Long-term memory and knowledge management" is top community question | #kiro-interest Slack | 2026-05-28 |
| AI-Native Brownfield Bootstrapper received 15 reactions in 24 hours | #amazon-builder-genai-power-users | 2026-05-27 |
| MeshClaw dashboard sessions start at ~38% context consumed before user types anything | #meshclaw-interest | 2026-05-28 |
| Multiple community-built memory solutions (KiroMem, KiRoom, LLM-Wiki) filling the gap | #kiro-interest, #q-command-line-interest | 2026-05-28 |
| Claude Code AIM plugins consume 333+ tokens each, always active — no on-demand loading | #claude-code-internal-interest | 2026-05-28 |
| Brownfield Bootstrapper: "hand it a pipeline URL and it generates AGENTS.md, specs, test plans" | AINativeBrownfieldBootstrapper package (@tommyb) | 2026-05-27 |

### Gap in current solutions

| Existing Solution | What It Covers | What It Misses |
|---|---|---|
| CLAUDE.md | Build commands, basic rules | Architecture, history, priorities, non-goals |
| agents.md spec | Template for code navigation | No generation tooling, no refresh, single flat file |
| Brownfield Bootstrapper | AGENTS.md + specs (Amazon-only) | No business context, no self-maintenance, no IDE-native install |
| Kiro steering docs | User-written rules | No auto-generation from codebase analysis |

**No existing tool addresses the full project understanding gap**: why it exists, how it works, what failed before, and what matters now — in a format that self-maintains.

---

## Key Insight: Why DDD, Not a Flat File

A single AGENTS.md (even at 150 lines) can tell an agent **where things are**. It cannot teach an agent **judgment**.

```
Judgment requires knowing:
  PRODUCT.md     → "We don't do caching because regulatory requires real-time data"
  TECH.md        → "All DB access through repository.ts, never raw SQL"
  IMPROVEMENT.md → "We tried event sourcing in Q1, reverted after 3 incidents"
  PROJECT.md     → "Currently migrating auth — don't touch identity module"
```

This is DDD's 4-file structure — the same names, the same purpose, the same philosophy we use internally across 8 active projects. No translation layer, no renaming. PRODUCT/TECH/IMPROVEMENT/PROJECT — battle-tested over 3 months with automated cultivation keeping docs alive.

The academic backing (Brownfield Bootstrapper team's internal research): context files beyond ~150 lines show diminishing returns on agent accuracy. Our solution: **≤150 line entry point** (AGENTS.md) + **layered deep context** (DDD's PRODUCT/TECH/IMPROVEMENT/PROJECT, loaded on demand by task type).

---

## User Scenarios

### Scenario 1: Engineer — "Make my team's service AI-ready"

**Who**: Li Wei, backend engineer. Team of 5 owns a payment processing service (Python, 3 packages). New hires take 2 weeks to onboard. Team recently adopted Kiro IDE.

**Pain**: Agent keeps asking "where is the config?" and "how do I run tests?" — same questions every session. Worse: it tried to add direct DB calls (violating the repository pattern) because nothing told it not to.

**Flow**:
```
Li Wei → SwarmAI: "Make payment-service AI-ready. Here's the repo + our wiki link + last quarter's postmortem doc."

Engine: INGEST (repos + wiki + postmortem) → UNDERSTAND (detects 3 modules, Python/FastAPI, 
        pytest, 12 conventions from code patterns) → ENRICH ("I see 2 DB connections — 
        which is source of truth?" "Any deploy gotchas?") → GENERATE → VERIFY → DELIVER

Li Wei reviews REVIEW-REPORT.md → assigns TECH.md review to senior engineer, 
confirms IMPROVEMENT.md gotchas himself → merges PR

Result: New hire's Kiro agent immediately knows conventions, avoids the DB pattern mistake, 
finds the right module in 1 try instead of 3.
```

**What Li Wei touches**: Provides repo + signals, answers 3 ENRICH questions, reviews TECH.md + IMPROVEMENT.md, merges PR.

---

### Scenario 2: PM/TPM — "I need the agent to understand our business rules"

**Who**: Sarah, TPM for a compliance-heavy fintech product. Frustrated that AI agents keep suggesting features that violate regulatory constraints or optimize for the wrong metric.

**Pain**: Agent proposed caching exchange rates (regulatory violation — must be real-time). Agent tried to speed up reconciliation (correctness is the only metric, not speed). Both would have shipped if not caught in code review.

**Flow**:
```
Sarah → SwarmAI: "Add business context to our AI-Ready artifacts. Here's our PRD, 
                  our compliance matrix, and the Q2 priorities doc."

Engine: Reads existing .ai-ready/ (Level 2 already exists from engineering) → 
        ENRICH focuses on PRODUCT.md + PROJECT.md questions:
        "What are the top 3 things an agent should NEVER do?"
        "What's the success metric — speed or correctness?"
        "What's blocked right now that agents shouldn't touch?"

Sarah answers → Engine updates PRODUCT.md (non-goals, constraints) + PROJECT.md (priorities, blockers)

Result: Agent now refuses to cache exchange rates (cites PRODUCT.md constraint), 
won't optimize reconciliation speed (cites non-goal), and knows not to touch 
the identity module (PROJECT.md: "migration in progress").
```

**What Sarah touches**: Provides business docs, answers ENRICH questions about non-goals and constraints, reviews PRODUCT.md + PROJECT.md. Never sees code-intel.json or TECH.md.

---

### Scenario 3: Knowledge Expert / Senior Engineer — "Capture tribal knowledge before people leave"

**Who**: Marcus, staff engineer who's been on the team 4 years. Two senior engineers leaving next month. Critical knowledge about system gotchas lives only in their heads.

**Pain**: After the last departure, team hit 3 known issues that the departing engineer would have warned about. Each cost 2-3 days of debugging.

**Flow**:
```
Marcus → SwarmAI: "Deep-mine our git history for tribal knowledge. Also, here's 
                   a Slack export from #payment-incidents channel and our postmortem wiki."

Engine: INGEST (git history 12 months + Slack export + postmortems) → 
        UNDERSTAND (finds 23 commits with "revert:", 8 with "hotfix:", 
        correlates with incident timeline) → 
        ENRICH: "I found that retry logic was changed 4 times in March. 
                 Was this a known instability?" (Marcus: "Yes — webhook ordering issue")

Engine generates IMPROVEMENT.md with 15 evidence-grounded gotchas:
  - WHEN: modifying webhook handler → RISK: order-dependent state corruption 
    → BECAUSE: commits abc123, def456, ghi789 (March 2026, 3 incidents)

Marcus reviews with departing engineers: "Anything missing?" → They add 3 more via 
<!-- user --> section → Knowledge preserved.

Result: After engineers leave, agent warns any developer touching webhook handler 
about the ordering issue — with commit links as proof.
```

**What Marcus touches**: Provides signal sources (Slack, wiki), answers contextual ENRICH questions, reviews IMPROVEMENT.md with departing team members, adds manual entries.

---

### Scenario 4: Business Stakeholder / Director — "I want to know our AI-readiness across the org"

**Who**: David, Director of Engineering. Owns 12 services across 4 teams. Evaluating where to invest in AI tooling adoption.

**Pain**: Some teams' agents work great (high velocity), others constantly make mistakes. No visibility into why.

**Flow**:
```
David → SwarmAI: "Score AI-readiness for all 12 services."

Engine: Runs lightweight INGEST + UNDERSTAND on each repo (no ENRICH needed for scoring).
        Produces per-repo ai-ready-score.json + aggregate dashboard.

Output: Scorecard
  | Service | Navigation | Build | Architecture | Conventions | Tribal | Graph | Tests | Ops | Overall |
  |---------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
  | payment-svc | 8 | 9 | 7 | 6 | 5 | 8 | 4 | 2 | **6.1** |
  | auth-svc    | 3 | 5 | 2 | 1 | 0 | 0 | 7 | 6 | **3.0** |
  | ...         |   |   |   |   |   |   |   |   |         |

David sees: auth-svc is the worst (score 3.0, zero tribal knowledge, no architecture docs).
Directs that team to run full AI-Ready Engine first.

Result: Investment prioritized by data, not gut feel. Teams with low scores get engine run first.
```

**What David touches**: Provides list of repos. Reviews aggregate scorecard. Makes investment decision. Never sees individual DDD files.

---

### Scenario 5: Cross-team handoff — "New team inheriting a service"

**Who**: Team Alpha built a service 2 years ago. Team Beta is inheriting it as Team Alpha is reorged.

**Pain**: Typical handoff doc is 80 pages of outdated Confluence that nobody reads. Agent is as lost as the new team.

**Flow**:
```
Team Alpha lead → SwarmAI: "Generate full AI-Ready for data-pipeline service. 
                             Here's our design doc, ops runbook, and I'll answer questions."

Engine: Full 7-phase run including deep ENRICH (Alpha lead answers 5 questions about 
        gotchas, operational quirks, and current state).

Output: zip bundle delivered to Team Beta.

Team Beta → runs install.sh in their Kiro workspace → immediately:
  - Agent knows the 3-module architecture without asking
  - Agent warns about the memory leak workaround in batch processor (IMPROVEMENT.md)
  - Agent respects the "never deploy on settlement day" rule (PRODUCT.md constraint)
  - Agent can answer "what calls what" from code-intel.json

Team Beta lead reviews REVIEW-REPORT.md → assigns TECH.md to their senior eng,
PROJECT.md they rewrite themselves (new priorities).

Result: Handoff from months → days. Knowledge transfer via structured artifacts, not meetings.
```

**What teams touch**: Alpha provides input + answers ENRICH. Beta runs install.sh + reviews report + rewrites PROJECT.md (their priorities differ).

---

### Summary: Who Interacts With What

| Role | Provides Input | Reviews | Cares About |
|------|---------------|---------|-------------|
| **Engineer** | Repo, wiki, "here's our gotchas" | TECH.md, IMPROVEMENT.md | Agent follows conventions, doesn't break things |
| **PM / TPM** | PRD, priorities, constraints | PRODUCT.md, PROJECT.md | Agent respects boundaries, optimizes right metric |
| **Knowledge Expert** | Incident history, Slack, postmortems | IMPROVEMENT.md | Institutional memory preserved and active |
| **Director / Stakeholder** | List of repos | Aggregate scorecard | Where to invest, which teams need help |
| **Receiving Team** | Nothing (gets artifacts) | REVIEW-REPORT.md, rewrites PROJECT.md | Agent works immediately on inherited codebase |
| **Agent (primary consumer)** | — | — | Read AGENTS.md → load relevant DDD file → execute task correctly |

---

## What Agents Actually Need to Understand

| Layer | Question | Who Provides | DDD File |
|-------|----------|-------------|----------|
| **Purpose** | Why does this exist? For whom? What's out of scope? | PM / Business | PRODUCT.md |
| **Architecture** | How is it built? What patterns? What invariants? | Engineer | TECH.md |
| **History** | What failed? What works? What are the known gotchas? | Team | IMPROVEMENT.md |
| **Current State** | What's the priority now? What's blocked? What decisions are pending? | Lead / PM | PROJECT.md |
| **Structure** | What depends on what? What's the blast radius? | Auto-generated | code-intel.json |

### Why Separation Matters

**For agents** (progressive loading — only read what the task needs):
| Agent Task | Needs | Doesn't Need |
|-----------|-------|-------------|
| "Add a new endpoint" | TECH (conventions, architecture) | PRODUCT, PROJECT |
| "Should we add caching?" | PRODUCT (constraints, non-goals) + IMPROVEMENT (past decisions) | TECH details |
| "Fix this bug" | TECH (code structure) + IMPROVEMENT (known issues) | PRODUCT, PROJECT |
| "Plan next sprint" | PROJECT (priorities) + PRODUCT (goals) | TECH details |
| "Refactor module X" | TECH (deps) + IMPROVEMENT (what broke before) + code-intel | PRODUCT, PROJECT |

**For human reviewers** (each stakeholder reviews their domain):
| File | Reviewer | Review Focus |
|------|---------|-------------|
| PRODUCT.md | PM + Business stakeholder | "Does this accurately describe our goals and boundaries?" |
| TECH.md | Senior Engineer / Architect | "Are these our real conventions and architecture?" |
| IMPROVEMENT.md | Whole team | "Are these gotchas still current?" |
| PROJECT.md | Lead + PM | "Are these the actual priorities?" |
| code-intel.json | Auto-verified via VERIFY phase | N/A |

---

## 3 Levels of AI-Readiness

| Level | What's Present | Agent Can | Agent Cannot |
|-------|---------------|-----------|-------------|
| **Level 1: Navigable** | AGENTS.md + TECH.md + code-intel.json (with routes) | Find files, find handlers, run build, follow conventions | Make judgments, avoid gotchas, understand priorities |
| **Level 2: Safe** | + IMPROVEMENT.md | + Avoid known pitfalls, understand blast radius | Make business decisions, understand priorities |
| **Level 3: Autonomous** | + PRODUCT.md + PROJECT.md (full DDD) | + Make judgment calls, understand what matters, know boundaries | Nothing — full project understanding |

**Engine target**: Always produce Level 3 structure. If user skips business context during ENRICH → PRODUCT.md and PROJECT.md are skeleton/placeholder (agent still benefits from Level 2). Graceful degradation, not binary.

---

## Technical Approach

### Output Artifacts

```
project-root/
├── AGENTS.md                        ← Agent entry point (≤150 lines, links to .ai-ready/)
├── .ai-ready/
│   ├── PRODUCT.md                   ← Why: purpose, audience, non-goals, success criteria
│   ├── TECH.md                      ← How: architecture, conventions, stack, invariants
│   ├── IMPROVEMENT.md               ← Learned: what failed, what works, gotchas, patterns
│   ├── PROJECT.md                   ← Now: current priorities, decisions, blockers
│   ├── code-intel.json              ← Graph: modules, deps, entry points, blast radius
│   ├── ai-ready.json                ← Meta: version, score, freshness, staleness config
│   └── REVIEW-REPORT.md            ← For humans: what engine found, confidence, gaps
```

### Engine Bundle (generated before installation)

```
.artifacts/ai-ready-{project-name}/
├── README.md                        # Quick start (1 command per IDE)
├── install.sh                       # Auto-detect IDE, non-destructive install
├── check-freshness.sh               # Standalone staleness check
├── [all content files]              # DDD docs + code-intel + metadata
├── scripts/ai-ready-parser.sh       # Zero-LLM structural refresh
├── skills/                          # IDE-specific refresh skills
├── ci-templates/                    # GitHub Actions / GitLab CI
└── ide-adapters/                    # Layout mappings per IDE
```

### code-intel.json Schema (v2 — 2026-05-30)

> Updated from v1 to add route awareness, structured risk scoring, dead code detection, and JSON Schema validation.
> See also: `Knowledge/Designs/2026-05-30-code-intel-v2-design.md` for SwarmAI-side implementation details.

```json
{
  "$schema": "https://ai-ready-repo.dev/schemas/code-intel.v2.json",
  "version": "2.0",
  "generated_at": "2026-05-29T15:10:00+08:00",
  "git_hash": "abc123",
  "repo": {
    "name": "payment-service",
    "languages": {"typescript": 0.82, "python": 0.15, "shell": 0.03},
    "total_symbols": 1847,
    "total_edges": 2341
  },
  "modules": [
    {
      "name": "processing",
      "path": "src/processing/",
      "responsibility": "Payment flow orchestration",
      "entry_points": ["src/processing/handler.ts:processPayment"],
      "depends_on": ["database", "stripe-client", "ledger"],
      "depended_by": ["api-routes", "webhooks"],
      "test_coverage": "high",
      "churn_rate": "medium",
      "cohesion": 0.72,
      "key_files": ["handler.ts", "state-machine.ts", "validators.ts"],
      "file_count": 12
    }
  ],
  "edges": [
    {"from": "processing", "to": "database", "type": "runtime", "weight": "critical"},
    {"from": "webhooks", "to": "processing", "type": "event", "weight": "normal"}
  ],
  "routes": [
    {
      "method": "POST",
      "path": "/api/payments/process",
      "handler": "src/routes/payments.ts::processPayment",
      "framework": "express",
      "middleware": ["auth", "rateLimit"]
    },
    {
      "method": "GET",
      "path": "/api/payments/:id",
      "handler": "src/routes/payments.ts::getPayment",
      "framework": "express"
    },
    {
      "method": "POST",
      "path": "/webhooks/stripe",
      "handler": "src/webhooks/stripe.ts::handleWebhook",
      "framework": "express",
      "middleware": ["verifyStripeSignature"]
    }
  ],
  "entry_points": [
    {"path": "src/server.ts", "type": "http", "description": "Express app, port 3000"},
    {"path": "src/workers/reconciliation.ts", "type": "cron", "description": "Daily 2am UTC"}
  ],
  "hot_zones": [
    {"path": "src/processing/state-machine.ts", "commits_30d": 45, "risk": "high", "reason": "active refactor, 18 callers"}
  ],
  "risk_areas": [
    {"module": "legacy", "dimension": "test_gap", "score": 0.8, "detail": "high churn + zero test coverage"},
    {"module": "webhooks", "dimension": "security_surface", "score": 0.6, "detail": "external input, signature verification critical"}
  ],
  "dead_code": [
    {"id": "src/legacy/old-processor.ts::legacyProcess", "last_touched": "2026-01-15", "confidence": 0.9}
  ],
  "dependencies": {
    "processing → database": {"edges": 23, "direction": "downstream"},
    "webhooks → processing": {"edges": 8, "direction": "upstream"}
  }
}
```

**v2 additions over v1:**

| Field | Purpose | Value to Agent |
|-------|---------|----------------|
| `routes` | URL→handler mapping (framework-aware) | Agent finds handler in 1 lookup instead of 3-5 grep attempts |
| `repo.languages` + `total_symbols/edges` | Quantified codebase size | Agent calibrates exploration scope |
| `modules[].cohesion` | Internal vs external edge ratio | Agent knows which modules are tightly coupled |
| `hot_zones[].risk` | Scored severity (not just listed) | Agent prioritizes caution |
| `risk_areas[].score` | 0-1 per dimension | Machine-readable risk, not prose |
| `dead_code` | Unused exported symbols with last-touch date | Agent can suggest cleanup; won't call dead functions |
| `dependencies` (directional) | Blast radius shortcut | Agent knows "change X → check Y" without traversing graph |
| `$schema` | JSON Schema URL | Tooling can validate, IDE can provide autocomplete |

**Git strategy:** code-intel.json is **committed** in AI-Ready-Repo output (cold-start agents need it). SwarmAI internal equivalent stays in SQLite (FS watcher keeps it real-time, no git churn).
```

---

## E2E User Flow

### Phase 1: INPUT (Human Touchpoint #1)

**Required**: Repo path(s) — local path or git URL (multiple packages supported)

**Optional (richer input → richer output)**:
| Signal Source | How to Provide | What Engine Extracts |
|---|---|---|
| Design docs / PRDs | File paths, URLs, paste | Purpose, decisions, architecture |
| Wiki / Confluence | URL (engine fetches) | Tribal knowledge, ops context |
| Slack exports / meeting notes | JSON export or text | Decisions, known issues |
| Issue tracker | URL or JSON | Known bugs, tech debt patterns |
| Existing CLAUDE.md / AGENTS.md | File path | Baseline for incremental update |
| Verbal context | Natural language | Domain constraints, priorities |
| Dashboard/runbook URLs | List | Operational context |
| DDD docs (if already exist) | Directory path | Full context (one-click export) |

**Principle**: Everything optional except repo path. Engine gracefully degrades — Level 3 with full input, Level 1-2 without.

### Phase 2: INGEST (Autonomous)

- Clone/mount repo(s)
- Parse all provided docs (PDF, MD, HTML, plain text)
- Extract decisions, gotchas, tribal knowledge from signals
- Build file inventory
- Tech stack auto-detection (LLM-driven — all languages Day 1, no per-language parser needed)

### Phase 3: UNDERSTAND (Autonomous)

- **Code Intelligence**: LLM reads code → builds module graph (deps, entry points, responsibilities)
- **Module detection**: File structure + import patterns → identify boundaries
- **Route extraction** (v2): Detect web framework → parse URL→handler mappings → populate `routes[]` in code-intel.json. Supported Day 1: FastAPI, Express, Next.js. Regex-first detection (decorators/route definitions have predictable syntax). Framework auto-detected from imports/package.json/requirements.txt.
- **Entry points**: Trace API surface, CLI commands, event handlers, workers
- **Pattern extraction**: Naming conventions, error handling style, test patterns
- **Hot zones**: git log → frequently modified files (30 days), scored by callers × churn
- **Tribal knowledge mining**: git blame + commit messages → gotchas
  - Grammar: WHEN [trigger] → RISK [what breaks] → BECAUSE [evidence: commit hash/date]
  - Rule: if evidence slot can't be filled, drop the gotcha
- **Risk areas**: 6-dimension scoring (module_spread, test_gap, caller_count, security_surface, file_churn, module_crossing) — each 0.0-1.0
- **Dead code detection**: Exported non-entry-point symbols with zero incoming edges
- **Build verification**: Actually run build + test commands, confirm they work

### Phase 4: ENRICH (Human Touchpoint #2)

Agent asks **only what it can't infer** (max 5 targeted questions):

For PRODUCT.md:
- "Who are the primary users of this service?"
- "What is explicitly NOT in scope?"
- "Any compliance/regulatory constraints?"

For PROJECT.md:
- "What are you focused on this quarter?"
- "Any major refactors planned?"
- "What's blocking progress?"

For IMPROVEMENT.md:
- "Any gotchas not captured in code that burned you recently?"

User answers inline or says "skip". All optional — engine generates best-effort from code + git alone.

### Phase 5: GENERATE (Autonomous)

Produce all DDD files + AGENTS.md + code-intel.json + REVIEW-REPORT.md.

**Multi-package**: Fan out (up to 4 parallel), per-package DDD + synthesize cross-package context.

### Phase 6: VERIFY (Autonomous)

Uses SwarmAI's existing sub-agent mechanism (same as adversarial review):
- Spawn via Claude SDK with worktree isolation
- System prompt contains ONLY generated artifacts (no session history, no DDD)
- 3 tasks selected from git log:
  - **Selection**: most recent bug fix (commit with `fix:`/`hotfix:`), most recent feature (`feat:`), most recent refactor (`refactor:`/large-diff non-feature). Falls back to most recent 3 commits of any type if conventional commits not used.
  - **Fallback (git history < 10 commits)**: Synthesize 3 tasks from README + TECH.md: (1) "add an endpoint following existing patterns", (2) "find where to fix a bug in module X", (3) "run the test suite". These test navigation, convention compliance, and build understanding.
- **Pass criteria**: agent identifies correct file within 2 tool calls, doesn't violate any Critical Rule from AGENTS.md, produces a plausible approach (not "I don't know where to look")
- **Fail criteria**: agent navigates to wrong module, violates stated convention, or requires >4 tool calls to orient. Specific failure reason fed back to GENERATE (max 2 iterations)
- **New project (no meaningful history)**: VERIFY tests only navigation and convention compliance (Levels 1-2). Tribal knowledge validation (Level 3) skipped — IMPROVEMENT.md will be skeletal by design.

### Phase 7: DELIVER

User chooses distribution:
1. **Install to workspace** → auto-run install.sh (detects IDE)
2. **Push to Git repo** → create branch + PR/CR with REVIEW-REPORT.md as PR description
3. **Zip package** → downloadable bundle for distribution
4. **Raw files** → leave in .artifacts/

---

## Installation — Zero Config, Non-Destructive

### install.sh Behavior

```
1. Auto-detect IDE:
   - .kiro/ directory → Kiro
   - .claude/ or CLAUDE.md → Claude Code
   - Fallback: Claude Code (AGENTS.md is most universal)

2. Place AGENTS.md at project root (coexists with CLAUDE.md)

3. Place DDD files + code-intel.json in .ai-ready/ directory

4. Install refresh mechanism:
   - Parser script (bash, zero deps)
   - Refresh skill (SKILL.md — IDE agent uses this)
   - Hook config (MERGED into existing settings, tagged _source for clean uninstall)

5. Generate WHAT_WAS_ADDED.md manifest

6. Print: "Your agent now has full project understanding. See REVIEW-REPORT.md for what to verify."
```

### Conflict Resolution

| Situation | Behavior |
|-----------|----------|
| AGENTS.md already exists | SKIP. Suggest merge. Never overwrite. |
| CLAUDE.md exists | Don't touch. Claude Code reads both AGENTS.md and CLAUDE.md. |
| .kiro/steering/ has existing rules | Add ai-ready-context.md alongside. Never modify existing files. |
| .ai-ready/ directory exists | Incremental update — merge new content, preserve `<!-- user -->` sections. |
| .claude/settings.json has hooks | Merge our hook entry (tagged `_source: ai-ready-engine`). |

### Post-Install Layout: Claude Code

```
project-root/
├── AGENTS.md                              ← Entry point (coexists with CLAUDE.md)
├── .ai-ready/
│   ├── PRODUCT.md / TECH.md / IMPROVEMENT.md / PROJECT.md
│   ├── code-intel.json
│   ├── ai-ready.json
│   └── REVIEW-REPORT.md
├── .claude/
│   ├── settings.json                      ← Hook merged (not overwritten)
│   ├── skills/s_ai-ready-refresh/SKILL.md
│   └── scripts/ai-ready-parser.sh
└── (all existing files untouched)
```

### Post-Install Layout: Kiro

```
project-root/
├── .kiro/
│   ├── steering/
│   │   ├── rules.md                       ← Untouched
│   │   └── ai-ready-context.md            ← Our AGENTS.md content (additive)
│   ├── specs/                             ← System specs in Kiro format
│   ├── docs/ai-ready/                     ← DDD files + code-intel
│   └── skills/ai-ready-refresh/SKILL.md
└── (all existing files untouched)
```

---

## Refresh Mechanism — Self-Maintaining Artifacts

### Problem

Static artifacts become actively misleading as code evolves. Stale context is worse than no context — it causes agents to make confidently wrong decisions.

### Solution: 3-Tier Refresh (borrowed from DDD Cultivation)

| Tier | Trigger | Cost | Scope | Automation |
|------|---------|------|-------|------------|
| **1: Structural** | Hook detects file/config change | Zero (bash) | code-intel.json, freshness markers | Full auto |
| **2: Semantic** | User says "refresh ai-ready" or notification | User's model (~2 min) | AGENTS.md + affected DDD sections | Semi-auto |
| **3: Full Re-gen** | Major refactor / quarterly / user request | SwarmAI engine | All artifacts | Manual |

### Tier 1: Parser (Zero LLM Cost)

`ai-ready-parser.sh` — bash + jq, language-agnostic:
- Detects new/removed directories
- Detects config file changes (package.json, pyproject.toml, Makefile, Cargo.toml, go.mod, pom.xml)
- Detects new files matching API/route patterns (v2: also detects route decorator changes)
- Re-extracts routes when router files change (regex-based, zero LLM — route decorators are syntactically predictable)
- Counts commits since last refresh
- Updates file-tree hash in ai-ready.json

Two modes:
- `--check-only`: exit 0 (fresh) / exit 1 (stale) — used by hook
- `--refresh`: Update code-intel.json structural data (modules + routes + hot zones) from file tree + git log

### Tier 2: Refresh Skill (IDE Agent Executes)

Installed as SKILL.md in user's IDE. The IDE agent itself maintains the artifacts:

```
Trigger: "refresh ai-ready" OR notification after Tier 1 detects staleness

Steps:
1. Run parser (Tier 1) → update code-intel.json
2. Diff current state vs ai-ready.json snapshot
3. For structural changes:
   - New module → add to AGENTS.md architecture, update TECH.md
   - Build command changed → update AGENTS.md ## Quick Start
   - Module deleted → remove from relevant files
   - New entry point → update AGENTS.md ## Entry Points
4. For knowledge changes:
   - Recent commits with fix:/revert:/hotfix: → candidate for IMPROVEMENT.md
   - Only add if evidence-grounded (cite commit)
5. Update ai-ready.json freshness metadata
6. NEVER rewrite entire files — patch affected sections only
7. NEVER touch <!-- user --> protected sections

Output: brief report of what changed + updated freshness scores
```

### Tier 3: Full Re-generation (SwarmAI Engine)

User returns to SwarmAI: "refresh ai-ready for project X"
→ Engine re-runs INGEST→VERIFY with incremental awareness
→ Reads existing artifacts as baseline, preserves user additions
→ Generates new REVIEW-REPORT.md highlighting what changed

### Freshness & Decay

```json
{
  "freshness": {
    "overall": "fresh",
    "last_structural_check": "2026-05-29T15:10:00+08:00",
    "last_semantic_refresh": "2026-05-29T15:10:00+08:00",
    "commits_since_refresh": 12,
    "per_file": {
      "PRODUCT.md": {"status": "fresh", "last_verified": "2026-05-29"},
      "TECH.md": {"status": "fresh", "last_verified": "2026-05-29"},
      "IMPROVEMENT.md": {"status": "aging", "last_verified": "2026-05-15", "days_old": 14},
      "PROJECT.md": {"status": "stale", "last_verified": "2026-04-20", "days_old": 39}
    }
  }
}
```

**Decay markers in content** (agents see staleness inline):
```markdown
## Known Issues
1. Stripe webhooks arrive out of order — handler must be idempotent
2. [⚠️ unverified 14d] The "pending" state can last 72h for bank transfers
3. Reconciliation worker OOMs if batch > 50K rows
```

Rule: if a gotcha's related code was refactored but the gotcha wasn't re-verified → mark `[⚠️ unverified Nd]`. Agent treats as "possibly stale, verify before relying."

### Hook Configuration (Claude Code)

```jsonc
{
  "hooks": {
    "FileChanged": [{
      "pattern": ["src/**/index.*", "package.json", "pyproject.toml",
                  "Makefile", "Cargo.toml", "go.mod", "**/routes/**", "**/api/**"],
      "command": "bash .claude/scripts/ai-ready-parser.sh --check-only",
      "onFailure": "notify",
      "message": "🔄 Code structure changed — run 'refresh ai-ready' to update context.",
      "_source": "ai-ready-engine"
    }]
  }
}
```

`_source` tag enables clean uninstall — removes only our hook entries.

### User-Added Content Protection

All DDD files end with:
```markdown
<!-- user: Your additions below — refresh preserves this section -->
```

Refresh skill NEVER modifies content below this marker.

---

## Knowledge Ingestion — User → Artifacts

### The Missing Direction

Refresh handles `Code changes → Artifacts update`. But there's another direction entirely uncovered:

```
Current:  Code changes → detect → refresh artifacts       ✅
Missing:  User provides knowledge → ??? → artifacts enriched  ❌
```

Real scenarios with no current path:
| User Action | Should Flow Into | Current Handling |
|---|---|---|
| Drops a PRD into workspace | PRODUCT.md (purpose, non-goals) | Lost |
| Tells agent "we never deploy on Fridays" | IMPROVEMENT.md (gotcha) | Lost next session |
| Corrects agent "no, use repository pattern" | TECH.md (convention) | Lost next session |
| Shares a decision doc | PROJECT.md (recent decisions) | Lost |
| Says "remember: this API sunset next month" | PROJECT.md (blocker) | Lost |

### Solution: Refresh Skill "Learn" Mode

The refresh skill gets a second mode:

```
Mode 1: "refresh ai-ready"       → Code → Artifacts (existing)
Mode 2: "learn this" / "ai-ready ingest"  → User input → Artifacts (NEW)
```

### Three Trigger Paths

**1. Explicit statement (user tells agent)**
```
User: "ai-ready learn: we use feature flags for all new endpoints. 
       Never ship without a flag."

→ Classified as: TECH.md convention
→ Appended: "ALWAYS use feature flags for new endpoints. Never ship directly to production."
→ Tagged: [added: 2026-05-29, source: user]
```

**2. Document ingest (file appears in workspace)**

Hook detects new docs:
```jsonc
{
  "pattern": ["docs/**/*.md", "docs/**/*.pdf", "*.prd", "design-*.md", 
              "adr-*.md", "postmortem-*.md"],
  "message": "📄 New document detected. Say 'ai-ready ingest [filename]' to extract into DDD context.",
  "_source": "ai-ready-engine"
}
```

On confirm → skill reads file → extracts relevant claims → presents grouped by target file → user approves → writes.

**3. Correction capture (agent learns from being corrected)**
```
Agent: "I'll add a direct database query—"
User: "No! Always use repository pattern."
Agent: "Got it. 💡 Want me to add this to .ai-ready/TECH.md so I remember next time?"
User: "Yes"
→ TECH.md ## Conventions: "NEVER use raw SQL. ALWAYS access DB through repository pattern."
```

### Classification Logic (LLM Intent Classification)

Classification is **LLM-driven intent analysis**, not keyword matching. The LLM reads the full statement in context and classifies by semantic intent. The table below shows representative signal patterns — they are training examples for the prompt, not an exhaustive rule set:

| Input Signal | Target File | Example Patterns |
|---|---|---|
| Purpose, audience, constraints | PRODUCT.md | "users are", "out of scope", "compliance", "we don't do" |
| Architecture, conventions, patterns | TECH.md | "always use", "never call", "pattern", "convention" |
| Failures, gotchas, incidents | IMPROVEMENT.md | "burned by", "don't touch", "broke when", "revert" |
| Priorities, blockers, decisions | PROJECT.md | "this quarter", "blocked", "decided to", "don't change until" |

**Multi-language support**: LLM classification works identically across English, Chinese, and mixed-language repos — no tokenization or keyword boundary assumptions. The model understands intent regardless of language.

### Rules
- NEVER overwrite existing entries (append only)
- ALWAYS cite source: `[added: {date}, source: user]` or `[source: {filename}, {date}]`
- ALWAYS ask confirmation before writing to any DDD file
- If classification unclear → ask user: "Is this a convention (TECH) or a lesson (IMPROVEMENT)?"
- Entries added via learn mode are placed ABOVE the `<!-- user -->` marker (engine-managed section)

### Full Lifecycle With Learn Mode

```
Day 1:  Engine generates → install.sh → agent has baseline context
Day 3:  Code changes → Tier 1 parser → Tier 2 refreshes TECH.md
Day 5:  User drops PRD → notification → "ingest" → PRODUCT.md enriched
Day 8:  Agent corrected → "capture?" → yes → TECH.md gains convention
Day 12: User: "learn: deprecated v1 API by Q3" → PROJECT.md updated
Day 30: 50+ commits → Tier 2 full refresh (preserves all learned entries)
Day 90: SwarmAI Tier 3 re-gen (reads all learned entries as baseline, never loses them)
```

---

## Multi-Package Support

### Scenario
```
"Give me AI-Ready for our payment system — 3 repos: frontend, backend, infra"
```

### Engine Behavior
1. Fan out analysis (up to 4 packages parallel)
2. Each package gets independent AGENTS.md + .ai-ready/ (full DDD)
3. Synthesize cross-package context (API contracts, deploy order, shared types)

### Output
```
.artifacts/ai-ready-payment-system/
├── payment-frontend/              ← per-package DDD
│   ├── agents.md + product/tech/improvement/project.md + code-intel.json
├── payment-backend/
│   └── ...
├── payment-infra/
│   └── ...
├── cross-package/                 ← system-level
│   ├── system-map.md
│   ├── cross-deps.json
│   └── deploy-order.md
└── install.sh --workspace ~/code/
```

---

## AI-Ready Score (9 Dimensions)

| Dimension | 0 (unusable) | 5 (passable) | 10 (excellent) |
|-----------|---|---|---|
| **Navigation** | No README, no structure | README exists but stale | AGENTS.md + module map + entry points |
| **Build/Test** | Can't figure out how to run | Commands exist but fail | One-command verified build + test + lint |
| **Architecture** | Black box | README describes but doesn't match | DDD TECH.md matches reality, boundaries clear |
| **Conventions** | No rules | Scattered in comments | Structured, prescriptive, agent-executable |
| **Tribal Knowledge** | All in people's heads | Some in commit messages | IMPROVEMENT.md: evidence-grounded, current |
| **Code Graph** | None | Partial module awareness | Full deps + entry points + blast radius + dead code |
| **Route Coverage** | Web framework detected, no route map | Partial routes (main app only) | All URL→handler mapped, middleware visible |
| **Test Safety** | No tests | Tests exist but broken CI | CI + coverage + safety-critical paths identified |
| **Ops Context** | Don't know how to deploy | Runbook somewhere | Deploy chain + dashboards + alarms + on-call |

> **Route Coverage** added in v2 (2026-05-30): For web projects, knowing "POST /api/payments → processPayment()" saves the agent 3-5 tool calls per endpoint interaction. Score formula: `detected_routes / (detected_routes + unresolved_handler_references)`. Non-web projects (CLI, library) get automatic 10/10 (not applicable = no penalty).

---

## Competitive Comparison

| Dimension | AI-Native Brownfield Bootstrapper | agents.md Spec | AI-Ready-Repo Engine (ours) |
|---|---|---|---|
| Knowledge model | AGENTS.md (flat, single file) | AGENTS.md (template) | **DDD 4-file (layered, progressive)** |
| Scope | Code navigation only | Code navigation only | **Full project understanding (why + how + history + now)** |
| Stakeholder support | Engineer only | Engineer only | **PM reviews PRODUCT, Engineer reviews TECH, Team reviews IMPROVEMENT** |
| Platform | Amazon-only | Platform-agnostic (spec) | Platform-agnostic (spec + engine) |
| Self-maintenance | None (static) | N/A | **3-tier refresh + decay markers** |
| Multi-package | Yes | No | Yes + cross-package synthesis |
| Evidence grounding | CRs + wiki (Amazon) | None | git history + user signals (universal) |
| Install UX | Manual | Manual | Zero-config auto-detect |
| Verification | None | None | **Fresh agent self-test** |
| Human review | None | None | **REVIEW-REPORT.md with per-doc assignments** |

### Honest Assessment: Where Others Are Better

- **Brownfield Bootstrapper**: Deeper Amazon ecosystem integration — auto-mines CRs, SIM tickets, and wiki pages using internal APIs we can't access externally
- **Kiro native specs**: Tighter IDE integration — no install.sh needed if Kiro builds DDD support natively
- **agents.md spec**: Simpler adoption curve — 1 file is easier to explain than 4+1

### What We Adopt from Bootstrapper
1. ✅ "Detect, don't assume" — auto-detect everything from code
2. ✅ ≤150 lines entry point (backed by their internal research on diminishing returns)
3. ✅ "Every line must prevent a systemic mistake" — deletion filter
4. ✅ Two human touchpoints only (Input + Enrich)
5. ✅ Grammar-based tribal knowledge (WHEN/RISK/BECAUSE)
6. ✅ Multi-package fan-out with cross-system synthesis
7. ✅ Verify with fresh agent

### What We Add Beyond Bootstrapper
1. **DDD 4-file model** — full project understanding, not just code navigation
2. **Multi-stakeholder review** — PM, Engineer, Team each review their domain
3. **Self-maintaining** — 3-tier refresh + per-file freshness + decay markers
4. **IDE-native installation** — zero-config + non-destructive + clean uninstall
5. **Code Intelligence graph** — structured JSON with blast radius
6. **Platform-agnostic** — Claude Code + Kiro + Codex (extensible)
7. **Scoring + Review Report** — quantified readiness + clear next actions
8. **Graceful degradation** — Level 1-3 based on available input

---

## Open Standard Publication

### What We Publish (open)
1. **Spec**: "The AI-Ready-Repo Standard" — defines DDD structure for AI-ready codebases
2. **Scoring rubric**: 8-dimension self-assessment anyone can run
3. **Template files**: Empty DDD templates with section guidance
4. **Philosophy doc**: Why layered knowledge > flat AGENTS.md

### What We Don't Publish (SwarmAI advantage)
- The engine (auto-generation from code + signals)
- The refresh mechanism (self-maintaining artifacts)
- The verify phase (quality assurance via fresh agent test)

**Narrative**: "The standard is open. The best way to achieve it is SwarmAI."

### Publication Vehicles
- GitHub Discussion: "What Makes a Codebase AI-Ready — The DDD Approach"
- Lightweight spec repo (like agents.md did)
- Reference implementation: SwarmAI's own repo as exemplar

---

## Security Considerations

- Engine runs locally within SwarmAI (user's machine). No code leaves the device unless user explicitly pushes to git.
- Artifacts contain code structure metadata (module names, file paths, dependency relationships) but NOT source code content.
- IMPROVEMENT.md may contain commit hashes — these are public information in any git repository.
- install.sh performs only local file operations — no network calls, no telemetry, no exfiltration.
- For Amazon internal use: generated artifacts inherit the same classification as the source repository.
- code-intel.json reveals architectural structure — teams should treat it with the same sensitivity as architecture diagrams.

---

## Known Limitations

| Limitation | Impact | Mitigation |
|---|---|---|
| **Monorepos >1M LOC** | LLM can't analyze every file | Sampling: top 200 files by churn + all entry points + all config files |
| **Generated code (protobuf, codegen)** | Pollutes module detection | Detected via heuristics (generated comment headers, known output paths) and excluded |
| **Binary assets** | Cannot be analyzed | Ignored entirely — only text-based source files |
| **Private/proprietary frameworks** | Conventions harder to detect | ENRICH phase asks user; if skipped, TECH.md conventions section may be thin |
| **PROJECT.md half-life** | ~2-4 weeks — most volatile file | Freshness tracking marks stale quickly; designed for frequent human update |
| **Tier 2 refresh model dependency** | Opus >> Sonnet for architectural understanding | Skill written with explicit mechanical steps (not requiring judgment); gracefully falls back to "recommend full re-gen" on complex changes |
| **AGENTS.md auto-read** | Not yet verified that Claude Code reads AGENTS.md automatically alongside CLAUDE.md | Must verify before Phase 1 launch; fallback: install.sh adds one reference line to CLAUDE.md |

---

## Token Budget & Progressive Loading

### Target Artifact Sizes

| Artifact | Target Size | Tokens (~) | Rationale |
|----------|------------|-----------|-----------|
| AGENTS.md | ≤150 lines | ~2,000 | Entry point — always loaded, must fit in any IDE's steering window |
| PRODUCT.md | 80-200 lines | 1,500-3,500 | Business context, loaded for design/priority tasks |
| TECH.md | 150-400 lines | 2,500-6,000 | Most detail-heavy; loaded for coding tasks |
| IMPROVEMENT.md | 50-150 lines | 800-2,500 | Gotchas + history; loaded for refactors and bug fixes |
| PROJECT.md | 30-80 lines | 500-1,200 | Most volatile; loaded for planning tasks |
| code-intel.json | Varies by repo | 2,000-8,000 | Module graph; loaded for navigation and blast radius |
| **Total (all loaded)** | | **~10K-23K** | Well within model context windows (1M). Note: Claude Code's per-task budget defaults to 128K — artifacts + tool results must fit within this, not just the model window. See "Implementation Pitfalls P2" below. |

### Progressive Loading Strategy

Not all artifacts load simultaneously. The agent entry point (AGENTS.md) includes a loading directive:

```markdown
## Deep Context (DDD)
Load based on task type — do NOT load all at once:
- Coding tasks → TECH.md + IMPROVEMENT.md + code-intel.json
- Design decisions → PRODUCT.md + IMPROVEMENT.md  
- Planning → PROJECT.md + PRODUCT.md
- Bug fixes → IMPROVEMENT.md + TECH.md + code-intel.json
- "I don't know where to start" → code-intel.json only
```

### IDE Context Window Compatibility

| IDE/Agent | Context Window | Budget for AI-Ready | Strategy |
|-----------|---------------|--------------------|---------| 
| Claude Code (1M) | ~1,000K | Unlimited — load all | Full injection, no concern |
| Kiro (steering) | ~8K steering + on-demand | 2K steering + on-demand file reads | AGENTS.md in steering; DDD loaded via skill |
| Codex (128K) | ~128K | ~30K available | Progressive loading (2-3 files per task) |
| Future agents (32K budget) | 32K | ~8K available | AGENTS.md only (Level 1); DDD via tool calls |

**Design invariant**: AGENTS.md (≤2K tokens) must be self-sufficient for Level 1 (navigation + build + conventions). DDD files are strictly additive depth — the system degrades gracefully at any context window size.

**Enforcement**: GENERATE phase runs token count on all artifacts. If any single DDD file exceeds 6K tokens → automatically splits into `{FILE}-summary.md` (≤2K, always loaded) + `{FILE}-detail.md` (on-demand reference). This ensures compatibility with constrained environments without sacrificing depth.

---

## Design Principles

| # | Principle | Source | Implementation |
|---|-----------|--------|----------------|
| 1 | **Detect, don't assume** | Brownfield Bootstrapper | Auto-detect stack, framework, patterns — LLM-driven, all languages Day 1 |
| 2 | **≤150 lines entry point** | Bootstrapper team internal research | AGENTS.md is brief; DDD docs go deeper, loaded on demand |
| 3 | **Every line earns its place** | Bootstrapper | Filter: "Will the agent make a systemic mistake without this line?" |
| 4 | **Two human touchpoints** | Bootstrapper | Input (what to analyze) + Enrich (answer targeted questions). Everything else autonomous |
| 5 | **Zero-config installation** | Our design | `install.sh` auto-detects IDE, one command, never overwrites existing config |
| 6 | **Self-maintaining artifacts** | Our design (DDD Cultivation) | Installed artifacts include refresh skill + parser — IDE agent maintains them |
| 7 | **Multi-package native** | Bootstrapper | Fan out analysis per package, produce cross-system context |
| 8 | **Separation: content vs placement** | Our design | Artifacts are IDE-agnostic content; `ide-adapters/` maps placement per IDE |
| 9 | **Knowledge has layers** | DDD philosophy | Different stakeholders review different docs. Progressive disclosure for agents |
| 10 | **Knowledge must evolve or die** | DDD Cultivation | Freshness tracking, decay markers, staleness detection |
| 11 | **Judgment > Description** | DDD | "Never call X directly" (judgment) beats "X exists" (description) |
| 12 | **Evidence-grounded** | Bootstrapper + DDD | Tribal knowledge backed by commit hash/issue — if can't ground, don't write |

---

## Implementation Pitfalls (Learned 2026-06-01)

Traps discovered during SwarmAI production that the Engine MUST avoid in generated artifacts and in its own execution:

### P1: Hook/Context Injection Dedup

**Trap**: IDE hooks (PreToolUse, system-reminders) inject context on every tool call. If the same file is accessed 20 times, the same annotation is injected 20 times — burning ~5K tokens of task budget on zero-information-gain noise.

**For the Engine**: Any generated hooks (e.g. Code Intel annotations, linting context) MUST include per-session dedup. First access → inject. Subsequent → skip. Implementation: `_seen_files: set` in hook closure.

**For generated AGENTS.md**: Keep it under 150 lines (already a principle), but also note: AGENTS.md is injected on EVERY system-reminder refresh. If it contains redundant info already in the system prompt, it doubles the cost.

### P2: Task Budget ≠ Context Window

**Trap**: Claude Code CLI has a `task_budget` (default 128K tokens) that triggers autocompact when a single user→agent interaction chain exceeds it. This is SEPARATE from the model's context window (1M). Deep investigations that read many files hit 128K in tool results alone → mid-task compaction → agent loses all accumulated understanding.

**For the Engine**: When generating artifacts for large codebases (>500 files), the UNDERSTAND phase will consume many tool calls. The Engine MUST:
1. Set `task_budget=800_000` for desktop execution (or higher if available)
2. Use progressive summarization during INGEST — don't dump raw file content into context; extract key patterns first, then deep-dive selectively
3. Prefer `Grep` (returns lines) over `Read` (returns full segments) for exploration
4. Use sub-agents for parallelizable analysis — each sub-agent gets its own task budget

### P3: Generated Artifacts Must Not Duplicate System Prompt Content

**Trap**: If AGENTS.md repeats info from CLAUDE.md or other always-loaded files, every API call pays double tokens for the same content. With 85 skills × 71 tok = 6K already in system prompt, duplicating conventions/rules wastes budget fast.

**For the Engine**: During GENERATE, cross-reference existing CLAUDE.md / settings.json. If a rule already exists there, reference it (`See CLAUDE.md`) rather than restating it in AGENTS.md.

### P4: Avoid Tool-Loop Patterns in Generated Refresh Skills

**Trap**: Refresh/maintenance skills that re-read the same files repeatedly trigger CompactionGuard (tool-loop detection) which kills the session. If the generated "ai-ready-refresh" skill reads all N modules sequentially, it looks like a loop to the guard.

**For the Engine**: Generated refresh skills should:
1. Use `git diff --stat` first to identify CHANGED files only (not re-scan everything)
2. Batch file reads with distinct patterns (not sequential reads of the same directory)
3. Include a `# unique-salt: {timestamp}` in tool inputs to avoid hash collisions that trigger consecutive-identical-call detection

---

## SwarmAI Integration Points

| Subsystem | Role |
|---|---|
| **Code Intelligence** | parser.py + graph_store.py → seed code-intel.json (enhanced by LLM) |
| **Session Context** | User's verbal input during ENRICH |
| **DDD (if available)** | One-click export — existing DDD docs → AI-Ready artifacts directly |
| **Pipeline** | Engine runs through EVALUATE→DELIVER stages |
| **Skills** | `s_ai-ready-repo` skill wraps the engine |
| **Jobs** | Optional: scheduled freshness check for managed projects |
| **DDD Cultivation** | Freshness/decay model reused for artifact maintenance |

---

## Implementation Milestones (Revised 2026-06-01)

_Reordered after M1 implementation. Original order assumed bash parser + fresh verify design. M1 proved: Python helpers already cover parsing, User-Value Probe covers verification core. New order reflects actual dependency chain + customer value priority._

| Priority | Milestone | Deliverable | Validation | Effort | Status |
|---|---|---|---|---|---|
| ✅ | **M1: Single-repo DDD generation** | Engine produces 7 DDD files + AGENTS.md for any single repo | E2E demo on MemPalace (391 files, 1124 commits). 3 adversarial rounds. | 1 session | **DONE** (2026-06-01) |
| 1 | **M5→M2: IDE install** | `install.sh` — auto-detect Claude Code/Kiro, copy files, merge hooks config | E2E: install → open IDE → agent uses artifacts on first task | S (~1hr) | ✅ **DONE** (2026-06-01, commit 54f18cd3) |
| 2 | **M3→M3: Verified output** | Sub-agent VERIFY — spawn fresh agent with ONLY output, give 3 tasks from git log, verify correct file found | Intentionally omit a module → VERIFY catches it → feedback to GENERATE | M (~1 session) | ✅ **DONE** (2026-06-01, commit 6a3209fe) |
| 3 | **M2→M4: Self-maintaining** | Staleness detection + refresh trigger. Reuse `gather_repo_info()` diff against `ai-ready.json` snapshot. Hook config for auto-notification. | Add module → run check → stale detected → notification fires | M (~1 session) | ✅ **DONE** (2026-06-01, commit 4955b97d) |
| 4 | **M4→M5: Multi-package** | Per-package execution with cross-package synthesis. Fix 300-file cap per package (not global). | 3-repo system (frontend+backend+infra) → independent DDD + cross-deps | L (~2 sessions) | ✅ **DONE** (2026-06-01, commit 4955b97d) |
| 5 | **M6: Published standard** | GitHub Discussion + spec repo + scoring rubric + templates | Community signal (stars, comments, forks) | S (~1 session) | ✅ **DONE** (2026-06-01, ai-ready-repo commit 1c5fb94) |

**ALL MILESTONES COMPLETE + 5 COMPETITIVE FEATURES** (2026-06-01).
Done in 1 session — originally estimated 7. Actual: 9 pipeline runs, 20 commits, 29 tests, ~2500 lines.

---

## Competitive Features (adopted from Understand-Anything, 2026-06-01)

Based on analysis of [Understand-Anything](https://github.com/Lum1104/Understand-Anything) (48K stars, v2.7):

| Feature | What We Adopted | What We Kept Different |
|---|---|---|
| **12+ IDE support** | Platforms table in `install.sh` — one table drives all IDEs (Claude Code, Kiro, Cursor, Codex, Gemini, OpenCode, VS Code Copilot, Windsurf, Cline, Hermes, Trae, generic) | They use symlinks to skill dirs. We copy DDD files directly (no runtime dependency on plugin install). |
| **Incremental update** | `incremental_update()` — git diff against stored commit, returns only changed source files | They fingerprint files. We use git commit hash (simpler, equally effective for git repos). |
| **Worktree detection** | `install.sh` warns when target is ephemeral worktree + suggests main repo root | Same approach — they learned this from issue #133, we adopted their fix pattern. |
| **Guided Tours** | `generate_learning_tour()` — topologically-sorted learning order from import graph | They use multi-agent to generate narrative tours. We use deterministic topo-sort (cheaper, reproducible). |
| **Localization** | Language parameter in INPUT phase — all generated text in user's language | They support `--language` flag. We ask at INPUT and carry through GENERATE. |

### Where We Intentionally Diverge

| Understand-Anything | AI-Ready-Repo Engine | Why |
|---|---|---|
| Interactive Dashboard (React Flow) | Static DDD text files | Our consumer is the IDE agent's system prompt, not a human looking at a browser. Text > graph for LLM consumption. |
| Tree-sitter AST (WASM) | Regex import extraction + LLM code reading | Tree-sitter gives precise syntax. But our LLM-driven UNDERSTAND phase gives *semantics* (what functions DO, not just their signatures). For "can this agent modify the code?" — semantics matter more. |
| Single knowledge-graph.json | DDD 4-file model (PRODUCT/TECH/IMPROVEMENT/PROJECT) + code-intel.json | Separation enables progressive loading (task-type → specific file). One mega-JSON forces full load always. |
| No quality verification | VERIFY sub-agent + User-Value Probe + adversarial review | Their output quality is unverified. Ours is mechanically tested: "can a fresh agent USE this?" |
| File-level summaries | Function-level tables + data flow diagrams for hot zones | File-level = "miner.py handles mining." Function-level = "miner.process_file() calls palace.file_already_mined() which paginates ALL groups because ChromaDB has undefined ordering." The latter prevents bugs. |

---

## References & Related Work

| Project | Relationship | What We Learned |
|---|---|---|
| **[Understand-Anything](https://github.com/Lum1104/Understand-Anything)** (48K★) | Primary competitor. Dashboard + graph visualization for codebases. | Platforms table pattern, incremental updates, worktree pitfall, localization. |
| **SwarmAI Code Intelligence** (`backend/core/code_intel/`) | Internal implementation of the same concepts. `json_exporter.py` exports code-intel.json v2. | Schema reuse (same v2 format). Production lessons: prefix resolution, test filtering, reindex timeout, thread safety. |
| **SwarmAI DDD Cultivation** (`backend/core/ddd/`) | The internal system that keeps DDD docs alive via event-driven updates. | Freshness/decay model reused for `ai-ready.json`. Tier concept (structural→semantic→full) directly maps to our 3-tier refresh. |
| **SwarmAI Autonomous Pipeline** (`skills/s_autonomous-pipeline/`) | Quality system that this engine runs through. | AC quality gate (Filter 3), User-Value Probe, adversarial review — all born from building this engine and discovering gaps. |
| **[agents.md spec](https://github.com/anthropics/agents-md)** | Community convention for AI agent context. | We extend it: AGENTS.md is our entry point (≤150 lines). The spec is flat; we add layered DDD depth behind it. |
| **[AI-Native Brownfield Bootstrapper](https://github.com/AINativeBrownfieldBootstrapper)** (Amazon internal) | Amazon-internal tool generating AGENTS.md from CRs/wiki. | "Detect don't assume", ≤150 line entry point, two human touchpoints, WHEN/RISK/BECAUSE grammar. All adopted in our design. |

---

## Final Architecture (as implemented)

```
User: "make [repo] AI-ready"
  │
  ├─ INPUT (Human Touchpoint #1)
  │    ├─ Repo path (required)
  │    ├─ Signal collection (multi-select: docs, wiki, verbal, etc.)
  │    └─ Output language (default: English)
  │
  ├─ INGEST (deterministic — ai_ready_helpers.py)
  │    ├─ gather_repo_info()      → file tree, tech stack, git stats
  │    ├─ parse_git_gotchas()     → evidence-grounded WHEN/RISK/BECAUSE
  │    └─ extract_import_graph()  → 1000+ edges from actual import statements
  │
  ├─ UNDERSTAND (LLM reads actual code — minimum 8 files)
  │    ├─ Function-level tables for top 3-5 hot-zone files
  │    ├─ Conventions with 2+ file citations each
  │    ├─ Data flow diagrams (E2E trace)
  │    └─ Extension points documentation
  │
  ├─ GENERATE (produce 7 files)
  │    ├─ AGENTS.md (≤150 lines, entry point)
  │    ├─ .ai-ready/PRODUCT.md, TECH.md, IMPROVEMENT.md, PROJECT.md
  │    ├─ .ai-ready/code-intel.json (v2 schema, validated)
  │    └─ .ai-ready/ai-ready.json + REVIEW-REPORT.md
  │
  ├─ VERIFY (sub-agent quality gate)
  │    ├─ select_verification_tasks() → 3 tasks from git log
  │    ├─ Spawn fresh agent with ONLY DDD output (no source)
  │    └─ evaluate_verification_response() → PASS (2/3) or FAIL + feedback
  │
  └─ DELIVER
       ├─ resolve_output_path() → deterministic location
       ├─ install.sh → 12 IDEs via platforms_table
       └─ generate_learning_tour() → topological onboarding order

Post-install maintenance:
  ├─ check_staleness()        → per-file fresh/stale status
  ├─ incremental_update()     → only re-analyze changed files
  ├─ generate_hook_config()   → FileChanged auto-notification
  └─ run_multi_package()      → per-package analysis + cross-pkg synthesis
```

---

## Implementation Status (2026-06-01)

### M1 Completed — Key Implementation Decisions

| Decision | What We Chose | Why | Alternative Rejected |
|---|---|---|---|
| Architecture | Skill (`s_ai-ready-repo`) + helper script (`ai_ready_helpers.py`) | LLM-driven analysis doesn't benefit from classes. Script handles deterministic ops (git, schema, templates). | Backend module `core/ai_ready/` — over-engineering (M2-M6 don't need Python backend) |
| Import graph | `extract_import_graph()` — automatic from source | Agent can't skip it (function call, not "remember to grep") | Manual grep instructions (agent skips them) |
| Output path | `resolve_output_path()` — deterministic: user target > SwarmWS .artifacts/ > alongside repo | Eliminates "wrote to /tmp, can't find it" | Hardcoded path (doesn't work outside SwarmAI) |
| Quality enforcement | Blocking quality gate in INSTRUCTIONS.md (7 checks before GENERATE) | Agent can't produce Level 1 output and claim Level 3 | Honor-system instructions only (agent rationalizes past them) |
| Line numbers | Prefixed with `~` (approximate) + commit hash + grep instruction | Lines drift after every commit. Function signature is the stable anchor. | Exact line numbers (misleading after 1 week) |
| Confidence claims | Per-scenario breakdown (hot-zone 85%, module-level 50%, unanalyzed 20%) | Honest. Blanket "90%" was falsified by adversarial review. | Single confidence number (false precision) |

### Output Levels (Formal Definition — from implementation)

| Level | What's Documented | Agent Can Do | Agent Cannot Do |
|---|---|---|---|
| **1: Navigable** | Module map + entry points + build commands | Find correct file, run build/test | Fix bugs, understand patterns |
| **2: Safe** | + conventions with file citations + gotchas with commit evidence + dependency graph | Avoid known mistakes, follow conventions | Modify complex code confidently |
| **3: Modifiable** | + function-level tables for hot zones + data flow diagrams + extension points + honest coverage % | Fix bugs in hot zones, add features following existing patterns | Modify unanalyzed modules without reading source |

**Design principle: Level 3 for hot-zone files, Level 2 for key modules, Level 1 for the rest.** Never claim blanket coverage.

### Implementation Pitfalls Added (P5-P7, from production)

| # | Pitfall | What Happened | Fix |
|---|---------|--------------|-----|
| P5 | Quality gate is text, not code | Agent claimed Level 3 with Level 1 output (README paraphrase). All 5 ACs passed because they tested existence, not quality. | Added Filter 3 "garbage-in test" to EVALUATE AC gate + User-Value Probe to DELIVER |
| P6 | Large repo silent truncation | `extract_import_graph` caps at 300 files, 500 edges. On 2000-file repos, 85% silently skipped. Output claims authority it doesn't have. | Honest coverage declaration mandatory. Future: configurable caps + warnings. |
| P7 | Line numbers become stale in 1 week | Agent trusts "line 720" as navigation anchor. After any commit, lands on wrong code. False confidence worse than no line numbers. | Prefix `~`, add commit hash, mandate grep-to-confirm for consumers. |

### Lessons Learned (from 3 adversarial rounds + 4 pipeline runs)

1. **Code reading is the ENTIRE value** — without it, output is a README paraphrase. No one pays for a paraphrase. The code-reading mandate in UNDERSTAND phase is non-negotiable.
2. **Function-level > module-level** — "miner.py imports palace.py" is useless for bug fixing. "miner.process_file() calls palace.file_already_mined() which paginates ALL groups because ChromaDB has undefined ordering" is actionable.
3. **Data flow diagrams are highest-ROI artifact** — one ASCII trace (CLI→mine→process_file→add_drawer) replaces reading 5 files. Agent can trace any bug through the call chain.
4. **Honest coverage > impressive numbers** — "7% files read, 85% confidence in hot zones, 20% elsewhere" builds trust. "90% confidence" (falsified by adversarial) destroys trust permanently.
5. **User-Value Probe is the right final gate** — asks: "can you point to 3 things only knowable from the work?" If output is derivable from `cat README.md` in 2 minutes, it's worthless regardless of how correct the JSON schema is.
6. **AC quality gate prevents pipeline theater** — "produces TECH.md" passes with Lorem Ipsum. "TECH.md conventions cite 2+ source files each" cannot. Filter 3 (garbage-in test) catches this at EVALUATE, before wasting BUILD tokens.
7. **Extension Points section is critical for feature work** — "add a webhook" is impossible without knowing where post-mine hooks plug in. If no hook system exists, saying "no hook system — add inline at [location]" is more useful than silence.

### Validated Output (MemPalace E2E Demo)

```
Projects/ai_ready_repo/.artifacts/ai-ready-mempalace/
├── AGENTS.md                  (53 lines)  ← IDE entry point
└── .ai-ready/
    ├── PRODUCT.md             (35 lines)  ← skeletal (code-only mode)
    ├── TECH.md               (150 lines)  ← function-level tables + data flow
    ├── IMPROVEMENT.md         (46 lines)  ← 7 function-level gotchas
    ├── PROJECT.md             (30 lines)  ← skeletal (code-only mode)
    ├── code-intel.json       (390 lines)  ← 12 modules, 14 edges, validated
    └── REVIEW-REPORT.md       (66 lines)  ← honest coverage + recommendations

Total: ~14K tokens. Fits in any model context window.
Score: 7.5/10 (gap: PRODUCT/PROJECT skeletal without ENRICH, KG/dedup undocumented)
```

---

## Success Criteria

1. Developer receives artifacts → runs `install.sh` → IDE agent correctly navigates codebase, avoids known gotchas, and respects non-goals on first task
2. PM reviews PRODUCT.md and says "yes, this accurately captures our intent"
3. After 50 commits, Tier 1 auto-detects staleness, Tier 2 refreshes in <2 min on user's own model
4. Multi-package system (3 repos) → each gets independent DDD + cross-system awareness
5. Uninstall removes all added files + hook entries cleanly, zero residue
6. Published spec gets community adoption (measurable: GitHub stars, forks, Discussions engagement)
7. AI-Ready Score improves ≥4 points from baseline (no artifacts) to post-install

---

## Appendix A: AGENTS.md Template

```markdown
# {project-name}

> AI-Ready (DDD) | Generated {date} | Score: {score}/10 | [Review Report](.ai-ready/REVIEW-REPORT.md)

## Quick Start
{build command}     # Build
{test command}      # Test (~{duration})
{lint command}      # Lint

## Architecture ({N} modules)
- `{path}/` — {one-line responsibility}

## Entry Points
- `{file}` → {type} ({description})

## Critical Rules
- ❌ {never do X — because Y}
- ✅ {always do A — because B}

## Top Gotchas
1. {evidence-grounded: what + commit/issue reference}

## Deep Context (DDD)
| Need to understand... | Read |
|---|---|
| Why this exists, what's out of scope | [PRODUCT.md](.ai-ready/PRODUCT.md) |
| Architecture, conventions, invariants | [TECH.md](.ai-ready/TECH.md) |
| What failed, known issues, patterns | [IMPROVEMENT.md](.ai-ready/IMPROVEMENT.md) |
| Current priorities, active decisions | [PROJECT.md](.ai-ready/PROJECT.md) |
| Module dependencies, blast radius | [code-intel.json](.ai-ready/code-intel.json) |

<!-- user: Your additions below — refresh preserves this section -->
```

## Appendix B: DDD File Templates

### PRODUCT.md
```markdown
# {project-name} — Product Context

## Purpose
{what problem, for whom}

## Audience
{primary users/consumers}

## Non-Goals
- {out of scope — and why}

## Success Criteria
- {measurable outcomes}

## Constraints
- {regulatory, compliance, SLA, business rules}

<!-- user: Your additions below — refresh preserves this section -->
```

### TECH.md
```markdown
# {project-name} — Technical Context

## Stack
{language, framework, database, infra}

## Architecture
{module map — matches code-intel.json}

## Conventions
{prescriptive: "ALWAYS do X" / "NEVER do Y"}

## Key Decisions
{architectural choices and WHY}

## Invariants
{things that must always be true}

<!-- user: Your additions below — refresh preserves this section -->
```

### IMPROVEMENT.md
```markdown
# {project-name} — Lessons & Knowledge

## What Failed
{WHEN [trigger] → RISK [what breaks] → BECAUSE [evidence]}

## What Works
{patterns that proved reliable}

## Known Issues
{current tech debt, quirks, workarounds}

## Gotchas
{evidence-grounded, cite commit/issue}

<!-- user: Your additions below — refresh preserves this section -->
```

### PROJECT.md
```markdown
# {project-name} — Current Context

## Current Priorities
{what the team is focused on now}

## Recent Decisions
{last 30 days, affects how agent should work}

## Blocked By
{known blockers}

## Open Questions
{decisions pending — agent should NOT make unilaterally}

<!-- user: Your additions below — refresh preserves this section -->
```

## Appendix C: REVIEW-REPORT.md Template

```markdown
# AI-Ready Review Report

Generated: {date} | Engine: SwarmAI AI-Ready-Repo Engine v{version}

## Overall Score: {score}/10

| Dimension | Score | Confidence | Gaps |
|-----------|-------|-----------|------|
| ... | .../10 | High/Medium/Low | {description or —} |

## Review Assignments

### PRODUCT.md — Reviewer: PM / Business Owner
- **Confidence**: {High|Medium|Low}
- **Source**: {README + user input / inferred / placeholder}
- **Please verify**: {specific items}
- **Known gaps**: {what's missing}

### TECH.md — Reviewer: Senior Engineer
(same format)

### IMPROVEMENT.md — Reviewer: Team
(same format)

### PROJECT.md — Reviewer: Lead / PM
(same format)

## Improvement Recommendations (prioritized)
1. 🔴 {highest impact — expected score +N}
2. 🟡 {medium impact}
3. 🔵 {nice to have}
```
