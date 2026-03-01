<!-- PE-REVIEWED -->
# Chat Streaming Visibility Bugfix Design

## Overview

The "Thinking..." spinner in `ChatPage.tsx` renders unconditionally whenever `isStreaming` is true, even when the assistant message bubble above it already contains visible content blocks (text, tool_use, tool_result). During multi-turn agent conversations with 50+ tool invocations spanning 2-7 minutes, users see only a static spinner with no indication of progress. The fix introduces two changes: (1) conditionally hide the spinner when content blocks are already rendered, and (2) display a real-time activity indicator showing the current tool name (e.g., "Running: Bash...") extracted from the most recent `tool_use` content block in the streaming message.

## Glossary

- **Bug_Condition (C)**: `isStreaming === true` AND the current assistant message has at least one rendered content block (text, tool_use, or tool_result)
- **Property (P)**: When C holds, the "Thinking..." spinner is hidden and replaced by a contextual activity indicator showing the latest tool name
- **Preservation**: When C does NOT hold (no content blocks yet, or not streaming), the UI renders identically to the current behavior
- **`createStreamHandler`**: The callback factory in `ChatPage.tsx` (~line 370) that processes SSE `StreamEvent`s and appends content blocks to the assistant message via `setMessages`
- **`isStreaming`**: Per-session boolean derived from `streamingSessions: Set<string>` — true while an SSE stream is active for the current session
- **Content Block**: A typed object (`text`, `tool_use`, `tool_result`, `ask_user_question`) within a `Message.content` array
- **Activity Indicator**: A new UI element replacing the spinner that shows "Running: {toolName}..." during active tool use

## Bug Details

### Fault Condition

The bug manifests when the frontend is actively streaming (`isStreaming === true`) and the assistant message already contains one or more content blocks, yet the "Thinking..." spinner continues to render unconditionally at the bottom of the message list. The spinner is rendered in `ChatPage.tsx` (line ~907-912) with a simple `{isStreaming && (...)}` guard that does not account for whether content is already visible.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type StreamingState { isStreaming: boolean, assistantMessage: Message }
  OUTPUT: boolean
  
  LET lastAssistantMsg = input.messages.findLast(m => m.role === 'assistant')
  RETURN input.isStreaming = true
         AND lastAssistantMsg IS NOT NULL
         AND lastAssistantMsg.content.length > 0
         AND lastAssistantMsg.content HAS blocks WHERE type IN ['text', 'tool_use', 'tool_result']
