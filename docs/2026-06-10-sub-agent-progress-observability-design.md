---
title: "Sub-Agent Progress Observability — Tiered Awareness for Long-Running Agents"
created: 2026-06-10
updated: 2026-06-10
tags: [ux, sub-agent, progress, observability, streaming]
project: SwarmAI
status: approved
---

# Sub-Agent Progress Observability

> **Superseded (implementation note):** This v1 design specifies a single
> `_active_agent_tool: dict | None` field and assumes only one Agent tool can be
> active at a time. Shipped reality is parallel-agent-capable: `session_unit.py`
> uses `_active_agent_tools: dict[str, dict]` keyed by tool_use_id (parallel
> reviewers — e.g. Gate 2 spawns several concurrently), and the
> `GET /sessions/{id}/sub-agent-progress` endpoint (`backend/routers/chat.py`)
> returns an extra `count` field alongside `elapsed_s` (oldest) and `label`
> (newest). Read the field/edge-case text below as the historical single-agent v1.

## The Problem You Will Never See Until It Bites You

### What Happened (2026-06-10, real incident)

User spawned a Plan agent via the `Agent` tool to design a DDD runtime activation system. The agent:
- Read 55 files across the codebase
- Ran for 12 minutes 45 seconds
- Produced excellent output

**But during those 12:45:**
- The user saw ONLY a spinner and "Running: Read..."
- No progress signal — how many files read? What step is it on?
- No ETA — is this 50% done or 5% done?
- No stall detection — is it stuck or working?
- No guidance — should I wait or stop?
- The ONLY option was to manually click Stop (losing all work) or wait blindly

The user's exact words: **"一直在progressing, 也不停 也不知道到哪一步了 也没timeout, 也没提醒 根本不知道在干啥 直到我stop, 这种问题很严重 用户预期没法管理"**

### Why This Is a P1 UX Problem

| Impact | Detail |
|--------|--------|
| **Trust erosion** | User can't distinguish "working well" from "stuck" — same visual |
| **Forced choice without info** | Stop = lose work. Wait = might wait forever. No data to decide. |
| **Anxiety scales with duration** | At 2 min: "probably fine." At 8 min: "is it broken?" At 12 min: "I'm stopping this." |
| **Compounding frequency** | Plan/research/code-review agents routinely run 3-15 min. This happens multiple times daily. |

### The SDK Constraint (Why This Is Hard)

```
Claude SDK CLI architecture:
  Parent agent calls Agent tool
    → SDK spawns sub-agent as separate CLI process
    → Parent stream PAUSES entirely (no events emitted)
    → Sub-agent runs to completion
    → tool_result returns to parent
    → Parent resumes streaming

During the pause: ZERO intermediate events reach the frontend.
- No tool_use events from sub-agent
- No text_delta events
- No progress callbacks
- Just... silence
```

This is documented in SwarmAI IMPROVEMENT.md as a known SDK constraint (`[constraint] Sub-agent events not observable from parent stream`). It is NOT a bug — it's the Claude Code architecture.

**What we CAN observe from the backend (verified 2026-06-10):**
- Parent SessionUnit is in STREAMING state
- The last ToolUseBlock received has `name === "Agent"` (we know a sub-agent is running)
- The ToolUseBlock's `input.description` field = the agent label
- The timestamp when that ToolUseBlock arrived → elapsed time
- The parent's `_last_event_time` stops updating (SDK stream paused)

**What we CANNOT observe (SDK architectural constraint):**
- Sub-agent does NOT get its own SessionUnit — it runs inside the SDK CLI subprocess
- No `tool_call_count` from sub-agent reaches the backend
- No intermediate events (tool_use, text_delta) from sub-agent are visible
- The backend only sees: ToolUseBlock(Agent) → silence → tool_result

---

## Design: Tiered Awareness (Not Forced Timeout)

### Core Principle: Inform, Don't Kill

