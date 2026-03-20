# SwarmAI Chat Session — End-to-End Architecture

## Overview

SwarmAI is a Tauri 2.0 desktop app with a React frontend and a Python FastAPI backend sidecar. Chat messages flow from the React UI → FastAPI SSE endpoint → Claude Agent SDK (ClaudeSDKClient) → AWS Bedrock (or Anthropic API) → streamed back via SSE → React state updates → rendered in the UI.

The Claude Agent SDK manages the actual conversation state (multi-turn history) internally. SwarmAI does NOT manually assemble conversation history arrays — the SDK handles that via its long-lived subprocess. SwarmAI's role is: session management, context injection (system prompt), message persistence, and SSE streaming orchestration.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    DESKTOP (Tauri 2.0)                       │
│                                                              │
│  ┌──────────────┐   ┌──────────────────┐   ┌─────────────┐ │
│  │  ChatPage.tsx │──▶│ chatService.ts   │──▶│ fetch() SSE │ │
│  │  (React)      │   │ streamChat()     │   │ POST /api/  │ │
│  │               │◀──│ onMessage()      │◀──│ chat/stream │ │
│  └──────┬───────┘   └──────────────────┘   └──────┬──────┘ │
│         │                                          │        │
│  ┌──────▼───────────────────┐                      │        │
│  │ useChatStreamingLifecycle│                      │        │
│  │ useUnifiedTabState     │                      │        │
│  │ (state machines, tabs)   │                      │        │
│  └──────────────────────────┘                      │        │
└────────────────────────────────────────────────────┼────────┘
                                                     │ HTTP/SSE
┌────────────────────────────────────────────────────┼────────┐
│                 BACKEND (FastAPI sidecar)           │        │
│                                                     ▼        │
│  ┌──────────────┐   ┌──────────────────┐   ┌─────────────┐ │
│  │ chat.py      │──▶│ SessionRouter     │──▶│ClaudeSDK    │ │
│  │ POST /stream │   │ run_conversation │   │Client       │ │
│  │ SSE response │◀──│ _run_query_on_   │◀──│(subprocess) │ │
│  │              │   │  client          │   │             │ │
│  └──────────────┘   └───────┬──────────┘   └──────┬──────┘ │
│                             │                      │        │
│  ┌──────────────┐   ┌──────▼──────────┐           │        │
│  │ SQLite DB    │◀──│ session_manager  │           │        │
│  │ ~/.swarm-ai/ │   │ _save_message   │           ▼        │
│  │ data.db      │   └─────────────────┘   ┌─────────────┐ │
│  └──────────────┘                         │ AWS Bedrock  │ │
│                                           │ (or Anthropic│ │
│                                           │  API)        │ │
│                                           └─────────────┘ │
└────────────────────────────────────────────────────────────┘
```

---

## Phase 1: Chat Session Start (New Conversation)

### 1.1 User Opens the App

1. `main.py` lifespan starts → initializes DB, loads `config.json` via `AppConfigManager`
2. `session_registry.initialize()` receives injected `AppConfigManager`, `CmdPermissionManager`, `CredentialValidator`
3. Frontend `ChatPage.tsx` mounts → queries `/api/agents` to get the default SwarmAgent
4. `useUnifiedTabState` restores tabs from `~/.swarm-ai/open_tabs.json`
5. `useChatStreamingLifecycle` initializes: `messages=[]`, `sessionId=undefined`, `isStreaming=false`

### 1.2 User Types First Message and Hits Send

**Frontend (`ChatPage.tsx` → `handleSendMessage`):**

```
User types "Hello" → clicks Send
  │
  ▼
handleSendMessage()
  ├── Guard: no empty text, no double-send (checks tabState.isStreaming)
  ├── Build content array (text + optional file attachments)
  ├── Create optimistic user Message object → setMessages(prev => [...prev, userMsg])
  ├── Create empty assistant Message placeholder → setMessages(prev => [...prev, assistantMsg])
  ├── setIsStreaming(true, tabId)
  ├── updateTabStatus(tabId, 'streaming')
  ├── incrementStreamGen() — new generation counter (stale handler protection)
  │
  └── chatService.streamChat({
        agentId, message: "Hello", sessionId: undefined,
        enableSkills, enableMCP
      }, onMessage, onError, onComplete)
