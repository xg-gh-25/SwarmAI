# Requirements Document

## Introduction

SwarmAI is a Tauri + React 19 desktop chat application that supports 1–4 concurrent
chat tabs (RAM-adaptive). Switching between chat tabs is currently janky and laggy,
and the freeze grows with the destination tab's message history length.

The root cause is a dual representation of message data. Each tab already has an
authoritative per-tab `MessageStore` (in `messageStoreRegistry`, keyed by tabId)
plus per-tab state in `tabMapRef`. However, `ChatPage` ALSO keeps a single shared
React `messages` useState that acts as a DISPLAY MIRROR of whichever tab is active.
On every tab switch, an effect swaps the whole array
(`setMessages([...activeTabState.messages])`). React then tears down ALL existing
message bubbles and mounts the destination tab's bubbles FRESH. Every
`MarkdownRenderer` (react-markdown + remarkGfm + remarkBreaks) mounts cold and
synchronously re-parses its markdown, so freeze time scales with history length.
`React.memo` cannot help a fresh mount because there is no prior render to compare.

This feature eliminates the shared swapped mirror as the authoritative display path
and replaces it with keep-mounted per-tab views: each open tab renders its own
message-list view that subscribes directly to its OWN `MessageStore` via
`useMessageStore(tabId)`. Only the active tab's view is visible; inactive tabs are
hidden (e.g. `display:none`) but remain mounted. Switching tabs becomes a
visibility toggle with zero remount and zero markdown re-parse.

This is the most regression-prone area of the codebase (project memory documents
repeated cross-tab message and streaming leak bugs). The project design invariant is
"make the wrong thing impossible" rather than stacking guards, so the goal is
structural. All existing multi-tab isolation guarantees MUST be preserved.

### Scope

**In scope:** the frontend message-list rendering layer — keep-mounted per-tab
views, per-tab `MessageStore` subscription, removal of the shared swapped `messages`
mirror as the authoritative display source, and preservation of per-tab UI state
across switches.

**Out of scope (non-goals):**

- Changing backend session routing or durability (owned by the
  `session-state-source-of-truth` spec).
- Virtualization or windowing of the message list. Considered and rejected in favor
  of keep-mounted views, given the 1–4 tab cap. May be revisited as a future
  alternative.
- Re-fixing the streaming "block append" / chunked-render issue. Already fixed in a
  prior change (memoizing `MessageBubble` + stabilizing handlers). This spec MUST
  NOT regress that fix.

## Glossary

- **Chat_View_Layer**: The frontend rendering subsystem responsible for displaying
  chat message lists across tabs. The system under specification.
- **Tab_View**: A per-tab message-list view instance. One Tab_View is rendered per
  open tab. Only the active tab's Tab_View is visible; others remain mounted but
  hidden.
- **MessageStore**: The per-tab, module-level single source of truth for a tab's
  message data (`desktop/src/stores/MessageStore.ts`). Exposes phase-gated
  operations (`append`, `appendMany`, `updateLast`, `replace`, `reconcile`,
  `startStreaming`, `endStreaming`) and a subscription API.
- **messageStoreRegistry**: The module-level registry mapping tabId to MessageStore.
  Provides `getOrCreate`, `get`, and `destroy`.
- **useMessageStore**: The React hook that subscribes a component to a tab's
  MessageStore and returns reactive `messages` plus a stable `store` reference.
- **Shared_Messages_Mirror**: The single shared React `messages` useState in
  `ChatPage` that today mirrors the active tab and is swapped on every tab switch.
  The structure this feature eliminates as the authoritative display source.
- **tabMapRef**: The authoritative `useRef<Map<string, UnifiedTab>>` holding per-tab
  state (messages, sessionId, pendingQuestion, isStreaming, scrollPosition, etc.).
- **useUnifiedTabState**: The hook that owns tab CRUD, `tabMapRef`, `activeTabId`,
  and persistence (`restoreFromFile`, `initTabState`, `restoreTab`).
- **Active_Tab**: The single tab currently selected and visible to the user.
- **Background_Tab**: Any open tab that is not the Active_Tab.
- **ResourceMonitor**: The backend module whose `compute_max_tabs()` returns a
  RAM-adaptive maximum tab count in the range [1, 4].
