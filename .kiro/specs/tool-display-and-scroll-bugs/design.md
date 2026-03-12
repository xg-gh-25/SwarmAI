<!-- PE-REVIEWED -->
# Tool Display & Scroll Bugs — Bugfix Design

## Overview

Three related UI bugs degrade the chat experience during tool-heavy agent interactions. (1) Tool loading labels misclassify MCP tools because `summarize_tool_use()` uses exact set membership on the full lowered tool name, which never matches `mcp__ServerName__tool_name` format names — they fall to a fallback that may produce misleading labels. (2) Per-tool spinners never stop individually because the backend emits `tool_result` blocks with `tool_use_id` (snake_case) but the frontend `resultMap` keys on `toolUseId` (camelCase) — the SSE path does raw `JSON.parse` with no case conversion, so the lookup always misses. (3) Scroll position leaks between tabs because `handleTabSelect` saves/restores messages, sessionId, and pendingQuestion but not scroll position — the single shared `messagesContainerRef` DOM ref retains the previous tab's scroll offset.

The fix strategy is minimal and targeted: add substring/token matching for MCP tool names in `tool_summarizer.py`, add a `toCamelCaseContentBlock` conversion in the SSE stream handler path, and add `scrollPosition` to `UnifiedTab` with save/restore in `handleTabSelect`.

## Glossary

- **Bug_Condition (C)**: The set of inputs/states that trigger one of the three bugs
- **Property (P)**: The desired correct behavior when the bug condition holds
- **Preservation**: Existing behaviors that must remain unchanged by the fix
- **`summarize_tool_use()`**: Function in `backend/core/tool_summarizer.py` that generates ≤200-char human-readable labels from tool name + input
- **`_format_message()`**: Method in `backend/core/agent_manager.py` that converts SDK messages to SSE event format, emitting `tool_result` blocks with snake_case fields
- **`resultMap`**: `Map<string, ToolResultContent>` in `AssistantMessageView.tsx` that pairs `tool_use` blocks with their `tool_result` by `toolUseId`
- **`tabMapRef`**: Authoritative `useRef<Map<string, UnifiedTab>>` storing all per-tab state
- **`messagesContainerRef`**: Single shared DOM ref for the scrollable messages container

## Bug Details

### Bug Condition

The three bugs manifest under distinct but related conditions during tool-heavy streaming sessions.

**Bug 1 — MCP Tool Label Misclassification**

MCP tool names follow the `mcp__ServerName__tool_name` format. When lowered, names like `mcp__aws_outlook_mcp__email_search` don't match any category set (`_BASH_NAMES`, `_READ_NAMES`, etc.) via exact membership. The fallback branch picks up a `query` field present in many MCP tool inputs, producing a misleading `"{name}: {query}"` label. For MCP tools that happen to have input fields matching web search patterns, the label can be confusing.

**Formal Specification:**
```
FUNCTION isBugCondition_Bug1(input)
  INPUT: input of type { name: string, input_data: dict }
  OUTPUT: boolean

  lower_name := input.name.lower()
  RETURN lower_name NOT IN _BASH_NAMES
         AND lower_name NOT IN _READ_NAMES
         AND lower_name NOT IN _WRITE_NAMES
         AND lower_name NOT IN _SEARCH_NAMES
         AND lower_name NOT IN _WEB_FETCH_NAMES
         AND lower_name NOT IN _WEB_SEARCH_NAMES
         AND lower_name NOT IN _LIST_DIR_NAMES
         AND lower_name NOT IN _TODOWRITE_NAMES
         AND lower_name CONTAINS "__"  // MCP tool format
END FUNCTION
```

**Bug 2 — tool_result snake_case → camelCase Conversion Gap**

The backend `_format_message()` emits `tool_result` blocks with `tool_use_id` and `is_error` (snake_case). The SSE parser in `chat.ts` does raw `JSON.parse(data)` with no field conversion. The `resultMap` in `AssistantMessageView` keys on `block.toolUseId` (camelCase). Since the parsed block has `tool_use_id` (not `toolUseId`), the map lookup always returns `undefined`, so `isPending` stays `true` until `isStreaming` becomes `false`.

