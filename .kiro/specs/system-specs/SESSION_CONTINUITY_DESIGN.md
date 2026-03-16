# Seamless Session Continuity — System Design

> **Status:** Implemented (2026-03-16)
> **Owner:** SwarmAI / agent_manager.py
> **Decision Date:** 2026-03-16
> **Related:** chat-session-stability-fix, COE 2026-03-15

## Problem Statement

A user sends a message, goes to lunch or a meeting, comes back 1-2 hours later, and sends another message. They expect it to just work — same conversation, full context, instant response. No warnings, no errors, no "Thinking... 180s".

The original design killed the Claude CLI subprocess after 5 minutes of idle (`SUBPROCESS_IDLE_SECONDS = 300`), then spent 10-45 seconds cold-starting a new one with degraded context (only 20 messages / 6K tokens survived). This made every coffee break, Slack check, or bathroom break trigger a visible degradation.

## Design Principle

**The user should never know about subprocess lifecycle.** The implementation detail of managing CLI processes must be invisible. The system must feel like a single continuous conversation that survives hours of idle time.

## Solution: 4-Tier Subprocess Lifecycle

Instead of a binary alive/dead model, we use a tiered lifecycle that trades resource intensity for resume speed:

```
HOT (0-5min idle)
 │  Subprocess running, full context, instant response (~2-3s)
 │
 ▼ SIGSTOP
FROZEN (5min - 2hr idle)
 │  Subprocess suspended (zero CPU), macOS pages out memory naturally
 │  On next message: SIGCONT thaws instantly (<100ms), full context
 │
 ▼ SIGKILL
DEAD (2hr+ idle, or memory pressure eviction)
 │  Subprocess killed, session metadata preserved
 │  On next message: PATH A cold-start with context injection (~10-15s)
 │  Watchdog: 45s timeout (tight, not the 180s used for active sessions)
 │
 ▼ Full cleanup
EVICTED (8hr+ idle)
    Session removed from memory, hooks fired, DailyActivity extracted
```

### Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `SUBPROCESS_IDLE_SECONDS` | 5 min | Freeze threshold (SIGSTOP) |
| `SUBPROCESS_KILL_SECONDS` | 2 hr | Kill threshold (resources fully reclaimed) |
| `SESSION_TTL_SECONDS` | 8 hr | Full session cleanup |
| `ACTIVITY_IDLE_SECONDS` | 30 min | DailyActivity extraction |
| `MAX_CONCURRENT_SUBPROCESSES` | 2 | Cap-based eviction trigger |
| `COLD_START_TIMEOUT` | 45 s | Watchdog for fresh subprocess on resume |
| `WATCHDOG_BASE_TIMEOUT` | 180 s | Watchdog for active sessions |

### User Experience by Idle Duration

| Come back after... | What happens | User sees |
|--------------------|-------------|-----------|
| 1 min | HOT — subprocess running | Instant response |
| 10 min | FROZEN — SIGCONT thaw | Instant response |
| 1 hr | FROZEN — SIGCONT thaw | Instant response |
| 2 hr | FROZEN — SIGCONT thaw | Instant response |
| 3 hr | DEAD — PATH A cold-start | "Resuming session..." (~10-15s) |
| 6 hr | DEAD — PATH A cold-start | "Resuming session..." (~10-15s) |
| 9 hr+ | EVICTED — fresh session | Normal new session |

## Architecture Details

### SIGSTOP/SIGCONT Freeze/Thaw

**File:** `agent_manager.py` — `_freeze_subprocess()`, `_thaw_subprocess()`

The subprocess is a Node.js Claude CLI process. `SIGSTOP`/`SIGCONT` is OS-level — the process doesn't know it was frozen. Key safety checks:

1. **Existence check** (`os.kill(pid, 0)`) before SIGSTOP/SIGCONT — if the process died while frozen, clear references and fall through to PATH A
2. **`is_frozen` flag** on session info — prevents double-freeze and ensures thaw happens before kill or disconnect
3. **`is_streaming` guard** — the cleanup loop skips sessions with active SSE streams, preventing mid-stream kills
4. **Early `is_streaming=True` on thaw** — set immediately in `_get_active_client` after successful SIGCONT, before returning the client. Closes the race window between thaw and the caller setting `is_streaming=True` in `_execute_on_session`. Without this, the cleanup loop's Tier 1.5 kill check could see the session as thawed + idle and kill it before the stream starts.

