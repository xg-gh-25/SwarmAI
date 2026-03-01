# Implementation Plan: Chat Experience Cleanup

## Overview

Implements 17 requirements across 4 phases (A–D) to clean up the SwarmAI chat experience layer. Each phase is independently shippable. Phase D tasks are optional higher-risk architectural refactors. All code is TypeScript/React in the `desktop/` workspace.

Test directory: `desktop/src/__tests__/chat-experience-cleanup/`

## Tasks

- [x] 1. Phase A — Quick Wins: Debug removal, dead code, memoization
  - [x] 1.1 Remove debug logging from `useChatStreamingLifecycle.ts` and `ChatPage.tsx`
    - Gate all `console.log` statements in `desktop/src/hooks/useChatStreamingLifecycle.ts` behind `if (import.meta.env.DEV)` guards
    - Remove the `setMessages` debug wrapper in `desktop/src/pages/ChatPage.tsx` that captures `new Error().stack` on every call — use `_rawSetMessages` directly
    - Ensure string interpolation and object allocation are skipped when the debug flag is disabled
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 1.2 Remove redundant `setMessages` call in `handleNewChat`
    - In `desktop/src/pages/ChatPage.tsx`, replace the double `setMessages([])` then `setMessages([createWelcomeMessage()])` with a single `setMessages([createWelcomeMessage()])` call
    - _Requirements: 6.1, 6.2_

  - [x] 1.3 Memoize `handleSendMessage` and `handlePluginCommand` in ChatPage
    - Wrap `handleSendMessage` in `useCallback` with refs for frequently-changing values (`inputValue`, `attachments`) to stabilize callback identity
    - Wrap `handlePluginCommand` in `useCallback` with correct dependencies
    - _Requirements: 7.1, 7.2, 7.3_

  - [x] 1.4 Memoize timeline merge computation in ChatPage
    - Replace the inline IIFE in JSX with a `useMemo` that merges messages + threadSnapshots into a sorted timeline
    - Dependency array: `[messages, threadSnapshots]`
    - _Requirements: 8.1, 8.2_

  - [x] 1.5 Fix missing dependencies in `loadSessionMessages` useCallback
    - Add `setMessages`, `setSessionId`, `setPendingQuestion` (and any other referenced outer-scope variables) to the dependency array
    - _Requirements: 9.1, 9.2_

  - [x] 1.6 Remove dead code from TSCC Panel
    - Remove `showResumed` state variable and its associated `useEffect` from `ExpandedView` in `desktop/src/pages/chat/components/TSCCPanel.tsx`
    - Simplify lifecycle label display to always use `lifecycleLabel(tsccState.lifecycleState)`
    - _Requirements: 11.1, 11.2_

  - [x] 1.7 Fix stale `DEFAULT_TSCC_STATE` timestamp
    - In `desktop/src/pages/chat/components/TSCCPanel.tsx`, replace the module-level `DEFAULT_TSCC_STATE` constant with a `createDefaultTSCCState()` factory function
    - Use `useMemo` in the component: `const effectiveState = useMemo(() => tsccState ?? createDefaultTSCCState(), [tsccState])`
    - _Requirements: 12.1, 12.2_

  - [x] 1.8 Differentiate pin icon visual state in TSCC Panel
    - Pinned: `push_pin` icon with `filled` variant (FILL:1) and 0° rotation
    - Unpinned: `push_pin` icon with `outlined` variant and 45° rotation
    - Preserve existing `aria-pressed` attribute on the pin button
    - _Requirements: 13.1, 13.2, 13.3_

  - [x] 1.9 Write unit tests for Phase A changes
    - Verify `handleNewChat` calls `setMessages` exactly once (Req 6)
    - Verify `loadSessionMessages` dependency array correctness (Req 9)
    - Verify dead code removal doesn't break TSCC panel rendering (Req 11)
    - Verify `createDefaultTSCCState()` produces fresh timestamps (Req 12)
    - Verify pin icon visual differentiation and `aria-pressed` attribute (Req 13)
    - Place tests in `desktop/src/__tests__/chat-experience-cleanup/phase-a.test.tsx`
    - _Requirements: 6.1, 9.1, 11.2, 12.2, 13.1, 13.2, 13.3_