**Formal Specification:**
```
FUNCTION isBugCondition_Bug2(input)
  INPUT: input of type SSEEvent with content blocks
  OUTPUT: boolean

  FOR EACH block IN input.content DO
    IF block.type == "tool_result" THEN
      RETURN block HAS FIELD "tool_use_id"
             AND block DOES NOT HAVE FIELD "toolUseId"
    END IF
  END FOR
  RETURN false
END FUNCTION
```

**Bug 3 — Cross-Tab Scroll Contamination**

The `UnifiedTab` interface has no `scrollPosition` field. `handleTabSelect()` saves `messages`, `sessionId`, `pendingQuestion`, `isExpanded`, and `contextWarning` — but not scroll position. The `messagesContainerRef` is a single shared DOM ref, so switching tabs leaves the previous tab's scroll offset in the DOM element.

**Formal Specification:**
```
FUNCTION isBugCondition_Bug3(input)
  INPUT: input of type { action: "tab_switch", sourceTabId: string, targetTabId: string }
  OUTPUT: boolean

  RETURN input.action == "tab_switch"
         AND sourceTabId != targetTabId
         AND messagesContainerRef.current.scrollTop != targetTab.savedScrollPosition
END FUNCTION
```

### Examples

- **Bug 1**: Agent invokes `mcp__aws_outlook_mcp__email_search` with `{"query": "meeting notes"}`. Expected: `"mcp: email_search — meeting notes"` or similar. Actual: `"mcp__aws_outlook_mcp__email_search: meeting notes"` (raw full name in fallback).
- **Bug 1**: Agent invokes `mcp__GitHub__create_issue` with `{"title": "Fix login bug"}`. Expected: `"mcp: create_issue — Fix login bug"`. Actual: `"mcp__GitHub__create_issue: Fix login bug"`.
- **Bug 2**: Backend sends `{"type": "tool_result", "tool_use_id": "toolu_abc123", "content": "file contents...", "is_error": false}`. Frontend stores it with key `tool_use_id` but looks up by `toolUseId` → miss → spinner keeps spinning.
- **Bug 2**: Three sequential tool calls in one turn. All three spinners keep spinning until `isStreaming` becomes `false`, even though each `tool_result` arrived seconds apart.
- **Bug 3**: User scrolls to line 50 in Tab A, switches to Tab B (which was at the top). Tab B shows at Tab A's scroll position instead of the top.
- **Bug 3**: User switches back to Tab A. Tab A's scroll position is lost — shows wherever Tab B left the DOM element.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- WebSearch tool (Claude SDK built-in) must continue to display "Searching web for {query}"
- Built-in SDK tools (Bash, Read, Write, Edit, Grep, Glob, ListDirectory, WebFetch) must continue to display their current category-specific labels
- Tools with no recognizable input fields must continue to display "Using {name}" fallback
- Tool blocks that have not yet received their `tool_result` during streaming must continue to show spinning progress indicator (`isPending = true`)
- When `isStreaming` becomes `false`, all remaining spinners must stop
- `tool_result` blocks arriving for background tabs must update only the `tabMapRef` entry for the originating tab (Principle 2)
- Tab switching must continue to save/restore all existing per-tab state (messages, sessionId, pendingQuestion, isExpanded, contextWarning)
- Tab switching during background streaming must use `bumpStreamingDerivation()`, not `setIsStreaming()` (Principle 7)
- Auto-scroll during streaming (user has not scrolled up) must continue to scroll to bottom on new messages

**Scope:**
- Bug 1 fix only affects the `summarize_tool_use()` and `get_tool_category()` functions in `tool_summarizer.py`
- Bug 2 fix affects BOTH the SSE content block processing path AND the REST API path (`toMessageCamelCase` in `chat.ts`). The `toMessageCamelCase()` function passes `data.content` through as-is with NO field conversion on content blocks — so `tool_result` blocks loaded from the DB via `getSessionMessages()` (e.g., on tab switch, app restart) also have `tool_use_id` (snake_case) instead of `toolUseId` (camelCase). Both paths must be fixed.
- Bug 3 fix only affects `UnifiedTab` interface, `handleTabSelect`, and scroll-related logic in `ChatPage.tsx`

