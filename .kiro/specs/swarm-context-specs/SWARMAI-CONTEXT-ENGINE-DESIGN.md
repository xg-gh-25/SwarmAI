# SwarmAI Context Engine
## Local-First, Layered Persistent Memory — SwarmWS-Aligned, Project-Scoped Working Memory

---

# 1. Purpose

This document defines the revised architecture for the **SwarmAI Context Engine** — a local-first subsystem that assembles **bounded working memory** for each agent turn, fully aligned with the SwarmWS model:

- **Single persistent workspace:** SwarmWS (global memory root)
- **Project-scoped execution containers:** under `Projects/`
- **Operating Loop scaffolding:** Signals → Plan → Execute → Communicate → Reflection
- **Hierarchical semantic context layering:** L0/L1 files at global, artifact, and project levels
- **Deterministic, incremental context assembly** via cached Working Memory Frames (W-Frames)

The engine ensures:
- High performance without full regeneration
- Predictable agent reasoning boundaries
- Strong alignment with the SwarmWS Explorer UX and mental model

---

# 2. Core Architectural Principle

## 2.1 Canonical Sources of Truth

| Domain | Canonical Store | Notes |
|---|---|---|
| Structured entities | **SQLite DB** | Signals (ToDos), PlanItems, Tasks, Communications, ChatThreads, ThreadSummary, Audit logs, Artifact metadata |
| Content files | **Filesystem (SwarmWS)** | Artifacts, Reflections, Project files, L0/L1 context files |
| Performance artifacts | **Local derived cache/index** | W-Frames, retrieval cache, optional FTS search index |

> Rule: DB is canonical for structured workflow entities.  
> Filesystem is canonical for knowledge content and semantic context.

---

# 3. Context Scope Model (SwarmWS-Aligned)

SwarmAI now uses a **single workspace with layered scopes**, not multiple independent workspaces.

## 3.1 Scope Hierarchy

Context is always assembled from four deterministic layers:

1. **Profile Layer (Global User Memory)**
2. **SwarmWS Global Layer (Root Workspace Memory)**
3. **Project Layer (Scoped Execution Context)**
4. **Thread Layer (Live Interaction State)**

Top layers override lower layers during reasoning.

---

# 4. Layered Context Composition (Deterministic Order)

The Context Engine assembles memory in this strict order:

1. Base system prompt (SwarmAgent core)
2. Live work context (current chat thread, tasks, files)
3. Project instructions (`instructions.md`)
4. Project semantic context (`context-L0.md`, `context-L1.md`)
5. Shared artifacts semantic context (`Artifacts/context-L0.md`, `context-L1.md`)
6. Global workspace semantic context (`SwarmWS/context-L0.md`, `context-L1.md`)
7. Optional scoped retrieval within SwarmWS

This mirrors the SwarmWS mental model:
> System Core → Live Work → Project Intent → Project Knowledge → Shared Knowledge → Global Memory

---

# 5. Section-Aware Context Assembly (Operating Loop Alignment)

The Context Engine prioritizes different state snapshots depending on the active Operating Loop section.

| Section | Context Engine MUST Prioritize |
|---|---|
| **Signals** | Pending/overdue ToDos, selected signal details |
| **Plan** | Current plan items, dependencies, upcoming milestones |
| **Execute** | Task status, blockers, latest outputs, linked thread summary |
| **Communicate** | Draft messages, pending replies, stakeholder context |
| **Reflection** | Retrospectives, lessons learned, historical summaries |
| **Artifacts** | Relevant artifact metadata and file paths (read-only context) |

These sections are **system scaffolding**, not independent memory domains.

---

# 6. Filesystem Layout (SwarmWS Canonical Structure)

```

SwarmWS/
├── context-L0.md
├── context-L1.md
├── Artifacts/
│   ├── context-L0.md
│   ├── context-L1.md
├── Projects/
│   └── Project-X/
│       ├── context-L0.md
│       ├── context-L1.md
│       ├── instructions.md
│       ├── research/
│       ├── reports/
│       └── user-files/

```

### Depth Guardrails (Enforced)
- Operating Loop sections: max depth **2**
- Projects: max depth **3**
- Artifacts / Notebooks: max depth **3**

