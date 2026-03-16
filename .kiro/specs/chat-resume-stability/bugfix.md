# Bugfix Requirements Document

## Introduction

When a user sends a message, goes to lunch/meeting, comes back 1-2 hours later and sends another message, they expect it to just work — same as if they'd never left. What actually happens: the 5-minute Tier 1 cleanup kills the subprocess, `_get_active_client()` returns None, PATH A fires a fresh subprocess (~5-15s, up to 180s under load), and the user sees "Thinking..." with no indication why. Worse, only 20 messages / 6K tokens of context survive — tool results, file reads, long assistant responses are all gone. The agent responds with degraded context. The real cost isn't the latency — it's the invisible context loss.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a user sends a message after more than 5 minutes of idle time THEN the Tier 1 cleanup has already killed the CLI subprocess (`SUBPROCESS_IDLE_SECONDS = 5 * 60`), forcing a full cold-start resume via PATH A with ~5-15s latency (up to 180s under memory pressure)

1.2 WHEN the resume-fallback PATH A creates a fresh subprocess THEN only 20 messages / 6K tokens of conversation history are injected via `context_injector.py`, losing tool results, file reads, and long assistant responses — the agent responds with degraded context while the user thinks they're continuing the same conversation

1.3 WHEN the resume-fallback PATH A is triggered THEN the system shows "Thinking..." with no indication that a reconnection/subprocess recreation is happening, leaving the user confused about why the response is slow

1.4 WHEN the fresh subprocess hangs under memory pressure THEN the 180-second watchdog (`_WATCHDOG_INITIAL_TIMEOUT = 180`) makes the user wait 3 minutes before error recovery fires, even though a fresh subprocess should respond within ~15 seconds

1.5 WHEN the session TTL is 2 hours (`SESSION_TTL_SECONDS = 2 * 60 * 60`) THEN sessions are fully cleaned up after just 2 hours, which doesn't cover a normal workday pattern (morning session → lunch → afternoon continuation)

### Expected Behavior (Correct)

2.1 WHEN a user sends a message after 5+ minutes of idle time (but less than 2 hours) THEN the system SHALL use SIGSTOP/SIGCONT hibernation instead of killing the subprocess — the process stays in memory (zero CPU, macOS pages out idle memory), and resumes instantly (<100ms) via SIGCONT when the next message arrives

2.2 WHEN a frozen (SIGSTOP'd) subprocess is needed for a new message THEN `_get_active_client()` SHALL detect the frozen state, send SIGCONT to thaw it, and return the same client with full conversation context — zero context loss, zero subprocess spawn delay

2.3 WHEN a subprocess has been frozen for more than 2 hours (or system memory pressure is detected) THEN the system SHALL kill the frozen subprocess (Tier 1.5) and fall through to PATH A cold-start resume, which is acceptable for very long idle periods

2.4 WHEN a cold-start resume (PATH A) is unavoidable THEN the system SHALL inject richer context: 40 messages / 12K token budget (up from 20/6K), with tool-use summarization so the resumed agent knows what tools were used (e.g., "→ Read(agent_manager.py)" instead of silently dropping tool blocks)

2.5 WHEN a cold-start resume (PATH A) is triggered THEN the system SHALL emit a `session_resuming` SSE event so the frontend shows "Resuming session..." instead of the ambiguous "Thinking..." indicator

2.6 WHEN the session TTL is reached THEN the system SHALL use an 8-hour TTL (`SESSION_TTL_SECONDS = 8 * 60 * 60`) to cover a full workday pattern, with subprocess kill at 2 hours (`SUBPROCESS_KILL_SECONDS = 2 * 60 * 60`) as the boundary between instant-thaw and cold-start

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a user sends a message within 5 minutes of their previous message (subprocess still HOT, PATH B reuse) THEN the system SHALL CONTINUE TO reuse the existing subprocess with instant response times

3.2 WHEN `MAX_CONCURRENT_SUBPROCESSES = 2` is exceeded THEN `_evict_idle_subprocesses()` SHALL CONTINUE TO evict the oldest idle subprocess before spawning a new one — frozen subprocesses should be thawed before disconnect for clean exit

3.3 WHEN a PATH B reused client encounters an error mid-stream THEN the system SHALL CONTINUE TO evict the client and auto-retry via PATH A with the existing `reconnecting` event

3.4 WHEN the SSE heartbeat wrapper sends periodic heartbeats THEN the system SHALL CONTINUE TO send heartbeats at the configured interval

3.5 WHEN the dynamic watchdog timeout is calculated for PATH B mid-stream operations THEN the system SHALL CONTINUE TO apply 180s+ dynamic scaling — the reduced timeout only applies to fresh subprocess spawns

3.6 WHEN `_cleanup_session()` is called on a frozen subprocess THEN the system SHALL thaw (SIGCONT) before disconnect to ensure clean process exit — never SIGKILL a SIGSTOP'd process without thawing first

3.7 WHEN `ACTIVITY_IDLE_SECONDS = 30 * 60` triggers DailyActivity extraction THEN the system SHALL CONTINUE TO extract activity at 30 minutes idle — this timing is independent of the freeze/kill lifecycle

3.8 WHEN the orphan sweep runs every 5 minutes THEN the system SHALL CONTINUE TO kill orphaned claude processes — frozen processes with valid PIDs in `_active_sessions` are NOT orphans and must be excluded
