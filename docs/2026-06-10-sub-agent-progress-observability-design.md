---
title: "Sub-Agent Progress Observability — Tiered Awareness for Long-Running Agents"
created: 2026-06-10
updated: 2026-06-10
tags: [ux, sub-agent, progress, observability, streaming]
project: SwarmAI
status: draft
---

# Sub-Agent Progress Observability

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

**What we CAN observe from the backend:**
- The sub-agent runs in its own session (session_unit)
- That session has `tool_call_count` incrementing in real-time
- That session has state (STREAMING, IDLE, etc.)
- We know when it started (timestamp)

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
| **T2: Active notice** | 3 min elapsed | Yellow inline banner below spinner: `"Sub-agent running 3+ min — this is normal for research/design tasks."` + tool count if available | Stop |
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

### Data Flow

```
┌──────────────────────────────────────────────────┐
│ Frontend (ChatPage / MessageBubbles)             │
│                                                  │
│  SSE stream from parent session:                 │
│    ... tool_use(Agent) ...                       │
│    ← silence (SDK constraint) →                  │
│    ... tool_result ...                           │
│                                                  │
│  NEW: Poll backend for sub-agent progress:       │
│    GET /api/chat/sessions/{parent_id}/sub-agent-progress
│    Response: { active: true, elapsed_s: 187,     │
│               tool_calls: 38, label: "DDD..." }  │
│                                                  │
│  Display logic:                                  │
│    elapsed < 60  → T0 (spinner only)             │
│    elapsed < 180 → T1 (show timer)              │
│    elapsed < 480 → T2 (yellow banner)           │
│    elapsed < 900 → T3 (orange banner + options) │
│    elapsed >= 900 → T4 (red banner)             │
└──────────────────────────────────────────────────┘
         │
         │ Poll every 5s (only while sub-agent active)
         ▼
┌──────────────────────────────────────────────────┐
│ Backend (new endpoint)                           │
│                                                  │
│ GET /api/chat/sessions/{id}/sub-agent-progress   │
│                                                  │
│ Logic:                                           │
│   1. Check if session is in STREAMING state      │
│   2. Check if current tool is "Agent"            │
│   3. Find the sub-agent session (child)          │
│   4. Read child session's:                       │
│      - start_time → compute elapsed              │
│      - tool_call_count → progress indicator      │
│      - last_tool_name → "what it's doing now"    │
│      - label (from Agent tool invocation)        │
│   5. Return structured progress object           │
│                                                  │
│ If no active sub-agent → { active: false }       │
└──────────────────────────────────────────────────┘
         │
         │ Reads from
         ▼
┌──────────────────────────────────────────────────┐
│ SessionUnit (child session)                      │
│                                                  │
│ Already tracks:                                  │
│   - _streaming_start_time (when send() called)   │
│   - _tool_call_count (incremented per tool_use)  │
│   - state (STREAMING/IDLE/etc)                   │
│                                                  │
│ Need to add:                                     │
│   - parent_session_id (who spawned me)           │
│   - agent_label (description from Agent tool)    │
│   - last_tool_name (most recent tool invocation) │
└──────────────────────────────────────────────────┘
```

### Backend Changes

| File | Change | Lines |
|------|--------|-------|
| `backend/core/session_unit.py` | Add `parent_session_id`, `agent_label`, `last_tool_name` fields to SessionUnit | ~10 |
| `backend/core/session_router.py` | When spawning sub-agent, pass parent_id + label to child SessionUnit | ~5 |
| `backend/routers/chat.py` | New endpoint `GET /sessions/{id}/sub-agent-progress` | ~30 |
| **Total backend** | | **~45 lines** |

### Frontend Changes

| File | Change | Lines |
|------|--------|-------|
| `desktop/src/hooks/useSubAgentProgress.ts` | New hook: poll endpoint every 5s when streaming, compute tier | ~40 |
| `desktop/src/components/chat/SubAgentProgressBanner.tsx` | New component: tiered banner (T0-T4) with elapsed timer | ~60 |
| `desktop/src/pages/chat/components/MessageBubbles.tsx` | Render `SubAgentProgressBanner` when `isStreaming && subAgentActive` | ~5 |
| **Total frontend** | | **~105 lines** |