**Why SIGSTOP works for Claude CLI:**
- CLI reconnects to Bedrock per-request (no persistent TCP connection to stale)
- The `--resume` session file on disk stays valid
- Node.js event loop resumes cleanly on SIGCONT
- Worst case: first message after thaw takes an extra second for TCP handshake

### Cold-Start Resume (PATH A)

**File:** `agent_manager.py` — `_execute_on_session_inner()`, `_run_query_on_client()`

When the subprocess is dead (2hr+ idle, memory pressure eviction, or app restart):

1. `_get_active_client()` returns `None` (client/wrapper cleared)
2. Backend emits `session_resuming` SSE event before spawning
3. Frontend shows "Resuming session..." indicator
4. `agent_config["needs_context_injection"] = True` triggers `context_injector.py`
5. `_evict_idle_subprocesses()` frees a slot if at cap
6. Fresh subprocess spawned with `COLD_START_TIMEOUT = 45s` watchdog
7. Context injected: last 40 messages / 12K token budget / tool summaries

### Context Injection on Resume

**File:** `context_injector.py` — `build_resume_context()`

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `max_messages` | 40 | Covers most conversation depth |
| `db_fetch_limit` | 100 | Accounts for tool-only messages being filtered |
| `token_budget` | 12,000 | ~3 pages of conversation, fits in system prompt |

Features:
- Filters tool-only messages (tool_use/tool_result blocks)
- Includes tool usage summaries only for tool-only messages (no text blocks): `→ Read(file_path=agent_manager.py)`. When text is present, it already provides context — tool summaries would be redundant noise.
- Drops last assistant message (prevents re-answer duplication)
- Oldest-first truncation with truncation note
- Wrapped in preamble that instructs Claude to treat as READ-ONLY context

### Dynamic Watchdog Timeout

**File:** `agent_manager.py` — `_compute_watchdog_timeout()`, `_run_query_on_client()`

Two timeout profiles:

| Scenario | Initial Timeout | Inter-Message Timeout |
|----------|----------------|----------------------|
| Cold-start resume (`is_cold_start=True`) | **45s** | 180s |
| Fresh new session | Dynamic (180s base + scaling) | 180s |
| Reused session (PATH B) | Dynamic (180s base + scaling) | 180s |

Dynamic scaling: `base(180) + tokens/100K * 30 + turns * 5`, capped at 600s.

The cold-start timeout fires fail-fast — the error message says "Session couldn't start within 45s. Your machine may be under load." instead of the generic "AI service didn't respond." The `had_error` flag triggers auto-retry with a fresh subprocess.

### Frontend Events

| SSE Event | When | Frontend Action |
|-----------|------|----------------|
| `session_resuming` | Before PATH A cold-start | Show "Resuming session..." indicator |
| `reconnecting` | PATH B error → auto-retry | Re-enter streaming state |
| First real data | Any response data arrives | Clear "Resuming session..." / "Reconnecting..." |

### Cap-Based Eviction

**File:** `agent_manager.py` — `_evict_idle_subprocesses()`

When a new subprocess needs to spawn and `MAX_CONCURRENT_SUBPROCESSES` (2) is reached:
1. Count sessions with live subprocesses, separating streaming from idle
2. **Never evict streaming sessions** — only idle ones are candidates
3. Sort idle sessions by `last_used` ascending (oldest first)
4. Thaw frozen sessions before disconnect (cleaner for graceful `__aexit__`)
5. Evict enough to make room for the new subprocess

### Cleanup Loop

**File:** `agent_manager.py` — `_cleanup_stale_sessions_loop()`

Runs every 60s. Five tiers in order:

| Tier | Threshold | Action | Guard |
|------|-----------|--------|-------|
| 1 | 5 min idle | SIGSTOP freeze | Skip if `is_streaming` or already `is_frozen` |
| 1.5 | 2 hr idle | SIGKILL (thaw first if frozen) | Skip if `is_streaming` |
| 2 | 30 min idle | DailyActivity extraction | Skip if already `activity_extracted` |
| 3 | 8 hr idle | Full `_cleanup_session` | — |
| 4 | Every 5 min | Orphan process sweep | — |

## Race Condition Analysis

### Verified Safe

1. **Freeze during active stream** — `is_streaming` flag is set before entering `_run_query_on_client` and cleared in `finally` block. Cleanup loop checks `is_streaming` before freeze/kill. **No race** — single event loop, flag check and freeze are synchronous within the same loop iteration.

