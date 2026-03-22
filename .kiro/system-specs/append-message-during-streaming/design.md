---
title: "Design: Append Message During Streaming"
date: 2026-03-21
updated: 2026-03-22
tags: [design, chat, ux, streaming, hang-recovery]
status: partially-implemented
---

# Design: Append Message During Streaming

## The Problem (User's Perspective)

Today, when Swarm is streaming a response, the user **cannot type**. The input is disabled, the textarea is greyed out, and the only option is a stop button. This creates three painful experiences:

1. **"I just thought of something"** — User has a follow-up thought mid-stream. They have to wait 30s-5min for the response to finish, then type. By then they've forgotten or context-switched.

2. **"Why is it stuck?"** — Swarm hangs (SDK subprocess stops producing events). The user sees the streaming animation forever. Input is disabled. The only escape is closing the tab or waiting 5 minutes for an automatic timeout kill. They lose the thread of what they were doing.

3. **"I clicked stop and everything broke"** — User panics, clicks stop mid-tool-use. The agent was writing files or running tests. Now the workspace is in an inconsistent state and the user has to explain what happened all over again.

**All three problems have the same root fix: let the user type and send at any time.**

## The Solution (User's Perspective)

### Scenario A: Normal Streaming

```
User: "Refactor the auth module"
[Assistant streaming... writing files, running tests...]
User types: "also fix the imports"  [Enter]

┌─────────────────────────────────────────────────┐
│  also fix the imports                           │
│  Queued — will send when response completes  x  │
└─────────────────────────────────────────────────┘

[...assistant keeps streaming, uninterrupted...]
[...response completes naturally...]

User: also fix the imports       <- auto-sent, badge removed
[Assistant streaming new response...]
```

The response is **never interrupted**. The user's message queues, shows immediately with a badge, and auto-fires when the turn completes. Input clears — feels like a normal send.

### Scenario B: Session Appears Stuck

```
User: "Analyze the codebase"
[Assistant streaming... then goes silent for 60s]

Input placeholder changes:
  "Type to queue a follow-up..."
        |
        v  (after 60s no real events)
  "Session may be stalled — send a message to recover"

User types: "continue"  [Enter]

[Behind the scenes: backend detects STREAMING state,
 kills stuck subprocess, respawns with --resume,
 sends "continue" as new query. ~30s delay.]

[Assistant streaming new response with full context...]
```

The user doesn't need to know about kill/resume mechanics. They just see: "it was stuck, I sent a message, it recovered." Their session context is preserved via `--resume`.

### Scenario C: User Wants to Stop

```
[Assistant streaming long response...]

User clicks small [stop] button (or presses Esc)

[Stream stops]
[If a message was queued, it auto-sends after stop]
[If no message was queued, input is ready for new message]
```

Stop is available but **secondary** — a small icon next to the send button, not the primary action. The mental model: "Send" is always primary. "Stop" is the escape hatch for "I don't want this at all."

### Scenario D: User Changes Their Mind

```
[Assistant streaming...]

User sends: "also fix the imports"  [Enter]
  -> Queued message appears with badge

User clicks [x] on the queued message
  -> Message removed from chat
  -> Text restored to input field
  -> Attachments restored to attachment bar
  -> User can edit and re-send, or just wait
```

## How It Works (Architecture)

### Two Paths, One UX

The user always does the same thing: type and send. The system decides the path based on state:

```
User sends message during streaming
         |
         v
    Is stream healthy?  ────────────────────────────────────┐
    (receiving SDK events                                    |
     within last 60s)                                        |
         |                                                   |
        YES                                                  NO
         |                                                   |
    QUEUE PATH                                    FORCE-RECOVERY PATH
    (frontend only)                               (backend handles)
         |                                                   |
    Store in tabState.queuedMessage               send() detects STREAMING
    Show queued badge in chat                     force_unstick_streaming()
    Clear input                                   Kill subprocess -> COLD
    Wait for stream to complete                   Respawn with --resume
    Drain on result/stop event                    Send new message
    Auto-send queued message                      Full context preserved
         |                                                   |
    Delay: 0s                                     Delay: ~30-90s
    Context: fully preserved                      Context: fully preserved
```