## Hypothesized Root Cause

Based on code analysis, the root causes are confirmed (not just hypothesized):

1. **Bug 1 — Exact Set Membership on Full MCP Name**: `summarize_tool_use()` does `name.lower() in _SEARCH_NAMES` etc. MCP tool names like `mcp__aws_outlook_mcp__email_search` never match any set because the sets contain short canonical names (`"bash"`, `"read"`, `"websearch"`). The fallback branch produces `"{name}: {context}"` using the full ugly MCP name. **Note**: The user's screenshot shows "Searching web for..." prefix on all tools, which is the `_WEB_SEARCH_NAMES` format. This could indicate either (a) the bottom streaming activity label (which shows the LAST tool_use block's `toolContext`) is being confused with individual tool block labels, or (b) there is an additional issue where certain tool names are unexpectedly matching `_WEB_SEARCH_NAMES`. The exploratory test (Task 1) must verify the actual root cause before implementing the fix. Regardless, the MCP name extraction fix is needed to produce clean labels for MCP tools. The fix needs to extract the final segment of MCP names (after the last `__`) and attempt category matching on that segment, plus provide a cleaner fallback format for MCP tools.

2. **Bug 2 — No snake_case → camelCase Conversion in SSE Path OR REST Path**: The backend `_format_message()` emits `tool_result` blocks with Python-convention field names: `tool_use_id`, `is_error`. The SSE parser in `chat.ts` does `JSON.parse(data)` and passes the raw object to `onMessage()` with zero field transformation. The `toMessageCamelCase()` function exists for REST API responses (`getSessionMessages`) but it also does NOT convert content block fields — it passes `data.content` through as-is via `data.content as unknown as ChatMessage['content']`. So BOTH paths have the same bug: `tool_result` blocks retain snake_case field names. The `resultMap` in `AssistantMessageView` keys on `block.toolUseId` (TypeScript convention), so the lookup always fails. The `blockKey()` function in `useChatStreamingLifecycle.ts` also reads `block.toolUseId` for deduplication, meaning duplicate `tool_result` blocks could accumulate. Additionally, `chat.ts` has THREE separate SSE streaming methods (`streamChat`, `streamAnswerQuestion`, `streamCmdPermissionContinue`) — all three do raw `JSON.parse(data)` and must be fixed.

3. **Bug 3 — Missing Scroll State in Tab Switch Protocol**: The multi-tab isolation Principle 7 mandates "Tab Switch = Save + Restore + Re-derive". The current `handleTabSelect` saves messages, sessionId, pendingQuestion, isExpanded, and contextWarning — but scroll position is not part of the `UnifiedTab` interface and is not saved/restored. The `messagesContainerRef` is a single shared DOM ref, so the scroll offset persists from the previous tab. Additionally, `userScrolledUpRef` is a shared ref that should be reset on tab switch to avoid stale auto-scroll suppression.

## Correctness Properties

Property 1: Bug Condition — MCP Tool Labels Are Meaningful

_For any_ tool invocation where the tool name follows the `mcp__ServerName__tool_name` format, the fixed `summarize_tool_use()` function SHALL produce a label that either (a) matches a known category by extracting the final segment of the MCP name, or (b) uses a clean fallback format showing the server name and tool name separately, not the raw full MCP name.

**Validates: Requirements 2.1, 2.2**

Property 2: Bug Condition — Per-Tool Spinner Stops on Result

_For any_ `tool_result` SSE event arriving during streaming, the fixed content block processing SHALL convert `tool_use_id` to `toolUseId` and `is_error` to `isError` so that the `resultMap` lookup in `AssistantMessageView` succeeds, causing the corresponding `MergedToolBlock`'s spinner to stop immediately while other pending tool blocks continue spinning.

**Validates: Requirements 2.3, 2.4**

Property 3: Bug Condition — Independent Scroll Per Tab

_For any_ tab switch from Tab A to Tab B, the fixed `handleTabSelect` SHALL save Tab A's `messagesContainerRef.current.scrollTop` into Tab A's `UnifiedTab.scrollPosition` before the switch, and restore Tab B's previously saved `scrollPosition` after the switch, so each tab maintains independent scroll state.

