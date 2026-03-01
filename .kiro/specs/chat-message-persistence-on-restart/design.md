<!-- PE-REVIEWED -->
# Chat Message Persistence on Restart — Bugfix Design

## Overview

When the backend restarts, it loses its in-memory `_active_sessions` dict. Tabs that try to resume conversations trigger a cascade: `run_conversation` eagerly saves the user message and emits `session_start` with the original session ID, then `_execute_on_session` discovers no active client exists and falls back to a fresh SDK session. The SDK's init handler in `_run_query_on_client` emits a *second* `session_start` with the new SDK-assigned ID, causing the frontend tab to silently switch IDs. The user message ends up duplicated across two sessions, the assistant response lands only under the new ID, and on the next restart the cycle repeats — orphaning all previous messages.

The fix defers user message persistence and `session_start` emission until after the SDK client is established, and ensures the app's original session ID is used for all persistence and frontend communication regardless of what internal ID the SDK assigns.

## Session ID Mapping Model

One chat tab has exactly one stable **App Session ID** that never changes. Over the tab's lifetime, the backend may create multiple Claude SDK clients (e.g. after restarts), each with its own **SDK Session ID**. The app layer maps all SDK session IDs back to the single app session ID for persistence and frontend communication.

```
Chat Tab (frontend, localStorage)
  └── 1 App Session ID: "9240de91..."     ← stable, assigned on first message, never replaced
        │
        ├── SDK Client #1: "9240de91..."   ← first SDK client (IDs happen to match)
        │     (backend restart — client lost)
        │
        ├── SDK Client #2: "7a3e4821..."   ← fresh SDK client, new internal ID
        │     (backend restart — client lost)
        │
        └── SDK Client #3: "fd1b01f8..."   ← another fresh SDK client, another internal ID

All messages saved under: "9240de91..." (the App Session ID)
Frontend always sees:     "9240de91..." (via session_start events)
_active_sessions keyed by: "9240de91..." (so next resume finds the client)
```

The `session_context` dict carries both IDs:
- `app_session_id`: The stable app-level ID (set when `is_resuming=True`, `None` for new conversations)
- `sdk_session_id`: The SDK's internal ID (set from the `init` SystemMessage)

A helper resolves which to use: `effective_session_id = app_session_id if app_session_id is not None else sdk_session_id`

For new conversations (no prior session), `app_session_id` is `None` and the SDK's ID becomes the app session ID — this is how the first session ID is assigned. For resumed conversations, `app_session_id` is always set, and the SDK's ID is only used internally for SDK subprocess management.

## Glossary

- **Bug_Condition (C)**: The backend restarts (losing `_active_sessions`), a tab resumes with a previously valid `session_id`, and `_execute_on_session` falls back to creating a fresh SDK client because no in-memory client exists
- **Property (P)**: All messages (user + assistant) are saved under the original `session_id`, exactly one `session_start` event is emitted with the original `session_id`, and no duplicate user messages are created
- **Preservation**: New conversations (no prior `session_id`), in-memory resume (client still alive), and multi-message sessions without restart must continue to work exactly as before
- **`run_conversation`**: The entry point in `backend/core/agent_manager.py` that orchestrates message sending — currently saves user message and emits `session_start` eagerly before calling `_execute_on_session`
- **`_execute_on_session`**: Method that checks for an active in-memory client, falls back to fresh SDK session if none found, and delegates to `_run_query_on_client`
- **`_run_query_on_client`**: Method that processes SDK messages; its `init` handler captures the SDK session ID and (for new sessions) emits `session_start` and saves the user message
- **`session_context`**: A mutable dict passed through the call chain; holds `sdk_session_id` which gets updated when the SDK's init message arrives
- **`_active_sessions`**: In-memory dict mapping session IDs to long-lived SDK client wrappers — lost on backend restart
- **`_clients`**: In-memory dict mapping session IDs to `ClaudeSDKClient` instances for active query processing — used by `continue_with_answer` and `continue_with_cmd_permission` to find the client mid-conversation
- **`continue_with_answer`**: Method that continues a conversation after an `ask_user_question` pause — also calls `_execute_on_session` with `is_resuming=True` and saves user message eagerly, so it has the same bug pattern
- **`run_skill_creator_conversation`**: Method for skill creation conversations — has the identical eager-save + fallback pattern as `run_conversation`

