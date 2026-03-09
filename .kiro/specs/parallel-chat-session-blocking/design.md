<!-- PE-REVIEWED -->
# Parallel Chat Session Blocking Bugfix Design

## Overview

The per-session concurrency lock in `_execute_on_session()` incorrectly falls back to `agent_id` (e.g., `"default"`) as the lock key when both `app_session_id` and `session_id` are `None` for new chat sessions. This causes all new sessions for the same agent to share a single `asyncio.Lock`, so the second concurrent new session is immediately rejected with `SESSION_BUSY`. The fix generates a unique ephemeral lock key (UUID) for new sessions, preserving the double-send protection for resumed sessions while allowing independent new sessions to run in parallel. Additionally, `run_skill_creator_conversation()` lacks any concurrency guard and should be wrapped with the same pattern.

## Glossary

- **Bug_Condition (C)**: Two or more new chat sessions (both `app_session_id` and `session_id` are `None`) for the same `agent_id` are initiated concurrently — they collide on the same lock key.
- **Property (P)**: Each new session gets a unique lock key so it proceeds independently; only requests on the *same* session are serialized.
- **Preservation**: Double-send protection for resumed sessions (same `session_id` or `app_session_id`) must remain unchanged. Lock cleanup, error handling, and all non-lock-related behavior must be unaffected.
- **`_execute_on_session()`**: The method in `backend/core/agent_manager.py` that wraps session execution with a per-session concurrency lock.
- **`_get_session_lock()`**: Returns (or lazily creates) an `asyncio.Lock` keyed by session ID in `self._session_locks`.
- **`lock_key`**: The string used to index into `self._session_locks`. Currently computed as `app_session_id or session_id or agent_id`.
- **`run_skill_creator_conversation()`**: Skill creation entry point that bypasses `_execute_on_session()` entirely, lacking any concurrency guard.

## Bug Details

### Fault Condition

The bug manifests when a user opens a second new chat tab and sends a message while the first new chat tab is still streaming. Both sessions compute `lock_key = app_session_id or session_id or agent_id`, and since both `app_session_id` and `session_id` are `None` for new sessions, both resolve to the same `agent_id` string (e.g., `"default"`). The second session finds the lock already held and is immediately rejected with `SESSION_BUSY`.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type SessionRequest {app_session_id, session_id, agent_id}
  OUTPUT: boolean

  lock_key := input.app_session_id OR input.session_id OR input.agent_id
  RETURN input.app_session_id IS None
         AND input.session_id IS None
         AND lock_key == input.agent_id
         AND _session_locks[lock_key].locked() == True