**Validates: Requirements 2.5, 2.6**

Property 4: Preservation — Existing Tool Labels Unchanged

_For any_ tool invocation where the tool name matches an existing category set via exact membership (Bash, Read, Write, Edit, Grep, Glob, WebSearch, WebFetch, ListDirectory, TodoWrite), the fixed `summarize_tool_use()` function SHALL produce the same label as the original function, preserving all existing category-specific label formats.

**Validates: Requirements 3.1, 3.2**

Property 5: Preservation — Streaming Lifecycle Unchanged

_For any_ streaming conversation turn, the fixed code SHALL preserve the existing streaming lifecycle: `isPending` remains `true` for tool blocks without results while `isStreaming` is `true`, all spinners stop when `isStreaming` becomes `false`, and background tab updates only modify `tabMapRef` entries.

**Validates: Requirements 3.3, 3.4, 3.5**

Property 6: Preservation — Tab Switch State Integrity

_For any_ tab switch, the fixed code SHALL continue to save and restore all existing per-tab state (messages, sessionId, pendingQuestion, isExpanded, contextWarning) and SHALL continue to use `bumpStreamingDerivation()` for isStreaming re-derivation, not `setIsStreaming()`.

**Validates: Requirements 3.6, 3.7, 3.8**

## Fix Implementation

### Changes Required

#### Bug 1 — MCP Tool Label Classification

**File**: `backend/core/tool_summarizer.py`

**Functions**: `summarize_tool_use()`, `get_tool_category()`

**Specific Changes**:

1. **Add MCP name extraction helper**: Create a `_extract_mcp_tool_name(name: str) -> tuple[str | None, str]` function that detects the `mcp__ServerName__tool_name` pattern and returns `(server_name, tool_name)`. If the name doesn't match the MCP pattern, return `(None, name)`.

2. **Add token-based category matching**: After exact set membership fails, if the name is an MCP tool, extract the final segment (e.g., `email_search` from `mcp__aws_outlook_mcp__email_search`) and attempt category matching on the LAST token only (split by `_`). For example, `email_search` → last token `search` → matches `_SEARCH_NAMES` → category `search`. Only the last token is checked to avoid false positives (e.g., `bash_runner` → last token `runner` → no match → fallback, NOT incorrectly matching `bash`).

3. **Improve MCP fallback format**: When no category match is found even after token extraction, format the label as `"mcp: {tool_name} — {context}"` using the extracted tool name (not the full raw MCP name), where context comes from the existing fallback field extraction logic.

4. **Update `get_tool_category()`**: Apply the same MCP extraction + token matching logic so the frontend receives the correct category for icon selection.

#### Bug 2 — SSE and REST Content Block Case Conversion

**File**: `desktop/src/services/chat.ts`

**Specific Changes**:

1. **Add `toCamelCaseContentBlock()` function**: Create a helper that converts snake_case fields in content blocks to camelCase. Specifically: `tool_use_id` → `toolUseId`, `is_error` → `isError`. Apply to `tool_result` type blocks. Also defensively handle any future snake_case fields on other block types.

2. **Add `toCamelCaseContent()` function**: Wrap the block-level converter to process an array of content blocks, applying `toCamelCaseContentBlock` to each.

3. **Extract shared `parseSSEEvent()` helper**: All three SSE streaming methods (`streamChat`, `streamAnswerQuestion`, `streamCmdPermissionContinue`) currently do raw `JSON.parse(data)` independently. Extract a shared helper that parses the SSE data AND applies content block case conversion. All three methods must call this shared helper instead of raw `JSON.parse`.

4. **Fix `toMessageCamelCase()` for REST path**: Update the existing `toMessageCamelCase()` function to apply `toCamelCaseContent()` to `data.content` instead of passing it through as-is. This fixes the same bug for messages loaded from the DB via `getSessionMessages()` (e.g., on tab switch, app restart).