2. **Thaw + cleanup loop Tier 1.5 kill** — After thaw, `_get_active_client` sets `is_streaming=True` immediately (before returning). This closes the race window where the cleanup loop could see the session as thawed + idle (not yet streaming) and kill it. The cleanup loop's Tier 1.5 checks `is_streaming` and skips streaming sessions. **Mitigated** — early `is_streaming` flag eliminates the gap.

3. **Kill + message arrive simultaneously** — Cleanup loop kills at 2hr. User sends message. `_get_active_client` finds `client=None`, falls through to PATH A. The session lock prevents the cleanup loop from running cleanup during the new spawn. **No race** — but could cause a brief "Resuming session..." indicator.

4. **Cap eviction during active stream** — `_evict_idle_subprocesses` skips sessions with `is_streaming=True`. Only idle sessions are candidates. **No race.**

5. **`is_cold_start` flag** — passed as a parameter (stack-local), not shared state. Computed from `agent_config["needs_context_injection"]` which is set synchronously before the async generator starts. **No race.**

6. **COLD_START_TIMEOUT constant** — class-level constant, immutable after class definition. **No race.**

### Edge Cases Handled

- **Process dies while frozen:** `_thaw_subprocess` does existence check (`os.kill(pid, 0)`) — if dead, clears references and returns `False`. `_get_active_client` catches this and falls through to PATH A.
- **App restart:** All subprocess references lost. `kill_all_claude_processes()` runs at startup to clean up orphans. Next message from any tab hits PATH A cold-start.
- **Rapid idle-resume cycles:** 5min freeze is generous enough for bursty usage. Even if the user triggers freeze/thaw every 10 minutes, SIGCONT is <100ms — imperceptible.
- **Memory pressure:** Cap-based eviction handles this. If 3+ tabs are open, oldest idle is evicted regardless of freeze state. macOS also naturally pages out SIGSTOP'd process memory.

## Comparison with Alternatives

| Approach | SwarmAI (current) | Claude Code | Kiro |
|----------|-------------------|-------------|------|
| Idle handling | SIGSTOP at 5min, kill at 2hr | No idle kill | Keeps alive for IDE session |
| Resume mechanism | SIGCONT (instant) or PATH A (cold-start) | `--resume` (disk-based) | N/A (never killed) |
| Context on resume | 40 msgs / 12K tokens + tool summaries | Full session replay | N/A |
| Concurrent limit | 2 subprocesses | 1 (single session) | 1 |
| Cold-start timeout | 45s (tight, fail-fast) | N/A | N/A |

## Files Changed

| File | Change |
|------|--------|
| `backend/core/agent_manager.py` | 4-tier lifecycle, SIGSTOP/SIGCONT, `COLD_START_TIMEOUT`, `is_cold_start` param |
| `backend/core/context_injector.py` | 12K token budget, tool summaries, last-assistant drop |
| `desktop/src/hooks/useChatStreamingLifecycle.ts` | `session_resuming` and `reconnecting` event handlers |
| `desktop/src/pages/ChatPage.tsx` | "Resuming session..." and "Reconnecting..." UI indicators |
| `desktop/src/types/index.ts` | `session_resuming` and `reconnecting` event types |

## Testing

- **Unit tests:** `test_chat_session_stability.py` — 21 tests covering freeze timing, idle detection, retry cascade, preservation invariants
- **Property-based:** Hypothesis strategies for idle duration edge cases
- **Manual validation:** Required — SIGSTOP/SIGCONT on Claude CLI subprocess, cap-based eviction under memory pressure

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-16 | SIGSTOP > kill for idle subprocess | Zero-cost hibernation, instant resume, no context loss |
| 2026-03-16 | 2hr kill threshold | Generous for normal work patterns (lunch, meeting), reclaims resources overnight |
| 2026-03-16 | 8hr session TTL | Full workday coverage; 4hr buffer between subprocess kill and session cleanup |
| 2026-03-16 | 45s cold-start timeout | 3x the expected ~15s response; fail-fast under resource pressure instead of 180s hang |
| 2026-03-16 | 12K token budget for resume | Covers ~40 messages with tool summaries; balances context richness vs system prompt size |
| 2026-03-16 | Cap at 2 concurrent | Each CLI uses 200-500MB; 3 caused kernel panics with Kiro + other tools (COE 2026-03-15) |
| 2026-03-16 | Never evict streaming sessions | Killing mid-stream causes "Cannot write to terminated process" cascade |
