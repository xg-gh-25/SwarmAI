# Requirements Document

## Introduction

This spec addresses 19 architectural and code-quality issues identified during a thorough code review of the SwarmAI chat experience layer. The affected files span the streaming lifecycle hook, the main chat page, the TSCC panel, the context preview panel, and cross-cutting state management. The cleanup targets debug code removal, performance improvements, memoization gaps, dead code elimination, and architectural simplification of state management.

## Glossary

- **Chat_Page**: The main chat page component (`ChatPage.tsx`) that orchestrates the chat UI, message handling, and tab management.
- **Streaming_Lifecycle_Hook**: The `useChatStreamingLifecycle` custom React hook that manages streaming state, message updates, session persistence, and tab isolation.
- **TSCC_Panel**: The Thread-Scoped Cognitive Context panel component (`TSCCPanel.tsx`) that displays live cognitive context above the chat input.
- **Context_Preview_Panel**: The collapsible context preview component (`ContextPreviewPanel.tsx`) that displays the 8-layer context assembly with token counts.
- **TSCC_State_Hook**: The `useTSCCState` custom React hook that manages TSCC state, expand/collapse preferences, and telemetry event application.
- **Tab_Status_Indicator**: The visual status indicator component (`TabStatusIndicator.tsx`) for chat tab headers.
- **Message_Updater**: The `updateMessages` pure function that merges new content blocks into existing messages during streaming.
- **Pending_State_Persistence**: The sessionStorage-based mechanism for persisting pending chat state across page reloads.
- **Tab_State**: The per-tab state isolation system comprising `useTabState`, `tabStateRef`, and `tabStatuses`.

## Requirements

### Requirement 1: Remove Debug Logging from Production Code

**User Story:** As a developer, I want production builds free of debug logging, so that runtime performance is not degraded and console output is clean.

#### Acceptance Criteria

1. THE Streaming_Lifecycle_Hook SHALL NOT contain unconditional `console.log` statements that execute on every SSE event.
2. WHEN a debug logging statement exists in the Streaming_Lifecycle_Hook, THE Streaming_Lifecycle_Hook SHALL gate the statement behind a compile-time or runtime debug flag (e.g., `import.meta.env.DEV`).
3. THE Chat_Page SHALL NOT contain a `setMessages` wrapper that captures `new Error().stack` on every call in production builds.
4. WHEN the debug flag is disabled, THE Streaming_Lifecycle_Hook SHALL skip all debug log formatting and string interpolation to avoid unnecessary object allocation.

### Requirement 2: Optimize Message Update Duplicate Detection

**User Story:** As a developer, I want message deduplication to be efficient, so that streaming updates with many content blocks do not cause UI jank.

#### Acceptance Criteria

1. THE Message_Updater SHALL detect duplicate content blocks using a Set-based lookup on block identifiers instead of O(n×m) nested iteration.
2. WHEN deduplicating `tool_use` blocks, THE Message_Updater SHALL use the block `id` field as the Set key.
3. WHEN deduplicating `tool_result` blocks, THE Message_Updater SHALL use the `toolUseId` field as the Set key.
4. WHEN deduplicating `text` blocks, THE Message_Updater SHALL use the `text` content as the Set key.
5. FOR ALL valid message arrays, the optimized Message_Updater SHALL produce identical output to the original implementation (behavioral equivalence).

### Requirement 3: Add Schema Versioning to Session Persistence

**User Story:** As a developer, I want persisted session state to include a version field, so that schema changes do not silently corrupt restored state.

#### Acceptance Criteria

1. THE Pending_State_Persistence SHALL include a `version` field in the `PersistedPendingState` interface.
2. WHEN persisting state to sessionStorage, THE Pending_State_Persistence SHALL write the current schema version number alongside the payload.
3. WHEN restoring state from sessionStorage, THE Pending_State_Persistence SHALL compare the stored version against the current version.
4. IF the stored version does not match the current version, THEN THE Pending_State_Persistence SHALL discard the stale entry and return null.
5. FOR ALL valid `PersistedPendingState` objects, persisting then restoring SHALL produce an equivalent object when the schema version matches (round-trip property).

### Requirement 4: Harden Stale Entry Cleanup Error Detection

