---
inclusion: fileMatch
fileMatchPattern: "desktop/src/pages/ChatPage.tsx,desktop/src/hooks/useChatStreamingLifecycle.ts,desktop/src/hooks/useUnifiedTabState.ts,desktop/src/services/tabPersistence.ts,desktop/src/services/chat.ts"
---

# Multi-Tab Chat Isolation Principles

## Architecture Overview

Each chat tab is a standalone session with its own end-to-end lifecycle. The system supports 1-4 concurrent tabs (RAM-adaptive) processing different tasks in parallel. Tabs MUST NOT interfere with each other at any layer.

### State Ownership Model

```
┌─────────────────────────────────────────────────────────────┐
│  tabMapRef (useRef<Map<string, UnifiedTab>>)                │
│  ════════════════════════════════════════════                │
│  AUTHORITATIVE source of truth for ALL per-tab state:       │
│  • messages, sessionId, pendingQuestion                     │
│  • isStreaming, abortController, streamGen                  │
│  • status (idle/streaming/waiting_input/error/etc.)         │
│                                                             │
│  React useState (messages, sessionId, isStreaming, etc.)    │
│  ════════════════════════════════════════════                │
│  DISPLAY MIRROR — reflects ONLY the active tab's state.     │
│  Updated on tab switch via restore + bumpStreamingDerivation│
│                                                             │
│  pendingStreamTabs (useState<Set<string>>)                  │
│  ════════════════════════════════════════════                │
│  PENDING TRACKER — covers the gap between handleSendMessage │
│  and session_start SSE event. Keyed by tabId.               │
└─────────────────────────────────────────────────────────────┘
```

## The 7 Isolation Principles

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

### Principle 6: Permission — Shared Approval, Per-Tab Request

Command permissions have two distinct layers:

**User-scoped approval (SHARED)**: Once a user approves a command pattern (e.g., `npm test`), that approval is stored in `CmdPermissionManager` (filesystem-backed) and applies across ALL sessions and tabs. This is correct — the user's trust decision is global.

**Per-tab permission request (ISOLATED)**: Each tab/session independently raises its own `cmd_permission_request` to the user. If Tab A asks to run `rm -rf build/` and Tab B asks to run `docker build`, these are separate prompts that must not be mixed. The permission modal shows only when the requesting tab is active.

```
Backend (shared):
  CmdPermissionManager.approve(command)  → persisted to ~/.swarm-ai/cmd_permissions/
  is_command_approved(command)           → checked by ALL sessions before executing

Frontend (per-tab):
  cmd_permission_request SSE event       → sets pendingPermission only if isActiveTab
  tabState.status = 'permission_needed'  → indicator for background tabs
  handlePermissionDecision()             → uses per-tab sessionId from tabMapRef
```

**Current behavior**: `pendingPermission` is a single `useState` in ChatPage. The `isActiveTab` guard prevents showing the wrong tab's permission request. Background tabs get a `permission_needed` status indicator in the tab bar.

**Known limitation**: If two background tabs both need permission simultaneously, only the last one to become active will show its modal. The other tab's request may have timed out on the backend. This is acceptable for now — concurrent permission requests across tabs are rare.

### Principle 7: Tab Switch = Save + Restore + Re-derive

Tab switching follows a strict 3-step protocol:

1. **Save**: Write current React state (`messages`, `sessionId`, `pendingQuestion`) into the source tab's `tabMapRef` entry
2. **Restore**: Read target tab's state from `tabMapRef` and set React state
3. **Re-derive**: Call `bumpStreamingDerivation()` so `isStreaming` re-derives from the new active tab's `tabMapRef` entry

**Critical**: Do NOT call `setIsStreaming()` during tab switch — it modifies `pendingStreamTabs` which could corrupt the source tab's pending state. The derivation handles it automatically.

## Customer Scenario Matrix

| Scenario | Expected Behavior | Isolation Mechanism |
|----------|-------------------|---------------------|
| 3 tabs streaming in parallel | Each tab shows its own spinner, messages, and activity label | `capturedTabId` in stream handlers, per-tab `isStreaming` in `tabMapRef` |
| Stop Tab A while Tab B and C stream | Only Tab A stops; B and C continue unaffected | Per-tab `abortController` in `tabMapRef`, per-tab `sessionId` for backend stop call |
| Tab B completes while A and C stream | Tab B shows "complete_unread" indicator; A and C spinners continue | `updateTabStatus(capturedTabId, isActiveTab ? 'idle' : 'complete_unread')` |
| Tab A needs permission while B streams | Permission modal shows only when Tab A is active; Tab B unaffected | `isActiveTab` guard on `setPendingPermission`, `tabState.status = 'permission_needed'` for background |
| Switch from streaming Tab A to idle Tab B | Tab B shows idle state, input enabled; Tab A continues streaming in background | `bumpStreamingDerivation()` re-derives `isStreaming` from Tab B's state; Tab A's `tabMapRef` entry unchanged |
| Switch back to Tab A (still streaming) | Tab A's messages and spinner restored correctly | `handleTabSelect` restores from `tabMapRef`; `bumpStreamingDerivation()` re-derives `isStreaming` |
| Tab A errors while viewing Tab B | Tab A gets `status: 'error'` indicator; Tab B unaffected | `setIsStreaming(false, capturedTabId)` only clears Tab A; error content written to Tab A's `tabMapRef` only |
| Send message on idle Tab B while Tab A streams | Message sends successfully on Tab B | Per-tab guard: `tabMapRef.get(activeTabId)?.isStreaming` instead of global `isStreamingRef` |
| Close streaming Tab A | Tab A's abort controller fires, state cleaned up; adjacent tab selected | `cleanupTabState(tabId)` aborts + removes from map; `closeTab` selects adjacent |
| Ask-user-question on Tab C while A and B stream | Tab C shows question form; A and B continue | `setIsStreaming(false, capturedTabId)` only clears Tab C; `pendingQuestion` set only if `isActiveTab` |

### Principle 8: Session ID Stability Across Restarts

A tab's session ID is the conversation's identity. It is the key that maps to all stored messages and provides critical context continuity for the model. Once assigned, a session ID MUST NEVER be replaced.

- One tab = one session ID for the lifetime of that conversation
- When the backend restarts and loses its in-memory SDK client, it MUST create a fresh SDK client but continue using the ORIGINAL session ID for all persistence and frontend communication
- The SDK's internal session ID is an implementation detail — it MUST NOT leak into the app's session model
- All messages (user + assistant) MUST be saved under the original session ID
- The `session_start` SSE event MUST always carry the original session ID
- The frontend tab's localStorage entry MUST NOT be overwritten with a different session ID

**Anti-pattern**: Backend falls back to a fresh SDK session and emits `session_start` with the NEW SDK session ID, causing the tab to silently switch IDs and orphan all previous messages.

**Correct pattern**: Backend detects resume failure → creates fresh SDK client → maps the SDK's internal session ID to the app's original session ID → saves all messages under the original ID → emits `session_start` with the original ID.

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
- [ ] Permission state is guarded by `isActiveTab` before showing modal
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
- Per-session concurrency locks (prevents double-send)
- Per-session permission queues (prevents cross-session contention)
- Session ID stability across backend restarts (resume-fallback path)
