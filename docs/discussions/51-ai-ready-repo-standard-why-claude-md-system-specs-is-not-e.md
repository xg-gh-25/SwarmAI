---
title: "AI-Ready Repo Standard — Why CLAUDE.md + System Specs Is Not Enough"
created: 2026-05-27
updated: 2026-05-27
status: published
---
<!-- GitHub Discussion #51: https://github.com/xg-gh-25/SwarmAI/discussions/51 -->

### AI-Ready Repo Standard — Why CLAUDE.md + System Specs Is Not Enough

---

## Sound Familiar?

These are real questions from engineering teams adopting AI coding tools right now. If any of them hit home — this post is for you.

### "How do I make my repo AI-ready?"

> *"Our knowledge base is project directories + CLAUDE.md files. How should we design it so AI can actually read and use it?"*

You've done more than most teams: you have documentation, you've written CLAUDE.md, maybe even added steering files. But the agent still doesn't *get it*. It follows the letter of your rules but misses the spirit of your architecture.

### "PRDs are scattered — how do I make them consumable?"

> *"Requirements live in Notion, Google Docs, Confluence, Slack threads, and people's heads. How do we present them coherently so AI can consume them?"*

The problem isn't that the PRD doesn't exist — it's that it exists in 5 places, none of which are in the agent's context window. You can't paste a 30-page PRD into CLAUDE.md. You need a distilled judgment substrate, not a document dump.

### "Knowledge is there but output still drifts"

> *"We put system specs and development guidelines in steering files. AI output still has deviations. We need to collect specific bad cases to analyze why."*

This is the most frustrating stage: you did the work, you wrote the docs, you configured the tool — and it still produces code that doesn't match your expectations. The issue is almost never "agent is stupid." It's usually: the right context isn't loaded at the right time, or lessons from past failures aren't captured.

### "Compliance constraints in B2B/ISV scenarios"

> *"We're an ISV updating code in our customer's environment (e.g., AIA platform). We must follow their approval workflows, permission models, and release standards. How do we use AI coding without violating multi-party governance?"*

Enterprise teams face a unique constraint: the agent needs to know not just "how to code" but "what I'm NOT allowed to do." Compliance rules, deployment gates, approval workflows — these are hard constraints that a flat rules file can't express with the necessary nuance.

### "AI doesn't understand our domain"

> *"We're in energy storage / renewable energy. Domain knowledge hasn't been injected. The agent writes syntactically correct code with zero domain understanding."*

Generic coding ability is table stakes. An agent that doesn't know "battery SOC should never drop below 20%" or "inverter commands require safety interlock" produces code that passes lint and fails in production. Domain knowledge needs a home.

### "Generated code quality is inconsistent"

> *"We lack unified standards and review criteria for AI-generated code. Different team members get wildly different output quality."*

Without a shared baseline of "what good looks like" in THIS codebase, quality depends entirely on who writes the prompt. The repo itself should encode quality standards — not individual engineers' prompt skills.

### "Nobody knows how to write specs for AI"

> *"The team doesn't know what an 'AI-consumable spec' looks like. Traditional PRD format doesn't work for agent context."*

Fair. An AI-consumable spec is NOT a traditional PRD. It's shorter, more structured, judgment-oriented (non-goals are more important than features), and uses formats the agent can parse into action.

### "Cost control — no ROI measurement"

> *"Multiple tools running in parallel (Claude Code + Cursor + Copilot). No way to measure ROI per tool. Hard to justify spend."*

If you can't measure "how much did AI save us this sprint," you can't defend the budget. And if the repo isn't AI-ready, the tools underperform → ROI looks worse than it is → tools get cancelled → everyone loses.

---

## The Common Root Cause

Every one of these problems traces back to the same gap:

**Your repo has context files, but no context architecture.**

A single markdown file telling the agent "use snake_case" or "we use PostgreSQL" is configuration, not knowledge. It's the difference between giving a new hire a style guide vs. giving them six months of institutional knowledge.

