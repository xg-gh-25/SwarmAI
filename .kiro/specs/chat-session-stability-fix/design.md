# Chat Session Stability Fix — Bugfix Design

## Overview

Three interacting bugs in `backend/core/agent_manager.py` cause cascading session failures: (1) `_cleanup_session` destroys session state before auto-retry can use it, (2) `last_used` is never updated after streaming completes on PATH B, causing the idle cleanup loop to kill active subprocesses, and (3) retry-eligibility checks differ between the SDK reader error path and the `error_during_execution` path, leading to inconsistent error suppression. The fix defers cleanup when retry is warranted, updates `last_used` after streaming, unifies retry-eligibility into a single helper call, and increases `SUBPROCESS_IDLE_SECONDS` from 2 to 5 minutes to match normal user interaction patterns.

## Glossary

- **Bug_Condition (C)**: The set of inputs/states where session stability fails — retriable errors with premature cleanup, stale `last_used` timestamps, or inconsistent retry decisions
- **Property (P)**: Session state is preserved for retry, timestamps reflect actual activity, and retry eligibility is evaluated identically across all error paths
- **Preservation**: All existing behaviors for non-retriable errors, interrupted sessions, genuine idle cleanup, PATH B→A fallback, and exhausted-retry error display must remain unchanged
- **`_cleanup_session`**: Method at line 811 that pops session from `_active_sessions`, disconnects wrapper, removes lock and permission queue
- **`_cleanup_stale_sessions_loop`**: Background loop at line 633 that disconnects idle subprocesses (Tier 1: `SUBPROCESS_IDLE_SECONDS`) and performs full cleanup (Tier 3: `SESSION_TTL_SECONDS`)
- **`_run_query_on_client`**: Shared message-processing loop at line 2573 containing both the SDK reader error path and the `error_during_execution` handler
- **`_is_retriable_error`**: Pure function at line 320 that checks if an error string matches retriable patterns
- **PATH A**: Fresh client creation path in `_execute_on_session_inner`
- **PATH B**: Reused client path in `_execute_on_session_inner`
- **`_will_auto_retry_ede`**: The retry-eligibility flag in the `error_during_execution` handler (currently uses `not session_context.get("_path_a_retried")`)
- **`_will_auto_retry`**: The retry-eligibility flag in the SDK reader error handler (currently uses `_retry_count < _max_retries`)

## Bug Details

### Bug Condition

The bugs manifest when a retriable error occurs during streaming and the system attempts auto-retry, OR when a PATH B streaming response takes significant time, OR when different error paths evaluate retry eligibility.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type {error: string | None, path: "A" | "B", streaming_duration: float, retry_count: int, max_retries: int, path_a_retried: bool}
  OUTPUT: boolean

  bug1 := input.error IS NOT None
          AND _is_retriable_error(input.error)
          AND retry_will_be_attempted(input)
          AND _cleanup_session_called_before_retry_check(input)

  bug2 := input.path == "B"
          AND input.streaming_duration > 0
          AND last_used_not_updated_after_streaming()

  bug3 := input.error IS NOT None
          AND (input.retry_count < input.max_retries) != (NOT input.path_a_retried)
          -- The two conditions disagree on retry eligibility

  RETURN bug1 OR bug2 OR bug3