```

**`chatService.streamChat()` (`desktop/src/services/chat.ts`):**

```typescript
fetch(`http://localhost:${port}/api/chat/stream`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    agent_id: request.agentId,
    message: "Hello",
    session_id: null,
    enable_skills: true,
    enable_mcp: false,
  }),
  signal: controller.signal,
})
```

The response is an SSE stream. The client reads it line-by-line, parsing `data: {...}\n\n` frames, ignoring heartbeats, and calling `onMessage(event)` for each parsed event.

---

## Phase 2: Backend Receives the Request

### 2.1 Router: `POST /api/chat/stream` (`backend/routers/chat.py`)

```python
@router.post("/stream")
async def chat_stream(request: Request):
    body = await request.json()
    chat_request = ChatRequest(**body)
    agent = await db.agents.get(chat_request.agent_id)
    return StreamingResponse(
        sse_with_heartbeat(message_generator()),
        media_type="text/event-stream",
    )
```

`sse_with_heartbeat` is a fan-in wrapper: it runs the actual `message_generator()` in a background task, puts messages into an `asyncio.Queue`, and the main loop either yields messages or sends heartbeat pings every 15 seconds to keep the SSE connection alive.

### 2.2 SessionRouter.run_conversation()

```python
async def run_conversation(self, agent_id, user_message, content, session_id, ...):
    is_resuming = session_id is not None
    query_content = user_message  # or content list for attachments
    async for event in self._execute_on_session(
        agent_config, query_content, display_text,
        session_id=None, is_resuming=False, ...
    ):
        yield event
```

---

## Phase 3: Session Execution (`_execute_on_session`)

This is the core orchestration method. It handles two paths:

### PATH A: New Session (first message)

```
_execute_on_session()
  │
  ├── 1. _configure_claude_environment(config)
  │     Sets env vars: CLAUDE_CODE_USE_BEDROCK=true, AWS_REGION, etc.
  │     Does NOT set AWS credentials (delegated to credential chain)
  │
  ├── 2. Pre-flight credential validation (Bedrock only)
  │     CredentialValidator.is_valid() → boto3 STS get_caller_identity
  │     If expired → yield error event, return early
  │
  ├── 3. _build_options(agent_config, enable_skills, enable_mcp, resume=None)
  │     │
  │     ├── _resolve_allowed_tools()     → ["Bash", "Read", "Write", ...]
  │     ├── _build_mcp_config()          → MCP server definitions
  │     ├── _build_hooks()               → 4-layer PreToolUse defense chain
  │     ├── _build_sandbox_config()      → Bash sandboxing settings
  │     ├── _resolve_model()             → "us.anthropic.claude-opus-4-6-v1"
  │     ├── _build_system_prompt()       → Context assembly (see Phase 3.1)
  │     │
  │     └── Returns ClaudeAgentOptions(
  │           system_prompt=...,
  │           model="us.anthropic.claude-opus-4-6-v1",
  │           allowed_tools=[...],
  │           mcp_servers={...},
  │           hooks=[...],
  │           cwd="/Users/x/.swarm-ai/SwarmWS",
  │           permission_mode="bypassPermissions",
  │           resume=None,
  │         )
  │
  ├── 4. Create ClaudeSDKClient
  │     wrapper = _ClaudeClientWrapper(options)
  │     client = await wrapper.__aenter__()
  │
  └── 5. _run_query_on_client(client, query_content, ...)
```

### PATH B: Resumed Session (2nd+ message)

```
_execute_on_session(session_id="abc-123", is_resuming=True)
  │
  ├── Check SessionUnit instances for existing long-lived client
  │
  ├── IF client found (subprocess still alive):
  │     Reuse it directly → _run_query_on_client(existing_client, ...)
  │     The SDK subprocess maintains full conversation history internally
  │
  └── IF client NOT found (server restart, TTL expired):
        Resume-fallback: start fresh SDK session
        options = _build_options(..., resume=None)
        client = new ClaudeSDKClient(options)
        Conversation history lost from SDK perspective
        But messages persisted in SQLite for UI display
