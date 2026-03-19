# Session Eviction Context Loss — Bugfix Design

## Overview

When a chat tab is evicted (IDLE → DEAD → COLD) to free a concurrency slot, the user permanently loses conversation context. Two interacting bugs prevent the SDK `--resume` flag from being passed on re-spawn:

1. `_cleanup_internal()` in `session_unit.py` clears `_sdk_session_id` — the only key needed to resume.
2. `run_conversation()` in `session_router.py` gates `resume_session_id` on `unit.is_alive`, which is always `False` after eviction.

The fix preserves `_sdk_session_id` across eviction cleanup and removes the `is_alive` gate so the resume ID is always passed when available.

## Glossary

- **Bug_Condition (C)**: A SessionUnit has been evicted (killed to free a concurrency slot) and the user returns to that tab — the combination of `_sdk_session_id` being cleared and `is_alive` gating prevents resume.
- **Property (P)**: When a user returns to an evicted tab that previously had a conversation, the subprocess spawns with `--resume <sdk_session_id>` and restores full conversation context.
- **Preservation**: Alive subprocess reuse, fresh tab spawning, non-retriable crash cleanup, retry loop resume capture, and shutdown disconnect_all behavior must all remain unchanged.
- **`_cleanup_internal()`**: Method in `session_unit.py` (line ~949) that resets internal fields after subprocess death during DEAD → COLD transition.
- **`run_conversation()`**: Method in `session_router.py` (line ~178) that builds SDK options and delegates to `SessionUnit.send()`.
- **`_sdk_session_id`**: The SDK-assigned session identifier captured from the init message — the ONLY key that enables `--resume` to restore conversation context.
- **Eviction**: The process where `SessionRouter._evict_idle()` kills an IDLE unit to free a concurrency slot for another tab.

## Bug Details

### Bug Condition

The bug manifests when a user returns to a tab whose SessionUnit was previously evicted. Two independent defects conspire to guarantee that `--resume` is never passed:

1. `_cleanup_internal()` sets `_sdk_session_id = None`, destroying the resume key.
2. `run_conversation()` evaluates `unit._sdk_session_id if unit.is_alive else None`, which always yields `None` for an evicted (COLD) unit.

**Formal Specification:**
```
FUNCTION isBugCondition(unit, action)
  INPUT: unit of type SessionUnit, action of type UserAction
  OUTPUT: boolean

  RETURN action == "send_message"
         AND unit.state == SessionState.COLD
         AND unit._sdk_session_id_before_eviction IS NOT None
         AND unit._sdk_session_id == None  -- cleared by _cleanup_internal
         AND unit.is_alive == False         -- always False when COLD
END FUNCTION
```

### Examples

- **Evicted tab resume (primary bug)**: User has tabs A and B active (MAX_CONCURRENT=2). User opens tab C → tab A (oldest IDLE) is evicted. User returns to tab A and sends a message → subprocess spawns WITHOUT `--resume` → blank conversation. Expected: subprocess spawns WITH `--resume <sdk_session_id>` → full conversation history restored.

- **Evicted tab after multiple turns**: User has a 20-turn conversation on tab A, switches to tabs B and C (evicting A), returns to A → all 20 turns of context are lost. Expected: `--resume` restores the full 20-turn conversation.

- **Double eviction**: Tab A is evicted, user returns (resumes successfully), tab A is evicted again, user returns again → should resume with the NEW `_sdk_session_id` from the resumed session.

- **Fresh tab (non-bug)**: User opens a brand new tab with no prior conversation → `_sdk_session_id` is `None` → subprocess spawns fresh without `--resume`. This is correct behavior and must be preserved.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Alive subprocess reuse: When a SessionUnit is IDLE and `send()` is called, the existing subprocess is reused without spawning a new one (Req 3.1)
- Fresh tab spawning: A COLD SessionUnit with no prior `_sdk_session_id` spawns a fresh subprocess without `--resume` (Req 3.2)
- Non-retriable crash cleanup: When a non-retriable error causes DEAD → COLD, ALL internal state including `_sdk_session_id` is cleared so the next conversation starts fresh (Req 3.3)
- Retry loop resume capture: The retry loop in `send()` captures `resume_session_id = self._sdk_session_id` BEFORE calling `_cleanup_internal()`, so retries continue to pass `--resume` using the captured value (Req 3.4)
- Shutdown disconnect_all: `disconnect_all()` fully cleans up all units including clearing `_sdk_session_id` (Req 3.5)

