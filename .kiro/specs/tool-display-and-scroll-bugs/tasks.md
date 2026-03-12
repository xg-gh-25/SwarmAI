# Implementation Plan

- [x] 1. Write bug condition exploration tests
  - **Property 1: Bug Condition** — Tool Display & Scroll Bugs
  - **CRITICAL**: These tests MUST FAIL on unfixed code — failure confirms the bugs exist
  - **DO NOT attempt to fix the tests or the code when they fail**
  - **NOTE**: These tests encode the expected behavior — they will validate the fixes when they pass after implementation
  - **GOAL**: Surface counterexamples that demonstrate all three bugs exist
  - **Scoped PBT Approach**: Scope properties to concrete failing cases for reproducibility

  - **Bug 1 — MCP Tool Label Misclassification** (backend/core/tool_summarizer.py):
    - Call `summarize_tool_use("mcp__GitHub__create_issue", {"title": "Fix bug"})` and assert label does NOT contain raw `mcp__` prefix
    - Call `summarize_tool_use("mcp__aws_outlook_mcp__email_search", {"query": "meeting notes"})` and assert label contains extracted tool name `email_search`, not full raw MCP name
    - Call `get_tool_category("mcp__aws_outlook_mcp__email_search")` and assert it returns `"search"`, not `"fallback"`
    - **IMPORTANT**: Verify actual root cause — design notes uncertainty about whether "Searching web for..." comes from individual tool block labels or the bottom streaming activity label. Test both `summarize_tool_use()` output format AND whether any MCP name unexpectedly matches `_WEB_SEARCH_NAMES`
    - Use property-based test: for random MCP names `mcp__{server}__{tool}` with random input dicts, assert label never contains raw `mcp__` prefix
  - **Bug 2 — SSE Content Block Case Conversion** (desktop/src/services/chat.ts):
    - Parse raw SSE event `{"type":"assistant","content":[{"type":"tool_result","tool_use_id":"toolu_abc","is_error":false}]}` and assert content block has `toolUseId` field (not `tool_use_id`)
    - Call `toMessageCamelCase()` with message containing `tool_result` blocks with `tool_use_id` and assert output has `toolUseId`
  - **Bug 3 — Cross-Tab Scroll Contamination** (desktop/src/hooks/useUnifiedTabState.ts, desktop/src/pages/ChatPage.tsx):
    - Assert `UnifiedTab` interface includes `scrollPosition` field
    - Simulate tab switch and assert scroll position is saved in source tab's `UnifiedTab`
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests FAIL (this is correct — it proves the bugs exist)
  - Document counterexamples found to understand root causes
  - Mark task complete when tests are written, run, and failures are documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_

- [x] 2. Write preservation property tests (BEFORE implementing fixes)
  - **Property 2: Preservation** — Existing Tool Labels, Streaming Lifecycle & Tab State
  - **IMPORTANT**: Follow observation-first methodology
  - **Bug 1 Preservation** (backend/core/tool_summarizer.py):
    - Observe: `summarize_tool_use("bash", {"command": "npm test"})` returns `"Running: npm test"` on unfixed code
    - Observe: `summarize_tool_use("read", {"path": "src/app.ts"})` returns `"Reading src/app.ts"` on unfixed code
    - Observe: `summarize_tool_use("websearch", {"query": "python docs"})` returns `"Searching web for python docs"` on unfixed code
    - Observe: `summarize_tool_use("unknown_tool", {})` returns `"Using unknown_tool"` on unfixed code
    - Write property-based test: for all SDK tool names from known category sets (`_BASH_NAMES`, `_READ_NAMES`, `_WRITE_NAMES`, `_SEARCH_NAMES`, `_WEB_FETCH_NAMES`, `_WEB_SEARCH_NAMES`, `_LIST_DIR_NAMES`, `_TODOWRITE_NAMES`), assert fixed function produces identical output to original function
    - Write property-based test: for tools with no recognizable input fields, assert `"Using {name}"` fallback is preserved
  - **Bug 2 Preservation** (desktop/src/services/chat.ts):
    - Observe: SSE events with `text` and `tool_use` content blocks pass through unchanged on unfixed code
    - Write test: for content blocks of type `text` and `tool_use`, assert no fields are modified by case conversion
    - Write test: `toMessageCamelCase()` continues to convert top-level message fields (role, model, stop_reason → stopReason, etc.)
  - **Bug 3 Preservation** (desktop/src/hooks/useUnifiedTabState.ts, desktop/src/pages/ChatPage.tsx):
    - Observe: tab switch saves/restores messages, sessionId, pendingQuestion, isExpanded, contextWarning on unfixed code
    - Write test: assert all existing per-tab state fields are still saved/restored after fix
    - Write test: assert `bumpStreamingDerivation()` is used during tab switch (not `setIsStreaming()`)
    - Write test: assert auto-scroll to bottom continues during streaming when user has not scrolled up
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