END FUNCTION
```

### Examples

- **Bug 1**: User sends a message, SDK subprocess is OOM-killed (exit code -9). The `error_during_execution` handler calls `_cleanup_session(eff_sid, skip_hooks=True)` which pops the session, disconnects the wrapper, and removes the lock. Then `_will_auto_retry_ede` evaluates to `True`. The retry loop in `_execute_on_session_inner` creates a fresh subprocess successfully (new wrapper + client), but operates with degraded metadata: the session lock was removed (concurrent requests can slip through), `interrupt_session` can't find the client during the retry stream (user can't stop it), and the original wrapper is double-disconnected. The retry may succeed, but the session is in an inconsistent state.

- **Bug 2**: User sends a message on an existing session (PATH B). `_get_active_client` sets `last_used = time.time()` at request start. The streaming response takes 150 seconds (long tool-use chain). During this time, `_cleanup_stale_sessions_loop` Tier 1 runs, sees `now - last_used > 120` (SUBPROCESS_IDLE_SECONDS), and kills the subprocess mid-stream via `_disconnect_wrapper` (sets wrapper=None, client=None but preserves session metadata). The SDK reader task then encounters "Cannot write to terminated process" because the process was killed underneath the active SSE stream. Note: between turns, Tier 1 is gracefully handled by `_get_active_client` returning None → resume-fallback. The "Cannot write to terminated process" error only occurs during in-flight requests where the SSE connection is still open when the process dies. Since "Cannot write to terminated process" IS in the `_is_retriable_error` set (line 335), this triggers the full auto-retry cascade (Bug 1 + Bug 3).

- **Bug 3**: A Bedrock throttling error occurs. The SDK reader error path checks `_retry_count (0) < _max_retries (2)` → `True` → suppresses error. But the `error_during_execution` path checks `not session_context.get("_path_a_retried")` → also `True` on first attempt, but after one retry where `_path_a_retried` is set to `True`, the `error_during_execution` path says "no more retries" while the SDK reader path (checking count) says "retries remain". This inconsistency causes errors to be either incorrectly suppressed or incorrectly shown.

- **Edge case**: A non-retriable error (e.g., auth failure) should still trigger immediate `_cleanup_session` and yield an error event — this behavior must be preserved.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Non-retriable errors in `error_during_execution` must still call `_cleanup_session` and yield error events to the frontend (Req 3.1)
- User-interrupted sessions must still be preserved with the `interrupted` flag check (Req 3.2)
- Genuinely idle sessions (no active streaming, no recent messages) must still be disconnected after `SUBPROCESS_IDLE_SECONDS` (Req 3.3)
- Sessions exceeding `SESSION_TTL_SECONDS` (2h) must still get full cleanup with hook firing (Req 3.4)
- PATH B error → evict → PATH A retry flow must continue to work (Req 3.5)
- Exhausted retries must still yield a friendly error event (Req 3.6)
- `_get_active_client` must still update `last_used` and reset `activity_extracted` at request start (Req 3.7)
- Non-retriable SDK reader errors must still yield error events immediately (Req 3.8)

**Scope:**
All inputs that do NOT involve retriable errors with remaining retries, PATH B streaming completion, or retry-eligibility evaluation should be completely unaffected by this fix. This includes:
- Mouse/keyboard interactions in the frontend
- Non-error streaming paths
- Session creation and initial registration
- Permission request handling
- Stale-result detection and re-query logic

## Hypothesized Root Cause

Based on the bug analysis and code review, the root causes are:

1. **Premature Cleanup Ordering (Bug 1)**: In the `error_during_execution` handler (around line 2960-2980), `_cleanup_session(eff_sid, skip_hooks=True)` is called unconditionally for non-interrupted errors BEFORE the `_will_auto_retry_ede` check. The cleanup destroys the session entry, wrapper, lock, and permission queue. The retry loop in `_execute_on_session_inner` (line 2440+) creates a fresh subprocess successfully, but operates with degraded metadata: the session lock is gone (no concurrency protection during retry), `interrupt_session` can't find the client (user can't stop the retry), and the original wrapper is double-disconnected (harmless but wasteful). The retry may succeed, but the session is left in an inconsistent state that can cause issues on subsequent turns.

2. **Missing Timestamp Update (Bug 2)**: After PATH B streaming completes successfully in `_execute_on_session_inner` (around line 2370), there is no `info["last_used"] = time.time()` call. The only `last_used` update happens in `_get_active_client` (line 1137) at request start. For PATH A, the post-stream storage block (line 2540) creates a new `_final_info` dict with `"last_used": time.time()`, so PATH A is not affected. But PATH B reuses the existing `info` dict without updating it.

3. **Divergent Retry Conditions (Bug 3)**: The SDK reader error path (around line 2870) uses `_retry_count < _max_retries` (count-based), while the `error_during_execution` path (around line 2990) uses `not session_context.get("_path_a_retried")` (boolean flag-based). The boolean becomes `True` after the first retry and stays `True`, meaning the `error_during_execution` path thinks retries are exhausted after one attempt. The count-based check correctly allows up to `MAX_RETRY_ATTEMPTS` retries.

4. **Compounding Effect**: Bug 1 + Bug 3 together cause silent failures: Bug 1 destroys the session before retry, and Bug 3's inconsistent condition may suppress the error that would otherwise alert the user.

## Correctness Properties

Property 1: Bug Condition — Deferred Cleanup on Retriable Errors

_For any_ `error_during_execution` event where `_is_retriable_error(error_text)` returns `True` AND retry attempts remain (`_retry_count < MAX_RETRY_ATTEMPTS`), the fixed `_run_query_on_client` SHALL NOT call `_cleanup_session` before breaking out of the message loop, preserving the session entry in `_active_sessions`, the wrapper connection, and the session lock for the retry path.

**Validates: Requirements 2.1, 2.6**

Property 2: Bug Condition — Timestamp Update After Streaming

_For any_ successful streaming completion via PATH B (reused client), the fixed `_execute_on_session_inner` SHALL update `info["last_used"]` to `time.time()` after the `_run_query_on_client` generator is exhausted, ensuring the timestamp reflects the end of streaming rather than the start of the request.

**Validates: Requirements 2.2, 2.3, 2.4**

Property 3: Bug Condition — Unified Retry Eligibility

_For any_ error evaluated for retry eligibility, the fixed code SHALL use the same condition (`_is_retriable_error(error) AND _retry_count < MAX_RETRY_ATTEMPTS`) in both the SDK reader error path and the `error_during_execution` path, so that the suppress-or-show decision is identical for the same error state.

**Validates: Requirements 2.5**

Property 4: Preservation — Non-Retriable Errors Still Cleaned Up

_For any_ `error_during_execution` event where `_is_retriable_error(error_text)` returns `False` OR all retry attempts are exhausted, the fixed code SHALL still call `_cleanup_session(eff_sid, skip_hooks=True)` and yield an error event to the frontend, preserving the existing error-handling behavior for non-retriable errors.

**Validates: Requirements 3.1, 3.6, 3.8**

Property 5: Preservation — Idle Cleanup Unchanged for Genuinely Idle Sessions

_For any_ session where no streaming is active AND `time.time() - last_used > SUBPROCESS_IDLE_SECONDS`, the fixed `_cleanup_stale_sessions_loop` SHALL still disconnect the subprocess, preserving the existing RAM-reclamation behavior for genuinely idle sessions.

**Validates: Requirements 3.3, 3.4**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `backend/core/agent_manager.py`

### Change 1: Defer `_cleanup_session` in `error_during_execution` handler (Bug 1)

**Function**: `_run_query_on_client` — the `error_during_execution` branch (~line 2960)

**Current code** (simplified):
```python
# Remove broken session from reuse pool
if eff_sid and eff_sid in self._active_sessions:
    await self._cleanup_session(eff_sid, skip_hooks=True)

