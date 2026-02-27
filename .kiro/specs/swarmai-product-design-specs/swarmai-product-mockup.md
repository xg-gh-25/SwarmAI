# SwarmAI - Core-Mockup Instructions

## Visual Language & Style Guide

To align with the requested Apple-inspired aesthetic, the UI should feel "calm and minimal".

- **Color Palette:** Deep space grays and pitch blacks for the background (OLED-friendly). High-contrast accents using the cyan-to-purple gradients seen in the logo for active AI elements.
- **Typography:** Clean, sans-serif fonts (e.g., SF Pro) with generous whitespace to reduce cognitive load.
- **Components:** Glassmorphism effects (frosted glass sidebars), subtle glows for "active" agents, and clear, rounded status indicators.

---

# 🎯 Core Mock-up Screens

## Screen A: Command Center (Primary View)

This is the default landing screen after login.

SwarmAI should feel like:
- A calm command bridge
- A workspace OS
- Not a messaging app

---

# 🧱 Layout Overview (3-Column Structure)

| | Left | Center | Right |
|---|---|---|---|
| 1 | Navigation | Task Command Panel | Radar & Control Panel |

---

# 1️⃣ Left Column — Navigation (Minimalist)

**Purpose:** Move between product pillars without cognitive overload.

Icons only (labels on hover or optional expand).

### Core Navigation

- **Command**  
  Active execution layer (default)

- **Workspaces**  
  Persistent memory containers (Root + child Workspaces)

- **Autonomy**  
  Delegations, subscriptions, automation logs

- **Swarm Core**  
  Personal context, knowledge, agents, tools

- **Settings**  
  Governance, models, privacy, backup

**Design Principle:**  
Clean. Calm. No technical noise.

---

# 2️⃣ Center Column — Task Command Panel

### (Primary Operational Surface)

This is the heart of SwarmAI.

Not a chat app.  
A Task Execution Console.

---

## 2.1 Chat-Based Work Thread (Execution Surface)

Each conversation = one Swarm Task.

### Default View (No Active Task)

Centered welcome:

> SwarmAI — Your AI Team, 24/7
> 
> What would you like your team to work on today?

Below:  
Minimal empty state guidance:
- Start a task
- Select a ToDo
- Open a Workspace

---

### Interaction Bar

At bottom of panel:

Placeholder:
> Delegate a task or start a new thread…

Icons:
- Voice input
- Attach files (local + connected sources)
- Select Workspace context
- Submit

---

## 2.2 Active Task Mode

When a Task starts:

The center panel expands into a full execution thread.

**Header displays:**
- Task Title
- Linked Workspace
- Task Status (WIP / Blocked / Completed)
- Delegation indicator (if autonomous)

**Task thread includes:**
- Chat messages
- Execution plan
- Tool calls (collapsible)
- Status updates
- Summary generation
- Feedback controls

**Design Goal:**  
This must feel like controlled power — not noisy logs.

---

# 3️⃣ Right Column — Intelligence & Control Panel

This is not secondary — it's the awareness layer.

Stacked vertically:

---

## 🔎 Section 1: ToDo Radar (Default Top)

**Purpose:** Attention capture engine.

**Default:** Show top 6 AI-prioritized ToDos.

**Each card shows:**
- ToDo Title
- Priority (H/M/L)
- Source icon (Slack, Gmail, etc.)
- Due indicator
- Workspace mapping

**Interaction:**  
Clicking a ToDo:
- Loads context
- Creates or resumes Task
- Activates center execution thread

**Expandable:**  
"View All ToDos"

---

## ⚙️ Section 2: Tasks — WIP

**Displays:**  
Active Swarm Tasks (max 5–10 initial limit)

**Each row shows:**
- Task Title
- Workspace
- Status
- Last updated time

Click → Resume Task in center panel.

---

## 🤖 Section 3: Autonomy Tasks

**Shows:**  
Delegated / autonomous executions.

**Each card:**
- Task name
- Execution status
- Awaiting approval indicator (if needed)
- Summary badge

Click → View execution log & report.

---

## 📜 Section 4: Task History

Completed Tasks.

**Filterable by:**
- Workspace
- Date
- Status

Click → View report & chat history.

---

# 🎬 Behavioral Flow

### When No Task Active:

Right panel is visible.  
User can pick ToDo or resume Task.

---

### When Task Active:

Center panel expands.  
Right panel remains visible but slightly compressed.  
User can switch tasks easily.

No bottom movement animation needed.  
Keep spatial stability.

---

# 🎯 Design Philosophy

**Center = Execution**  
**Right = Awareness**  
**Left = Structure**

This mirrors:

🧠 Intent → 🤖 Execution → 📊 Oversight
