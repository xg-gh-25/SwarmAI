# Thread-Scoped Cognitive Context (TSCC) — Unified UX Design & Lifecycle Spec
*SwarmAI Chat-Embedded Context Panel (Input-Adjacent, Collapsible, Thread-Archived)*

---

## 1. Purpose

This document specifies the unified UX design for the **Thread-Scoped Cognitive Context (TSCC)** in SwarmAI.

TSCC is a **lightweight, collapsible context panel** anchored **directly above the chat input box**. It provides **live, thread-specific cognitive context** without interrupting the chat flow, and it is **archived with the chat thread**.

TSCC answers six key questions for the *current thread*:

1. Where am I working? (scope clarity)
2. What is the AI doing right now? (live activity)
3. Which agents are involved? (execution transparency)
4. What capabilities are being used? (skills, MCPs, tools)
5. What sources is the AI using? (trust + grounding)
6. What is the current working conclusion? (continuity)

---

## 2. Design Principles

### 2.1 Thread-Owned
- TSCC belongs strictly to the current chat thread.
- It does not represent workspace-global or cross-thread state.

### 2.2 Non-Intrusive by Default
- Collapsed by default.
- Never blocks typing or reading.
- Never auto-expands unless high-signal events occur.

### 2.3 Transparent Multi-Agent Cognition
- Explicitly show which **subagents, skills, MCPs, and tools** are active in the thread.
- Provide clarity without exposing low-level orchestration complexity.

### 2.4 Trust Through Grounded Execution
- Users should always understand what capabilities and sources the AI is using.

### 2.5 Live + Archivable
- TSCC maintains a **live cognitive state**.
- Periodic **context snapshots** are archived with the thread.

### 2.6 Human Language, Not System Telemetry
- Avoid internal pipeline jargon (Signals / Plan / Execute / …).
- Use calm, human-readable descriptions.

---

## 3. Placement & Layout

### 3.1 Placement

TSCC is placed **above the chat input** and below the message list:

```

Chat Messages (Thread History)
────────────────────────────────────
[ ... messages ... ]
────────────────────────────────────
[ TSCC (collapsed by default) ]
────────────────────────────────────
[ Input Box ]

```

### 3.2 Visual Style
- Compact bar when collapsed
- Expandable panel when opened
- Soft separators, calm background
- Must not visually compete with chat messages

---

## 4. Collapsed State (Default)

### 4.1 Goals
- Provide minimal situational awareness
- Encourage optional expansion
- Avoid UI noise and cognitive overload

### 4.2 Collapsed Content

One-line contextual summary + indicators:

```

Context ▸ Workspace: SwarmWS · Agents: 2 · Capabilities: Research + Drafting · Sources: 3 · Updated 2m ago

```

### 4.3 Collapsed Interactions
- Click anywhere to expand
- Optional “pin” to keep expanded

---

## 5. Expanded State (Core Cognitive Modules)

### 5.1 Structure

```

📍 Current Context
🤖 Active Agents & Capabilities
🧠 What AI is doing
📚 Active Sources
✨ Key Summary

```

---

## 6. Module Definitions

### 6.1 Current Context

**Purpose:** Ensure users always know the operational scope of the current thread.

Fields:
- Workspace or Project name
- Thread title/name
- Optional mode tag (Research / Writing / Debugging)

Example:
```

📍 Current Context
Workspace: SwarmWS (General)
Thread: Competitor Brainstorming
Mode: Exploration

```

Rules:
- Always visible
- Updates instantly on project/thread switch
- Avoid negative wording like “No project selected”

---

### 6.2 Active Agents & Capabilities

**Purpose:** Provide transparent visibility into multi-agent execution.

Displays:
- Subagents engaged
- Skills invoked
- MCP connectors used
- Built-in tools activated

Example:
```

🤖 Active Agents & Capabilities
Agents:
• ResearchAgent
• StrategyPlanner

Capabilities:
• Skill: Market Analysis
• MCP: Google Drive Connector
• Tool: Web Search

```

Rules:
- Show only entities actually used in the thread
- Group by Agents / Skills / MCPs / Tools
- Human-readable labels only
- If none: “Using core SwarmAgent only”

---

### 6.3 What AI is Doing (Narrative Activity)

**Purpose:** Explain current AI activity in human language.

Example:
```

🧠 What AI is doing
• Analyzing competitor positioning
• Drafting go-to-market outline
• Preparing executive summary

```

Rules:
- 2–4 bullets max
- No technical stage names
- If idle: “Waiting for your input”

---

### 6.4 Active Sources

**Purpose:** Show grounding materials referenced in this thread.

Example:
```

📚 Active Sources
• competitor-analysis-2026.md (Project/Research)
• pricing-notes.md (Knowledge Base)
• q1-meeting-notes.md (Notes)

```

Rules:
- Only thread-relevant sources
- Include origin tag (Project / Knowledge / Notes / External MCP)
- If none: “Using conversation context only”

---

### 6.5 Key Summary (Working Conclusion)

**Purpose:** Provide continuity across turns.

Example:
```

✨ Key Summary
• Enter mid-market via channel partners
• Differentiate on compliance + onboarding speed
• Rollout in 3 phases starting with SG

```

Rules:
- 3–5 bullets max
- Represents working conclusion, not final report
- If not ready: “No summary yet — ask me to summarize this thread”

---