- [x] 2. Checkpoint — Phase A complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Phase B — Algorithmic & Persistence: Set-based dedup, schema versioning, hardened 404
  - [x] 3.1 Implement Set-based duplicate detection in `updateMessages`
    - In `desktop/src/hooks/useChatStreamingLifecycle.ts`, extract the `updateMessages` pure function
    - Implement `blockKey()` helper that derives Set keys: `tool_use:${id}`, `tool_result:${toolUseId}`, `text:${text}`
    - Replace the `O(n×m)` nested `.some()` with a `Set<string>` lookup for `O(n+m)` complexity
    - Return same message reference when no new content is added (referential stability)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 3.2 Write property test: updateMessages behavioral equivalence
    - **Property 1: updateMessages Behavioral Equivalence**
    - Generate random message arrays with mixed content block types (`text`, `tool_use`, `tool_result`)
    - Compare output of original nested-iteration implementation vs optimized Set-based implementation
    - Place in `desktop/src/__tests__/chat-experience-cleanup/update-messages.property.test.ts`
    - **Validates: Requirements 2.5**

  - [x] 3.3 Add schema versioning to session persistence
    - Add `version: number` field to `PersistedPendingState` interface
    - Export `PERSISTED_STATE_VERSION = 1` constant
    - Update `persistPendingState` to write `version: PERSISTED_STATE_VERSION` into the payload
    - Update `restorePendingState` to compare `parsed.version === PERSISTED_STATE_VERSION`; discard and return `null` on mismatch
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 3.4 Write property test: Persist/restore round-trip
    - **Property 2: Persist/Restore Round-Trip**
    - Generate random valid `PersistedPendingState` objects with matching version
    - Persist to sessionStorage, restore by same sessionId, assert deep equality on post-truncation payload
    - Place in `desktop/src/__tests__/chat-experience-cleanup/persistence.property.test.ts`
    - **Validates: Requirements 3.5**

  - [x] 3.5 Write property test: Version mismatch discards state
    - **Property 3: Version Mismatch Discards State**
    - Generate random `PersistedPendingState`, persist with version V, change `PERSISTED_STATE_VERSION` to W≠V, assert `restorePendingState` returns `null`
    - Place in `desktop/src/__tests__/chat-experience-cleanup/persistence.property.test.ts`
    - **Validates: Requirements 3.4**

  - [x] 3.6 Harden stale entry cleanup 404 detection
    - In `desktop/src/hooks/useChatStreamingLifecycle.ts`, implement `isNotFoundError(err: unknown): boolean`
    - Check `err.response.status === 404` (Axios-style), then `err.status === 404` (custom API errors)
    - Return `false` for errors without structured status (treat as indeterminate, skip cleanup)
    - Remove all `err.message.includes(...)` substring checks
    - _Requirements: 4.1, 4.2, 4.3_

  - [x] 3.7 Write property test: Structured 404 detection
    - **Property 4: Structured 404 Detection**
    - Generate random error objects: with `status` property, with `response.status`, plain `Error` with arbitrary messages
    - Assert `isNotFoundError` returns `true` iff status === 404; returns `false` for errors without structured status
    - Place in `desktop/src/__tests__/chat-experience-cleanup/error-detection.property.test.ts`
    - **Validates: Requirements 4.2, 4.3**

- [x] 4. Checkpoint — Phase B complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Phase C — UX Polish: Visibility-based polling pause and debounced expand/collapse
  - [x] 5.1 Pause polling when ContextPreviewPanel is off-screen
    - In `desktop/src/components/workspace/ContextPreviewPanel.tsx`, add `isPageVisible` state using the Page Visibility API
    - Add a `useEffect` that listens to `visibilitychange` events and updates `isPageVisible`
    - Modify the polling `useEffect` to include `isPageVisible` in its dependency array
    - Only start the polling interval when `!collapsed && isPageVisible`
    - Clean up interval on visibility change
    - _Requirements: 14.1, 14.2, 14.3_

  - [x] 5.2 Debounce rapid expand/collapse fetch calls
    - In `desktop/src/components/workspace/ContextPreviewPanel.tsx`, add a 300ms debounce timer before firing the initial fetch on expand
    - Start polling only after the debounced fetch completes
    - Use a `cancelled` flag pattern to prevent state updates after unmount or re-toggle
    - Clean up both debounce timer and polling interval on effect cleanup
    - _Requirements: 15.1, 15.2_

  - [x] 5.3 Write property test: Polling pauses when not visible
    - **Property 5: Polling Pauses When Not Visible**
    - Mock `document.hidden`, verify no fetch requests fire while hidden
    - Verify polling resumes within one interval after visibility returns
    - Place in `desktop/src/__tests__/chat-experience-cleanup/context-preview.property.test.ts`
    - **Validates: Requirements 14.1, 14.2**

  - [x] 5.4 Write property test: Debounce limits fetch calls
    - **Property 6: Debounce Limits Fetch Calls**
    - Generate random sequences of N expand/collapse toggles within a 300ms window
    - Assert at most one fetch request is initiated per 300ms window of toggle inactivity
    - Place in `desktop/src/__tests__/chat-experience-cleanup/context-preview.property.test.ts`
    - **Validates: Requirements 15.1, 15.2**

  - [x] 5.5 Write unit tests for Phase C changes
    - Verify polling resumes on visibility change
    - Verify debounce timer cleanup on unmount
    - Verify no concurrent fetch requests during rapid toggling
    - Place in `desktop/src/__tests__/chat-experience-cleanup/context-preview.test.tsx`
    - _Requirements: 14.1, 14.2, 15.1, 15.2_

