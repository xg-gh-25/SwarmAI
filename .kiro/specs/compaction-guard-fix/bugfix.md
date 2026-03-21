# Bugfix Requirements Document

## Introduction

The CompactionGuard in `backend/core/compaction_guard.py` has three confirmed bugs that cause dead loops where the agent wastes hundreds of thousands of tokens repeating the same tool calls without intervention. The guard was originally designed for 200K context windows but is now deployed on 1M context models (Claude Opus 4.6, Sonnet 4.6), where its fixed 85% activation threshold, per-message escalation reset, and lack of progress-based detection combine to render it ineffective. Additionally, the codebase has accumulated dead code references and stale test assertions that need cleanup. This spec also addresses 6 deferred bugs from the original 23-bug process audit (4 frontend, 2 backend) that were not covered by the process-resource-management-fix spec — specifically: closeTab not cleaning up backend sessions, tab restore without validation, SSE connections lingering on tab switch, no app close handler, sessionStorage leak, and stop endpoint not notifying the SSE consumer.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the model context window is 1M tokens (e.g., Claude Opus 4.6) THEN the system does not activate loop detection until 850K tokens (85% of 1M), allowing the agent to waste ~550K tokens per compaction cycle with zero guard intervention because `_CONTEXT_ACTIVATION_PCT = 85` in `check()` gates all detection behind `context_pct < 85`.

1.2 WHEN a new user message is sent (via `session_unit.send()`), or the user answers a question (`continue_with_answer`), or the user grants permission (`continue_with_permission`) THEN the system resets `self._escalation` to `MONITORING` in `reset()`, wiping all escalation progress and requiring the agent to waste another full compaction cycle (~550K tokens on 1M windows) to re-reach each escalation level.

1.3 WHEN the agent enters a dead loop consisting entirely of read-only tool calls (Read, Grep, Glob, Search) with zero productive tool calls (Edit, Write, Bash) THEN the system does not detect or intervene because the only detection mechanisms (set-overlap and single-tool repetition) are gated behind the 85% context threshold, and there is no progress-based detection that fires regardless of context usage percentage.

1.4 WHEN the agent is on a 1M context window and the user sends any message during a loop THEN the system requires approximately 1.65M tokens (3 full compaction cycles of ~550K tokens each) to reach KILL escalation, because each user interaction resets escalation to zero via `reset()`.

1.5 WHEN `test_context_templates.py::test_memory_agent_managed_marker` runs THEN the test fails because the assertion references a MEMORY.md marker string that no longer matches the current template content.

1.6 WHEN `test_context_templates.py::TestSoulTemplate::test_continuity_section` runs THEN the test fails because the assertion references a SOUL.md section heading that no longer matches the current template content.

1.7 WHEN the codebase is searched for references to the deleted `_crash_to_cold()` sync method THEN stale references exist in comments, docstrings, or test files that reference a method that no longer exists.

**Deferred Frontend Bugs (from 23-bug audit)**

1.8 WHEN a user closes a tab that is in IDLE state (not streaming) THEN `closeTab()` only aborts the `abortController` but does NOT call the backend `stopSession` or any cleanup endpoint, leaving the backend SessionUnit alive with its subprocess and MCP servers consuming resources until the LifecycleManager's TTL (12hr) or memory pressure eviction kicks in.

1.9 WHEN the app restores tabs from `open_tabs.json` on startup THEN the frontend does not validate whether the `sessionId` values still exist in the backend database, potentially creating ghost tabs that reference deleted or expired sessions and fail silently on first message.

1.10 WHEN a user switches tabs while an SSE connection is active THEN the SSE connection is not explicitly terminated — it relies on a 45-second stall timeout (heartbeat interval) before the connection is detected as dead, wasting a backend SSE slot and keeping the SessionUnit in STREAMING state unnecessarily.

1.11 WHEN the user closes the app (Cmd+Q, window close, or system shutdown) THEN there is no `beforeunload` or Tauri close handler that calls the backend `/shutdown` endpoint or aborts active SSE connections, leaving orphaned Claude CLI subprocesses and MCP servers until the next startup's `kill_all_claude_processes()` cleanup.

**Deferred Backend Bugs**

1.12 WHEN `sessionStorage` entries are created for pending `ask_user_question` prompts and the SSE stream errors or the user navigates away THEN the `sessionStorage` entries are never cleaned up, accumulating stale data across the browser session lifetime.

1.13 WHEN the stop endpoint (`POST /chat/stop/{session_id}`) is called THEN it calls `interrupt_session()` which transitions the unit state, but it does NOT notify the active SSE consumer (`sse_with_heartbeat`) that the stream should end — the SSE generator continues running until the next heartbeat timeout or message, adding up to 15 seconds of unnecessary processing.

### Expected Behavior (Correct)

2.1 WHEN the model context window is 1M tokens THEN the system SHALL activate loop detection at approximately 40% context usage (400K tokens), scaling the activation threshold dynamically based on the model's context window size so that larger windows get proportionally earlier detection. For 200K windows, the threshold SHALL remain at 85% (170K tokens) to preserve original behavior.

2.2 WHEN `reset()` is called on a new user message, answer, or permission grant THEN the system SHALL clear `_post_compaction_sequence` and `_last_pattern_desc` (per-turn tracking) but SHALL NOT reset `_escalation`, allowing escalation state to persist across user interactions until the session succeeds or is killed.

2.3 WHEN the agent has made 15 or more consecutive non-productive tool calls (Read, Glob, Grep, Search) with zero productive tool calls (Edit, Write, Bash, NotebookEdit) THEN the system SHALL escalate to SOFT_WARN regardless of context usage percentage. WHEN the count reaches 30 or more consecutive non-productive calls with zero productive THEN the system SHALL escalate to HARD_WARN.