**The user never chooses a path.** From their perspective, they just typed and sent. The system handles the rest.

### Why Two Paths?

| | Queue Path | Force-Recovery Path |
|---|---|---|
| When | Stream is healthy (events flowing) | Stream is stuck (no SDK events >60s) |
| What happens | Message waits in frontend queue | Backend kills subprocess, resumes, re-sends |
| Delay | Zero — fires on stream completion | 30-90s — subprocess restart + resume |
| Interrupts stream? | No — current response completes fully | Yes — stuck response is abandoned |
| Context loss | None | None (`--resume` restores conversation) |

**Why not always force-recovery?** Because killing a healthy stream destroys in-progress work (file writes, test runs). The queue path is strictly better when the stream is healthy.

**Why not always queue?** Because if the SDK is hung, the stream never completes, and the queue never drains. The message sits there forever.

### SDK Constraint

Claude Agent SDK's `client.query()` cannot be called while `receive_response()` is iterating — it's not reentrant. We cannot write to the subprocess stdin mid-stream like Kiro does. The only way to send a new message during STREAMING is:

1. Kill the subprocess (`force_unstick_streaming()`)
2. Respawn with `--resume` (restores full conversation context)
3. Send the new message

This is already implemented in `session_unit.send()` (line 449-457). The `--resume` flag ensures zero context loss.

### Hang Detection (Frontend)

**Current problem:** Backend sends SSE heartbeats every 15s to keep the connection alive. The frontend's 45s stall timer (`STALL_TIMEOUT_MS`) resets on heartbeats. So when the SDK subprocess hangs, the heartbeat masks it — the frontend thinks everything is fine.

**The fix:** Track time since last **real** (non-heartbeat) SSE event. Context-aware thresholds: 60s for text generation, 180s for tool execution.

```typescript
// In useChatStreamingLifecycle.ts
const lastRealEventRef = useRef<number>(Date.now());
const pendingToolUseRef = useRef<boolean>(false);
const STALL_THRESHOLD_TEXT_MS = 60_000;   // 60s for text generation gaps
const STALL_THRESHOLD_TOOL_MS = 180_000;  // 3min for tool execution gaps

// In stream event handler:
if (event.type !== 'heartbeat') lastRealEventRef.current = Date.now();
if (event.type === 'tool_use') pendingToolUseRef.current = true;
if (event.type === 'tool_result') pendingToolUseRef.current = false;

// 10s polling interval while streaming:
const threshold = pendingToolUseRef.current
  ? STALL_THRESHOLD_TOOL_MS : STALL_THRESHOLD_TEXT_MS;
const stalled = Date.now() - lastRealEventRef.current > threshold;
```

**Context-aware thresholds (Kiro review feedback):** A Bash tool running `npm test` or a large Read can easily take 60-120s with zero SDK events. The backend emits `tool_use` at invocation, then nothing until `tool_result`. Using a flat 60s would false-alarm during legitimate tool runs. The fix: track whether a tool is in flight and use 3min threshold during tool execution, 60s during text generation.

This drives the input placeholder change:
- Normal streaming: `"Type to queue a follow-up..."`
- Likely stalled: `"Session may be stalled — send a message to recover"`

No auto-recovery. No auto-kill. Just information + the ability to act. **The user decides.**

### Backend Flow (Already Implemented)

When the user sends during a stalled session, the request hits `session_unit.send()`:

```python
# session_unit.py line 449-457 — already implemented
if self.state == SessionState.STREAMING:
    stall = self.streaming_stall_seconds
    logger.warning("auto_recover_stuck session_id=%s stall=%.0fs", ...)
    await self.force_unstick_streaming()
    # State is now COLD — falls through to spawn + send
```

`force_unstick_streaming()` (line 1734):
- Kills the subprocess via `_crash_to_cold_async(clear_identity=False)`
- Preserves `_sdk_session_id` so `--resume` works
- State: STREAMING -> DEAD -> COLD

