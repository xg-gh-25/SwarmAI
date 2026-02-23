# SwarmWS — Root Workspace Design Specification
## SwarmWS = Global Daily Work Operating System

> SwarmWS is the built-in, non-deletable Root Workspace of SwarmAI.  
> It represents the user’s **global, always-on work operating environment**, orchestrating signals, planning, execution, communication, artifacts, and reflection across all domains.

SwarmWS is not just a default folder.  
It is the **primary cockpit** for managing a knowledge worker’s entire daily work lifecycle.

---

# 1. Purpose & Positioning

## 1.1 Definition

**SwarmWS (Swarm Workspace System)** is the permanent root workspace automatically created for every user.  
It cannot be deleted and serves as the global coordination layer for all work.

> Positioning:  
> **SwarmWS = Personal Work Operating System**  
> Custom Workspaces = Domain/Project Execution Environments

---

## 1.2 Core Value Proposition

SwarmWS answers:
- What needs my attention today?
- What’s happening across all my work?
- What should I focus on next?
- What did I accomplish and learn?

It consolidates:
- Signals from all integrations
- Cross-project planning
- Ad-hoc and personal tasks
- Global communication and follow-ups
- Long-term personal knowledge artifacts
- Cross-domain reflection and improvement

---

# 2. Role in Workspace Hierarchy

## 2.1 Hierarchical Model