**Preferred approach (MANDATORY — do NOT use alternative)**: Create the `toCamelCaseContentBlock` / `toCamelCaseContent` helpers once, then apply them in both the SSE `parseSSEEvent()` helper and the REST `toMessageCamelCase()` function. This keeps all case conversion logic in the service layer and ensures both paths are consistent. The conversion MUST happen at the `chat.ts` parse level, NOT in `createStreamHandler` within `useChatStreamingLifecycle.ts`. Applying conversion in the stream handler would cause `blockKey()` to read `block.toolUseId` as `undefined` (before conversion), producing `tool_result:undefined` keys that break Set-based dedup — all tool_result blocks would collide and only the first would be kept.

#### Bug 3 — Per-Tab Scroll Position

**File**: `desktop/src/hooks/useUnifiedTabState.ts`

**Specific Changes**:

1. **Add `scrollPosition` to `UnifiedTab` interface**: Add `scrollPosition?: number` as a runtime-only field (not persisted to `open_tabs.json`). Default to `undefined` (meaning "scroll to bottom").

**File**: `desktop/src/pages/ChatPage.tsx`

**Specific Changes**:

2. **Save scroll position in `handleTabSelect`**: Before switching tabs, read `messagesContainerRef.current?.scrollTop` and save it into the current tab's `UnifiedTab` via `updateTabState(currentTabId, { scrollPosition: ... })`.

3. **Restore scroll position in `handleTabSelect`**: After restoring the target tab's state from `tabMapRef`, read `tabState.scrollPosition` and set `messagesContainerRef.current.scrollTop` after a double-`requestAnimationFrame` (to ensure React has committed the new messages to the DOM — the same pattern already used in the existing "scroll to bottom after tab restore" effect). If `scrollPosition` is `undefined`, scroll to bottom (new tab behavior).

**CRITICAL — Async guard in double-rAF callback**: The double-rAF callback MUST check `activeTabIdRef.current === tabId` before applying the scroll position. If the user switched tabs again during the rAF delay, the callback must be a no-op. Without this guard, rapid tab switching (A→B→C) would cause Tab B's scroll restore to fire after Tab C is already active, corrupting Tab C's scroll position.

**CRITICAL — Suppress auto-scroll during tab switch**: Set `userScrolledUpRef.current = true` BEFORE calling `setMessages(tabState.messages)` in the tab-switch restore path. This prevents the `[messages]` auto-scroll effect from firing `scrollToBottom()` before the double-rAF scroll restore runs. Without this, the user sees a flash of scroll-to-bottom followed by a jump to the saved position.

**CRITICAL — Async `loadSessionMessages` path**: For tabs with `sessionId` but empty messages (the `loadSessionMessages` branch), scroll restore MUST NOT happen in `handleTabSelect`. Instead, save the target tab's `scrollPosition` and restore it AFTER `loadSessionMessages` completes and `setMessages` is called. This can be done by passing the scroll position to `loadSessionMessages` or by restoring it in the `messagesReady` effect. The synchronous double-rAF approach won't work here because the messages aren't in the DOM yet.

4. **Reset `userScrolledUpRef` on tab switch**: After the double-rAF scroll restore completes, recompute `userScrolledUpRef` based on the restored position relative to the container's `scrollHeight`. This must happen INSIDE the double-rAF callback (after DOM has updated with new messages) to avoid using stale `scrollHeight`. This prevents stale auto-scroll suppression from the previous tab.

5. **Save `scrollPosition` in `handleNewSession`**: Also save scroll position when creating a new tab (same save pattern as `handleTabSelect`).

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bugs on unfixed code, then verify the fixes work correctly and preserve existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bugs BEFORE implementing the fixes. Confirm the root cause analysis.

**Test Plan**: Write tests that exercise each bug condition on the unfixed code to observe failures.

**Test Cases**:
1. **MCP Tool Label Test**: Call `summarize_tool_use("mcp__GitHub__create_issue", {"title": "Fix bug"})` and assert the label does NOT contain the full raw MCP name (will fail on unfixed code — label will be `"mcp__GitHub__create_issue: Fix bug"`)
2. **MCP Tool Category Test**: Call `get_tool_category("mcp__aws_outlook_mcp__email_search")` and assert it returns a meaningful category, not `"fallback"` (will fail on unfixed code)
3. **tool_result Case Conversion Test**: Parse a raw SSE event `{"type": "assistant", "content": [{"type": "tool_result", "tool_use_id": "abc", "is_error": false}]}` and assert the content block has `toolUseId` field (will fail on unfixed code — field will be `tool_use_id`)
4. **REST tool_result Case Conversion Test**: Call `toMessageCamelCase()` with a message containing `tool_result` blocks with `tool_use_id` and assert the output has `toolUseId` (will fail on unfixed code — `toMessageCamelCase` passes content through as-is)
5. **Scroll Position Save Test**: Simulate tab switch and assert scroll position is saved in the source tab's `UnifiedTab` (will fail on unfixed code — no `scrollPosition` field exists)