```

### 3.1 System Prompt & Context Assembly (`_build_system_prompt`)

The system prompt is assembled by three cooperating systems:

```
_build_system_prompt() in SessionRouter
  │
  ├── Step 1: ContextDirectoryLoader (global context from SwarmWS/.context/)
  │     │
  │     ├── ensure_directory() → copy defaults from backend/context/
  │     ├── Compute dynamic token budget based on model context window
  │     ├── load_all(model_context_window)
  │     │     → ≥64K: L1 cache (if fresh) or assemble 11 source files
  │     │     → <64K: L0 compact cache
  │     ├── BOOTSTRAP.md detection (ephemeral onboarding, prepended)
  │     ├── DailyActivity reading (today + yesterday, 2K token cap per file)
  │     └── Appended to agent_config["system_prompt"]
  │
  ├── Step 2: Metadata collection for TSCC viewer
  │     Per-file: filename, tokens, truncated, user_customized
  │     Stored on agent_config["_system_prompt_metadata"]
  │
  └── Step 3: SystemPromptBuilder (non-file sections only)
        → Identity ("You are {name}...")
        → Safety principles (6 rules)
        → Workspace cwd
        → Selected directories (if add_dirs)
        → Date/time (UTC + local)
        → Runtime metadata (agent, model, OS, channel)
```

### What the Final System Prompt Looks Like

```
## SwarmAI
<contents of SWARMAI.md>

## Identity
<contents of IDENTITY.md>

## Soul
<contents of SOUL.md>

## Agent Directives
<contents of AGENT.md>

## User
<contents of USER.md>

## Steering
<contents of STEERING.md>

## Tools
<contents of TOOLS.md>

## Memory
<contents of MEMORY.md>

## Knowledge
<contents of KNOWLEDGE.md>

## Projects
<contents of PROJECTS.md>

## Daily Activity (2026-03-07)
<today's activity log, capped at 2K tokens>

You are SwarmAgent, a personal assistant running inside SwarmAI. ...

## Safety Principles
- You have no independent goals beyond helping the user.
- Never attempt self-preservation...

Your working directory is: `/Users/x/.swarm-ai/SwarmWS`

Current date/time: 2026-03-07 10:30 UTC / 2026-03-07 18:30 CST

`agent=SwarmAgent | model=us.anthropic.claude-opus-4-6-v1 | os=Darwin (arm64) | channel=direct`
```

### 3.2 Model Resolution (`_resolve_model`)

```python
def _resolve_model(self, agent_config):
    model_id = agent_config.get("model") or self._config.get("default_model") or "claude-opus-4-6"
    if os.environ.get("CLAUDE_CODE_USE_BEDROCK") == "true":
        bedrock_map = self._config.get("bedrock_model_map")
        return get_bedrock_model_id(model_id, bedrock_map)
    return model_id
```

Config.json `default_model` is the single source of truth (via `AppConfigManager`).

---

## Phase 4: Query Execution & Response Streaming (`_run_query_on_client`)

### 4.1 Sending the Query

```python
await client.query("Hello")
# OR multimodal (images/PDFs)
async def multimodal_message_generator():
    yield {"type": "user", "message": {"role": "user", "content": [...]}}
await client.query(multimodal_message_generator())
```

The SDK handles conversation history management, tool execution, and agentic loops internally.

### 4.2 Fan-In Message Loop

Two concurrent tasks feed a single `asyncio.Queue`:

```
┌─────────────────────┐     ┌──────────────────────┐
│ sdk_message_reader   │     │ permission_forwarder  │
│ (reads SDK responses)│     │ (monitors permission  │
│                      │     │  request queue)       │
└──────────┬──────────┘     └──────────┬───────────┘
           │                           │
           ▼                           ▼
      ┌────────────────────────────────────┐
      │         combined_queue             │
      │  (fan-in: merges both streams)     │
      └──────────────┬─────────────────────┘
                     │
                     ▼
              Main Message Loop
              (dispatches by source)