**User Story:** As a developer, I want 404 detection in stale entry cleanup to be reliable, so that unrelated errors are not misidentified as missing sessions.

#### Acceptance Criteria

1. THE Streaming_Lifecycle_Hook SHALL detect 404 responses using structured error properties (e.g., a `status` field or error code) instead of substring matching on `err.message`.
2. IF the error object contains a numeric `status` property, THEN THE Streaming_Lifecycle_Hook SHALL compare it against 404.
3. IF the error object does not contain a `status` property, THEN THE Streaming_Lifecycle_Hook SHALL treat the entry as indeterminate and skip cleanup for that entry.

### Requirement 5: Decompose Streaming Lifecycle Hook Interface

**User Story:** As a developer, I want the streaming lifecycle hook to expose a focused interface, so that consuming components only depend on the state they need and the hook is easier to maintain.

#### Acceptance Criteria

1. THE Streaming_Lifecycle_Hook SHALL expose its return value as logically grouped sub-interfaces rather than a flat object with 30+ fields.
2. THE Streaming_Lifecycle_Hook SHALL group tab-related state and operations (tab state map, active tab ref, save/restore/init/cleanup, tab statuses, updateTabStatus) into a dedicated sub-interface or sub-hook.
3. THE Streaming_Lifecycle_Hook SHALL group streaming control operations (abort ref, stream generation ref, increment, stream handler factories) into a dedicated sub-interface or sub-hook.
4. THE Streaming_Lifecycle_Hook SHALL group message state (messages, setMessages, pending question, session ID) into a dedicated sub-interface or sub-hook.
5. WHEN a consuming component destructures the hook return, THE consuming component SHALL be able to import only the sub-interface it needs.

### Requirement 6: Remove Redundant State Updates in Chat Page

**User Story:** As a developer, I want state updates to be minimal and intentional, so that unnecessary re-renders are avoided.

#### Acceptance Criteria

1. WHEN `handleNewChat` is invoked, THE Chat_Page SHALL call `setMessages` exactly once to set the initial empty state.
2. THE Chat_Page SHALL NOT call `setMessages` with a value that is immediately overwritten by a subsequent `setMessages` call within the same synchronous handler.

### Requirement 7: Memoize Event Handlers in Chat Page

**User Story:** As a developer, I want expensive event handlers to be memoized, so that child components do not re-render unnecessarily.

#### Acceptance Criteria

1. THE Chat_Page SHALL wrap `handleSendMessage` in `useCallback` with correct dependencies.
2. THE Chat_Page SHALL wrap `handlePluginCommand` in `useCallback` with correct dependencies.
3. WHEN the dependencies of a memoized handler have not changed, THE Chat_Page SHALL return the same function reference across renders.

### Requirement 8: Memoize Timeline Merge Computation

**User Story:** As a developer, I want the timeline merge of messages and snapshots to be computed efficiently, so that it does not re-execute on every render.

#### Acceptance Criteria

1. THE Chat_Page SHALL compute the merged timeline (messages + snapshots) using `useMemo` instead of an inline IIFE in JSX.
2. THE Chat_Page SHALL declare the correct dependency array for the memoized timeline so it recomputes only when messages or snapshots change.

### Requirement 9: Fix Missing Dependencies in useCallback

**User Story:** As a developer, I want hook dependency arrays to be correct, so that stale closures do not cause subtle bugs.

#### Acceptance Criteria

1. THE Chat_Page SHALL declare all referenced variables in the dependency array of `loadSessionMessages`'s `useCallback`.
2. THE Chat_Page SHALL NOT use an empty dependency array for `useCallback` when the callback body references outer-scope variables that can change.

### Requirement 10: Reduce Overlapping useEffect Dependencies

**User Story:** As a developer, I want useEffect hooks to have non-overlapping responsibilities, so that mount-time race conditions are eliminated.

#### Acceptance Criteria

1. THE Chat_Page SHALL NOT have multiple `useEffect` hooks that watch the same dependency and mutate the same state.
2. WHEN two effects need to respond to the same dependency change, THE Chat_Page SHALL combine them into a single effect or use a ref-based coordination pattern to prevent races.

### Requirement 11: Remove Dead Code from TSCC Panel

