# SwarmAI - Product Design

# SwarmAI - Product Overview

## **SwarmAI — Your AI Team, 24/7**

**_Work Smarter. Stress Less._**

SwarmAI gives you a supervised team of AI agents that plan, act, and follow through across your work. It brings emails, meetings, communications, tasks, documents, and projects into one intelligent command center — where everything stays connected and moving forward.

Unlike traditional AI tools that reset every session, SwarmAI builds private, long-term local memory. It remembers context, preferences, and ongoing priorities so productivity compounds instead of starting from scratch each day. You delegate. Your AI team executes. Every action is transparent, reviewable, and secure.

From extracting ToDos to autonomously handling follow-ups and delivering daily briefings, SwarmAI transforms fragmented tasks into coordinated execution — helping you deliver results with greater efficiency, quality, and confidence.

**Ultra-Short Version (App Store)**

SwarmAI gives you a supervised team of AI agents that plan, act, and follow through across your work. With private local memory and autonomous execution, productivity compounds instead of resetting every day.

## 🎯 Core Product Concept

SwarmAI is a **Persistent Agentic Workspace** for knowledge workers.

It replaces fragmented tools with a coordinated AI team that executes daily work under human supervision — turning disconnected tasks into structured, compounding progress.

## 🧾 Product Thesis

SwarmAI is a persistent, supervised agentic workspace where AI teams execute daily work under human guidance — transforming fragmented effort into lasting, institutional value.

## 🧠 Core Product Mental Model

SwarmAI is **not**:
* A chat app
* A task manager
* A project tool
* An automation bot

It **is**:

> A Command Center for Your AI Team.

Everything revolves around four principles:

🧠 You supervise  
🤖 Agents execute  
📁 Memory persists  
📈 Work compounds

Over time:
* Context accumulates
* Agents adapt and improve
* Workspaces retain structured intelligence
* Productivity scales naturally

SwarmAI doesn't just help you finish tasks. It transforms daily work into compounding value.

---

# SwarmAI - Product Architecture (5 Pillars)

## 1️⃣ Command

### (Execution & Interaction Layer)

**Purpose:**  
The Command layer is the user's operational control center.

Chat is not conversation — it is the interface layer of a Swarm Task.

### Core Components

**Work Thread (Chat = Interface of Task)**

Every chat session maps to exactly one **Swarm Task**.

Task is created when:
* User inputs free text → `TaskSource = free-task`
* User selects a ToDo → `TaskSource = todo-task`
* User resumes an existing WIP/Completed task

No duplicate tasks are created.

### Work Thread Capabilities

Within a Task thread, SwarmAI can:
* Generate Plans
* Draft Documents
* Execute actions
* Call tools
* Communicate externally
* Produce Reports
* Update Workspace memory

Each Task includes:
* Linked Workspace context (Root + specific Workspace)
* Execution plan
* Tool logs
* Status updates
* Summary report
* Feedback system
* Time metadata

### Task States
* WIP (Active)
* Blocked
* Completed
* Cancelled

Users can run multiple tasks in parallel (e.g. 5–10 initial limit).

## 2️⃣ Workspaces

### (Persistent Domain/Project Memory Layer)

**Purpose:**  
Workspaces are structured memory containers.

They define context boundaries and ensure work compounds.

### Hierarchy

Every user has:

**Root Workspace (Global Memory)**

Includes:
* Global Context
* Local Folders
* Knowledge Sources
* Tools & Actions
* Root-level ToDos
* Root-level Tasks
* Child Workspaces

This ensures long-term persistent memory.

**Swarm Workspaces (Project / Domain Memory)**

Created:
* From Workspace panel
* From Chat
* From ToDo

Each Workspace:
* Inherits Root context
* Maintains structured local memory

Includes:
* Context
* Files
* Knowledge Sources
* Tools & Actions
* Plans
* Reports
* Swarm ToDos
* Swarm Tasks (WIP)
* Swarm Completed Tasks

### Design Principle

Every Workspace is a persistent memory boundary.  
Every Task execution enriches that memory.

## 3️⃣ Swarm ToDos

### (Structured Intent Layer)

**Purpose:**  
ToDos represent structured work before execution.

They are intent objects — not execution objects.

### Creation Sources

Automatically extracted from:
* Email
* Calendar
* Slack
* Meeting notes
* Taskei
* Jira
* Workspace tasks
* Other integrations

Users may also manually:
* Add
* Edit
* Cancel
* Delete
* Prioritize

### ToDo Lifecycle

When initiated via Chat:
* Swarm Task is created
* ToDo status changes to **Handled**
* Task becomes **WIP**
* ToDo disappears from Pending view

### ToDo Status Model
* Pending
* Overdue
* Handled (mapped to Task)
* Cancelled
* Deleted

### ToDo Schema (Conceptual)

Each ToDo includes:
* ID
* Title
* Mapped Workspace
* Mapped Task (only when Handled)
* Priority
* Source
* Context summary
* Timestamps
* Status

ToDos = structured intent  
Tasks = structured execution

## 4️⃣ Autonomy

### (Supervised Execution Layer)

**Purpose:**  
Enable SwarmAI to execute under human supervision.

Autonomy operates at the **Task level**, not ToDo level.

### A. Delegated Task Execution

For a Task:
* SwarmAI plans execution
* Calls tools
* Communicates with stakeholders
* Drafts responses
* Completes actions
* Produces summary
* Saves full audit trail

Every execution generates:
* Transparent activity log
* Summary outcome
* Retro report

Trust is built through visibility.

### B. Intelligent Extraction

Feeds Swarm ToDos continuously.

Ensures: Nothing slips through.

### C. Briefings & Subscriptions

Periodic summaries:
* Inbox summary
* Slack digest
* Workspace health
* Delegation recap

