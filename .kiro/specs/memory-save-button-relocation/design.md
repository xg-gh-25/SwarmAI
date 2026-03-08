# Memory Save Button Relocation Bugfix Design

## Overview

The Save-to-Memory button currently lives in `ChatHeader.tsx` as a global action, causing two bugs: (1) it's spatially disconnected from the assistant message it acts on, and (2) the single `useMemorySave` hook instance shares status state across all tabs, leaking visual indicators (saved/loading/error) when the user switches sessions. The fix relocates the button into `AssistantMessageView` (last assistant message only, matching the existing Copy button's hover pattern) and makes `useMemorySave` track status per-session via a `Record<string, MemorySaveStatus>`.

## Glossary

- **Bug_Condition (C)**: The condition that triggers the bug — the Save-to-Memory button is rendered in the global header instead of per-message, and its status state is shared across sessions
- **Property (P)**: The desired behavior — the button appears next to Copy on the last assistant message, with session-scoped status that doesn't leak across tab switches
- **Preservation**: Existing Copy button behavior, other header buttons (Compact Context, New Session, sidebar toggles), Toast formatting, backend API contract, and non-last-assistant messages showing only Copy
- **`useMemorySave`**: Hook in `desktop/src/hooks/useMemorySave.ts` that manages the save API call, loading/saved state, and incremental `nextMessageIdxRef` per session
- **`AssistantMessageView`**: Component in `desktop/src/pages/chat/components/AssistantMessageView.tsx` that renders assistant messages with branded layout, content blocks, and the hover-revealed Copy button
- **`ChatHeader`**: Component in `desktop/src/pages/chat/components/ChatHeader.tsx` that renders session tabs and right-side action buttons (currently including the Save-to-Memory button)
- **`MessageBubble`**: Thin dispatcher in `desktop/src/pages/chat/components/MessageBubble.tsx` that routes to `UserMessageView` or `AssistantMessageView` by role

## Bug Details

### Fault Condition

The bug manifests in two ways: (1) the Save-to-Memory button is rendered in `ChatHeader` instead of next to the assistant message content, making it spatially disconnected from the conversation, and (2) the `useMemorySave` hook uses a single `useState<MemorySaveStatus>` for status, so switching tabs carries stale status from the previous session.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type { action: 'render_save_button' | 'switch_tab', currentSessionId: string, previousSessionId?: string }
  OUTPUT: boolean

  IF input.action = 'render_save_button' THEN
    RETURN saveButtonRenderedIn('ChatHeader')
           AND NOT saveButtonRenderedIn('AssistantMessageView', lastAssistantMessage)
  END IF

  IF input.action = 'switch_tab' THEN
    RETURN input.currentSessionId ≠ input.previousSessionId
           AND memorySaveStatus reflects input.previousSessionId's state
           AND NOT memorySaveStatus reflects input.currentSessionId's state
  END IF

  RETURN false