**Timeout is the wrong abstraction.** The user didn't want the agent to stop — they wanted to KNOW what was happening. The problem is information asymmetry, not duration.

A 12-minute research agent producing excellent output is GOOD. Force-killing it at 5 min would be worse than no indicator at all (lost work > no progress info).

### Tiered UX Response

| Tier | Trigger | Visual | User Options |
|------|---------|--------|-------------|
| **T0: Normal** | 0-60s | Standard spinner + "Agent thinking..." | Stop button (existing) |
| **T1: Duration notice** | 60s elapsed | Subtle elapsed timer appears: `"1:03"` in muted text next to spinner | Stop |
| **T2: Active notice** | 3 min elapsed | Yellow inline banner below spinner: `"Sub-agent running 3+ min — this is normal for research/design tasks."` | Stop |
| **T3: Stall warning** | 8 min elapsed | Orange banner: `"8+ minutes. May be doing extensive work or stuck."` | **[Stop]** + **[Keep waiting]** |
| **T4: Soft ceiling** | 15 min elapsed | Red banner: `"15 min without response. Consider stopping."` | **[Stop now]** (prominent) + [Keep waiting] (secondary) |

### Why These Thresholds

| Threshold | Rationale | Data |
|-----------|-----------|------|
| **60s** | Most simple sub-agents (grep, single-file read) complete < 60s. If still running, it's a "deep" task. | Observed: quick searches 10-30s, code reviews 60-120s |
| **3 min** | Matches our existing `AUTO_RECOVER_STALL_THRESHOLD = 180s`. Beyond this = longer than typical. | 80% of sub-agents complete < 3 min |
| **8 min** | Beyond any "normal" code review or analysis. Either genuinely deep research OR stuck. | Plan agents routinely 5-12 min. Design agents 8-15 min. |
| **15 min** | Practical upper bound. Even the deepest research rarely exceeds this productively. | Diminishing returns beyond 15 min (context saturation) |

### Why NOT Force Timeout

| Argument for timeout | Counter-argument |
|---------------------|-----------------|
| "Prevents runaway agents" | `max_turns` already prevents infinite loops. Duration ≠ runaway. |
| "Saves user time" | Killing a 12-min agent that was 90% done WASTES more time than waiting 3 more min. |
| "Consistent UX" | A forced timeout that sometimes kills good work is INCONSISTENT UX — sometimes helpful, sometimes destructive. |
| "Resource management" | Sub-agents use the same session slot. If user needs the slot, they can manually stop. |

**The right default is: inform + empower, never force.** User has the Stop button at every tier. The system's job is to give them enough info to decide.

---

## Technical Architecture

### Key Insight: Observe Parent, Not Child

The sub-agent is a **black box** to our backend (runs inside SDK CLI subprocess). But we don't need to observe the child — we can detect the sub-agent's existence entirely from the **parent session's event stream**:

1. Parent receives `ToolUseBlock(name="Agent", input={description: "..."})` → sub-agent started
2. Parent's SSE stream goes silent (no more events) → sub-agent is running
3. Parent receives `tool_result` for that block → sub-agent completed

We track the **gap** between step 1 and step 3. That's all we need for tiered UX.

### Data Flow

