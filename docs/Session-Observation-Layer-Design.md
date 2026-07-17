---
title: "Session Observation Layer — Technical Design Document"
created: 2026-05-29
updated: 2026-05-29
author: XG (architecture), Swarm (synthesis)
status: shipped
audience: AWS Internal PEs, Technical Architects
tags: [observation, hooks, ring-buffer, session-recovery, ddd-cultivation, self-improving-loop]
toc: true
toc-depth: 3
numbersections: true
geometry: margin=1in
fontsize: 11pt
---

# Session Observation Layer

## Executive Summary

SwarmAI's Session Observation Layer is a real-time, per-session tool-call recording system that captures every tool invocation with intent, files touched, duration, and status. It ships as a PreToolUse/PostToolUse hook pair writing into an in-memory ring buffer (`ObservationRing`) --- no file I/O on the critical path, no threads, no locks.

Before this system, session crash recovery knew only aggregate statistics (tool call counts, last user message). DDD cultivation ran exclusively at session end --- a 4-hour delay between a knowledge-impacting edit and the DDD event. The Observation Layer solves both: it gives crash recovery structured context about WHAT was happening, and it fires DDD events in real-time as qualifying observations complete.

**Key metrics:**

| Metric | Value |
|--------|-------|
| Core module | `core/observation_ring.py`, ~120 lines |
| Ring capacity | 200 slots (~80KB memory) |
| Per-record latency | <0.1ms (no I/O, no locks) |
| Hook pair | `observation_recorder` (PreToolUse) + `observation_completer` (PostToolUse) |
| Consumers | 2 active (Checkpoint Writer, DDD Event Emitter) + 1 planned (ObservationMiner) |
| Crash tolerance | Buffer lost is acceptable --- checkpoint persists snapshots every 10 calls |

**Design principle:** Observe everything, persist selectively. The ring is a passive data structure --- consumers pull from it on their own schedule. Recording never blocks tool execution.

---

## 1. The Problem: Blind Recovery and Delayed Cultivation

Two structural weaknesses existed before the Observation Layer:

| Problem | Before | After |
|---------|--------|-------|
| **Crash recovery** | "14 Read, 8 Edit, 3 Bash" --- counts only, no context | "Edit on session_unit.py (fixing credential chain), Read on test_credentials.py (verifying fix)" |
| **DDD cultivation** | Fires at session end (4+ hours after events) | Fires within milliseconds of qualifying observations |
| **Pattern extraction** | Mining runs weekly from transcripts | Ring provides real-time data for future pattern extraction |

The fundamental insight: a tool-call count tells you HOW BUSY the session was. An observation record tells you WHAT THE AGENT WAS DOING. That distinction is the difference between useless crash recovery and actionable crash recovery.

---

## 2. Architecture

### 2.1 Component Overview

```
PreToolUse hook                PostToolUse hook
(observation_recorder)         (observation_completer)
       |                              |
       v                              v
  [Create Observation]       [Complete Observation]
       |                       (duration, status)
       v                              |
  ObservationRing <-------------------+
  (deque, 200 slots)
       |
       +--- Consumer 1: Checkpoint Writer (every 10 calls)
       |         -> enriches session_checkpoint.json
       |
       +--- Consumer 2: DDD Event Emitter (on qualifying observations)
       |         -> emits FILE_EDITED/CORRECTION events via EventDispatcher
       |
       +--- Consumer 3 (future): ObservationMiner
                  -> pattern extraction for Evolution pipeline
```

### 2.2 ObservationRing

**Implementation:** `core/observation_ring.py`, ~120 lines.

The ring is a fixed-size `collections.deque` with `maxlen=200`. When full, new records rotate out the oldest automatically. No explicit memory management required.

**Dataclass:**

```python
@dataclass(slots=True)
class Observation:
    ts: float                  # monotonic timestamp
    tool_name: str             # "Bash", "Read", "Edit", "Skill", etc.
    intent: str                # 1-line purpose (max 200 chars)
    files: list[str]           # file paths from input (max 5)
    completed: bool = False
    result_status: str = ""    # "success" | "error"
    duration_ms: int = 0
```

**Why `slots=True`:** Trims per-instance dict/attribute overhead. Each Observation averages ~400 bytes; at 200 slots the total ring overhead is ~80KB (see §3.3 and the `observation_ring.py` header comment).

**Why no locks:** The hook chain guarantees single-writer semantics. PreToolUse and PostToolUse execute sequentially in the same event loop --- there is never concurrent writing. Consumers read from the ring between hook invocations, also sequentially. Lock-free means zero contention overhead.

### 2.3 Hook Pair