END FUNCTION
```


### Examples

- **Misplaced button**: User finishes reading an assistant response and looks for a save button near the message content (next to Copy). The button is in the header bar instead — user must visually scan away from the message to find it.
- **Status leak on tab switch**: User saves memory in Tab A (session S1), sees green checkmark. Switches to Tab B (session S2) — the green checkmark persists on the header button even though S2 was never saved.
- **Status not reset**: User saves in Tab A (status becomes `saved`), switches to Tab B (status still shows `saved`), clicks save for Tab B — during the API call, the button briefly shows the stale `saved` state before transitioning to `loading`.
- **Edge case — no session**: User opens a fresh "New Session" tab with no messages. The header button shows as disabled but provides no contextual feedback. With the fix, no save button appears at all since there's no last assistant message.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- The Copy button on every assistant message must continue to appear on hover and copy text to clipboard with "Copied!" feedback
- Non-last assistant messages must show only the Copy button on hover (no Save-to-Memory)
- All action buttons (Copy and Save-to-Memory) must remain hidden while the assistant message is streaming
- The Compact Context button, New Session (+), and sidebar toggles (TodoRadar, History, FileBrowser) in ChatHeader must continue to function identically
- The backend `POST /api/memory/save-session` contract (request/response shape) is unchanged
- Toast notification formatting (decisions, lessons, threads, context counts) must remain identical
- The `nextMessageIdxRef` per-session incremental index tracking must continue to work correctly

**Scope:**
All inputs that do NOT involve the Save-to-Memory button placement or its cross-session status should be completely unaffected by this fix. This includes:
- Mouse clicks on the Copy button
- All ChatHeader buttons except the removed Save-to-Memory button
- Message rendering, streaming, and content block display
- Tab creation, switching, and closing (aside from save status display)
- Backend memory extraction logic

## Hypothesized Root Cause

Based on the bug description and code analysis, the issues are:

1. **Incorrect Component Placement**: The Save-to-Memory button is rendered in `ChatHeader.tsx` (lines ~107-128) as a global header action. It should be rendered in `AssistantMessageView.tsx` next to the existing Copy button, following the same `group-hover/msg` pattern. This is a design/architecture issue, not a logic bug.

2. **Shared Status State**: `useMemorySave.ts` uses a single `useState<MemorySaveStatus>('idle')` to track save status. When the user switches tabs, the status from the previous session persists because there's no per-session state isolation. The `nextMessageIdxRef` correctly uses a `Record<string, number>` keyed by sessionId, but the visual status does not.

3. **No Session-Scoped Reset**: There is no mechanism to reset or scope the `status` state when the active session changes. The `reset()` function exists but is only called on toast dismiss, not on tab switch.

4. **Missing Props Pipeline**: `AssistantMessageView` does not receive `sessionId` or `isLastAssistant` props, and `MessageBubble` does not pass them through. These are needed to conditionally render the save button and invoke the hook with the correct session.

## Correctness Properties

Property 1: Fault Condition - Save Button Appears on Last Assistant Message

_For any_ assistant message that is the last assistant message in the session and is not currently streaming, the system SHALL render a Save-to-Memory button next to the Copy button, following the same hover-to-reveal pattern (`opacity-0 group-hover/msg:opacity-100`), and clicking it SHALL invoke `useMemorySave.save(sessionId)` for the correct session.

**Validates: Requirements 2.1, 2.4**

Property 2: Fault Condition - Session-Scoped Status Isolation

_For any_ tab switch from session S1 to session S2, the Save-to-Memory button on S2's last assistant message SHALL display the status scoped to S2 (idle if never saved, saved if previously saved), NOT the status from S1.

**Validates: Requirements 2.2, 2.3**

Property 3: Preservation - Copy Button Unchanged

_For any_ assistant message (whether or not it is the last), the Copy button SHALL continue to appear on hover, copy the message text to clipboard, and show "Copied!" feedback exactly as before the fix.

**Validates: Requirements 3.1, 3.2**

Property 4: Preservation - Non-Last Messages Show Only Copy

_For any_ assistant message that is NOT the last assistant message in the session, the system SHALL render only the Copy button on hover — no Save-to-Memory button.

**Validates: Requirements 3.1**

Property 5: Preservation - Header Buttons Unchanged

_For any_ interaction with ChatHeader buttons (Compact Context, New Session, TodoRadar, ChatHistory, FileBrowser), the behavior SHALL be identical to before the fix. The Save-to-Memory button SHALL no longer be present in the header.

**Validates: Requirements 2.5, 3.6**

Property 6: Preservation - Streaming Hides All Action Buttons

_For any_ assistant message that is currently streaming, the system SHALL hide both the Copy button and the Save-to-Memory button until streaming completes.

**Validates: Requirements 3.5**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `desktop/src/hooks/useMemorySave.ts`

**Changes**:
1. **Per-session status tracking**: Replace `useState<MemorySaveStatus>('idle')` with `useState<Record<string, MemorySaveStatus>>({})`. Add a helper `getStatus(sessionId): MemorySaveStatus` that returns the session's status or `'idle'` if not present.
2. **Per-session toast message**: Replace `useState<string | null>(null)` with `useState<Record<string, string | null>>({})`. Add `getToastMessage(sessionId): string | null`.
3. **Scoped save**: The `save(sessionId)` function already accepts sessionId — update it to write status into the per-session record: `setStatusMap(prev => ({ ...prev, [sessionId]: 'loading' }))`.
4. **Scoped reset**: Update `reset(sessionId: string)` to clear only the specified session's status and toast message from the records.
5. **Return shape change**: Return `{ statusMap, toastMap, save, reset }` instead of `{ status, toastMessage, save, reset }`. Consumers call `statusMap[sessionId] || 'idle'` and `toastMap[sessionId] || null`.

**File**: `desktop/src/pages/chat/components/AssistantMessageView.tsx`

**Changes**:
1. **Add props**: Add `sessionId?: string` and `isLastAssistant?: boolean` to `AssistantMessageViewProps`.
2. **Instantiate hook**: Call `useMemorySave()` inside the component. Read status via `statusMap[sessionId] || 'idle'`.
3. **Render save button**: Next to the existing Copy button `<div>`, conditionally render the Save-to-Memory button when `isLastAssistant && !isStreaming && sessionId`. Use the same hover pattern: place it inside the existing `opacity-0 group-hover/msg:opacity-100` wrapper alongside the Copy button.
4. **Render Toast**: Add Toast rendering for save results, gated on `toastMap[sessionId]`.
5. **Icon and styling**: Define a local `MEMORY_ICON_MAP` (or import a shared constant) mapping `MemorySaveStatus` to Material Symbols icon names, matching the current ChatHeader pattern.

**File**: `desktop/src/pages/chat/components/MessageBubble.tsx`

**Changes**:
1. **Add props**: Add `sessionId?: string` and `isLastAssistant?: boolean` to `MessageBubbleProps`.
2. **Pass through**: Forward these props to `AssistantMessageView` in the assistant branch.

**File**: `desktop/src/pages/ChatPage.tsx`

**Changes**:
1. **Compute `isLastAssistant` for save button**: The existing `isLastAssistant` variable is only true when streaming. Add a separate `isLastAssistantMsg` that is true for the last assistant message regardless of streaming state: `msg.role === 'assistant' && idx === messages.length - 1` (or find the last index where `role === 'assistant'`).
2. **Pass props**: Pass `sessionId={sessionId}` and `isLastAssistant={isLastAssistantMsg}` to `MessageBubble`. Note: `sessionId` is already in scope from `useChatStreamingLifecycle`.

**File**: `desktop/src/pages/chat/components/ChatHeader.tsx`

**Changes**:
1. **Remove save button JSX**: Delete the Save-to-Memory `<button>` block (~lines 107-128).
2. **Remove hook**: Remove `useMemorySave()` call and destructured `{ status: memorySaveStatus, toastMessage, save: saveMemory, reset: resetMemory }`.
3. **Remove toast**: Remove the memory-save `<Toast>` rendering block.
4. **Remove helpers**: Remove `MEMORY_ICON_MAP` constant, `handleSaveMemory` function, `handleToastDismiss` function.
5. **Clean imports**: Remove `useMemorySave`, `MemorySaveStatus`, `Toast` (if only used for memory save — but Toast is also used for compact, so keep it), and `chatService` (if only used for memory save — but it's not used for memory save, it's used for compact, so keep it). Remove `useState` only if no other state remains (compact state still uses it, so keep it).

## Risks and Regression Analysis

This section documents potential risks, edge cases, and regression vectors identified during design review.

### Risk 1: Hook Instance Lifecycle and Status Loss

**Risk**: When a new assistant message arrives, the "last assistant message" shifts from the previous message to the new one. The old AssistantMessageView unmounts, the new one mounts. If `useMemorySave` is instantiated per-component, the hook state (saved status) would be lost on unmount.

**Mitigation**: The `useMemorySave` hook uses `useState` with a `Record<string, MemorySaveStatus>` keyed by sessionId. Since React hooks are tied to component instances, we need the hook to be called in every AssistantMessageView but only the `isLastAssistant` instance renders the button. The per-session Record ensures that when the last-assistant shifts to a new message (same sessionId), the new component instance reads the same session status from the hook's fresh state. However, since each component instance has its own hook state, the status IS lost when the component unmounts.

**Accepted trade-off**: When a new message arrives (new "last assistant"), the save button resets to `idle` for that session. This is acceptable because: (a) a new message means the conversation progressed, so the previous save is stale anyway, and (b) the `nextMessageIdxRef` (which tracks incremental save position) is preserved in the ref and will correctly do an incremental save on the next click.

**Alternative considered**: Lifting the hook to ChatPage and passing status down as props. This would preserve status across message changes but adds prop-threading complexity. The trade-off favors simplicity since the status reset is semantically correct.

### Risk 2: Toast Duplication from Multiple Hook Instances

**Risk**: Every AssistantMessageView calls `useMemorySave()`, but only the last one renders the button and Toast. If the hook's internal state triggers a re-render across all instances, multiple Toasts could appear.

**Mitigation**: Only the `isLastAssistant` instance renders the `<Toast>` component. Non-last instances call the hook but never render its Toast output. The hook's state changes (statusMap, toastMap) will cause re-renders in all instances, but since non-last instances don't render any save-related UI, the re-renders are no-ops visually. The performance cost is negligible for typical conversation lengths.

### Risk 3: Multiple Hook Instances Sharing Ref State

**Risk**: `nextMessageIdxRef` is a `useRef` inside the hook. Each AssistantMessageView instance gets its own ref. When the user clicks save on the last message, that instance's ref tracks the next index. When a new message arrives and a new "last" instance mounts, its ref starts fresh with `{}`.

**Mitigation**: This is a real issue. The `nextMessageIdxRef` must be preserved across component remounts to support incremental saves. Two options:
- **Option A (recommended)**: Lift `nextMessageIdxRef` to module scope (outside the hook function) so it persists across all hook instances and component lifecycles. This is safe because the ref is keyed by sessionId and is never reset.
- **Option B**: Lift the hook to ChatPage and pass `save`/`status` down as props. More complex but keeps all state in one place.

**Decision**: Option A — module-scoped ref. It's a one-line change (`const nextMessageIdxRef` moves from inside `useMemorySave` to module scope) and preserves the incremental save behavior without prop-threading.

### Risk 4: `isLastAssistant` Computation Edge Cases

**Risk**: The current `isLastAssistant` in ChatPage is `isStreaming && msg.role === 'assistant' && idx === messages.length - 1`. For the save button, we need `isLastAssistant` even when NOT streaming. But the last message in the array might be a user message (if the user just sent a message and the assistant hasn't responded yet).

**Mitigation**: Compute `isLastAssistantMsg` as: the message is the last message in the array with `role === 'assistant'`. This handles:
- Normal case: last message is assistant → button shown
- User just sent message: last message is user → no assistant message is "last assistant" → no button (correct, the assistant hasn't responded yet)
- Empty conversation: no messages → no button (correct)
- Only user messages: no assistant messages → no button (correct)

**Implementation**: Find the last index where `msg.role === 'assistant'` and compare with current `idx`. This is O(n) per render but messages arrays are small (typically <100).

### Risk 5: ChatHeader Property Test Regression

**Risk**: `ChatHeader.property.test.tsx` renders ChatHeader with `openTabs: [], activeTabId: null`. The test currently passes with the Save-to-Memory button present in the DOM. After removing the button, the test might fail if it queries for the button.

**Mitigation**: Verified by reading the test file — the property tests only query sidebar toggle buttons by their aria-labels (`'ToDo Radar'`, `'Chat History'`, `'File Browser'`). They do NOT query the Save-to-Memory button. The tests will pass unchanged after the button is removed.

### Risk 6: Import Cleanup in ChatHeader

**Risk**: Removing memory-save code from ChatHeader could accidentally remove imports still needed by other features (Compact Context, sidebar toggles).

**Mitigation**: Careful analysis of what stays:
- `useState` — still needed for `compactStatus` and `compactToast`
- `Toast` — still needed for compact toast
- `chatService` — still needed for `compactSession`
- `clsx` — still needed for button styling
- `useTranslation` — still needed for i18n

Only these are safe to remove:
- `useMemorySave` import
- `MemorySaveStatus` type import
- `MEMORY_ICON_MAP` constant

### Risk 7: Streaming-to-Complete Transition

**Risk**: During streaming, `isStreaming=true` on the last assistant message, so the save button is hidden. When streaming completes, `isStreaming` flips to `false`. The `isLastAssistant` prop (for save button purposes) must also be true at this point.

**Mitigation**: The `isLastAssistantMsg` computation is independent of `isStreaming` — it only checks if the message is the last assistant message by index. When streaming completes, `isStreaming` becomes false, `isLastAssistantMsg` remains true, and the save button appears. The Copy button already handles this transition correctly, so the same pattern applies.

### Risk 8: Toast Fixed Positioning Conflicts

**Risk**: Toast uses `fixed bottom-4 right-4 z-50` positioning. If the user triggers a memory save Toast and a compact Toast simultaneously (unlikely but possible), they would overlap at the same position.

**Mitigation**: This is a pre-existing issue (both Toasts already render from ChatHeader today). The relocation doesn't make it worse. A future improvement could stack Toasts, but that's out of scope for this bugfix.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Fault Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that render ChatHeader and AssistantMessageView, then assert where the Save-to-Memory button appears and whether status leaks across sessions. Run these tests on the UNFIXED code to observe failures.

**Test Cases**:
1. **Button Location Test**: Render AssistantMessageView as the last assistant message — assert no Save-to-Memory button exists (will fail on unfixed code because button is in ChatHeader, not AssistantMessageView)
2. **Status Leak Test**: Simulate saving in session S1, then render with session S2 — assert status is `idle` for S2 (will fail on unfixed code because status is global)
3. **Header Button Presence Test**: Render ChatHeader with an active session — assert Save-to-Memory button IS present in header (will pass on unfixed code, confirming the button is in the wrong place)

**Expected Counterexamples**:
- AssistantMessageView does not contain a Save-to-Memory button
- useMemorySave returns the same status regardless of which sessionId is queried
- Possible causes: button rendered in wrong component, status not keyed by session

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  IF input.action = 'render_save_button' THEN
    rendered := renderAssistantMessageView(lastAssistantMessage, sessionId)
    ASSERT rendered contains SaveToMemoryButton
    ASSERT SaveToMemoryButton is next to CopyButton
    ASSERT SaveToMemoryButton follows hover-to-reveal pattern
  END IF

  IF input.action = 'switch_tab' THEN
    save(input.previousSessionId)
    switchTo(input.currentSessionId)
    ASSERT getStatus(input.currentSessionId) = 'idle'
    ASSERT getStatus(input.previousSessionId) = 'saved'
  END IF
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  IF input involves CopyButton THEN
    ASSERT CopyButton behavior unchanged (click copies text, shows "Copied!")
  END IF

  IF input involves non-last assistant message THEN
    ASSERT only CopyButton shown on hover (no SaveToMemory button)
  END IF

  IF input involves ChatHeader buttons THEN
    ASSERT CompactContext, NewSession, TodoRadar, ChatHistory, FileBrowser unchanged
    ASSERT SaveToMemory button NOT present in header
  END IF

  IF input involves streaming message THEN
    ASSERT no action buttons visible during streaming
  END IF
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain
- It catches edge cases that manual unit tests might miss
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: Observe behavior on UNFIXED code first for Copy button, header buttons, and streaming states, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Copy Button Preservation**: Observe that clicking Copy on any assistant message copies text and shows "Copied!" on unfixed code, then write test to verify this continues after fix
2. **Non-Last Message Preservation**: Observe that hovering over non-last assistant messages shows only Copy on unfixed code, then write test to verify this continues after fix
3. **Header Button Preservation**: Observe that Compact Context, New Session, and sidebar toggles work on unfixed code, then write test to verify this continues after fix
4. **Streaming State Preservation**: Observe that action buttons are hidden during streaming on unfixed code, then write test to verify this continues after fix

### Unit Tests

- Test that AssistantMessageView renders Save-to-Memory button when `isLastAssistant=true` and `isStreaming=false` and `sessionId` is provided
- Test that AssistantMessageView does NOT render Save-to-Memory button when `isLastAssistant=false`
- Test that AssistantMessageView does NOT render Save-to-Memory button when `isStreaming=true`
- Test that AssistantMessageView does NOT render Save-to-Memory button when `sessionId` is undefined
- Test that clicking Save-to-Memory calls `save(sessionId)` with the correct session ID
- Test that ChatHeader no longer renders a Save-to-Memory button
- Test that ChatHeader still renders Compact Context, New Session, and sidebar toggle buttons
- Test that `useMemorySave` tracks status per-session (save S1, check S2 is idle)
- Test that `useMemorySave` preserves `nextMessageIdxRef` across hook re-instantiations (module-scoped ref)

### Property-Based Tests

- Generate random combinations of `{ isLastAssistant, isStreaming, sessionId }` and verify the save button visibility invariant: `visible ↔ (isLastAssistant && !isStreaming && sessionId != null)`
- Generate random sequences of save operations across multiple sessionIds and verify status isolation: `statusMap[s1]` is independent of `statusMap[s2]`
- Generate random `activeSidebar` values and verify ChatHeader sidebar button highlighting is unchanged (existing property test — should pass without modification)

### Integration Tests

- Test full flow: render ChatPage with messages, verify save button appears on last assistant message, click it, verify Toast appears with correct formatting
- Test tab switching: save in Tab A, switch to Tab B, verify Tab B shows idle status, switch back to Tab A, verify Tab A shows saved status
- Test streaming transition: start with streaming message (no save button), complete streaming, verify save button appears
- Test new message arrival: save button on message N, new message N+1 arrives, verify save button moves to N+1 with idle status