# Check if auto-retry will handle this silently
_will_auto_retry_ede = (
    _is_retriable_error(error_text)
    and not session_context.get("_path_a_retried")
)
```

**Fixed code** (simplified):
```python
# Determine retry eligibility BEFORE cleanup
_retry_count = session_context.get("_path_a_retry_count", 0)
_will_auto_retry_ede = (
    _is_retriable_error(error_text)
    and _retry_count < self.MAX_RETRY_ATTEMPTS
)

# Only clean up if auto-retry will NOT handle this
if not _will_auto_retry_ede:
    if eff_sid and eff_sid in self._active_sessions:
        await self._cleanup_session(eff_sid, skip_hooks=True)
        logger.info(f"Removed broken session {eff_sid} from active sessions pool")
```

**Specific Changes**:
1. Move the `_will_auto_retry_ede` evaluation BEFORE the `_cleanup_session` call
2. Wrap `_cleanup_session` in `if not _will_auto_retry_ede:` guard
3. Replace the boolean `_path_a_retried` check with the count-based `_retry_count < MAX_RETRY_ATTEMPTS` check (also fixes Bug 3 for this path)

### Change 2: Update `last_used` after PATH B streaming completes (Bug 2)

**Function**: `_execute_on_session_inner` — after the PATH B `_run_query_on_client` yield loop (~line 2370)

**Current code** (simplified):
```python
async for event in self._run_query_on_client(...):
    yield event

