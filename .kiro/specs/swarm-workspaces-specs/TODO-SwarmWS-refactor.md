# SwarmWS Final Structure Specification
*Single Workspace + Projects Model for SwarmAI (Final Consolidated Design)*

**Root Path:** `~/SwarmAI/Swarm-Workspace/SwarmWS`  
SwarmWS is the **single default workspace** (non-deletable) and serves as the persistent memory container for SwarmAI.

---

# 1. Design Principles

1. **Single Workspace Model**  
   SwarmWS is the only root workspace and represents the user’s long-term working memory.

2. **Projects as Primary Organization Unit**  
   All active and ongoing work is organized under the `Projects/` directory. Projects are the primary execution containers for users.

3. **Operating Loop Sections**  
   The workspace follows a continuous work loop:  
   **Signals → Plan → Execute → Communicate → Reflection**  
   These sections provide system scaffolding and should not require heavy manual management by users.

4. **Artifacts as Shared Memory**  
   Global reusable assets are stored centrally and accessible across all projects.

5. **Hierarchical Context Layering**  
   Two-layer semantic context model:  
   - `context-L0.md` — concise abstract (~100 tokens)  
   - `context-L1.md` — structured overview (~2k tokens)  
   This enables fast routing, relevance detection, and efficient agent reasoning.

6. **Clear Ownership Boundaries**  
   The system enforces a stable structure for agent reliability while leaving maximum flexibility for user content.

   **System-managed (non-removable, structure-enforcing):**
   - Root files: `system-prompts.md`, `context-L0.md`, `context-L1.md`
   - Operating loop sections: `Signals/`, `Plan/`, `Execute/`, `Communicate/`, `Reflection/`
   - Shared scaffolding: default structure of `Artifacts/` and `Notebooks/`
   - Project required items:
     - `context-L0.md`, `context-L1.md`
     - `instructions.md`
     - `chats/`
     - `research/`
     - `reports/`
     - `.project.json`
   - System-provided onboarding content and sample data within default sections

   **User-managed (flexible, content-extensible):**
   - Custom project subfolders (e.g., `docs/`, `data/`, `images/`)
   - User-uploaded or user-created files within projects
   - Custom nested folders inside `Artifacts/`, `Notebooks/`, and project directories
   - Edits to system files’ content (without deleting or structurally renaming them)

   **Enforcement Rules:**
   - System-managed items cannot be deleted or structurally renamed
   - Users may freely add, modify, or reorganize content within user-managed areas
   - The system guarantees structural stability while preserving flexibility

7. **Non-Destructive Defaults**  
   - Core system files and folders cannot be deleted  
   - Distinct system icons differentiate system defaults from user content

8. **Single Workspace Enforcement**  
   - SwarmAI operates with only one workspace: **SwarmWS**  
   - All UI elements and backend logic related to creating or switching workspaces must be removed  
   - Clean up all related unused or dead code during refactor

9. **Built-in Real & Sample Data for Onboarding**  
   - On first launch, users see a fully populated SwarmWS with realistic sample data  
   - Include example projects, subfolders, and representative files under each core section  
   - Every default section and folder should contain meaningful starter content demonstrating intended usage

10. **Codebase Hygiene**  
   - Remove all dead and duplicate code (including obsolete tests)  
   - Update all relevant specification documents under `/specs`

---

# 2. Top-Level Workspace Structure

## 2.1 Workspace Explorer UX Requirements (Structural Controls)

1. Replace header title **“Explorer”** with **“SwarmWS”**
2. Remove workspace dropdown selector
3. Remove “Show Archived Workspaces” checkbox
4. Remove **Global | SwarmWS** toggle switch
5. Move search box to the center of the top bar (similar to Kiro IDE)
6. Remove “New Workspace” button
7. Remove the add-context area under workspace
8. Refine the workspace tree to match the structure below

## 2.2 Workspace Tree (UX-Aligned Structure & Depth Constraints)

The SwarmWS explorer presents a unified, transparent view of the entire agent workspace while remaining simple and cognitively manageable.

The tree is conceptually grouped into three zones:

1. **Operating Loop (System Scaffolding)** — background workflow pipeline  
2. **Shared Knowledge** — reusable global memory  
3. **Active Work (Primary Focus)** — user execution containers  

### Tree Structure

