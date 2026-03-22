# SwarmAI — High-Level Product Design & Architecture (Final)
*Unified overview integrating SwarmWS, Projects, Swarm Radar, TSCC, and Agent Execution Model*

> Canonical foundations:
> - **SwarmWS** — single persistent workspace root (Knowledge + Projects)
> - **Projects** — primary organization unit for active work
> - **Chat Threads** — execution surfaces (explore → execute → review)
> - **Swarm Radar** — right sidebar attention & action control panel
> - **TSCC** — thread-scoped cognitive context panel above input
> - **SwarmAgent + Subagents** — governed multi-agent orchestration
>
> Goal: Present a simple mental model for users and a clean architecture model for implementation.

---

# 1. Product Overview

## SwarmAI — Your AI Team, 24/7  
**Work Smarter. Stress Less. Execute Continuously.**

SwarmAI is a persistent Agentic Operating System for Knowledge Work where a supervised team of AI agents plans, executes, and follows through on your daily work.

It unifies emails, meetings, communications, tasks, documents, and projects into a single operating environment — where context persists, priorities stay visible, and progress compounds over time.

Unlike traditional AI tools that reset every session, SwarmAI maintains long-lived memory across projects and workflows.

You delegate intent signals. The AI team executes under governance. Outcomes become durable knowledge and reusable artifacts.

SwarmAI doesn’t just help you think — it helps you **continuously get real work done.**

---

# 2. Core Mental Model

SwarmAI is not:
- a chat tool
- a task manager
- a simple automation bot

It is:

> **A Command Center for Your AI Execution Team**

### Four Core Principles

- 🧠 **You supervise** — define goals, priorities, and guardrails  
- 🤖 **Agents execute** — plan, coordinate, and carry out work continuously  
- 📁 **Memory persists** — context, decisions, and knowledge accumulate over time  
- 📈 **Work compounds** — outputs become reusable artifacts and institutional knowledge  

Over time, SwarmAI transforms scattered daily activities into structured execution flows, durable outputs, and continuously improving operational memory.


# 3. Core Layout Model

SwarmAI follows a stable three-column + embedded context layout:

| Area | Role |
|------|------|
| **Left — SwarmWS Explorer** | Persistent knowledge + project memory |
| **Center — Chat Threads** | Command & execution surface |
| **Above Input — TSCC** | Live cognitive context of current thread |
| **Right — Swarm Radar** | Unified attention & action control panel |

This layout balances:
- persistent memory (left)
- execution (center)
- transparency (TSCC)
- workload awareness (right)

---

# 4. SwarmWS — Persistent Workspace Root

SwarmWS is the single, non-deletable workspace that acts as the user’s long-term memory container. It organizes content into two semantic zones:

- **Shared Knowledge** (`Knowledge/`) — reusable assets, notes, and distilled memory
- **Active Work** (`Projects/`) — self-contained execution and knowledge containers

Hierarchical context files (`context-L0.md`, `context-L1.md`) exist at workspace, section, and project levels, enabling efficient agent reasoning and predictable context assembly.

Projects replace the concept of custom workspaces. Each project contains instructions, chats, research, and reports, governed by depth guardrails and system-managed templates. This ensures structure consistency, scalable growth, and agent reasoning clarity.

SwarmWS enforces:
- single workspace model
- semantic zone grouping
- system-managed vs user-managed ownership
- folder depth guardrails
- automatic integrity repair and idempotent initialization

This provides a stable, local-first memory foundation for all SwarmAI activity.

---

# 5. Chat Threads — Execution Surfaces

Chat is the primary command surface. Each thread is a live execution workspace rather than a passive conversation log.

Threads support two modes:
- **Exploration Mode** — brainstorming, clarification, planning (no task created)
- **Execution Mode** — governed multi-agent execution tied to a Task

A thread can be associated with a project or run under the workspace root when no project is selected. Threads accumulate context, decisions, artifacts, and execution history, enabling continuity across sessions.

---

# 6. TSCC — Thread-Scoped Cognitive Context

TSCC is a lightweight, collapsible context panel anchored directly above the chat input. It is owned strictly by the current thread and archived alongside it.

TSCC answers six key questions:
1. Where am I working?
2. What is the AI doing right now?
3. Which agents are involved?
4. What capabilities and tools are being used?
5. What sources ground the reasoning?
6. What is the current working conclusion?

It provides calm, human-readable transparency into multi-agent cognition without exposing low-level orchestration details. TSCC updates live, handles thread lifecycle states (active, paused, failed, resumed), and records periodic context snapshots for continuity.

---

# 7. Swarm Radar — Unified Attention & Action Panel (High-Level)

