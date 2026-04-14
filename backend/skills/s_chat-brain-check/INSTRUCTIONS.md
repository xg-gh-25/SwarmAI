# Chat Brain Check

SwarmAI's chat is its brain. This skill validates chat correctness at two tiers:

- **Quick Check** (default) -- automated tests + invariant greps + regression scans + tsc. ~5 min. Run after every chat-related change.
- **Full Audit** -- quick check + scenario traces + SSE pipeline + indicator pipeline + live smoke test. ~30 min. Run before releases or after major refactors.

**Trigger:** "chat brain check" = quick check. "full chat audit" = full audit.

---

## Quick Check (default)

Run phases Q1-Q4 in order. Any BLOCK failure = do not ship.

### Q1: Automated Tests [BLOCK]

```bash
# Backend E2E (14 scenarios)
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend && \
source .venv/bin/activate && \
python -m pytest tests/test_chat_scenarios_e2e.py -v --tb=short 2>&1

# Backend chat-related
python -m pytest tests/ -k "chat or session or stream or sse or context_warning or context_inject" -v --tb=short 2>&1

# Frontend streaming (3 test files, ~170 tests)
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/desktop && \
npx vitest run src/__tests__/useChatStreamingLifecycle.test.ts \
  src/__tests__/streaming-lifecycle-preservation.test.ts \
  src/pages/__tests__/ChatPageSpinner.property.test.tsx \
  --reporter=verbose 2>&1
```

Any failure = BLOCK. Known skip: `test_context_warning_bridge::test_yields_warn_event_above_70pct`.

### Q2: State Machine Invariants [BLOCK]

Run all checks. Any failure = BLOCK.

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/desktop/src

echo "=== 2.1: Drain preserves streaming (no false-to-true gap) ==="
# Must find: if (!hasQueuedMessage) { setIsStreaming(false, ...) }
grep -A2 'hasQueuedMessage' hooks/useChatStreamingLifecycle.ts | grep 'setIsStreaming(false'

echo "=== 2.2: Generation guard in completeHandler ==="
# Must find 2 guards: tabState.streamGen and streamGenRef.current
grep -c 'streamGen.*!== capturedGen\|!== capturedGen' hooks/useChatStreamingLifecycle.ts
# Expected: >= 2

echo "=== 2.3: 3 drain sites present ==="
# Site A (result): in createStreamHandler
# Site C-error: in createErrorHandler
# Site C-complete: in createCompleteHandler (pre-guard)
grep -n 'onDrainQueue' hooks/useChatStreamingLifecycle.ts

echo "=== 2.4: Pre-guard drain before gen check ==="
# Must appear BEFORE the 'tabState.streamGen !== capturedGen' line
grep -n 'preGuardTab\|streamGen !== capturedGen' hooks/useChatStreamingLifecycle.ts | head -4
# preGuardTab line number must be LESS than streamGen check line number

echo "=== 2.5: Drain failure cleanup ==="
# cleanupStreamingState called in empty-content return AND catch block
grep -c 'cleanupStreamingState' ../pages/ChatPage.tsx
# Expected: >= 3 (declaration + 2 call sites)

echo "=== 2.6: isStreaming derived from ref (not useState) ==="
grep 'const isStreaming =' hooks/useChatStreamingLifecycle.ts
# Must contain: activeTabState?.isStreaming
# Must NOT be: useState

echo "=== 2.7: No dead event handlers ==="
grep -c 'cmd_permission_acknowledged' ../pages/ChatPage.tsx hooks/useChatStreamingLifecycle.ts
# Expected: 0 for both files

echo "=== 2.8: Permission uses standard handler ==="
grep -A5 'streamCmdPermissionContinue' ../pages/ChatPage.tsx | grep 'streamHandler'
# Must find: streamHandler passed directly as onMessage (no wrapper)
```

### Q3: Regression Patterns [BLOCK / WARN]

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai

echo "=== 3.1: Null bytes in context files [BLOCK] ==="
find ~/.swarm-ai/SwarmWS/.context/ ~/.swarm-ai/SwarmWS/Knowledge/DailyActivity/ \
  ~/.swarm-ai/SwarmWS/Projects/ -name '*.md' -exec python3 -c "
import sys
with open(sys.argv[1], 'rb') as f:
    if b'\x00' in f.read(): print(f'NULL BYTE: {sys.argv[1]}')
" {} \;
# Expected: no output

echo "=== 3.2: Binary in skills [BLOCK] ==="
find ~/.swarm-ai/SwarmWS/.claude/skills/ \
  -name '*.pyc' -o -name '*.pyo' -o -name '*.so' -o -name '*.dylib' 2>/dev/null
# Expected: no output

echo "=== 3.3: incrementStreamGen at transitions [WARN] ==="
grep -c 'incrementStreamGen' desktop/src/hooks/useChatStreamingLifecycle.ts \
  desktop/src/pages/ChatPage.tsx
# Expected: lifecycle >= 5, ChatPage >= 4
# Sites: result, error(SSE), error(connection), ask_user_question,
#   cmd_permission_request, drain, handleSendMessage, handleAnswerQuestion,
#   handleRetryQueueTimeout, handlePermissionDecision

echo "=== 3.4: userStopped guard present [WARN] ==="
grep -c 'userStopped' desktop/src/hooks/useChatStreamingLifecycle.ts
# Expected: >= 4 (check in streamHandler, check in errorHandler, set in handleStop, clear in drain/send)
```