- **Optimistic_Echo**: A locally-inserted user message (id prefix `local-`) shown
  immediately on send, later reconciled against its persisted counterpart via
  `client_id` correlation on the `result` event (contract owned by the
  `session-state-source-of-truth` spec).
- **Read_Model**: The camelCase frontend shape of backend session state, including
  fields such as `pendingQuestion` and `waitingInput`.
- **Markdown_Re_Parse**: The synchronous parse performed by a MarkdownRenderer when
  it mounts cold, proportional to the markdown content size.

## Requirements

### Requirement 1: Keep-Mounted Per-Tab Views

**User Story:** As a user with multiple open chat tabs, I want each tab's message
list to stay mounted when I switch away, so that returning to a tab is instant and
does not re-render its history from scratch.

#### Acceptance Criteria

1. THE Chat_View_Layer SHALL render one Tab_View per open tab.
2. WHILE a tab is the Active_Tab, THE Chat_View_Layer SHALL render that tab's
   Tab_View as visible.
3. WHILE a tab is a Background_Tab, THE Chat_View_Layer SHALL keep that tab's
   Tab_View mounted and hidden from view.
4. WHEN the user switches from one tab to another, THE Chat_View_Layer SHALL change
   tab visibility without unmounting either tab's Tab_View.
5. WHEN the user switches to a tab whose Tab_View is already mounted, THE
   Chat_View_Layer SHALL NOT trigger a Markdown_Re_Parse of that tab's existing
   message bubbles.

### Requirement 2: Per-Tab MessageStore Subscription (Eliminate Shared Mirror)

**User Story:** As a developer maintaining the chat code, I want each Tab_View to
read its messages directly from its own MessageStore, so that there is no shared
swapped array that must be rebuilt on every switch.

#### Acceptance Criteria

1. THE Tab_View SHALL obtain its messages by subscribing to its own tab's
   MessageStore via useMessageStore using that tab's tabId.
2. WHEN a tab switch occurs, THE Chat_View_Layer SHALL NOT swap a Shared_Messages_Mirror
   array in as the authoritative display source of the destination tab's message list.
3. WHERE a Shared_Messages_Mirror value is used for non-authoritative concerns, THE
   Chat_View_Layer SHALL permit reads and writes of that value, provided they do not
   become the authoritative display source of any Tab_View's message list.
4. WHEN a MessageStore for a given tabId emits a change notification, THE
   Chat_View_Layer SHALL re-render only that tab's Tab_View.
5. WHERE a Shared_Messages_Mirror value is retained for non-authoritative concerns,
   THE Chat_View_Layer SHALL NOT use it to determine which messages a Tab_View
   displays.

### Requirement 3: Instant Tab Switch Performance

**User Story:** As a user switching between two tabs that both have long histories,
I want the switch to feel instant, so that the app does not freeze proportionally to
history length.

#### Acceptance Criteria

1. WHEN the user switches to a Background_Tab whose history was previously rendered,
   THE Chat_View_Layer SHALL display that tab without performing a synchronous
   Markdown_Re_Parse of the destination tab's message bubbles.
2. WHEN the user switches between two tabs, THE Chat_View_Layer SHALL complete the
   visible switch within an upper bound of 100 milliseconds, independent of the
   destination tab's message count.
3. WHEN the user switches between two tabs, THE Chat_View_Layer SHALL impose no
   minimum switch duration, and SHALL permit an instant (including effectively
   zero-millisecond) switch provided no full remount of the destination tab's
   message bubbles occurs.
4. WHEN the user switches tabs, THE Chat_View_Layer SHALL NOT perform a full remount
   of the destination tab's message bubbles.

### Requirement 4: Cross-Tab Content Isolation

**User Story:** As a user running several conversations in parallel, I want each
tab to show only its own messages, so that one tab's content never appears in
another.

#### Acceptance Criteria

1. THE Tab_View SHALL display only the messages held by its own tab's MessageStore.
2. IF a message is appended to one tab's MessageStore, THEN THE Chat_View_Layer
   SHALL NOT display that message in any other tab's Tab_View.
3. WHILE multiple tabs hold distinct message histories, THE Chat_View_Layer SHALL
   render each Tab_View from its own tabId-keyed MessageStore with no shared message
   array between Tab_Views.
4. WHEN the user switches tabs during active streaming in a Background_Tab, THE
   Chat_View_Layer SHALL NOT render the Background_Tab's streaming content in the
   Active_Tab's Tab_View.