## Bug Details

### Fault Condition

The bug manifests when a tab resumes a conversation after a backend restart. The `run_conversation` method eagerly saves the user message and emits `session_start` before `_execute_on_session` discovers that no in-memory client exists. The fallback path creates a fresh SDK client, and the `init` handler in `_run_query_on_client` emits a second `session_start` with the SDK's new session ID, replacing the original.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type ConversationRequest
  OUTPUT: boolean
  
  RETURN input.session_id IS NOT NULL
         AND _active_sessions[input.session_id] IS NULL
         AND _get_active_client(input.session_id) RETURNS NULL
END FUNCTION
```

The condition is: `is_resuming=True` AND no in-memory client exists for the given session_id. This happens after every backend restart for every tab that had an active conversation.

### Examples

- Tab sends "hello" with session_id `abc-123` after restart → user message saved under `abc-123`, `session_start` emitted with `abc-123`, then SDK creates new session `xyz-789`, second `session_start` emitted with `xyz-789`, user message saved again under `xyz-789`, assistant response only under `xyz-789`. Tab now holds `xyz-789`.
- On next restart, tab sends "continue" with session_id `xyz-789` → same cascade, new session `def-456` created. Messages under `abc-123` and `xyz-789` are now orphaned.
- Tab sends "hello" with session_id `abc-123` when backend has NOT restarted (client still in memory) → single `session_start` with `abc-123`, messages saved correctly. No bug.
- Brand new tab sends first message (no session_id) → SDK assigns `new-001`, single `session_start` with `new-001`. No bug.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Brand-new conversations (no prior `session_id`) must continue to use the SDK-assigned session ID from the `init` message for all persistence and frontend communication
- In-memory resume (backend has NOT restarted, active client exists) must continue to reuse the existing client and emit a single `session_start` with the original session ID
- Multi-message sessions without restart must continue to save each user/assistant message pair under the same session ID in chronological order
- Frontend `loadSessionMessages` must continue to fetch messages by `session_id` from the database — no schema changes required
- The `session_start` SSE event contract (shape: `{type: "session_start", sessionId: string}`) must remain unchanged
- The `_save_message` method signature and behavior must remain unchanged — only the session ID passed to it changes

**Scope:**
All inputs where `is_resuming=False` (new conversations) or where `is_resuming=True` AND an active in-memory client exists should be completely unaffected by this fix. This includes:
- First message on a new tab (no session_id)
- Resume where the backend has not restarted (client in `_active_sessions`)
- Subsequent messages within an already-established session
- `continue_with_answer` and `continue_with_cmd_permission` flows (they use `_clients` dict, not `_active_sessions`)

## Hypothesized Root Cause

Based on code analysis of `run_conversation`, `_execute_on_session`, and `_run_query_on_client`, the root causes are:

1. **Premature User Message Save in `run_conversation`** (lines 1005-1017): When `is_resuming=True`, `run_conversation` immediately emits `session_start` and saves the user message BEFORE calling `_execute_on_session`. At this point, it doesn't know whether the resume will succeed (active client found) or fail (fallback to fresh SDK session). This creates the first copy of the user message under the original session ID.

2. **Unconditional `session_start` + Save in `_run_query_on_client` Init Handler** (lines 1459-1476): When `_execute_on_session` falls back to a fresh session (setting `is_resuming=False`), the init handler in `_run_query_on_client` treats it as a brand-new session. It emits a second `session_start` with the SDK's new session ID and saves the user message again under that new ID. This creates the duplicate user message and the ID replacement.

3. **`_active_sessions` Keyed by SDK Session ID**: After the fresh session completes, `_execute_on_session` stores the client in `_active_sessions` keyed by the SDK's `final_session_id` — not the original app session ID. So even within the same backend lifetime, the mapping is lost.

4. **No App-Level Session ID Propagation**: The `session_context` dict carries `sdk_session_id` but has no concept of an "app session ID" that should override the SDK's ID for persistence. When `_execute_on_session` resets `session_context["sdk_session_id"] = None` on fallback, the original ID is effectively discarded.

5. **Same Pattern in `continue_with_answer`**: `continue_with_answer` saves the user answer message eagerly and then calls `_execute_on_session` with `is_resuming=True`. If the backend restarted and the client is gone, the same fallback cascade occurs — duplicate user message, wrong `_active_sessions` keying, and the `_clients` dict registration in the init handler uses the SDK's ID instead of the app session ID.

6. **Same Pattern in `run_skill_creator_conversation`**: The skill creator method has an identical eager-save + fallback pattern. It emits `session_start` and stores the session before discovering the client is gone.

## Correctness Properties

Property 1: Fault Condition — Session ID Stability on Resume-Fallback

_For any_ conversation request where `is_resuming=True` and no active in-memory client exists (isBugCondition returns true), the fixed `run_conversation` + `_execute_on_session` + `_run_query_on_client` chain SHALL emit exactly one `session_start` event containing the ORIGINAL `session_id` that the frontend sent, and SHALL save both the user message and assistant response under that same original `session_id`.

**Validates: Requirements 2.1, 2.2, 2.3, 2.5**

Property 2: Fault Condition — No Duplicate User Messages

_For any_ conversation request where `is_resuming=True` and no active in-memory client exists (isBugCondition returns true), the fixed code SHALL save the user message exactly once in the database. There SHALL NOT be two rows in the `messages` table with the same user content for the same logical conversation turn.

**Validates: Requirements 2.3, 2.4**

Property 3: Preservation — New Conversation Behavior

_For any_ conversation request where `is_resuming=False` (brand-new conversation, no prior session_id), the fixed code SHALL produce exactly the same behavior as the original code: the SDK-assigned session ID from the `init` message is used for `session_start`, session storage, and message persistence.

**Validates: Requirements 3.1**

Property 4: Preservation — In-Memory Resume Behavior

_For any_ conversation request where `is_resuming=True` AND an active in-memory client exists (isBugCondition returns false), the fixed code SHALL produce exactly the same behavior as the original code: the existing client is reused, a single `session_start` is emitted with the original session ID, and messages are saved under that session ID.

**Validates: Requirements 3.2, 3.3**

Property 5: Fault Condition — Active Sessions and Clients Pool Keying

_For any_ conversation request where `is_resuming=True` and no active in-memory client exists, after the fresh SDK client is created and the conversation completes, the client SHALL be stored in `_active_sessions` keyed by the ORIGINAL app session ID (not the SDK's internal session ID). Additionally, the `_clients` dict registration in the init handler SHALL use the app session ID so that `continue_with_answer` and `continue_with_cmd_permission` can find the client.

**Validates: Requirements 2.2, 2.4**

Property 6: Fault Condition — continue_with_answer Resume-Fallback

_For any_ `continue_with_answer` call where `is_resuming=True` and no active in-memory client exists (backend restarted since the ask_user_question pause), the fixed code SHALL save the user answer exactly once under the ORIGINAL session ID, create a fresh SDK client, and use the original session ID for all downstream persistence and `_active_sessions` keying — identical guarantees to Property 1 and Property 2.

**Validates: Requirements 2.1, 2.2, 2.3**

Property 7: Fault Condition — run_skill_creator_conversation Resume-Fallback

_For any_ `run_skill_creator_conversation` call where `is_resuming=True` and no active in-memory client exists, the fixed code SHALL emit exactly one `session_start` with the original session ID, save messages under it, and key `_active_sessions` by it — identical guarantees to Property 1, Property 2, and Property 5.

**Validates: Requirements 2.1, 2.2, 2.5**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**Helper pattern used throughout**: Define `effective_session_id` as `session_context["app_session_id"] if session_context.get("app_session_id") is not None else session_context["sdk_session_id"]`. Use explicit `is not None` check — never rely on truthiness to avoid empty-string edge cases.

---

**File**: `backend/core/agent_manager.py`

**Function**: `run_conversation`

1. **Defer User Message Save and `session_start` Emission**: Remove the eager `session_start` yield, `store_session` call, and `_save_message` call from the `if is_resuming:` block. These will be handled after the SDK client path is determined.

2. **Pass `app_session_id` to `_execute_on_session`**: Add a new parameter `app_session_id: Optional[str] = None` to `_execute_on_session`. When `is_resuming=True`, pass `app_session_id=session_id`. This is necessary because `_execute_on_session` creates its own `session_context` dict internally — setting a field on a dict that doesn't exist yet from the caller would not work.

3. **Pass deferred user content**: Add a new parameter `deferred_user_content: Optional[list[dict]] = None` to `_execute_on_session`. When `is_resuming=True`, pass the user content so it can be saved after the client path is determined. This is needed because `_execute_on_session` doesn't otherwise have access to the original content for the deferred save.

**Function**: `_execute_on_session`

4. **Set `app_session_id` in `session_context`**: At the top, after creating `session_context = {"sdk_session_id": session_id}`, add `session_context["app_session_id"] = app_session_id` (from the new parameter). This ensures the app session ID is available throughout the call chain.

5. **Emit Deferred `session_start` and Save User Message**: When `app_session_id is not None` (meaning this is a resumed conversation), emit `session_start` with `app_session_id`, call `store_session`, and save the user message using `deferred_user_content` — exactly once. This applies to BOTH paths:
   - **PATH B (reused client)**: The deferred save happens immediately at the top of PATH B, before calling `_run_query_on_client`. The session ID is the original app session ID (which matches the reused client's key). This is functionally identical to the old eager save in `run_conversation`, just moved here.
   - **PATH A (fresh client, resume-fallback)**: The deferred save happens after the fallback decision but before creating the new SDK client. The session ID is the original app session ID, not the SDK's new ID.
   - For both paths, the save happens exactly once. The init handler in `_run_query_on_client` skips its own `session_start` + save when `app_session_id is not None`.

6. **Propagate `app_session_id` Through Fallback**: When the resume-fallback path resets `is_resuming=False` and `session_context["sdk_session_id"] = None`, the `app_session_id` remains intact in `session_context`. No additional flag needed — the presence of `app_session_id` itself signals resume-fallback mode.

7. **Key `_active_sessions` by `effective_session_id`**: After the fresh session completes, store the client in `_active_sessions` keyed by `effective_session_id` (which resolves to `app_session_id` during resume-fallback) instead of `final_session_id`.

8. **Fix Error Cleanup to Use `effective_session_id`**: In the error handler at the bottom of `_execute_on_session`, use `effective_session_id` (not just `sdk_session_id`) when cleaning up `_active_sessions`. This ensures error cleanup finds the correct entry after resume-fallback.

**Function**: `_run_query_on_client`

9. **Override Session ID in Init Handler**: In the `init` SystemMessage handler, after capturing the SDK session ID, check if `session_context.get("app_session_id") is not None`. If so:
   - Register the client in `_clients` under `app_session_id` (so `continue_with_answer` can find it by app session ID)
   - Skip the `session_start` + `store_session` + `_save_message` block (already done by `_execute_on_session` for resume-fallback, or by `run_conversation` for reused-client)

10. **Use `effective_session_id` for All Downstream Persistence**: Throughout `_run_query_on_client`, use `effective_session_id` for:
   - Assistant message saves (both normal completion and early-return paths for `ask_user_question` and `cmd_permission_request`)
   - The `result` SSE event's `session_id` field (line ~1620)
   - This ensures assistant responses are saved under the original session ID

11. **Fix `_clients` Cleanup in Finally Block**: The `finally` block pops from `_clients` using `sdk_session_id`. After the fix, the client may be registered under `app_session_id`. Update the cleanup to pop by `effective_session_id` so the entry is correctly removed.

12. **Add Observability Logging**: When `app_session_id` is set and differs from `sdk_session_id`, log a mapping line: `"Resume-fallback: mapping SDK session {sdk_id} → app session {app_id}"`. This is critical for debugging session ID mismatches.

**Note on `permission_request_forwarder`**: The forwarder matches on `session_context["sdk_session_id"]`, which is the SDK's internal ID. Permission requests from the SDK hook carry the SDK's session ID. This remains correct after the fix — the forwarder needs the SDK ID to match SDK-originated requests, while persistence uses the app ID. No change needed.

**Note on error handling**: If `_execute_on_session` errors out after emitting `session_start` and saving the user message (in the deferred approach), the user message is persisted but no assistant response follows. This matches the current error behavior for non-resume paths and is acceptable — the user can retry.

**Note on TSCC telemetry**: TSCC calls use `session_context["sdk_session_id"]` for state tracking. After resume-fallback, TSCC will track the SDK's internal ID while messages are under the app ID. This is acceptable — TSCC is best-effort telemetry and does not affect correctness.

---

**Function**: `continue_with_answer`

13. **Defer User Answer Save**: Move the `_save_message` call for the user answer from before `_execute_on_session` to after the client path is determined. Pass `app_session_id=session_id` and `deferred_user_content=[{"type": "text", "text": f"User answers:\n{answer_message}"}]` to `_execute_on_session` so the answer is saved under the original session ID even if resume-fallback occurs.

**Note on `continue_with_cmd_permission`**: This method does NOT call `_execute_on_session` — it calls `set_permission_decision` and returns immediately. It saves a user decision message under `session_id` which is the app session ID from the frontend. No resume-fallback can occur here because the method doesn't create a new SDK client. No change needed.

**Function**: `run_skill_creator_conversation`

14. **Apply Fix Directly to Inline Logic**: This method does NOT use `_execute_on_session` — it has its own inline client creation and message handling. Apply the same pattern directly:
   - Remove the eager `session_start` + `store_session` from the `if is_resuming:` block
   - Track `app_session_id` in `session_context` when `is_resuming=True`
   - In the fresh-client path (when `is_resuming` was True but no active client found): emit `session_start` with `app_session_id`, call `store_session` with `app_session_id`
   - Key `_active_sessions` by `effective_session_id` instead of `final_session_id`
   - Use `effective_session_id` for error cleanup

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Fault Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that simulate the resume-fallback scenario by calling `run_conversation` with a `session_id` that has no corresponding entry in `_active_sessions`. Collect all yielded SSE events and database writes. Run these tests on the UNFIXED code to observe the double `session_start` and duplicate user message.

**Test Cases**:
1. **Resume After Restart**: Call `run_conversation(session_id="existing-id")` with empty `_active_sessions` → expect two `session_start` events and duplicate user message (will fail on unfixed code by producing wrong behavior)
2. **Multiple Restarts**: Simulate two consecutive restart-resume cycles → expect session ID cascade with three different IDs (will fail on unfixed code)
3. **Assistant Response Orphaning**: After resume-fallback, check that assistant response is saved under a different session ID than the original (will fail on unfixed code)

**Expected Counterexamples**:
- Two `session_start` events yielded: first with original ID, second with SDK-assigned ID
- User message saved twice: once under original ID, once under new ID
- Assistant response saved only under new ID
- `_active_sessions` keyed by new SDK ID, not original ID

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  events := collect(run_conversation_fixed(input))
  session_start_events := filter(events, type="session_start")
  
  ASSERT len(session_start_events) == 1
  ASSERT session_start_events[0].sessionId == input.session_id
  
  user_messages := db.messages.query(role="user", content=input.message)
  ASSERT len(user_messages) == 1
  ASSERT user_messages[0].session_id == input.session_id
  
  assistant_messages := db.messages.query(role="assistant", session_id=input.session_id)
  ASSERT len(assistant_messages) >= 1
END FOR
```