### Q4: TypeScript [BLOCK]

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/desktop && npx tsc --noEmit 2>&1 | tail -5
```

Must be clean. Pre-existing `stall` warnings in chat.ts are acceptable.

### Quick Check Report

```
## Quick Check -- YYYY-MM-DD

| Phase | Status | Details |
|-------|--------|---------|
| Q1 Tests | PASS/BLOCK | backend N/N, frontend N/N |
| Q2 Invariants | PASS/BLOCK | 8/8 checks passed |
| Q3 Regressions | PASS/WARN/BLOCK | null bytes, binaries, patterns |
| Q4 TypeScript | PASS/BLOCK | clean / N errors |

**Verdict: SHIP IT / BLOCK**
```

---

## Full Audit (on request)

Run Quick Check first, then phases F1-F4.

### F1: Scenario Code Path Trace

For each scenario relevant to the change, trace the code path and verify the checklist. Skip scenarios unrelated to the change.

#### Scenario 1: Fresh Send (COLD start)

**Path:** `handleSendMessage` -> `streamChat` -> `POST /api/chat/stream` -> `run_conversation` -> `get_or_create_unit` (COLD) -> `send()` -> `_spawn` -> `_stream_response` -> SSE events

**Checklist:**
- [ ] `session_id` flows frontend -> backend -> unit -> `session_start` event back
- [ ] System prompt has no null bytes
- [ ] State: COLD -> IDLE (spawn) -> STREAMING (send) -> IDLE (result)
- [ ] `setIsStreaming(true)` synchronous, BEFORE async work
- [ ] `userStopped`, `hasReceivedData`, `isReconnecting`, `isResuming` all reset
- [ ] Assistant placeholder synced to React state AND tabMapRef

#### Scenario 2: Warm Send (IDLE)

- [ ] No re-spawn, SDK client reused, `_sdk_session_id` preserved

#### Scenario 3: Append While Streaming (Queue -> Drain)

**Path:** `handleSendMessage` -> queue -> `result` event: `hasQueuedMessage=true` -> streaming preserved -> `setTimeout(0)` -> `drainQueuedMessage` -> `streamChat`

**Checklist:**
- [ ] Queued message has `isQueued=true` badge + cancel button
- [ ] **Result event preserves `isStreaming=true` when queue exists**
- [ ] `resolvedSessionId` from `tabState.sessionId` (not stale ref)
- [ ] Queue cleared BEFORE send (exactly-once)
- [ ] Append: second message concatenates with first
- [ ] `incrementStreamGen()` in BOTH result handler AND drain
- [ ] Drain failure -> `cleanupStreamingState()`
- [ ] `resetUserScroll()` in drain
- [ ] Indicator stays visible through transition (no cold-start flicker)

#### Scenario 4: Stop -> Queue Drain

- [ ] `handleStop`: `isStreaming=false` synchronous, "Stopped" appended
- [ ] `userStopped=true` suppresses errors from aborted SSE
- [ ] Drain fires via `setTimeout(0)`, indicator re-enables
- [ ] `userStopped = false` reset at drain start

#### Scenario 5: Error -> Queue Drain

**Path:** Terminal error -> createErrorHandler fires -> error shown -> drain. OR SSE closes after error -> createCompleteHandler pre-guard -> drain.

- [ ] createErrorHandler terminal triggers drain when `queuedMessage` exists
- [ ] createCompleteHandler pre-guard runs BEFORE gen check
- [ ] Pre-guard guarded by `!tabState.isStreaming` (prevents double-drain)
- [ ] Error message stays in chat, new placeholder added after it

#### Scenario 6: Stop -> New Message (no queue)

- [ ] Guard passes after stop, `userStopped = false` reset on fresh send

#### Scenario 7: Permission Approve

**Path:** `handlePermissionDecision` -> `setIsStreaming(true)` -> `streamCmdPermissionContinue(... streamHandler ...)` -> `tool_result`, `text_delta`, `result`

- [ ] `onMessage` is `streamHandler` directly (no wrapper/special-casing)
- [ ] No dead `cmd_permission_acknowledged` handler
- [ ] Placeholder synced to tabMapRef
- [ ] Guard: `if (currentTabState?.isStreaming) return` prevents double-submit

#### Scenario 8: Permission Deny

- [ ] `setIsStreaming(false)` at end (cleanup), guarded by isStreaming check

#### Scenario 9: Resume Within TTL

- [ ] No context injection, subprocess reused, `_sdk_session_id` preserved

#### Scenario 10: Resume Post TTL (COLD)

- [ ] Cold resume: `state==COLD && _sdk_session_id is None && msg_count > 1`
- [ ] `session_resuming` -> "Resuming session..." indicator
- [ ] Null bytes stripped from system prompt

#### Scenario 11: Backend Auto-Retry (error -> reconnecting)

- [ ] `error` -> status='error', `reconnecting` -> status='streaming'
- [ ] `streamStartTimeRef` reset on reconnecting
- [ ] After retry: `result` fires normal drain if queue exists
- [ ] 1-frame flash between error and reconnecting (acceptable)

### F2: SSE Pipeline Integrity

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai

# [DONE] sentinel
grep -n 'data: \[DONE\]' backend/routers/chat.py
# Buffer flush
grep -n 'decoder.decode()' desktop/src/services/chat.ts
# Result sync
grep -n 'setMessages(tabState.messages)' desktop/src/hooks/useChatStreamingLifecycle.ts
# Heartbeat filter
grep -n "event.type === 'heartbeat'" desktop/src/services/chat.ts
```

