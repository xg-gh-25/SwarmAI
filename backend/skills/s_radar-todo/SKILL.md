---
name: Radar ToDo
description: >
  Manage SwarmAI Radar ToDos — add, list, edit, complete, and delete items that
  appear in the Radar sidebar. Each todo is a self-contained work packet: when
  dragged into a chat tab, the agent has all context to start executing immediately.
  Also proactively creates todos from detected action items and blockers.
  TRIGGER: "add todo", "create todo", "new todo", "my todos", "list todos",
  "mark todo done", "complete todo", "delete todo", "todo list", "what's on my radar".
  DO NOT USE: for Apple Reminders (use apple-reminders), calendar events (use
  outlook-assistant), or project task management (tasks are a different entity).
  SIBLINGS: apple-reminders = one-time reminders synced to Apple |
  outlook-assistant = Outlook calendar + email | save-memory = persistent memory.
input_type: text
output_type: text
---

# Radar ToDo Skill

Manage ToDo items in the SwarmAI Radar sidebar. ToDos are stored in SQLite
(`~/.swarm-ai/data.db`) and displayed in the Radar → TODO section.

**Core principle:** Every todo is a **self-contained work packet**. When a user
drags a todo into a chat tab or says "work on this todo", the agent must be able
to start executing immediately — no re-discovery, no context hunting.

## Tool

```bash
python3 {SKILL_DIR}/scripts/todo_db.py <command> [options]
```

## Work Packet Schema (linked_context)

Every todo's `linked_context` field stores a JSON object with structured context:

```json
{
  "files": ["backend/core/session_unit.py", "desktop/src/hooks/useChatStreamingLifecycle.ts"],
  "design_docs": ["Knowledge/Designs/2026-03-21-append-message-design.md"],
  "commits": ["81f596c", "a070ca3"],
  "sessions": ["3af6258b"],
  "memory_refs": ["COE:2026-03-20:big-bang-refactor", "Lesson:2026-03-22:invariants"],
  "next_step": "Extract _handle_agent_task_result into dispatch table in session_unit.py",
  "acceptance": "Append message queues during stream, last-message-wins, no content loss on tab switch",
  "blockers": ["Need to verify queue drain in finally block handles CancelledError"],
  "notes": "Design doc v2 approved. Queue-based approach — never stops stream."
}
```

| Field | Required? | Purpose |
|-------|-----------|---------|
| `next_step` | **YES** | Concrete first action. Not vague — an actual step the agent executes. |
| `files` | YES for code todos | Source files to read/modify. Relative to swarmai repo root. |
| `acceptance` | Recommended | How to know it's done. "Tests pass" is not enough — describe the behavior. |
| `design_docs` | If exists | Design docs or specs. Agent reads these before starting. |
| `commits` | If relevant | Related git commits for context (e.g. prior fix attempts). |
| `sessions` | If relevant | Chat session IDs where this was discussed. |
| `memory_refs` | If relevant | MEMORY.md entries (COEs, lessons, decisions) that apply. |
| `blockers` | If any | What's preventing progress. High priority if blockers exist. |
| `notes` | Optional | Free-form context that doesn't fit elsewhere. |

## Commands

### Add a todo (with full context)

```bash
python3 {SKILL_DIR}/scripts/todo_db.py add \
  --title "Implement append-message queue (Phase 1)" \
  --priority high \
  --source-type ai_detected \
  --description "Design doc v2 approved. Queue-based append: never stops stream, input always enabled, last message wins." \
  --files backend/core/session_unit.py desktop/src/hooks/useChatStreamingLifecycle.ts desktop/src/pages/ChatPage.tsx \
  --design-docs Knowledge/Designs/2026-03-21-append-message-design.md \
  --commits 3af6258b \
  --next-step "Extract _handle_agent_task_result into dispatch table in session_unit.py" \
  --acceptance "User can type new message during streaming. Last message wins. No content loss. Tests pass." \
  --notes "Kiro reviewed and approved. Queue-don't-kill is the model."
```