| Hook | Event | Position | Behavior |
|------|-------|----------|----------|
| `observation_recorder` | PreToolUse | LAST | Creates Observation, appends to ring, returns immediately |
| `observation_completer` | PostToolUse | LAST | Finds matching Observation by ts, fills duration/status, triggers consumers |

**Position LAST** ensures all other hooks (security, permission, validation) execute first. The observation layer is purely passive --- it never modifies tool behavior, never blocks, never raises.

**Non-blocking guarantee:** Both hooks are synchronous Python with no I/O. Worst case: deque append + dataclass construction = <0.1ms. No await, no file write, no network call.

### 2.4 Consumers

Consumers are triggered from the `observation_completer` hook after marking an observation complete. They pull from the ring --- the ring never pushes.

#### Consumer 1: Checkpoint Writer

**Trigger:** Every 10 completed observations.

**Behavior:** Reads the last 10 observations from the ring and writes a structured summary into `session_checkpoint.json`:

```json
{
  "observation_window": [
    {"tool": "Edit", "intent": "fix credential chain", "file": "session_unit.py", "status": "success"},
    {"tool": "Read", "intent": "verify test passes", "file": "test_credentials.py", "status": "success"}
  ],
  "tool_counts": {"Edit": 8, "Read": 14, "Bash": 3},
  "last_intent": "fix credential chain",
  "last_files": ["session_unit.py"]
}
```

This enriches the existing checkpoint with structured context. On crash recovery, the agent knows not just that 25 tools were called, but that the last window of work was "fixing the credential chain in session_unit.py."

#### Consumer 2: DDD Event Emitter

**Trigger:** On qualifying observations (tool is Edit or Write, file matches a tracked project path).

**Behavior:** Emits `FILE_EDITED` or `CORRECTION` events to the existing `EventDispatcher`. The DDD cultivation system receives these events and can update domain documents in real-time rather than waiting for session-end extraction.

**Qualifying criteria:**
- Tool is `Edit`, `Write`, or `Bash` with file-modifying commands
- File path resolves to a tracked project directory
- Observation status is `success`

This replaces the previous model where DDD events only fired from the DailyActivityExtractionHook (at session end, 4+ hours after the actual edit).

#### Consumer 3: ObservationMiner

**Update (shipped):** The ObservationMiner has since landed as `backend/core/observation_miner.py` — the "Future/Step 3" framing below is historical. It extracts patterns from the ring:
- Repeated tool sequences (e.g., Read -> Edit -> Bash test cycle)
- Common file clusters (files that are always edited together)
- Intent patterns that correlate with corrections

---

## 3. Key Design Decisions

### 3.1 In-Memory Ring, NOT File Append

| Approach | Latency | Failure Mode | Memory |
|----------|---------|-------------|--------|
| **Ring buffer (chosen)** | <0.1ms | Lost on crash | ~80KB fixed |
| File append | 1-5ms | Disk full, permission errors | Unbounded |
| SQLite | 2-10ms | Lock contention, corruption | Unbounded |

The ring buffer wins on every dimension that matters for a per-tool-call recording system. The only cost is data loss on crash --- which is acceptable because the Checkpoint Writer persists snapshots every 10 calls. Worst case: lose the last 9 observations on crash.

### 3.2 PostToolUse Hook Reuses Existing Infrastructure

No new threads, timers, tasks, or event loops. The PostToolUse hook is the natural completion point for every tool call --- it already exists in the hook chain. Adding observation completion here is zero architectural cost.

### 3.3 Fixed 200 Slots --- Bounded Memory

200 slots at ~400 bytes each = ~80KB maximum. This bound is unconditional --- no configuration, no growth, no OOM risk. A typical session executes 50-200 tool calls, so the ring holds either the full session or the most recent window of a long session.

### 3.4 DDD Events Fire from PostToolUse via Existing Async Dispatcher

The `EventDispatcher` is already wired for async event delivery. DDD cultivation handlers are already registered. The Observation Layer simply emits events into the existing infrastructure rather than building a parallel notification system.

### 3.5 Buffer Lost on Crash is Acceptable

The checkpoint persists structured snapshots every 10 calls. If the process crashes between checkpoints, at most 9 observations are lost. The checkpoint itself contains enough context for meaningful crash recovery. Perfect recording is not the goal --- useful recovery is.

---

## 4. What the Observation Layer Enables

### 4.1 Crash Recovery with Context

Before: "Session crashed. 25 tools were called. Last user message: fix the credential chain."

After: "Session crashed during Edit of session_unit.py (intent: fix credential chain). Previous observations: Read test_credentials.py (verifying test), Bash pytest (3 tests passing). The agent was in a fix-verify cycle."

This transforms crash recovery from "start over" to "resume where you left off."

### 4.2 Real-Time DDD Cultivation

Before: DDD events fire from DailyActivityExtractionHook at session end. A session that runs 4 hours produces DDD updates only at hour 4.