2.4 WHEN the agent is on a 1M context window THEN the system SHALL detect loops and begin escalation within the first compaction cycle (~400K tokens) rather than requiring 850K tokens, and escalation SHALL persist across user interactions so that KILL can be reached without requiring 1.65M tokens of waste.

2.5 WHEN `test_context_templates.py::test_memory_agent_managed_marker` runs THEN the test SHALL pass by asserting against the current MEMORY.md template marker string.

2.6 WHEN `test_context_templates.py::TestSoulTemplate::test_continuity_section` runs THEN the test SHALL pass by asserting against the current SOUL.md template section heading.

2.7 WHEN the codebase is searched for references to the deleted `_crash_to_cold()` sync method THEN no stale references SHALL exist in comments, docstrings, or test files within the compaction guard scope. Any unused imports or dead code paths discovered during the fix SHALL be removed.

**Deferred Frontend Bug Fixes**

2.8 WHEN a user closes a tab (any state — IDLE, STREAMING, or COLD) THEN `closeTab()` SHALL call the backend to notify it of the tab closure so the SessionUnit can be cleaned up promptly, rather than waiting for TTL expiry. For IDLE tabs, this means calling a lightweight cleanup endpoint. For STREAMING tabs, the existing abort + stopSession flow is preserved.

2.9 WHEN the app restores tabs from `open_tabs.json` on startup THEN the frontend SHALL validate each `sessionId` against the backend (e.g., via the existing `GET /api/chat/sessions/{id}` endpoint) and remove tabs whose sessions no longer exist, preventing ghost tabs.

2.10 WHEN a user switches away from a tab with an active SSE connection THEN the frontend SHALL abort the SSE fetch connection for the background tab within 2 seconds (not 45s stall timeout), freeing the backend SSE slot. The tab's messages are preserved in `tabMapRef` and the session can resume on the next user message via cold-start resume.

2.11 WHEN the user closes the app THEN a Tauri `close-requested` event handler (or `beforeunload` for web) SHALL call the backend `/shutdown` endpoint to trigger graceful cleanup of all active sessions, subprocesses, and MCP servers before the process exits.

**Deferred Backend Bug Fixes**

2.12 WHEN `sessionStorage` entries are created for pending questions THEN they SHALL be cleaned up on SSE stream completion (success or error), SSE disconnect, or tab close — whichever comes first.

2.13 WHEN the stop endpoint is called THEN it SHALL signal the active SSE consumer to break its loop within 1 second (e.g., via a per-session `asyncio.Event` that `sse_with_heartbeat` checks alongside the message queue), rather than waiting for the next heartbeat timeout.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN the model context window is 200K tokens (the original design target) THEN the system SHALL CONTINUE TO activate loop detection at 85% (170K tokens), preserving the original threshold behavior for smaller context windows.

3.2 WHEN the guard is in PASSIVE phase (before any compaction is detected) THEN the system SHALL CONTINUE TO return MONITORING from `check()` without any interference, regardless of context usage or tool call patterns.

3.3 WHEN `reset_all()` is called (subprocess respawn) THEN the system SHALL CONTINUE TO fully reset all state including escalation, phase, context tracking, and all tool records back to initial values.

3.4 WHEN set-overlap detection identifies >60% of post-compaction calls matching the pre-compaction baseline (with minimum 5 calls) THEN the system SHALL CONTINUE TO detect this as a loop pattern and escalate accordingly.

3.5 WHEN single-tool repetition detection identifies the same (tool_name, input_hash) pair appearing 5 or more times THEN the system SHALL CONTINUE TO detect this as a loop pattern and escalate accordingly.

3.6 WHEN `work_summary()` is called THEN the system SHALL CONTINUE TO generate a structured summary of all tool calls grouped by tool name with representative input details and "CRITICAL: Do NOT re-run" instructions.

3.7 WHEN `build_guard_event()` is called with an escalation level THEN the system SHALL CONTINUE TO return properly formatted SSE event dicts with type, subtype, context_pct, message, and pattern_description fields (or None for MONITORING).

3.8 WHEN heuristic compaction detection identifies a ≥30 percentage point context drop THEN the system SHALL CONTINUE TO auto-activate the guard and snapshot the pre-compaction baseline.

3.9 WHEN `record_tool_call()` is called THEN the system SHALL CONTINUE TO hash inputs, create ToolRecords, append to tracking structures, and never raise exceptions that block streaming.

3.10 WHEN any guard method encounters an internal exception THEN the system SHALL CONTINUE TO catch it, log it, and return a safe default (MONITORING for `check()`, empty string for `work_summary()`, None for `build_guard_event()`), ensuring the guard never blocks streaming.

**Deferred Bug Regression Prevention**

3.11 WHEN `closeTab()` calls the backend cleanup endpoint and the backend is unreachable (e.g., during shutdown) THEN the frontend SHALL handle the error silently and proceed with local tab removal — backend cleanup is best-effort, never blocking.

3.12 WHEN tab restore validation finds that ALL saved sessions are expired THEN the frontend SHALL create a fresh default tab rather than showing an empty tab bar.

3.13 WHEN the SSE connection is aborted on tab switch THEN the backend SessionUnit SHALL transition from STREAMING to IDLE (not DEAD) so the conversation can resume via cold-start on the next message — the abort is a UI optimization, not a session termination.

3.14 WHEN the Tauri close handler calls `/shutdown` THEN the existing `disconnect_all()` → `kill()` → hook firing sequence SHALL CONTINUE TO work as before — the close handler is an additional trigger, not a replacement for the existing shutdown path.
