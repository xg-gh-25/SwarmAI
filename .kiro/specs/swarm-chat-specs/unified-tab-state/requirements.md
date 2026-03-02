# Requirements Document: Unified Tab State

## Introduction

This feature consolidates tab state management in the SwarmAI chat experience into a single source of truth. Currently, tab state is spread across three separate stores (`useTabState`, `tabStateRef`, `tabStatuses`), requiring every tab operation to update all three in lockstep. This creates drift risk, maintenance burden, and subtle bugs when stores fall out of sync. The unified hook (`useUnifiedTabState`) replaces all three with a single `useRef<Map<string, UnifiedTab>>` backed by a `useState` re-render counter, deriving `openTabs` and `tabStatuses` views via `useMemo`.

## Glossary

- **Unified_Hook**: The `useUnifiedTabState` React hook that serves as the single source of truth for all tab state, replacing `useTabState`, `tabStateRef`, and `tabStatuses`.
- **UnifiedTab**: A data structure combining metadata fields (id, title, agentId, isNew, sessionId) with runtime state fields (messages, pendingQuestion, isStreaming, abortController, streamGen, status) for a single tab.
- **Tab_Map**: The internal `useRef<Map<string, UnifiedTab>>` that holds all tab entries keyed by tab ID. This is the authoritative store.
- **Render_Counter**: A `useState` integer counter incremented on every mutation to the Tab_Map, triggering React re-renders.
- **Serializable_Subset**: The fields of UnifiedTab persisted to localStorage: id, title, agentId, isNew, sessionId.
- **TabStatus**: An enumeration of tab activity states: `idle`, `streaming`, `waiting_input`, `permission_needed`, `error`, `complete_unread`.
- **Active_Tab**: The tab currently displayed in the foreground, identified by `activeTabId`.
- **MAX_OPEN_TABS**: The maximum number of concurrently open tabs, set to 6.
- **ChatPage**: The top-level React component (`ChatPage.tsx`) that orchestrates the chat experience and consumes the Unified_Hook.
- **Streaming_Lifecycle_Hook**: The `useChatStreamingLifecycle` hook that currently owns `tabStateRef` and `tabStatuses`.

## Requirements

### Requirement 1: Single Authoritative Store

**User Story:** As a developer, I want all tab state consolidated into one store, so that I never need to synchronize multiple stores when performing tab operations.

#### Acceptance Criteria

1. THE Unified_Hook SHALL store all tab entries in a single Tab_Map of type `useRef<Map<string, UnifiedTab>>`.
2. THE Unified_Hook SHALL use a Render_Counter of type `useState<number>` to trigger React re-renders when the Tab_Map is mutated.
3. WHEN any public mutation method (addTab, closeTab, updateTabState, updateTabTitle, updateTabSessionId, setTabIsNew, updateTabStatus) modifies the Tab_Map, THE Unified_Hook SHALL increment the Render_Counter.
4. THE Unified_Hook SHALL derive `openTabs` as an ordered array from the Tab_Map via `useMemo` keyed on the Render_Counter.
5. THE Unified_Hook SHALL derive `tabStatuses` as a `Record<string, TabStatus>` from the Tab_Map via `useMemo` keyed on the Render_Counter.
6. THE Unified_Hook SHALL derive `activeTab` as the UnifiedTab matching `activeTabId` from the Tab_Map via `useMemo` keyed on the Render_Counter.

### Requirement 2: Tab CRUD Operations

**User Story:** As a user, I want to create, close, and switch between chat tabs, so that I can manage multiple concurrent conversations.

#### Acceptance Criteria

1. WHEN `addTab` is called with an agentId, THE Unified_Hook SHALL create a new UnifiedTab with a UUID id, title "New Session", the provided agentId, isNew set to true, empty messages array, null pendingQuestion, isStreaming false, null abortController, streamGen 0, and status "idle".
2. WHEN `addTab` is called, THE Unified_Hook SHALL set the new tab as the Active_Tab.
3. WHEN `addTab` is called and the number of open tabs equals MAX_OPEN_TABS, THE Unified_Hook SHALL reject the operation and return undefined.
4. WHEN `closeTab` is called with a tabId, THE Unified_Hook SHALL remove the tab entry from the Tab_Map.
5. WHEN `closeTab` is called on the Active_Tab and other tabs remain, THE Unified_Hook SHALL set the adjacent tab (by index, clamped to bounds) as the new Active_Tab.
6. WHEN `closeTab` is called and the closed tab has a non-null abortController, THE Unified_Hook SHALL invoke the abortController before removing the tab.
7. WHEN `closeTab` removes the last remaining tab, THE Unified_Hook SHALL auto-create a new "New Session" tab and set the new tab as Active_Tab.
8. WHEN `selectTab` is called with a tabId that exists in the Tab_Map, THE Unified_Hook SHALL set that tab as the Active_Tab.