**Context args (all optional, but fill as many as applicable):**
- `--files PATH [PATH ...]` — Source files to read/modify
- `--design-docs PATH [PATH ...]` — Design docs for reference
- `--commits SHA [SHA ...]` — Related git commits
- `--sessions ID [ID ...]` — Related chat session IDs
- `--memory-refs REF [REF ...]` — MEMORY.md references
- `--next-step TEXT` — Concrete first action (ALWAYS provide this)
- `--acceptance TEXT` — Definition of done
- `--blockers TEXT [TEXT ...]` — Current blockers
- `--notes TEXT` — Free-form context
- `--linked-context JSON` — Raw JSON (overrides structured args)

**Other args:**
- `--title` / `-t` (required): Todo title
- `--priority` / `-p`: `high` | `medium` | `low` | `none` (default: none)
- `--description` / `-d`: Why this todo exists + background
- `--source-type`: `manual` | `chat` | `ai_detected` | `email` | `slack` | `meeting` | `integration`
- `--source`: Source reference (e.g. "Slack #general thread", "email from X")
- `--due-date`: ISO date string

### List todos

```bash
python3 {SKILL_DIR}/scripts/todo_db.py list           # compact: active only (pending + overdue)
python3 {SKILL_DIR}/scripts/todo_db.py list --all      # all statuses
python3 {SKILL_DIR}/scripts/todo_db.py list -s handled  # specific status
python3 {SKILL_DIR}/scripts/todo_db.py list -v          # full work packets for all items
```

Compact output includes the `next_step` hint:
```
  ⏳ 🔴 [a30ac3d8] Implement append-message queue → Extract _handle_agent_task_result...
  ⏳ 🟡 [78d62711] Review Kiro commits → Read recent git log and identify catch-vs-eliminate patterns
```

### Get a specific todo (full work packet)

```bash
python3 {SKILL_DIR}/scripts/todo_db.py get <todo_id_or_prefix>
```

Output is a **full work packet** — human-readable header + JSON payload:
```
======================================================================
TODO: Implement append-message queue (Phase 1)
======================================================================
  ID:       a30ac3d8-...
  Priority: 🔴 HIGH
  Status:   pending

  WHY: Design doc v2 approved. Queue-based append...

  NEXT STEP: Extract _handle_agent_task_result into dispatch table

  DONE WHEN: User can type new message during streaming...

  FILES:
    - backend/core/session_unit.py
    - desktop/src/hooks/useChatStreamingLifecycle.ts

  DESIGN DOCS:
    - Knowledge/Designs/2026-03-21-append-message-design.md
---JSON---
{ ... full structured data ... }
```

Short ID prefixes work (first 8 chars is usually enough).

### Update a todo (context merges)

```bash
python3 {SKILL_DIR}/scripts/todo_db.py update <todo_id> \
  --files backend/routers/chat.py \
  --next-step "Wire queue drain into SSE disconnect handler" \
  --blockers "Need to verify CancelledError path"
```

Context **merges intelligently**: list fields (files, commits, blockers) extend
without duplicates; scalar fields (next_step, acceptance) overwrite.

### Change status

```bash
python3 {SKILL_DIR}/scripts/todo_db.py status <todo_id> handled
python3 {SKILL_DIR}/scripts/todo_db.py status <todo_id> in_discussion
python3 {SKILL_DIR}/scripts/todo_db.py status <todo_id> cancelled
```

Valid statuses: `pending`, `overdue`, `in_discussion`, `handled`, `cancelled`, `deleted`

### Delete a todo

```bash
python3 {SKILL_DIR}/scripts/todo_db.py delete <todo_id>          # soft delete (status → deleted)
python3 {SKILL_DIR}/scripts/todo_db.py delete <todo_id> --hard    # hard delete (remove from DB)
```

## CRITICAL: Context Requirements by Source

### User chat todos (source_type=`chat`)