```
┌──────────────────────────────────────────────────┐
│ Frontend (ChatPage / MessageBubbles)             │
│                                                  │
│  SSE stream from parent session:                 │
│    ... tool_use(name="Agent") ...                │
│    ← silence (SDK constraint) →                  │
│    ... tool_result ...                           │
│                                                  │
│  NEW: Poll backend for sub-agent progress:       │
│    GET /api/chat/sessions/{id}/sub-agent-progress│
│    Response: { active: true, elapsed_s: 187,     │
│               label: "DDD runtime design..." }   │
│                                                  │
│  Display logic (frontend-owned):                 │
│    elapsed < 60  → T0 (spinner only)             │
│    elapsed < 180 → T1 (show timer)              │
│    elapsed < 480 → T2 (yellow banner)           │
│    elapsed < 900 → T3 (orange banner + options) │
│    elapsed >= 900 → T4 (red banner)             │
└──────────────────────────────────────────────────┘
         │
         │ Poll every 5s (only while streaming)
         ▼
┌──────────────────────────────────────────────────┐
│ Backend (new endpoint)                           │
│                                                  │
│ GET /api/chat/sessions/{id}/sub-agent-progress   │
│                                                  │
│ Logic:                                           │
│   1. Get SessionUnit for this session            │
│   2. Check if _active_agent_tool is set:         │
│      - _active_agent_tool.start_time → elapsed   │
│      - _active_agent_tool.label → description    │
│   3. Return { active, elapsed_s, label }         │
│                                                  │
│ If no active Agent tool → { active: false }      │
└──────────────────────────────────────────────────┘
         │
         │ Reads from
         ▼
┌──────────────────────────────────────────────────┐
│ SessionUnit (parent session — already exists)    │
│                                                  │
│ NEW field (lightweight):                         │
│   _active_agent_tool: {                          │
│     tool_use_id: str,   # block.id              │
│     label: str,         # input.description      │
│     start_time: float,  # time.time()           │
│   } | None                                       │
│                                                  │
│ Set when: ToolUseBlock with name="Agent" arrives │
│ Cleared when: matching tool_result arrives       │
│                                                  │
│ Already tracks:                                  │
│   - state (STREAMING/IDLE/etc) — no change       │
│   - _last_event_time — no change                 │
└──────────────────────────────────────────────────┘
```

### Backend Changes

| File | Change | Lines |
|------|--------|-------|
| `backend/core/session_unit.py` | Add `_active_agent_tool: dict | None` field. Set on ToolUseBlock(Agent), clear on matching tool_result. | ~15 |
| `backend/routers/chat.py` | New endpoint `GET /sessions/{id}/sub-agent-progress` — reads `_active_agent_tool` from SessionUnit | ~25 |
| **Total backend** | | **~40 lines** |

### Frontend Changes

| File | Change | Lines |
|------|--------|-------|
| `desktop/src/hooks/useSubAgentProgress.ts` | New hook: poll endpoint every 5s when `isStreaming`, compute tier from elapsed_s | ~40 |
| `desktop/src/components/chat/SubAgentProgressBanner.tsx` | New component: tiered banner (T0-T4) with elapsed timer + label | ~60 |
| `desktop/src/pages/chat/components/MessageBubbles.tsx` | Render `SubAgentProgressBanner` when `isStreaming && subAgentActive` | ~5 |
| `desktop/src/services/chat.ts` | New API function `getSubAgentProgress(sessionId)` | ~10 |
| **Total frontend** | | **~115 lines** |

### API Contract

```typescript
// GET /api/chat/sessions/{session_id}/sub-agent-progress
// Response:
interface SubAgentProgress {
  active: boolean;            // Is a sub-agent currently running in this session?
  elapsed_s: number;          // Seconds since Agent tool_use block arrived (0 if not active)
  label: string | null;       // Agent description from tool_use input.description
  // Frontend derives:
  // tier: 0-4 based on elapsed_s thresholds
}

// Backend Python model:
// class SubAgentProgressResponse(BaseModel):
//     active: bool
//     elapsed_s: float = 0
//     label: str | None = None
```

### Polling Strategy