Then `send()` continues normally: spawn new subprocess with `--resume`, send the user's message. Full conversation context restored.

**Timeout safety net:** If the user never sends, `LifecycleManager` auto-kills stuck sessions after `STREAMING_TIMEOUT_SECONDS = 300s` (5 min). This is the last resort — not the primary recovery mechanism.

## Implementation Status

| Component | Status | Location |
|-----------|--------|----------|
| `UnifiedTab.queuedMessage` field | DONE | `useUnifiedTabState.ts:139-146` |
| `Message.isQueued` field | DONE | `types/index.ts:283` |
| Queue path in `handleSendMessage` | DONE | `ChatPage.tsx:1232-1299` |
| Replace path (queue-while-queued) | DONE | `ChatPage.tsx:1244-1269` |
| `drainQueuedMessage` helper | DONE | `ChatPage.tsx:1447-1518` |
| Drain site B (`handleStop` finally) | DONE | `ChatPage.tsx:1799-1802` |
| `userStopped` clearing in drain | DONE | `ChatPage.tsx:1453` |
| Backend force-recovery (`send()` in STREAMING) | DONE | `session_unit.py:449-457` |
| Backend timeout safety net (300s) | DONE | `lifecycle_manager.py:54,261` |
| SSE heartbeat (15s keepalive) | DONE | `chat.py:138,221-307` |
| Drain site A (`result` event) | DONE | `useChatStreamingLifecycle.ts` — calls `deps.onDrainQueue` after result |
| `drainQueueRef` assignment (bug fix) | DONE | `ChatPage.tsx` — ref bridged after `drainQueuedMessage` defined |
| ChatInput always-enabled | DONE | `ChatInput.tsx` — `isStreaming` removed from disabled/opacity/attachment |
| Dual-button layout (stop + send) | DONE | `ChatInput.tsx` — stop always rendered (`invisible` when idle), no layout shift |
| Escape key for stop | DONE | `ChatInput.tsx` — Esc stops streaming, slash menu takes priority |
| Hang detection (context-aware) | DONE | `useChatStreamingLifecycle.ts` — 60s text / 180s tool thresholds |
| Stall-aware input placeholder | DONE | `ChatInput.tsx` — "stalled" / "queue" / "ask" per state |
| Queued message UI (badge + cancel) | DONE | `UserMessageView.tsx` — `schedule_send` icon, dashed border, cancel button |
| `onCancelQueued` threading | DONE | `MessageBubble.tsx` — prop threaded to `UserMessageView` |
| `handleCancelQueued` | DONE | `ChatPage.tsx` — removes message, restores text + attachments |
| `restoreAttachments` | DONE | `useUnifiedAttachments.ts` — new method on hook |
| `isQueued` persistence stripping | DEFERRED | Not needed — messages not persisted from frontend (P2) |

## Component Specs

### Already Implemented (Verified Against Codebase)

**Tab State — `queuedMessage` field** (`useUnifiedTabState.ts:139-146`):

```typescript
interface UnifiedTab {
  // ... existing fields ...
  queuedMessage?: {
    text: string;
    attachments: UnifiedAttachment[];  // raw refs, not base64-encoded
    displayContent: ContentBlock[];     // text preview for chat bubble
    messageId: string;                  // crypto.randomUUID()
  };
}
```

Stores `UnifiedAttachment[]` directly. `buildContentArray` (async, does file I/O) is deferred to drain time — no memory bloat during streaming.

**Queue path in `handleSendMessage`** (`ChatPage.tsx:1232-1299`):
- Guard ordering correct: executes inside `isStreaming` guard, BEFORE `setIsStreaming(true)` — no deadlock
- Replace path: if already queued, updates bubble in-place (same messageId)
- Syncs to `tabState.messages` — survives tab switch
- Shows attachment indicator in display content