```

### 4.3 SDK Message Dispatch

```
for each item from combined_queue:
  │
  ├── source="sdk_done"  → break (stream finished)
  ├── source="permission" → yield cmd_permission_request event
  ├── source="error"     → break (SDK reader failed)
  │
  └── source="sdk" → message type dispatch:
      │
      ├── ResultMessage (checked FIRST)
      │   ├── subtype="error_during_execution" → yield error event
      │   ├── is_error=True → yield error event (auth failures, etc.)
      │   └── has result text → yield assistant event + accumulate
      │
      ├── SystemMessage
      │   ├── subtype="init" → capture session_id, store session, save user msg
      │   ├── subtype="task_started" → yield agent_activity event
      │   └── other → skip (internal metadata)
      │
      └── Other (AssistantMessage, ToolUseMessage, etc.)
          └── _format_message() → convert to SSE-friendly dict
              ├── type="assistant" → yield + accumulate content blocks
              ├── type="ask_user_question" → yield, save partial, return
              ├── type="cmd_permission_request" → yield, save partial, return
              └── (ResultMessage) → save full response, yield "result"
```

### 4.4 Message Persistence

Messages are saved to SQLite at specific points:

| When | What | Where |
|------|------|-------|
| `SystemMessage(init)` | User message | `_save_message(session_id, "user", content)` |
| `ResultMessage` | Full assistant response | `_save_message(session_id, "assistant", accumulated_blocks)` |
| `ask_user_question` | Partial assistant response | `_save_message(session_id, "assistant", accumulated_blocks)` |
| `cmd_permission_request` | Partial assistant response | `_save_message(session_id, "assistant", accumulated_blocks)` |

The `ContentBlockAccumulator` deduplicates blocks by key (text content hash, tool_use id, tool_result tool_use_id) using O(1) hash-set lookup.

### 4.5 Tool Summarization

`tool_summarizer.py` transforms raw tool calls for the UI:

- `summarize_tool_use(name, input)` → ≤200-char human-readable summary
- `get_tool_category(name)` → category string for frontend icon mapping
- `truncate_tool_result(content, limit)` → truncated content + flag (default 500 chars)
- Sensitive tokens (passwords, API keys) redacted via regex patterns

---

## Phase 5: SSE Events Flow Back to Frontend

### 5.1 SSE Wire Format

```
data: {"type":"session_start","sessionId":"abc-123"}\n\n
data: {"type":"assistant","content":[{"type":"text","text":"Hello!"}],"model":"claude-opus-4-6"}\n\n
data: {"type":"assistant","content":[{"type":"tool_use","id":"tu_1","name":"Bash","input":{"command":"ls"}}]}\n\n
data: {"type":"result","session_id":"abc-123","duration_ms":1500,"num_turns":1}\n\n
data: {"type":"heartbeat","timestamp":1709123456.789}\n\n
```

### 5.2 Frontend SSE Processing (`chatService.streamChat`)

```typescript
while (true) {
  const { done, value } = await reader.read();
  buffer += decoder.decode(value, { stream: true });
  for (const line of buffer.split('\n')) {
    if (line.startsWith('data: ')) {
      const event = JSON.parse(line.slice(6));
      if (event.type === 'heartbeat') continue;
      onMessage(event);
    }
  }
}
```

### 5.3 Stream Handler (`createStreamHandler` in `useChatStreamingLifecycle`)

```
onMessage(event)
  │
  ├── session_start → setSessionId(event.sessionId), update tabState
  │
  ├── assistant → updateMessages() — merge content blocks into assistant message
  │   ├── Always update tabState.messages (per-tab map, even background tabs)
  │   └── If active tab → setMessages(prev => updateMessages(prev, ...))
  │
  ├── ask_user_question → setPendingQuestion(), setIsStreaming(false)
  │   └── updateTabStatus('waiting_input')
  │
  ├── cmd_permission_request → setPendingPermission(), setIsStreaming(false)
  │   └── updateTabStatus('permission_needed')
  │
  ├── result → setIsStreaming(false), invalidate queries
  │   └── updateTabStatus('idle' or 'complete_unread')
  │
  └── error → append error text to assistant message
      └── updateTabStatus('error')
