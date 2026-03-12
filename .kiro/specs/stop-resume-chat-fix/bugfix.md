# Bugfix Requirements Document

## Introduction

When a user clicks the Stop button during streaming and then sends a new message, the session becomes unusable. The interrupt triggers an `error_during_execution` result from the SDK, which the backend treats as a fatal session error — cleaning up the long-lived SDK client from `_active_sessions`. The next message cannot find the client, falls back to PATH A (new CLI subprocess), which is slow and may fail with a misleading "please start a new conversation" error. Additionally, the frontend appends a jarring "⏹️ Generation stopped by user." text block that feels like a hard break rather than a natural pause.

The root cause is that `interrupt_session()` sets no flag to distinguish user-initiated interrupts from genuine errors, so the `error_during_execution` handler in `_run_query_on_client` unconditionally calls `_cleanup_session()` — destroying the reusable SDK client subprocess.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the user clicks Stop during streaming and the SDK returns `error_during_execution` THEN the system sets `session_context["had_error"] = True` and calls `_cleanup_session(eff_sid, skip_hooks=True)`, destroying the long-lived SDK client from `_active_sessions`

1.2 WHEN the user sends a new message after stopping and `_get_active_client()` returns None (client was cleaned up) THEN the system falls back to PATH A (spawns a new CLI subprocess) instead of reusing the existing session, losing conversation context in the SDK subprocess

1.3 WHEN PATH A fallback fails during resume-after-interrupt THEN the system displays "Session failed. This may be a stale session — please start a new conversation." which misleads the user into thinking the tab is broken

1.4 WHEN the user clicks Stop and the session lock is still held by the `_run_query_on_client` finally block THEN the next message sent immediately gets rejected with SESSION_BUSY ("This chat session is still processing a previous message")

1.5 WHEN the user clicks Stop during streaming THEN the frontend appends "⏹️ Generation stopped by user." as a text content block to the last assistant message, creating a jarring visual break in the conversation

### Expected Behavior (Correct)

2.1 WHEN the user clicks Stop during streaming and the SDK returns `error_during_execution` due to interrupt THEN the system SHALL recognize this as a user-initiated interrupt (via an `interrupted` flag on `session_context`), skip `_cleanup_session()`, and preserve the SDK client in `_active_sessions` for reuse

2.2 WHEN the user sends a new message after stopping THEN the system SHALL find the preserved client via `_get_active_client()` and resume on PATH B (reuse existing long-lived client), maintaining full conversation context

2.3 WHEN an `error_during_execution` occurs due to user interrupt THEN the system SHALL NOT emit an error event to the frontend, since the stop was intentional and the frontend already handles the UI transition

2.4 WHEN the user clicks Stop THEN the session lock SHALL be released promptly after the SDK stream ends so the next message is not rejected with SESSION_BUSY

2.5 WHEN the user clicks Stop during streaming THEN the frontend SHALL display a subtle, non-intrusive indicator (e.g., a softer message like "Stopped" or an inline visual cue) instead of appending a prominent text block to the assistant message

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a genuine (non-interrupt) `error_during_execution` occurs THEN the system SHALL CONTINUE TO set `had_error = True`, call `_cleanup_session()`, and emit an error event to the frontend

3.2 WHEN a user sends a message while another is genuinely still processing (not interrupted) THEN the system SHALL CONTINUE TO reject with SESSION_BUSY to prevent double-send corruption

3.3 WHEN sessions are idle beyond the 12-hour TTL THEN the system SHALL CONTINUE TO clean them up via `_cleanup_stale_sessions_loop`

3.4 WHEN `continue_with_answer` or `continue_with_cmd_permission` is called on an active session THEN the system SHALL CONTINUE TO find the client in `_clients` and resume correctly

3.5 WHEN the frontend handles Stop for a specific tab THEN the system SHALL CONTINUE TO use `tabMapRef` (not React state) for per-tab isolation decisions

3.6 WHEN `interrupt_session()` is called but no active client exists in `_clients` THEN the system SHALL CONTINUE TO return `{"success": False}` without side effects
