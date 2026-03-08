# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Fault Condition** - Save Button Misplacement and Status Leak
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists
  - **Scoped PBT Approach**: Scope the property to two concrete failing cases:
    1. AssistantMessageView with `isLastAssistant=true, isStreaming=false, sessionId='s1'` does NOT render a Save-to-Memory button (because it doesn't exist there yet)
    2. `useMemorySave` returns the same `status` for any sessionId after saving in a different session (because status is global, not per-session)
  - Create test file: `desktop/src/hooks/__tests__/useMemorySave.fault.property.test.ts`
  - Test 1 (Button Location): Render AssistantMessageView as last assistant message with sessionId — assert a Save-to-Memory button exists next to Copy. On unfixed code this FAILS because the button is in ChatHeader, not AssistantMessageView.
  - Test 2 (Status Isolation): For any two distinct sessionIds (s1, s2), call `save(s1)`, then assert `statusMap[s2]` is `'idle'`. On unfixed code this FAILS because `useMemorySave` returns a single global `status`, not a per-session map.
  - Use `vitest`, `fast-check`, and `@testing-library/react` (matching existing test patterns in `ChatHeader.property.test.tsx`)
  - Mock `api.post` to return a successful `SaveSessionResponse`
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bug exists)
  - Document counterexamples found to understand root cause
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Copy Button, Non-Last Messages, Header Buttons, and Streaming Behavior
  - **IMPORTANT**: Follow observation-first methodology
  - Create test file: `desktop/src/pages/chat/components/__tests__/memoryRelocation.preservation.property.test.tsx`
  - Observe on UNFIXED code:
    - Copy button appears on hover for any assistant message and copies text to clipboard
    - Non-last assistant messages show only Copy button on hover (no Save-to-Memory)
    - ChatHeader sidebar toggle buttons (ToDo Radar, Chat History, File Browser) render with correct highlight/muted styling per `activeSidebar`
    - Action buttons are hidden while `isStreaming=true`
  - Write property-based tests capturing observed behavior:
    - **Preservation A**: For any assistant message (generate random `isLastAssistant` boolean), the Copy button is always present on hover — `visible ↔ !isStreaming`
    - **Preservation B**: For any non-last assistant message (`isLastAssistant=false`), only the Copy button appears — no Save-to-Memory button in the message actions
    - **Preservation C**: For any `activeSidebar` value from `RIGHT_SIDEBAR_IDS`, ChatHeader sidebar buttons maintain correct highlight/muted state (reuse pattern from existing `ChatHeader.property.test.tsx`)
    - **Preservation D**: For any assistant message with `isStreaming=true`, no action buttons (Copy or Save-to-Memory) are visible
  - Use `vitest`, `fast-check`, `@testing-library/react` with same mock patterns as existing tests
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [x] 3. Fix: Relocate Save-to-Memory button and isolate per-session status

  - [x] 3.1 Refactor `useMemorySave` hook for per-session status tracking
    - Replace `useState<MemorySaveStatus>('idle')` with `useState<Record<string, MemorySaveStatus>>({})`
    - Replace `useState<string | null>(null)` for toast with `useState<Record<string, string | null>>({})`
    - Add helper `getStatus(sessionId): MemorySaveStatus` returning `statusMap[sessionId] || 'idle'`
    - Add helper `getToastMessage(sessionId): string | null` returning `toastMap[sessionId] || null`
    - Update `save(sessionId)` to write status into per-session record: `setStatusMap(prev => ({ ...prev, [sessionId]: 'loading' }))`
    - Update `reset(sessionId: string)` to clear only the specified session's status and toast
    - Move `nextMessageIdxRef` to module scope (outside the hook function) for persistence across remounts
    - Return `{ statusMap, toastMap, save, reset }` instead of `{ status, toastMessage, save, reset }`
    - _Bug_Condition: isBugCondition(input) where status is global instead of per-session, and nextMessageIdxRef resets on remount_
    - _Expected_Behavior: statusMap[s1] independent of statusMap[s2]; nextMessageIdxRef persists across component lifecycles_
    - _Preservation: formatToastMessage logic unchanged; API contract unchanged; incremental save via nextMessageIdxRef unchanged_
    - _Requirements: 2.2, 2.3_

  - [x] 3.2 Add Save-to-Memory button to `AssistantMessageView`
    - Add `sessionId?: string` and `isLastAssistant?: boolean` to `AssistantMessageViewProps`
    - Call `useMemorySave()` inside the component; read status via `statusMap[sessionId] || 'idle'`
    - Conditionally render Save-to-Memory button when `isLastAssistant && !isStreaming && sessionId`
    - Place button inside existing `opacity-0 group-hover/msg:opacity-100` wrapper alongside Copy button
    - Define local `MEMORY_ICON_MAP` constant mapping `MemorySaveStatus` to Material Symbols icon names
    - Add Toast rendering for save results, gated on `isLastAssistant && toastMap[sessionId]`
    - _Bug_Condition: isBugCondition(input) where saveButtonRenderedIn('ChatHeader') AND NOT saveButtonRenderedIn('AssistantMessageView')_
    - _Expected_Behavior: Save button rendered next to Copy on last assistant message with hover-to-reveal pattern_
    - _Preservation: Copy button behavior unchanged; non-last messages show only Copy; streaming hides all buttons_
    - _Requirements: 2.1, 2.4, 3.1, 3.2, 3.5_

  - [x] 3.3 Thread props through `MessageBubble`
    - Add `sessionId?: string` and `isLastAssistant?: boolean` to `MessageBubbleProps`
    - Forward these props to `AssistantMessageView` in the assistant branch
    - _Requirements: 2.1_

  - [x] 3.4 Compute and pass `isLastAssistantMsg` in `ChatPage`
    - Compute `isLastAssistantMsg`: find last index where `msg.role === 'assistant'` and compare with current `idx`
    - Pass `sessionId={sessionId}` and `isLastAssistant={isLastAssistantMsg}` to `MessageBubble`
    - `sessionId` is already in scope from `useChatStreamingLifecycle`
    - _Requirements: 2.1_

  - [x] 3.5 Remove Save-to-Memory from `ChatHeader`
    - Delete Save-to-Memory `<button>` block
    - Remove `useMemorySave()` call and destructured variables
    - Remove memory-save `<Toast>` rendering block
    - Remove `MEMORY_ICON_MAP` constant, `handleSaveMemory` function, `handleToastDismiss` function
    - Clean imports: remove `useMemorySave`, `MemorySaveStatus` (keep `useState`, `Toast`, `chatService` — still used by compact feature)
    - _Bug_Condition: saveButtonRenderedIn('ChatHeader') must become false_
    - _Expected_Behavior: ChatHeader no longer renders any memory-save button or related state_
    - _Preservation: Compact Context button, New Session, sidebar toggles all unchanged; existing ChatHeader property tests pass_
    - _Requirements: 2.5, 3.6_

  - [x] 3.6 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Save Button Misplacement and Status Leak
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior (button in AssistantMessageView, per-session status isolation)
    - When this test passes, it confirms the expected behavior is satisfied
    - Run bug condition exploration test from step 1: `desktop/src/hooks/__tests__/useMemorySave.fault.property.test.ts`
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 3.7 Verify preservation tests still pass
    - **Property 2: Preservation** - Copy Button, Non-Last Messages, Header Buttons, and Streaming Behavior
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run preservation property tests from step 2: `desktop/src/pages/chat/components/__tests__/memoryRelocation.preservation.property.test.tsx`
    - Also run existing ChatHeader property tests: `desktop/src/pages/chat/components/ChatHeader.property.test.tsx`
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all tests still pass after fix (no regressions)

- [x] 4. Checkpoint - Ensure all tests pass
  - Run full test suite: `cd desktop && npm test -- --run`
  - Ensure all property tests pass (fault condition, preservation, existing ChatHeader)
  - Ensure no regressions in other test files
  - Ask the user if questions arise