```

### 5.4 The `updateMessages` Pure Function

Core message merge logic — finds the assistant message by ID and merges new content blocks:

```typescript
function updateMessages(prev, assistantMessageId, newContent, model) {
  return prev.map(msg =>
    msg.id === assistantMessageId
      ? { ...msg, content: mergeContentBlocks(msg.content, newContent), model }
      : msg
  );
}
```

Content blocks merged by type: `text` (concatenated/replaced), `tool_use` (matched by id), `tool_result` (matched by tool_use_id).

---

## Phase 6: Second User Message (Resumed Session)

### 6.1 Frontend Sends with sessionId

```typescript
chatService.streamChat({
  agentId: selectedAgentId,
  message: "What files are in the project?",
  sessionId: "abc-123",  // captured from session_start event
  enableSkills: true,
  enableMCP: false,
})
```

### 6.2 Backend: Client Reuse (PATH B)

```python
reused_client = self._get_active_client("abc-123")
if reused_client:
    # Reuse long-lived subprocess — SDK has full conversation history
    yield {"type": "session_start", "sessionId": "abc-123"}
    await self._save_message("abc-123", "user", deferred_content)
    async for event in self._run_query_on_client(reused_client, ...):
        yield event
else:
    # Resume-fallback: subprocess died — start fresh SDK session
    client = new ClaudeSDKClient(options)  # fresh, no --resume
