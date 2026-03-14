---
title: "Proactive Intelligence — Design Specification"
date: 2026-03-14
author: Swarm + XG
status: active
version: "1.0"
tags: [proactive-intelligence, system-prompt, session-briefing, architecture]
---

# Proactive Intelligence — Design Specification

## Problem Statement

SwarmAI is reactive — the agent waits for the user to ask what's pending, what happened last session, and what to work on. This wastes 2-3 round-trips at every session start just to get oriented. The agent has all the data (MEMORY.md, DailyActivity, EVOLUTION.md) but doesn't use it proactively.

## Vision

Make SwarmAI *aware* at session start. The agent should know what's blocking, what's stale, what was left unfinished, and what the user should focus on — without being asked.

## E2E User Flow

### Before (reactive)
```
User: Hi Swarm, I'm back
Swarm: Welcome back! What do you want to work on?
User: What were we doing last time?
Swarm: Let me check... [reads files] ... we had a tab-switch bug, some MCP fixes...
User: Right, and what's still pending?
Swarm: [reads more files] ... 3 things need rebuild...
```
3 round-trips to orient.

### After (proactive)
```
User: Hi Swarm, I'm back
Swarm: Welcome back, XG. Here's where things stand:
  P0: Tab-switch streaming bug still open (reported 4x, no durable fix).
  3 fixes pending rebuild verification (MCP, streaming, sandbox).
  Last session: "Move forward on Proactive Intelligence POC."
  What do you want to tackle first?
```
Zero round-trips. Agent already oriented from turn 1.

## Data Flow

```
 MEMORY.md ──────────┐
   (Open Threads)     │
                      ├──▶ build_session_briefing() ──▶ system prompt
 DailyActivity/ ─────┤       (pure parsing, <1ms)
   (**Next:** lines)  │
                      │
 Pattern detection ───┘
   (repeat bugs, stale threads, rebuild debt)
```

### Injection Point in System Prompt Assembly

```
_build_system_prompt()
  1. Context files (P0-P10)          ~11,000 tokens
  2. Bootstrap.md (if exists)
  3. DailyActivity (last 2 days)      ~2,000 tokens
  4. ** Session Briefing **            ~185 tokens   <-- HERE
  5. Resume Context (if resumed)       ~350 tokens
```

Position rationale: after raw history data, before conversation resume. Agent sees the briefing as synthesized awareness, not raw data.

## Multi-Tab Safety

`build_session_briefing()` is **read-only** — no writes, no shared state, no locks.

```
Tab A: _build_system_prompt() → reads files → briefing A
Tab B: _build_system_prompt() → reads files → briefing B (identical content)
```

Both tabs get the same briefing. Both agents independently orient. No race condition.

**V2 consideration:** Deduplicate briefing across concurrent tabs — only inject on first session of the day, or mark "briefing already delivered" in a lightweight flag file.

## Implementation Levels

### Level 0: Session Briefing (MVP) — SHIPPED

Pure text parsing of MEMORY.md + DailyActivity. No LLM call.

**Components:**
- `_parse_open_threads()` — extracts P0/P1/P2 with report counts + status
- `_parse_continue_hints()` — pulls `**Next:**` lines, filters stale "Ongoing:"
- `_detect_patterns()` — repeat offenders (3x+), pending rebuilds, COEs, uncommitted work
- `build_session_briefing()` — assembles into ~185 token `## Session Briefing`

**Properties:**
- Execution: <1ms (pure regex)
- Never blocks agent startup (try/except wrapped)
- Token budget: ~185 typical, hard cap ~500
- 19 tests, all passing

**Files:**
- `backend/core/proactive_intelligence.py`
- `backend/tests/test_proactive_intelligence.py`
- Injection: `backend/core/agent_manager.py` line ~1061

**Sample output:**
```
## Session Briefing
**Blockers:**
  - BLOCKING: Tab switching loses streaming content (4x)
**Signals:**
  - "Tab switching loses streaming content" reported 4x -- needs durable fix
  - 3 fix(es) pending rebuild verification
  - 3 COE(s) still under investigation
  - Uncommitted work detected in Open Threads
**Continue from last session:**
  - Rebuild and verify tab switching works correctly with concurrent streaming.
  - Move forward on Proactive Intelligence POC.
**Also pending (P1):**
  - MCP servers not connecting in app (2x)
  - Streaming feels non-streaming
  - Sandbox network blocked
```

### Level 1: Temporal Awareness — SHIPPED

Added time-based signals. Still no LLM call.

| Signal | Logic | Example |
|---|---|---|
| Session gap | No session >24h | "2 days since last session" |
| Stale P0 | P0 open >3 days | "Tab-switch bug open 3 days — escalate?" |
| Rebuild debt | Fixes accumulate without rebuild | "5 unverified fixes — rebuild first" |
| Time-of-day | Morning vs evening | Morning: full briefing. Evening: "wrap up" nudge |
| First session of day | Date comparison | Full briefing vs compact "still working on X" |

