---
name: chat-brain-check
description: Comprehensive chat experience audit — code review, E2E tests, SSE pipeline verification, and regression detection for SwarmAI's core chat system.
trigger:
  - chat brain check
  - chat health
  - chat regression test
  - test chat experience
  - verify chat pipeline
  - chat audit
  - is chat working
do_not_use:
  - general app health (use health-check)
  - UI-only review (use web-design-review)
  - backend API review unrelated to chat
siblings:
  - health-check = post-build smoke test
  - code-review = general code review
---

# Chat Brain Check — Comprehensive Chat Experience Audit

SwarmAI's chat is its brain. This skill runs a full audit: automated tests, code review by scenario, SSE pipeline verification, and regression pattern detection. Use it before any release, after any streaming/session change, or when chat feels broken.

## Architecture Quick Reference

```
Frontend                          Backend                         SDK/CLI
--------                          -------                         -------
ChatPage.tsx                      chat.py (SSE router)            Claude CLI subprocess
  handleSendMessage()               sse_with_heartbeat()            --system-prompt
  handleStop()                       message_generator()             --input-format stream-json
  drainQueuedMessage()             session_router.py                 stdin: query JSON
useChatStreamingLifecycle.ts        run_conversation()              stdout: SDK messages
  createStreamHandler()              _acquire_slot()
  createErrorHandler()               _persist_assistant_blocks()
  createCompleteHandler()          session_unit.py
  setIsStreaming()                   send() -> _stream_response()
  appendTextDelta()                  _read_formatted_response()
  updateMessages()                   _spawn() -> ClaudeClientWrapper
chat.ts (SSE service)               interrupt() / kill()
  streamChat()                     context_injector.py
  reader.read() loop                 build_resume_context()
  buffer + TextDecoder             prompt_builder.py
  [DONE] sentinel                    build_options() / build_system_prompt()
```

## Execution Plan

Run ALL phases in order. Report results with pass/fail per check.

---

### Phase 1: Automated Tests (MUST PASS)