**`drainQueuedMessage` helper** (`ChatPage.tsx:1447-1518`):
- Idempotent: `if (!tabState?.queuedMessage) return;`
- Exactly-once: clears queue BEFORE async send
- Clears `userStopped` flag before new stream (line 1453)
- Failure recovery: catch block restores queue — no message loss
- Bypasses all guards (isStreaming, pendingStreamTabs) — trusted internal call

**Drain site B** (`ChatPage.tsx:1794-1803`):

```typescript
} finally {
  setIsStreaming(false, currentTabId ?? undefined);
  if (currentTabId) updateTabStatus(currentTabId, 'idle');
  if (currentTabId) {
    setTimeout(() => drainQueuedMessage(currentTabId), 0);
  }
}
```

**Drain site A — result event** (`useChatStreamingLifecycle.ts`):

After result event updates tab status:

```typescript
// Drain site A: auto-send queued message on stream completion
if (capturedTabId && tabState?.queuedMessage) {
  setTimeout(() => deps.onDrainQueue?.(capturedTabId), 0);
}
```

Wired via ref: `drainQueueRef` in ChatPage, set to `drainQueuedMessage` after definition. The hook reads it via `deps.onDrainQueue`.

**ChatInput — always enabled** (`ChatInput.tsx`):

```typescript
// textarea — only disabled when backend offline
disabled={disabled}
className={clsx('...', disabled && 'opacity-50 cursor-not-allowed')}
placeholder={
  disabled ? 'Backend offline...'
  : isLikelyStalled ? 'Session may be stalled -- send a message to recover'
  : isStreaming ? 'Type to queue a follow-up...'
  : 'Ask Swarm anything...'
}

// Stop button — same size as send, always rendered (invisible when idle)
<button
  onClick={onStop}
  className={clsx(
    'w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 transition-colors',
    isStreaming
      ? 'text-[var(--color-text-muted)] hover:text-red-500 hover:bg-red-500/10'
      : 'invisible'
  )}
  title="Stop generation (Esc)"
  tabIndex={isStreaming ? 0 : -1}
  aria-hidden={!isStreaming}
>
  <span className="material-symbols-outlined text-[16px]">stop</span>
</button>

// Send button — always primary, queues during streaming
<button onClick={handleSend} disabled={!canSend || disabled}
  className={clsx(
    'w-7 h-7 rounded-lg flex items-center justify-center transition-colors',
    'bg-gradient-to-b from-[#3d7ef0] to-[#2b6cee]',
    (!canSend || disabled) && 'opacity-50 cursor-not-allowed'
  )}
  title={isStreaming ? 'Queue message' : 'Send message'}>
  <span className="material-symbols-outlined text-white text-[16px]">arrow_upward</span>
</button>

// Attachment button — isStreaming removed from disabled
<FileAttachmentButton disabled={isProcessingFiles || disabled} ... />
```

**Hang detection** (`useChatStreamingLifecycle.ts`):

```typescript
const lastRealEventRef = useRef<number>(Date.now());
const pendingToolUseRef = useRef<boolean>(false);
const STALL_THRESHOLD_TEXT_MS = 60_000;
const STALL_THRESHOLD_TOOL_MS = 180_000;

// In stream event handler:
if (event.type !== 'heartbeat') {
  lastRealEventRef.current = Date.now();
}
if (event.type === 'tool_use') pendingToolUseRef.current = true;
if (event.type === 'tool_result') pendingToolUseRef.current = false;

// 10s polling interval while streaming:
useEffect(() => {
  if (!isStreaming) { setIsLikelyStalled(false); return; }
  const interval = setInterval(() => {
    const threshold = pendingToolUseRef.current
      ? STALL_THRESHOLD_TOOL_MS : STALL_THRESHOLD_TEXT_MS;
    setIsLikelyStalled(Date.now() - lastRealEventRef.current > threshold);
  }, 10_000);
  return () => clearInterval(interval);
}, [isStreaming]);
```

**Queued message UI** (`UserMessageView.tsx`):

