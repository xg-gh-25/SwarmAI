---
inclusion: fileMatch
fileMatchPattern: ["desktop/src/pages/ChatPage.tsx", "desktop/src/hooks/useChatStreamingLifecycle.ts", "desktop/src/hooks/useUnifiedTabState.ts", "desktop/src/services/tabPersistence.ts", "desktop/src/services/chat.ts", "desktop/src/stores/MessageStore.ts"]
---

# Multi-Tab Chat Isolation Principles

## Architecture Overview

Each chat tab is a standalone session with its own end-to-end lifecycle. Several tabs
(RAM-adaptive limit, see `ResourceMonitor.compute_max_tabs()`) process different tasks in
parallel. Tabs MUST NOT interfere with each other at any layer.

### State Ownership Model

```
┌─────────────────────────────────────────────────────────────┐
│  MessageStore (messageStoreRegistry.get(tabId))             │
│  ════════════════════════════════════════════                │
│  SINGLE WRITER for a tab's messages. All message mutations  │
│  go through the store (insertOptimisticMessages, append,    │
│  updateLast, replace). tabState.messages is a fallback      │
│  cache only.                                                │
│                                                             │
│  tabMapRef (useRef<Map<string, UnifiedTab>>)                │
│  ════════════════════════════════════════════                │
│  AUTHORITATIVE for all other per-tab state:                 │
│  • sessionId, pendingQuestion, pendingPermissionRequestId   │
│  • isStreaming (readonly; driven by the per-tab streaming   │
│    state machine inside setIsStreaming), abortController,   │
│    streamGen, userStopped                                   │
│  • status (idle/streaming/permission_needed/error/etc.)     │
│                                                             │
│  React useState (messages, sessionId, isStreaming, etc.)    │
│  ════════════════════════════════════════════                │
│  DISPLAY MIRROR — reflects ONLY the active tab's state.     │
│  Updated on tab switch via restore + bumpStreamingDerivation│
│                                                             │
│  pendingStreamTabs (useState<Set<string>>)                  │
│  ════════════════════════════════════════════                │
│  PENDING TRACKER — narrow: covers only the gap before a tab │
│  is registered as streaming. Keyed by tabId. Once the tab   │
│  is registered, its own isStreaming flag is the only truth. │
└─────────────────────────────────────────────────────────────┘
```

## The 9 Isolation Principles

### Principle 1: Tab-Scoped State Mutations

Every state mutation MUST target a specific tabId. Never mutate global/shared state that affects all tabs.

- `setIsStreaming(value, tabId)` — always pass the originating tab's ID
- `updateTabStatus(tabId, status)` — always scope to the specific tab
- Stream handlers capture `capturedTabId` at creation time and use it for all mutations
- `pendingStreamTabs` is keyed by tabId, not a single boolean

**Anti-pattern**: `setIsStreaming(false)` without tabId — this defaults to `activeTabIdRef.current` which may have changed if the user switched tabs during streaming.

### Principle 2: Active Tab = Display Mirror Only

React `useState` values (`messages`, `sessionId`, `pendingQuestion`, `isStreaming`) are a display mirror of the active tab's state from `tabMapRef`. They exist solely for rendering.

- On tab switch: restore from `tabMapRef` → React state, call `bumpStreamingDerivation()`
- On stream event: write to `tabMapRef` always, write to React state only if `isActiveTab`
- Never read React state to make decisions about a specific tab — read from `tabMapRef`

**Anti-pattern**: Using `sessionIdRef.current` or `isStreamingRef.current` to make decisions — these reflect the active tab, not necessarily the originating tab.

### Principle 3: Stream Handler Closure Capture

Stream handlers (`createStreamHandler`, `createErrorHandler`, `createCompleteHandler`) capture their tab identity at creation time via `capturedTabId`. This closure is immutable for the lifetime of the SSE connection.

- `capturedTabId` determines which tab's state to update in `tabMapRef`
- `isActiveTab` is computed dynamically: `capturedTabId === activeTabIdRef.current`
- If `isActiveTab` is true → update both `tabMapRef` AND React state
- If `isActiveTab` is false → update only `tabMapRef` (background tab)
- If tab was closed (`tabMapRef.get(capturedTabId)` returns undefined) → no-op

**Anti-pattern**: Creating a stream handler without passing `tabId` — it defaults to `activeTabIdRef.current` at creation time, which is correct for user-initiated actions but wrong if called from a background context.

### Principle 4: Per-Tab Session Identity for Backend Calls