Run the E2E scenario tests. These cover all 6 user scenarios with mocked SDK.

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend && \
source .venv/bin/activate && \
python -m pytest tests/test_chat_scenarios_e2e.py -v --tb=short 2>&1
```

**Expected:** 14/14 pass. ANY failure is a P0 blocker.

Then run all chat-related tests:

```bash
python -m pytest tests/ -k "chat or session or stream or sse or context_warning or context_inject" -v --tb=short 2>&1
```

**Expected:** All pass except the known pre-existing `test_context_warning_bridge::test_yields_warn_event_above_70pct`.

---

### Phase 2: Scenario-by-Scenario Code Review

For each scenario, trace the EXACT code path and verify correctness. Read the actual files — don't guess.

#### Scenario 1: Fresh Send (COLD start)

**Path:** `handleSendMessage` -> `streamChat` -> `POST /api/chat/stream` -> `run_conversation` -> `get_or_create_unit` (COLD) -> `_acquire_slot` -> `build_options` -> `send()` -> `_ensure_spawned` -> `_spawn` -> `_stream_response` -> `_read_formatted_response` -> SSE events

**Files to review:**
- `desktop/src/pages/ChatPage.tsx` — `handleSendMessage` function
- `backend/routers/chat.py` — `chat_stream` endpoint + `sse_with_heartbeat`
- `backend/core/session_router.py` — `run_conversation`
- `backend/core/session_unit.py` — `send()`, `_ensure_spawned`, `_spawn`, `_stream_response`
- `backend/core/prompt_builder.py` — `build_options`, `build_system_prompt`

**Verify:**
- [ ] `session_id` flows correctly (frontend -> backend -> unit)
- [ ] `session_start` event carries session_id back to frontend
- [ ] System prompt assembled without null bytes or corruption
- [ ] `result` event sent before generator returns
- [ ] `data: [DONE]\n\n` sentinel sent after generator finishes
- [ ] State transitions: COLD -> IDLE (spawn) -> STREAMING (send) -> IDLE (result)

#### Scenario 2: Warm Send (subprocess alive, IDLE)

**Path:** Same as Scenario 1 but skips spawn. `send()` detects IDLE -> goes straight to STREAMING.

**Files to review:**
- `backend/core/session_unit.py` — `send()` line ~510 (spawn check)

**Verify:**
- [ ] No re-spawn when state is IDLE
- [ ] SDK client reused (same subprocess)
- [ ] `_sdk_session_id` preserved across sends

#### Scenario 3: Append While Streaming (Queue Path)

**Path:** `handleSendMessage` -> detects `isStreaming=true` -> queue path -> `tabState.queuedMessage` set -> on `result` event: `onDrainQueue` -> `drainQueuedMessage` -> `streamChat`

**Files to review:**
- `desktop/src/pages/ChatPage.tsx` — `handleSendMessage` queue path (line ~1257), `drainQueuedMessage`
- `desktop/src/hooks/useChatStreamingLifecycle.ts` — `result` event handler drain trigger (line ~1372)

**Verify:**
- [ ] Queued message displayed with `isQueued=true` badge
- [ ] Queue drain fires after `result` event (setTimeout 0)
- [ ] Queue drain also fires after `handleStop` (line ~1913)
- [ ] `resolvedSessionId` in drain uses `tabState.sessionId` (not stale ref)
- [ ] Queued message cleared BEFORE send (exactly-once)
- [ ] Append path: second queue message concatenates with first (not replaces)

#### Scenario 4: Stop -> New Message

**Path:** `handleStop` -> abort SSE + `setIsStreaming(false)` + append "Stopped" + `chatService.stopSession` -> backend `interrupt()` -> STREAMING->IDLE. Then `handleSendMessage` -> normal send from IDLE.

**Files to review:**
- `desktop/src/pages/ChatPage.tsx` — `handleStop`
- `backend/core/session_unit.py` — `interrupt()` with stale-interrupt guard
- `backend/routers/chat.py` — `_recover_streaming_on_disconnect`

**Verify:**
- [ ] `handleStop` sets `isStreaming=false` synchronously (before async backend call)
- [ ] `userStopped=true` flag prevents spurious error events
- [ ] Backend `interrupt()` transitions STREAMING -> IDLE (warm) or kills -> COLD
- [ ] Stale-interrupt guard prevents killing a NEW stream's subprocess
- [ ] `_stop_event` cleared after interrupt (prevents stale stop in next stream)
- [ ] Queue drain fires after stop if queued message exists
- [ ] Next `send()` works from IDLE without issues

#### Scenario 5: Resume Within TTL (12hr)

**Path:** Same as Scenario 2 (Warm Send). Subprocess still alive, session IDLE.

**Verify:**
- [ ] No context injection (not cold resume)
- [ ] `_sdk_session_id` still valid
- [ ] Last used timestamp updated

#### Scenario 6: Resume Post TTL (subprocess killed, COLD)

**Path:** `handleSendMessage` -> `streamChat` -> `run_conversation` -> `get_or_create_unit` (existing or new COLD) -> cold resume detection (`state==COLD && _sdk_session_id is None && msg_count > 1`) -> `needs_context_injection=True` -> `build_resume_context` -> `_spawn` with enriched system prompt

**Files to review:**
- `backend/core/session_router.py` — cold resume detection (line ~501)
- `backend/core/context_injector.py` — `build_resume_context`
- `backend/core/session_unit.py` — `_spawn` null-byte sanitization

**Verify:**
- [ ] Cold resume detected when `state==COLD && _sdk_session_id is None`
- [ ] `msg_count > 1` check prevents injection on truly new sessions
- [ ] `session_resuming` SSE event emitted for UI indicator
- [ ] Resume context strips tool-only messages, drops last assistant message
- [ ] Token budget scales with model context window
- [ ] Null bytes stripped from system prompt before spawn
- [ ] `embedded null byte` classified as retriable for auto-retry

---

### Phase 3: SSE Pipeline Integrity

Verify the SSE data flow from backend to frontend.

**Backend side (chat.py):**
```bash
# Verify [DONE] sentinel is sent
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend
grep -n 'data: \[DONE\]' routers/chat.py
```

**Frontend side (chat.ts):**
```bash
# Verify buffer flush on stream close
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/desktop/src
grep -n 'decoder.decode()' services/chat.ts
```

**Frontend side (useChatStreamingLifecycle.ts):**
```bash
# Verify result event syncs messages
grep -n 'setMessages(tabState.messages)' hooks/useChatStreamingLifecycle.ts
```

**Verify:**
- [ ] Backend `sse_with_heartbeat` sends `data: [DONE]\n\n` on generator completion
- [ ] Frontend flushes TextDecoder + buffer on `reader.read()` done=true
- [ ] `[DONE]` sentinel triggers `onComplete()` before HTTP close
- [ ] `result` event syncs `tabState.messages` -> React state (safety net)
- [ ] Heartbeats sent every 15s during streaming
- [ ] Stall detection: 45s (reader level), 60s text / 180s tool (hook level)

---

### Phase 4: Regression Pattern Detection

Search for known anti-patterns that caused past bugs.

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai

# 1. Null bytes in workspace files that feed into system prompt
find ~/.swarm-ai/SwarmWS/.context/ ~/.swarm-ai/SwarmWS/Knowledge/DailyActivity/ ~/.swarm-ai/SwarmWS/Projects/ -name '*.md' -exec python3 -c "
import sys
with open(sys.argv[1], 'rb') as f:
    if b'\x00' in f.read():
        print(f'NULL BYTE: {sys.argv[1]}')
" {} \;

# 2. Binary files in .claude/skills/ (cause "embedded null byte" on spawn)
find ~/.swarm-ai/SwarmWS/.claude/skills/ -name '*.pyc' -o -name '*.pyo' -o -name '*.so' -o -name '*.dylib' 2>/dev/null

# 3. Check isStreaming derivation hasn't regressed (must read from tabMapRef)
grep -n 'const isStreaming' desktop/src/hooks/useChatStreamingLifecycle.ts

# 4. Check createCompleteHandler generation guard is intact
grep -n 'streamGen.*capturedGen\|capturedGen.*streamGen' desktop/src/hooks/useChatStreamingLifecycle.ts

# 5. Verify error handler suppresses user-stopped errors
grep -n 'userStopped' desktop/src/hooks/useChatStreamingLifecycle.ts | head -5

# 6. Check that result event clears streaming (not just onComplete)
grep -n 'setIsStreaming.*false.*result\|result.*setIsStreaming' desktop/src/hooks/useChatStreamingLifecycle.ts
```