- [x] 3. Fix Bug 1 — MCP Tool Label Classification

  - [x] 3.1 Add `_extract_mcp_tool_name()` helper to `backend/core/tool_summarizer.py`
    - Create `_extract_mcp_tool_name(name: str) -> tuple[str | None, str]` that detects `mcp__ServerName__tool_name` pattern
    - Return `(server_name, tool_name)` for MCP tools, `(None, name)` for non-MCP tools
    - Handle edge cases: empty names, single `mcp__`, 4+ segments (use last segment as tool_name, second segment as server_name)
    - Add module-level docstring update per code documentation standards
    - _Bug_Condition: isBugCondition_Bug1(input) where name contains "__" and doesn't match any category set_
    - _Expected_Behavior: Label contains extracted tool_name or meaningful category, never raw mcp__ prefix_
    - _Requirements: 2.1, 2.2_

  - [x] 3.2 Add LAST-token-only category matching to `summarize_tool_use()` and `get_tool_category()`
    - After exact set membership fails, if MCP tool detected, extract final segment (e.g., `email_search` from `mcp__aws_outlook_mcp__email_search`)
    - Split extracted tool_name by `_` and check ONLY the LAST token against category sets
    - Example: `email_search` → last token `search` → matches `_SEARCH_NAMES` → category `search`
    - CRITICAL: Only last token to avoid collisions (e.g., `bash_runner` → `runner` → no match → fallback, NOT `bash`)
    - _Bug_Condition: MCP tool names never match category sets via exact membership_
    - _Expected_Behavior: Last-token matching provides category for MCP tools with recognizable suffixes_
    - _Preservation: SDK tools still matched by exact set membership first — token matching only runs for MCP tools_
    - _Requirements: 2.1, 2.2, 3.1, 3.2_

  - [x] 3.3 Improve MCP fallback format in `summarize_tool_use()`
    - When no category match found even after token extraction, format label as `"mcp: {tool_name} — {context}"` using extracted tool_name (not full raw MCP name)
    - Context comes from existing fallback field extraction logic (query, path, command, url, title, etc.)
    - If no context fields found, use `"mcp: {tool_name}"` (no trailing separator)
    - _Bug_Condition: MCP tools with no recognizable last-token category_
    - _Expected_Behavior: Clean fallback with extracted tool_name, not raw mcp__ServerName__tool_name_
    - _Requirements: 2.2_

  - [x] 3.4 Verify bug condition exploration test for Bug 1 now passes
    - **Property 1: Expected Behavior** — MCP Tool Labels Are Meaningful
    - **IMPORTANT**: Re-run the SAME Bug 1 tests from task 1 — do NOT write new tests
    - The tests from task 1 encode the expected behavior for MCP tool labels
    - When these tests pass, it confirms MCP tools get clean labels
    - Run Bug 1 exploration tests from step 1
    - **EXPECTED OUTCOME**: Tests PASS (confirms Bug 1 is fixed)
    - _Requirements: 2.1, 2.2_

  - [x] 3.5 Verify preservation tests for Bug 1 still pass
    - **Property 2: Preservation** — Existing SDK Tool Labels Unchanged
    - **IMPORTANT**: Re-run the SAME Bug 1 preservation tests from task 2 — do NOT write new tests
    - Run Bug 1 preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions to SDK tool labels)
    - Confirm WebSearch still shows "Searching web for {query}", generic fallback still shows "Using {name}"

