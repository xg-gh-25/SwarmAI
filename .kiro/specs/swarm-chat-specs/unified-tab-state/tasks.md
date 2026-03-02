# Implementation Plan: Unified Tab State

## Overview

Consolidate the three separate tab state stores (`useTabState`, `tabStateRef`, `tabStatuses`) into a single `useUnifiedTabState` hook. Implementation follows the design's three-phase migration: build the new hook, wire it into ChatPage and streaming lifecycle, then update downstream consumers and delete legacy code.

## Tasks

- [x] 1. Implement `useUnifiedTabState` hook
  - [x] 1.1 Create `desktop/src/hooks/useUnifiedTabState.ts` with `UnifiedTab`, `SerializableTab`, `TabStatus` types and `UseUnifiedTabStateReturn` interface
    - Define all types and interfaces from the design document
    - Export `TabStatus` type for downstream consumers
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_

  - [x] 1.2 Implement Tab_Map, Render_Counter, activeTabId state, and localStorage initialization
    - `useRef<Map<string, UnifiedTab>>` for Tab_Map
    - `useState<number>` for Render_Counter
    - `useState<string | null>` for activeTabId with `useRef` mirror
    - Read `swarmAI_openTabs` and `swarmAI_activeTabId` from localStorage on mount
    - Fall back to single default "New Session" tab if localStorage is empty/corrupt
    - Validate persisted activeTabId references an existing tab; fall back to first tab if not
    - _Requirements: 1.1, 1.2, 6.2, 6.3, 6.5_

  - [x] 1.3 Implement `useMemo` derived views: `openTabs`, `tabStatuses`, `activeTab`
    - `openTabs` as ordered array from Tab_Map keyed on Render_Counter
    - `tabStatuses` as `Record<string, TabStatus>` keyed on Render_Counter
    - `activeTab` keyed on Render_Counter and activeTabId
    - _Requirements: 1.4, 1.5, 1.6_

  - [x] 1.4 Implement tab CRUD methods: `addTab`, `closeTab`, `selectTab`
    - `addTab(agentId)`: create UnifiedTab with defaults, set as active, reject at MAX_OPEN_TABS (return undefined)
    - `closeTab(tabId)`: abort controller if non-null, remove from map, reselect adjacent tab, auto-create if last tab
    - `selectTab(tabId)`: set activeTabId if tab exists
    - All mutations increment Render_Counter exactly once
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_

  - [x] 1.5 Implement metadata update methods: `updateTabTitle`, `updateTabSessionId`, `setTabIsNew`
    - Guard with `Map.has(tabId)` — no-op if tab doesn't exist
    - Increment Render_Counter only on actual mutation
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [x] 1.6 Implement runtime state methods: `getTabState`, `updateTabState`, `updateTabStatus`
    - `getTabState` returns full UnifiedTab or undefined
    - `updateTabState` shallow-merges patch (excluding `id` field) into existing entry
    - `updateTabStatus` updates only the status field
    - Guard with `Map.has(tabId)` — no-op if tab doesn't exist
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 8.1, 8.2, 8.3_

  - [x] 1.7 Implement lifecycle methods: `saveCurrentTab`, `restoreTab`, `initTabState`, `cleanupTabState`
    - `saveCurrentTab` writes foreground React state into active tab entry
    - `restoreTab` returns true/false based on tab existence
    - `initTabState` creates entry with default runtime state + optional initial messages
    - `cleanupTabState` aborts controller and removes entry
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [x] 1.8 Implement `removeInvalidTabs` and localStorage persistence effect
    - `removeInvalidTabs(validSessionIds)`: reset stale tabs (clear sessionId, set isNew true, title "New Session"); no-op if none invalid
    - `useEffect` on Render_Counter: persist SerializableTab subset to localStorage, catch errors silently
    - Persist activeTabId separately
    - Only metadata-changing mutations trigger persistence (not updateTabState/updateTabStatus)
    - _Requirements: 6.1, 6.4, 6.6, 6.7, 7.1, 7.2, 7.3_

  - [x] 1.9 Assemble and export the hook return object with all methods and refs
    - Return `UseUnifiedTabStateReturn` with all derived views, CRUD, metadata, runtime, lifecycle, cleanup methods, and direct ref access
    - _Requirements: 9.1, 9.2, 9.3_

