# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Fault Condition** - Error ResultMessages Yielded as Assistant Events
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists
  - **Scoped PBT Approach**: Scope the property to concrete failing cases: `ResultMessage(is_error=True, subtype=None)` with various error texts (auth errors, general errors)
  - Create test file `backend/tests/test_property_auth_error_fault.py`
  - Mock the SDK message stream to yield `ResultMessage` objects with `is_error=True` and `subtype != 'error_during_execution'`
  - Use Hypothesis to generate arbitrary error text strings for `ResultMessage.result`
  - Test concrete auth error case: `ResultMessage(is_error=True, result="Not logged in · Please run /login", subtype=None, total_cost_usd=0)`
  - Test concrete general error case: `ResultMessage(is_error=True, result="Rate limit exceeded", subtype=None)`
  - Property assertion: for all `ResultMessage` where `is_error=True` and `subtype != 'error_during_execution'`, the yielded SSE events MUST contain `type: "error"` and MUST NOT contain `type: "assistant"` with the error text
  - Also assert: session MUST NOT be stored in `_active_sessions` after an error ResultMessage
  - From Fault Condition in design: `isBugCondition(message) = message.is_error == True AND message.subtype != 'error_during_execution'`
  - From Expected Behavior in design: such messages should yield SSE `error` events, not `assistant` events
  - Run test on UNFIXED code - expect FAILURE (this confirms the bug exists)
  - **EXPECTED OUTCOME**: Test FAILS because unfixed code yields `type: "assistant"` for `is_error=True` messages
  - Document counterexamples found (e.g., "ResultMessage(is_error=True, result='Not logged in') yielded as assistant instead of error")
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 2.1, 2.2_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Non-Error ResultMessages and Existing Error Handling Unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - Create test file `backend/tests/test_property_auth_error_preservation.py`
  - Observe on UNFIXED code: `ResultMessage(is_error=False, result="Hello world")` yields `type: "assistant"` with the result text
  - Observe on UNFIXED code: `ResultMessage(subtype='error_during_execution', result="Tool failed")` yields `type: "error"` and triggers session cleanup
  - Observe on UNFIXED code: successful conversation stores session in `_active_sessions`
  - Write property-based test with Hypothesis: for all `ResultMessage` where `is_error=False` and `result` is non-empty text, the yielded SSE events MUST contain `type: "assistant"` with the result text (from Preservation Requirements in design)
  - Write property-based test: for all `ResultMessage` where `subtype='error_during_execution'`, the yielded SSE events MUST contain `type: "error"` and session MUST be cleaned from `_active_sessions` (from Preservation Requirements 3.2)
  - Write test: Bedrock-configured environments (no API key, `use_bedrock=True`) must pass pre-flight validation without error (from Preservation Requirements 3.4)
  - Write test: `ResultMessage` with `is_error=False` must persist content via `assistant_content.add()` (from Preservation Requirements 3.1)
  - Verify all tests pass on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 3. Fix for auth error ResultMessages yielded as assistant events

  - [x] 3.1 Add `is_error` detection in `_run_query_on_client` ResultMessage handling
    - In `backend/core/agent_manager.py`, in the `_run_query_on_client` method's `ResultMessage` handling block
    - After the existing `subtype == 'error_during_execution'` check and BEFORE the `result_text` yield, add a check for `message.is_error == True`
    - When `is_error=True` (and subtype is not `error_during_execution`), classify the error:
      - Auth error detection: check if error text matches patterns like "not logged in", "please run /login", "invalid api key", "authentication"
      - For auth errors: yield `{"type": "error", "error": "Authentication failed. Please configure your API key in Settings or run /login."}` with user-friendly message
      - For non-auth errors: yield `{"type": "error", "error": <raw error text>}` as a general error fallback
    - Skip `assistant_content.add()` when `is_error=True` — error text must NOT be persisted as an assistant message
    - Set `session_context["had_error"] = True` to signal error state to `_execute_on_session`
    - _Bug_Condition: isBugCondition(message) where message.is_error == True AND message.subtype != 'error_during_execution'_
    - _Expected_Behavior: yield SSE event with type "error" containing error text, NOT type "assistant"_
    - _Preservation: ResultMessages with is_error=False must continue yielding as "assistant" events unchanged_
    - _Requirements: 1.1, 2.1, 2.2, 3.1, 3.5_

  - [x] 3.2 Add conditional session storage in `_execute_on_session`
    - In `backend/core/agent_manager.py`, in the `_execute_on_session` method
    - After the `_run_query_on_client` loop, before storing in `_active_sessions`, check `session_context.get("had_error")`
    - If `had_error` is True, disconnect the wrapper via `wrapper.__aexit__()` instead of storing the session
    - If `had_error` is False (or not set), store in `_active_sessions` as before (preserve existing behavior)
    - _Bug_Condition: session stored in _active_sessions despite is_error=True ResultMessage_
    - _Expected_Behavior: failed sessions are cleaned up, not stored for reuse_
    - _Preservation: successful sessions must continue to be stored in _active_sessions for resume_
    - _Requirements: 1.2, 2.2, 3.3_

  - [x] 3.3 Add pre-flight auth validation in `_configure_claude_environment`
    - In `backend/core/claude_environment.py`, in the `_configure_claude_environment` function
    - After reading API settings and before returning, check if at least one auth method is configured:
      - `has_api_key = api_settings.get("anthropic_api_key") or settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")`
      - `use_bedrock = api_settings.get("use_bedrock", False) or settings.claude_code_use_bedrock`
    - If `not has_api_key and not use_bedrock`, raise a specific `AuthenticationNotConfiguredError` exception
    - Define `AuthenticationNotConfiguredError` (can be a simple subclass of `Exception`) in `claude_environment.py`
    - In `_execute_on_session`, catch `AuthenticationNotConfiguredError` and yield `{"type": "error", "error": "No API key configured. Please add your Anthropic API key in Settings or enable Bedrock authentication."}` before creating the client
    - _Bug_Condition: isMissingAuthCondition(api_settings, env) where NOT has_api_key AND NOT use_bedrock_
    - _Expected_Behavior: early error event before SDK round-trip_
    - _Preservation: Bedrock auth flows must continue to work without ANTHROPIC_API_KEY (Requirement 3.4)_
    - _Requirements: 1.3, 2.3, 3.4_

  - [x] 3.4 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Error ResultMessages Yield Error SSE Events
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior
    - When this test passes, it confirms the expected behavior is satisfied
    - Run `cd backend && pytest tests/test_property_auth_error_fault.py -v`
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2_

  - [x] 3.5 Verify preservation tests still pass
    - **Property 2: Preservation** - Non-Error ResultMessages and Existing Error Handling Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run `cd backend && pytest tests/test_property_auth_error_preservation.py -v`
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all tests still pass after fix (no regressions)

- [x] 4. Checkpoint - Ensure all tests pass
  - Run full test suite: `cd backend && pytest tests/test_property_auth_error_fault.py tests/test_property_auth_error_preservation.py -v`
  - Ensure all property-based tests pass
  - Ensure no regressions in existing tests: `cd backend && pytest --timeout=30 -x`
  - Ask the user if questions arise