- [x] 4. Fix Bug 2 — SSE and REST Content Block Case Conversion

  - [x] 4.1 Add `toCamelCaseContentBlock()` and `toCamelCaseContent()` helpers to `desktop/src/services/chat.ts`
    - `toCamelCaseContentBlock(block)`: convert `tool_use_id` → `toolUseId`, `is_error` → `isError` for `tool_result` blocks
    - Defensively handle all block types — only transform known snake_case fields, pass others through unchanged
    - `toCamelCaseContent(content)`: map over content array applying `toCamelCaseContentBlock` to each block
    - MANDATORY: conversion at `chat.ts` parse level, NOT in `createStreamHandler` (would break `blockKey()` dedup)
    - Update `toCamelCase()` function documentation per API naming convention (backend snake_case → frontend camelCase)
    - _Bug_Condition: tool_result blocks have tool_use_id (snake_case) but resultMap keys on toolUseId (camelCase)_
    - _Expected_Behavior: All content blocks have camelCase fields after parsing_
    - _Requirements: 2.3, 2.4_

  - [x] 4.2 Extract shared `parseSSEEvent()` helper for all three SSE methods
    - All three SSE streaming methods (`streamChat`, `streamAnswerQuestion`, `streamCmdPermissionContinue`) currently do raw `JSON.parse(data)`
    - Extract shared `parseSSEEvent(data: string)` that parses AND applies `toCamelCaseContent()` to content blocks
    - Update all three methods to call `parseSSEEvent()` instead of raw `JSON.parse()`
    - _Bug_Condition: Raw JSON.parse produces snake_case fields from backend_
    - _Expected_Behavior: Shared helper ensures consistent camelCase conversion across all SSE paths_
    - _Requirements: 2.3, 2.4_

  - [x] 4.3 Fix `toMessageCamelCase()` for REST path
    - Update `toMessageCamelCase()` to apply `toCamelCaseContent()` to `data.content` instead of passing through as-is
    - This fixes the same bug for messages loaded from DB via `getSessionMessages()` (tab switch, app restart)
    - _Bug_Condition: toMessageCamelCase passes content through with NO field conversion_
    - _Expected_Behavior: REST path content blocks also get camelCase conversion_
    - _Requirements: 2.3, 2.4_

  - [x] 4.4 Verify bug condition exploration test for Bug 2 now passes
    - **Property 1: Expected Behavior** — Per-Tool Spinner Stops on Result
    - **IMPORTANT**: Re-run the SAME Bug 2 tests from task 1 — do NOT write new tests
    - The tests from task 1 encode the expected behavior for content block case conversion
    - When these tests pass, it confirms `toolUseId` is available for `resultMap` lookup
    - Run Bug 2 exploration tests from step 1
    - **EXPECTED OUTCOME**: Tests PASS (confirms Bug 2 is fixed)
    - _Requirements: 2.3, 2.4_

  - [x] 4.5 Verify preservation tests for Bug 2 still pass
    - **Property 2: Preservation** — Streaming Lifecycle & Non-tool_result Blocks Unchanged
    - **IMPORTANT**: Re-run the SAME Bug 2 preservation tests from task 2 — do NOT write new tests
    - Run Bug 2 preservation tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions to text/tool_use blocks or top-level message fields)