# PATH B post-run: if the reused client hit an error...
if session_context.get("had_error") and session_id:
    ...
```

**Fixed code** (simplified):
```python
async for event in self._run_query_on_client(...):
    yield event

# Update last_used after streaming completes (Bug 2 fix)
# This prevents _cleanup_stale_sessions_loop from killing
# subprocesses that were actively streaming.
_path_b_info = self._active_sessions.get(session_id)
if _path_b_info:
    _path_b_info["last_used"] = time.time()

# PATH B post-run: if the reused client hit an error...
if session_context.get("had_error") and session_id:
    ...
```

**Specific Changes**:
1. After the `async for event in self._run_query_on_client(...)` loop in the PATH B block, look up the session info and update `last_used` to `time.time()`
2. This runs regardless of error state — even if `had_error` is True, the timestamp should reflect the last activity time

### Change 3: Unify retry-eligibility in SDK reader error path (Bug 3)

**Function**: `_run_query_on_client` — the SDK reader `"error"` source handler (~line 2870)

**Current code**:
```python
_retry_count = session_context.get("_path_a_retry_count", 0)
_max_retries = self.MAX_RETRY_ATTEMPTS
_will_auto_retry = (
    _is_retriable_error(raw_error)
    and _retry_count < _max_retries
)
```

This path is already correct (count-based). The fix is in the `error_during_execution` path (Change 1 above) and the `is_error` ResultMessage path.

**Function**: `_run_query_on_client` — the `is_error` ResultMessage handler (~line 3085)

**Current code**:
```python
_will_auto_retry_sdk = (
    _is_retriable_error(error_msg)
    and not session_context.get("_path_a_retried")
)
```

**Fixed code**:
```python
_retry_count_sdk = session_context.get("_path_a_retry_count", 0)
_will_auto_retry_sdk = (
    _is_retriable_error(error_msg)
    and _retry_count_sdk < self.MAX_RETRY_ATTEMPTS
)
```

**Specific Changes**:
1. Replace `not session_context.get("_path_a_retried")` with `session_context.get("_path_a_retry_count", 0) < self.MAX_RETRY_ATTEMPTS` in both the `error_during_execution` handler and the `is_error` ResultMessage handler
2. This aligns all three error paths (SDK reader error, `error_during_execution`, `is_error`) to use the same count-based condition

### Change 5: Increase `SUBPROCESS_IDLE_SECONDS` from 120 to 300 (Bug 2 mitigation)

**Location**: `AgentManager` class constants (~line 487)

**Current code**:
```python
SUBPROCESS_IDLE_SECONDS = 2 * 60
```

**Fixed code**:
```python
SUBPROCESS_IDLE_SECONDS = 5 * 60
```

**Rationale**:
Even with the `last_used` fix (Change 2), 2 minutes is too aggressive for normal user interaction patterns. Users routinely spend 2-5 minutes reading a response, thinking, or switching to another app before sending the next message. Each premature subprocess kill forces a full resume-fallback (context injection + fresh subprocess spawn), adding 5-15s overhead and producing the "⚠️ AI service was slow to respond. Retrying automatically..." message.

The original value was 5 minutes before it was lowered to 2 in the COE fix (commit `ee790fe`). The COE addressed OOM kills from too many concurrent subprocesses — but that concern is already handled by `MAX_CONCURRENT_SUBPROCESSES = 3` and `_evict_idle_subprocesses()`. The cap-based eviction is the correct defense against OOM, not aggressive idle timeouts.

With `MAX_CONCURRENT_SUBPROCESSES = 3` and each subprocess using 200-500MB, the worst case is 600MB-1.5GB — well within macOS limits. 5 minutes provides a comfortable window for normal interaction while still reclaiming RAM from genuinely abandoned sessions.

**Update comment**:
```python
# Idle threshold for subprocess disconnect (5 minutes).
# Kills the claude CLI subprocess to free ~100-300MB RAM per tab,
# but keeps session metadata.  On next message, the resume-fallback
# path (context injection) seamlessly recreates the session.
# Set to 5min to allow normal user reading/thinking time between
# messages.  OOM prevention is handled by MAX_CONCURRENT_SUBPROCESSES
# cap + _evict_idle_subprocesses(), not by aggressive idle timeouts.
SUBPROCESS_IDLE_SECONDS = 5 * 60
```

### Change 4: Update `last_used` after PATH A streaming completes (defensive)

**Function**: `_execute_on_session_inner` — the PATH A post-stream storage block (~line 2540)

The PATH A path already creates a new `_final_info` dict with `"last_used": time.time()`, so this is already correct. However, for the retry loop within PATH A, each retry iteration should also update `last_used` on the early-registered session info to prevent the cleanup loop from interfering during retries.

**Specific Changes**:
1. Inside the PATH A retry `while` loop, after `session_context["had_error"] = False`, add:
   ```python
   _early_key = session_context.get("_early_active_key")
   if _early_key:
       _early_info = self._active_sessions.get(_early_key)
       if _early_info:
           _early_info["last_used"] = time.time()
   ```

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bugs on unfixed code, then verify the fixes work correctly and preserve existing behavior. All tests target `backend/core/agent_manager.py` and can use mocked SDK clients and session state.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bugs BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that simulate error conditions in `_run_query_on_client` and verify session state after the error handler runs. Run these tests on the UNFIXED code to observe failures.

**Test Cases**:
1. **Premature Cleanup Test**: Simulate a retriable `error_during_execution` with retries remaining. Assert that `_active_sessions[eff_sid]` still exists after the handler runs. (Will fail on unfixed code — session is popped before retry check)
2. **Stale Timestamp Test**: Simulate a PATH B streaming completion that takes 150s. Assert that `info["last_used"]` is updated to reflect completion time, not request start time. (Will fail on unfixed code — `last_used` stays at request start)
3. **Idle Kill During Streaming Test**: Set up a session with `last_used` 130s ago, simulate active streaming. Run `_cleanup_stale_sessions_loop` logic. Assert subprocess is NOT disconnected. (Will fail on unfixed code — cleanup loop sees stale `last_used`)
4. **Retry Condition Divergence Test**: Set `_path_a_retry_count=1` and `_path_a_retried=True` with `MAX_RETRY_ATTEMPTS=2`. Evaluate both retry conditions. Assert they agree. (Will fail on unfixed code — count says "retry", boolean says "no retry")

**Expected Counterexamples**:
- Session entry missing from `_active_sessions` after retriable `error_during_execution`
- `last_used` timestamp unchanged after 150s of streaming on PATH B
- Both retry conditions returning different values for the same state

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  IF input.bug == "premature_cleanup":
    result := handle_error_during_execution_fixed(input)
    ASSERT session_still_in_active_sessions(input.eff_sid)
    ASSERT wrapper_not_disconnected(input.eff_sid)
  
  IF input.bug == "stale_timestamp":
    result := execute_on_session_inner_fixed(input)
    ASSERT info["last_used"] >= streaming_end_time
  
  IF input.bug == "inconsistent_retry":
    sdk_reader_decision := evaluate_sdk_reader_retry(input)
    ede_decision := evaluate_ede_retry(input)
    ASSERT sdk_reader_decision == ede_decision
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT handle_error_original(input) == handle_error_fixed(input)
  -- Specifically:
  -- Non-retriable errors: _cleanup_session still called, error event still yielded
  -- Interrupted sessions: still preserved
  -- Genuinely idle sessions: still disconnected
  -- Exhausted retries: error event still yielded
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many combinations of error strings, retry counts, and session states
- It catches edge cases like retry count exactly at MAX_RETRY_ATTEMPTS boundary
- It provides strong guarantees that non-buggy paths are unchanged

**Test Plan**: Observe behavior on UNFIXED code first for non-retriable errors, interrupted sessions, and idle cleanup, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Non-Retriable Error Preservation**: For any error where `_is_retriable_error` returns False, verify `_cleanup_session` is called and error event is yielded — same as unfixed code
2. **Interrupted Session Preservation**: For any session with `interrupted=True`, verify cleanup is skipped and error is suppressed — same as unfixed code
3. **Idle Cleanup Preservation**: For any session with `last_used` older than `SUBPROCESS_IDLE_SECONDS` and no active streaming, verify subprocess is disconnected — same as unfixed code
4. **Exhausted Retry Preservation**: For any error where `_retry_count >= MAX_RETRY_ATTEMPTS`, verify error event is yielded — same as unfixed code

### Unit Tests

- Test that `_cleanup_session` is NOT called when `_is_retriable_error` returns True and `_retry_count < MAX_RETRY_ATTEMPTS` in the `error_during_execution` handler
- Test that `_cleanup_session` IS called when `_is_retriable_error` returns False in the `error_during_execution` handler
- Test that `_cleanup_session` IS called when `_retry_count >= MAX_RETRY_ATTEMPTS` even for retriable errors
- Test that `last_used` is updated after PATH B streaming completes successfully
- Test that `last_used` is updated after PATH B streaming completes with error
- Test that all three error paths (SDK reader, `error_during_execution`, `is_error`) produce the same retry-eligibility decision for the same inputs
- Test edge case: `_retry_count == MAX_RETRY_ATTEMPTS` (boundary — should NOT retry)
- Test edge case: `_retry_count == MAX_RETRY_ATTEMPTS - 1` (boundary — should retry)

### Property-Based Tests

- Generate random error strings (mix of retriable patterns and non-retriable strings) and random retry counts (0 to MAX_RETRY_ATTEMPTS+1). For each combination, verify all three error paths produce the same suppress/show decision (Property 3).
- Generate random session states with varying `last_used` timestamps and streaming durations. Verify that after PATH B completion, `last_used` is always >= the completion time (Property 2).
- Generate random error scenarios with varying retriability and retry counts. Verify that `_cleanup_session` is called if and only if auto-retry will NOT handle the error (Properties 1 and 4).
- Generate random idle durations and verify that `_cleanup_stale_sessions_loop` disconnects if and only if `last_used` is older than `SUBPROCESS_IDLE_SECONDS` (Property 5).

### Integration Tests

- Test full PATH B flow: reuse client → stream for >120s → verify subprocess survives cleanup loop → send follow-up message successfully
- Test full retry flow: retriable error → session preserved → retry creates fresh client → streaming succeeds
- Test PATH B → PATH A fallback: reused client errors → evict → fresh client retry → verify `last_used` updated at each stage
- Test cascading scenario (Bug 1 + Bug 3): retriable error in `error_during_execution` → verify session preserved AND retry condition consistent → retry succeeds