### API Contract

```typescript
// GET /api/chat/sessions/{session_id}/sub-agent-progress
// Response:
interface SubAgentProgress {
  active: boolean;            // Is a sub-agent currently running?
  elapsed_s: number;          // Seconds since sub-agent started
  tool_calls: number;         // How many tools the sub-agent has used
  last_tool: string | null;   // Most recent tool name ("Read", "Grep", "Bash")
  label: string | null;       // Agent description from parent's Agent tool call
  // Derived by frontend:
  // tier: 0-4 based on elapsed_s thresholds
}
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
| Sub-agent finishes between polls | Next poll returns `active: false` → banner disappears (max 5s stale) |
| Multiple nested sub-agents | Show outermost only (deepest child's elapsed is what matters to user) |
| Sub-agent spawns from a sub-agent | Track chain via `parent_session_id`. Show total elapsed from root. |
| Session switches while sub-agent runs | Poll is per-session. Switching tab stops the poll. Switching back resumes. |
| Backend restart during sub-agent | Sub-agent session dies → next poll: `active: false` → banner disappears |
| Sub-agent in channel (non-desktop) | No frontend → no banner. Channel has its own timeout (max_turns=15). |

---

## What This Does NOT Solve (Explicit Non-Goals)

| Non-goal | Why not | Future? |
|----------|---------|---------|
| **Real-time tool streaming from sub-agent** | SDK constraint. Would need Anthropic to change Agent tool architecture. | Wait for SDK evolution |
| **Sub-agent step-by-step progress** | Would need sub-agent to write to a progress file. High coupling, fragile. | Maybe U3 later if demand |
| **Automatic ETA prediction** | Would need historical duration data per task type. Over-engineering for now. | Maybe after 100+ datapoints |
| **Force timeout** | See "Why NOT Force Timeout" section above. | Never (by design) |

---

## Implementation Plan

| Order | Component | Effort | Dependency |
|-------|-----------|--------|-----------|
| 1 | SessionUnit fields (`parent_session_id`, `agent_label`, `last_tool_name`) | 20 min | None |
| 2 | SessionRouter: wire parent→child metadata on spawn | 15 min | Step 1 |
| 3 | New API endpoint `/sub-agent-progress` | 30 min | Step 2 |
| 4 | Frontend hook `useSubAgentProgress` | 30 min | Step 3 |
| 5 | Frontend component `SubAgentProgressBanner` | 45 min | Step 4 |
| 6 | Integration test (mock sub-agent, verify tiers) | 30 min | Step 5 |
| **Total** | | **~3 hours** | |

---

## Success Criteria

| Metric | Before | After |
|--------|--------|-------|
| User knows sub-agent is running (not stuck) | ❌ No signal after 5s | ✅ Timer + tool count visible |
| User knows when to intervene | ❌ Blind guess | ✅ Tiered guidance at 3/8/15 min |
| User can stop without guilt | ❌ "Maybe it's almost done?" | ✅ "8+ min, 55 tools called — if that's too long, stop" |
| False "stuck" perception | High (any > 2 min feels stuck) | Low (timer + tool count = "it's working") |

---

## Relationship to DDD Runtime Activation (F1-F5)

This design (U2) is **independent** of the DDD runtime fixes (F1-F5) but was discovered during the same session. Both are part of a "close the gap between design and production" effort:

- F1-F5: DDD knowledge system has dead code that needs activation
- U2: Sub-agent UX has a blind spot that needs observability

They can be built in parallel. U2 is pure UX (frontend + one endpoint), F1-F5 is pure backend (hooks + orchestrator).

---

*Author: XG | SwarmAI*
*Triggered by: Real incident 2026-06-10 — Plan agent ran 12:45 with zero user feedback*