- [x] 6. Checkpoint — Phase C complete
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Phase D — Architectural Refactors (DEFERRED — see notes below)
  - [ ]* 7.1 Decompose streaming lifecycle hook into grouped sub-interfaces
    - Define `MessageState`, `StreamingControl`, `ScrollControl`, `TabLifecycle` interfaces in `desktop/src/hooks/useChatStreamingLifecycle.ts`
    - Refactor the hook return value to group fields into these sub-interfaces under a single `ChatStreamingLifecycle` object
    - Update `desktop/src/pages/ChatPage.tsx` to destructure from sub-interfaces: `const { messageState, streaming, scroll, tabs } = useChatStreamingLifecycle(deps)`
    - Update all consuming components that reference the flat fields
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [ ]* 7.2 Consolidate overlapping useEffect hooks in ChatPage
    - In `desktop/src/pages/ChatPage.tsx`, identify all `useEffect` hooks that watch `activeTabId` and mutate `messages`/`sessionId`
    - Consolidate into a single tab-switching `useEffect` using a `prevActiveTabIdRef` pattern
    - Handle tab registration, restore from per-tab map, and load from API in a single effect
    - _Requirements: 10.1, 10.2_

  - [ ]* 7.3 Decouple TSCC state hook from streaming lifecycle hook
    - Define `TSCCCallbacks` interface: `{ applyTelemetryEvent, triggerAutoExpand }`
    - Update `desktop/src/hooks/useChatStreamingLifecycle.ts` to accept `TSCCCallbacks` instead of direct hook references
    - Update `desktop/src/hooks/useTSCCState.ts` to remove any direct imports from the streaming lifecycle hook
    - Update `desktop/src/pages/ChatPage.tsx` to mediate communication by creating callback refs and passing them to both hooks
    - Verify neither hook imports from the other
    - _Requirements: 16.1, 16.2, 16.3, 16.4_

  - [ ]* 7.4 Consolidate tab state into `useUnifiedTabState`
    - Create `desktop/src/hooks/useUnifiedTabState.ts` with the `UnifiedTab` interface and `UseUnifiedTabStateReturn` API
    - Internal state: `useRef<Map<string, UnifiedTab>>` as single authoritative store with a `useState` counter for re-render triggers
    - Derive `openTabs` (ordered array) and `tabStatuses` (Record) via `useMemo` keyed on the re-render counter
    - Persist only serializable subset (id, title, agentId, isNew, sessionId) to localStorage
    - Implement all tab operations: add, close, select, updateTitle, updateSessionId, updateStatus, saveCurrentTab, restoreTab, removeInvalidTabs
    - _Requirements: 17.1, 17.2, 17.3, 17.4_

  - [ ]* 7.5 Wire `useUnifiedTabState` into ChatPage and streaming lifecycle hook
    - Replace `useTabState`, `tabStateRef`, and `tabStatuses` usage in `desktop/src/pages/ChatPage.tsx` with `useUnifiedTabState`
    - Update `desktop/src/hooks/useChatStreamingLifecycle.ts` to use `TabLifecycle` sub-interface backed by `useUnifiedTabState`
    - Remove the old `desktop/src/hooks/useTabState.ts` or mark as deprecated
    - Verify all tab operations (add, close, select, save, restore, init, cleanup, status updates) work end-to-end
    - _Requirements: 17.1, 17.2, 17.3, 17.4_

  - [ ]* 7.6 Write property test: Tab operation invariants
    - **Property 7: Tab Operation Invariants**
    - Generate random sequences of tab operations (add, close, select, updateTitle, updateSessionId, updateStatus)
    - After each operation assert: at least one tab exists, `activeTabId` references an existing tab, no duplicate tab IDs, closing last tab auto-creates a new one
    - Place in `desktop/src/__tests__/chat-experience-cleanup/unified-tab-state.property.test.ts`
    - **Validates: Requirements 17.3**

  - [ ]* 7.7 Write property test: Per-tab state isolation
    - **Property 8: Per-Tab State Isolation**
    - Generate random pairs of tabs and state updates (messages, pendingQuestion, isStreaming, status)
    - Assert updating tab A's state does not modify any field of tab B's state
    - Place in `desktop/src/__tests__/chat-experience-cleanup/unified-tab-state.property.test.ts`
    - **Validates: Requirements 17.4**

- [x] 8. Final checkpoint — All phases complete
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Phase D (task 7) sub-tasks are all optional — these are higher-risk architectural refactors that touch multiple files
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation between phases
- Property tests use `fast-check` library with minimum 100 iterations per property
- All test files go in `desktop/src/__tests__/chat-experience-cleanup/`

## Spec Closure — Phase D Deferral (2026-03-01)

Phases A–C are complete. Phase D was evaluated against the current codebase and deferred:

| Task | Decision | Rationale |
|------|----------|-----------|
| 7.1 Sub-interfaces | Skip | Comment-based grouping is sufficient; only one consumer (ChatPage) |
| 7.2 Consolidate effects | Skip | Current separation is clearer; `prevActiveTabIdRef` guard handles coordination |
| 7.3 Decouple TSCC | Already done | `useTSCCState` doesn't import from streaming hook; ChatPage mediates via refs |
| 7.4/7.5 Unified tab state | Deferred | High value but high risk — deserves its own spec with focused property tests |
| 7.6/7.7 Tab property tests | Blocked | Depends on 7.4/7.5 |

See: `.kiro/specs/unified-tab-state/` for the follow-up spec (when created).
