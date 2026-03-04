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
│  │ useUnifiedTabState       │                      │        │
│  │ (state machines, tabs)   │                      │        │
│  └──────────────────────────┘                      │        │
└────────────────────────────────────────────────────┼────────┘
                                                     │ HTTP/SSE
┌────────────────────────────────────────────────────┼────────┐
│                 BACKEND (FastAPI sidecar)           │        │
│                                                     ▼        │
│  ┌──────────────┐   ┌──────────────────┐   ┌─────────────┐ │
│  │ chat.py      │──▶│ AgentManager     │──▶│ClaudeSDK    │ │
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
2. `AgentManager.configure()` receives injected `AppConfigManager`, `CmdPermissionManager`, `CredentialValidator`
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
        agentId, message: "Hello", sessionId: undefined,  // undefined = new session
        enableSkills, enableMCP
      }, onMessage, onError, onComplete)
```

**`chatService.streamChat()` (`desktop/src/services/chat.ts`):**

```typescript
fetch(`http://localhost:${port}/api/chat/stream`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    agent_id: request.agentId,       // snake_case for backend
    message: "Hello",                // simple text
    session_id: null,                // new session
    enable_skills: true,
    enable_mcp: false,
  }),
  signal: controller.signal,         // AbortController for cancel
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
    chat_request = ChatRequest(**body)  # Pydantic validation
    
    agent = await db.agents.get(chat_request.agent_id)  # Verify agent exists
    
    return StreamingResponse(
        sse_with_heartbeat(message_generator()),  # Wraps with 15s heartbeats
        media_type="text/event-stream",
    )