```

### 6.3 The `effective_session_id` Pattern

Throughout the resume-fallback flow, the code uses `effective_session_id` (~15 occurrences) to ensure all persistence and client keying uses the stable app session ID:

```python
effective_session_id = (
    session_context["app_session_id"]
    if session_context.get("app_session_id") is not None
    else session_context["sdk_session_id"]
)
```

This pattern appears in:
- Message persistence (`_save_message`)
- Client storage (`SessionUnit instances[effective_session_id]`)
- Session cleanup (`_cleanup_session(eff_sid)`)
- Result event emission (`yield {"type": "result", "session_id": effective_session_id}`)
- TSCC metadata keying

### 6.4 How Multi-Turn Context Works

The Claude Agent SDK manages conversation history internally:

```
SDK Subprocess (long-lived)
  ├── Turn 1: user="Hello" → assistant="Hi there!"
  │   (stored in SDK's internal transcript)
  ├── Turn 2: user="What files?" → SDK sends to Bedrock:
  │   { "system": "<system prompt>",
  │     "messages": [
  │       {"role": "user", "content": "Hello"},
  │       {"role": "assistant", "content": "Hi there!"},
  │       {"role": "user", "content": "What files?"}
  │     ] }
  └── Turn N: Full history accumulates in the subprocess
```

SwarmAI does NOT manually build the messages array. The SDK subprocess maintains it.

---

## Phase 7: Session Lifecycle & Cleanup

### 7.1 Active Session Storage

```python
self.SessionUnit instances[session_id] = {
    "client": client,
    "wrapper": wrapper,
    "created_at": time.time(),
    "last_used": time.time(),
    "failure_tracker": ToolFailureTracker(),  # Per-session evolution nudges
}
```

### 7.2 TTL Cleanup (2-hour idle timeout)

```python
async def _maintenance_loop (LifecycleManager)(self):
    while True:
        await asyncio.sleep(60)
        now = time.time()
        # Tier 1: Early DailyActivity extraction (30 min idle)
        for sid, info in idle_sessions:
            await self._extract_activity_early(sid, info)
        # Tier 2: Full cleanup (2h TTL)
        for sid in list(self.SessionUnit instances):
            if now - info["last_used"] > SESSION_TTL:  # 2 hours
                await self._cleanup_session(sid)
```

### 7.3 Graceful Shutdown

```python
@app.post("/shutdown")
async def shutdown():
    await session_registry.disconnect_all()
    # Kills all Claude CLI subprocesses
```

---

## Phase 8: Answer User Question (continue_with_answer)

When Claude asks the user a question via `AskUserQuestion` tool, the conversation pauses. The frontend collects answers and resumes:

### 8.1 Frontend → Backend

```
POST /api/chat/answer-question
{
  agent_id: "swarm-agent",
  session_id: "abc-123",
  tool_use_id: "tu_ask_1",
  answers: { "What language?": "Python" },
  enable_skills: true,
  enable_mcp: false
}
```

### 8.2 Backend: `continue_with_answer()`

```
continue_with_answer()
  ├── Format answers as JSON string
  ├── Delegate to _execute_on_session(is_resuming=True)
  │   ├── Deferred user content: [{"type": "text", "text": "User answers:\n{...}"}]
  │   ├── app_session_id = session_id (stable)
  │   └── Same PATH A/B logic as regular messages
  └── Stream response back via SSE
```

The answer is saved as a user message under the effective_session_id. The SDK subprocess receives the formatted answer and continues its agentic loop.

---

## Phase 9: Command Permission Decision (continue_with_cmd_permission)

When a dangerous bash command triggers the human approval hook, the conversation suspends:

### 9.1 Hook Suspension

```
human_approval_hook fires
  ├── Creates permission request (UUID)
  ├── Puts request in SSE queue → frontend shows PermissionRequestModal
  ├── SUSPENDS execution: await wait_for_permission_decision(request_id)
  └── Blocks until user responds
```

### 9.2 Frontend → Backend

```
POST /api/chat/cmd-permission-continue
{
  request_id: "perm-uuid-123",
  session_id: "abc-123",
  decision: "approve",  // or "deny"
  feedback: null
}
```

### 9.3 Backend: `continue_with_cmd_permission()`

```
continue_with_cmd_permission()
  ├── Look up pending request from PermissionManager
  ├── If approved:
  │   ├── CmdPermissionManager.approve(command) — persistent, filesystem-backed
  │   └── Fallback: per-session PermissionManager if pattern too broad
  ├── set_permission_decision(request_id, decision) — unblocks the waiting hook
  ├── Save decision as user message
  └── Yield permission_acknowledged event (original stream handles execution)
```

The original SSE stream (from the initial `POST /api/chat/stream`) is still alive and waiting. `set_permission_decision()` unblocks it, and the SDK continues executing (or skipping) the command.

---

## Slash Command Handling

When a user sends `/help` or similar slash commands, the SDK may handle them silently with no assistant content. The code synthesizes a default response:

```python
if is_slash_command and not assistant_content:
    command_name = display_text.strip().split()[0]
    default_response = f"Command `{command_name}` executed."
    yield {"type": "assistant", "content": [{"type": "text", "text": default_response}]}
    assistant_content.add({"type": "text", "text": default_response})
```

This ensures the frontend always has something to display for every conversation turn.

---

## Auto-Commit Workspace

Workspace auto-commit has been migrated from per-turn to per-session-close via `WorkspaceAutoCommitHook`:

```python
class WorkspaceAutoCommitHook:
    # Registered as 2nd session lifecycle hook
    # Analyzes git diff --stat, categorizes files by path pattern
    # Generates conventional commit messages (framework:, skills:, content:, project:, output:, chore:)
    # Skips trivial changes (only skill config syncs)
    # Uses shared git_lock to prevent .git/index.lock contention
```

- Fires once per session close (not per message — cleaner git history)
- Smart commit messages derived from actual file changes, not user's first message
- Categorizes by path prefix: `.context/` → `framework:`, `Knowledge/` → `content:`, etc.
- Trivial changes (only skill syncs) get `chore: session sync` or are skipped

---

## Model Context Window Resolution

`_get_model_context_window()` maps model IDs to context window sizes for L0/L1 cache selection:

```python
_MODEL_CONTEXT_WINDOWS = {
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-4-5-20250929": 200_000,
    "claude-opus-4-5-20251101": 200_000,
}
_DEFAULT_CONTEXT_WINDOW = 200_000
```

Bedrock model IDs are stripped of prefix/suffix before lookup: `us.anthropic.claude-opus-4-6-v1` → `claude-opus-4-6`. Unknown models default to 200K.

---

## Error Event Sanitization

`_build_error_event()` sanitizes error details based on environment:

- **Debug mode** (`settings.debug=True`): Full traceback included in `detail` field
- **Production mode**: Strips traceback headers, file paths with line numbers, caret lines, and library version strings
- Auth errors: Detected via `_AUTH_PATTERNS` (13 patterns), enriched with `_CREDENTIAL_SETUP_GUIDE`
- Bedrock fallback: Unclassified errors while Bedrock is active hint at expired credentials

---

## Context Usage Warning

After every turn, `_build_context_warning()` checks context window consumption and emits a `context_usage` SSE event:

| Threshold | Level | Message |
|-----------|-------|---------|
| < 70% | `ok` | No event emitted |
| 70–84% | `warn` | "Heads up — we've used about N% of this session's context" |
| ≥ 85% | `critical` | "Recommend: save context and start a new session" |

Uses `_sum_usage_input_tokens()` to extract input token count from the SDK's usage dict. The warning is emitted in both `_execute_on_session_inner()` and `continue_with_answer()` flows.

---

## Frontend Resilience: sessionStorage Persistence (Fix 5)

When `ask_user_question` arrives, the frontend persists pending state to `sessionStorage` so the conversation + question form survives component re-mounts:

```
ask_user_question event arrives
  ├── persistPendingState(sessionId, messages, pendingQuestion)
  │   └── Writes to sessionStorage: swarm_chat_pending_{sessionId}
  │       Schema: { version, messages, pendingQuestion, sessionId }
  │
  ├── On component mount: restorePendingState(sessionId)
  │   └── Reads from sessionStorage, validates schema version
  │       Returns null if corrupted/missing/version-mismatch
  │
  ├── On result event: removePendingState(sessionId)
  │   └── Cleans up the sessionStorage entry
  │
  └── Deferred stale cleanup (2s after mount):
      └── cleanupStalePendingEntries() scans up to 5 entries
          Removes entries for completed/404 sessions
```

Additional resilience:
- Large sessions (80+ tool_use blocks): tool_result content truncated to 200 chars before serializing
- Quota exceeded: graceful degradation (console.warn, continues without persistence)
- Schema versioning: `PERSISTED_STATE_VERSION = 1`, mismatched versions discarded

---

## Multi-Tab Chat — End-to-End Flow

### Tab Restoration on Startup

```
App Launch → useUnifiedTabState initializes with temporary default tab
  → ChatPage mount calls restoreFromFile()
  → open_tabs.json exists?
    YES → Clear default, hydrate saved tabs (messages=[])
          Set activeTabId, lazy-load active tab messages
    NO  → Keep default tab (fresh start)
```

### Tab Switching — Lazy Message Loading

```
User clicks Tab B (on Tab A)
  → Save Tab A state to tabMapRef
  → selectTab(tabB.id)
  → Tab B has sessionId + messages? → Restore from cache
  → Tab B has sessionId + empty messages? → loadSessionMessages from API
  → Tab B has no sessionId? → Show welcome message
```

### Parallel Streaming — Tab Isolation

Each tab has its own: `messages[]`, `sessionId`, `isStreaming`, `abortController`. Stream handlers check `isActiveTab` before updating React state. Inactive tab messages update only in `tabMapRef` (no re-renders).

### Tab Persistence

Any tab mutation → renderCounter bumps → debounced save (500ms) → `PUT /api/settings/open-tabs` → writes `~/.swarm-ai/open_tabs.json`. Saves metadata only (not messages) — messages live in the database.

---

## ChatThread System

ChatThreads provide project-scoped conversation binding:

- `chat_threads` table: id, workspace_id, agent_id, project_id, mode
- `ChatThreadManager`: CRUD, project-filtered listing, mid-session binding
- Thread summaries: per-thread AI-generated summaries stored in DB
- Global threads: conversations not bound to any project
- Binding: `POST /api/chat_threads/{id}/bind` for mid-session project association

---

## TSCC (Thread-Scoped Cognitive Context)

In-memory per-thread state with LRU eviction (max 200 entries):

- `TSCCStateManager`: OrderedDict keyed by thread_id
- Lifecycle states: new → active → paused/failed/cancelled/idle
- Per-thread asyncio.Lock prevents concurrent mutation
- System prompt metadata stored separately in `session_registry.system_prompt_metadata`
- Frontend `useTSCCState` hook + `TSCCPanel` component display live context

---

## Known Gap: No Project-Scoped Context Injection

The old architecture referenced an 8-layer `ContextAssembler` for project-scoped context. This module was fully removed — `grep context_assembler` returns zero matches. Currently `_build_system_prompt()` only uses:

1. `ContextDirectoryLoader` — global context from `.context/`
2. `SystemPromptBuilder` — non-file sections (safety, datetime, runtime)

There is no project-scoped context injection. When a chat is bound to a project via ChatThread, the agent receives the same global context as an unbound chat. Project-specific instructions, context files, or semantic retrieval are not implemented.

---

## ClaudeAgentOptions: Non-Prompt Fields

| # | Field | Status | Description |
|---|-------|--------|-------------|
| ① | `allowed_tools` | ✅ | Tool whitelist from agent config |
| ② | `mcp_servers` | ✅ | MCP server configs (stdio/sse/http) + channel injection |
| ③ | `plugins` | ❌ Unused | Always None — skills use symlink projection |
| ④| `permission_mode` | ✅ | Default "bypassPermissions" — enforcement via hooks |
| ⑤ | `model` | ✅ | config.json → Bedrock ID conversion |
| ⑥ | `stderr` | ✅ | Routes SDK stderr to Python logger |
| ⑦ | `cwd` | ✅ | Always `~/.swarm-ai/SwarmWS` |
| ⑧ | `setting_sources` | ✅ | `["project"]` — SDK scans `.claude/skills/` |
| ⑨ | `hooks` | ✅ | 4-layer PreToolUse chain |
| ⑩ | `resume` | ✅ | SDK session ID for multi-turn resume |
| ⑪ | `sandbox` | ✅ | macOS/Linux bash sandboxing |
| ⑫ | `can_use_tool` | ⚠️ | Only when `global_user_mode=False` |
| ⑬ | `max_buffer_size` | ✅ | Default 10MB output buffer |
| ⑭ | `add_dirs` | ❌ Unused | Always None |

---

## Summary: Complete Request Lifecycle

```
1. User types message → handleSendMessage()
2. Optimistic UI update (user msg + empty assistant placeholder)
3. chatService.streamChat() → POST /api/chat/stream (SSE)
4. FastAPI router → SessionRouter.run_conversation()
5. _execute_on_session():
   a. Configure Claude env vars (Bedrock toggle, region)
   b. Pre-flight credential check (STS call)
   c. Build ClaudeAgentOptions (tools, hooks, model, system prompt + context)
   d. Create or reuse ClaudeSDKClient (long-lived subprocess)
6. _run_query_on_client():
   a. client.query(message) → sends to Claude subprocess → Bedrock API
   b. Fan-in loop: SDK responses + permission requests → combined_queue
   c. Dispatch by message type → yield SSE events
   d. Accumulate assistant content blocks (deduplicated via ContentBlockAccumulator)
   e. On ask_user_question: save partial, persist to sessionStorage, return
   f. On cmd_permission_request: save partial, suspend hook, return
   g. On ResultMessage: save to SQLite, auto-commit workspace, yield "result"
   h. Slash commands with no content: synthesize default response
7. SSE events stream back through FastAPI → fetch() reader
8. createStreamHandler() dispatches each event:
   - session_start → capture sessionId
   - assistant → merge content into message state
   - ask_user_question → persist to sessionStorage, show form
   - cmd_permission_request → show PermissionRequestModal
   - result → setIsStreaming(false), remove sessionStorage entry, done
9. React re-renders with updated messages
10. (If ask_user_question) User answers → POST /api/chat/answer-question
    → continue_with_answer() → _execute_on_session(is_resuming=True)
11. (If cmd_permission_request) User decides → POST /api/chat/cmd-permission-continue
    → set_permission_decision() unblocks original stream
```