After: Every qualifying Edit/Write triggers a DDD event within milliseconds. The DDD Auto-Approval Gate (Step 2) can process these events immediately, keeping domain documents current throughout the session rather than batch-updating at the end.

### 4.3 Pattern Data for Evolution Pipeline (Future)

The ring provides a structured stream of tool-call data that the ObservationMiner (Step 3) can analyze for patterns:
- Tool sequence patterns that indicate specific workflows
- File clustering patterns that reveal architectural coupling
- Intent patterns that correlate with user corrections
- Duration patterns that identify slow operations

---

## 5. Part of the Self-Improving Loop (3 Steps)

The Session Observation Layer is Step 1 of a 3-step design for closing the self-improving loop in real-time (rather than weekly via the Evolution pipeline):

| Step | Name | Function | Commit | Status |
|------|------|----------|--------|--------|
| **Step 1** | Session Observation Layer | CAPTURE --- record tool calls in real-time | `8ba70094` | Shipped |
| **Step 2** | DDD Auto-Approval Gate | JUDGE + ACT --- auto-approve mechanical DDD updates | `b9e10d9e` | Shipped |
| **Step 3** | Evolution Threshold Recalibration + Pattern Miner | LEARN --- lower thresholds, extract patterns from observations | `ac1021f6` (partial) | Partially shipped |

**How the steps compose:**

1. Step 1 captures every tool invocation with structured metadata
2. Step 2 receives DDD events from Step 1 and auto-approves mechanical updates (no human gate for obvious changes)
3. Step 3 mines patterns from the observation ring and feeds them into the Evolution pipeline, lowering the confidence threshold for well-evidenced improvements

Together, these three steps reduce the feedback loop from "weekly batch mining of transcripts" to "real-time observation -> immediate cultivation -> accelerated evolution."

---

## 6. Integration Points

| System | Integration | Direction |
|--------|-------------|-----------|
| Hook chain | PreToolUse/PostToolUse registration | Ring receives from hooks |
| session_checkpoint.json | Checkpoint Writer enriches with observation window | Ring pushes to checkpoint |
| EventDispatcher | DDD Event Emitter fires FILE_EDITED/CORRECTION | Ring pushes to dispatcher |
| DDD Auto-Approval Gate | Receives events from dispatcher | Downstream of ring |
| Resume context builder | Reads enriched checkpoint on --resume | Reads checkpoint output |
| Evolution pipeline | Future ObservationMiner feeds patterns | Planned |

---

## 7. Performance Characteristics

| Operation | Latency | Frequency |
|-----------|---------|-----------|
| Observation creation (PreToolUse) | <0.05ms | Every tool call |
| Observation completion (PostToolUse) | <0.1ms | Every tool call |
| Checkpoint write (every 10 calls) | 1-3ms (single file write) | ~5-20x per session |
| DDD event emit (qualifying observations) | <0.5ms (async dispatch) | ~5-30x per session |
| Ring memory | ~80KB fixed | Constant |

Total overhead per tool call: <0.1ms. For a session with 200 tool calls, total observation overhead is <20ms --- invisible against the seconds-to-minutes of actual tool execution.

---

## 8. Failure Modes

| Failure | Impact | Mitigation |
|---------|--------|-----------|
| Process crash | Ring contents lost | Checkpoint persists every 10 calls; max 9 lost |
| Checkpoint write fails | Stale checkpoint on next resume | Non-fatal; agent falls back to count-only recovery |
| DDD event dispatch fails | Delayed cultivation (session-end fallback) | Existing DailyActivityExtractionHook still fires at session end |
| Ring full | Oldest observations rotated out | By design; 200 slots covers most sessions entirely |
| Malformed observation | Consumer skips it | Dataclass construction validates types at creation |

No failure mode in the Observation Layer can affect tool execution. The hooks are LAST-position, non-blocking, and exception-isolated. A bug in observation recording is invisible to the user.

---

## 9. Status

**Shipped and in production.** Deployed in commit `8ba70094`. The Session Observation Layer has been running in production since deployment with:

- Zero reported latency impact on tool execution
- Checkpoint Writer enriching crash recovery context
- DDD Event Emitter firing real-time cultivation events
- Ring buffer operating within its 80KB memory bound

The DDD Auto-Approval Gate (Step 2, commit `b9e10d9e`) consumes events from this layer. Evolution threshold recalibration (Step 3, commit `ac1021f6`) has partially shipped with lowered confidence thresholds; the ObservationMiner component remains planned.

---

*Updated 2026-05-29. Covers the Session Observation Layer: ObservationRing, PreToolUse/PostToolUse hook pair, Checkpoint Writer, DDD Event Emitter, and integration with the Self-Improving Loop.*
