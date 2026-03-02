# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Fault Condition** - Session ID Replacement on Resume-Fallback
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the session ID replacement cascade
  - **Scoped PBT Approach**: Scope the property to the concrete failing case: `is_resuming=True` with a `session_id` that has no entry in `_active_sessions` (simulating backend restart)
  - Create test file `backend/tests/test_property_session_persistence_fault.py`
  - Mock `ClaudeSDKClient` to simulate the SDK init handler assigning a NEW session ID (different from the original)
  - Mock `_save_message` and SSE event collection to capture all `session_start` events and message saves
  - Test `run_conversation(session_id="original-abc", ...)` with empty `_active_sessions`:
    - Assert exactly ONE `session_start` event is emitted (not two)
    - Assert the `session_start` event contains `session_id="original-abc"` (not the SDK's new ID)
    - Assert user message is saved exactly ONCE under `"original-abc"`
    - Assert assistant response is saved under `"original-abc"`
    - Assert `_active_sessions` is keyed by `"original-abc"` after completion
  - Also test `continue_with_answer` with same resume-fallback scenario (Property 6)
  - Also test `run_skill_creator_conversation` with same resume-fallback scenario (Property 7)
  - Use Hypothesis `@given` with `st.text(min_size=1)` for session IDs and message content to generalize beyond hardcoded values
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS — confirms the bug exists (double `session_start`, duplicate user message, wrong `_active_sessions` key)
  - Document counterexamples found (e.g., "Two `session_start` events: first with original ID, second with SDK-assigned ID; user message saved under both IDs")
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 2.5_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - New Conversation and In-Memory Resume Behavior
  - **IMPORTANT**: Follow observation-first methodology
  - Create test file `backend/tests/test_property_session_persistence_preservation.py`
  - **Observe on UNFIXED code first**, then write properties capturing observed behavior:
  - Observe: `run_conversation(session_id=None)` (new conversation) → SDK assigns a session ID via init handler, single `session_start` emitted with that ID, user + assistant messages saved under it
  - Observe: `run_conversation(session_id="existing-id")` with active client in `_active_sessions` (in-memory resume, no restart) → client reused, single `session_start` with `"existing-id"`, messages saved under it
  - Observe: Multiple messages within same session → each user/assistant pair saved under same session ID
  - Write property-based tests using Hypothesis:
    - **Property 3 (New Conversation)**: For all generated session-less inputs, the SDK-assigned ID is used for `session_start` and all message persistence — `app_session_id` is None, so `effective_session_id` equals `sdk_session_id`
    - **Property 4 (In-Memory Resume)**: For all generated inputs where `is_resuming=True` AND an active client exists in `_active_sessions`, the existing client is reused, single `session_start` emitted with original ID, messages saved under original ID
  - Mock `ClaudeSDKClient` and `_save_message` to capture behavior without hitting real SDK
  - Verify tests PASS on UNFIXED code (confirms baseline behavior to preserve)
  - **EXPECTED OUTCOME**: Tests PASS — confirms these non-buggy paths work correctly before the fix
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 3. Fix session ID persistence on backend restart

  - [x] 3.1 Remove eager user message save and `session_start` from `run_conversation`
    - In `run_conversation`, remove the `session_start` yield, `store_session` call, and `_save_message` call from the `if is_resuming:` block
    - These will be deferred to `_execute_on_session` after the SDK client path is determined
    - Pass `app_session_id=session_id` and `deferred_user_content=content` to `_execute_on_session` when `is_resuming=True`
    - _Bug_Condition: isBugCondition(input) where input.session_id IS NOT NULL AND _active_sessions[input.session_id] IS NULL_
    - _Expected_Behavior: User message saved exactly once under original session_id after client path is determined_
    - _Preservation: New conversations (is_resuming=False) are unaffected — they don't enter the if-block_
    - _Requirements: 1.1, 2.1, 2.3_

  - [x] 3.2 Add `app_session_id` and `deferred_user_content` parameters to `_execute_on_session`
    - Add `app_session_id: Optional[str] = None` parameter
    - Add `deferred_user_content: Optional[list[dict]] = None` parameter
    - Set `session_context["app_session_id"] = app_session_id` after creating `session_context`
    - Define helper: `effective_session_id = session_context["app_session_id"] if session_context.get("app_session_id") is not None else session_context["sdk_session_id"]`
    - _Bug_Condition: app_session_id is set when is_resuming=True, propagated through fallback_
    - _Expected_Behavior: session_context carries both app_session_id and sdk_session_id; effective_session_id resolves correctly_
    - _Preservation: When app_session_id is None (new conversations), effective_session_id falls back to sdk_session_id_
    - _Requirements: 2.1, 2.2, 3.1_

  - [x] 3.3 Emit deferred `session_start` and save user message in `_execute_on_session`
    - When `app_session_id is not None` (resumed conversation), emit `session_start` with `app_session_id`, call `store_session`, and save user message using `deferred_user_content` — exactly once
    - This applies to BOTH PATH A (fresh client, resume-fallback) and PATH B (reused client)
    - For PATH B: deferred save happens at top of PATH B before `_run_query_on_client`
    - For PATH A: deferred save happens after fallback decision but before creating new SDK client
    - _Bug_Condition: Deferred save ensures user message is saved under app_session_id regardless of which path is taken_
    - _Expected_Behavior: Exactly one session_start event with app_session_id; user message saved once under app_session_id_
    - _Preservation: When app_session_id is None, no deferred save occurs — init handler handles it as before_
    - _Requirements: 2.1, 2.2, 2.3, 2.5_

  - [x] 3.4 Key `_active_sessions` by `effective_session_id` and fix error cleanup
    - After fresh session completes, store client in `_active_sessions[effective_session_id]` instead of `_active_sessions[final_session_id]`
    - In error handler, use `effective_session_id` when cleaning up `_active_sessions`
    - _Bug_Condition: After resume-fallback, _active_sessions must be keyed by app_session_id so next resume finds the client_
    - _Expected_Behavior: _active_sessions[original_session_id] points to the client wrapper_
    - _Preservation: For new conversations, effective_session_id == sdk_session_id, so keying is unchanged_
    - _Requirements: 2.2, 2.4_

  - [x] 3.5 Override session ID in `_run_query_on_client` init handler
    - In the `init` SystemMessage handler, after capturing SDK session ID, check `session_context.get("app_session_id") is not None`
    - If so: register client in `_clients` under `app_session_id` (not `sdk_session_id`), and SKIP the `session_start` + `store_session` + `_save_message` block (already done by `_execute_on_session`)
    - If not (new conversation): keep existing behavior — emit `session_start` with SDK ID, save user message
    - _Bug_Condition: Prevents the second session_start and duplicate user message save during resume-fallback_
    - _Expected_Behavior: _clients keyed by app_session_id so continue_with_answer can find the client_
    - _Preservation: New conversations (app_session_id is None) use existing init handler logic unchanged_
    - _Requirements: 2.2, 2.3, 2.5, 3.1_

  - [x] 3.6 Use `effective_session_id` for all downstream persistence in `_run_query_on_client`
    - Use `effective_session_id` for assistant message saves (normal completion, `ask_user_question`, `cmd_permission_request` early returns)
    - Use `effective_session_id` for the `result` SSE event's `session_id` field
    - Fix `_clients` cleanup in `finally` block to pop by `effective_session_id`
    - _Bug_Condition: Ensures assistant responses are saved under original session_id during resume-fallback_
    - _Expected_Behavior: All messages (user + assistant) under same session_id_
    - _Preservation: For new conversations, effective_session_id == sdk_session_id — no change_
    - _Requirements: 2.1, 2.3_

  - [x] 3.7 Defer user answer save in `continue_with_answer`
    - Move `_save_message` for user answer from before `_execute_on_session` to after client path is determined
    - Pass `app_session_id=session_id` and `deferred_user_content=[{"type": "text", "text": f"User answers:\n{answer_message}"}]` to `_execute_on_session`
    - _Bug_Condition: Same cascade as run_conversation — eager save + fallback = duplicate user answer_
    - _Expected_Behavior: User answer saved exactly once under original session_id_
    - _Preservation: continue_with_cmd_permission is NOT affected (doesn't create SDK clients)_
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.8 Apply fix to `run_skill_creator_conversation` inline logic
    - Remove eager `session_start` + `store_session` from the `if is_resuming:` block
    - Track `app_session_id` in `session_context` when `is_resuming=True`
    - In fresh-client path (when `is_resuming` was True but no active client found): emit `session_start` with `app_session_id`, call `store_session` with `app_session_id`
    - Key `_active_sessions` by `effective_session_id` instead of `final_session_id`
    - Use `effective_session_id` for error cleanup
    - _Bug_Condition: Identical eager-save + fallback pattern as run_conversation_
    - _Expected_Behavior: Single session_start with original ID, messages under original ID, _active_sessions keyed by original ID_
    - _Preservation: New skill creator conversations (is_resuming=False) unaffected_
    - _Requirements: 2.1, 2.2, 2.5_

  - [x] 3.9 Add observability logging
    - When `app_session_id` is set and differs from `sdk_session_id`, log: `"Resume-fallback: mapping SDK session {sdk_id} → app session {app_id}"`
    - Add logging in `_execute_on_session` and `run_skill_creator_conversation` fallback paths
    - _Requirements: 2.2_

  - [x] 3.10 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Session ID Stability on Resume-Fallback
    - **IMPORTANT**: Re-run the SAME test from task 1 — do NOT write a new test
    - The test from task 1 encodes the expected behavior (single `session_start`, no duplicate messages, correct `_active_sessions` keying)
    - When this test passes, it confirms Properties 1, 2, 5, 6, and 7 are satisfied
    - Run `cd backend && pytest tests/test_property_session_persistence_fault.py -v`
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 3.11 Verify preservation tests still pass
    - **Property 2: Preservation** - New Conversation and In-Memory Resume Behavior
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run `cd backend && pytest tests/test_property_session_persistence_preservation.py -v`
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions for Properties 3 and 4)
    - Confirm all preservation tests still pass after fix (no regressions)

- [x] 4. Checkpoint — Ensure all tests pass
  - Run full test suite: `cd backend && pytest`
  - Ensure all existing tests pass (no regressions from the fix)
  - Ensure both property test files pass:
    - `test_property_session_persistence_fault.py` — PASSES (bug is fixed)
    - `test_property_session_persistence_preservation.py` — PASSES (no regressions)
  - Ask the user if questions arise