END FUNCTION
```

### Examples

- **Example 1**: User opens Tab A (new chat, agent="default", session_id=None) and sends a message. While streaming, user opens Tab B (new chat, agent="default", session_id=None) and sends a message. Both compute `lock_key = "default"`. Tab B gets `SESSION_BUSY`. **Expected**: Tab B should proceed independently.
- **Example 2**: User opens Tab A (new chat, agent="coding-agent", session_id=None) and sends a message. While streaming, user opens Tab B (new chat, agent="coding-agent", session_id=None). Both compute `lock_key = "coding-agent"`. Tab B gets `SESSION_BUSY`. **Expected**: Tab B should proceed independently.
- **Example 3**: User has a resumed session (session_id="abc-123") streaming. User opens a new tab (session_id=None, agent="default"). New tab computes `lock_key = "default"`, which doesn't collide with "abc-123". **Expected**: This works correctly today — no collision. But if the resumed session also fell back to agent_id (e.g., its app_session_id was None and session_id was None at some point), it would collide.
- **Edge case**: User double-clicks Send on the same resumed session (session_id="abc-123"). Both requests compute `lock_key = "abc-123"`. Second request gets `SESSION_BUSY`. **Expected**: This is correct behavior and must be preserved.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Double-send protection: when the same `session_id` or `app_session_id` sends a concurrent request, the system must reject it with `SESSION_BUSY`
- Lock cleanup in `_cleanup_session()` must continue to remove locks from `self._session_locks` for completed/errored sessions
- Error handling paths (auth failures, credential expiry, conversation errors) must continue to clean up sessions identically
- The `_get_session_lock()` lazy-creation pattern must remain intact for all non-ephemeral lock keys
- Session reuse, resume-fallback, and client storage logic must be completely unaffected
- All event streaming (session_start, result, context_warning, error) must be unchanged

**Scope:**
All inputs where `app_session_id` or `session_id` is not `None` (i.e., resumed sessions) should be completely unaffected by this fix. This includes:
- Resumed sessions with a known `session_id`
- Resumed sessions with a known `app_session_id`
- Double-send on any session with an established ID
- All `_execute_on_session_inner()` logic (unchanged — only the lock key computation in the outer method changes)
- `continue_with_cmd_permission()` — does not call `_execute_on_session()` at all (signals the waiting hook directly), so it is unaffected

**Concurrency Note:**
The global `_env_lock` serializes environment configuration and subprocess creation across all sessions. Even after this fix, two parallel new sessions will briefly serialize during `ClaudeSDKClient` creation (subprocess spawn). This is by design to prevent environment variable races and does not affect the fix — sessions proceed independently once the subprocess is created. Testers should expect a brief sequential phase during client creation, followed by fully parallel execution.

## Hypothesized Root Cause

Based on the code analysis, the root cause is confirmed (not just hypothesized):

1. **Overly broad lock key fallback**: The line `lock_key = app_session_id or session_id or agent_id` at line ~1373 of `_execute_on_session()` falls back to `agent_id` when both IDs are `None`. Since `agent_id` is shared across all sessions of the same agent (e.g., `"default"`), this creates a global lock per agent rather than a per-session lock.

2. **New sessions lack identity at lock time**: When a user starts a new chat, neither `app_session_id` nor `session_id` is assigned yet — the SDK assigns `session_id` later via the `init` message. The lock is acquired *before* the session gets its identity, so there's no unique key available from the existing parameters.

3. **Missing concurrency guard in `run_skill_creator_conversation()`**: This method directly manages its own client lifecycle without going through `_execute_on_session()`, so it has zero protection against double-send on the same skill-creator session.

4. **Ephemeral lock not cleaned up**: If we introduce a UUID-based ephemeral lock key, it will accumulate in `self._session_locks` since `_cleanup_session()` only cleans up locks keyed by the effective session ID. The ephemeral key needs explicit cleanup after the session execution completes.

5. **Interrupt safety**: `interrupt_session()` calls `client.interrupt()` which causes the inner generator in `_execute_on_session_inner()` to finish, releasing the `async with session_lock:` block. For ephemeral locks, the `finally` cleanup will fire correctly after interrupt. No special handling needed.

## Correctness Properties

Property 1: Fault Condition - Parallel New Sessions Proceed Independently

_For any_ two concurrent `_execute_on_session()` calls where both `app_session_id` and `session_id` are `None` and `agent_id` is the same, the fixed function SHALL assign each call a unique lock key (UUID) so that neither call blocks the other with `SESSION_BUSY`.

**Validates: Requirements 2.1, 2.2**

Property 2: Preservation - Double-Send Protection for Resumed Sessions

_For any_ `_execute_on_session()` call where `app_session_id` or `session_id` is not `None`, the fixed function SHALL use the same lock key as the original function (`app_session_id or session_id`), preserving the `SESSION_BUSY` rejection for concurrent requests on the same session.

**Validates: Requirements 3.1, 3.2**

Property 3: Preservation - Lock Cleanup and Resource Management

_For any_ session that completes (normally or via error), the fixed function SHALL clean up the ephemeral lock key from `self._session_locks` after execution, and `_cleanup_session()` SHALL continue to clean up locks for non-ephemeral keys exactly as before.

**Validates: Requirements 3.3, 3.4**

Property 4: Fault Condition - Skill Creator Concurrency Guard

_For any_ concurrent `run_skill_creator_conversation()` calls on the same skill-creator session (same `session_id`), the fixed function SHALL apply a per-session concurrency guard that rejects the duplicate with `SESSION_BUSY`, matching the pattern used by `_execute_on_session()`. New skill-creator sessions (session_id=None) SHALL use ephemeral UUID lock keys to allow parallel creation.

**Validates: Requirement 2.3**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `backend/core/agent_manager.py`

**Function**: `_execute_on_session()`

**Specific Changes**:

1. **Generate ephemeral UUID lock key for new sessions**: When both `app_session_id` and `session_id` are `None`, generate a `uuid.uuid4()` string as the lock key instead of falling back to `agent_id`. This ensures each new session gets its own independent lock.
   - Change: `lock_key = app_session_id or session_id or str(uuid.uuid4())`
   - This preserves the existing behavior for resumed sessions (where `app_session_id` or `session_id` is set)

2. **Clean up ephemeral lock after execution**: Since the UUID-based lock key is ephemeral (not tied to a persistent session ID), it won't be cleaned up by `_cleanup_session()`. Add a `try/finally` block around the `async with session_lock:` to pop the ephemeral key from `self._session_locks` after execution completes.
   - Only clean up when the lock key was ephemeral (i.e., neither `app_session_id` nor `session_id` was provided)
   - Non-ephemeral keys continue to be cleaned up by `_cleanup_session()` as before

3. **`uuid4` is already imported**: The module already has `from uuid import uuid4` at line 30 — no new import needed.

**Function**: `run_skill_creator_conversation()`

**Specific Changes**:

4. **Add per-session concurrency guard**: Wrap the skill-creator execution with the same lock pattern used by `_execute_on_session()`. Use `session_id` as the lock key for resumed sessions, and a UUID for new sessions.
   - Compute `lock_key = session_id or str(uuid4())`
   - `is_ephemeral_lock = (session_id is None)` — simpler than `_execute_on_session` since skill creator has no `app_session_id` parameter. The skill creator's `session_id` is the SDK-assigned session ID (captured from the `session_start` SSE event on the frontend's SkillsPage), not a tab/app session ID. First call is always `None` (new creation); follow-ups pass the SDK session ID for resume.
   - Check `session_lock.locked()` and yield `SESSION_BUSY` error if already held
   - Acquire lock with `async with session_lock:` around the existing try/except block
   - Clean up ephemeral lock key in a `finally` block

5. **Track ephemeral status**: In `_execute_on_session()`, use `is_ephemeral_lock = (app_session_id is None and session_id is None)`. In `run_skill_creator_conversation()`, use `is_ephemeral_lock = (session_id is None)` since that method has no `app_session_id` parameter. This correctly identifies ephemeral keys in both contexts.

6. **Add observability logging**: Log when an ephemeral UUID lock key is generated (with the UUID value) and when a stable session-based key is used. This aids debugging in production when investigating session concurrency issues.
   - Example: `logger.info(f"Using ephemeral lock key {lock_key} for new session (agent={agent_id})")`
   - Example: `logger.debug(f"Using stable lock key {lock_key} for session")`

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Fault Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write async tests that simulate two concurrent `_execute_on_session()` calls with `app_session_id=None` and `session_id=None` for the same `agent_id`. Run these tests on the UNFIXED code to observe that the second call receives `SESSION_BUSY`.

**Test Cases**:
1. **Parallel New Sessions Test**: Launch two concurrent `_execute_on_session()` calls with `session_id=None`, `app_session_id=None`, `agent_id="default"`. Assert the second call gets `SESSION_BUSY` (will pass on unfixed code — confirming the bug exists).
2. **Same Agent Different Tabs Test**: Simulate two new chat tabs for agent `"coding-agent"` sending messages concurrently. Assert the second is blocked (will pass on unfixed code — confirming the bug).
3. **Lock Key Collision Test**: Directly verify that `lock_key` resolves to `agent_id` when both IDs are `None` by inspecting the computed key (will pass on unfixed code — demonstrating the collision).

**Expected Counterexamples**:
- Second concurrent new session receives `SESSION_BUSY` error event
- Both sessions compute `lock_key = agent_id`, confirming the shared-lock collision
- Root cause: the `or` chain fallback to `agent_id` creates a per-agent lock instead of per-session

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := _execute_on_session_fixed(input)
  ASSERT result does NOT contain SESSION_BUSY error
  ASSERT lock_key is a unique UUID (not agent_id)
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT _execute_on_session_original(input) = _execute_on_session_fixed(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many combinations of `app_session_id`, `session_id`, and `agent_id` values
- It catches edge cases where the lock key computation might differ unexpectedly
- It provides strong guarantees that double-send protection is unchanged for resumed sessions

**Test Plan**: Observe behavior on UNFIXED code first for resumed sessions (same session_id sending concurrent requests), then write property-based tests capturing that the `SESSION_BUSY` rejection still occurs after the fix.

**Test Cases**:
1. **Double-Send Preservation**: Verify that two concurrent requests with the same `session_id="abc-123"` still result in `SESSION_BUSY` for the second request — both before and after the fix
2. **App Session ID Preservation**: Verify that two concurrent requests with the same `app_session_id="tab-456"` still result in `SESSION_BUSY` — both before and after the fix
3. **Lock Cleanup Preservation**: Verify that `_cleanup_session()` still removes locks from `self._session_locks` for non-ephemeral keys after the fix
4. **Error Path Preservation**: Verify that sessions encountering errors still clean up from the reuse pool identically

### Unit Tests

- Test that `lock_key` is a UUID when both `app_session_id` and `session_id` are `None`
- Test that `lock_key` equals `app_session_id` when it is provided
- Test that `lock_key` equals `session_id` when `app_session_id` is `None` but `session_id` is set
- Test that ephemeral lock keys are removed from `self._session_locks` after execution completes
- Test that non-ephemeral lock keys are NOT removed by the ephemeral cleanup (left for `_cleanup_session()`)
- Test that `run_skill_creator_conversation()` rejects concurrent requests on the same `session_id` with `SESSION_BUSY`
- Test that `run_skill_creator_conversation()` allows concurrent new sessions (different UUIDs)

### Property-Based Tests

- Generate random combinations of `(app_session_id: Optional[str], session_id: Optional[str], agent_id: str)` and verify: when either ID is set, `lock_key` equals that ID; when both are `None`, `lock_key` is a valid UUID distinct from `agent_id`
- Generate pairs of concurrent session requests with random ID combinations and verify: `SESSION_BUSY` occurs if and only if both requests share the same non-None `app_session_id` or `session_id`
- Generate random session lifecycles (create, execute, cleanup) and verify `self._session_locks` does not grow unboundedly — ephemeral keys are cleaned up after execution, non-ephemeral keys are cleaned up by `_cleanup_session()`

### Integration Tests

- Test full conversation flow: start two new chat sessions concurrently via `run_conversation()` and verify both produce `result` events (no `SESSION_BUSY`)
- Test mixed scenario: one resumed session streaming + one new session starting concurrently — both should succeed
- Test skill creator: start a skill creation session, then send a concurrent request on the same session — should get `SESSION_BUSY`
- Test memory leak prevention: run multiple new sessions sequentially and verify `self._session_locks` size stays bounded
