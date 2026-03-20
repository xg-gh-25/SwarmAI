# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** — NameError on ResultMessage with Usage Data
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the NameError crash in `_read_formatted_response()`
  - **Scoped PBT Approach**: Scope the property to concrete failing cases:
    - Create a `SessionUnit` in STREAMING state with a mocked `_client.receive_response()` that yields a `ResultMessage` with `usage={"input_tokens": N}` where N > 0
    - Use Hypothesis to generate `input_tokens` values (positive integers) and optional model names
    - Assert that iterating `_read_formatted_response()` does NOT raise `NameError`
    - Assert that the unit transitions STREAMING→IDLE after processing the `ResultMessage`
    - Assert that a `result` event is yielded with correct usage data
  - The bug condition from design: `isBugCondition(input) := input_tokens IS NOT None AND input_tokens > 0 AND "options" NOT IN local_scope_of(_read_formatted_response)`
  - On UNFIXED code: `NameError: name 'options' is not defined` is raised at the context warning bridge (~line 887)
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct — it proves the bug exists)
  - Document counterexamples found (e.g., "ResultMessage with usage={'input_tokens': 1500} raises NameError instead of completing STREAMING→IDLE")
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 2.1, 2.2, 2.3_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** — Non-ResultMessage Processing and No-Usage ResultMessages Unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - **Observe on UNFIXED code**:
    - `AssistantMessage` with `TextBlock` content yields `{"type": "assistant", "content": [{"type": "text", ...}]}`
    - `AssistantMessage` with `ToolUseBlock` content yields `{"type": "assistant", "content": [{"type": "tool_use", ...}]}`
    - `AssistantMessage` with `ToolResultBlock` content yields `{"type": "assistant", "content": [{"type": "tool_result", ...}]}`
    - `SystemMessage` with `subtype="init"` yields `{"type": "session_start", "sessionId": ...}`
    - `StreamEvent` with `content_block_delta` / `text_delta` yields `{"type": "text_delta", ...}`
    - `StreamEvent` with `content_block_start` / `thinking` yields `{"type": "thinking_start", ...}`
    - `ResultMessage` with `usage=None` yields `{"type": "result", ...}` and transitions STREAMING→IDLE without error
    - `ResultMessage` with `usage={}` yields `{"type": "result", ...}` and transitions STREAMING→IDLE without error
    - `ResultMessage` with `usage={"input_tokens": 0}` yields `{"type": "result", ...}` and transitions STREAMING→IDLE without error
  - Write property-based tests capturing observed behavior:
    - Generate random message sequences (AssistantMessage, SystemMessage, StreamEvent) followed by a ResultMessage with no/zero usage
    - Assert each message type yields the expected SSE event format
    - Assert ResultMessage with `usage=None`, `usage={}`, or `input_tokens=0` completes without NameError and transitions STREAMING→IDLE
    - Assert non-ResultMessage processing is identical before and after fix
  - Property-based testing generates many combinations of message types and content blocks for stronger preservation guarantees
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.6, 3.7_

- [x] 3. Implement chat session stability fix

  - [x] 3.1 Change 1 & 2: Store model name on SessionUnit and fix context warning bridge
    - In `SessionUnit.__init__()`, add `self._model_name: Optional[str] = None` in the internal fields section
    - In `SessionUnit.send()`, before `self._transition(SessionState.STREAMING)`, add `self._model_name = getattr(options, "model", None)`
    - In `_read_formatted_response()` context warning bridge (~line 887):
      - Change `if input_tokens and input_tokens > 0 and options:` to `if input_tokens and input_tokens > 0:`
      - Replace `getattr(options, "model", None)` with `self._model_name`
    - The `try/except Exception: pass` already wraps the PromptBuilder call — removing `and options` from the outer `if` means the entire warning logic is now defense-in-depth protected
    - _Bug_Condition: isBugCondition(input) where input_tokens > 0 AND "options" not in local scope_
    - _Expected_Behavior: Context warning bridge accesses model info via self._model_name, completes without NameError, session transitions STREAMING→IDLE_
    - _Preservation: Non-usage ResultMessages still skip the warning bridge (Req 3.1); all other message processing unchanged (Req 3.7)_
    - _Requirements: 1.1, 2.1, 2.2, 2.3, 3.1, 3.7_

  - [x] 3.2 Change 4: Broaden orphan reaper to catch stale Python backend processes
    - In `LifecycleManager._reap_orphans()`, after the existing claude CLI reaping block:
    - Add a second `pgrep -f "python main.py"` call
    - For each matched PID, check ppid=1 (orphaned) via `/proc/{pid}/stat` or `ps -o ppid= -p {pid}`
    - Skip `os.getpid()` and any PID in `known_pids`
    - Kill orphaned matches with `SIGKILL`
    - Log count of killed python backend orphans
    - _Bug_Condition: isBugCondition_orphan(proc) where proc.cmdline matches "python main.py" AND proc.ppid == 1_
    - _Expected_Behavior: Orphaned python main.py processes detected and killed at startup_
    - _Preservation: Existing claude CLI reaping unchanged (Req 3.5); non-orphaned python processes not killed_
    - _Requirements: 1.6, 2.6, 3.5_

  - [x] 3.3 Change 5: Add Hypothesis deadline and health check settings
    - In `backend/conftest.py`, add Hypothesis profile configuration:
      ```python
      from hypothesis import settings, HealthCheck
      settings.register_profile(
          "default",
          deadline=5000,
          suppress_health_check=[HealthCheck.too_slow],
      )
      settings.load_profile("default")
      ```
    - This prevents infinite shrinking loops and bounds test execution time
    - _Bug_Condition: isBugCondition_hypothesis(test) where test.settings.deadline IS None_
    - _Expected_Behavior: All Hypothesis tests run with 5s deadline and too_slow suppressed_
    - _Requirements: 1.5, 2.5_

  - [x] 3.4 Change 6: Clear `_model_name` in `_cleanup_internal()`
    - In `SessionUnit._cleanup_internal()`, add `self._model_name = None`
    - Prevents stale model names persisting across session reuse after DEAD→COLD transitions
    - _Preservation: All other cleanup fields unchanged_
    - _Requirements: 2.1, 2.3_

  - [x] 3.5 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** — NameError Structurally Eliminated
    - **IMPORTANT**: Re-run the SAME test from task 1 — do NOT write a new test
    - The test from task 1 encodes the expected behavior
    - When this test passes, it confirms:
      - `self._model_name` is set during `send()` from `options.model`
      - `_read_formatted_response()` uses `self._model_name` instead of undefined `options`
      - ResultMessage with `input_tokens > 0` completes without NameError
      - Session transitions STREAMING→IDLE normally
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.6 Verify preservation tests still pass
    - **Property 2: Preservation** — Non-ResultMessage Processing Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all tests still pass after fix:
      - AssistantMessage, SystemMessage, StreamEvent processing yields identical SSE events
      - ResultMessage with no/zero usage still skips context warning bridge
      - Retry logic for genuinely retriable errors still works
      - Normal STREAMING→IDLE transitions still reset hooks and fire idle hooks

- [x] 4. Checkpoint — Ensure all tests pass
  - Run the full backend test suite (`cd backend && pytest`) to verify no regressions
  - Ensure bug condition exploration test passes (task 1 test on fixed code)
  - Ensure preservation property tests pass (task 2 tests on fixed code)
  - Ensure existing backend tests pass
  - Ask the user if questions arise