```mermaid
flowchart TD
A[SwarmWS - Root Workspace\n(Global Daily Work Loop)]
B1[Workspace: Project Alpha]
B2[Workspace: Customer Account]
B3[Workspace: Personal Initiatives]

A --> B1
A --> B2
A --> B3
````

* SwarmWS is always present and pinned at the top.
* All custom workspaces inherit baseline context, tools, and policies from SwarmWS.
* Custom workspaces can be created, edited, archived, and deleted.

---

# 3. Core Responsibilities of SwarmWS

SwarmWS operates as the **global coordination hub** across the Daily Work Operating Loop.

---

## 3.1 Signals — Global Intake Layer

### Purpose

Aggregate all incoming work signals across integrations and workspaces.

### Sources

* Email
* Slack / messaging
* Meetings & calendars
* Tasks from external systems
* Signals promoted from custom workspaces
* Manual quick capture (notes, ideas, reminders)

### Capabilities

* Triage signals globally
* Route signals to specific custom workspaces
* Convert signals into tasks directly within SwarmWS

> SwarmWS acts as the **universal inbox for work signals**.

---

## 3.2 Plan — Cross-Workspace Planning Layer

### Purpose

Provide a unified planning and prioritization view across all workspaces.

### Contents

* Today’s focus across domains
* Upcoming priorities
* Blocked items needing decisions
* AI-recommended task prioritization
* Cross-workspace workload balancing

### Capabilities

* Reprioritize tasks across workspaces
* Set daily/weekly focus
* Defer or escalate tasks
* Decide where execution should happen (SwarmWS vs specific workspace)

> SwarmWS functions as the **cross-workspace planning dashboard**.

---

## 3.3 Execute — Default & Ad-Hoc Execution Layer

### Purpose

Serve as the fallback execution context for general or cross-domain work.

### Usage Scenarios

* Personal productivity tasks
* Ad-hoc quick tasks
* Cross-project initiatives
* Tasks created without selecting a custom workspace

### Capabilities

* Run agent-driven execution threads
* Attach global context and knowledge
* Delegate work that does not belong to a single domain

> SwarmWS is the **default execution context** for general work.

---

## 3.4 Communicate — Global Alignment Layer

### Purpose

Centralize all communication and follow-up work across domains.

### Contents

* Pending replies across email/Slack/etc.
* AI-generated draft messages
* Follow-up reminders
* Stakeholder update history

### Capabilities

* Approve and send communications
* Track cross-project stakeholder alignment
* Audit communication actions executed by agents

> SwarmWS acts as the **global communication command center**.

---

## 3.5 Artifacts — Personal Knowledge Vault

### Purpose

Store reusable, cross-domain knowledge outputs and decisions.

### Artifact Types

* Personal plans and frameworks
* Cross-project reports
* Decision records and rationales
* Reusable templates and strategies

### Capabilities

* Version artifacts
* Tag and search knowledge
* Reuse artifacts across workspaces

> SwarmWS becomes the user’s **long-term personal knowledge repository**.

---

## 3.6 Reflection — Cross-Domain Review & Learning

### Purpose

Enable high-level review, retrospection, and continuous improvement.

### Contents

* Daily recap across all workspaces
* Weekly summary of achievements and blockers
* Key decisions made
* Lessons learned and improvement suggestions

### Capabilities

* Generate AI retrospectives
* Highlight productivity patterns
* Recommend next-day priorities

> SwarmWS is the **learning and improvement hub** for all work.

---

# 4. Behavioral Rules

## 4.1 Non-Deletable Root Workspace

* SwarmWS cannot be deleted or archived.
* It always exists as the root anchor of user work memory.

## 4.2 Default Routing Rules

* New signals default to SwarmWS unless assigned.
* Tasks created without selecting a workspace belong to SwarmWS.
* Users can reassign signals and tasks to custom workspaces.

## 4.3 Inheritance Model

Custom workspaces inherit from SwarmWS:

* Baseline context memory
* Allowed tool & MCP access
* Autonomy and review gate defaults (only stricter overrides allowed)

---

# 5. UX & Navigation Design

## 5.1 Navigation Placement

SwarmWS is always pinned at the top:

```text
Workspaces
────────────
• SwarmWS (Global)
• Project Alpha
• Customer X
• Personal Growth
```

## 5.2 Naming & Labeling

* Display Name: **SwarmWS**
* Subtitle: “Your Global Work Hub”
* Tooltip: “The default workspace that aggregates and orchestrates all your daily work.”

---

# 6. Interaction Model

## 6.1 Default User Flow

1. User opens SwarmAI → lands in SwarmWS.
2. Reviews Signals and Planning suggestions.
3. Decides:

   * Handle directly in SwarmWS
   * Or route to a specific custom workspace.
4. Executes tasks with agents.
5. Reviews artifacts and communication outputs.
6. Ends day with Reflection recap.

---

# 7. Comparison: SwarmWS vs Custom Workspace

| Dimension     | SwarmWS (Root)                 | Custom Workspace         |
| ------------- | ------------------------------ | ------------------------ |
| Scope         | Global, cross-domain           | Focused domain/project   |
| Deletable     | ❌ No                           | ✅ Yes                    |
| Signal Intake | All sources aggregated         | Scoped signals only      |
| Planning      | Cross-workspace prioritization | Local prioritization     |
| Execution     | Default & ad-hoc tasks         | Domain-specific tasks    |
| Communication | Unified global inbox           | Scoped communications    |
| Artifacts     | Personal/global knowledge      | Domain/project knowledge |
| Reflection    | Cross-domain learning          | Domain retrospectives    |

---

# 8. Design Principles

1. **Always-On Root Context**
   SwarmWS is the persistent working environment across sessions.

2. **Global Orchestration Layer**
   Coordinates work across all custom workspaces.

3. **Default Safe Fallback**
   If no workspace is specified, execution safely occurs in SwarmWS.

4. **Personal Knowledge Memory**
   Serves as long-term decision and learning repository.

5. **Governance Anchor**
   Provides baseline autonomy policies and audit defaults.

---

# 9. Summary

SwarmWS is the permanent Root Workspace that functions as the user’s:

> **Global Daily Work Operating System**

It captures all signals, guides planning, orchestrates cross-domain execution, centralizes communication, preserves personal knowledge artifacts, and enables continuous reflection and improvement — while custom workspaces provide focused environments for specific projects or domains.