This post proposes a convention standard for making any repository AI-ready — independent of which tool you use.

---

## The Gap: Static Config vs Living Knowledge

Here's what every tool offers today:

| Tool | Context Mechanism | What It Contains | Limitation |
|------|------------------|-----------------|------------|
| **Claude Code** | `CLAUDE.md` | Style rules, project description, commands | Flat. No structure. No separation of concerns. No learning. |
| **Kiro** | `.kiro/steering/` + `.kiro/specs/` | Steering rules + feature specs | Per-feature scope. Specs are one-shot (write → execute → done). No cross-project memory. |
| **Cursor** | `.cursorrules` + `@docs` | Rules + indexed docs | Rules are static. Docs are reference, not judgment. |
| **Windsurf** | `.windsurfrules` | Style rules | Same as .cursorrules — config, not knowledge. |

**What they all miss:**

1. **Separation of concerns** — "What are we building?" vs "How do we build it?" vs "What have we learned?" are three fundamentally different knowledge types that need different update cadences
2. **Accumulated lessons** — mistakes, bad patterns, corrections. The MOST valuable context is "we tried X and it failed because Y" — no tool captures this today
3. **Structural code awareness** — who calls what, what breaks if I change this. File-level rules can't express this.
4. **Living knowledge** — docs that grow from work, not docs that rot from neglect

CLAUDE.md is a single flat file. It's like running a company with one shared Google Doc instead of an org chart, knowledge base, and post-mortem library.

---

## The Standard: `.ai-context/`

We propose a convention — a directory structure that any AI coding tool can consume:

```
your-repo/
├── .ai-context/
│   ├── PRODUCT.md          # What are we building? Why? For whom?
│   ├── TECH.md             # How is it built? Stack, conventions, APIs
│   ├── IMPROVEMENT.md      # What worked, what failed, what to avoid
│   ├── PROJECT.md          # Current focus, recent decisions, open items
│   └── code-intel.db       # (Optional) Pre-computed dependency graph
│
├── .claude/                 # Tool-specific (Claude Code hooks, skills)
├── .kiro/                   # Tool-specific (Kiro steering, specs)
├── .cursorrules             # Tool-specific (Cursor rules)
└── src/                     # Your code
```

### The 4 Documents — Separation of Concerns

| Document | Answers | Update Cadence | Who Updates |
|----------|---------|---------------|-------------|
| **PRODUCT.md** | *What* are we building? Why? For whom? Non-goals? | Weekly (strategic shifts) | Human |
| **TECH.md** | *How* is it built? Stack, conventions, key subsystems, API contracts | On architecture change | Human + Agent |
| **IMPROVEMENT.md** | What *worked*? What *failed*? What to *avoid*? | Every significant task | Agent (human reviews) |
| **PROJECT.md** | What's happening *now*? Current focus, recent decisions, blockers | Every session | Agent |

**Why 4 files, not 1?**

Because they have completely different:
- **Authors** — PRODUCT.md is human-authored strategy. IMPROVEMENT.md is agent-accumulated experience.
- **Cadences** — PROJECT.md changes daily. TECH.md changes monthly. Mixing them means either stale strategy or noisy architecture docs.
- **Consumers** — The agent reads TECH.md when coding. It reads IMPROVEMENT.md when reviewing. It reads PRODUCT.md when evaluating "should we build this?" Different phases need different context.

---

## Why This Beats a Single File

### Scenario 1: "Agent ignores our architecture"

**With CLAUDE.md (flat):**
```markdown
# Project
We use FastAPI, React, SQLite...
[200 lines of everything mixed together]
```
Agent reads 200 lines, attention dilutes. The critical "never use lsof on macOS" is buried between "we use Tailwind" and "deploy via launchd."

**With `.ai-context/` (structured):**
- Agent coding? → loads TECH.md (conventions, APIs, traps)
- Agent reviewing? → loads IMPROVEMENT.md (past failures, anti-patterns)
- Agent evaluating a feature request? → loads PRODUCT.md (non-goals, priorities)