**User Story:** As a developer, I want the TSCC panel free of dead code, so that the component is easier to understand and maintain.

#### Acceptance Criteria

1. THE TSCC_Panel SHALL NOT contain the `showResumed` state variable or its associated `useEffect` in `ExpandedView` if neither is connected to any user-visible behavior.
2. WHEN dead code is removed, THE TSCC_Panel SHALL preserve all existing functional behavior.

### Requirement 12: Fix Stale Default State in TSCC Panel

**User Story:** As a developer, I want the default TSCC state to reflect the current time, so that freshness calculations are accurate.

#### Acceptance Criteria

1. THE TSCC_Panel SHALL NOT create `DEFAULT_TSCC_STATE` with `new Date()` at module load time.
2. WHEN a default TSCC state is needed, THE TSCC_Panel SHALL generate the `lastUpdatedAt` timestamp at the time of use (e.g., via a factory function or inline computation).

### Requirement 13: Differentiate Pin Icon Visual State

**User Story:** As a user, I want the pin icon to look different when pinned versus unpinned, so that I can tell the current state at a glance.

#### Acceptance Criteria

1. WHEN the TSCC panel is pinned, THE TSCC_Panel SHALL render the pin icon with a visually distinct style (e.g., filled icon variant, rotation, or contrasting color).
2. WHEN the TSCC panel is unpinned, THE TSCC_Panel SHALL render the pin icon in its default muted style.
3. THE TSCC_Panel SHALL maintain the existing `aria-pressed` attribute on the pin button to preserve accessibility.

### Requirement 14: Pause Polling When Off-Screen

**User Story:** As a developer, I want the context preview panel to stop polling when not visible, so that unnecessary network requests and CPU usage are avoided.

#### Acceptance Criteria

1. WHILE the Context_Preview_Panel is not visible in the viewport (e.g., tab is backgrounded or panel is scrolled out of view), THE Context_Preview_Panel SHALL pause its polling interval.
2. WHEN the Context_Preview_Panel becomes visible again, THE Context_Preview_Panel SHALL resume polling.
3. THE Context_Preview_Panel SHALL use the Page Visibility API or Intersection Observer API to detect visibility changes.

### Requirement 15: Debounce Rapid Expand/Collapse in Context Preview

**User Story:** As a developer, I want rapid expand/collapse toggling to be debounced, so that API call bursts are prevented.

#### Acceptance Criteria

1. WHEN a user rapidly toggles the Context_Preview_Panel expand/collapse state, THE Context_Preview_Panel SHALL debounce the resulting fetch calls with a minimum interval of 300ms.
2. THE Context_Preview_Panel SHALL NOT fire multiple concurrent fetch requests due to rapid toggling.

### Requirement 16: Decouple TSCC State Hook from Streaming Lifecycle Hook

**User Story:** As a developer, I want the TSCC state hook and streaming lifecycle hook to communicate through a well-defined interface, so that the circular reference pattern is eliminated.

#### Acceptance Criteria

1. THE TSCC_State_Hook SHALL NOT directly import from or hold a reference to the Streaming_Lifecycle_Hook.
2. THE Streaming_Lifecycle_Hook SHALL NOT directly import from or hold a reference to the TSCC_State_Hook.
3. WHEN the Streaming_Lifecycle_Hook needs to notify the TSCC_State_Hook of a telemetry event, THE Chat_Page SHALL mediate the communication through callback props or a shared event interface.
4. THE decoupled design SHALL preserve all existing TSCC telemetry update behavior.

### Requirement 17: Consolidate Tab State into a Single Source of Truth

**User Story:** As a developer, I want tab state managed in one place, so that triple-bookkeeping across `useTabState`, `tabStateRef`, and `tabStatuses` is eliminated.

#### Acceptance Criteria

1. THE Chat_Page SHALL maintain tab state in a single authoritative data structure rather than three separate stores.
2. WHEN tab state is updated, THE Chat_Page SHALL update the single source of truth and derive any needed views from it.
3. THE consolidated tab state SHALL support all existing tab operations: add, close, select, save, restore, init, cleanup, and status updates.
4. THE consolidated tab state SHALL preserve all existing per-tab isolation guarantees (messages, session ID, streaming state, pending question).