**Verify:**
- [ ] No null bytes in any workspace text file
- [ ] No binary files (`.pyc`, `.so`, `.dylib`) in `.claude/skills/`
- [ ] `isStreaming` derived from `tabMapRef` + `pendingStreamTabs` (not useState)
- [ ] `createCompleteHandler` has generation guard (stale handler is no-op)
- [ ] `userStopped` flag suppresses spurious errors after stop
- [ ] `result` event calls `setIsStreaming(false)` directly (not wait for [DONE])

---

### Phase 5: Frontend TypeScript Check

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/desktop && npx tsc --noEmit 2>&1 | tail -5
```

**Expected:** Clean — no type errors.

---

## Report Format

After all phases, output a structured report:

```
## Chat Brain Check Report

### Automated Tests
- E2E Scenarios: 14/14 PASS
- Chat-related tests: N/N PASS (1 known skip)

### Scenario Code Review
| Scenario | Status | Issues |
|----------|--------|--------|
| 1. Fresh Send | PASS | — |
| 2. Warm Send | PASS | — |
| 3. Append While Streaming | PASS | — |
| 4. Stop -> New Message | PASS | — |
| 5. Resume Within TTL | PASS | — |
| 6. Resume Post TTL | PASS | — |

### SSE Pipeline
- [DONE] sentinel: PASS
- Buffer flush: PASS
- Result sync: PASS

### Regression Patterns
- Null bytes: CLEAN
- Binary in skills: CLEAN
- State derivation: CORRECT

### TypeScript
- Type check: CLEAN

### Verdict: SHIP IT / BLOCK (with reasons)
```

## When to Run

- **Before any release** — mandatory gate
- **After changes to**: session_unit.py, session_router.py, chat.py, useChatStreamingLifecycle.ts, ChatPage.tsx, chat.ts, context_injector.py, prompt_builder.py
- **After chat bug reports** — before and after the fix
- **Weekly** — proactive regression detection

## Known Acceptable Failures

- `test_context_warning_bridge::test_yields_warn_event_above_70pct` — pre-existing mock issue with empty ResultMessage detection. Not a real bug.