**Expected Counterexamples**:
- `summarize_tool_use("mcp__GitHub__create_issue", {"title": "Fix bug"})` returns `"mcp__GitHub__create_issue: Fix bug"` instead of a clean label
- `get_tool_category("mcp__aws_outlook_mcp__email_search")` returns `"fallback"` instead of `"search"`
- SSE content blocks retain `tool_use_id` (snake_case) causing `resultMap.get(block.toolUseId)` to return `undefined`

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed functions produce the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition_Bug1(input) DO
  result := summarize_tool_use_fixed(input.name, input.input_data)
  ASSERT result does NOT contain raw MCP name with double underscores
  ASSERT result contains extracted tool_name or meaningful category label
END FOR

FOR ALL input WHERE isBugCondition_Bug2(input) DO
  normalized := normalizeSSEEvent(input)
  FOR EACH block IN normalized.content DO
    IF block.type == "tool_result" THEN
      ASSERT block HAS FIELD "toolUseId"
      ASSERT block HAS FIELD "isError"
      ASSERT block DOES NOT HAVE FIELD "tool_use_id"
      ASSERT block DOES NOT HAVE FIELD "is_error"
    END IF
  END FOR
END FOR

FOR ALL input WHERE isBugCondition_Bug3(input) DO
  ASSERT sourceTab.scrollPosition == savedScrollTop AFTER switch
  ASSERT messagesContainer.scrollTop == targetTab.scrollPosition AFTER restore
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed functions produce the same result as the original functions.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition_Bug1(input) DO
  ASSERT summarize_tool_use_original(input) == summarize_tool_use_fixed(input)
  ASSERT get_tool_category_original(input) == get_tool_category_fixed(input)
END FOR

FOR ALL input WHERE NOT isBugCondition_Bug2(input) DO
  ASSERT normalizeSSEEvent(input) preserves all existing fields unchanged
END FOR

FOR ALL input WHERE NOT isBugCondition_Bug3(input) DO
  ASSERT handleTabSelect_fixed preserves all existing per-tab state fields
  ASSERT bumpStreamingDerivation is still used (not setIsStreaming)