When user says "add a todo for X" during conversation:
1. **Title**: Clear, actionable (verb + object)
2. **Description**: WHY — capture the user's intent and any discussion context
3. **Files**: Any files mentioned or being worked on in current session
4. **Next step**: What the user would do first if they started now
5. **Acceptance**: What "done" looks like from the user's perspective
6. **Session**: Current session ID for back-reference
7. **Source**: `chat`

### Proactive todos (source_type=`ai_detected`)

Agent creates these from detected patterns:
1. **Title**: Specific and descriptive (not "follow up on X")
2. **Description**: WHY this was flagged — what triggered detection
3. **Files**: All files involved (trace the actual code path, don't guess)
4. **Design docs**: If there's a design doc for this work, include it
5. **Commits**: Recent related commits (find via `git log --oneline -5 -- <file>`)
6. **Memory refs**: Any MEMORY.md COEs, lessons, or decisions that apply
7. **Next step**: CONCRETE first action — "Read file X line Y" not "investigate"
8. **Acceptance**: Measurable — "tests pass", "no regression", specific behavior
9. **Blockers**: What's blocking, if anything

## Proactive ToDo Detection

### The Regret Test

Before creating any proactive todo, ask: **"Would the user regret NOT seeing this tomorrow?"**
If you're not confident the answer is yes → don't create it.

### Hard Limits

- **Max 2 proactive todos per session.** Scarcity forces prioritization. If you can
  only create 2, you'll pick the ones that actually matter. Todo fatigue kills the
  entire system — a noisy Radar gets ignored.
- **Always announce in chat.** Never create silently. Show the user what you added:
  ```
  📌 Added to Radar: "Test SSE disconnect with real network kill" (🔴 high)
  ```
  User can immediately say "remove that" if it's noise. Silent creation = shadow
  decisions = trust violation.

### When to Create (passes regret test)

1. **User commits to future action**: "I'll do X tomorrow", "need to follow up on Y",
   "let me think about Z" — people forget verbal commitments. Capture before it's lost.

2. **Blocker identified**: Work can't proceed because of a dependency, missing info,
   or approval needed. Without a visible todo, the blocker gets buried in chat history.

3. **COE follow-up**: Patch ships now, the real architectural fix is the todo. Without
   it, the fix never happens. (Evidence: 3 COEs in our history with this exact pattern.)

### When NOT to Create (fails regret test)

- **Vague observations** — "might want to look at X" is not a todo. If you can't fill
  `next_step` with a concrete action, it's not ready.
- **Things already tracked** — If it's in MEMORY.md Open Threads, don't duplicate as
  a Radar todo. Those are agent-level tracking; Radar todos are user-facing.
- **Minor improvements** — "could refactor this function" is not worth a todo slot.
  Note it in the session, move on.
- **More than 2 per session** — If you've already created 2, the 3rd one isn't
  important enough. Save it for next session or don't create it.

### Context Requirements for Proactive Todos

Every proactive todo MUST have:
- `--next-step` — concrete first action (not "investigate" — an actual step)
- `--files` — for code-related todos, all files involved
- `--description` — WHY this was flagged, what triggered detection
- `--source-type ai_detected`

Should have (when applicable):
- `--design-docs` — if a design doc exists for this work
- `--commits` — recent related commits
- `--memory-refs` — any MEMORY.md COEs, lessons, or decisions that apply
- `--acceptance` — measurable definition of done
- `--blockers` — what's blocking, if anything

## Integration Notes

- **DB location**: `~/.swarm-ai/data.db` (SQLite, WAL mode)
- **Workspace ID**: Always `swarmws` (single-workspace model)
- **Frontend refresh**: TodoSection polls on mount/tab-switch. New todos appear
  when user navigates to Radar or switches tabs.
- **No API needed**: Direct SQLite writes — avoids sandbox network restrictions.
- **Concurrent safety**: SQLite WAL mode handles concurrent reads from backend +
  writes from agent. Single-writer serialization is automatic.
- **Drag-to-chat**: When user drags a todo into chat, frontend sends the todo ID.
  Agent calls `get <id>` to load the full work packet, then executes.