Autonomy becomes proactive intelligence.

## 5️⃣ Swarm Core

### (Personalization & Configuration Layer)

**Purpose:**  
Control memory, agents, tools, and integrations.

### Personal Context (Super Agent Memory)

Stored locally by default. Includes:
* Profile
* Goals
* Communication style
* Long-term priorities

Persistent. Private. Optional cloud backup.

### Knowledge Layer

User-managed:
* Local folders
* Drive / S3
* Websites
* Pinned chats
* Vector database

### Sub Agents & Skills

Role-specialized agents that operate within Tasks (provide built-in and custom-agent, custom-skills functions). Examples:
* Research Agent
* Document Agent
* Communication Agent
* Strategy Agent
* Project Agent
* Reporting Agent
* Task Execution Agent
* Daily Intelligence Agent

### Tools & Integrations
* MCP tools
* Connected apps
* Plugins

Advanced configuration layer.

## 🔁 Unified Relationship Model

| | Entity | Layer | Function |
|---|---|---|---|
| 1 | Workspace | Memory Layer | Persistent structured context |
| 2 | ToDo | Intent Layer | Structured work signal |
| 3 | Task | Execution Layer | Active execution thread |
| 4 | Chat | Interface Layer | User interaction surface |

---

# SwarmAI - Product Models

* Swarm Workspace = Memory container
* Swarm ToDo = Structured intent
* Swarm Task = Execution unit
* Chat = Interface layer

## 🧠 Swarm Workspaces — Product Model

### Core Principle

SwarmAI organizes work into **nested memory containers** called Workspaces.

Every user has:
* One **Root Workspace**
* Multiple optional **Swarm Workspaces** (on-demand)

Workspaces structure memory, context, and execution boundaries.

## 🏗 Workspace Hierarchy

### 1️⃣ Root Workspace (Default)

Created automatically for every user. This represents the user's global work memory.

**Root Workspace Includes:**
* **Context**
  * Free text or uploaded files
  * Defines global goals, role, and priorities
* **Local Computer Folders**
  * Files / Docs (user-permitted folders only)
* **Knowledge Sources**
  * MCP sources
  * Cloud storage
  * Vector database
  * Pinned content
* **Tools & Actions**
  * Built-in tools
  * User-configured actions
* **Swarm ToDo List**
* **Swarm Task List (WIP)**
* **Swarm Completed Tasks**
* **Child Workspaces**
  * Workspace 1
  * Workspace 2
  * Workspace 3
  * etc.

The Root Workspace ensures global persistent memory.

### 2️⃣ Swarm Workspaces (Project or Domain Memory)

Users can create Workspaces:
* From the Workspaces panel
* Directly from a chat thread

Each Workspace inherits Root context but maintains its own structured memory.

**Each Workspace Contains:**
* **Context**
  * Free text or uploaded files
  * Defines global goals, role, and priorities
* **Local Computer Folders**
  * Files / Docs (user-permitted folders only)
* **Knowledge Sources**
  * MCP sources
  * Cloud storage
  * Vector database
  * Pinned content
* **Tools & Actions**
  * Built-in tools
  * User-configured actions
* **Plans**
* **Reports**
* **Swarm ToDo List**
* **Swarm Task List (WIP)**
* **Swarm Completed Tasks**

### Design Principle

> Every Workspace is a persistent memory boundary.
> 
> Every interaction enriches that memory.

---

## 📌 Swarm ToDo Model

Swarm ToDos represent structured intent before execution.

ToDos are automatically extracted from:
* Email
* Calendar
* Slack
* Meeting notes
* Jira
* Workspace tasks
* Other configured sources

Users can:
* Add
* Edit
* Cancel
* Delete
* Prioritize

### Swarm ToDo Lifecycle

When a ToDo is initiated in chat:
* A Swarm Task is created
* ToDo status changes to "Handled"
* Swarm Task becomes WIP
* ToDo will invisible (only the Pending, Overdue ToDo will visible)

### Swarm ToDo Schema (Conceptual)

Each ToDo includes:
* ID
* Title
* Mapped Workspace (Root or specific Workspace)
* Mapped Swarm Task (only when ToDo status = handled will have the Swarm Task)
* Priority
* Connected Sources (From where)
* Context Summary
* Status
* Created Time
* Due Time
* Handled Time
* Last Updated Time

### Status Options
* Pending
* Overdue
* Handled (has Swarm task WIP)
* Cancelled
* Deleted

---

## 🤖 Swarm Task Model

A Swarm Task represents an active execution thread.

Each Task = one persistent chat-session + execution history.

### Task Creation

A Swarm Task is created when:
* User inputs free-text (TaskSource = free-task)
* User selects a ToDo (TaskSource = todo-task)

User resumes a WIP or completed task, If a task already exists: It resumes — no duplicate created.

### Swarm Task Capabilities

Each Task contains:
* Linked Workspace context (Root + specific workspace if has)
* Task Source (free-task or todo-task)
* Historical chat session
* Execution plan
* Tool usage logs
* Status updates
* Summary report
* Human Feedback
* Start Time
* Completed Time
* Last Update Time

Users can:
* Run multiple tasks in parallel (limit initially e.g. 5–10)
* Switch between tasks
* Resume anytime
* Review and Revise reports
* Provide feedback (Good / Improve / Share / Copy)
* Change task status

### Swarm Task States
* WIP (Active)
* Blocked (External dependency)
* Completed
* Cancelled

# Appendix

![SwarmAI Home Mockup ](../../../assets/swarmai-home-mockup.png)

![SwarmAI Chat Mockup ](../../../assets/swarmai-chat-mockup.png)

![SwarmAI logo](../../../assets/swarmai-logo-final.png)
