# SwarmAI Chat Session & Thread Management Design (Enhanced with Kiro, Claude Code & Claude Co-work Lessons)

## 1. Purpose

This document defines the enhanced production-grade architecture for managing Chat Sessions and Chat Threads in SwarmAI, incorporating key lessons learned from:

- Kiro (Agent + Skills + MCP governance model)
- Claude Code (execution-centric thread model)
- Claude Co-work (collaborative human-in-the-loop workflows)

The goal is to ensure SwarmAI threads are not mere conversations, but **executable work contexts** that:

- Run in parallel
- Respect workspace-scoped memory and governance
- Produce durable outputs (Artifacts)
- Support multi-user collaboration
- Remain deterministic, auditable, and high-performance (local-first)

---

## 2. Core Design Principles

### 2.1 Canonical Storage Principle

| Component | Canonical Storage | Rationale |
|-----------|------------------|-----------|
| Chat Threads | SQLite DB | Structured, queryable, concurrent-safe |
| Chat Messages | SQLite DB | Pagination, filtering, integrity |
| Thread Runs | SQLite DB | Parallel execution lifecycle tracking |
| Thread Summaries | SQLite DB | Efficient search & retrieval |
| Session UI State | Local Filesystem | Ephemeral runtime state |
| Artifacts | Filesystem + DB metadata | Durable knowledge outputs |
| W-Frame Cache | Filesystem | Rebuildable performance cache |
| Transcripts (export) | Filesystem | Human-readable portability |

> **Rule:** Database is canonical for structured entities; filesystem is for UI state, content, exports, and caches only.

---

## 3. Conceptual Model

### 3.1 Key Definitions

#### Chat Thread
A persistent, executable work context bound to:
- Workspace
- Agent role
- Optional ToDo / Task
- Effective Skills, MCPs, and Knowledgebases
- Context Engine W-Frame snapshot

#### Chat Session
A runtime UI container that manages:
- Open tabs (thread IDs)
- Draft input text
- Scroll positions
- Selected workspace and scope
- Panel & navigation state

Sessions are ephemeral and not part of canonical work history.

#### Thread Run
A single execution cycle within a thread:
- Context build (W-Frame)
- Agent execution (Skills + MCP)
- Streaming outputs
- Optional artifact creation

---

## 4. Relationship Model

```

Workspace
└── ChatThread
├── ChatMessages
├── ThreadRuns (parallel execution instances)
├── ThreadSummary (rolling / final)
├── Linked ToDo / Task (optional)
└── Produced Artifacts

```

Sessions reference open thread IDs and UI state only; they never own history.

---

## 5. Key Lessons Integrated from External Systems

### 5.1 From Kiro: Capability-Governed Agent Execution

**Insight:** Threads must carry explicit capability configuration, not implicit tool access.

Each thread execution must include:
- agent_id
- effective Skills (intersection model)
- effective MCP servers
- effective Knowledgebases (union with exclusions)
- workspace context summary

Execution must validate:
```

effective_skills = swarmws_skills ∩ workspace_skills
effective_mcps   = swarmws_mcps ∩ workspace_mcps

```

If required capabilities are disabled:
- Execution SHALL be blocked
- UI SHALL show policy conflict explanation
- User may enable required capabilities via workspace settings

---

### 5.2 From Claude Code: Thread = Live Execution Workspace

**Insight:** Threads are not chat logs; they are live working environments.

Each thread should behave as:
- Iterative execution loop
- Tool-transparent environment
- Output-producing sandbox

Execution lifecycle:
```

User Prompt → Create Run → Build W-Frame → Execute → Produce Output → Update Summary

````

Threads must support:
- Multiple runs per thread
- Iterative refinement
- Transparent tool usage logging
- Direct artifact generation

---

### 5.3 From Claude Co-work: Collaborative Human-in-the-Loop Workflows

**Insight:** AI should collaborate with humans inside shared work contexts.

SwarmAI threads must support:
- Workspace-scoped shared execution context
- Human review checkpoints before artifact promotion
- Multi-user visibility of thread status
- Assignment routing across users

Threads become collaborative execution spaces rather than private chat sessions.

---

## 6. Database Schema (Canonical)

### 6.1 chat_threads

```sql
chat_threads (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  agent_id TEXT,
  task_id TEXT,
  todo_id TEXT,
  mode TEXT CHECK(mode IN ('explore','execute')),
  title TEXT,
  created_at DATETIME,
  updated_at DATETIME
);
````

### 6.2 chat_messages

```sql
chat_messages (
  id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL,
  role TEXT CHECK(role IN ('user','assistant','tool','system')),
  content TEXT,
  tool_calls JSON,
  created_at DATETIME
);
```

### 6.3 thread_runs (parallel execution tracking)

```sql
thread_runs (
  id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL,
  status TEXT CHECK(status IN ('pending','running','completed','failed','cancelled')),
  context_hash TEXT,
  started_at DATETIME,
  completed_at DATETIME,
  error TEXT
);
```

### 6.4 thread_summaries (searchable indexing layer)

```sql
thread_summaries (
  id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL,
  summary_type TEXT CHECK(summary_type IN ('rolling','final')),
  summary_text TEXT,
  key_decisions TEXT,
  open_questions TEXT,
  updated_at DATETIME
);
```

### 6.5 thread_artifacts (link table)

```sql
thread_artifacts (
  id TEXT PRIMARY KEY,
  thread_id TEXT,
  artifact_id TEXT,
  created_at DATETIME
);
```