```text
SwarmWS/
├── system-prompts.md
├── context-L0.md
├── context-L1.md

├── ── Operating Loop (System) ──
├── Signals/
├── Plan/
├── Execute/
├── Communicate/
├── Reflection/

├── ── Shared Knowledge ──
├── Artifacts/
└── Notebooks/

├── ── Active Work ──
└── Projects/
````

### Depth Guardrails (UX + Agent Consistency)

To maintain usability, navigability, and efficient agent retrieval, folder depth is constrained as follows:

#### Operating Loop Sections (Strict Limit)

Applies to:

* `Signals/`
* `Plan/`
* `Execute/`
* `Communicate/`
* `Reflection/`
* `research/`, `reports/`, `chats/` within projects

Rules:

* **Maximum depth: 2 levels**
* Deeper nesting is blocked
* Tooltip: “System-managed sections use a shallow structure to ensure clarity and predictable agent reasoning.”

#### Shared Knowledge (Artifacts & Notebooks)

Applies to:

* `Artifacts/`
* `Notebooks/`

Rules:

* **Maximum depth: 3 levels**
* Encourages reusable knowledge organization without becoming a file jungle
* Promotes semantic naming over deep archival nesting

#### Projects (Primary Work Containers)

Applies to:

* All project directories under `Projects/`

Rules:

* **Maximum depth: 3 levels (hard limit)**
* Prevents uncontrolled nesting that harms discoverability and reasoning clarity
* Encourages flatter, semantically meaningful structures

Example (Recommended):

```text
Project-A/
├── docs/
│   └── prd.md
├── data/
│   └── raw.csv
└── analysis/
    └── competitors.md
```

Example (Disallowed – Too Deep):

```text
Project-A/
└── analysis/
    └── 2026/
        └── Q1/
            └── competitors/
                └── final/
```

Instead, prefer:

```text
Project-A/
└── analysis-2026-Q1-competitors.md
```

---

# 3. Global Context Files

## 3.1 `context-L0.md`

* Ultra-concise semantic abstract (~100 tokens)
* Used for fast relevance detection and routing decisions

## 3.2 `context-L1.md`

* Structured workspace overview (~2k tokens)
* Describes scope, structure, goals, key knowledge, and relationships

Both files are **system-managed and non-deletable**.

---

# 4. Core System Sections (Operating Loop)

These folders implement the **Daily Work Operating Loop** and are always present:

| Section        | Purpose                                                     |
| -------------- | ----------------------------------------------------------- |
| `Signals/`     | Incoming triggers, observations, and raw inputs             |
| `Plan/`        | Strategic planning, roadmaps, and task decomposition        |
| `Execute/`     | Active tasks, WIP outputs, and runtime execution state      |
| `Communicate/` | Reports, stakeholder updates, and outbound messaging        |
| `Reflection/`  | Retrospectives, lessons learned, and continuous improvement |

These sections are system scaffolding that support agent workflows and must not be removed.

---

# 5. Artifacts Folder (Shared Workspace Assets)

```text
Artifacts/
├── context-L0.md        # System default (non-deletable)
├── context-L1.md        # System default (non-deletable)
├── <user-uploaded-files>
└── <user-created-subfolders>/
```

## Purpose

* Central repository for reusable assets across projects
* Supports flexible but depth-limited nested subfolder structures (max 3 levels)
* Provides global semantic grounding via L0/L1 context files

---

# 6. Projects Folder (Primary Work Container)

The `Projects/` directory contains all user-defined projects.
Each project acts as a **self-contained execution and knowledge container**.

```text
Projects/
├── context-L0.md        # System default (non-deletable)
├── context-L1.md        # System default (non-deletable)
├── Project-A/
├── Project-B/
└── Project-C/
```

## Project-Level Context Files

Each project includes:

* `context-L0.md` — concise project abstract (~100 tokens)
* `context-L1.md` — detailed project overview (~2k tokens)

These files enable rapid agent understanding of project scope and intent.

---

# 7. Standard Project Folder Template (Final)

```text
Project X/
├── context-L0.md        # System default (non-deletable) — Project abstract
├── context-L1.md        # System default (non-deletable) — Project overview
├── instructions.md      # System default (editable, non-deletable)

├── chats/               # System default (non-deletable) — Chat threads
│   └── thread_xx/

├── research/            # System default (non-deletable)
│   ├── request.json
│   ├── plan.md
│   ├── sources.json
│   ├── notes/
│   ├── assets/
│   └── report.md

├── reports/             # System default (non-deletable)
│   ├── weekly-report.*
│   └── monthly-report.*

├── user-created-sub-folder/    # User-managed content (depth-limited ≤ 3)
│   ├── docs/
│   ├── data/
│   ├── images/
│   └── ...