### Requirement 5: Background Streaming Continuity

**User Story:** As a user who switches to another tab while a response is streaming,
I want the original tab to keep streaming in the background, so that switching tabs
does not interrupt or abort in-flight work.

#### Acceptance Criteria

1. WHEN the user switches away from a tab that is streaming, THE Chat_View_Layer
   SHALL keep that tab's SSE stream active without aborting it.
2. WHILE a Background_Tab is streaming, THE Chat_View_Layer SHALL apply that tab's
   streaming updates to that tab's MessageStore only.
3. WHEN the user switches back to a tab that streamed while in the background, THE
   Chat_View_Layer SHALL display the content accumulated during the background
   period.
4. WHILE a Background_Tab is streaming, THE Chat_View_Layer SHALL NOT apply that
   tab's streaming updates to the Active_Tab's Tab_View.

### Requirement 6: MessageStore as Single Source of Truth for Inserts

**User Story:** As a developer, I want every message insert to go through the per-tab
MessageStore, so that the displayed list and the authoritative data never diverge.

#### Acceptance Criteria

1. WHEN an optimistic user message is inserted, THE Chat_View_Layer SHALL insert it
   through the target tab's MessageStore using store append or appendMany.
2. WHEN an assistant placeholder is inserted for a streaming response, THE
   Chat_View_Layer SHALL insert it through the target tab's MessageStore.
3. THE Chat_View_Layer SHALL NOT perform a Shared_Messages_Mirror-only write that
   bypasses the target tab's MessageStore for message data that must be displayed.
4. WHEN an update to a tab's MessageStore has settled, THE Tab_View SHALL reflect
   the contents of that tab's MessageStore as the authoritative message data.
5. WHILE an update to a tab's MessageStore is in progress, THE Chat_View_Layer SHALL
   permit that tab's Tab_View to temporarily diverge from the MessageStore contents
   until the update settles, rather than requiring the Tab_View to match the
   MessageStore on every intermediate microtask.

### Requirement 7: Single Source of Truth for Streaming Status

**User Story:** As a developer, I want one authoritative `isStreaming` value per tab,
so that the view layer never reintroduces a competing streaming-state truth source.

#### Acceptance Criteria

1. THE Chat_View_Layer SHALL derive a tab's streaming status from that tab's
   registered `isStreaming` value in tabMapRef.
2. THE Chat_View_Layer SHALL NOT introduce a second authoritative source of
   streaming status for a tab.
3. WHEN a tab's `isStreaming` value in tabMapRef changes, THE Chat_View_Layer SHALL
   reflect the updated streaming status for that tab's Tab_View.

### Requirement 8: Streaming Hot Path Remains Scoped

**User Story:** As a user watching a response stream, I want smooth per-token
rendering, so that introducing per-tab views does not bring back chunked or
full-list re-rendering.

#### Acceptance Criteria

1. WHILE a tab is streaming, THE Chat_View_Layer SHALL scope per-token re-rendering
   to the streaming message bubble within that tab's Tab_View.
2. WHILE a tab is streaming, THE Chat_View_Layer SHALL NOT re-render the
   non-streaming historical bubbles of that tab's Tab_View on each token.
3. WHILE a tab is streaming, THE Chat_View_Layer SHALL NOT re-render any other tab's
   Tab_View in response to that tab's tokens.

### Requirement 9: Per-Tab UI State Preservation Across Switches

**User Story:** As a user switching between tabs, I want my scroll position, draft
input, pending question, and pending permission preserved per tab, so that nothing
is lost when I switch away and back.

#### Acceptance Criteria

1. WHEN the user switches away from a tab and later returns, THE Chat_View_Layer
   SHALL restore that tab's scroll position.
2. WHEN the user switches away from a tab and later returns, THE Chat_View_Layer
   SHALL restore that tab's draft input text.
3. WHEN the user switches away from a tab that has a pending question and later
   returns, THE Chat_View_Layer SHALL display that tab's pending question.
4. WHEN the user switches away from a tab that has a pending permission request and
   later returns, THE Chat_View_Layer SHALL display that tab's pending permission
   request.
5. WHEN the user switches tabs, THE Chat_View_Layer SHALL NOT apply one tab's scroll
   position, draft input, pending question, or pending permission to another tab.