## 7. Scope Model — Workspace Root vs Project Scope

### 7.1 No Project Selected (Workspace Root Mode)

If no project is selected, the thread operates under **SwarmWS root scope**.

UX must NEVER display:
- “Project: None”
- “No project selected”

Instead show a positive, intentional scope:

```

Workspace: SwarmWS (General)

```

Meaning:
- Thread belongs directly to workspace root
- Can access Shared Knowledge and workspace memory
- Not bound to any specific project

---

### 7.2 Scope Transition

When user later assigns a project:
- TSCC context updates seamlessly
- No disruptive modal required

Before:
```

Workspace: SwarmWS (General)

```

After:
```

Project: Marketing Strategy

```

Optional subtle system message:
> “This thread is now associated with Project: Marketing Strategy.”

---

## 8. Lifecycle & Edge Case UX Handling

TSCC must gracefully handle the full thread lifecycle.

### 8.1 New Chat Session

Collapsed:
```

Context ▸ Workspace: SwarmWS · New thread · Ready

```

Expanded narrative:
- Waiting for your first instruction
- Core SwarmAgent ready

---

### 8.2 Active Session (Normal Operation)

Collapsed:
```

Context ▸ Project: GTM Strategy · Agents: 2 · Updated just now

```

Tone: confident, progressing, calm.

---

### 8.3 Paused Session

Collapsed:
```

Context ▸ Paused · Waiting for your input

```

Expanded:
- Paused intentionally
- No progress being made
- Resume available

---

### 8.4 Failed Session

Collapsed:
```

Context ▸ Encountered an issue · Needs your guidance

```

Expanded:
- Human-readable explanation
- Suggested recovery options (retry, continue, adjust plan)

Avoid technical errors like:
> “MCP timeout 504”

Use:
> “I couldn’t retrieve external data due to a connection issue.”

---

### 8.5 Cancelled Session

Collapsed:
```

Context ▸ Execution stopped · Partial progress saved

```

Expanded:
- Execution stopped by user
- Progress preserved
- Summary of completed work

Cancel ≠ failure.

---

### 8.6 Resumed Session

Collapsed:
```

Context ▸ Resumed · Continuing previous analysis

```

Expanded:
- Continuing from last cognitive step
- Restores continuity without re-reading history

---

### 8.7 Post-Chat (Idle State)

Collapsed:
```

Context ▸ Idle · Ready for next task

```

Expanded:
- Idle but ready
- Shows final working summary and suggested next step

---

## 9. Snapshot & Archival Model

TSCC maintains both:
- Live cognitive state
- Archived context snapshots

### 9.1 Snapshot Triggers
- Plan decomposition completed
- Decision recorded
- User requests recap
- Multi-step execution phase completed

### 9.2 Snapshot Example
```

[Context Snapshot ▼] (timestamp)
• Agents: ResearchAgent + StrategyPlanner
• Capabilities: Market Analysis Skill, Web Search Tool
• Sources: 3
• Focus: competitor analysis
• Summary: pricing advantage dominates

```

Snapshots appear in history collapsed by default.

---

## 10. Thread Switching Behavior

### 10.1 Strict Thread Binding
- TSCC instantly switches to that thread’s live state
- Expanded/collapsed preference preserved per thread

### 10.2 No Cross-Thread Leakage
- Agents, skills, MCPs, tools, and sources must reflect only the active thread

---

## 11. Interaction Rules

1. TSCC must never auto-open during normal chat flow.
2. Auto-expand only for high-signal events:
   - First plan creation
   - Blocking issue requiring input
   - Explicit “show context / sources / agents” request
3. Otherwise update silently with freshness indicator.

---

## 12. Data Model (Conceptual)

TSCC is persisted as part of a **Thread State** object.

Fields:

- `thread_id`
- `project_id` (nullable)
- `scope_type` ("workspace" | "project")
- `last_updated_at`

`live_state`:
- `context`: { workspace_name | project_name, thread_title, mode }
- `active_agents`: [string]
- `active_capabilities`:
  - `skills`: [string]
  - `mcps`: [string]
  - `tools`: [string]
- `what_ai_doing`: [string]
- `active_sources`: [{ path, origin }]
- `key_summary`: [string]

`snapshots`:
- `timestamp`
- `active_agents`
- `active_capabilities`
- `what_ai_doing`
- `active_sources`
- `key_summary`
- `reason`

---

## 13. UX Acceptance Criteria

1. TSCC is visible above input in every thread.
2. TSCC is collapsed by default.
3. Expanded TSCC shows all five modules.
4. TSCC correctly handles Workspace Root scope when no project is selected.
5. Switching threads updates TSCC state instantly.
6. TSCC gracefully handles new, active, paused, failed, cancelled, resumed, and idle states.
7. Snapshots are archived and viewable in thread history.
8. TSCC never interrupts typing or causes scroll jumps.
9. Active Agents & Capabilities always reflect only entities used in the current thread.

---

## 14. Summary

TSCC is a **thread-owned, collapsible cognitive context panel** that:

- reveals active subagents, skills, MCPs, and tools,
- grounds reasoning with explicit sources,
- maintains continuity via live summaries and snapshots,
- gracefully handles lifecycle edge cases,
- and supports both workspace-root and project-scoped execution.

It functions as a calm, transparent “cognitive window” into SwarmAI’s multi-agent execution model, without ever overwhelming the user.