**Right context at the right time.**

### Scenario 2: "We keep making the same mistakes"

**With CLAUDE.md:**
You'd have to manually add "don't do X" rules. Nobody remembers to update CLAUDE.md after every bug. The doc rots.

**With IMPROVEMENT.md:**
After every significant task, the agent appends:
```markdown
## What Failed
- 2026-05-20: subprocess.run() in async context blocks event loop → use asyncio.to_thread() + timeout
- 2026-05-15: Changed shared function signature → broke 3 downstream callers (use blast_radius check first)
```

**The repo gets smarter with every task.**

### Scenario 3: "PRDs are scattered everywhere"

**With CLAUDE.md:**
You paste the whole PRD into one file. 3000 tokens of product context mixed with 500 tokens of coding rules. Neither is effective.

**With PRODUCT.md:**
Distilled product context — vision, priorities, non-goals, audience map. Not the full PRD, but the **judgment substrate**: enough for the agent to answer "should we build this?" and "does this align with our direction?" Link to the full PRD as a reference.

---

## Structural Awareness: Scaled to Your Repo Size

The 4 docs give the agent *judgment* (what to build, how, what to avoid). But for code changes, it also needs *structural awareness* — who depends on whom. The solution scales with your codebase:

### Small repos (<50K LOC): `CODEBASE.md` — Hand-Maintained Map

For small projects, the agent can grep and read most files in context. A computed graph is overkill. But a **human-written module map** still helps — it tells the agent "here's how the pieces connect" without it having to discover the architecture by reading 100 files:

```
.ai-context/
└── CODEBASE.md       # ~50-100 lines, human-maintained
```

Example `CODEBASE.md`:
```markdown
# Codebase Map

## Module Overview
- `src/auth/` — Authentication & authorization (JWT, OAuth2)
- `src/billing/` — Payment processing, subscription management
- `src/api/` — REST endpoints (depends on auth + billing)
- `src/workers/` — Background jobs (depends on billing)

## Key Dependencies
- api → auth (every endpoint validates token)
- api → billing (checkout, subscription endpoints)
- workers → billing (invoice generation, payment retry)
- billing → auth (permission checks on payment actions)

## Shared Interfaces (change carefully)
- `src/auth/validator.py::validate_token()` — called by api + workers + billing
- `src/billing/models.py::Subscription` — used across 4 modules
- `src/common/errors.py` — all modules import error types

## Entry Points
- `src/api/main.py` — FastAPI app
- `src/workers/scheduler.py` — Celery beat
- `scripts/migrate.py` — DB migrations
```

**Why this works for small repos:** Agent reads CODEBASE.md once at session start → knows the architecture → makes informed decisions about blast radius. Cost: 5 minutes to write, ~200 tokens to inject. Update when you add a new module (maybe once a month).

**When to upgrade:** If you notice the agent regularly breaking cross-module interfaces, or if CODEBASE.md grows past 200 lines → time for code-intel.db.

---

### Medium repos (50K-200K LOC): Both

Use CODEBASE.md for the high-level architecture (humans still write better summaries than parsers) PLUS code-intel.db for precise caller/callee queries. The agent reads the map for orientation, queries the graph for specific decisions.

---

### Large repos (>200K LOC): `code-intel.db` — Pre-Computed Graph

At this scale, no human can maintain an accurate dependency map. You need automated structural analysis:

```
.ai-context/
├── CODEBASE.md       # High-level module overview (still useful)
└── code-intel.db     # SQLite, ~30-50MB for 200K LOC
```

Schema:
```sql
-- What's defined where
CREATE TABLE code_nodes (
    id TEXT PRIMARY KEY,           -- "src/auth/validator.py::validate_token"
    file_path TEXT NOT NULL,
    node_type TEXT NOT NULL,       -- function | class | method
    name TEXT NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    language TEXT
);

-- Who calls whom
CREATE TABLE code_edges (
    source_id TEXT NOT NULL,       -- caller
    target_id TEXT NOT NULL,       -- callee
    edge_type TEXT DEFAULT 'calls',
    confidence REAL DEFAULT 1.0
);

-- Freshness tracking
CREATE TABLE graph_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL             -- last_indexed_commit, repo_root, etc.
);
```

