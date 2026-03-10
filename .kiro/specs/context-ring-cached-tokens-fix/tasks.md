# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Fault Condition** - Cached Tokens Ignored in Context Usage Calculation
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate cached tokens are ignored and model is always None
  - **Scoped PBT Approach**: Scope the property to concrete failing cases with known cached token values
  - Add a new `TestCachedTokensBugExploration` class in `backend/tests/test_context_usage_inline.py`
  - Test 1: Mock `_execute_on_session` to yield a result event with `usage: {input_tokens: 3, cache_read_input_tokens: 98599, cache_creation_input_tokens: 948}`. Assert `_build_context_warning()` receives total=99550 (not 3). On unfixed code, it receives 3 → FAIL proves the bug.
  - Test 2: Mock with `usage: {input_tokens: 11337, cache_read_input_tokens: 661568, cache_creation_input_tokens: 66889}`. Assert context_warning pct ≈ 370% (critical). On unfixed code, pct ≈ 6% (ok) → FAIL proves over-window bug.
  - Test 3: Assert `last_model` is resolved from `agent_config.get("model")` (e.g., "claude-sonnet-4-20250514") not from `event.get("model")` (always None). On unfixed code, model is None → FAIL proves model resolution bug.
  - Use property-based test: for random `(input_tokens, cache_read, cache_creation)` triples where `cache_read + cache_creation > 0`, assert `_build_context_warning()` receives the sum of all three fields
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bug exists)
  - Document counterexamples found (e.g., "received input_tokens=3 instead of total=99550")
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.3, 2.5_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Non-Cached Inputs and Threshold Behavior Unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - Add a new `TestCachedTokensPreservation` class in `backend/tests/test_context_usage_inline.py`
  - Observe on UNFIXED code: when `cache_read_input_tokens=0` and `cache_creation_input_tokens=0`, `_build_context_warning(input_tokens, model)` returns the same result as it would with the sum (since sum == input_tokens)
  - Observe on UNFIXED code: `_build_context_warning(None, None)` returns `None` (no event emitted)
  - Observe on UNFIXED code: `_build_context_warning(0, "claude-sonnet-4-20250514")` returns `None`
  - Property-based test 1: For random `input_tokens > 0` with `cache_read=0, cache_creation=0`, verify `_build_context_warning(input_tokens, model)` returns a dict with correct `pct = round(input_tokens / window * 100)` and correct `level` classification (ok/warn/critical at 70/85 thresholds)
  - Property-based test 2: For random `(input_tokens, cache_read, cache_creation)` triples, verify the sum formula `(x or 0) + (y or 0) + (z or 0)` handles None values correctly (None treated as 0)
  - **NOTE (PE Review)**: Since `isBugCondition` is true for every turn (model always None in result event), preservation tests must target `_build_context_warning()` directly (unchanged function), not the full pipeline
  - Unit test: Verify `_build_context_warning()` returns `None` when `input_tokens` is `None` or `0` (no false context_warning events)
  - Unit test: Verify `context_warning` event shape contains `type`, `level`, `pct`, `tokensEst`, `message` fields
  - Unit test: Verify threshold boundaries are preserved (69→ok, 70→warn, 84→warn, 85→critical)
  - Verify all tests PASS on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

- [x] 3. Fix cached token summation and model resolution

  - [x] 3.1 Implement the fix in `run_conversation()` and `continue_with_answer()`
    - In `run_conversation()` (~line 1432): Replace `last_input_tokens = _usage.get("input_tokens")` with the three-field sum: `last_input_tokens = (_usage.get("input_tokens") or 0) + (_usage.get("cache_read_input_tokens") or 0) + (_usage.get("cache_creation_input_tokens") or 0)`
    - In `run_conversation()` (~line 1433): Replace `last_model = event.get("model")` with `last_model = agent_config.get("model")`
    - In `continue_with_answer()` (~line 2519): Apply the same `last_input_tokens` three-field sum replacement
    - In `continue_with_answer()` (~line 2520): Apply the same `last_model = agent_config.get("model")` replacement
    - Do NOT modify `_build_context_warning()`, `_run_query_on_client()`, `_get_model_context_window()`, or any frontend code
    - _Bug_Condition: isBugCondition(input) where usage.cache_read_input_tokens + usage.cache_creation_input_tokens > 0 OR event.model is None_
    - _Expected_Behavior: last_input_tokens = (input_tokens or 0) + (cache_read_input_tokens or 0) + (cache_creation_input_tokens or 0); last_model = agent_config.get("model")_
    - _Preservation: _build_context_warning() unchanged, context_warning SSE shape unchanged, result SSE shape unchanged, threshold levels unchanged_
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 3.2 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Cached Tokens Summed Correctly
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior (total = sum of all three fields, model from agent_config)
    - Run: `cd backend && pytest tests/test_context_usage_inline.py::TestCachedTokensBugExploration -v`
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed — cached tokens are now summed, model is resolved from config)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 3.3 Verify preservation tests still pass
    - **Property 2: Preservation** - Non-Cached Inputs and Threshold Behavior Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run: `cd backend && pytest tests/test_context_usage_inline.py::TestCachedTokensPreservation -v`
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions — thresholds, event shape, null suppression all preserved)
    - Also run the original preservation tests: `cd backend && pytest tests/test_context_usage_inline.py::TestPreservation -v`
    - Confirm all tests still pass after fix (no regressions)

- [x] 4. Checkpoint - Ensure all tests pass
  - Run full test suite: `cd backend && pytest tests/test_context_usage_inline.py -v`
  - Verify ALL test classes pass: `TestBugConditionExploration`, `TestPreservation`, `TestCachedTokensBugExploration`, `TestCachedTokensPreservation`
  - Ensure all tests pass, ask the user if questions arise.