├── user-created-or-upload-files-1.*
├── user-created-or-upload-files-2.*
└── .project.json        # Hidden system metadata (non-deletable)
```

---

# 8. Context Layering Semantics

## 8.1 `context-L0.md`

* Ultra-concise semantic abstract (~100 tokens)
* Used for rapid routing and relevance filtering

## 8.2 `context-L1.md`

* Expanded structured overview (~2k tokens)
* Describes goals, structure, key knowledge, and relationships

Together, L0 + L1 form a **two-layer semantic context model** for efficient reasoning and retrieval.

---

# 9. Context Assembly Order (Agent Runtime)

When a user interacts within **SwarmAgent**, context is assembled in the following order:

1. Base system prompt (SwarmAgent core)
2. Current live work context (active chat thread, ToDos, tasks, files)
3. Project intent & instructions (`instructions.md`)
4. Project semantic context (`context-L0.md`, `context-L1.md`)
5. Shared artifacts semantic context (`Artifacts/context-L0.md`, `Artifacts/context-L1.md`)
6. Global workspace semantic context (`SwarmWS/context-L0.md`, `SwarmWS/context-L1.md`)
7. Optional scoped retrieval within SwarmWS

### Mental Model

```text
[ System Core ]
        ↓
[ Live Work Context ]
        ↓
[ Project Intent & Instructions ]
        ↓
[ Project Knowledge ]
        ↓
[ Shared Artifacts Knowledge ]
        ↓
[ Global Workspace Memory ]
```

This ensures precision during execution while maintaining long-term memory influence without overwhelming task-specific context.

---

# 10. Workspace Explorer UX Requirements (Complex Structure Made Simple)

SwarmWS exposes a rich, multi-layered structure. The Explorer must present this complexity in a way that feels **simple, calm, and intuitive**, while preserving full transparency and power.

**Core Principle:**

> Full system visibility with selective user attention.

## 10.1 Progressive Disclosure

* Default view shows only top-level sections
* Subfolders collapsed by default
* Expand on demand
* Persist expand/collapse state per session

## 10.2 Semantic Grouping

Visually group the tree into:

* **Operating Loop** (system-driven)
* **Shared Knowledge** (`Artifacts`, `Notebooks`)
* **Active Work** (`Projects`)

Users primarily operate within **Projects**, while other sections act as supporting scaffolding and shared memory.

## 10.3 Focus Mode (Project-Centric)

When opening a project:

* Auto-expand the project
* Collapse operating loop sections
* Keep `Artifacts` visible
* Optional toggle: “Focus on Current Project”

## 10.4 Visual Hierarchy

* Consistent indentation per depth level
* Slight font-weight differences by hierarchy
* Optional indentation guides
* Generous whitespace to reduce visual density

## 10.5 System vs User Content Differentiation

* System folders: neutral icons, non-deletable, tooltip indicating system-managed
* User folders/files: accent icons with full CRUD actions

## 10.6 Search-First Navigation

* Centered global search bar
* Fuzzy search across projects, folders, and files
* Auto-expand path to search results
* Highlight matched nodes

## 10.7 Folder Depth Guardrails

* Operating loop sections: max depth **2**
* Projects: max depth **3**
* Artifacts / Notebooks: max depth **3**

These guardrails prevent structural sprawl, maintain navigability, and improve agent reasoning consistency.

## 10.8 Guided Empty States & Sample Data

* Each default folder contains a README or sample files
* Provide clear usage hints per section
* Ship SwarmWS with realistic sample projects and assets for onboarding

## 10.9 Minimalist Interactions

* Show actions (add, rename, delete) only on hover
* Use minimal icon set (`+`, `⋯`)
* Avoid persistent visual clutter

## 10.10 Smooth Micro-Interactions

* Subtle expand/collapse animations (150–200ms)
* Gentle selection highlights
* Preserve scroll position
* Lazy-load deep folders

## 10.11 Scalability

* Virtualized tree rendering
* Efficient state management
* Maintain responsiveness with hundreds of projects and thousands of files

## 10.12 Visual Design Principles

* Calm, neutral background tones
* Soft separators instead of heavy borders
* Limited accent colors reserved for user content
* Clear, readable typography

## 10.13 Mental Model Alignment

The explorer structure mirrors the agent reasoning model:

1. System Core (Operating Loop)
2. Active Work (Projects)
3. Shared Knowledge (Artifacts / Notebooks)
4. Global Memory (Context Layers)

This alignment increases transparency and trust in agent behavior.

---

# 11. Summary

The final SwarmWS structure provides:

* A **single persistent workspace** acting as long-term memory (**SwarmWS**)
* Clear separation between:

  * System scaffolding (Operating Loop)
  * Shared knowledge (Artifacts / Notebooks)
  * Active execution containers (Projects)
* Predictable, system-managed structure for stable agent operations
* Flexible but depth-controlled user areas for real-world project organization
* Built-in hierarchical context layering (L0/L1) for efficient reasoning and retrieval
* A calm, scalable Explorer UX that progressively reveals complexity and supports focused work

This design positions SwarmWS as:

> A local, persistent, agent-native operating workspace for structured knowledge work.