This ensures stable retrieval paths and prevents structural entropy.

---

# 7. Working Memory Frame (W-Frame)

## 7.1 Definition
A **W-Frame** is the bounded context bundle provided to SwarmAgent for a single turn.

### Recommended Contents
- `profile_excerpt`
- `scope` (project_id or global)
- `project_instructions`
- `semantic_context_layers` (L0/L1 across layers)
- `section_snapshot`
- `evidence_snippets[]` with provenance
- `thread_tail` + `latest_user_turn`
- `watermarks` (DB + FS versions)

---

# 8. Incremental Assembly (No Full Regeneration)

## 8.1 Change Detection
On each turn, the engine computes a delta plan using:

### DB Changes
- Updated Signals / PlanItems / Tasks / Communications
- Updated ThreadSummary

### Filesystem Changes
- L0/L1 context file modifications
- New artifacts or reports added

### Scope Changes
- Switching project
- Switching Operating Loop section
- Focus mode activation

---

## 8.2 Fast Path
If no meaningful change:
- Reuse cached W-Frame
- Append latest user message
- Update rolling ThreadSummary incrementally

## 8.3 Delta Path
If change detected:
- Recompute only impacted slots:
  - semantic context layers
  - section snapshot
  - retrieval cache (if signature changes)

---

# 9. Hierarchical Semantic Context Injection

## 9.1 Injection Priority
1. Project `context-L0.md` and `context-L1.md`
2. Artifacts `context-L0.md` and `context-L1.md`
3. SwarmWS global `context-L0.md` and `context-L1.md`

This enforces:
> Project intent dominates, shared knowledge informs, global memory guides.

## 9.2 Budgets
- Default injection budget: **10K tokens**
- Prefer L0 summaries first
- Expand to L1 only when needed

---

# 10. Retrieval Model (SwarmWS-Scoped)

Retrieval is always scoped within:
- Current project (primary)
- Shared artifacts (secondary)
- Global workspace memory (fallback)

### Retrieval Signature
```

signature = hash(
project_id,
section,
intent_fingerprint,
semantic_context_hash,
freshness_policy
)

```

---

# 11. Deterministic Token Budgeting

Priority order:
1. Governance + scope mode
2. Project instructions
3. Semantic context layers (L0/L1)
4. Section snapshot
5. Evidence snippets
6. Thread tail

Trimming rules:
- Drop evidence first
- Shrink thread window
- Prefer summaries over raw files
- Never remove governance or project instructions

---

# 12. Governance & Tool Surface

The Context Engine exposes only allowed tools:
- Enabled Skills
- Enabled MCP servers
- Effective knowledge sources summary

SwarmAgent must not access tools outside this surface.

---

# 13. Local Performance Layer (Derived)

```

SwarmWS/
└── .swarm/
├── cache/
│   ├── context/
│   │   ├── wframe.json
│   │   └── wframe.meta.json
│   └── retrieval/
└── index/

```

`.swarm/` is derived and safe to rebuild.

---

# 14. Heartbeat (Optional, Event-Driven)

### Deterministic Scheduler
- Overdue tasks
- Daily recap triggers
- Status badge refresh

### Optional LLM Heartbeat
- Runs only on meaningful changes
- Uses minimal W-Frame
- Produces low-noise suggestions

---

# 15. Correctness & Consistency

- DB updates must be transactional
- W-Frame stores DB watermark to detect staleness
- Filesystem writes must be atomic (temp → rename)
- Cache invalidation rules must cover:
  - context file updates
  - project switches
  - section switches
  - artifact additions

---

# 16. Final Summary

The SwarmAI Context Engine is the deterministic **layered working memory builder** that:

- Aligns with the **single SwarmWS workspace model**
- Uses **project-scoped execution context** as primary focus
- Injects **hierarchical semantic context (L0/L1)** across project → artifacts → global layers
- Assembles bounded **W-Frames** incrementally using watermarks and cache invalidation
- Respects DB/FS canonical separation
- Enforces predictable reasoning boundaries and high performance
- Mirrors the SwarmWS Explorer mental model and UX guardrails

This enables SwarmAI to function as a:

> Local-first, layered persistent memory operating system for structured knowledge work.