END FUNCTION
```

### Examples

- User sends a complex query → backend streams `assistant` event with `tool_use` block for "Bash" → ToolUseBlock renders inside the message bubble → "Thinking..." spinner STILL shows below it (expected: spinner hidden, activity indicator shows "Running: Bash...")
- Backend streams 5 consecutive `assistant` events with text + tool_use blocks → all content appends to the message bubble → spinner persists the entire time, obscuring the fact that content is actively updating
- Backend streams `assistant` event with only a `text` block (intermediate reasoning) → text renders in bubble → spinner still shows below (expected: spinner hidden since text is visible)
- Simple single-turn query → backend streams one `assistant` event with text → completes quickly → spinner shows briefly then disappears (this is correct behavior, should be preserved)

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- When `isStreaming` is true but no assistant message content blocks exist yet (initial API wait), the "Thinking..." spinner MUST continue to display exactly as today
- Simple single-turn queries that complete quickly with one assistant response MUST render identically to current behavior
- `ask_user_question` events MUST continue to pause streaming, hide the spinner, and display the question form
- `cmd_permission_request` events MUST continue to pause streaming, hide the spinner, and display the permission modal
- `result` events MUST continue to finalize the conversation, stop streaming, and invalidate radar caches
- The stop button MUST continue to abort the stream and display the stop confirmation message
- Mouse/keyboard interactions with existing ToolUseBlock collapse/expand MUST remain unchanged
- The `scrollToBottom` behavior on message updates MUST continue to work

**Scope:**
All inputs where `isBugCondition` returns false should produce identical UI output. This includes:
- Initial streaming state before any content arrives
- Non-streaming states (conversation complete, idle)
- Permission request and ask_user_question paused states
- Session switching, tab management, history loading

## Hypothesized Root Cause

Based on code analysis, the root causes are:

1. **Unconditional Spinner Rendering**: In `ChatPage.tsx` (~line 907-912), the spinner renders with a simple `{isStreaming && (...)}` guard. This boolean check does not consider whether the assistant message already has visible content blocks. The fix is to add a derived condition that checks the last assistant message's content array length.

2. **No Activity State Tracking**: The `createStreamHandler` callback processes `tool_use` content blocks but does not extract or surface the tool name to any UI state. There is no `currentToolName` or `activeToolInfo` state variable that could drive an activity indicator. The tool name is buried inside the content block array and only visible if the user scrolls to the ToolUseBlock widget.

3. **Auto-scroll Timing**: The `scrollToBottom` effect triggers on `[messages]` dependency, which fires when the messages array reference changes. However, during streaming, content blocks are appended to an existing message (same array length, different content within a message), so the scroll may not trigger on every content block update. This needs verification — the current `setMessages(prev => prev.map(...))` does create a new array reference, so scroll should fire, but the scroll target (`messagesEndRef`) is after the spinner, not after the latest content.

## Correctness Properties

Property 1: Fault Condition - Spinner Label Reflects Activity When Content Visible

_For any_ streaming state where `isStreaming === true` AND the last assistant message contains at least one content block of type `text`, `tool_use`, or `tool_result`, the fixed `ChatPage` render SHALL NOT display the generic `chat.thinking` ("Thinking...") label. Instead, it SHALL display either a tool-specific label (e.g., "Running: Bash...") if a `tool_use` block is the most recent activity, or a generic processing label ("Processing...") if only text/tool_result blocks are present.

**Validates: Requirements 2.1, 2.2**

Property 2: Fault Condition - Activity Indicator Shows Current Tool Name

_For any_ streaming state where `isStreaming === true` AND the last assistant message's most recent content block is of type `tool_use` with a non-empty `name` field, the fixed `ChatPage` render SHALL display an activity indicator containing that tool name (e.g., "Running: Bash...", "Reading file...").

**Validates: Requirements 2.2, 2.4**

Property 3: Preservation - Spinner Shown When No Content Yet

_For any_ streaming state where `isStreaming === true` AND the last assistant message has an empty content array (no blocks received yet), the fixed `ChatPage` render SHALL display the "Thinking..." spinner identically to the original behavior.

**Validates: Requirements 3.1**

Property 4: Preservation - Non-Streaming States Unchanged

_For any_ state where `isStreaming === false`, the fixed `ChatPage` render SHALL produce identical output to the original render function, preserving all existing behavior for completed conversations, idle states, and history viewing.

**Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.6**

Property 5: Fault Condition - Auto-Scroll to Latest Content

_For any_ streaming state where new content blocks are appended to the assistant message, the chat container SHALL scroll to keep the latest content visible to the user, rather than leaving the viewport stuck at an earlier position.

**Validates: Requirements 2.3**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `desktop/src/pages/ChatPage.tsx`

**Change 1: Add `currentToolActivity` derived state**

Add a `useMemo` that computes the current streaming activity from the messages array. Uses `findLast()` to avoid copying arrays on every render:
```typescript
const streamingActivity = useMemo(() => {
  if (!isStreaming) return null;
  const lastAssistant = messages.findLast(m => m.role === 'assistant');
  if (!lastAssistant || lastAssistant.content.length === 0) return null;
  
  const hasContent = lastAssistant.content.some(b => 
    b.type === 'text' || b.type === 'tool_use' || b.type === 'tool_result'
  );
  if (!hasContent) return null;
  
  // Find the most recent tool_use block with a non-empty name
  const lastToolUse = lastAssistant.content.findLast(b => b.type === 'tool_use');
  const toolName = lastToolUse?.name?.trim() || null;
  
  return { hasContent, toolName };
}, [isStreaming, messages]);
```

**Change 2: Replace unconditional spinner with conditional rendering**

Replace the current spinner block (~line 907-912):
```tsx
// BEFORE:
{isStreaming && (
  <div className="flex items-center gap-2 text-[var(--color-text-muted)]">
    <Spinner size="sm" />
    <span className="text-sm">{t('chat.thinking')}</span>
  </div>
)}

