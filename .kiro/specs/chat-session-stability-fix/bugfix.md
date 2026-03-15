# Bugfix Requirements Document

## Introduction

Chat sessions suffer from a cascading stability regression where three interacting bugs in `backend/core/agent_manager.py` cause frequent "Not connected. Call connect() first" and "Cannot write to terminated process (exit code: -9)" errors. After these errors, streaming responses disappear from the frontend. The root cause is a combination of: (1) premature session cleanup before auto-retry can use the session, (2) `last_used` timestamp never updating after streaming completes, causing aggressive idle timeouts to kill active conversations, and (3) inconsistent retry-eligibility checks between the SDK reader and `error_during_execution` error paths.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN `error_during_execution` is received in `_run_query_on_client` AND the error is retriable THEN the system calls `_cleanup_session(eff_sid, skip_hooks=True)` which pops the session from `_active_sessions`, disconnects the wrapper, and removes the session lock BEFORE checking `_will_auto_retry_ede`. The subsequent retry in `_execute_on_session_inner` creates a fresh subprocess successfully, but the premature cleanup causes metadata loss: (a) the session lock is removed, allowing concurrent requests to slip through during the retry, (b) `interrupt_session` cannot find the client during the retry stream because `_active_sessions` was popped, (c) the `_early_active_key` in `session_context` becomes stale, and (d) the wrapper is double-disconnected (once by `_cleanup_session`, once by the retry loop's `_disconnect_wrapper` call on the original wrapper variable)

1.2 WHEN a streaming response completes successfully via PATH B (reused client) THEN the system does not update the `last_used` timestamp on the session, leaving it at the value set by `_get_active_client` at the start of the request

1.3 WHEN a streaming response takes longer than `SUBPROCESS_IDLE_SECONDS` (120s) to complete (e.g., long tool-use chains, code generation) THEN the `_cleanup_stale_sessions_loop` Tier 1 idle disconnect sees the session as idle (because `last_used` was never updated after streaming started) and kills the subprocess mid-stream via `_disconnect_wrapper`, setting `info["wrapper"] = None` and `info["client"] = None` while the SSE connection is still open. The SDK reader task then encounters "Cannot write to terminated process" because the underlying process was killed underneath the active stream. Note: Tier 1 preserves session metadata in `_active_sessions` — the "Not connected" error only occurs when the subprocess dies during an in-flight request, not between turns (between turns, `_get_active_client` returns None and gracefully falls through to resume-fallback)

1.4 WHEN the user sends a follow-up message after the subprocess was killed by Tier 1 idle timeout between turns THEN `_get_active_client` returns None (wrapper=None, client=None) and the system gracefully falls through to resume-fallback (PATH A with context injection), which is slower (5-15s overhead) but functional. The "Not connected" error only occurs if the kill happens during an active stream, not between turns

1.7 WHEN `SUBPROCESS_IDLE_SECONDS` is set to 120 (2 minutes) THEN even with the `last_used` fix, normal user reading/thinking time between messages (2-5 minutes) frequently exceeds the threshold, causing unnecessary subprocess kills and forcing expensive resume-fallback (context injection) on the next message, which produces the "⚠️ AI service was slow to respond. Retrying automatically..." message

1.5 WHEN the SDK reader error path determines retry eligibility THEN the system uses `_retry_count < _max_retries` as the condition, but WHEN the `error_during_execution` path determines retry eligibility THEN the system uses `not session_context.get("_path_a_retried")` as the condition, creating an inconsistency where errors may be incorrectly suppressed or incorrectly shown to the user

1.6 WHEN Bug 1 causes a retry to fail on a destroyed session AND Bug 3 suppresses the error THEN the user sees streaming responses disappear with no error message and no recovery

1.8 WHEN Bug 1's `_cleanup_session` pops the `_early_active_key` entry from `_active_sessions` AND the retry's `_run_query_on_client` receives a new SDK session ID via the `init` SystemMessage THEN the early registration code (line ~3262) creates a NEW entry keyed by the new SDK session ID (because `_init_sid not in self._active_sessions` is True after cleanup), but `session_context["_early_active_key"]` still references the old key that was already cleaned up, causing the post-stream cleanup to no-op on a stale key while the new entry persists with potentially inconsistent state

1.9 WHEN "Cannot write to terminated process" is received as an error AND `_is_retriable_error` returns True for this pattern (line 335) THEN the full auto-retry cascade fires: error is suppressed from the frontend, `had_error` is set, and the retry loop in `_execute_on_session_inner` creates a fresh subprocess. This means Bug 2's mid-stream subprocess kill triggers Bug 1's premature cleanup path AND Bug 3's inconsistent retry eligibility, creating the full cascading failure

### Expected Behavior (Correct)

2.1 WHEN `error_during_execution` is received AND the error is retriable AND auto-retry will handle it THEN the system SHALL defer `_cleanup_session` and NOT destroy the session state, allowing the retry path in `_execute_on_session_inner` to cleanly disconnect and re-create the client

2.2 WHEN a streaming response completes successfully (both PATH A and PATH B) THEN the system SHALL update the `last_used` timestamp on the session to the current time, reflecting that the session was actively used throughout the streaming duration

2.3 WHEN a streaming response is in progress THEN the `_cleanup_stale_sessions_loop` SHALL NOT kill the subprocess, because the updated `last_used` timestamp keeps the idle time below the `SUBPROCESS_IDLE_SECONDS` threshold

2.4 WHEN the user sends a follow-up message after a completed response THEN the system SHALL find a live subprocess (because `last_used` was updated at completion) or gracefully fall through to resume-fallback without "Not connected" errors

2.7 WHEN `SUBPROCESS_IDLE_SECONDS` is configured THEN the value SHALL be 300 (5 minutes) to provide a comfortable window for normal user reading/thinking time between messages, while still reclaiming RAM from genuinely abandoned sessions within a reasonable timeframe

2.5 WHEN any error path (SDK reader error OR `error_during_execution`) determines retry eligibility THEN the system SHALL use the same condition (`_retry_count < _max_retries`) consistently, so that error suppression and error display decisions are aligned across all paths

2.6 WHEN an auto-retry is warranted THEN the system SHALL successfully create a fresh client and stream the response, because session state was preserved (not prematurely cleaned up) and the retry condition was evaluated consistently

### Unchanged Behavior (Regression Prevention)

3.1 WHEN `error_during_execution` is received AND the error is NOT retriable THEN the system SHALL CONTINUE TO call `_cleanup_session` and yield an error event to the frontend

3.2 WHEN `error_during_execution` is received AND the session was interrupted by the user THEN the system SHALL CONTINUE TO preserve the client and suppress the error (existing interrupt handling logic)

3.3 WHEN a session is genuinely idle (no active streaming, no recent messages) for longer than `SUBPROCESS_IDLE_SECONDS` THEN the system SHALL CONTINUE TO disconnect the subprocess to free RAM

3.4 WHEN a session reaches `SESSION_TTL_SECONDS` (2 hours) of true idleness THEN the system SHALL CONTINUE TO perform full cleanup including hook firing

3.5 WHEN PATH B (reused client) encounters an error THEN the system SHALL CONTINUE TO evict the broken session and signal auto-retry via PATH A with a "reconnecting" indicator

3.6 WHEN all retry attempts are exhausted THEN the system SHALL CONTINUE TO yield a friendly error event to the frontend with a suggested action

3.7 WHEN `_get_active_client` is called at the start of a request THEN the system SHALL CONTINUE TO update `last_used` and reset `activity_extracted` as it does today

3.8 WHEN the SDK reader error path encounters a non-retriable error THEN the system SHALL CONTINUE TO yield the error event immediately without suppression