END FOR
```

**Testing Approach**: Property-based testing is recommended for Bug 1 preservation checking because:
- It generates many tool name + input combinations automatically
- It catches edge cases in the MCP name extraction logic
- It provides strong guarantees that existing SDK tool labels are unchanged

### Unit Tests

- `test_summarize_tool_use_mcp_tools`: Test MCP tool names produce clean labels with extracted tool name
- `test_summarize_tool_use_mcp_category_extraction`: Test that MCP tools with recognizable segments get correct categories
- `test_summarize_tool_use_sdk_tools_unchanged`: Test all SDK built-in tools produce identical labels to before
- `test_get_tool_category_mcp_tools`: Test category extraction for MCP tool names
- `test_normalize_sse_content_blocks`: Test snake_case → camelCase conversion for tool_result blocks
- `test_normalize_sse_preserves_other_blocks`: Test that text and tool_use blocks are unchanged
- `test_scroll_position_saved_on_tab_switch`: Test that scrollTop is captured before switch
- `test_scroll_position_restored_on_tab_switch`: Test that scrollTop is set after switch

### Property-Based Tests

- Generate random MCP tool names (`mcp__{server}__{tool}` format) with random input dicts and verify the label never contains the raw full MCP name with `mcp__` prefix
- Generate random SDK tool names from the known sets and verify the fixed function produces identical output to the original
- Generate random SSE events with mixed content block types and verify only `tool_result` blocks get case-converted, all other blocks are unchanged
- Generate random scroll positions and tab switch sequences and verify each tab's scroll position is independently maintained

### Integration Tests

- Test full streaming session with MCP tools: verify labels display correctly in the UI
- Test multi-tool streaming turn: verify each tool's spinner stops independently as results arrive
- Test multi-tab workflow: open 3 tabs, scroll to different positions, switch between them, verify each tab's scroll position is preserved
- Test tab switch during streaming: verify scroll position is saved/restored while background tab continues streaming

## PE Design Review Findings

| # | Category | Severity | Finding | Resolution |
|---|----------|----------|---------|------------|
| 1 | Correctness | 🔴 High | Bug 2 REST path has same snake_case bug — `toMessageCamelCase()` passes `data.content` through with NO field conversion | Fixed: design now covers both SSE and REST paths |
| 2 | Correctness | 🔴 High | Bug 1 root cause inconsistent with screenshot — "Searching web for..." prefix doesn't match fallback format `"{name}: {context}"` | Fixed: added note to verify actual root cause in exploratory test before implementing fix |
| 3 | Architecture | 🟡 Medium | Three separate SSE methods all do raw `JSON.parse` — fix must cover all three or extract shared helper | Fixed: design now specifies shared `parseSSEEvent()` helper |
| 4 | Correctness | 🟡 Medium | Token-based MCP matching has collision risk (e.g., `bash_runner` → `bash` matches `_BASH_NAMES`) | Fixed: design now specifies only LAST token matching |
| 5 | Correctness | 🟡 Medium | Single `requestAnimationFrame` may fire before React commits — need double-rAF | Fixed: design now specifies double-rAF pattern |
| 6 | Correctness | 🟡 Medium | `userScrolledUpRef` recomputation must happen after double-rAF, not before | Fixed: design now specifies recomputation inside double-rAF callback |
| 7 | API Design | 🟡 Medium | `toCamelCaseContentBlock()` should defensively handle all block types | Fixed: design now specifies defensive handling |
| 8 | Simplicity | 🟢 Low | MCP fallback label format — consider dropping "mcp:" prefix | Left as suggestion |
| 9 | Observability | 🟢 Low | No logging for SSE case conversion | Left as suggestion |
| 10 | Testing | 🟢 Low | PBT should cover edge cases: empty names, `"mcp__"`, 4+ segments | Left as suggestion |

### Async & Race Condition Review (Pass 2)

| # | Category | Severity | Finding | Resolution |
|---|----------|----------|---------|------------|
| 11 | Race Condition | 🔴 High | Scroll restore vs auto-scroll effect: `setMessages()` triggers `[messages]` effect which calls `scrollToBottom()` BEFORE the double-rAF scroll restore fires. User sees flash of scroll-to-bottom then jump to saved position. | Fixed: design now mandates setting `userScrolledUpRef.current = true` BEFORE `setMessages()` in tab-switch path |
| 12 | Race Condition | 🔴 High | Scroll restore vs async `loadSessionMessages`: for tabs with empty messages, `loadSessionMessages` is async — scroll restore in `handleTabSelect` fires before messages are in the DOM. `scrollTop` has no effect. | Fixed: design now specifies scroll restore must happen AFTER `loadSessionMessages` completes, not in synchronous `handleTabSelect` |
| 13 | Race Condition | 🟡 Medium | Rapid tab switching: double-rAF from A→B switch fires after B→C switch started, corrupting Tab C's scroll position | Fixed: design now mandates async guard `activeTabIdRef.current === tabId` in double-rAF callback |
| 14 | Race Condition | 🟡 Medium | SSE conversion in `createStreamHandler` (alternative approach) would cause `blockKey()` to read `toolUseId` as `undefined`, breaking dedup — all tool_result blocks collide on key `tool_result:undefined` | Fixed: design now mandates parse-level conversion in `chat.ts` only, alternative approach removed |
| 15 | Race Condition | ✅ Safe | Background tab stream handler + case conversion: both paths receive already-converted blocks from parse level | No action needed |
| 16 | Race Condition | 🟢 Low | `handleNewSession` scroll save is redundant (new tab has no scroll to restore) but harmless | Left as-is |