```typescript
{message.isQueued && (
  <div className="flex items-center gap-1.5 mt-1 text-xs
    text-[var(--color-text-muted)] justify-end">
    <span className="material-symbols-outlined text-sm">schedule_send</span>
    <span>Queued &mdash; will send when ready</span>
    {onCancelQueued && (
      <button onClick={onCancelQueued}
        className="ml-2 hover:text-[var(--color-text)] transition-colors"
        title="Cancel queued message">
        <span className="material-symbols-outlined text-sm">close</span>
      </button>
    )}
  </div>
)}
```

Bubble styling: `opacity-85`, dashed left border when `isQueued`.

**Cancel handler** (`ChatPage.tsx`):

```typescript
const handleCancelQueued = useCallback((tabId: string) => {
  const tabState = tabMapRef.current.get(tabId);
  if (!tabState?.queuedMessage) return;

  const queued = tabState.queuedMessage;

  // Remove from display + authoritative store
  setMessages((prev) => prev.filter((m) => m.id !== queued.messageId));
  if (tabState.messages) {
    tabState.messages = tabState.messages.filter(
      (m) => m.id !== queued.messageId
    );
  }

  tabState.queuedMessage = undefined;
  setInputValue(queued.text);

  if (queued.attachments.length > 0) {
    restoreAttachments(queued.attachments);
  }
}, [setMessages, setInputValue, tabMapRef, restoreAttachments]);
```

**`restoreAttachments`** (`useUnifiedAttachments.ts`):

```typescript
const restoreAttachments = useCallback(
  (restored: UnifiedAttachment[]): void => {
    const tid = tabIdRef.current;
    if (!tid) return;
    updateAttachments(tid, () => [...restored]);
    setError(null);
  },
  [tabIdRef, updateAttachments],
);
```

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| Stream completes before queue displays | Drain fires via setTimeout(0), auto-sends. Brief "Queued" flash. |
| Stream errors while queued | Queue stays with badge. NOT auto-sent on error. User can cancel or wait. |
| Stop while queued | `handleStop` finally drains queue. User stopped the response, not their follow-up. |
| Send twice while streaming | Replace: update existing bubble in-place, overwrite queue slot. One slot per tab. |
| Close tab while queued | Queue discarded. Acceptable — same as closing during any operation. |
| `pendingStreamTabs` (no session yet) | Rejected — can't queue before a session exists. |
| Multi-tab | Queue is per-tab. Tab A's queue is independent of Tab B. |
| Permission request during queue | Queue stays. If denied -> error -> queue NOT drained. Cancel available. |
| Tab switch while queued | Queue survives in `tabMapRef`. Badge renders correctly on switch back. |
| Drain fails (network error) | Queue restored via try/catch. No message loss. |
| Drain race (result + stop same tick) | Both use setTimeout(0). First clears queue; second is no-op (idempotent). |
| SDK hang + user sends | Backend `send()` detects STREAMING, kills subprocess, resumes, sends new message. 30-90s delay. Full context preserved. |
| SDK hang + user does nothing | LifecycleManager kills after 300s. Session recoverable via new message. |
| Heartbeat masks hang | Frontend tracks last real (non-heartbeat) event. Context-aware threshold: 60s text / 3min tool. |
| Long tool run (npm test) | `pendingToolUseRef` extends threshold to 3min. No false stall alarm. |
| Escape key during streaming | Stops generation. Slash command menu takes priority if open. |

## Why Not Auto-Stop?

Considered and rejected:

1. **Users panic** — "What happens to my 3-minute refactoring task?"
2. **Lost work** — Mid-tool-use stop leaves workspace inconsistent
3. **Wrong mental model** — Append = "I have more to say", not "stop what you're doing"
4. **Queue is simpler** — No state machine races. Clean sequential turns.

## Correctness Properties

1. **Queue preserves full response** — Queuing never truncates, stops, or modifies the in-flight response.
2. **Exactly-once delivery** — Queued message sent exactly once on success (result or stop). Not on error. Not twice.
3. **One queue slot per tab** — Last queued message wins. Previous replaced in-place.
4. **Cancel restores everything** — Text and attachments restored to input. No orphans.
5. **Tab isolation** — Queue on Tab A has zero effect on Tab B.
6. **Hang recovery preserves context** — Force-recovery via `--resume` restores full conversation. Zero context loss.