### Requirement 10: Initial Restore and Lazy Message Loading

**User Story:** As a user reopening the app, I want my previously open tabs restored
and each tab's history loaded when I first view it, so that startup and first-view
behavior still work with per-tab views.

#### Acceptance Criteria

1. WHEN the app restores tabs from open_tabs.json via restoreFromFile, THE
   Chat_View_Layer SHALL render a Tab_View for each restored tab.
2. WHEN a restored tab has a sessionId but no loaded messages and becomes active for
   the first time, THE Chat_View_Layer SHALL load that tab's messages from the
   backend into that tab's MessageStore.
3. WHILE a tab is streaming, THE Chat_View_Layer SHALL NOT overwrite that tab's
   in-flight MessageStore content with a lazy backend message load.
4. WHEN a tab's messages are loaded from the backend, THE Tab_View SHALL display the
   loaded messages for that tab.

### Requirement 11: Tab Close Destroys View and Store

**User Story:** As a user closing a tab, I want its view and its message data fully
released, so that closed tabs do not leak memory or linger.

#### Acceptance Criteria

1. WHEN a tab is closed, THE Chat_View_Layer SHALL unmount that tab's Tab_View.
2. WHEN a tab is closed, THE Chat_View_Layer SHALL destroy that tab's MessageStore
   via messageStoreRegistry.destroy.
3. WHEN a tab is closed, THE Chat_View_Layer SHALL NOT retain that tab's Tab_View or
   MessageStore after the close completes.

### Requirement 12: Bounded Memory Within RAM-Adaptive Tab Budget

**User Story:** As a user on a memory-constrained machine, I want the keep-mounted
views to stay within the existing tab budget, so that keeping a few tabs mounted
does not exhaust memory.

#### Acceptance Criteria

1. THE Chat_View_Layer SHALL keep mounted only the Tab_Views for tabs that are open
   within the maximum tab count returned by ResourceMonitor.compute_max_tabs().
2. WHERE ResourceMonitor.compute_max_tabs() reports a lower maximum on a
   memory-constrained machine, THE Chat_View_Layer SHALL honor that maximum as the
   upper bound on concurrently mounted Tab_Views.
3. WHEN a tab is closed and its Tab_View is destroyed, THE Chat_View_Layer SHALL
   release the memory associated with that Tab_View and its MessageStore.

### Requirement 13: Compatibility With session-state-source-of-truth Spec

**User Story:** As a developer integrating both specs, I want the per-tab view layer
to honor the optimistic-echo and reconcile contracts of the
`session-state-source-of-truth` spec, so that the structural change does not break
session-state durability or reconciliation.

#### Acceptance Criteria

1. THE Chat_View_Layer SHALL preserve the Optimistic_Echo contract by inserting the
   optimistic user message through the originating tab's MessageStore and allowing
   it to be reconciled via client_id correlation on the result event.
2. WHEN a store-driven reconcile or drain updates a tab's MessageStore, THE Tab_View
   SHALL reflect the reconciled message set for that tab.
3. THE Chat_View_Layer SHALL consume the camelCase Read_Model fields, including
   pendingQuestion and waitingInput, without altering their shape.
4. THE Chat_View_Layer SHALL NOT take ownership of backend session-state durability,
   coalesce-drain, or client_id reconciliation, which remain owned by the
   `session-state-source-of-truth` spec.

### Requirement 14: No Regression of Multi-Tab Isolation Invariants

**User Story:** As a maintainer of this regression-prone area, I want the existing
multi-tab isolation invariants to hold after the change, so that previously fixed
cross-tab leak bugs do not return.

#### Acceptance Criteria

1. THE Chat_View_Layer SHALL keep tabMapRef as the authoritative per-tab state
   source and treat React display values as a mirror of the Active_Tab only.
2. WHEN a stream handler writes for a Background_Tab, THE Chat_View_Layer SHALL apply
   the write to that tab's MessageStore and tabMapRef entry, not to the Active_Tab's
   Tab_View.
3. WHEN a backend call is made for a specific tab, THE Chat_View_Layer SHALL use that
   tab's per-tab sessionId from tabMapRef rather than a shared session value.
4. WHILE multiple tabs stream concurrently, THE Chat_View_Layer SHALL render each
   tab's spinner, messages, and activity within that tab's own Tab_View.
