---
title: "Multi-Session Re-Architecture Design"
date: 2026-03-18
tags: [architecture, design, stability, multi-tab]
status: approved-v4-final
reviewers: [Swarm, Kiro]
---

# Multi-Session Re-Architecture Design (v4 — Final, Approved)

_v4: Kiro final approval. 5 items incorporated: TTL simplified to 2hr for all machines, WAITING_INPUT state added, interrupt() with 5s timeout fallback, queue dispatch safety, startup orphan reaper. **Design approved — ready for Phase 1 implementation.**_

_v3: Incorporated Kiro's 6-point review. QUEUED state (not error), RAM-adaptive TTL, env spawn fix, event-driven Radar, SDK interrupt() for stop, open questions closed._

_v2: Reconciled Swarm's Subprocess Pool with Kiro's SessionUnit feedback. Adopted SessionUnit ownership model with explicit concurrency cap. Dropped frozen state._

## The Requirement

> "Multi-Tab and multi chat session in parallel experience, required not impact each other, run stable."

Three non-negotiable properties:
1. **Parallel** — Two tabs can stream responses simultaneously
2. **Isolated** — Tab 1 crashing does not affect Tab 2
3. **Stable** — No daily patches. Predictable behavior under all conditions.

---

## Hard Constraints (SDK-Level, Cannot Change)

_Independently verified by both Swarm and Kiro via SDK source analysis._

| Constraint | Evidence | Impact |
|-----------|----------|--------|
| 1 ClaudeSDKClient = 1 subprocess | `anyio.open_process()` in transport layer | N parallel tabs = N subprocesses |
| 1 subprocess = 1 active query | Single `_write_lock` + single stdout reader | Cannot send 2 messages concurrently on same client |
| Resume = NEW subprocess | `--resume` flag passed to fresh `open_process()` | No process reuse across resume |
| MCPs are child processes of CLI | CLI spawns stdio MCPs as children | Cannot share MCPs across CLI instances |
| Response stream is NOT demuxed | Single `_message_send` stream; `AssistantMessage` and `StreamEvent` have NO `session_id` | Multiplexing two conversations on one CLI is **impossible** |

**Bottom line: True parallel requires separate subprocesses. Multiplexing was explored and ruled out by both independent analyses.**

---

## Resource Budget

**Per subprocess (measured):**

| Component | Memory | Processes |
|-----------|--------|-----------|
| Claude CLI | 200-400 MB | 1 |
| builder-mcp | 50-100 MB | 1 |
| aws-sentral-mcp | 50-100 MB | 1 |
| outlook-mcp | 30-50 MB | 1 |
| slack-mcp | 30-50 MB | 1 |
| taskei-mcp | 30-50 MB | 1 |
| **Subtotal per session** | **400-750 MB** | **6** |

**Decision: MAX 2 concurrent subprocesses. Physics constraint, not design choice.**

**MCP duplication is unsolvable at our layer** — the Claude CLI spawns its own MCPs internally via stdio. We can't share them across CLI instances.

---

## Architecture: SessionUnit Model

Each tab gets a **SessionUnit** — a self-contained state machine that owns its subprocess. Units are independent. No cross-session coordination. A global **concurrency cap (MAX=2)** prevents OOM.

### SessionUnit State Machine

**States:**
- `COLD` — No subprocess allocated. Tab exists as metadata only. Zero resource cost.
- `IDLE` — Subprocess alive, ready for queries. Warm cache, fast response.
- `STREAMING` — Actively processing a query. Protected from eviction.
- `WAITING_INPUT` — Subprocess alive, blocked waiting for user input (permission prompt or continue_with_answer). Protected from eviction like STREAMING.
- `DEAD` — Subprocess crashed or was killed. Cleaned up → transitions to COLD.

No FROZEN state. No SIGSTOP/SIGCONT. Alive (IDLE/STREAMING/WAITING_INPUT) or dead (COLD/DEAD).

### Key Design Rules