- [x] 2. Checkpoint - Verify hook implementation
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Property-based and unit tests for `useUnifiedTabState`
  - [x] 3.1 Write property test: Tab Operation Invariants
    - **Property 1: Tab Operation Invariants**
    - Generate random sequences of operations (addTab, closeTab, selectTab, metadata updates), assert all four invariants after every step
    - Use `fc.array(fc.oneof(...))` with minimum 100 iterations
    - Test file: `desktop/src/hooks/__tests__/useUnifiedTabState.test.ts`
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5, 2.3, 2.7**

  - [x] 3.2 Write property test: Per-Tab State Isolation
    - **Property 2: Per-Tab State Isolation**
    - Generate map with 2+ tabs, apply random patch to tab A, verify tab B unchanged via deep-equal
    - Minimum 100 iterations
    - **Validates: Requirements 8.1, 8.2, 8.3**

  - [x] 3.3 Write property test: addTab Produces Correct Defaults
    - **Property 3: addTab Produces Correct Defaults**
    - Generate random agentId strings, call addTab, assert all default field values match spec
    - Minimum 100 iterations
    - **Validates: Requirements 2.1, 2.2**

  - [x] 3.4 Write property test: closeTab Removes and Reselects
    - **Property 4: closeTab Removes and Reselects**
    - Generate maps with 2+ tabs, close a tab, verify removal, reselection, and abort behavior
    - Minimum 100 iterations
    - **Validates: Requirements 2.4, 2.5, 2.6**

  - [x] 3.5 Write property test: Metadata Updates Apply to Correct Tab
    - **Property 5: Metadata Updates Apply to Correct Tab**
    - Generate random tab + random values, apply each update method, verify only target field changed
    - Minimum 100 iterations
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4**

  - [x] 3.6 Write property test: localStorage Persistence Round-Trip
    - **Property 6: localStorage Persistence Round-Trip**
    - Generate random tab configurations, trigger persistence, read back from localStorage mock, compare serializable subset
    - Minimum 100 iterations
    - **Validates: Requirements 6.1, 6.2, 6.4, 6.6**

  - [x] 3.7 Write property test: removeInvalidTabs Resets Stale Tabs
    - **Property 7: removeInvalidTabs Resets Stale Tabs**
    - Generate tabs with random sessionIds, generate valid subset, call removeInvalidTabs, verify stale reset and valid unchanged
    - Minimum 100 iterations
    - **Validates: Requirements 7.1, 7.2**

  - [x] 3.8 Write unit tests for edge cases
    - addTab at MAX_OPEN_TABS returns undefined
    - closeTab on last tab auto-creates new tab
    - Initialization with empty localStorage creates default tab
    - Initialization with stale activeTabId falls back to first tab
    - updateTabState with non-existent tabId is a no-op
    - removeInvalidTabs with no invalid tabs does not trigger re-render
    - closeTab aborts streaming tab's controller
    - selectTab updates activeTabId and derived activeTab
    - Persistence excludes runtime fields from localStorage
    - **Validates: Requirements 2.3, 2.7, 6.2, 6.3, 6.5, 6.6, 4.4, 7.3, 2.6**

- [x] 4. Checkpoint - Verify all hook tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Wire `useUnifiedTabState` into ChatPage and streaming lifecycle
  - [x] 5.1 Update `desktop/src/pages/ChatPage.tsx` to use `useUnifiedTabState` instead of `useTabState`
    - Replace `useTabState` import with `useUnifiedTabState`
    - Destructure all needed methods and derived views from the unified hook
    - Remove `tabStateRef` and `tabStatuses` destructuring from `useChatStreamingLifecycle` return
    - Pass unified hook methods to `useChatStreamingLifecycle` instead of receiving tab lifecycle methods back
    - _Requirements: 9.4_

  - [x] 5.2 Update `desktop/src/hooks/useChatStreamingLifecycle.ts` to consume unified hook instead of owning tab state
    - Remove internal `tabStateRef` (useRef<Map>) creation
    - Remove internal `tabStatuses` (useState<Record>) creation
    - Remove `saveTabState`, `restoreTabState`, `initTabState`, `cleanupTabState`, `updateTabStatus` from return interface
    - Accept unified hook's `getTabState`, `updateTabState`, `updateTabStatus`, `tabMapRef` as parameters
    - _Requirements: 9.6_

- [x] 6. Checkpoint - Verify ChatPage integration works
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Update downstream consumers and delete legacy code
  - [x] 7.1 Update `desktop/src/pages/chat/components/ChatHeader.tsx` to import `TabStatus` from `useUnifiedTabState`
    - Replace any `TabStatus` import from `useChatStreamingLifecycle` with import from `useUnifiedTabState`
    - _Requirements: 9.3_

  - [x] 7.2 Update `desktop/src/pages/chat/components/SessionTabBar.tsx` to import from `useUnifiedTabState`
    - Update any tab-related type imports to reference the unified hook module
    - _Requirements: 9.3_

  - [x] 7.3 Update `desktop/src/pages/chat/components/TabStatusIndicator.tsx` to import `TabStatus` from `useUnifiedTabState`
    - Replace any `TabStatus` import from `useChatStreamingLifecycle` with import from `useUnifiedTabState`
    - _Requirements: 9.3_

  - [x] 7.4 Delete `desktop/src/hooks/useTabState.ts`
    - Remove the legacy hook file entirely
    - Verify no remaining imports reference `useTabState`
    - _Requirements: 9.5_

- [x] 8. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation between phases
- Property tests use fast-check with minimum 100 iterations per property
- The hook uses the same localStorage keys as the existing `useTabState` — no data migration needed
- Unit tests and property tests share the same test file: `desktop/src/hooks/__tests__/useUnifiedTabState.test.ts`