- [ ] `sse_with_heartbeat` sends `[DONE]` on completion
- [ ] TextDecoder flushed on `done=true`
- [ ] `[DONE]` triggers `onComplete()`
- [ ] `result` syncs tabState.messages -> React
- [ ] Heartbeats filtered before onMessage
- [ ] Stall detection: reader + hook level

### F3: Streaming Indicator Pipeline

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/desktop/src

# Render condition
grep 'isLastAssistantForStreaming' pages/ChatPage.tsx | head -2
# Derivation
grep -A3 'const lastAssistantIdx' pages/ChatPage.tsx
# Null cases
grep -A6 'function deriveStreamingActivity' hooks/useChatStreamingLifecycle.ts
# Debounce reset
grep -B1 -A3 'lastActivityChangeTimeRef.current = 0' hooks/useChatStreamingLifecycle.ts
# Constants
grep 'ELAPSED_DISPLAY_THRESHOLD_MS\|MIN_ACTIVITY_DISPLAY_MS' hooks/useChatStreamingLifecycle.ts | head -2
# Tab switch sync
grep -A8 'bumpStreamingDerivation' hooks/useChatStreamingLifecycle.ts | head -10
# Fallback
grep 'lastAssistantIdx < 0' pages/ChatPage.tsx
```

- [ ] `isLastAssistantForStreaming = isStreaming && assistant && idx === lastAssistantIdx`
- [ ] `lastAssistantIdx` via `useMemo([messages])` with `.reduce()`
- [ ] `deriveStreamingActivity` null for: !isStreaming, no assistant, empty content
- [ ] Debounce resets `lastActivityChangeTimeRef = 0` on `!isStreaming`
- [ ] `ELAPSED_DISPLAY_THRESHOLD_MS = 10000`, `MIN_ACTIVITY_DISPLAY_MS = 1500`
- [ ] `bumpStreamingDerivation` derives immediately (no useEffect lag)
- [ ] Fallback "Thinking..." when `isStreaming && lastAssistantIdx < 0`

### F4: Live Smoke Test

Open the running app and manually test. Skip if no running instance.

| Test | Steps | Expected |
|------|-------|----------|
| Fresh send | New tab, send message | "Thinking..." -> "Running: {tool}" -> response complete |
| Append | Send during streaming | Queued badge appears, drains after first response, indicator stays visible through transition |
| Stop + drain | Send during streaming, click Stop | "Stopped" shown, queued message sends automatically |
| Permission | Trigger a bash command needing approval | Permission UI shows, approve -> tool executes -> completes |
| Tab switch | Start stream in tab A, switch to B and back | Indicator restores correctly, no flash |
| Error recovery | (if reproducible) Kill backend mid-stream | Error shown, queued message drains or stays with cancel option |

### Full Audit Report

```
## Full Audit -- YYYY-MM-DD