All backend API calls (stop, answer question, permission decision) MUST use the per-tab `sessionId` from `tabMapRef`, not the shared React `sessionId` state.

```typescript
// CORRECT:
const tabSessionId = tabMapRef.current.get(tabId)?.sessionId;
await chatService.stopSession(tabSessionId);

// WRONG:
await chatService.stopSession(sessionId); // shared React state — may be wrong tab
```

This ensures that stopping Tab A doesn't accidentally stop Tab B's backend session after a tab switch.

### Principle 5: Per-Tab Abort Controller Isolation

Each tab stores its own `abortController` in `tabMapRef`. When the user clicks "Stop":

1. Read the active tab's abort controller from `tabMapRef`
2. Call `.abort()` on that specific controller
3. Send `chatService.stopSession(tabSessionId)` to the backend
4. Clear only that tab's streaming state

**Anti-pattern**: Using a shared `abortRef` that gets overwritten by the last tab to start streaming — only the last tab's abort function would be reachable.

### Principle 6: Permission — Per-Session Approval, Per-Tab Request

**Backend approval is per session.** `PermissionManager.approve_command(session_id, …)`
stores a command hash in memory for that session only. An approval in Tab A does not
apply to Tab B and does not survive a restart. (`CmdPermissionManager` and
`~/.swarm-ai/cmd_permissions/` no longer exist.)

**Each tab owns its pending request.** On a `cmd_permission_request` SSE event, for the
originating tab whether active or not, the stream handler:

1. Appends an inline `cmd_permission_request` block to that tab's MessageStore
   (rendered by `InlinePermissionRequest`, not a modal).
2. Sets `tabState.pendingPermissionRequestId` (and `sessionId`); mirrors it to React state
   only if `isActiveTab`.
3. Ends streaming for that tab (`setIsStreaming(false, capturedTabId)`, `incrementStreamGen()`,
   turn-end reconcile) and sets status `permission_needed`.

`pendingPermissionRequestId` is saved and restored on tab switch, so every tab keeps its
own prompt answerable. `handlePermissionDecision()` uses the per-tab `sessionId`, guards
double clicks with `permissionLoadingTabs`, and streams both approve and deny through
`/cmd-permission-continue`.

### Principle 7: Tab Switch = Save + Restore + Re-derive

Tab switching follows a strict 3-step protocol:

1. **Save**: Write current React state (`pendingQuestion`, `pendingPermissionRequestId`, scroll; plus `messages`/`sessionId` only for an IDLE tab — a streaming tab's `tabMapRef` is already newer) into the source tab's `tabMapRef` entry
2. **Restore**: Read target tab's state from `tabMapRef` and set React state
3. **Re-derive**: Call `bumpStreamingDerivation()` so `isStreaming` re-derives from the new active tab's `tabMapRef` entry

**Critical**: Do NOT call `setIsStreaming()` during tab switch — it modifies `pendingStreamTabs` which could corrupt the source tab's pending state. The derivation handles it automatically.

## Customer Scenario Matrix

| Scenario | Expected Behavior | Isolation Mechanism |
|----------|-------------------|---------------------|
| 3 tabs streaming in parallel | Each tab shows its own spinner, messages, and activity label | `capturedTabId` in stream handlers, per-tab `isStreaming` in `tabMapRef` |
| Stop Tab A while Tab B and C stream | Only Tab A stops; B and C continue unaffected | Per-tab `abortController` in `tabMapRef`, per-tab `sessionId` for backend stop call |
| Tab B completes while A and C stream | Tab B shows "complete_unread" indicator; A and C spinners continue | `updateTabStatus(capturedTabId, isActiveTab ? 'idle' : 'complete_unread')` |
| Tab A needs permission while B streams | Tab A gets an inline approval block and `permission_needed` status; Tab B unaffected | Per-tab `pendingPermissionRequestId` in `tabMapRef`; React mirror set only if `isActiveTab` |
| Switch from streaming Tab A to idle Tab B | Tab B shows idle state, input enabled; Tab A continues streaming in background | `bumpStreamingDerivation()` re-derives `isStreaming` from Tab B's state; Tab A's `tabMapRef` entry unchanged |
| Switch back to Tab A (still streaming) | Tab A's messages and spinner restored correctly | `handleTabSelect` restores from `tabMapRef`; `bumpStreamingDerivation()` re-derives `isStreaming` |
| Tab A errors while viewing Tab B | Tab A gets `status: 'error'` indicator; Tab B unaffected | `setIsStreaming(false, capturedTabId)` only clears Tab A; error content written to Tab A's `tabMapRef` only |
| Send message on idle Tab B while Tab A streams | Message sends successfully on Tab B | Per-tab guard: `tabMapRef.get(activeTabId)?.isStreaming` instead of global `isStreamingRef` |
| Close streaming Tab A | Tab A's abort controller fires, state cleaned up; adjacent tab selected | `cleanupTabState(tabId)` aborts + removes from map; `closeTab` selects adjacent |
| Ask-user-question on Tab C while A and B stream | Tab C shows question form; A and B continue | `setIsStreaming(false, capturedTabId)` only clears Tab C; `pendingQuestion` set only if `isActiveTab` |

### Principle 8: Session ID Stability Across Restarts

A tab's session ID is the conversation's identity and MUST NEVER be replaced. On the
frontend: never overwrite a tab's `sessionId` (in `tabMapRef` or `open_tabs.json`) with a
different value after `session_start`. The backend side of this contract (app ID vs SDK
ID, resume, identity persistence) is in `session-identity-and-backend-isolation.md`.

### Principle 9: One Writer per Message — the Newest Stream Only

A reconnect or resend opens a SECOND stream handler for the same reply, with the SAME
generation, so the generation guard cannot tell them apart; if the old one still
delivers, every delta lands twice (the doubled thought/paragraph, `1e570b29`).

1. **Only the tab's newest handler writes content.** Each handler gets a monotonic token
   (`streamHandlerSeq`); the tab records it as `latestStreamHandler`. A handler whose
   token is not the tab's latest drops content events (`text_*`, `thinking_*`,
   `assistant`, `tool_result`); control events still pass.