- [x] 5. Fix Bug 3 — Per-Tab Scroll Position

  - [x] 5.1 Add `scrollPosition` to `UnifiedTab` interface in `desktop/src/hooks/useUnifiedTabState.ts`
    - Add `scrollPosition?: number` as runtime-only field (not persisted to `open_tabs.json`)
    - Default to `undefined` (meaning "scroll to bottom" for new tabs)
    - _Bug_Condition: UnifiedTab has no scrollPosition field, so tab switch cannot save/restore scroll state_
    - _Expected_Behavior: UnifiedTab includes scrollPosition for per-tab scroll tracking_
    - _Requirements: 2.5, 2.6_

  - [x] 5.2 Save scroll position in `handleTabSelect` and `handleNewSession` in `desktop/src/pages/ChatPage.tsx`
    - Before switching tabs, read `messagesContainerRef.current?.scrollTop` and save into current tab's `UnifiedTab` via `updateTabState(currentTabId, { scrollPosition: ... })`
    - Also save scroll position in `handleNewSession` before creating new tab
    - _Bug_Condition: handleTabSelect saves messages/sessionId/pendingQuestion but not scroll position_
    - _Expected_Behavior: Scroll position saved before every tab switch and new session creation_
    - _Requirements: 2.5, 2.6_

  - [x] 5.3 Restore scroll position with double-rAF and race condition guards in `ChatPage.tsx`
    - After restoring target tab's state from `tabMapRef`, read `tabState.scrollPosition`
    - Set `messagesContainerRef.current.scrollTop` after double-`requestAnimationFrame` (ensures React has committed new messages to DOM)
    - If `scrollPosition` is `undefined`, scroll to bottom (new tab behavior)
    - **CRITICAL race condition guards**:
      1. Set `userScrolledUpRef.current = true` BEFORE `setMessages(tabState.messages)` to suppress auto-scroll effect
      2. Async guard in double-rAF: check `activeTabIdRef.current === tabId` before applying scroll — prevents rapid tab switching corruption (A→B→C)
      3. For `loadSessionMessages` async path: defer scroll restore to AFTER load completes (synchronous double-rAF won't work — messages aren't in DOM yet)
      4. Recompute `userScrolledUpRef` INSIDE double-rAF callback (after DOM update) based on restored position vs scrollHeight — prevents stale auto-scroll suppression
    - _Bug_Condition: messagesContainerRef is shared DOM ref, scroll offset persists from previous tab_
    - _Expected_Behavior: Each tab's scroll position independently restored with no race conditions_
    - _Preservation: All existing per-tab state (messages, sessionId, pendingQuestion, isExpanded, contextWarning) still saved/restored_
    - _Requirements: 2.5, 2.6, 3.6, 3.7, 3.8_

  - [x] 5.4 Verify bug condition exploration test for Bug 3 now passes
    - **Property 1: Expected Behavior** — Independent Scroll Per Tab
    - **IMPORTANT**: Re-run the SAME Bug 3 tests from task 1 — do NOT write new tests
    - The tests from task 1 encode the expected behavior for per-tab scroll state
    - When these tests pass, it confirms scroll position is saved/restored per tab
    - Run Bug 3 exploration tests from step 1
    - **EXPECTED OUTCOME**: Tests PASS (confirms Bug 3 is fixed)
    - _Requirements: 2.5, 2.6_

  - [x] 5.5 Verify preservation tests for Bug 3 still pass
    - **Property 2: Preservation** — Tab Switch State Integrity
    - **IMPORTANT**: Re-run the SAME Bug 3 preservation tests from task 2 — do NOT write new tests
    - Run Bug 3 preservation tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions to existing tab state, streaming derivation, auto-scroll)

- [x] 6. Checkpoint — Ensure all tests pass
  - Run full backend test suite: `cd backend && pytest`
  - Run full frontend test suite: `cd desktop && npm test -- --run`
  - Verify all exploration tests (task 1) now PASS
  - Verify all preservation tests (task 2) still PASS
  - Verify no regressions in existing test suites
  - Ensure all tests pass, ask the user if questions arise