**Scope:**
All code paths that do NOT involve the eviction→return flow should be completely unaffected by this fix. This includes:
- Normal send/receive on alive subprocesses
- Retry loops (already capture `_sdk_session_id` before cleanup)
- Non-retriable crash recovery (must still clear `_sdk_session_id`)
- Shutdown cleanup
- Health check dead-process detection
- MCP hot-swap reclaim
- Interrupt flows

## Hypothesized Root Cause

Based on code analysis, the root causes are confirmed (not hypothesized):

1. **`_cleanup_internal()` over-clears state** (`session_unit.py` line ~949): The method treats `_sdk_session_id` as a transient subprocess resource and clears it alongside `_client` and `_wrapper`. But `_sdk_session_id` is the session's *identity* — it survives subprocess death and is needed to resume. The fix: stop clearing `_sdk_session_id` in `_cleanup_internal()`.

2. **`run_conversation()` gates resume on `is_alive`** (`session_router.py` line ~222): The expression `unit._sdk_session_id if unit.is_alive else None` assumes that only alive units should resume. But the entire point of `--resume` is to restore context for a DEAD/COLD unit. The fix: use `unit._sdk_session_id` unconditionally.

3. **No explicit "eviction cleanup" vs "crash cleanup" distinction**: `_cleanup_internal()` is called from both eviction paths (via `kill()`) and crash paths (non-retriable errors in `send()`). The fix must differentiate: eviction preserves `_sdk_session_id`, non-retriable crashes clear it, and `disconnect_all` clears it.

## Correctness Properties

Property 1: Bug Condition — Evicted Tab Resumes With Context

_For any_ SessionUnit that was evicted (killed to free a concurrency slot) and previously had a non-None `_sdk_session_id`, when the user returns and sends a message, the fixed code SHALL pass that `_sdk_session_id` as the `resume_session_id` to `PromptBuilder.build_options()`, resulting in a subprocess spawned with `--resume`.

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation — Non-Eviction Behavior Unchanged

_For any_ input that does NOT involve the eviction→return flow (alive subprocess reuse, fresh tabs, non-retriable crashes, retry loops, shutdown), the fixed code SHALL produce exactly the same behavior as the original code, preserving all existing functionality.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

## Fix Implementation

### Changes Required

**File**: `backend/core/session_unit.py`

**Function**: `_cleanup_internal()`

**Specific Changes**:
1. **Preserve `_sdk_session_id` in `_cleanup_internal()`**: Remove the `self._sdk_session_id = None` line from `_cleanup_internal()`. This method is called during eviction (via `kill()`), retry cleanup, and health-check dead-process detection — all paths where the session identity should survive for potential resume.

2. **Add `_full_cleanup()` method**: Create a new method that calls `_cleanup_internal()` AND clears `_sdk_session_id`. This is used for:
   - Non-retriable crash cleanup in `send()` (DEAD → COLD after unrecoverable error)
   - Exhausted retries cleanup in `send()` (all retries failed)

3. **Update non-retriable crash paths in `send()`**: Replace `_cleanup_internal()` with `_full_cleanup()` in the two non-retriable error paths:
   - Non-retriable spawn failure (line ~260)
   - Non-retriable streaming error (line ~340)
   - All-retries-exhausted (line ~330)

**File**: `backend/core/session_router.py`

**Function**: `run_conversation()`

**Specific Changes**:
4. **Remove `is_alive` gate on `resume_session_id`**: Change line ~222 from:
   ```python
   resume_session_id=unit._sdk_session_id if unit.is_alive else None,
   ```
   to:
   ```python
   resume_session_id=unit._sdk_session_id,
   ```
   This ensures that if we have a session ID (from a prior conversation), we always pass it for resume, regardless of subprocess state.

**File**: `backend/core/session_router.py`

**Function**: `disconnect_all()`

**Specific Changes**:
5. **Clear `_sdk_session_id` during shutdown**: After killing each unit in `disconnect_all()`, explicitly clear `_sdk_session_id` to ensure full cleanup during shutdown (Req 3.5). Since `kill()` calls `_cleanup_internal()` which no longer clears `_sdk_session_id`, we need this explicit step.


## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm the root cause analysis by observing that `_sdk_session_id` is `None` after eviction and that `run_conversation()` never passes a resume ID for evicted units.

**Test Plan**: Write unit tests that simulate the eviction→return flow and assert on `_sdk_session_id` state and `resume_session_id` passed to `build_options()`. Run these tests on the UNFIXED code to observe failures.

**Test Cases**:
1. **SDK Session ID Survival Test**: Set `_sdk_session_id` on a unit, call `kill()` (which calls `_cleanup_internal()`), assert `_sdk_session_id` is preserved (will fail on unfixed code — it gets cleared)
2. **Resume ID Passed After Eviction Test**: Mock `build_options()`, evict a unit, call `run_conversation()`, assert `resume_session_id` is the original SDK session ID (will fail on unfixed code — `is_alive` gate returns `None`)
3. **End-to-End Eviction Resume Test**: Simulate full eviction→return flow, assert the spawned subprocess receives `--resume` flag (will fail on unfixed code)

**Expected Counterexamples**:
- `_sdk_session_id` is `None` after `_cleanup_internal()` runs during eviction
- `resume_session_id` passed to `build_options()` is `None` because `is_alive` is `False` for COLD units
- Possible causes confirmed: `_cleanup_internal()` over-clears, `is_alive` gate blocks resume

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL unit WHERE isBugCondition(unit, "send_message") DO
  result := run_conversation_fixed(unit, message)
  ASSERT resume_session_id_passed == unit._sdk_session_id_before_eviction
  ASSERT subprocess_spawned_with_resume_flag == True
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL (unit, action) WHERE NOT isBugCondition(unit, action) DO
  ASSERT behavior_fixed(unit, action) == behavior_original(unit, action)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many combinations of unit states and actions automatically
- It catches edge cases in state machine transitions that manual tests might miss
- It provides strong guarantees that non-eviction paths are unchanged

**Test Plan**: Observe behavior on UNFIXED code first for all non-eviction paths, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Alive Subprocess Reuse Preservation**: Verify that IDLE units with alive subprocesses continue to reuse them without spawning (Req 3.1)
2. **Fresh Tab Preservation**: Verify that COLD units with `_sdk_session_id=None` spawn fresh subprocesses without `--resume` (Req 3.2)
3. **Non-Retriable Crash Cleanup Preservation**: Verify that non-retriable errors still clear `_sdk_session_id` via `_full_cleanup()` so the next conversation starts fresh (Req 3.3)
4. **Retry Loop Resume Preservation**: Verify that the retry loop still captures `_sdk_session_id` before cleanup and passes it on retry (Req 3.4)
5. **Shutdown Cleanup Preservation**: Verify that `disconnect_all()` still fully cleans up including `_sdk_session_id` (Req 3.5)

### Unit Tests

- Test `_cleanup_internal()` preserves `_sdk_session_id` after fix
- Test `_full_cleanup()` clears `_sdk_session_id`
- Test `run_conversation()` passes `_sdk_session_id` unconditionally (when non-None)
- Test `run_conversation()` passes `None` for fresh tabs (no prior `_sdk_session_id`)
- Test non-retriable crash path calls `_full_cleanup()` (clears `_sdk_session_id`)
- Test retry-exhausted path calls `_full_cleanup()` (clears `_sdk_session_id`)
- Test `disconnect_all()` clears `_sdk_session_id` on all units
- Test eviction→return→eviction→return (double eviction) preserves the latest `_sdk_session_id`

### Property-Based Tests

- Generate random sequences of (evict, return, send) actions and verify `_sdk_session_id` is always passed when available
- Generate random SessionUnit states and verify `_cleanup_internal()` never clears `_sdk_session_id`
- Generate random non-eviction action sequences (alive reuse, fresh spawn, crash, retry) and verify behavior matches original code
- Generate random interleaving of eviction and non-retriable crashes to verify `_sdk_session_id` is cleared only on crashes

### Integration Tests

- Test full eviction→return flow with mocked SDK client verifying `--resume` flag is set
- Test concurrency cap enforcement (MAX_CONCURRENT=2) with eviction and resume across 3+ tabs
- Test that resumed sessions receive the correct conversation context (mock SDK response)