## Testing

### Manual Test Script

| # | Test | Expected |
|---|------|----------|
| 1 | Ask long question, type follow-up during stream, press Enter | Input clears, queued badge appears, stream continues |
| 2 | Wait for stream to complete | Queued message auto-sends, new response starts |
| 3 | Queue message, click cancel before completion | Text restored to input, message removed |
| 4 | Send twice during streaming | First replaced by second, one bubble |
| 5 | Queue on Tab A, check Tab B | Tab B unaffected |
| 6 | Stream errors while queued | Queue stays with badge, not auto-sent |
| 7 | Queue message, click Stop | Stream stops, queued message auto-sends |
| 8 | Queue, switch tabs, switch back | Queued badge still visible |
| 9 | Queue with file attachment | Badge shows, attachment sends on completion |
| 10 | Cancel queued message with attachment | Both text and attachment restored |
| 11 | Wait for stream to stall >60s | Placeholder changes to stall message |
| 12 | Send during stalled session | Session recovers (30-90s), response with full context |

### Unit Tests

- `test_send_during_streaming_queues`: isStreaming=true -> queuedMessage stored, shown in chat
- `test_queue_replaces_previous`: Send twice -> only last exists, same messageId
- `test_cancel_queued_restores_input`: Cancel -> text + attachments restored, message removed
- `test_auto_send_on_completion`: result event + queuedMessage -> drainQueuedMessage called
- `test_no_auto_send_on_error`: error event + queuedMessage -> queue untouched
- `test_queue_per_tab_isolation`: Queue Tab A, complete Tab B -> Tab A queue untouched
- `test_stop_drains_queue`: Stop while queued -> finally drains queue
- `test_drain_failure_restores_queue`: streamChat throws -> queue restored
- `test_replace_updates_existing_bubble`: Replace -> same messageId, content updated
- `test_tab_switch_preserves_queue`: Queue, switch, switch back -> badge renders
- `test_permission_deny_preserves_queue`: Deny -> error -> queue stays
- `test_drain_bypasses_guards`: No isStreaming/pendingStreamTabs check in drain
- `test_drain_builds_content_at_send_time`: buildContentArray called at drain, not queue time
- `test_cancel_restores_attachments`: Cancel with attachments -> restored
- `test_drain_clears_userStopped`: userStopped=false before new stream
- `test_stall_detection_ignores_heartbeats`: Heartbeats don't reset lastRealEvent
- `test_stall_detection_resets_on_real_events`: Tool use / text events reset lastRealEvent
- `test_stall_threshold_tool_aware`: tool_use event -> threshold becomes 180s, tool_result -> back to 60s
- `test_escape_stops_streaming`: Escape key during streaming -> onStop called
- `test_escape_slash_priority`: Escape with slash menu open -> closes menu, not stop

## Deferred Items (P2 — Kiro Review)

Captured from Kiro PE review 2026-03-22. Not blocking ship.

1. **`pendingStreamTabs` silent rejection**: Queue during session creation is silently rejected. Consider a brief toast "Wait for session to start..." or allowing queue during pending (drain after `session_start`).

2. **Error -> queue "Send now" action**: If stream errors while a message is queued, user can only cancel. Consider adding a "Send now" button on the queued badge when stream has errored, bypassing the queue to send directly.

3. **`isQueued` stripping**: Currently unneeded (messages aren't persisted from frontend). If a serializer is added, strip in the `result` event handler (co-located with the `isQueued:false` state transition) rather than a separate serializer.

## Not In Scope

- **Multiple queued messages**: One slot is enough. Extend to array later if needed.
- **Backend message queue API**: Frontend queue is sufficient. Zero backend risk.
- **Queued message persistence**: Ephemeral. App crash = re-type. Acceptable trade-off.
- **Zero-delay hang recovery (Kiro-style stdin write)**: SDK `client.query()` is not reentrant during `receive_response()`. Would require SDK API changes. Kill + resume is the best we can do today.