**Pseudocode for expected behavior:**
```
FUNCTION expectedBehavior(result)
  INPUT: result of type ConversationResult (collected events + DB state)
  OUTPUT: boolean
  
  RETURN result.session_start_count == 1
         AND result.session_start_id == result.original_session_id
         AND result.user_message_count == 1
         AND result.user_message_session_id == result.original_session_id
         AND result.assistant_message_session_id == result.original_session_id
         AND result.active_sessions_key == result.original_session_id
END FUNCTION
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT run_conversation_original(input) = run_conversation_fixed(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain
- It catches edge cases that manual unit tests might miss
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: Observe behavior on UNFIXED code first for new conversations and in-memory resumes, then write property-based tests capturing that behavior.

**Test Cases**:
1. **New Conversation Preservation**: Verify that `run_conversation(session_id=None)` produces a single `session_start` with the SDK-assigned ID and saves messages under it — same as before
2. **In-Memory Resume Preservation**: Verify that `run_conversation(session_id="id")` with an active client in `_active_sessions` reuses the client, emits single `session_start` with original ID, saves messages under it — same as before
3. **Multi-Message Preservation**: Verify that sending multiple messages within a session saves each pair under the same session ID — same as before
4. **Continue-With-Answer Preservation**: Verify that `continue_with_answer` still works correctly using `_clients` dict

### Unit Tests

- Test `run_conversation` with `is_resuming=True` and empty `_active_sessions`: verify single `session_start` with original ID
- Test `run_conversation` with `is_resuming=True` and active client: verify single `session_start` with original ID
- Test `run_conversation` with `is_resuming=False`: verify `session_start` with SDK-assigned ID
- Test that `_active_sessions` is keyed by app session ID after resume-fallback
- Test that `_clients` dict is keyed by app session ID after resume-fallback (so `continue_with_answer` can find it)
- Test that user message is saved exactly once under original session ID after resume-fallback
- Test that assistant message is saved under original session ID after resume-fallback
- Test `continue_with_answer` with `is_resuming=True` and empty `_active_sessions`: verify user answer saved once under original ID, assistant response under original ID
- Test `run_skill_creator_conversation` with `is_resuming=True` and empty `_active_sessions`: verify single `session_start` with original ID

### Property-Based Tests

- Generate random session IDs and message content, simulate resume-fallback, verify session ID stability and no duplicates (Property 1, 2)
- Generate random new conversation inputs, verify SDK-assigned ID is used correctly (Property 3)
- Generate random resume inputs with active clients, verify existing behavior preserved (Property 4)
- Generate random session IDs, simulate resume-fallback + completion, verify `_active_sessions` and `_clients` keying (Property 5)
- Generate random `continue_with_answer` inputs with resume-fallback, verify same guarantees as Property 1/2 (Property 6)
- Generate random `run_skill_creator_conversation` inputs with resume-fallback, verify same guarantees (Property 7)

### Integration Tests

- Full end-to-end: start conversation → restart backend (clear `_active_sessions`) → resume conversation → verify messages load correctly under original session ID
- Multi-tab: two tabs resume after restart → verify each tab's messages stay under their respective original session IDs
- Chain: restart → resume → send another message → restart again → resume again → verify all messages under original session ID across multiple restart cycles