```

`sse_with_heartbeat` is a fan-in wrapper: it runs the actual `message_generator()` in a background task, puts messages into an `asyncio.Queue`, and the main loop either yields messages or sends heartbeat pings every 15 seconds to keep the SSE connection alive.

### 2.2 AgentManager.run_conversation()

```python
async def run_conversation(self, agent_id, user_message, content, session_id, ...):
    # 1. Determine if new or resumed session
    is_resuming = session_id is not None
    
    # 2. Build query content (text string or multimodal content list)
    query_content = user_message  # or content list for attachments
    
    # 3. Delegate to shared execution pattern
    async for event in self._execute_on_session(
        agent_config, query_content, display_text,
        session_id=None,  # new session
        is_resuming=False,
        ...
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
  │     ├── _build_hooks()               → Security hooks (dangerous cmd blocker, etc.)
  │     ├── _build_sandbox_config()      → Bash sandboxing settings
  │     ├── _resolve_model()             → "us.anthropic.claude-opus-4-6-v1" (Bedrock ID)
  │     ├── _build_system_prompt()       → 8-layer context assembly (see Phase 3.1)
  │     │
  │     └── Returns ClaudeAgentOptions(
  │           system_prompt=...,
  │           model="us.anthropic.claude-opus-4-6-v1",
  │           allowed_tools=[...],
  │           mcp_servers={...},
  │           hooks=[...],
  │           cwd="/Users/x/.swarm-ai/SwarmWS",
  │           permission_mode="bypassPermissions",
  │           resume=None,  # new session
  │         )
  │
  ├── 4. Create ClaudeSDKClient
  │     wrapper = _ClaudeClientWrapper(options)
  │     client = await wrapper.__aenter__()
  │     # This spawns a Claude CLI subprocess with the configured options
  │
  └── 5. _run_query_on_client(client, query_content, ...)
        # Sends the query and processes the response stream
```

### PATH B: Resumed Session (2nd+ message)

```
_execute_on_session(session_id="abc-123", is_resuming=True)
  │
  ├── Check _active_sessions for existing long-lived client
  │
  ├── IF client found (subprocess still alive):
  │     Reuse it directly → _run_query_on_client(existing_client, ...)
  │     # The SDK subprocess maintains full conversation history internally
  │
  └── IF client NOT found (server restart, TTL expired):
        # Resume-fallback: start fresh SDK session
        # The CLI subprocess is gone, --resume can't work
        options = _build_options(..., resume=None)  # fresh session
        client = new ClaudeSDKClient(options)
        # Conversation history is lost from SDK perspective
        # But messages are persisted in SQLite for UI display
```

### 3.1 System Prompt & Context Assembly (`_build_system_prompt`)

The system prompt is assembled with an 8-layer priority pipeline:

```
Layer 1: System Prompt        — Agent's base system prompt (from DB)
Layer 2: Live Work Context    — Recent messages summary (bounded to 1,200 tokens)
Layer 3: Project Instructions — .project.json instructions
Layer 4: Project Semantic     — Project-scoped context files
Layer 5: Knowledge Semantic   — Knowledge base files
Layer 6: Memory               — Agent memory files
Layer 7: Workspace Semantic   — Workspace-level context
Layer 8: Scoped Retrieval     — Tag/keyword-matched retrieval
```

Assembly uses `ContextAssembler` with a configurable token budget (default 10,000). Progressive 3-stage truncation ensures the most important layers survive budget constraints.

The assembled context is appended to the agent's `system_prompt` field before being passed to `ClaudeAgentOptions`.

**For project-scoped chats:** Uses `ContextAssembler` with `ContextSnapshotCache` (version-based caching).

**For global chats (no project):** Global context is provided by `ContextDirectoryLoader` from `~/.swarm-ai/.context/`. No project-specific layers are loaded.

### 3.2 Model Resolution (`_resolve_model`)

```python
def _resolve_model(self, agent_config):
    model_id = agent_config.get("model") or self._config.get("default_model") or "claude-opus-4-6"
    
    if os.environ.get("CLAUDE_CODE_USE_BEDROCK") == "true":
        # Convert: "claude-opus-4-6" → "us.anthropic.claude-opus-4-6-v1"
        bedrock_map = self._config.get("bedrock_model_map")
        return get_bedrock_model_id(model_id, bedrock_map)
    
    return model_id
```

---

## Phase 4: Query Execution & Response Streaming (`_run_query_on_client`)

### 4.1 Sending the Query

```python
# Simple text query
await client.query("Hello")

# OR multimodal (images/PDFs)
async def multimodal_message_generator():
    yield {"type": "user", "message": {"role": "user", "content": [...]}}
await client.query(multimodal_message_generator())
```

The `ClaudeSDKClient.query()` sends the message to the Claude CLI subprocess, which forwards it to Bedrock/Anthropic API. The SDK handles:
- Conversation history management (multi-turn)
- Tool execution (Bash, Read, Write, etc.)
- Agentic loops (tool use → tool result → continue)

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

The main loop processes messages in this order:

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
          │
          └── _format_message() → convert to SSE-friendly dict
              │
              ├── type="assistant" → yield + accumulate content blocks
              ├── type="ask_user_question" → yield, save partial, return
              ├── type="cmd_permission_request" → yield, save partial, return
              └── (ResultMessage second check) → save full response, yield "result"
```

### 4.4 Message Persistence

Messages are saved to SQLite at specific points:

| When | What | Where |
|------|------|-------|
| `SystemMessage(init)` — new session | User message | `_save_message(session_id, "user", content)` |
| `ResultMessage` — conversation complete | Full assistant response | `_save_message(session_id, "assistant", accumulated_blocks)` |
| `ask_user_question` — early return | Partial assistant response | `_save_message(session_id, "assistant", accumulated_blocks)` |
| `cmd_permission_request` — early return | Partial assistant response | `_save_message(session_id, "assistant", accumulated_blocks)` |

The `ContentBlockAccumulator` deduplicates blocks by key (text content hash, tool_use id, tool_result tool_use_id) so streaming doesn't produce duplicate entries.

---

## Phase 5: SSE Events Flow Back to Frontend

### 5.1 SSE Wire Format

```
data: {"type":"session_start","sessionId":"abc-123"}\n\n
data: {"type":"assistant","content":[{"type":"text","text":"Hello!"}],"model":"claude-opus-4-6"}\n\n
data: {"type":"assistant","content":[{"type":"tool_use","id":"tu_1","name":"Bash","input":{"command":"ls"}}]}\n\n
data: {"type":"assistant","content":[{"type":"text","text":"Here are the files..."}]}\n\n
data: {"type":"result","session_id":"abc-123","duration_ms":1500,"num_turns":1}\n\n
data: {"type":"heartbeat","timestamp":1709123456.789}\n\n
```

### 5.2 Frontend SSE Processing (`chatService.streamChat`)

```typescript
// Read SSE stream line by line
while (true) {
  const { done, value } = await reader.read();
  buffer += decoder.decode(value, { stream: true });
  
  for (const line of buffer.split('\n')) {
    if (line.startsWith('data: ')) {
      const event = JSON.parse(line.slice(6));
      if (event.type === 'heartbeat') continue;  // skip keepalives
      onMessage(event);  // → createStreamHandler
    }
  }
}
```

### 5.3 Stream Handler (`createStreamHandler` in `useChatStreamingLifecycle`)

Each SSE event is dispatched by type:

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

This is the core message merge logic. It finds the assistant message by ID and merges new content blocks:

```typescript
function updateMessages(
  prev: Message[],
  assistantMessageId: string,
  newContent: ContentBlock[],
  model?: string
): Message[] {
  return prev.map(msg =>
    msg.id === assistantMessageId
      ? { ...msg, content: mergeContentBlocks(msg.content, newContent), model }
      : msg
  );
}
```

Content blocks are merged by type:
- `text` blocks: concatenated or replaced
- `tool_use` blocks: matched by `id`, updated
- `tool_result` blocks: matched by `tool_use_id`, updated

---

## Phase 6: Second User Message (Resumed Session)

### 6.1 Frontend Sends with sessionId

```typescript
chatService.streamChat({
  agentId: selectedAgentId,
  message: "What files are in the project?",
  sessionId: "abc-123",  // ← captured from session_start event
  enableSkills: true,
  enableMCP: false,
})
```

### 6.2 Backend: `run_conversation` with session_id

```python
# is_resuming = True (session_id is not None)
# Deferred save pattern: session_start + user message save
# are deferred until we know which client path (reuse vs fresh)

async for event in self._execute_on_session(
    session_id="abc-123",
    is_resuming=True,
    app_session_id="abc-123",
    deferred_user_content=[{"type": "text", "text": "What files..."}],
    ...
):
    yield event
```

### 6.3 Client Reuse (PATH B)

```python
# _execute_on_session checks for existing client:
reused_client = self._get_active_client("abc-123")

if reused_client:
    # PATH B: Reuse long-lived subprocess
    # The SDK subprocess has full conversation history in memory
    # Just send the new query — SDK handles multi-turn context
    
    # Emit deferred session_start + save user message
    yield {"type": "session_start", "sessionId": "abc-123"}
    await self._save_message("abc-123", "user", deferred_content)
    
    # Send query on existing client
    async for event in self._run_query_on_client(reused_client, ...):
        yield event
else:
    # Resume-fallback: subprocess died (restart, TTL)
    # Start fresh SDK session — conversation history is lost from SDK
    # But UI still shows previous messages from SQLite
    client = new ClaudeSDKClient(options)  # fresh, no --resume
```

### 6.4 How Multi-Turn Context Works

The Claude Agent SDK manages conversation history internally:

```
SDK Subprocess (long-lived)
  │
  ├── Turn 1: user="Hello" → assistant="Hi there!"
  │   (stored in SDK's internal transcript)
  │
  ├── Turn 2: user="What files?" → SDK sends to Bedrock:
  │   {
  │     "system": "<assembled system prompt>",
  │     "messages": [
  │       {"role": "user", "content": "Hello"},
  │       {"role": "assistant", "content": "Hi there!"},
  │       {"role": "user", "content": "What files are in the project?"}
  │     ]
  │   }
  │   → Bedrock returns response with full context
  │
  └── Turn N: Full history accumulates in the subprocess
```

SwarmAI does NOT manually build the messages array. The SDK subprocess maintains it. SwarmAI only:
1. Sends new user messages via `client.query()`
2. Receives responses via `client.receive_response()`
3. Persists messages to SQLite for UI display and history

---

## Phase 7: Session Lifecycle & Cleanup

### 7.1 Active Session Storage

```python
# After successful conversation turn:
self._active_sessions[session_id] = {
    "client": client,           # ClaudeSDKClient instance
    "wrapper": wrapper,         # _ClaudeClientWrapper (for cleanup)
    "created_at": time.time(),
    "last_used": time.time(),
}
```

### 7.2 TTL Cleanup (12-hour idle timeout)

```python
# Background loop runs every 60 seconds:
async def _cleanup_stale_sessions_loop(self):
    while True:
        await asyncio.sleep(60)
        now = time.time()
        for sid in list(self._active_sessions):
            info = self._active_sessions[sid]
            if now - info["last_used"] > SESSION_TTL:  # 12 hours
                await self._cleanup_session(sid)
                # Calls wrapper.__aexit__() → kills CLI subprocess
```

### 7.3 Graceful Shutdown

```python
# Tauri calls POST /shutdown before killing the backend process
@app.post("/shutdown")
async def shutdown():
    await agent_manager.disconnect_all()
    # Iterates all active sessions, calls wrapper.__aexit__()
    # Kills all Claude CLI subprocesses
```

---

## Key Data Models

### SQLite Tables (DB-Canonical)

| Table | Key Fields | Purpose |
|-------|-----------|---------|
| `sessions` | id, agent_id, title, work_dir, last_accessed | Session metadata |
| `messages` | id, session_id, role, content (JSON), model | Message persistence |
| `chat_threads` | id, workspace_id, agent_id, project_id, mode | Thread binding |
| `agents` | id, name, system_prompt, model, allowed_tools | Agent configuration |

### Frontend Types

```typescript
interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: ContentBlock[];
  timestamp: string;
  model?: string;
}

interface StreamEvent {
  type: 'session_start' | 'assistant' | 'result' | 'error' |
        'ask_user_question' | 'cmd_permission_request' | 'heartbeat' | ...;
  sessionId?: string;
  content?: ContentBlock[];
  model?: string;
  // ... type-specific fields
}
```

---

## Summary: Complete Request Lifecycle

```
1. User types message → handleSendMessage()
2. Optimistic UI update (user msg + empty assistant placeholder)
3. chatService.streamChat() → POST /api/chat/stream (SSE)
4. FastAPI router → AgentManager.run_conversation()
5. _execute_on_session():
   a. Configure Claude env vars (Bedrock toggle, region)
   b. Pre-flight credential check (STS call)
   c. Build ClaudeAgentOptions (tools, hooks, model, system prompt + 8-layer context)
   d. Create or reuse ClaudeSDKClient (long-lived subprocess)
6. _run_query_on_client():
   a. client.query(message) → sends to Claude subprocess → Bedrock API
   b. Fan-in loop: SDK responses + permission requests → combined_queue
   c. Dispatch by message type → yield SSE events
   d. Accumulate assistant content blocks (deduplicated)
   e. On ResultMessage: save to SQLite, yield "result" event
7. SSE events stream back through FastAPI → fetch() reader
8. createStreamHandler() dispatches each event:
   - session_start → capture sessionId
   - assistant → merge content into message state
   - result → setIsStreaming(false), done
9. React re-renders with updated messages
```

---

## Context Injection: What Gets Sent to the Claude SDK Client

The Claude SDK client receives context through two independent channels:

1. **`ClaudeAgentOptions.system_prompt`** — a single string assembled at session creation time
2. **`ClaudeAgentOptions` fields** — hooks, tools, MCP servers, sandbox config, etc.

The system prompt is the primary vehicle for injecting knowledge, personality, and workspace context. It is built by three cooperating systems in sequence:

### Injection Architecture (Current — Post Centralized Context Directory)

```
                    ClaudeAgentOptions.system_prompt
                    ================================

  ContextDirectoryLoader         ContextAssembler          SystemPromptBuilder
  (global context from           (project-scoped           (non-file sections
   ~/.swarm-ai/.context/)         context, optional)        only)

  ┌─────────────────────┐       ┌──────────────────┐      ┌─────────────────┐
  │ 1. SWARMAI.md  (P0) │       │ Layer 1: system  │      │ 1. Identity     │
  │ 2. IDENTITY.md (P1) │       │   prompts.md     │      │ 2. Safety       │
  │ 3. SOUL.md     (P2) │       │ Layer 2: Live    │      │ 3. Workspace    │
  │ 4. AGENT.md    (P3) │       │   work context   │      │ 4. Selected dirs│
  │ 5. USER.md     (P4) │       │ Layer 3: Project │      │ 5. Date/time    │
  │ 6. STEERING.md (P5) │       │   instructions   │      │ 6. Runtime meta │
  │ 7. MEMORY.md   (P6) │       │ Layer 4-7: Sem.  │      └─────────────────┘
  │ 8. KNOWLEDGE.md(P7) │       │   context + mem   │
  │ 9. PROJECTS.md (P8) │       │ Layer 8: TBD     │
  └─────────────────────┘       └──────────────────┘
         ↓                              ↓                         ↓
  Appended to agent_config       Appended to agent_config   Final prompt string
  ["system_prompt"] first        ["system_prompt"] second   returned by build()
```

### Source-by-Source Breakdown

| Source File | Location | Injected? | How | When |
|---|---|---|---|---|
| **SWARMAI.md** | `~/.swarm-ai/.context/SWARMAI.md` | ✅ Yes | `ContextDirectoryLoader` → "## SwarmAI" section (priority 0, never truncated) | Every session |
| **IDENTITY.md** | `~/.swarm-ai/.context/IDENTITY.md` | ✅ Yes | `ContextDirectoryLoader` → "## Identity" section (priority 1, never truncated) | Every session |
| **SOUL.md** | `~/.swarm-ai/.context/SOUL.md` | ✅ Yes | `ContextDirectoryLoader` → "## Soul" section (priority 2, never truncated) | Every session |
| **AGENT.md** | `~/.swarm-ai/.context/AGENT.md` | ✅ Yes | `ContextDirectoryLoader` → "## Agent Directives" section (priority 3) | Every session |
| **USER.md** | `~/.swarm-ai/.context/USER.md` | ✅ Yes | `ContextDirectoryLoader` → "## User" section (priority 4) | Every session |
| **STEERING.md** | `~/.swarm-ai/.context/STEERING.md` | ✅ Yes | `ContextDirectoryLoader` → "## Steering" section (priority 5) | Every session |
| **MEMORY.md** | `~/.swarm-ai/.context/MEMORY.md` | ✅ Yes | `ContextDirectoryLoader` → "## Memory" section (priority 6) | Every session |
| **KNOWLEDGE.md** | `~/.swarm-ai/.context/KNOWLEDGE.md` | ✅ Yes | `ContextDirectoryLoader` → "## Knowledge" section (priority 7) | Every session |
| **PROJECTS.md** | `~/.swarm-ai/.context/PROJECTS.md` | ✅ Yes | `ContextDirectoryLoader` → "## Projects" section (priority 8) | Every session |
| **L1_SYSTEM_PROMPTS.md** | `~/.swarm-ai/.context/L1_SYSTEM_PROMPTS.md` | ✅ Auto | Pre-assembled cache of all 9 files; used when fresh (mtime check) | 128K+ models |
| **L0_SYSTEM_PROMPTS.md** | `~/.swarm-ai/.context/L0_SYSTEM_PROMPTS.md` | ✅ Auto | Compact version for small models | 32K-64K models |
| **Project context** | `ContextAssembler` 8-layer pipeline | ✅ Yes | Layers 1-8 for project-scoped chats (only when project_id exists) | Project chats |
| **Hooks** | `_build_hooks()` in AgentManager | ✅ Yes | Injected as `ClaudeAgentOptions.hooks` (NOT in system prompt) | Every session |

### Detailed Flow: How Context Reaches the Model

```
_build_options() in AgentManager
  │
  ├── Step 8: _build_system_prompt(agent_config, working_directory, channel_context)
  │     │
  │     ├── 1. ContextDirectoryLoader (global context)
  │     │     loader = ContextDirectoryLoader(~/.swarm-ai/.context/, budget=25K)
  │     │     loader.ensure_directory()  → copy defaults from backend/context/
  │     │     context = loader.load_all(model_context_window)
  │     │       → >= 64K: L1 cache (if fresh) or assemble 9 source files
  │     │       → < 64K: L0 compact cache
  │     │     Appended to agent_config["system_prompt"]
  │     │
  │     ├── 2. ContextAssembler (project-scoped, only when project_id exists)
  │     │     assembler.assemble(project_id, thread_id)
  │     │       → Layers 1-8 (system prompts, live work, instructions, etc.)
  │     │     Appended to agent_config["system_prompt"]
  │     │
  │     └── 3. SystemPromptBuilder.build() (non-file sections only)
  │           → Identity, Safety, Workspace cwd, Date/time, Runtime metadata
  │           → Returns final system prompt string
  │
  ├── Step 3: _build_hooks()
  │     → PreToolUse hooks: tool logger, dangerous cmd blocker,
  │       human approval hook, skill access checker
  │
  └── Returns ClaudeAgentOptions(system_prompt=..., hooks=..., ...)
```

### What the Final System Prompt Looks Like

```
## SwarmAI
<contents of ~/.swarm-ai/.context/SWARMAI.md>

## Identity
<contents of ~/.swarm-ai/.context/IDENTITY.md>

## Soul
<contents of ~/.swarm-ai/.context/SOUL.md>

## Agent Directives
<contents of ~/.swarm-ai/.context/AGENT.md>

## User
<contents of ~/.swarm-ai/.context/USER.md>

## Steering
<contents of ~/.swarm-ai/.context/STEERING.md>

## Memory
<contents of ~/.swarm-ai/.context/MEMORY.md>

## Knowledge
<contents of ~/.swarm-ai/.context/KNOWLEDGE.md>

## Projects
<contents of ~/.swarm-ai/.context/PROJECTS.md>

<project-scoped context from ContextAssembler, if project_id exists>

You are SwarmAgent, a personal assistant running inside SwarmAI. ...

## Safety Principles
- You have no independent goals beyond helping the user.
- Never attempt self-preservation...

Your working directory is: `/Users/x/.swarm-ai/SwarmWS`

Current date/time: 2026-03-04 10:30 UTC / 2026-03-04 17:30 ICT

`agent=SwarmAgent | model=us.anthropic.claude-opus-4-6-v1 | os=Darwin (arm64) | channel=direct`
```

---

## ClaudeAgentOptions: Non-Prompt Fields Sent to the SDK

Beyond `system_prompt`, the `ClaudeAgentOptions` object carries runtime configuration that controls what the agent CAN DO, not what it KNOWS. These fields are assembled by `_build_options()` in `AgentManager`.

### Summary Table

| # | Field | Status | Source | Current Behavior | Gap | Proposal |
|---|-------|--------|--------|-----------------|-----|----------|
| ① | `allowed_tools` | ✅ Working | `agent_config["allowed_tools"]` or enable flags | Whitelist of tool names (Bash, Read, Write, etc.) set once at session creation | Static per-session — no dynamic restriction/expansion mid-conversation | Add per-turn tool scoping via hooks or session state (e.g., read-only exploration mode) |
| ② | `mcp_servers` | ✅ Working | `agent_config["mcp_ids"]` → DB lookup + channel injection | Dict of MCP server configs (stdio/sse/http) keyed by server name | No health monitoring — crashed MCP servers produce opaque errors | Add health probes at session creation; reconnection logic for long sessions |
| ③ | `plugins` | ❌ Unused | Hardcoded `None` | Always None — plugin skills use symlink projection instead | SDK's native plugin system completely bypassed (no plugin MCP servers or hooks) | Evaluate SDK plugin capabilities; consider using for plugin-scoped MCP servers |
| ④ | `permission_mode` | ✅ Working | `agent_config["permission_mode"]` | Default `"bypassPermissions"` — actual enforcement via hooks + can_use_tool | SDK's built-in permission system unused; all enforcement is custom | Acceptable — custom hooks provide more control than SDK permissions |
| ⑤ | `model` | ✅ Working | `config.json` → `AppConfigManager` (single source of truth) | Anthropic ID → Bedrock ID conversion via `get_bedrock_model_id()` | Agent DB `model` field is silently ignored — potential confusion | Document clearly that config.json wins; consider removing agent-level model field |
| ⑥ | `stderr` | ✅ Working | Hardcoded lambda | Routes SDK stderr to Python logger | None | — |
| ⑦ | `cwd` | ✅ Working | `initialization_manager.get_cached_workspace_path()` | Always `~/.swarm-ai/SwarmWS` | Single workspace only — no per-project cwd | Support per-project cwd when agent is bound to a project |
| ⑧ | `setting_sources` | ✅ Working | Hardcoded `["project"]` | SDK scans `{cwd}/.claude/skills/` for symlinked skills | Only "project" source — no "user" or "global" sources used | Consider adding "user" source for user-level SDK config |
| ⑨ | `hooks` | ✅ Working | `_build_hooks()` → 4-layer PreToolUse chain | Tool logger, dangerous cmd blocker, human approval, skill access checker | PreToolUse only — no PostToolUse hooks; hook rules not communicated to model | Add PostToolUse for audit logging; inject "## Active Constraints" into system prompt |
| ⑩ | `resume` | ✅ Working | `session_id` from frontend | SDK session ID for multi-turn resume; falls back to fresh session if subprocess gone | System prompt not refreshed on resume — stale context if files changed | Version-check context before reuse; rebuild options if counters changed |
| ⑪ | `sandbox` | ✅ Working | `agent_config["sandbox_enabled"]` + global defaults | macOS/Linux bash sandboxing with excluded commands and network config | Windows not supported (auto-disabled) | Acceptable — Windows support is low priority |
| ⑫ | `can_use_tool` | ⚠️ Conditional | `create_file_access_permission_handler()` | Only set when `global_user_mode=False` (restricted mode); checks file paths against allowed dirs | Default is `None` (no restrictions) — most users run in global mode | Acceptable for desktop app; consider default-on for multi-user deployments |
| ⑬ | `max_buffer_size` | ✅ Working | `MAX_BUFFER_SIZE` env var | Default 10MB output buffer limit | No per-agent or per-session override | Add to agent config for agents that process large outputs |
| ⑭ | `add_dirs` | ❌ Unused | Hardcoded `None` | Always None — `agent_config["add_dirs"]` only used for system prompt section | SDK may support expanding file access scope via this field | Wire `agent_config["add_dirs"]` through to SDK for multi-directory agents |

### Field-by-Field Breakdown

```python
ClaudeAgentOptions(
    system_prompt=...,          # Covered in previous section
    allowed_tools=...,          # ① What tools the agent can use
    mcp_servers=...,            # ② External tool servers
    plugins=None,               # ③ Always None (unused)
    permission_mode=...,        # ④ How tool permissions are handled
    model=...,                  # ⑤ Which model to use
    stderr=...,                 # ⑥ Error logging callback
    cwd=...,                    # ⑦ Working directory
    setting_sources=...,        # ⑧ Where SDK discovers skills
    hooks=...,                  # ⑨ Security hooks (4-layer defense)
    resume=...,                 # ⑩ Session resume ID
    sandbox=...,                # ⑪ Bash sandboxing config
    can_use_tool=...,           # ⑫ File access permission handler
    max_buffer_size=...,        # ⑬ Max output buffer
    add_dirs=None,              # ⑭ Always None (unused)
)
```

### ① allowed_tools — Tool Whitelist

Built by `_resolve_allowed_tools(agent_config)`.

```
Source: agent_config["allowed_tools"] (from DB agents table)
Fallback: enable_bash_tool, enable_file_tools, enable_web_tools flags

Default tools when no explicit list:
  - Bash (if enable_bash_tool=True)
  - Read, Write, Edit, Glob, Grep (if enable_file_tools=True)
  - WebFetch, WebSearch (if enable_web_tools=True)

The "Skill" tool is NOT auto-included — user must enable it explicitly
via the Advanced Tools section in the agent settings UI.
```

### ② mcp_servers — MCP Server Configuration

Built by `_build_mcp_config(agent_config, enable_mcp)` + `_inject_channel_mcp()`.

```
Source: agent_config["mcp_ids"] → looked up from DB mcp_servers table
Each MCP server record contains: connection_type (stdio|sse|http), config (command, args, env, url)

Assembly:
  for each mcp_id in agent_config["mcp_ids"]:
      mcp_config = await db.mcp_servers.get(mcp_id)
      mcp_servers[server_name] = {
          "type": "stdio",
          "command": config["command"],
          "args": config["args"],
          "env": config.get("env"),
      }

Channel injection:
  If channel_context is provided (e.g., Feishu channel), a "channel-tools"
  MCP server is injected pointing to backend/mcp_servers/channel_file_sender.py
  with channel-specific env vars (FEISHU_APP_ID, CHAT_ID, etc.)

Result: dict[str, dict] keyed by server name (not ID, for shorter tool names)
```

### ③ plugins — Always None

```
Plugins are NOT passed via ClaudeAgentOptions.plugins.
Instead, plugin skills are projected as symlinks into SwarmWS/.claude/skills/
via ProjectionLayer, and the SDK discovers them through setting_sources=["project"].
This gives precise per-plugin skill control.
```

### ④ permission_mode — Tool Permission Strategy

```
Source: agent_config.get("permission_mode", "bypassPermissions")
Default: "bypassPermissions" — tools execute without asking the SDK for permission

The actual permission enforcement happens via hooks (⑨) and can_use_tool (⑫),
not via the SDK's built-in permission system.
```

### ⑤ model — Model Identifier

Built by `_resolve_model(agent_config)`.

```
Source: config.json "default_model" (single source of truth via AppConfigManager)
        Agent DB "model" field is IGNORED — config.json always wins.

If Bedrock enabled:
  "claude-opus-4-6" → get_bedrock_model_id() → "us.anthropic.claude-opus-4-6-v1"
  Checks config.json "bedrock_model_map" first, then hardcoded ANTHROPIC_TO_BEDROCK_MODEL_MAP
```

### ⑥ stderr — Error Logging

```
Value: lambda msg: logger.error(msg)
Captures SDK stderr output into the backend's Python logger.
```

### ⑦ cwd — Working Directory

```
Source: initialization_manager.get_cached_workspace_path()
Value: ~/.swarm-ai/SwarmWS (the unified SwarmWorkspace root)

This is where the Claude CLI subprocess runs. All relative file paths
in tool calls are resolved relative to this directory.
```

### ⑧ setting_sources — Skill Discovery

```
Value: ["project"]

Tells the Claude SDK: "look in {cwd}/.claude/ for skills and config."
Despite the name "project", this has NO relation to SwarmAI's Projects/ folder.

Skill discovery flow:
  1. User creates skill → writes to ~/.swarm-ai/skills/my-skill/SKILL.md
  2. ProjectionLayer creates symlink: SwarmWS/.claude/skills/my-skill → ~/.swarm-ai/skills/my-skill
  3. SDK reads setting_sources=["project"]
  4. SDK scans {cwd}/.claude/skills/
  5. SDK discovers my-skill symlink → skill is available to the agent
```

### ⑨ hooks — 4-Layer Security Defense

Built by `_build_hooks(agent_config, enable_skills, enable_mcp, ...)`.

All hooks are `PreToolUse` hooks — they fire BEFORE a tool executes.

```
Layer 1: pre_tool_logger
  Matcher: all tools
  Action: Logs tool name and input keys (observability only, never blocks)

Layer 2: dangerous_command_blocker
  Matcher: "Bash" tool only
  Action: Regex-matches command against DANGEROUS_PATTERNS (13 patterns)
          Blocks: rm -rf /, dd if=/dev/zero, fork bombs, chmod 777 /, curl|bash, etc.
          Returns permissionDecision="deny" if matched

Layer 3: human_approval_hook (created by create_human_approval_hook)
  Matcher: "Bash" tool only
  Action: Checks if command is dangerous via CmdPermissionManager (glob-based)
          If dangerous and not previously approved:
            1. Creates permission request (UUID)
            2. Puts request in SSE queue → frontend shows approval dialog
            3. SUSPENDS execution (await wait_for_permission_decision)
            4. User approves/denies via frontend
            5. If approved: stores in CmdPermissionManager (persistent, cross-session)
            6. Returns allow or deny based on user decision

Layer 4: skill_access_checker (created by create_skill_access_checker)
  Matcher: "Skill" tool only
  Condition: Only added when enable_skills=True AND allow_all_skills=False
  Action: Checks if requested skill name is in the agent's allowed_skills set
          Built-in skills are always allowed regardless of the list
          Returns permissionDecision="deny" if skill not authorized

Hook assembly result:
  hooks = {
      "PreToolUse": [
          HookMatcher(hooks=[pre_tool_logger]),           # all tools
          HookMatcher(matcher="Bash", hooks=[blocker]),   # bash only
          HookMatcher(matcher="Bash", hooks=[approval]),  # bash only
          HookMatcher(matcher="Skill", hooks=[checker]),  # skill only (conditional)
      ]
  }
```

### ⑩ resume — Session Resume ID

```
Source: session_id parameter from the frontend (for 2nd+ messages)
Value: The SDK session ID to resume, or None for new sessions

When set, the SDK attempts to resume the existing CLI subprocess session.
If the subprocess is gone (server restart, TTL), _execute_on_session
falls back to creating a fresh session (resume=None).
```

### ⑪ sandbox — Bash Sandboxing

Built by `_build_sandbox_config(agent_config)`.

```
Source: agent_config["sandbox_enabled"] (fallback: settings.sandbox_enabled_default=True)
Not supported on Windows (auto-disabled).

When enabled:
  sandbox = {
      "enabled": True,
      "autoAllowBashIfSandboxed": True,    # Auto-approve bash when sandboxed
      "excludedCommands": ["docker"],       # Commands that bypass sandbox
      "allowUnsandboxedCommands": False,    # Don't allow model to escape sandbox
      "network": {"allowLocalBinding": True}
  }

When disabled: sandbox = None
```

### ⑫ can_use_tool — File Access Permission Handler

Built by `create_file_access_permission_handler(allowed_directories)`.

```
Condition: Only set when global_user_mode=False (restricted mode)
Default: None (global_user_mode=True → no file access restrictions)

When set, this async callback is invoked for EVERY tool call:
  - File tools (Read, Write, Edit, Glob, Grep): checks file_path against allowed_directories
  - Bash tool: extracts potential file paths from command, checks each
  - Other tools: always allowed

Path validation uses os.path.realpath() to resolve symlinks before comparison,
preventing symlink-based path traversal attacks.
```

### ⑬ max_buffer_size — Output Buffer Limit

```
Source: MAX_BUFFER_SIZE env var (default: 10MB = 10 * 1024 * 1024)
Limits the maximum size of tool output the SDK will buffer.
```

### ⑭ add_dirs — Additional Directories

```
Value: Always None
The agent_config may have "add_dirs" but it's used for system prompt
section only (_section_selected_dirs), not passed to the SDK.
```

---

### TBD: ClaudeAgentOptions Gaps

#### TBD 8: No PostToolUse Hooks

All hooks are `PreToolUse` only. There are no `PostToolUse` hooks for:
- Logging tool results/outputs
- Auditing what the agent actually did (vs. what it attempted)
- Triggering side effects after tool execution (e.g., updating TSCC state from tool results)

**Potential**: Add PostToolUse hooks for audit logging, TSCC telemetry enrichment, and result validation.

#### TBD 9: plugins Field Always None

The `plugins` field is hardcoded to `None`. Plugin skills are projected via symlinks instead. This means:
- Plugin MCP servers are not supported (only plugin skills work)
- Plugin hooks are not supported
- The SDK's native plugin system is completely bypassed

**Potential**: Evaluate whether the SDK's plugin system offers capabilities beyond what symlink projection provides (e.g., plugin-scoped MCP servers, plugin hooks).

#### TBD 10: add_dirs Always None

The `add_dirs` field is always `None` even though `agent_config` may contain `add_dirs`. The value is only used for the system prompt section (`_section_selected_dirs`), not passed to the SDK.

**Gap**: The SDK may support `add_dirs` for expanding the agent's file access scope, but SwarmAI doesn't use it.

#### TBD 11: No Dynamic Tool Restriction Per-Turn

`allowed_tools` is set once at session creation and never changes. There's no mechanism to dynamically restrict or expand tools mid-conversation based on context (e.g., disable Write tool during a read-only exploration phase).

**Potential**: Implement a tool restriction layer that can be updated per-turn via hooks or session state.

#### TBD 12: No MCP Server Health Monitoring

MCP servers are configured at session creation but there's no health check or reconnection logic. If an MCP server crashes mid-session, the agent gets opaque errors.

**Potential**: Add MCP server health probes at session creation and periodic checks during long sessions.

---

## TBD: Features Not Yet Implemented

### ~~TBD 1: AGENT.md~~ — ✅ Resolved

Now loaded from `~/.swarm-ai/.context/AGENT.md` via `ContextDirectoryLoader` (priority 3).

### ~~TBD 2: HEARTBEAT.md~~ — Deferred

Not included in the centralized context directory. Periodic task awareness requires a cron/heartbeat system that doesn't exist yet. Can be added as a 10th context file when the heartbeat system is built.

### ~~TBD 3: Steering Files~~ — ✅ Resolved

Now loaded from `~/.swarm-ai/.context/STEERING.md` via `ContextDirectoryLoader` (priority 5). Contains session-level overrides, standing rules, and temporary focus areas.

### ~~TBD 4: workspace_context from Frontend~~ — ✅ Resolved

Dead code removed. The `workspace_context` field was deleted from `ChatRequest` schema, `run_conversation()` parameter, `chat.py` router, and frontend `ChatRequest` type.

### TBD 5: Layer 8 Scoped Retrieval — Placeholder

`ContextAssembler._load_layer_8_scoped_retrieval()` returns `None`. No RAG implemented.

### TBD 6: Dynamic Context Refresh on Resumed Sessions

The system prompt (including `.context/` files and project context) is assembled once when `_build_options()` runs. For resumed sessions that reuse a long-lived client (PATH B), the system prompt is NOT refreshed.

**Gap**: If `.context/MEMORY.md` or `.context/STEERING.md` change between turns, the agent won't see updates until a new session starts.

### TBD 7: Hooks as Context (Not Just Behavior)

Hooks modify runtime behavior but their rules are NOT communicated to the model via the system prompt. The model may attempt actions that will be blocked.

---

## Implemented: Centralized Context Directory

### Summary

The centralized context directory has been implemented. All context files live in `~/.swarm-ai/.context/` (runtime) with defaults sourced from `backend/context/` (repo). The system uses filesystem-only storage — no DB for context content.

### What Changed

| Aspect | Before | After |
|--------|--------|-------|
| Location | Scattered across `.swarmai/`, workspace root, DB | Single `~/.swarm-ai/.context/` directory |
| Storage | Hybrid: DB `agents.system_prompt` + filesystem | Filesystem-only — editable, git-friendly, portable |
| Core prompt | DB `agents.system_prompt` (loaded at bootstrap) | `.context/SWARMAI.md` (loaded at session start) |
| Discovery | SystemPromptBuilder + ContextAssembler + ContextManager + DB | `ContextDirectoryLoader` (global) + `ContextAssembler` (project) + `SystemPromptBuilder` (non-file) |
| Memory | Split across `Knowledge/Memory/*.md` (50 file cap) | Single `MEMORY.md` (curated) |
| Steering | Not implemented | `STEERING.md` for session-level overrides |
| Template management | `AgentSandboxManager` copying to `.swarmai/` | `ContextDirectoryLoader.ensure_directory()` copying from `backend/context/` |
| Portability | Not portable (DB + filesystem + scattered paths) | Copy `~/.swarm-ai/.context/` to another machine → same agent personality |

### Deleted Code

- `backend/core/agent_sandbox_manager.py` — fully replaced by `ContextDirectoryLoader`
- `backend/templates/` directory — all 7 files deleted, replaced by `backend/context/` (12 files)
- `SystemPromptBuilder._load_workspace_file()`, `_section_user_identity()`, `_section_project_context()`, `_section_extra_prompt()` — all file-loading methods removed
- `agent_defaults.py` SWARMAI.md→DB bootstrap code — system prompt no longer stored in DB
- `workspace_context` dead code — removed from schema, router, agent_manager, and frontend types
- Legacy `ContextManager` fallback for global chats — replaced by `ContextDirectoryLoader`

### Core Idea

One hidden directory. All context files. Filesystem only — no DB storage for context. Every file is a global context source that gets assembled into the system prompt on every session start.

Using `~/.swarm-ai/.context/` (hidden with dot prefix) keeps the context directory out of casual `ls` output while remaining easily accessible to users who know it's there — same convention as `.git/`, `.claude/`, `.kiro/`.

**Why filesystem-only, no DB:**
- Human-editable: users open files in any text editor, no API needed
- Version-controllable: `git init` in `.context/` and you have full history
- Debuggable: `cat ~/.swarm-ai/.context/MEMORY.md` to see exactly what the agent knows
- No relational queries needed: context is read-and-concatenate, not join-and-filter
- Single source of truth: no sync issues between DB and filesystem
- Portable: copy `.context/` to another machine and the agent has the same personality

The DB `agents.system_prompt` field is no longer used for context content. The `agents` table retains only metadata (name, model, allowed_tools, mcp_ids, etc.). The system prompt is assembled entirely from `.context/` files at session start.

```
~/.swarm-ai/.context/
├── SWARMAI.md              — Core system prompt
├── AGENT.md                — Behavioral rules, directives, how to act
├── SOUL.md                 — Personality, tone, communication style
├── IDENTITY.md             — Agent name, avatar, self-description
├── USER.md                 — Who the user is, preferences, timezone
├── MEMORY.md               — Cross-session persistent memory (curated)
├── KNOWLEDGE.md            — Domain knowledge, facts, reference material
├── PROJECTS.md             — Active projects summary, priorities, status
├── STEERING.md             — Session-level rules, constraints, overrides
├── L0_SYSTEM_PROMPTS.md    — Compact system prompt (auto-generated, for small models)
└── L1_SYSTEM_PROMPTS.md    — Full system prompt (auto-generated, for large models)
```

### File Responsibilities

| File | Purpose | Who Edits | When Loaded |
|------|---------|-----------|-------------|
| `SWARMAI.md` | Core system prompt — the foundational identity and mission statement. | User or developer | Every session (highest priority) |
| `AGENT.md` | Behavioral rules — how the agent should act, what to do first each session, safety rules, external vs internal action boundaries | User or agent (with permission) | Every session |
| `SOUL.md` | Personality definition — tone, vibe, communication style, boundaries. "Be genuinely helpful, not performatively helpful." | User initially, agent can evolve it | Every session |
| `IDENTITY.md` | Agent identity — name, creature type, emoji, avatar path. The "who am I" record. | User during first-run, rarely changed | Every session |
| `USER.md` | User profile — name, pronouns, timezone, preferences, what they care about, what annoys them. | Agent (learned over time), user can edit | Every session |
| `MEMORY.md` | Curated long-term memory — decisions made, lessons learned, important context from past sessions. NOT raw logs. | Agent (auto-updated), user can edit | Every session |
| `KNOWLEDGE.md` | Domain knowledge — technical facts, API references, codebase conventions, team norms. Replaces the scattered context-L0/L1 files. | User or agent | Every session |
| `PROJECTS.md` | Active projects — what's in flight, priorities, deadlines, status. Replaces per-project instructions.md for the global view. | Agent (auto-updated from DB), user can edit | Every session |
| `STEERING.md` | Session-level overrides — temporary rules, focus areas, "for this week focus on X", "don't touch Y". Replaces the missing steering concept. | User | Every session |
| `L0_SYSTEM_PROMPTS.md` | Compact system prompt — auto-generated compressed version of all context files. Used when token budget is tight (small models, 32K context). | Auto-generated | When model context < 64K |
| `L1_SYSTEM_PROMPTS.md` | Full system prompt — auto-generated assembled version of all context files. Used as a cache/snapshot for large models (128K+ context). | Auto-generated | When model context ≥ 64K |

### L0/L1 Context Compaction System

The L0/L1 system solves the token budget problem across different model sizes:

```
Context Compaction Strategy:

  Source files (9 editable files)
  ┌─────────────────────────────────────────────┐
  │ SWARMAI.md + AGENT.md + SOUL.md + ...       │
  │ Total: ~7,500–26,000 tokens (full content)  │
  └──────────────┬──────────────────────────────┘
                 │
        ┌────────┴────────┐
        ▼                 ▼
  L1_SYSTEM_PROMPTS.md    L0_SYSTEM_PROMPTS.md
  (Full assembly)         (Compact assembly)
  ~20,000–26,000 tokens   ~3,000–6,000 tokens
  For 128K+ models        For 32K–64K models

Selection logic:
  if model_context_window >= 64K:
      use L1 (full) or load source files directly
  else:
      use L0 (compact)
```

**L1_SYSTEM_PROMPTS.md** — Full assembly:
- Concatenation of all 9 source files with section headers
- Auto-regenerated whenever any source file changes (mtime check)
- Serves as a pre-assembled cache to avoid reading 9 files on every session start
- For 128K+ context models (claude-opus, claude-sonnet) — plenty of room

**L0_SYSTEM_PROMPTS.md** — Compact assembly:
- AI-summarized version of all 9 source files
- Each file compressed to its essential directives (no examples, no prose)
- IDENTITY.md → 2 lines (name + vibe)
- SOUL.md → 5 bullet points (core personality traits)
- MEMORY.md → last 10 key decisions only
- KNOWLEDGE.md → top-level facts only (no deep reference)
- PROJECTS.md → active project names + status only
- STEERING.md → included verbatim (already concise)
- Auto-regenerated periodically or on explicit user request
- For 32K–64K context models — leaves room for conversation

**Generation triggers:**
- L1: Regenerated on session start if any source file mtime > L1 mtime (cheap, just concatenation)
- L0: Regenerated on explicit request (`/compact-context`) or when L1 exceeds a threshold (e.g., 20K tokens). Uses the agent itself to summarize — one-shot compression task.

### Assembly Order & Token Budget

```
System Prompt Assembly (v2):

  Priority  File                    Role                        Est. Tokens
  ────────  ──────────────────────  ──────────────────────────  ───────────
  0 (core)  SWARMAI.md              Core system prompt           1,000–3,000
  1 (high)  IDENTITY.md             Who I am                       200–500
  2         SOUL.md                 How I sound                    500–1,500
  3         AGENT.md                How I act                    1,000–3,000
  4         USER.md                 Who I'm helping                300–1,000
  5         STEERING.md             Session overrides              200–1,000
  6         MEMORY.md               What I remember              1,000–5,000
  7         KNOWLEDGE.md            What I know                  2,000–8,000
  8 (low)   PROJECTS.md             What's in flight             1,000–3,000
  ────────                                                       ───────────
  Total (L1 full)                                               7,200–26,000
  Total (L0 compact)                                            3,000–6,000

  + Safety principles (hardcoded)                                   ~200
  + Runtime metadata (date, cwd, model)                             ~100
  ────────────────────────────────────────────────────────────────────────
  Grand total (L1): 7,500–26,300 tokens
  Grand total (L0): 3,300–6,300 tokens
```

**Model selection logic:**
- 200K models (claude-opus-4-6, claude-sonnet-4-6): Use L1 or load source files directly — 25K system prompt leaves ~167K for conversation (~11 heavy agentic turns or ~25 light turns)
- 128K models: Use L1 with aggressive KNOWLEDGE.md and PROJECTS.md truncation — 25K leaves ~95K for conversation (~6 heavy turns)
- 64K models: Use L0 compact version (~5K) — leaves ~50K for conversation (~3 heavy turns)
- <32K models: Use L0 + skip KNOWLEDGE.md and PROJECTS.md entirely

**Token budget reality check (200K model, heavy agentic session):**

```
Context window: 200,000 tokens

Fixed overhead:
  System prompt (.context/ files)        25,000   (12.5%)
  SDK internal instructions               8,000    (4.0%)
  MCP tool definitions (5 servers)    10,000-20,000 (5-10%)
  ──────────────────────────────────────────────────────────
  Total overhead before conversation  43,000-53,000 (21-26%)
  Remaining for conversation         147,000-157,000

Per conversation turn (heavy agentic):
  User message                              500
  Assistant reasoning + tool_use calls    3,000
  Tool results (Read, Bash, Grep)        10,000
  Assistant response                      2,000
  ──────────────────────────────────────────────
  ~15,500 tokens per turn

Estimated turns before context fills:
  157,000 / 15,500 ≈ 10 heavy turns
  157,000 / 5,000  ≈ 31 light turns (text-only, no tools)

Conclusion: 25K system prompt costs ~1-2 turns vs a 10K prompt.
The bottleneck is tool results, not system prompt size.
```

Priority determines truncation order: when the token budget is exceeded, lower-priority files are truncated first (PROJECTS.md → KNOWLEDGE.md → MEMORY.md → ...). SWARMAI.md, IDENTITY.md, and SOUL.md are never truncated.

### How It Differs from Current Architecture

| Aspect | Before | After |
|--------|--------|-------|
| Location | Scattered across `.swarmai/`, workspace root, DB | Single `~/.swarm-ai/.context/` directory |
| Storage | Hybrid: DB + filesystem | Filesystem-only — editable, git-friendly, portable |
| Core prompt | DB `agents.system_prompt` | `.context/SWARMAI.md` (loaded at session start) |
| Discovery | Multiple loaders (SystemPromptBuilder, ContextAssembler, ContextManager, DB) | `ContextDirectoryLoader` + `ContextAssembler` (project) + `SystemPromptBuilder` (non-file) |
| User visibility | Hidden in nested directories, some in DB | Flat `.context/` directory, all files editable |
| Memory | Split across `Knowledge/Memory/*.md` (50 file cap) | Single `MEMORY.md` (curated) |
| Steering | Not implemented | `STEERING.md` for session-level overrides |
| Knowledge | `context-L0.md` / `context-L1.md` with tag-based filtering | `KNOWLEDGE.md` — single file, always loaded |
| Small model support | No compaction | L0/L1 system: compact for small models, full for large |
| Token budget | 10K for ContextAssembler, none for SystemPromptBuilder | Unified 25K budget with priority-based truncation |
| Portability | Not portable | Copy `.context/` → same agent personality |

### Implementation Approach

**New module: `context_directory_loader.py`**

```python
"""Centralized context directory loader.

Reads all *.md files from ~/.swarm-ai/.context/ and assembles them
into the system prompt with priority-based ordering, token budget
enforcement, and L0/L1 compaction support.
"""

CONTEXT_FILES = [
    ("SWARMAI.md",    0, "SwarmAI",          False),  # (filename, priority, section_name, truncatable)
    ("IDENTITY.md",   1, "Identity",         False),
    ("SOUL.md",       2, "Soul",             False),
    ("AGENT.md",      3, "Agent Directives", True),
    ("USER.md",       4, "User",             True),
    ("STEERING.md",   5, "Steering",         True),
    ("MEMORY.md",     6, "Memory",           True),
    ("KNOWLEDGE.md",  7, "Knowledge",        True),
    ("PROJECTS.md",   8, "Projects",         True),
]

class ContextDirectoryLoader:
    def __init__(self, context_dir: Path, token_budget: int = 25_000):
        self._dir = context_dir
        self._budget = token_budget

    def load_all(self, model_context_window: int = 200_000) -> str:
        """Read all context files, assemble with headers, enforce budget.

        Uses L0/L1 compaction based on model context window size:
        - >= 64K: Load source files directly (or use L1 cache)
        - < 64K: Use L0 compact version
        """
        if model_context_window < 64_000:
            return self._load_l0()

        # Check L1 cache freshness
        l1_path = self._dir / "L1_SYSTEM_PROMPTS.md"
        if l1_path.is_file() and self._is_l1_fresh(l1_path):
            return l1_path.read_text(encoding="utf-8")

        # Load source files, assemble, write L1 cache
        assembled = self._assemble_from_sources()
        self._write_l1(assembled)
        return assembled

    def _load_l0(self) -> str:
        """Load the compact L0 version."""
        l0_path = self._dir / "L0_SYSTEM_PROMPTS.md"
        if l0_path.is_file():
            return l0_path.read_text(encoding="utf-8")
        # Fallback: assemble from sources with aggressive truncation
        return self._assemble_from_sources(compact=True)

    def _assemble_from_sources(self, compact: bool = False) -> str:
        """Read all source files and assemble with headers."""
        sections = []
        for filename, priority, section_name, truncatable in CONTEXT_FILES:
            path = self._dir / filename
            if path.is_file():
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    sections.append((priority, section_name, content, truncatable))
        sections.sort(key=lambda s: s[0])
        parts = [f"## {name}\n{content}" for _, name, content, _ in sections]
        # ... progressive truncation logic based on self._budget ...
        return "\n\n".join(parts)
```

**Integration point: replace the scattered loaders**

In `_build_system_prompt()`, replace the current ContextAssembler + ContextManager + SystemPromptBuilder chain with:

```python
# Load centralized context files
context_loader = ContextDirectoryLoader(
    context_dir=get_app_data_dir() / ".context",
    token_budget=agent_config.get("context_token_budget", 25_000),
)
# Pass model context window for L0/L1 selection
model_context = self._get_model_context_window(model)
context_text = context_loader.load_all(model_context_window=model_context)

# Append to agent's base system prompt
if context_text:
    agent_config["system_prompt"] = (
        (agent_config.get("system_prompt", "") or "") + "\n\n" + context_text
    )
```

The existing `SystemPromptBuilder` still handles non-file sections (safety principles, datetime, runtime metadata, workspace cwd). The `ContextDirectoryLoader` handles all file-based context.

### No Migration

Clean break — no migration logic. The `~/.swarm-ai/.context/` directory is initialized from `backend/context/` defaults on first startup. Legacy sources (`.swarmai/`, `Knowledge/Memory/`, DB `agents.system_prompt`) are ignored. Users who want to preserve old customizations can manually copy files into `.context/`.

### Relationship to ContextAssembler (8-Layer)

The 8-layer `ContextAssembler` is NOT replaced — it serves a different purpose:
- `ContextDirectoryLoader` = **global context** (who I am, who you are, what I remember)
- `ContextAssembler` = **project-scoped context** (this project's instructions, semantic context, live work)

Both contribute to the final system prompt:

```
Final System Prompt =
    ContextDirectoryLoader output (9 files from ~/.swarm-ai/.context/)
  + ContextAssembler output (project-specific layers, only when project_id exists)
  + SystemPromptBuilder sections (identity, safety, datetime, runtime)
```

### Open Questions

1. Should `MEMORY.md` be auto-updated by the agent at end of each session, or only on explicit user request?
2. Should `PROJECTS.md` be auto-generated from the DB, or manually maintained?
3. Should `STEERING.md` support expiration dates (e.g., "valid until 2026-03-10")?
4. Should we support per-project overrides (e.g., `~/.swarm-ai/.context/projects/{id}/STEERING.md`)?
5. L0 generation: Should it use AI summarization (better quality, costs tokens) or rule-based compression (free, deterministic)?
6. Should L1 regeneration be synchronous (block session start) or async (use stale cache while regenerating)?

---

## TBD 13: Tool Result Optimization

### Problem

Tool results are the #1 token consumer in agentic sessions. Most of the content is noise — not meaningful to the user and not useful for the model's next reasoning step. This wastes tokens, reduces conversation depth, and makes the chat history harder to read.

### Examples of Wasteful Tool Results

```
┌─────────────────────────────────────────────────────────────────┐
│ Bash: ls -la                                                     │
│                                                                  │
│ Current output (~2K tokens for a medium directory):              │
│   drwxr-xr-x  15 gawan  staff    480 Mar  3 10:30 .             │
│   drwxr-xr-x   8 gawan  staff    256 Mar  1 09:15 ..            │
│   -rw-r--r--   1 gawan  staff   1234 Mar  3 10:30 main.py       │
│   -rw-r--r--   1 gawan  staff    567 Mar  2 14:22 config.py     │
│   drwxr-xr-x   5 gawan  staff    160 Mar  1 09:15 core/         │
│   ... (50 more lines)                                            │
│                                                                  │
│ What actually matters (~200 tokens):                             │
│   main.py, config.py, core/, tests/, schemas/                    │
│   (15 files, 5 directories)                                      │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ Read: backend/core/agent_manager.py (2278 lines)                 │
│                                                                  │
│ Current output (~15K tokens): entire file content                │
│                                                                  │
│ What the model actually needed: lines 812-910 (_build_options)   │
│ Useful output (~1.5K tokens): just that function                 │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ Grep: "swarm-workspaces" across codebase                         │
│                                                                  │
│ Current output (~5K tokens): 50 matches with 2-line context each │
│                                                                  │
│ What matters (~500 tokens): 3 production code matches            │
│ (the other 47 are in tests and old spec docs)                    │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ Bash: npm test -- --run                                          │
│                                                                  │
│ Current output (~8K tokens): full test runner output with        │
│   PASS/FAIL for every test file, timing, coverage table          │
│                                                                  │
│ What matters (~300 tokens):                                      │
│   "986 tests passing, 0 failed, 60 test files"                  │
│   or if failed: the specific failure message + stack trace       │
└─────────────────────────────────────────────────────────────────┘
```

### Token Waste Estimate

In a typical 10-turn agentic session:

```
Tool result breakdown (current, unoptimized):

  Tool              Calls   Avg tokens/call   Total tokens
  ────────────────  ──────  ────────────────   ────────────
  Read (full file)     5      3,000–10,000      25,000–50,000
  Bash (ls, find)      8        500–2,000        4,000–16,000
  Grep                 3      1,000–5,000        3,000–15,000
  Bash (test/build)    2      2,000–8,000        4,000–16,000
  Write/Edit           4        200–500          800–2,000
  ────────────────────────────────────────────────────────────
  Total tool results                            36,800–99,000

  With optimization (estimated 60-70% reduction):
  Total tool results                            11,000–30,000
  Savings per session                           25,000–69,000 tokens
```

That's 2-5 extra conversation turns recovered per session.

### Proposed Optimization Strategies

#### Strategy 1: PostToolUse Result Compression (Hook-based)

Add a `PostToolUse` hook that compresses tool results before they enter the conversation history. The Claude SDK supports PostToolUse hooks — we just don't use them yet (TBD 8).

```python
# PostToolUse hook: compress tool results
async def tool_result_compressor(
    tool_name: str,
    tool_input: dict,
    tool_result: str,
    context: Any,
) -> dict:
    """Compress tool results to reduce token consumption."""

    if tool_name == "Bash":
        command = tool_input.get("command", "")
        if command.startswith("ls"):
            # Strip permissions, owner, group, timestamps
            return {"result": compress_ls_output(tool_result)}
        if "test" in command or "pytest" in command:
            # Keep only summary + failures
            return {"result": compress_test_output(tool_result)}
        if "build" in command:
            # Keep only last 20 lines + errors
            return {"result": compress_build_output(tool_result)}

    if tool_name == "Read":
        # If result > 5K tokens, summarize with line count + key symbols
        if estimate_tokens(tool_result) > 5000:
            return {"result": compress_file_read(tool_result)}

    if tool_name == "Grep":
        # Cap at 10 most relevant matches
        return {"result": compress_grep_output(tool_result, max_matches=10)}

    return {}  # No compression
```

#### Strategy 2: Smart Read with AST Awareness

Instead of dumping entire files, the Read tool could return:
- For code files: function/class signatures + the specific section requested
- For markdown: headers + the specific section
- For config files: full content (usually small)

This is similar to how Kiro's `readCode` tool works — AST-based extraction for large files.

#### Strategy 3: Result Summarization for Chat Display

Tool results shown to the user in the chat UI don't need to be the same as what's in the conversation history. Two separate representations:

```
Conversation history (for the model):
  tool_result: "15 files found: main.py, config.py, core/, tests/, ..."

Chat UI (for the user):
  [Expandable] Bash: ls -la → 15 items
  └─ Click to expand full output
```

This is a frontend optimization — the model sees compressed results, the user can expand to see the full output if needed.

#### Strategy 4: Conversation History Compaction

After N turns, compress older tool results in the conversation history:
- Keep the last 3 turns fully intact
- For turns 4-N: replace tool_result content with a 1-line summary
- The model still knows what tools were called and roughly what they returned

```
Turn 1 (compressed): Read main.py → [2278 lines, Python, AgentManager class]
Turn 2 (compressed): Bash ls -la → [15 files in backend/core/]
Turn 3 (full): Read system_prompt.py → <full content>
Turn 4 (full): <current turn>
```

### Implementation Priority

| Strategy | Effort | Impact | Dependency |
|----------|--------|--------|------------|
| 1. PostToolUse compression | Medium | High — 60-70% token reduction | Requires PostToolUse hook support (TBD 8) |
| 2. Smart Read (AST) | High | High — biggest single-tool savings | Requires custom Read tool or SDK extension |
| 3. UI vs history split | Medium | Medium — better UX, same model savings as #1 | Frontend changes + backend result storage |
| 4. History compaction | High | Medium — helps long sessions | Requires conversation history manipulation |

### Relationship to L0/L1 Context Compaction

The L0/L1 system compacts the system prompt (static context). Tool result optimization compacts the conversation history (dynamic context). Together they address both halves of the token budget:

```
200K context window:
  ┌──────────────────────────────────────────────┐
  │ System prompt (L0/L1 compaction)    25K → 5K │ ← addressed by .context/ proposal
  │ SDK overhead                             8K  │
  │ MCP tool definitions                    15K  │
  │ Conversation history                         │
  │   └─ Tool results (compression)  100K → 30K │ ← addressed by this TBD
  │   └─ Messages                          40K  │
  │ Current response                         8K  │
  └──────────────────────────────────────────────┘
  Before optimization: ~196K (barely fits 10 turns)
  After optimization:  ~126K (fits 15-20 turns comfortably)
```