---

## 7. Filesystem Usage (Non-Canonical)

### 7.1 Storage Layout

```
<swarm_root>/
└── .swarm/
    ├── sessions/
    │   ├── sessions.json
    │   ├── session_<id>.json
    │   └── drafts/
    │       └── thread_<thread_id>.md
    ├── cache/
    │   └── wframes/
    │       └── <thread_id>.json
    └── index/
        └── search.sqlite  (optional future)
```

Filesystem is used only for:

* UI session state
* Draft inputs
* W-Frame performance caches
* Optional transcript exports

---

## 8. Session Management Design

### 8.1 Why Sessions Use Filesystem

Session state is:

* Highly dynamic
* UI-scoped
* Rebuildable from DB history

Thus it is stored as lightweight JSON files.

### 8.2 sessions.json

```json
{
  "activeSessionId": "sess_123",
  "openSessions": ["sess_123", "sess_456"]
}
```

### 8.3 session_<id>.json Schema

```json
{
  "sessionId": "sess_123",
  "activeWorkspaceId": "swarmws",
  "scopeMode": "global",
  "openThreadIds": ["thread_a", "thread_b"],
  "activeThreadId": "thread_b",
  "uiState": {
    "leftPanelCollapsed": false,
    "selectedSection": "execute",
    "scrollPositions": {
      "thread_a": 1200,
      "thread_b": 340
    }
  },
  "updatedAt": "2026-02-22T10:30:00Z"
}
```

---

## 9. Parallel Thread Execution Model

### 9.1 Execution Rules

* Each thread may spawn multiple independent runs
* Each run builds its own W-Frame snapshot
* Runs are isolated and do not mutate other threads
* Runs inherit workspace configuration + agent capabilities

### 9.2 Run Lifecycle

```
User Input
   ↓
Create Run (thread_runs)
   ↓
Context Engine builds W-Frame
   ↓
Capability validation (Skills/MCP policy check)
   ↓
Agent Execution (tools + reasoning)
   ↓
Streaming Messages
   ↓
Artifact Creation (optional)
   ↓
Run Completed → Rolling Summary Update
```

---

## 10. Artifactization Workflow (Durable Outputs)

Threads are execution sandboxes; durable knowledge must be promoted to Artifacts.

### 10.1 Promotion Flow

```
Thread Output → Human Review Gate → Confirm → Save as Artifact
```

Artifacts stored in workspace filesystem:

```
Artifacts/
├── Plans/
├── Reports/
├── Docs/
└── Decisions/
```

Metadata stored in DB; content stored as markdown file.

This aligns with Claude Code’s “write real outputs” model and Co-work’s review checkpoints.

---

## 11. W-Frame Cache Integration (Context Engine)

Each run constructs a workspace-scoped W-Frame:

* Workspace context files
* Effective Skills & MCP summary
* Knowledgebase sources
* Thread rolling summary

### Cache Location

```
.swarm/cache/wframes/<thread_id>.json
```

Cache Strategy:

* TTL invalidation
* Rebuild when workspace context changes
* Never canonical (always rebuildable)

---

## 12. Multi-Agent & Role-Specialized Threads

Inspired by Kiro’s specialized agents, threads may bind to specific agent roles:

Examples:

* Planning Agent Thread
* Execution Agent Thread
* Reporting Agent Thread

All threads share:

* Same workspace memory container
* Same canonical entities
* Same artifact repository

This enables a true “AI team” collaboration model inside one workspace.

---

## 13. Collaboration & Multi-User Integration

Threads operate within workspace-scoped shared contexts:

* Visible to all workspace members
* Assignment routing links threads to Tasks
* Users can collaborate on same thread execution
* Review gates enforce human-in-the-loop governance

This extends Claude Co-work’s collaboration idea with structured canonical entities.

---

## 14. Search & Indexing Strategy

Default search uses `thread_summaries` rather than raw messages:

* Faster queries
* Lower storage overhead
* Better semantic retrieval

Future optional full-text index:

```
.swarm/index/search.sqlite
```

---

## 15. Concurrency & Integrity Guarantees

### 15.1 Database

* ACID transactions for messages and runs
* Safe parallel execution
* Deterministic ordering

### 15.2 Filesystem Safety

All writes must use atomic pattern:

```
write temp file → fsync → rename
```

Prevents corruption during crashes or power loss.

---

## 16. Final Design Summary

### 16.1 Canonical vs Non-Canonical Split

| Layer               | Storage           | Canonical |
| ------------------- | ----------------- | --------- |
| Threads             | SQLite DB         | Yes       |
| Messages            | SQLite DB         | Yes       |
| Runs                | SQLite DB         | Yes       |
| Summaries           | SQLite DB         | Yes       |
| Sessions (UI state) | Filesystem        | No        |
| Drafts              | Filesystem        | No        |
| W-Frame Cache       | Filesystem        | No        |
| Artifacts           | FS + DB metadata  | Yes       |
| Transcripts         | Filesystem export | No        |

---

## 17. Final Principles

1. Threads are **executable work contexts**, not simple conversations.
2. Sessions manage **UI runtime state**, not canonical history.
3. Runs provide **parallel, auditable execution cycles**.
4. Artifacts represent **durable knowledge outputs**.
5. Workspace configuration governs **capabilities and context deterministically**.
6. Multi-agent and multi-user collaboration operate within shared workspace memory.

> This architecture transforms SwarmAI from a chat interface into a collaborative, capability-governed, multi-user AI Work Operating System.