**Implemented:** `_detect_temporal_signals()` added to `proactive_intelligence.py`. 6 new tests (25 total). Signals: session gap, first session of day, stale P0.

### Level 2: Actionable Suggestions — SHIPPED

Deterministic scoring engine that ranks all Open Threads + continue hints by
priority, staleness, report frequency, blocking relationships, and momentum.
Top items shown as "Suggested focus" with template-generated reasoning.

**Components (added to `proactive_intelligence.py`):**
- `ScoredItem` dataclass — candidate action with computed score
- `_score_item()` — deterministic scoring: priority weight + staleness + frequency + blocking + momentum
- `_detect_blocking()` — cross-reference threads for blocking relationships
- `_build_suggestions()` — merge threads + hints, score, rank
- `_generate_reasoning()` — template-based "why this order" explanation
- `_format_suggestions()` — format top-N focus + background sections

**Scoring weights:**
- Priority: P0=100, P1=40, P2=10
- Staleness: +5/day (cap 30)
- Frequency: +8/report (cap 40)
- Blocking bonus: +30
- Momentum: +15 (from continue hint)

**Properties:**
- No LLM, no state file — pure deterministic like L0/L1
- Replaces raw P0/P1 listing with ranked suggestions
- Falls back to L0+L1 format on any failure
- 51 tests, all passing

**Sample output:**
```
## Session Briefing
**Suggested focus for this session:**
  1. Tab switching loses streaming content (4x)
  2. MCP servers not connecting in app (3x)

**Why this order:** Tab switching: reported 4x. MCP: reported 3x.
**Also in the background:**
  - Investigate tab switch streaming...
  - Move forward on Proactive Intelligence POC...
```

**Design doc:** `docs/specs/proactive-intelligence-L2.md`

### Level 3: Cross-Session Learning

Track suggestions vs. actual user behavior. Learn preferences.

```
Suggested: "rebuild app"
User did: worked on Proactive Intelligence (3 sessions in a row)
Learning: User prefers feature work over maintenance. Adjust threshold.
```

**Estimated effort:** New `proactive_learning.py` + EVOLUTION.md integration. ~4-6 hours.

### Level 4: Proactive Interrupts

Mid-session signals via SSE events. Non-blocking toast/banner in UI.

| Trigger | Signal |
|---|---|
| Context >70% | "Running low — wrap up or compact" |
| Same error 3x | "Step back and rethink?" |
| Long session >2h | "Good stopping point?" |
| External event | "Meeting in 30min" |

**Estimated effort:** Background monitor + SSE events + frontend renderer. ~6-8 hours.

### Level 5: Autonomous Preparation

Agent works *before* user arrives. Scheduled background sessions.

```
[User opens app in morning]
Swarm: While you were away, I:
  - Ran the rebuild (tests pass)
  - Verified MCP connects
  - Drafted Session State Machine design
```

**Estimated effort:** Scheduler + safety guardrails + approved-actions config. ~2-3 days.

## Architecture Across Levels

```
Level 0-1: Pure parsing           | proactive_intelligence.py (existing)
Level 2:   Rule engine            | proactive_suggestions.py (new)
Level 3:   Learning loop          | proactive_learning.py (new) + EVOLUTION.md
Level 4:   Background monitor     | proactive_monitor.py (new) + SSE events
Level 5:   Autonomous agent       | proactive_executor.py (new) + scheduler

All levels share:                 | MEMORY.md, DailyActivity, EVOLUTION.md
                                  | (existing infrastructure, no new storage)
```

## Iteration Plan

| Iteration | Scope | Dependency | Verify by |
|---|---|---|---|
| 0 (done) | MVP: static briefing | None | Unit tests (19 pass) |
| 1 (done) | Temporal signals | Rebuild + app test | Unit tests (25 pass) |
| 2 (done) | Actionable suggestions | Level 1 verified | 51 tests pass, real workspace verified |
| 3 | Cross-session learning | Level 2 stable | 1 week of data |
| 4 | Mid-session interrupts | Frontend SSE handler | E2E in app |
| 5 | Autonomous prep | Safety review by XG | Controlled pilot |

## Process

For Level 1+: design doc update first, XG review, then implement. Each level is a small, testable increment. No big-bang changes.

## Changelog

- 2026-03-14: v1.0 — Initial spec. Level 0 (MVP) shipped. Levels 1-5 designed.
- 2026-03-14: v1.1 — Level 1 (Temporal Awareness) shipped. 25 tests total.
- 2026-03-14: v1.2 — Level 2 (Actionable Suggestions) shipped. 51 tests total. Design doc: proactive-intelligence-L2.md.