### Quick Check
(paste quick check report)

### F1: Scenarios
| # | Scenario | Status | Notes |
|---|----------|--------|-------|
| 1 | Fresh Send (COLD) | | |
| 2 | Warm Send (IDLE) | | |
| 3 | Append While Streaming | | |
| 4 | Stop -> Queue Drain | | |
| 5 | Error -> Queue Drain | | |
| 6 | Stop -> New Message | | |
| 7 | Permission Approve | | |
| 8 | Permission Deny | | |
| 9 | Resume Within TTL | | |
| 10 | Resume Post TTL | | |
| 11 | Backend Auto-Retry | | |

### F2: SSE Pipeline
(pass/fail per check)

### F3: Indicator Pipeline
(pass/fail per check)

### F4: Live Smoke Test
(pass/fail per test, or SKIPPED)

**Verdict: SHIP IT / BLOCK**
```

---

## Reference: setIsStreaming(false) Audit Table

Every call site. Update this table when adding new sites.

| # | Location | Trigger | Followed by true? | Severity |
|---|----------|---------|-------------------|----------|
| 1 | lifecycle: `ask_user_question` | User input pause | Yes: handleAnswerQuestion | SAFE |
| 2 | lifecycle: `cmd_permission_request` | Permission pause | Yes: handlePermissionDecision | SAFE |
| 3 | lifecycle: `result` | Stream complete | Conditional: only if `!hasQueuedMessage` | SAFE |
| 4 | lifecycle: `error` (userStopped) | Aborted stream | No | SAFE |
| 5 | lifecycle: `error` (real SSE) | Backend error | Maybe: reconnecting follows | 1-frame flash OK |
| 6 | lifecycle: compaction `kill` | Guard killed stream | No | SAFE |
| 7 | lifecycle: errorHandler (userStopped) | Suppressed error | No | SAFE |
| 8 | lifecycle: errorHandler (terminal) | Connection failure | Site C drain follows | SAFE |
| 9 | lifecycle: completeHandler | SSE close | No (gen-guarded) + pre-guard drain | SAFE |
| 10 | ChatPage: new tab init | Fresh tab | No | SAFE |
| 11 | ChatPage: plugin command | Not a stream | No | SAFE |
| 12 | ChatPage: empty content | Build failed | No | SAFE |
| 13 | ChatPage: drain cleanup | Drain failed | No | SAFE |
| 14 | ChatPage: permission deny | Deny decision | No | SAFE |
| 15 | ChatPage: handleStop | User stop | Yes: Site B drain | "Stopped" first OK |

**Rule:** Any new `setIsStreaming(false)` must be added here. If it can be followed by `true`, verify the transition is intentional (user action) or seamless (no false render frame).

## Reference: Bug Classes

| Bug Class | Root Cause | Detection | Fix Pattern |
|-----------|-----------|-----------|-------------|
| **false-to-true gap** | `false` then `true` with render gap | Q2.1 | Conditional: skip false when queue exists |
| **stale handler** | Old onComplete/onError fires | Q2.2 | streamGen generation guard |
| **orphaned queue** | Error kills stream, queue never drains | Q2.3 | 3 drain sites (A/B/C) |
| **indicator cold-start** | displayedActivity resets on false | F3 debounce check | Keep isStreaming true through drain |
| **invisible indicator** | Below viewport, stale scroll ref | F4 live test | resetUserScroll() in all drain sites |
| **null byte crash** | Binary in context/skills | Q3.1 | Strip nulls in _spawn |
| **stuck isStreaming** | Drain fails, true never cleared | Q2.5 | cleanupStreamingState in failure paths |
| **dead code** | Handler for nonexistent event | Q2.7 | Remove, pass to standard handler |
| **double-drain** | result + complete both drain | Q2.4 pre-guard | `!isStreaming` guard in pre-guard |

## When to Run

| Trigger | Tier |
|---------|------|
| Any change to chat files | Quick Check |
| Before release | Full Audit |
| After chat bug report (before AND after fix) | Full Audit |
| Weekly proactive | Quick Check |
| Major refactor (session, streaming, SSE) | Full Audit |

**Chat files:** session_unit.py, session_router.py, chat.py, useChatStreamingLifecycle.ts, ChatPage.tsx, chat.ts, context_injector.py, prompt_builder.py