**Rule 1: Each SessionUnit is self-contained.**
No global PID tracking sets. No shared locks (except concurrency cap). When a unit dies, it cleans up its own resources.

**Rule 2: Concurrency cap = 2. Enforced at send time. Queue when full.**
If both units streaming and a third tab sends → queued (60s timeout), not error.

**Rule 3: STREAMING units are NEVER evicted.**
No killing mid-stream. This is the #1 stability rule.

**Rule 4: No SIGSTOP/SIGCONT. Alive or dead.**
Two states, not five tiers.

**Rule 5: Crash is local.**
Tab 1's subprocess dies → Tab 1 gets an error. Tab 2 unaffected. No cascade. No global cooldown.

**Rule 6: Env isolation via scoped spawn lock.**
`_spawn_lock` held only during subprocess creation. Released after subprocess has its own env copy.

**Rule 7: Stop uses SDK `interrupt()` with 5s timeout fallback to kill.**
Subprocess stays warm after Stop. Falls back to kill only if interrupt() hangs mid-tool-execution.

---

## Module Decomposition (4 Focused Modules, ~1,600 LOC total)

- `session_unit.py` (~300 LOC) — One tab's complete subprocess lifecycle
- `session_router.py` (~300 LOC) — Route requests, enforce concurrency cap
- `prompt_builder.py` (~500 LOC) — Build system prompts and SDK options (pure functions)
- `lifecycle_manager.py` (~400 LOC) — Background maintenance + hooks + startup orphan reaper

### Dependency Graph (no circular dependencies)
```
routers/chat.py → session_router.py ← lifecycle_manager.py
                  ↓       ↓                    ↓
            session_unit  prompt_builder   session_hooks
                  ↓
            ClaudeSDKClient (SDK)
```

---

## Global Services Layer

SessionUnits own subprocess lifecycle. Global Services own shared state. The boundary is strict — units call global services, never the reverse.

### Concurrency Fixes Required (Before Migration)

| Fix | Priority |
|-----|----------|
| Add fcntl.flock to EVOLUTION_CHANGELOG.jsonl | 🔴 P0 |
| Add 10s timeout to DailyActivity lock | 🔴 P0 |
| Serialize hook pipeline through single queue | 🔴 P0 |
| Add retry queue for failed locked_writes | 🟡 P1 |
| Move tab persistence to frontend localStorage | 🟡 P1 |
| Add content hash to L1 cache | 🔵 P2 |

---

## Migration Plan

- **Phase 1** (1-2 days): Extract modules from agent_manager.py. Zero behavior change.
- **Phase 2** (1-2 days): Simplify lifecycle. Remove SIGSTOP, 5-tier cleanup, global PID tracking.
- **Phase 3** (2-3 days): Frontend single store (zustand). Remove dual-state sync.
- **Phase 4** (1 day): Lazy MCP loading. Default builder-mcp only.

---

## Design Principles

1. Accept the constraint. SDK requires 1 subprocess per conversation.
2. Own your lifecycle. Each SessionUnit is self-contained.
3. Alive or dead. No freeze/thaw.
4. Crash is local. Unit 1 dying never affects Unit 2.
5. Cap, don't track. One integer (MAX_CONCURRENT=2) replaces 3 PID tracking sets.
6. Frontend renders, backend decides. Retry, stall detection — all backend.
7. One store, one truth. No dual-state synchronization.
8. Less code = fewer bugs. 71% reduction (9,441 → ~2,700 LOC).
9. Queue, don't reject. Users expect all tabs to work.
10. Interrupt, don't kill. Stop button uses SDK interrupt() — subprocess stays warm.

---

## Resolved Questions

| # | Question | Decision |
|---|----------|----------|
| 1 | Queue UX when both slots busy | QUEUED with 60s timeout → error |
| 2 | Session TTL | 2hr (7200s) for all machines |
| 3 | MCP hot-swap | Restart (reclaim + respawn) |
| 4 | Tab limit | Keep 6. Cold tabs are free. |