### Requirement 3: Tab Metadata Updates

**User Story:** As a developer, I want to update tab metadata (title, sessionId, isNew flag) through the unified hook, so that tab display state stays consistent with backend state.

#### Acceptance Criteria

1. WHEN `updateTabTitle` is called with a tabId and title, THE Unified_Hook SHALL update the title field of the matching UnifiedTab in the Tab_Map.
2. WHEN `updateTabSessionId` is called with a tabId and sessionId, THE Unified_Hook SHALL update the sessionId field of the matching UnifiedTab in the Tab_Map.
3. WHEN `setTabIsNew` is called with a tabId and a boolean value, THE Unified_Hook SHALL update the isNew field of the matching UnifiedTab in the Tab_Map.
4. IF `updateTabTitle`, `updateTabSessionId`, or `setTabIsNew` is called with a tabId that does not exist in the Tab_Map, THEN THE Unified_Hook SHALL perform no mutation and not increment the Render_Counter.

### Requirement 4: Per-Tab Runtime State Management

**User Story:** As a developer, I want to read and update per-tab runtime state (messages, pendingQuestion, streaming flags, status) through the unified hook, so that background tabs preserve their state while the foreground tab renders.

#### Acceptance Criteria

1. WHEN `getTabState` is called with a tabId, THE Unified_Hook SHALL return the full UnifiedTab entry from the Tab_Map, or undefined if the tabId does not exist.
2. WHEN `updateTabState` is called with a tabId and a partial patch object, THE Unified_Hook SHALL merge the patch into the existing UnifiedTab entry using shallow merge. The patch type SHALL exclude the `id` field to prevent primary key corruption.
3. WHEN `updateTabStatus` is called with a tabId and a TabStatus value, THE Unified_Hook SHALL update the status field of the matching UnifiedTab in the Tab_Map.
4. IF `updateTabState` or `updateTabStatus` is called with a tabId that does not exist in the Tab_Map, THEN THE Unified_Hook SHALL perform no mutation and not increment the Render_Counter.

### Requirement 5: Tab Lifecycle Operations

**User Story:** As a developer, I want save/restore/init/cleanup lifecycle methods on the unified hook, so that tab switching preserves per-tab state without data loss.

#### Acceptance Criteria

1. WHEN `saveCurrentTab` is called, THE Unified_Hook SHALL write the current foreground React state (messages, sessionId, pendingQuestion, isStreaming, streamGen, status) into the Active_Tab entry in the Tab_Map.
2. WHEN `restoreTab` is called with a tabId that exists in the Tab_Map, THE Unified_Hook SHALL return true and the caller can read the tab's state via `getTabState`.
3. WHEN `restoreTab` is called with a tabId that does not exist in the Tab_Map, THE Unified_Hook SHALL return false.
4. WHEN `initTabState` is called with a tabId and optional initial messages, THE Unified_Hook SHALL create a new entry in the Tab_Map with default runtime state values and the provided messages.
5. WHEN `cleanupTabState` is called with a tabId, THE Unified_Hook SHALL invoke the tab's abortController if non-null and remove the tab's entry from the Tab_Map.

### Requirement 6: localStorage Persistence

**User Story:** As a user, I want my open tabs to persist across app restarts, so that I can resume my sessions without losing my tab layout.

#### Acceptance Criteria