Swarm Radar is the right sidebar that acts as the **operational cockpit** of SwarmAI. It provides glanceable awareness of all work items across their lifecycle:

> **Source → ToDo → Task (WIP) → Waiting Input / Review → Completed → Archived**

At a glance, users understand:
- what needs attention
- what the AI is currently executing
- what is waiting for input or review
- what has recently completed
- what is running automatically in the background

Swarm Radar organizes items into four conceptual zones:
1. **Needs Attention** — ToDos and items requiring user input
2. **In Progress** — Active execution tasks
3. **Completed** — Recently completed outcomes within an archive window
4. **Autonomous Jobs** — Background and recurring agent jobs

ToDos represent structured intent signals arriving from chat, manual capture, or external integrations (email, Slack, meetings, etc.). Tasks represent governed execution threads derived from those signals or directly from user chat.

Waiting Input items originate from active execution sessions when agents require clarification or permission. Completed tasks provide lightweight closure and traceability without forcing heavy review workflows. Autonomous jobs represent system-managed or user-defined recurring agent work (e.g., daily digest, indexing, scheduled reports).

The interaction model is primarily **click-to-chat**: acting on any Radar item routes the user into the appropriate chat thread where deep work and execution occur. Swarm Radar therefore surfaces workload awareness, while chat remains the execution command surface.

Design goals:
- Glanceable awareness in seconds
- Clear separation between intent signals (ToDos) and execution (Tasks)
- Progressive disclosure via collapsible zones
- Minimal cognitive load with strong priority and timeline cues
- Seamless linkage to conversational execution

---

# 8. Swarm ToDos — Intent Signals

ToDos are structured intent signals representing incoming work from:
- manual quick capture
- chat commands
- AI-detected commitments
- external systems (email, Slack, meetings, integrations)

They are distinct from tasks: ToDos express intent, while tasks represent committed execution. This separation ensures clear triage, prioritization, and lifecycle governance before agents begin execution.

---

# 9. Multi-Agent Orchestration (Autonomy Layer)

SwarmAI uses a governed multi-agent model orchestrated by a central SwarmAgent. The orchestrator interprets goals, selects specialized subagents (planning, research, execution, communication, review), and coordinates parallel work under capability and policy constraints.

Human-in-the-loop checkpoints are enforced before sensitive or irreversible actions. Waiting Input signals surface in Swarm Radar, while TSCC transparently shows which agents, skills, and tools are active in the current thread.

This design enables supervised autonomy: agents act proactively but remain transparent, interruptible, and auditable.

---

# 10. Context Assembly Model

When executing within a project, context is assembled in a predictable priority order:
1. Base system prompt
2. Live thread context (current chat, ToDos, tasks, files)
3. Project instructions and semantic context (L0/L1)
4. Shared knowledge context (Knowledge L0/L1)
5. Persistent semantic memory distilled from past interactions
6. Global workspace semantic context (SwarmWS L0/L1)
7. Optional scoped retrieval within SwarmWS

L0 files provide fast relevance routing before loading richer L1 context, ensuring performance and bounded token usage.

---

# 11. Unified Relationship Model

| Entity | Role |
|--------|------|
| SwarmWS | Persistent memory root |
| Project | Self-contained execution and knowledge container |
| ToDo | Structured intent signal |
| Task | Governed multi-agent execution |
| Chat Thread | Command & execution surface |
| TSCC | Thread-owned cognitive transparency panel |
| Swarm Radar | Workload awareness & attention control |
| Artifact | Durable reusable knowledge output |

---

# 12. Continuous Value Loop

SwarmAI turns daily work into compounding knowledge through a continuous loop:

```mermaid
flowchart LR
    A[Workspace & Project Context] --> B[Intent Signals - ToDos]
    B --> C[Governed Execution - Tasks]
    C --> D[Artifacts & Decisions]
    D --> E[Enriched Knowledge & Memory]
    E --> A
````

Each cycle enriches persistent memory, improving future reasoning and execution quality.

---

# 13. Final Summary

SwarmAI is a **persistent agentic operating system for knowledge work** built on four tightly integrated pillars:

* **SwarmWS** provides a single, local-first memory root with semantic zones and hierarchical context layering.
* **Projects** structure active work into self-contained execution and knowledge containers.
* **Swarm Radar** offers a unified, glanceable control panel showing intent signals, execution progress, waiting input, recent outcomes, and autonomous jobs.
* **TSCC** delivers thread-scoped cognitive transparency, revealing agents, tools, sources, and working conclusions in human-readable form.

Together, these elements create a cohesive experience where:

* users supervise high-level intent,
* AI agents execute under governance,
* context persists across sessions,
* and outcomes accumulate into durable institutional knowledge.