2. **A duplicate never wins the reconcile.** The DB reconcile keeps the LONGER side, and a
   doubled local copy is always longer. Before comparing lengths (and when streamed
   content is confirmed), `collapseAdjacentDuplicateBlocks` merges ADJACENT, IDENTICAL
   text / thinking blocks of at least 20 chars into one. Short repeats ("Done.") and
   non-adjacent repeats are left alone: they can be real.
3. Every collapse logs `[stream-dup]`. Seeing it after a fix means another writer
   still doubles content — find that writer; never widen the collapse to hide it.

## Regression Prevention Checklist

When modifying chat tab code, verify:

- [ ] Every `setIsStreaming()` call passes an explicit `tabId` parameter
- [ ] Every backend API call uses `tabMapRef.current.get(tabId)?.sessionId`, not shared `sessionId`
- [ ] Stream handler factories receive `tabId` at creation time
- [ ] Tab switch does NOT call `setIsStreaming()` — uses `bumpStreamingDerivation()` instead
- [ ] New React state added to the streaming lifecycle is either per-tab (in `UnifiedTab`) or display-only (mirrors active tab)
- [ ] No new shared `useRef` or `useState` that could leak between tabs
- [ ] `isActiveTab` guard is used before writing to React state in stream handlers
- [ ] Error/complete handlers use `capturedTabId`, not `activeTabIdRef.current`
- [ ] Permission state lives in `tabState.pendingPermissionRequestId`; the React mirror is set only if `isActiveTab`
- [ ] Message writes go through the tab's MessageStore, never a raw `setMessages`-only write
- [ ] A new stream handler (reconnect/resend/retry) stamps `latestStreamHandler`; content from a superseded handler is dropped
- [ ] Any "longer side wins" merge collapses adjacent identical blocks (>= 20 chars) first
- [ ] Tests cover multi-tab scenarios (at least 2 tabs with concurrent streaming)

## Tab Persistence Safety

Tab state is persisted to `~/.swarm-ai/open_tabs.json` via the backend settings API (replaces unreliable localStorage on macOS Tauri WebKit).

Rules:
- Persistence is debounced (500ms) to avoid excessive writes during streaming
- Save effect is gated by `fileRestoreDone` — prevents overwriting persisted state with the temporary default tab before real tabs are restored
- Race condition guard: if user already started a conversation before file restore completes, skip restore (don't clobber active state)
- On app restart: `restoreFromFile()` hydrates tabs from the file, then messages are loaded from the DB via `getSessionMessages()`

Anti-pattern: Saving tab state before `fileRestoreDone.current` is true — this overwrites the real persisted tabs with a single default tab.

## Cross-Reference: Backend Isolation

Frontend tab isolation depends on backend session isolation. See `session-identity-and-backend-isolation.md` for:
- Send admission / SESSION_BUSY → pending-message conversion (prevents double-send and lost messages)
- Per-session permission queues and approvals
- Session ID stability across backend restarts and subprocess recycles
