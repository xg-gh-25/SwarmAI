# Stop/Resume Chat Fix — Bugfix Design

## Overview

When a user clicks Stop during streaming, the Claude SDK returns `error_during_execution`. The backend treats this identically to a genuine error — destroying the long-lived SDK client from `_active_sessions`. The next message cannot find the client, falls back to PATH A (new subprocess), which is slow and may fail with a misleading "please start a new conversation" error.

The fix introduces an `interrupted` flag on the session's `_active_sessions` entry. `interrupt_session()` sets this flag before calling `client.interrupt()`. The `error_during_execution` handler in `_run_query_on_client` checks the flag: if set, it skips `_cleanup_session()`, preserves the client, and suppresses the error event. The next message finds the preserved client via `_get_active_client()` and resumes on PATH B. The frontend stop indicator is softened from a jarring text block to a subtle inline cue.

## Glossary

- **Bug_Condition (C)**: An `error_during_execution` ResultMessage that was caused by a user-initiated interrupt (the `interrupted` flag is set on the session's `_active_sessions` entry)
- **Property (P)**: The SDK client is preserved in `_active_sessions`, no error event is emitted, and the next message resumes on PATH B
- **Preservation**: Genuine (non-interrupt) errors still trigger `_cleanup_session()` and emit error events; SESSION_BUSY, TTL cleanup, continue_with_answer, and per-tab isolation are unchanged
- **`_clients`**: Temporary dict populated during `_run_query_on_client` execution only; used by `interrupt_session()` and `continue_with_answer` to find the active streaming client
- **`_active_sessions`**: Long-lived dict persisting between turns; keyed by `effective_session_id` (app_session_id ?? sdk_session_id); used by `_get_active_client()` for session resume
- **PATH A**: New client creation path — spawns a fresh CLI subprocess via `_ClaudeClientWrapper`
- **PATH B**: Client reuse path — `_get_active_client()` finds an existing client in `_active_sessions`
- **`effective_session_id`**: `app_session_id` if set, otherwise `sdk_session_id` — the canonical key for `_active_sessions` and message persistence

## Bug Details

### Bug Condition

The bug manifests when a user clicks Stop during streaming, the SDK returns a `ResultMessage` with `subtype='error_during_execution'`, and the backend unconditionally treats this as a fatal error — destroying the reusable SDK client. The `interrupt_session()` method sets no flag to distinguish user-initiated interrupts from genuine errors.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type {session_id: str, result_message: ResultMessage}
  OUTPUT: boolean

  RETURN result_message.subtype == 'error_during_execution'
         AND session_was_interrupted(session_id)
         AND client_exists_in_active_sessions(session_id)
END FUNCTION
```

Where `session_was_interrupted(session_id)` is true when `interrupt_session(session_id)` was called during the current streaming turn. Currently this function always returns false because no interrupt flag exists.

### Examples

- User clicks Stop while assistant is mid-response → SDK returns `error_during_execution` → `_cleanup_session()` destroys client → next message falls to PATH A → slow or fails with "stale session" error
- User clicks Stop, then immediately sends a new message → `_get_active_client()` returns None → PATH A spawns new subprocess → conversation context lost in SDK subprocess
- User clicks Stop during tool execution → SDK returns `error_during_execution` → error event emitted to frontend → user sees red error banner for an intentional action
- User clicks Stop when only 2 buttons exist → `interrupt_session()` succeeds → but cleanup still destroys the client (edge case: interrupt succeeds but error handling still fires)

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Genuine (non-interrupt) `error_during_execution` errors must still call `_cleanup_session()`, set `had_error = True`, and emit an error event to the frontend
- `SESSION_BUSY` rejection must still fire when a message is genuinely still processing (lock held, no interrupt)
- 12-hour TTL stale session cleanup via `_cleanup_stale_sessions_loop` must continue unchanged
- `continue_with_answer` and `continue_with_cmd_permission` must continue to find clients in `_clients` dict and resume correctly
- Per-tab isolation via `tabMapRef` must be preserved — `handleStop` must use per-tab `sessionId` from `tabMapRef`, not shared React state
- `interrupt_session()` must continue to return `{"success": False}` when no active client exists in `_clients`
- The `_clients` dict cleanup in the `_run_query_on_client` finally block must continue to pop the client (this dict is temporary, not the long-lived `_active_sessions`)
- `effective_session_id` pattern (`app_session_id ?? sdk_session_id`) must be used consistently for all keying

**Scope:**
All inputs that do NOT involve a user-initiated interrupt followed by `error_during_execution` should be completely unaffected by this fix. This includes:
- Normal conversation completion (ResultMessage without error)
- Genuine SDK errors (auth failures, subprocess crashes, timeouts)
- Permission request/response flows
- Ask-user-question flows
- Tab switching during streaming
- Backend restart resume-fallback path

## Hypothesized Root Cause

Based on code analysis, the root cause is a missing interrupt flag in the error handling pipeline:

1. **No interrupt flag**: `interrupt_session()` calls `client.interrupt()` but sets no flag anywhere. The `session_context` dict is local to `_execute_on_session_inner` and not accessible from `interrupt_session()`. The `_active_sessions` dict IS accessible from both methods but currently has no `interrupted` field.

2. **Unconditional error handling**: The `error_during_execution` handler in `_run_query_on_client` (line ~2205) unconditionally sets `session_context["had_error"] = True` and calls `_cleanup_session(eff_sid, skip_hooks=True)`. There is no check for whether the error was caused by an interrupt.

3. **Client destruction cascade**: `_cleanup_session()` pops the session from `_active_sessions`, disconnects the wrapper, removes the session lock, and cleans up permission queues. This is correct for genuine errors but destructive for interrupts — the client subprocess is still healthy after an interrupt.

4. **PATH A fallback on resume**: After cleanup, `_get_active_client()` returns None, forcing PATH A. The new subprocess has no conversation context from the previous turns (SDK 0.1.34+ doesn't persist transcripts to disk), so the resume is degraded.

## Correctness Properties

Property 1: Bug Condition — Interrupted sessions preserve client

_For any_ session where `interrupt_session(session_id)` was called and the SDK subsequently returns a `ResultMessage` with `subtype='error_during_execution'`, the fixed `_run_query_on_client` SHALL NOT call `_cleanup_session()`, SHALL NOT set `session_context["had_error"] = True`, SHALL NOT emit an error SSE event, and the client SHALL remain in `_active_sessions` for reuse by the next message.

**Validates: Requirements 2.1, 2.3**

Property 2: Bug Condition — Interrupted sessions resume on PATH B

_For any_ session where the client was preserved after interrupt (Property 1), the next call to `_execute_on_session_inner` with `is_resuming=True` SHALL find the client via `_get_active_client()` and take PATH B (reuse existing client), maintaining full conversation context in the SDK subprocess.

**Validates: Requirements 2.2**

Property 3: Preservation — Genuine errors still cleaned up

_For any_ `error_during_execution` ResultMessage where the session was NOT interrupted (no `interrupted` flag set on `_active_sessions` entry), the fixed code SHALL produce exactly the same behavior as the original code: set `session_context["had_error"] = True`, call `_cleanup_session(eff_sid, skip_hooks=True)`, and emit an error SSE event with code `ERROR_DURING_EXECUTION`.

**Validates: Requirements 3.1**

Property 4: Preservation — Non-error flows unchanged

_For any_ input that does NOT result in `error_during_execution` (normal completion, ask_user_question, cmd_permission_request, other error subtypes), the fixed code SHALL produce exactly the same behavior as the original code, preserving all existing functionality.

**Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.6**

## PE Design Review Findings

Review conducted against `session-identity-and-backend-isolation.md` and `multi-tab-isolation-principles.md` steering files.

### Finding 1 (HIGH) — `_clients` key mismatch in `interrupt_session()`

`interrupt_session()` receives `session_id` and does `self._clients.get(session_id)`. But `_clients` may be keyed by `app_session_id` (line 2428: resume-fallback path registers under `session_context["app_session_id"]`). The frontend calls `chatService.stopSession(tabSessionId)` where `tabSessionId` comes from `tabMapRef` — this IS the `app_session_id`, so the lookup works for resumed sessions. But for brand-new sessions where `app_session_id` is None, the client is registered under `sdk_session_id` (line 2438). The frontend's `sessionIdRef.current` gets set from the `session_start` SSE event which carries `sdk_session_id` for new sessions. So the key alignment is correct in practice, but the design should document this explicitly and add a fallback lookup.

Resolution: Change 1 already uses client-reference matching (iterating `_active_sessions`), which avoids the key mismatch. Add a comment documenting the key alignment assumption. Also add a fallback in `interrupt_session()` to search `_clients` values by identity if the direct key lookup fails.

### Finding 2 (HIGH) — Double cleanup: `_execute_on_session_inner` except block

The `_execute_on_session_inner` except block (lines ~1940-1960) ALSO calls `_cleanup_session(eff_sid, skip_hooks=True)` when any exception propagates from `_run_query_on_client`. If the interrupt causes an exception to propagate (not just `error_during_execution` ResultMessage), the except block would still destroy the session even though we suppressed cleanup inside `_run_query_on_client`. The design must ensure that when the interrupt flag is set, the except block in `_execute_on_session_inner` also skips cleanup.

Resolution: Add Change 5b — in the `_execute_on_session_inner` except block, check the interrupted flag before calling `_cleanup_session()`.

### Finding 3 (MEDIUM) — TSCC lifecycle state incorrectly set to "failed" on interrupt

The `error_during_execution` handler sets TSCC lifecycle to "failed" (line ~2211). For user-initiated interrupts, this is incorrect — the session isn't failed, it's paused. The interrupted branch should either skip the TSCC update or set it to a neutral state.

Resolution: The interrupted branch in Change 2 should skip the TSCC `set_lifecycle_state("failed")` call entirely.

### Finding 4 (MEDIUM) — `_run_query_on_client` finally block timing vs Change 3

Change 3 clears the `interrupted` flag at the start of `_run_query_on_client`. But `_run_query_on_client` is called INSIDE the session lock scope. The flag is set by `interrupt_session()` which runs OUTSIDE the lock (it's a separate HTTP request). The timing is: (1) `_run_query_on_client` starts, clears flag → (2) streaming begins → (3) user clicks Stop → `interrupt_session()` sets flag → (4) SDK returns error → (5) error handler checks flag. This sequence is correct. But if `interrupt_session()` is called BEFORE `_run_query_on_client` starts (e.g., user clicks Stop on a stale request), the flag would be cleared by Change 3 and the error handler wouldn't see it. This is actually the correct behavior — if the interrupt happened before the current query, it shouldn't affect the current query's error handling.

Resolution: No change needed — the timing is correct. Add a comment in Change 3 explaining this.

### Finding 5 (LOW) — Frontend "Stopped." text persisted to DB

The "Stopped." text block is appended to the last assistant message in React state. If the message is later saved to the DB (e.g., via the partial content save in Change 2), the "Stopped." text would be persisted. This is a minor issue — the text is appended client-side AFTER the backend has already saved partial content, so it won't be in the DB. But if the user refreshes and reloads messages from DB, the "Stopped." indicator disappears (inconsistent).

Resolution: Accept this as-is. The "Stopped." is a transient UI indicator, not persistent data. Document this behavior.

### Finding 6 (LOW) — Double assistant content save after interrupt

After the interrupted branch in Change 2 saves partial content, control falls through to the second `ResultMessage` check (the "conversation-complete bookkeeping" block at line ~2590). This block saves assistant content again via `_save_message` and yields the `result` SSE event. The double save is harmless (same content, idempotent). The `result` event is actually desirable — it signals the frontend that the stream ended, which is correct after interrupt. The TSCC lifecycle is set to "idle" (since `had_error` is not set), which is correct for an interrupted-but-healthy session.

Resolution: Accept this as-is. The fall-through behavior is correct and beneficial. Document that the second `ResultMessage` check intentionally fires after the interrupted branch to emit the `result` event. Consider removing the partial content save from the interrupted branch (Change 2) since the second check will save it anyway — but keeping it is safer (defense-in-depth in case the second check is skipped for some reason).

## Fix Implementation (Revised after PE Review)

### Changes Required

Assuming our root cause analysis is correct:

**File**: `backend/core/agent_manager.py`

**Function**: `interrupt_session`

**Change 1 — Set interrupted flag on `_active_sessions` (revised per Finding 1)**:
Before calling `client.interrupt()`, set an `interrupted` flag on the session's `_active_sessions` entry. Use client-reference matching to avoid key mismatch between `_clients` (keyed by the session_id passed to interrupt) and `_active_sessions` (keyed by effective_session_id which may be app_session_id).

```python
async def interrupt_session(self, session_id: str) -> dict:
    client = self._clients.get(session_id)
    if not client:
        logger.warning(f"No active client found for session {session_id}")
        return {"success": False, "message": f"No active session found with ID {session_id}"}

    try:
        logger.info(f"Interrupting session {session_id}")
        # Set interrupted flag BEFORE calling interrupt() so the error handler
        # in _run_query_on_client can distinguish user-initiated stops from
        # genuine errors. We match by client reference (not key) because
        # _active_sessions may be keyed by app_session_id while _clients
        # is keyed by the session_id passed from the frontend.
        for sid, info in self._active_sessions.items():
            if info.get("client") is client:
                info["interrupted"] = True
                logger.info(f"Set interrupted flag on _active_sessions[{sid}]")
                break
        await client.interrupt()
        logger.info(f"Session {session_id} interrupted successfully")
        return {"success": True, "message": "Session interrupted successfully"}
    except Exception as e:
        logger.error(f"Error interrupting session {session_id}: {e}")
        return {"success": False, "message": f"Failed to interrupt session: {str(e)}"}
```

---

**File**: `backend/core/agent_manager.py`

**Function**: `_run_query_on_client`

**Change 2 — Check interrupted flag in `error_during_execution` handler (revised per Finding 3)**:
In the `error_during_execution` branch, check the interrupted flag. If set: skip cleanup, skip TSCC "failed" state, skip error event emission. Save partial content.

```python
if message.subtype == 'error_during_execution':
    eff_sid = (
        session_context["app_session_id"]
        if session_context.get("app_session_id") is not None
        else session_context.get("sdk_session_id")
    )
    session_info = self._active_sessions.get(eff_sid)

    if session_info and session_info.get("interrupted"):
        # ── User-initiated interrupt — preserve client, suppress error ──
        logger.info(f"Session {eff_sid} interrupted by user, preserving client")
        session_info.pop("interrupted", None)  # Clear for next turn
        # Save partial assistant content (user may want to see what was generated)
        if assistant_content and eff_sid:
            try:
                await self._save_message(
                    session_id=eff_sid, role="assistant",
                    content=assistant_content.blocks, model=assistant_model,
                )
            except Exception:
                logger.warning("Failed to save partial content after interrupt", exc_info=True)
        # Do NOT: set had_error, call _cleanup_session, set TSCC "failed", yield error event
    else:
        # ── Genuine error — existing behavior unchanged ──
        error_text = message.result or "Session failed. This may be a stale session — please start a new conversation."
        logger.warning(f"SDK error_during_execution: {error_text}")
        session_context["had_error"] = True
        # ... (rest of existing error_during_execution handling unchanged)
```

---

**Change 3 — Clear stale interrupted flag on query start (confirmed per Finding 4)**:
At the top of `_run_query_on_client`, clear any stale `interrupted` flag. This prevents a flag set during a previous turn from leaking into the current turn. The timing is correct: if `interrupt_session()` is called after this clear but before the error handler runs, the flag will be freshly set and visible.

```python
# At the start of _run_query_on_client, after the docstring:
# Clear any stale interrupted flag from a previous turn. If interrupt_session()
# is called during THIS turn's streaming, it will re-set the flag after this
# point, and the error handler will see it correctly.
eff_sid = (
    session_context["app_session_id"]
    if session_context.get("app_session_id") is not None
    else session_context.get("sdk_session_id")
)
if eff_sid:
    session_info = self._active_sessions.get(eff_sid)
    if session_info:
        session_info.pop("interrupted", None)
```

---

**Change 4 — Check interrupted flag in `source="error"` handler**:
The SDK reader task may surface the interrupt as an exception rather than a `ResultMessage`. Apply the same interrupted-flag check. Note: unlike Change 2 (which falls through to the second `ResultMessage` check that yields the `result` event), the `break` here exits the loop without emitting a `result` event. This is acceptable — the frontend's SSE connection will close when the generator ends, and the `createCompleteHandler` will fire to clean up streaming state.

```python
if item["source"] == "error":
    eff_sid = (
        session_context["app_session_id"]
        if session_context.get("app_session_id") is not None
        else session_context.get("sdk_session_id")
    )
    session_info = self._active_sessions.get(eff_sid) if eff_sid else None
    if session_info and session_info.get("interrupted"):
        logger.info(f"SDK reader error after interrupt for {eff_sid}, treating as user stop")
        session_info.pop("interrupted", None)
        # Save partial content
        if assistant_content and eff_sid:
            try:
                await self._save_message(
                    session_id=eff_sid, role="assistant",
                    content=assistant_content.blocks, model=assistant_model,
                )
            except Exception:
                logger.warning("Failed to save partial content after interrupt error", exc_info=True)
        break  # Exit the combined_queue loop cleanly
    # else: existing error handling unchanged
    logger.error(f"Error from SDK reader: {item['error']}")
    session_context["had_error"] = True
    # ... (rest of existing error handling)
```

---

**File**: `backend/core/agent_manager.py`

**Function**: `_execute_on_session_inner`

**Change 5a — PATH A post-streaming: no change needed**:
The code checks `session_context.get("had_error")` to decide disconnect vs store. Since we no longer set `had_error` for interrupts, this naturally preserves the client. No change needed.

**Change 5b — Except block: skip cleanup on interrupt (NEW, per Finding 2)**:
The `_execute_on_session_inner` except block also calls `_cleanup_session()`. If an interrupt causes an exception to propagate (not just a ResultMessage), this would still destroy the session. Add an interrupted-flag check.

```python
except Exception as e:
    error_traceback = traceback.format_exc()
    logger.error(f"Error in conversation: {e}")
    logger.error(f"Full traceback:\n{error_traceback}")
    eff_sid = (
        session_context["app_session_id"]
        if session_context.get("app_session_id") is not None
        else session_context.get("sdk_session_id")
    )
    # Check if this error was caused by a user-initiated interrupt.
    # If so, preserve the session for reuse instead of cleaning up.
    session_info = self._active_sessions.get(eff_sid) if eff_sid else None
    was_interrupted = session_info and session_info.get("interrupted")
    if was_interrupted:
        logger.info(f"Exception after interrupt for {eff_sid}, preserving session")
        if session_info:
            session_info.pop("interrupted", None)
    elif eff_sid and eff_sid in self._active_sessions:
        await self._cleanup_session(eff_sid, skip_hooks=True)
    if not was_interrupted:
        yield _build_error_event(
            code="CONVERSATION_ERROR",
            message=str(e),
            detail=error_traceback,
        )
```

---

**File**: `desktop/src/pages/ChatPage.tsx`

**Function**: `handleStop`

**Change 6 — Softer stop indicator (per Finding 5: transient UI only)**:
Replace the jarring text with a minimal indicator. This is a transient UI-only change — not persisted to DB. If the user reloads messages from DB, the indicator disappears (acceptable — it's a session-time visual cue, not data).

```typescript
// Replace both occurrences:
{ type: 'text' as const, text: '⏹️ Generation stopped by user.' }
// With:
{ type: 'text' as const, text: '\n\n---\n*Stopped*' }
```

Uses a horizontal rule + italic "Stopped" for a subtle visual separator that doesn't look like an error.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write unit tests that mock the Claude SDK client and simulate the interrupt → error_during_execution flow. Run these tests on the UNFIXED code to observe that `_cleanup_session` is called and the client is destroyed.

**Test Cases**:
1. **Interrupt then error_during_execution**: Call `interrupt_session()`, then simulate `ResultMessage(subtype='error_during_execution')` — verify `_cleanup_session` is called and client is removed from `_active_sessions` (will fail on unfixed code by demonstrating the bug)
2. **Resume after interrupt**: After the above, simulate a new message with `is_resuming=True` — verify `_get_active_client()` returns None and PATH A is taken (will fail on unfixed code by demonstrating degraded resume)
3. **Error event emitted on interrupt**: After interrupt + error_during_execution, verify an error SSE event with code `ERROR_DURING_EXECUTION` is yielded (will fail on unfixed code by demonstrating unwanted error emission)
4. **SDK reader error after interrupt**: Simulate the SDK reader raising an exception (source="error") after interrupt — verify the error is treated as fatal (will fail on unfixed code)

**Expected Counterexamples**:
- `_cleanup_session` is called unconditionally on `error_during_execution`, destroying the client even when the error was caused by user interrupt
- Possible causes: no interrupt flag exists, `error_during_execution` handler has no conditional branch

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := _run_query_on_client_fixed(input)
  ASSERT client_preserved_in_active_sessions(input.session_id)
  ASSERT no_error_event_emitted(result)
  ASSERT had_error_not_set(input.session_context)
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT _run_query_on_client_original(input) = _run_query_on_client_fixed(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain (various error subtypes, message types, session states)
- It catches edge cases that manual unit tests might miss (e.g., interrupted flag set but error subtype is NOT error_during_execution)
- It provides strong guarantees that behavior is unchanged for all non-interrupt error scenarios

**Test Plan**: Observe behavior on UNFIXED code first for genuine errors and normal completions, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Genuine error preservation**: Generate random genuine errors (auth failures, subprocess crashes) and verify `_cleanup_session` is still called and error events are still emitted
2. **Normal completion preservation**: Generate random successful completions and verify client storage in `_active_sessions` is unchanged
3. **SESSION_BUSY preservation**: Verify concurrent request rejection still works when the lock is genuinely held (not interrupted)
4. **TTL cleanup preservation**: Verify `_cleanup_stale_sessions_loop` still cleans up sessions idle > 12h

### Unit Tests

- Test `interrupt_session()` sets `interrupted` flag on the correct `_active_sessions` entry (client-reference matching)
- Test `interrupt_session()` with key mismatch: `_clients` keyed by sdk_session_id, `_active_sessions` keyed by app_session_id — flag still set correctly
- Test `error_during_execution` with `interrupted=True` skips cleanup, skips TSCC "failed", preserves client
- Test `error_during_execution` with `interrupted=False` (or missing) still cleans up (unchanged)
- Test `interrupted` flag is cleared at the start of each new query (no stale flag leakage)
- Test SDK reader error (source="error") with `interrupted=True` is treated as user stop
- Test `_execute_on_session_inner` except block with `interrupted=True` skips cleanup (Finding 2)
- Test `_execute_on_session_inner` except block with `interrupted=False` still cleans up (preservation)
- Test partial assistant content is saved after interrupt
- Test frontend `handleStop` appends subtle "Stopped" indicator instead of the old text

### Property-Based Tests

- Generate random `(interrupted: bool, error_subtype: str)` pairs and verify: cleanup happens iff `NOT interrupted AND error_subtype == 'error_during_execution'`
- Generate random session states (with/without client in `_active_sessions`, with/without `interrupted` flag) and verify `interrupt_session()` only sets the flag when a client exists
- Generate random sequences of (interrupt, send_message) operations and verify the client is always reusable after interrupt

### Integration Tests

- Test full stop → resume flow: start streaming, click Stop, send new message, verify response arrives on PATH B
- Test stop → immediate send timing: verify no SESSION_BUSY race condition
- Test stop during tool execution: verify partial content is preserved and next message works
- Test multiple stops in sequence: verify the flag doesn't accumulate or leak