1. WHEN the Tab_Map is mutated, THE Unified_Hook SHALL persist the Serializable_Subset (id, title, agentId, isNew, sessionId) of each tab to localStorage.
2. WHEN the Unified_Hook initializes, THE Unified_Hook SHALL read persisted tabs from localStorage and populate the Tab_Map with the serializable fields plus default runtime state values (empty messages, null pendingQuestion, isStreaming false, null abortController, streamGen 0, status "idle").
3. WHEN localStorage contains no valid persisted tabs, THE Unified_Hook SHALL create a single "New Session" tab as the default.
4. THE Unified_Hook SHALL persist the activeTabId to localStorage separately.
5. WHEN the Unified_Hook initializes with a persisted activeTabId, THE Unified_Hook SHALL validate that the activeTabId references an existing tab in the restored Tab_Map; IF the activeTabId does not reference an existing tab, THEN THE Unified_Hook SHALL fall back to the first tab's id.
6. THE Unified_Hook SHALL NOT persist runtime state fields (messages, pendingQuestion, isStreaming, abortController, streamGen, status) to localStorage.
7. WHEN `localStorage.setItem` throws an error (e.g., quota exceeded), THE Unified_Hook SHALL catch the error silently and continue operating with in-memory state as authoritative.

### Requirement 7: Invalid Tab Cleanup

**User Story:** As a developer, I want to remove tabs that reference deleted backend sessions, so that stale tabs do not confuse users.

#### Acceptance Criteria

1. WHEN `removeInvalidTabs` is called with a set of valid session IDs, THE Unified_Hook SHALL identify tabs whose sessionId is defined and not present in the valid set.
2. WHEN a tab is identified as invalid, THE Unified_Hook SHALL clear the tab's sessionId to undefined, set isNew to true, and set title to "New Session".
3. IF no tabs are identified as invalid, THEN THE Unified_Hook SHALL not increment the Render_Counter.

### Requirement 8: Per-Tab State Isolation

**User Story:** As a user, I want each tab's conversation state to be independent, so that actions in one tab do not affect another tab's messages or status.

#### Acceptance Criteria

1. WHEN `updateTabState` is called for a specific tabId, THE Unified_Hook SHALL modify only the entry matching that tabId in the Tab_Map.
2. FOR ALL pairs of distinct tabs A and B in the Tab_Map, updating tab A's messages, pendingQuestion, isStreaming, or status SHALL NOT modify any field of tab B's entry.
3. WHEN `updateTabStatus` is called for a specific tabId, THE Unified_Hook SHALL modify only the status field of the entry matching that tabId in the Tab_Map.

### Requirement 9: Integration and Migration

**User Story:** As a developer, I want the unified hook to be a drop-in replacement for the three existing stores, so that ChatPage and useChatStreamingLifecycle can migrate incrementally.

#### Acceptance Criteria

1. THE Unified_Hook SHALL expose an API surface that covers all operations currently provided by `useTabState` (addTab, closeTab, selectTab, updateTabTitle, updateTabSessionId, setTabIsNew, removeInvalidTabs, openTabs, activeTabId).
2. THE Unified_Hook SHALL expose an API surface that covers all operations currently provided by `tabStateRef` (saveTabState, restoreTabState, initTabState, cleanupTabState, getTabState, updateTabState).
3. THE Unified_Hook SHALL expose an API surface that covers all operations currently provided by `tabStatuses` (tabStatuses record, updateTabStatus).
4. WHEN the Unified_Hook is adopted in ChatPage, THE ChatPage SHALL remove its dependency on `useTabState`, the `tabStateRef` from Streaming_Lifecycle_Hook, and the `tabStatuses` from Streaming_Lifecycle_Hook.
5. WHEN the Unified_Hook is adopted, THE `useTabState.ts` file SHALL be deleted.
6. WHEN the Unified_Hook is adopted, THE Streaming_Lifecycle_Hook SHALL remove `tabStateRef`, `tabStatuses`, and all tab lifecycle methods (saveTabState, restoreTabState, initTabState, cleanupTabState, updateTabStatus) from its return interface.

### Requirement 10: Tab Operation Invariants

**User Story:** As a developer, I want formal guarantees about tab state consistency, so that property tests can verify correctness under arbitrary operation sequences.

#### Acceptance Criteria

1. FOR ALL sequences of tab operations applied to the Unified_Hook, the Tab_Map SHALL contain at least one tab after each operation.
2. FOR ALL sequences of tab operations applied to the Unified_Hook, the activeTabId SHALL reference a tab that exists in the Tab_Map after each operation.
3. FOR ALL sequences of tab operations applied to the Unified_Hook, no two tabs in the Tab_Map SHALL share the same id.
4. FOR ALL sequences of tab operations applied to the Unified_Hook, the number of tabs in the Tab_Map SHALL NOT exceed MAX_OPEN_TABS.
5. WHEN `closeTab` removes the last tab, THE Unified_Hook SHALL create a new tab such that the Tab_Map contains exactly one tab and activeTabId references that tab.