// AFTER:
{isStreaming && (
  <div className="flex items-center gap-2 text-[var(--color-text-muted)]">
    <Spinner size="sm" />
    <span className="text-sm">
      {streamingActivity?.toolName
        ? t('chat.runningTool', { tool: streamingActivity.toolName })
        : streamingActivity?.hasContent
          ? t('chat.processing')
          : t('chat.thinking')}
    </span>
  </div>
)}
```

This keeps the spinner visible during streaming (as a "still working" signal) but changes its label based on state:
- No content yet → "Thinking..." (original behavior, preservation)
- Content visible, tool active → "Running: Bash..." (activity indicator)
- Content visible, no active tool → "Processing..." (acknowledges content exists)

**Change 3: Add i18n keys for activity states**

Add to the translation file:
```json
"chat.runningTool": "Running: {{tool}}...",
"chat.processing": "Processing..."
```

**File**: `desktop/src/pages/chat/components/MessageBubble.tsx`

**Change 4: No changes needed**

MessageBubble already correctly renders all content blocks via `ContentBlockRenderer`. The content blocks are already visible — the issue is purely the spinner label at the bottom of the chat, not the message bubble rendering.

### Design Decision: Keep Spinner, Change Label Based on State

Rather than hiding the spinner entirely when content is visible, we keep the spinner but update its label to reflect the current activity state. Three distinct labels:
- "Thinking..." — no content blocks received yet (preservation of original behavior)
- "Running: {toolName}..." — a `tool_use` block is the most recent activity
- "Processing..." — content blocks exist but no active tool (text-only streaming)

Rationale:
- The spinner at the bottom of the message list serves as a clear "still processing" signal — hiding it could confuse users into thinking the response is complete
- The label change addresses the core UX problem: users now see what the agent is doing
- This is the minimal change that addresses all requirements without introducing new components or complex state management
- The "Thinking..." label is only shown when genuinely waiting for the first response, aligning with Requirement 2.1

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Fault Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write React component tests that render `ChatPage` (or a minimal reproduction) with controlled `messages` and `isStreaming` state, then assert on the rendered output. Run these tests on the UNFIXED code to observe failures.

**Test Cases**:
1. **Spinner With Content Test**: Set `isStreaming=true` and provide an assistant message with `tool_use` content blocks → assert spinner shows generic "Thinking..." with no tool name (will fail on unfixed code if we expect tool name)
2. **Multiple Tool Calls Test**: Set `isStreaming=true` with assistant message containing 5+ tool_use blocks → assert the displayed label reflects the LATEST tool name (will fail on unfixed code)
3. **Text-Only Streaming Test**: Set `isStreaming=true` with assistant message containing only text blocks → assert spinner label is contextual (will fail on unfixed code)
4. **Empty Content Test**: Set `isStreaming=true` with assistant message with empty content array → assert "Thinking..." spinner displays (should pass on unfixed code — this is preservation)

**Expected Counterexamples**:
- The spinner always shows "Thinking..." regardless of content block state
- No tool name is surfaced in the UI during active tool use

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL state WHERE isBugCondition(state) DO
  ui := renderChatPage_fixed(state)
  LET lastToolUse = state.lastAssistantMessage.content.findLast(b => b.type === 'tool_use')
  IF lastToolUse EXISTS AND lastToolUse.name IS NOT EMPTY THEN
    ASSERT ui.spinnerLabel CONTAINS lastToolUse.name
  ELSE
    ASSERT ui.spinnerLabel = "Processing..."
  END IF
  ASSERT ui.spinnerLabel != "Thinking..."
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL state WHERE NOT isBugCondition(state) DO
  ASSERT renderChatPage_original(state) = renderChatPage_fixed(state)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many combinations of message arrays, streaming states, and content block types
- It catches edge cases like empty messages, messages with only `ask_user_question` blocks, or rapid state transitions
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: Observe behavior on UNFIXED code first for non-streaming states and empty-content streaming states, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Non-Streaming Preservation**: Verify that when `isStreaming=false`, the spinner area renders nothing regardless of message content — same as original
2. **Empty Content Preservation**: Verify that when `isStreaming=true` and assistant message has empty content, "Thinking..." spinner displays — same as original
3. **Ask User Question Preservation**: Verify that `ask_user_question` events still pause streaming and hide spinner — same as original
4. **Permission Request Preservation**: Verify that `cmd_permission_request` events still pause streaming and show modal — same as original

### Unit Tests

- Test `streamingActivity` useMemo derivation with various message/content combinations
- Test that the spinner label changes based on `streamingActivity.toolName`
- Test that the spinner shows "Thinking..." when `streamingActivity` is null (no content yet)
- Test edge case: assistant message with only `tool_result` blocks (no `tool_use`) — should show "Thinking..." since there's no tool name to display
- Test edge case: rapid content block additions don't cause flickering between labels

### Property-Based Tests

- Generate random arrays of content blocks (text, tool_use, tool_result) and verify `streamingActivity` correctly identifies the latest tool_use name
- Generate random streaming states (isStreaming true/false × content empty/non-empty) and verify the spinner label matches the expected output
- Generate random message histories and verify preservation: non-streaming states always produce null `streamingActivity`

### Integration Tests

- Test full SSE streaming flow: send a message, receive tool_use events, verify spinner label updates in real-time
- Test that auto-scroll keeps the spinner/activity indicator visible as content blocks accumulate
- Test session switching during streaming: verify spinner state is per-session and doesn't leak across tabs
