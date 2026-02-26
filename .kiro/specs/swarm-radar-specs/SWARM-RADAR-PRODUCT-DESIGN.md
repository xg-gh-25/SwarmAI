# SwarmAI — Swarm Radar (Right Sidebar)
*Final Product Design Specification*

---

# 1. Overview

## 1.1 Purpose

**Swarm Radar** is the unified attention & action control panel of SwarmAI, providing a real-time, glanceable view of all work items across their lifecycle:

Source → ToDo → Task (WIP) → Review/Waiting → Completed → Archived

It consolidates:
- ToDos (from chat, manual entry, or external sources)
- Active WIP execution tasks
- Items requiring user input or approval
- Completed tasks (recent)
- Autonomous and recurring AI jobs

**Primary Goal:**  
Allow users to instantly know what needs attention, what AI is doing, and what is done — with minimal cognitive load.

---

# 2. Core Layout Positioning

Left: SwarmWS (Knowledge / Projects / Memory)  
Center: Chat Thread (Execution Surface)  
Right: Swarm Radar (Attention & Action Panel)

| Area | Role |
|------|------|
| SwarmWS (Left) | Persistent knowledge, context, and project memory |
| Chat (Center) | Execution surface for tasks and conversations |
| Swarm Radar (Right) | Real-time attention & action control center |

---

# 3. Core Design Principles

1. **Glanceable Awareness** — Users understand priorities in seconds  
2. **Lifecycle-Driven** — Reflect full work lifecycle  
3. **Burn-Down Friendly** — Active lists shrink as work progresses  
4. **Human-in-the-Loop** — Only surface necessary decisions  
5. **Conversational Simplicity** — All deep work handled via chat  
6. **Bidirectional Interaction** — Radar items can feed into chat as context  
7. **Priority & Timeline Clarity** — Clear urgency via priority and due/overdue indicators  

---

# 4. High-Level Structure

Swarm Radar
- 🔴 Needs Attention
  - ToDos (Unified Inbox)
  - Waiting Input / ToReview
- 🟡 In Progress
  - WIP Tasks
- 🟢 Completed
  - Recently Completed Tasks
- 🤖 Autonomous Jobs

---

# 5. ToDos — Unified Work Inbox

## 5.1 Sources of ToDos

ToDos can be created from:

1. External integrations  
   - Email / Slack / Teams  
   - Calendar  
   - Jira / Taskei / task systems  

2. Chat session  
   - Example: “Create a ToDo to review Q2 plan tomorrow”

3. Manual creation  
   - Quick-add input in Swarm Radar

4. AI-detected intent  
   - Example: “I should follow up with the client next week”

---

## 5.2 ToDo Data Model (User-Facing Fields)

Each ToDo includes:
- Title / Description
- Source (Chat / Email / Slack / Manual / AI-detected)
- Priority (High / Medium / Low)
- Due date / timeline
- Status (Active / Overdue / Cancelled / Completed)
- Linked context (message, document, or thread)

---

## 5.3 Priority & Timeline Indicators

| Indicator | Meaning |
|-----------|---------|
| 🔴 High Priority | Urgent / critical |
| 🟡 Medium | Normal work |
| 🔵 Low | Optional / backlog |
| ⏰ Due Today | Needs attention soon |
| ⚠️ Overdue | Past due date |

Example:
[Email] Prepare client escalation response  
Priority: High 🔴  
Due: Today ⏰  
Status: Active  

---

## 5.4 ToDo Lifecycle Actions

Users can:
- Start (convert to task)
- Edit
- Cancel
- Delete

| Action | Result |
|--------|--------|
| Start | Converts to TaskDraft → WIP |
| Cancel | Marks as cancelled & archived |
| Delete | Removes from active view (traceable in history) |
| Complete | Mark resolved without execution |

---

# 6. Drag & Drop Interaction (Global Rule)

> All Swarm Radar items support drag & drop into the chat window to dive deeper or continue work.

This creates a consistent mental model:  
“Drag any work item → discuss or act on it in chat”

---

# 7. Drag-Drop Behavior by Item Type

## 7.1 ToDo → Chat
- Fetch full context (source, links, metadata)
- Clarify requirements (if needed)
- Create TaskDraft → start execution
- ToDo moves to WIP

## 7.2 WIP Task → Chat
- Reuse the same existing execution thread
- Continue discussion or provide input
- No new thread created

## 7.3 Completed Task → Chat
- Resume prior thread OR
- Create new thread seeded with completion context

## 7.4 Autonomous Job → Chat
- Open configuration / discussion context
- Used for editing schedule or reviewing outputs

---

# 8. Needs Attention — Waiting Input / ToReview

## 8.1 Mid-Execution Input
Task paused because clarification or decision is required.

Example:
Draft client email (Waiting for input)  
Question: Confirm tone (formal / friendly)?

## 8.2 Conditional Review (Risk-Based)
Not all completed tasks require review. AI decides based on risk & confidence.

| Risk Level | Behavior |
|------------|----------|
| Low | Auto-complete silently |
| Medium | Notify only |
| High | Move to Review |
| Critical | Mandatory approval |

---

# 9. WIP Tasks — Execution Threads

## 9.1 Creation Paths
WIP Tasks are created when:
1. ToDo is started (drag/click/command)
2. User starts free-form chat (auto-task created)
3. AI proposes a task and user confirms

Every chat thread = one execution context = one WIP Task.

## 9.2 Status Variants
- Executing
- Waiting for input
- Paused
- Error / retrying

---

# 10. Completed Tasks — Lightweight Closure

Completed tasks:
- Appear temporarily in “Completed”
- Move to history archive after time window
- Fully traceable via lineage

No mandatory review unless policy requires it.

---

# 11. Autonomous Jobs

Two categories:
1. System built-in background jobs (sync, indexing)
2. User-defined recurring agent jobs (daily digest, reports)

Failures or attention-needed states surface in Needs Attention.

---

# 12. Free-Form Chat → ToDo Creation

Chat can create ToDos naturally:

User: “Remind me to prepare the weekly report tomorrow”

System:
ToDo created:
“Prepare weekly report”
Due: Tomorrow
Priority: Medium

This ToDo appears in Swarm Radar with full edit/cancel support.

---

# 13. Unified User Flow

1. ToDo appears or is created
2. User drag / click / command
3. TaskDraft created (linked to ToDo)
4. Clarification in chat (if needed)
5. Execution begins → WIP
6. Mid-execution input (optional)
7. Completion → auto or review (policy-based)
8. Completed → archived (traceable)

---

# 14. Final Mental Model for Users

Users experience Swarm Radar as:

- What needs my attention
- What AI is working on
- What is waiting for my input
- What has been completed
- What is running automatically for me

They do not need to understand internal lifecycle states — only act when necessary.

---

# 15. Final Summary

Swarm Radar is the AI-native operational cockpit of SwarmAI.

It:
- Aggregates ToDos from chat, manual input, and external sources
- Shows clear priority and timeline urgency
- Supports delete / cancel / edit lifecycle control
- Allows drag & drop of any item into chat for deep interaction
- Uses smart policies to minimize unnecessary reviews
- Maintains full traceability while keeping active lists clean

This design ensures:
- User-friendly simplicity
- Strong lifecycle governance
- Seamless chat-centric workflow
- Scalable enterprise-ready task orchestration