- **When to poll:** Only when `isStreaming === true` AND parent session has been streaming > 5s (don't poll for quick tool calls)
- **Interval:** Every 5 seconds (lightweight — single DB read, no computation)
- **Stop condition:** `active: false` response → stop polling, hide banner
- **Backoff:** None needed (5s interval is already conservative)

---

## Edge Cases

| Case | Handling |
|------|---------|
| Sub-agent finishes between polls | tool_result clears `_active_agent_tool` → next poll returns `active: false` → banner disappears (max 5s stale) |
| Nested sub-agents (agent spawns agent) | We only see the parent's Agent tool_use. Nested agents are invisible — but elapsed time still accumulates correctly from parent's perspective. |
| Session switches while sub-agent runs | Poll is per-session. Switching tab stops the poll for that tab. Switching back resumes. |
| Backend restart during sub-agent | SessionUnit recreated → `_active_agent_tool` is None → poll returns `active: false`. SDK subprocess also dies, so parent will error/resume. |
| Sub-agent in channel (non-desktop) | No frontend → no banner. Channel has its own timeout (max_turns=15). |
| Agent tool_use arrives but NOT name="Agent" | Only `name === "Agent"` triggers tracking. Regular tools (Read, Bash) don't set `_active_agent_tool`. |
| Concurrent Agent tools | v1 assumed "theoretically impossible" (SDK serializes). **Superseded:** parallel Agent tools DO occur (e.g. Gate 2 parallel reviewers), so the shipped impl tracks a `dict[str, dict]` keyed by tool_use_id and the endpoint returns a `count` of active sub-agents. |

---

## What This Does NOT Solve (Explicit Non-Goals)

| Non-goal | Why not | Future? |
|----------|---------|---------|
| **Real-time tool count from sub-agent** | Sub-agent runs inside SDK subprocess — backend has zero visibility into its internal tool calls. Would need Anthropic to expose sub-agent events. | Wait for SDK evolution |
| **Sub-agent step-by-step progress** | Would need sub-agent to write to a shared progress file. High coupling, fragile, and SDK doesn't support it. | Maybe if SDK adds progress callbacks |
| **Automatic ETA prediction** | Would need historical duration data per task type. Over-engineering for now. | Maybe after 100+ datapoints |
| **Force timeout** | See "Why NOT Force Timeout" section above. | Never (by design) |

---

## Implementation Plan

| Order | Component | Effort | Dependency |
|-------|-----------|--------|-----------|
| 1 | SessionUnit: add `_active_agent_tool` field + set/clear logic in event processing | 20 min | None |
| 2 | New API endpoint `GET /sessions/{id}/sub-agent-progress` in `chat.py` | 25 min | Step 1 |
| 3 | Frontend service: `getSubAgentProgress()` in `chat.ts` | 10 min | Step 2 |
| 4 | Frontend hook `useSubAgentProgress.ts` (poll + tier computation) | 30 min | Step 3 |
| 5 | Frontend component `SubAgentProgressBanner.tsx` (tiered banners) | 45 min | Step 4 |
| 6 | Integration in `MessageBubbles.tsx` | 10 min | Step 5 |
| 7 | Tests: backend endpoint + frontend hook + component | 40 min | Step 6 |
| **Total** | | **~3 hours** | |

---

## Success Criteria

| Metric | Before | After |
|--------|--------|-------|
| User knows sub-agent is running (not stuck) | ❌ No signal after 5s | ✅ Elapsed timer + label visible |
| User knows when to intervene | ❌ Blind guess | ✅ Tiered guidance at 3/8/15 min |
| User can stop without guilt | ❌ "Maybe it's almost done?" | ✅ "8+ min elapsed — if that's too long for this task, stop" |
| False "stuck" perception | High (any > 2 min feels stuck) | Low (timer = "it's actively running, time is progressing") |

---

## Relationship to DDD Runtime Activation (F1-F5)

This design (U2) is **independent** of the DDD runtime fixes (F1-F5) but was discovered during the same session. Both are part of a "close the gap between design and production" effort:

- F1-F5: DDD knowledge system has dead code that needs activation
- U2: Sub-agent UX has a blind spot that needs observability

They can be built in parallel. U2 is pure UX (frontend + one endpoint), F1-F5 is pure backend (hooks + orchestrator).

---

*Author: XG | SwarmAI*
*Triggered by: Real incident 2026-06-10 — Plan agent ran 12:45 with zero user feedback*