A PreToolUse hook queries this on every `Read`:
```
Agent reads: src/auth/validator.py
Injected: "⚡ validate_token() has 12 callers across 4 packages. Blast radius: HIGH."
```

Without this, agent changes `validate_token()` signature → 4 packages break. With this, agent KNOWS before it starts.

(Full implementation details: [Discussion #49](https://github.com/xg-gh-25/SwarmAI/discussions/49))

---

### Summary: Pick Your Level

| Repo Size | Structural Awareness | Effort | When to Upgrade |
|-----------|---------------------|--------|-----------------|
| **<50K LOC** | `CODEBASE.md` (hand-written) | 5 min | Agent breaks cross-module code |
| **50K-200K** | `CODEBASE.md` + `code-intel.db` | 30 min setup | Never — this is the sweet spot |
| **>200K / monorepo** | `code-intel.db` + cross-package refs | 1 hour setup + CI integration | N/A — you're at the ceiling |

---

## How It Maps to Your Tool

### Claude Code

```
.ai-context/           → Referenced in CLAUDE.md: "Read .ai-context/ docs for project context"
.claude/hooks/         → PreToolUse hook loads relevant .ai-context/ doc based on task type
                       → PostSession hook appends to IMPROVEMENT.md
```

Claude Code's hooks are the enforcement mechanism. The convention provides the knowledge. Together: living context that grows without manual maintenance.

### Kiro

```
.ai-context/PRODUCT.md   → Informs requirements generation (product context)
.ai-context/TECH.md      → Informs design spec (architecture constraints)
.ai-context/IMPROVEMENT.md → Steering file: "avoid these patterns"
.kiro/steering/ai-context.md → "Always read .ai-context/TECH.md before generating design specs"
```

Kiro's spec-driven workflow benefits most from PRODUCT.md (shapes requirements) and TECH.md (constrains design). IMPROVEMENT.md becomes a steering file that prevents repeating past mistakes.

---

## The Living Loop (Why This Is Different From More Static Docs)

Static docs rot. The key innovation isn't the directory structure — it's the **accumulation loop**:

```
        ┌─────────────────────────────────────────┐
        │                                         │
        ▼                                         │
   Agent works on task                            │
        │                                         │
        ├─ Reads TECH.md (how to build)           │
        ├─ Reads IMPROVEMENT.md (what to avoid)   │
        │                                         │
        ▼                                         │
   Task completes (or fails)                      │
        │                                         │
        ├─ Lesson learned?                        │
        │   └─ Append to IMPROVEMENT.md ──────────┘
        │
        ├─ Architecture changed?
        │   └─ Update TECH.md
        │
        └─ Decision made?
            └─ Update PROJECT.md
```

**Each task makes the repo smarter.** Not because someone remembers to update docs — because the agent does it structurally.

Without the loop, you have documentation. With it, you have **institutional memory**.

---

## Adoption: 5-Minute Quick Start

```bash
# 1. Create the directory
mkdir -p .ai-context

# 2. Scaffold the docs
cat > .ai-context/PRODUCT.md << 'EOF'
# Product Context

## What We're Building
[One paragraph: what is this project?]

## Why
[The problem we're solving]

## For Whom
[Target users]

## Non-Goals
[What we explicitly won't do — critical for agent judgment]
EOF

cat > .ai-context/TECH.md << 'EOF'
# Technical Context

## Stack
[Languages, frameworks, key libraries]

## Architecture
[Key subsystems and how they connect]

## Conventions
[Naming, file structure, patterns we follow]

## Traps
[Things that look right but are wrong in this codebase]
EOF

cat > .ai-context/IMPROVEMENT.md << 'EOF'
# Improvement Log

## What Worked
[Patterns that produced good results]

## What Failed
[Patterns that caused bugs — the MOST valuable section]

## What to Watch For
[Recurring risk areas]
EOF

cat > .ai-context/PROJECT.md << 'EOF'
# Current Project Context

## Current Focus
[What are we working on right now?]

## Recent Decisions
[Last 3-5 significant decisions and their reasoning]

## Open Items
[Unresolved questions, blockers]
EOF

# 3. Reference from your tool
echo "Read .ai-context/ docs for project knowledge." >> CLAUDE.md
```

**Time to value: 5 minutes.** Fill in the bracketed sections. Your agent immediately has structured context instead of nothing (or a 500-line flat file).

---

## Design Decisions

### Why filesystem, not a service?

Git-tracked files mean:
- Version history for free
- Works offline
- Works with every tool (any tool can Read a file)
- Team sees changes in PRs
- No setup, no auth, no API keys

### Why markdown, not YAML/JSON?

Agent-consumable AND human-readable. Engineers actually edit markdown. Nobody voluntarily edits JSON config for knowledge management.

### Why 4 files, not 7 or 12?

Minimum viable separation of concerns. 4 is enough to eliminate cross-contamination (strategy vs tactics vs lessons vs status) without creating a filing system nobody maintains.

### Why `.ai-context/` not `.claude/` or `.kiro/`?

Tool-agnostic. Your context architecture shouldn't be locked to one vendor. Claude Code reads it. Kiro reads it. Cursor reads it. Switch tools, keep your knowledge.

---

## Comparison With Existing Approaches

| Approach | Knowledge Structure | Learning | Code Awareness | Tool Lock-in |
|----------|-------------------|----------|---------------|:---:|
| **CLAUDE.md** | 1 flat file | ❌ Manual | ❌ | Claude |
| **Kiro SDD** | 3 per-feature specs | ❌ One-shot | ❌ | Kiro |
| **.cursorrules** | 1 rules file | ❌ Manual | ❌ | Cursor |
| **Aider conventions** | `.aider.conf.yml` | ❌ Manual | Repo map (basic) | Aider |
| **`.ai-context/` (this)** | 4 concern-separated docs | ✅ Agent accumulates | ✅ code-intel.db | **None** |

---

## What This Enables (That Nothing Else Does Today)

1. **Cross-session learning** — IMPROVEMENT.md persists failures across agent sessions. No agent today remembers what went wrong yesterday unless you tell it.

2. **Right-context-right-time** — Different files for different phases (coding vs reviewing vs planning). No attention dilution from loading irrelevant context.

3. **Blast radius awareness** — code-intel.db gives structural code understanding. No other convention includes this.

4. **Team knowledge, not personal config** — Git-tracked means the whole team benefits. New team member's agent immediately has all accumulated knowledge. Onboarding cost → zero.

5. **Tool migration with zero cost** — Switch from Cursor to Claude Code to Kiro? `.ai-context/` comes with you. Only the tool-specific hooks change.

---

## Call to Action

If this resonates, try it:
1. Create `.ai-context/` in your repo
2. Fill in the 4 docs (10 minutes)
3. Reference them from your tool's config (CLAUDE.md, .kiro/steering/, etc.)
4. After your next significant task, add one entry to IMPROVEMENT.md

Report back: did the agent's output quality improve? What's still missing?

Related discussions:
- [DDD Cultivation — Domain Knowledge That Grows From Work](https://github.com/xg-gh-25/SwarmAI/discussions/9) (the theory behind living knowledge)
- [Code Intelligence for Large Codebases](https://github.com/xg-gh-25/SwarmAI/discussions/49) (the code-intel.db implementation)
- [Design Philosophy — Six Pillars](https://github.com/xg-gh-25/SwarmAI/discussions/38) (why "prevention over recovery" applies to context)

---

*Published from [SwarmAI](https://github.com/xg-gh-25/SwarmAI) — where this standard has been running in production for 3 months across 7 projects, 170K LOC, with 14 lessons auto-accumulated per week.*
