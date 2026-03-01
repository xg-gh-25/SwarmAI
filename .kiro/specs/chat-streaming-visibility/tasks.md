# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Fault Condition** - Spinner Always Shows "Thinking..." When Content Blocks Exist
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the spinner label never changes from "Thinking..." regardless of streaming content state
  - **Scoped PBT Approach**: Generate streaming states where `isStreaming=true` AND the last assistant message has content blocks (text, tool_use, tool_result), then assert the spinner label reflects the activity state
  - Create test file `desktop/src/pages/__tests__/ChatPageSpinner.property.test.tsx`
  - Use `fast-check` to generate content block arrays with at least one block of type `text`, `tool_use`, or `tool_result`
  - For each generated state, render the spinner label logic and assert:
    - When the latest content block is `tool_use` with name "Bash", label should contain "Bash" (not "Thinking...")
    - When content blocks exist but no active tool_use, label should be "Processing..." (not "Thinking...")
  - The `streamingActivity` useMemo derivation is the unit under test — extract the logic into a pure function `deriveStreamingActivity(isStreaming, messages)` for testability
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (the current code always renders "Thinking..." — confirms the bug exists)
  - Document counterexamples found (e.g., "With tool_use block name='Bash', spinner still shows 'Thinking...' instead of 'Running: Bash...'")
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Non-Streaming and Empty-Content States Unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - Create test in same file `desktop/src/pages/__tests__/ChatPageSpinner.property.test.tsx`
  - Observe on UNFIXED code: when `isStreaming=false`, no spinner label renders regardless of message content
  - Observe on UNFIXED code: when `isStreaming=true` AND assistant message has empty content array, "Thinking..." spinner displays
  - Observe on UNFIXED code: when `isStreaming=true` AND no assistant message exists, "Thinking..." spinner displays
  - Use `fast-check` to generate:
    - Random message arrays with `isStreaming=false` → assert `deriveStreamingActivity` returns `null`
    - Random message arrays with `isStreaming=true` and empty content on last assistant message → assert `deriveStreamingActivity` returns `null`
    - Random message arrays with only non-assistant messages and `isStreaming=true` → assert `deriveStreamingActivity` returns `null`
  - Property: for all inputs where `isBugCondition` returns false, `deriveStreamingActivity` returns `null` (meaning "Thinking..." label is preserved)
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [x] 3. Fix for chat streaming visibility — spinner label reflects activity state

  - [x] 3.1 Add `deriveStreamingActivity` pure function and `streamingActivity` useMemo to ChatPage.tsx
    - Extract a pure function `deriveStreamingActivity(isStreaming: boolean, messages: Message[])` that returns `{ hasContent: boolean, toolName: string | null } | null`
    - Logic: if `!isStreaming` return `null`; find last assistant message; if no content blocks return `null`; check for text/tool_use/tool_result blocks; find last `tool_use` block name
    - Add `const streamingActivity = useMemo(() => deriveStreamingActivity(isStreaming, messages), [isStreaming, messages]);` inside `ChatPage`
    - Export `deriveStreamingActivity` for test access
    - _Bug_Condition: isBugCondition(input) where isStreaming=true AND lastAssistantMsg.content has text/tool_use/tool_result blocks_
    - _Expected_Behavior: returns { hasContent: true, toolName } when content blocks exist with tool_use, or { hasContent: true, toolName: null } for text-only_
    - _Preservation: returns null when isStreaming=false OR no content blocks yet_
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.4_

  - [x] 3.2 Replace unconditional spinner label with 3-state conditional label
    - In the spinner JSX block (~line 908-913), replace `{t('chat.thinking')}` with conditional:
      - `streamingActivity?.toolName` → `t('chat.runningTool', { tool: streamingActivity.toolName })`
      - `streamingActivity?.hasContent` → `t('chat.processing')`
      - else → `t('chat.thinking')`
    - Keep the spinner visible during all streaming states (only the label changes)
    - _Bug_Condition: When content blocks exist, label must not be "Thinking..."_
    - _Expected_Behavior: "Running: {toolName}..." for active tool_use, "Processing..." for text-only, "Thinking..." for no content_
    - _Preservation: "Thinking..." label preserved when no content blocks received yet_
    - _Requirements: 2.1, 2.2, 2.4, 3.1_

  - [x] 3.3 Add i18n keys to `desktop/src/i18n/locales/en.json`
    - Add `"runningTool": "Running: {{tool}}..."` near the existing `"thinking"` key in the chat section
    - Add `"processing": "Processing..."` near the existing `"thinking"` key in the chat section
    - _Requirements: 2.2_

  - [x] 3.4 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Spinner Label Reflects Activity When Content Visible
    - **IMPORTANT**: Re-run the SAME test from task 1 — do NOT write a new test
    - The test from task 1 encodes the expected behavior (tool name in label, "Processing..." for text-only)
    - When this test passes, it confirms the expected behavior is satisfied
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2, 2.4_

  - [x] 3.5 Verify preservation tests still pass
    - **Property 2: Preservation** - Non-Streaming and Empty-Content States Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all tests still pass after fix (no regressions)

- [x] 4. Checkpoint - Ensure all tests pass
  - Run `cd desktop && npm test -- --run` to execute all tests
  - Ensure all property tests from tasks 1 and 2 pass
  - Ensure no existing tests are broken by the changes
  - Ask the user if questions arise
