# Channel & Gateway System Design for owork

## Context

owork currently only supports interaction via its desktop/web UI (HTTP/SSE). Users need to expose AI agents to external messaging platforms so agents can serve broader audiences. This design adds a **multi-channel gateway** starting with **Feishu (飞书)**, then expanding to Slack, Discord, and web widgets.

The design is inspired by [OpenClaw](https://github.com/openclaw/openclaw)'s plugin-based multi-channel architecture, adapted for owork's desktop-first, single-process architecture.

---

## Architecture Overview

```
External Platforms                  owork Backend (in-process)
┌──────────┐                       ┌─────────────────────────────────────┐
│  Feishu  │──WebSocket长连接──┐   │  ChannelGateway                     │
│  Bot     │                   │   │  ├── AdapterRegistry                │
└──────────┘                   │   │  ├── SessionRouter                  │
┌──────────┐                   ├──▶│  ├── AccessControl                  │
│  Slack   │──Socket Mode──────┤   │  └── RateLimiter                    │
│  Bot     │                   │   │         │                           │
└──────────┘                   │   │         ▼                           │
┌──────────┐                   │   │  agent_manager.run_conversation()   │
│ Discord  │──Gateway WS───────┤   │         │                           │
│  Bot     │                   │   │         ▼                           │
└──────────┘                   │   │  Claude Agent SDK → Claude API      │
┌──────────┐                   │   └─────────────────────────────────────┘
│Web Widget│──HTTP/SSE─────────┘
│(embedded)│
└──────────┘
┌──────────┐
│ Desktop  │──HTTP/SSE────────────▶  (existing path, 100% unchanged)
│   App    │
└──────────┘
```

**Core Principle**: Channels are **adapters** that translate between external message formats and owork's internal `ChatRequest`. The gateway orchestrates lifecycle, routing, and access control. `agent_manager.run_conversation()` remains the **single execution path** — channels feed into it, not around it.

**Key Design Choices:**
- **1:1 agent binding**: Each channel connects to exactly one agent (simple to configure)
- **Outbound connections only**: Feishu WebSocket长连接, Slack Socket Mode, Discord Gateway WS — no public URL needed for desktop app
- **In-process gateway**: Runs as asyncio tasks within FastAPI, not a separate service
- **Optional dependencies**: `lark-oapi`, `slack_bolt`, `discord.py` only loaded when their adapter is used
- **Existing sessions untouched**: `channel_sessions` is a separate mapping table; desktop chat path unchanged

---

## Phase 1: Foundation (Database + Core Abstractions + CRUD API)

### 1.1 New Database Tables

Add to `backend/database/sqlite.py` — `SQLiteDatabase.SCHEMA`:

```sql
-- Channels table
CREATE TABLE IF NOT EXISTS channels (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    channel_type TEXT NOT NULL,          -- 'feishu', 'slack', 'discord', 'web_widget'
    agent_id TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',   -- JSON: channel-specific config
    status TEXT DEFAULT 'inactive',      -- 'active' | 'inactive' | 'error'
    error_message TEXT,
    access_mode TEXT DEFAULT 'allowlist',-- 'open' | 'allowlist' | 'api_key'
    allowed_senders TEXT DEFAULT '[]',
    blocked_senders TEXT DEFAULT '[]',
    api_keys TEXT DEFAULT '[]',
    rate_limit_per_minute INTEGER DEFAULT 10,
    enable_skills INTEGER DEFAULT 0,
    enable_mcp INTEGER DEFAULT 0,
    user_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_channels_agent_id ON channels(agent_id);
CREATE INDEX IF NOT EXISTS idx_channels_status ON channels(status);

-- Channel sessions (maps external conversations → internal sessions)
CREATE TABLE IF NOT EXISTS channel_sessions (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    external_chat_id TEXT NOT NULL,
    external_sender_id TEXT,
    external_thread_id TEXT,
    session_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    sender_display_name TEXT,
    last_message_at TEXT,
    message_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    UNIQUE(channel_id, external_chat_id, external_thread_id)
);
CREATE INDEX IF NOT EXISTS idx_channel_sessions_lookup
    ON channel_sessions(channel_id, external_chat_id, external_thread_id);

-- Channel messages (audit log)
CREATE TABLE IF NOT EXISTS channel_messages (
    id TEXT PRIMARY KEY,
    channel_session_id TEXT NOT NULL,
    direction TEXT NOT NULL,             -- 'inbound' | 'outbound'
    external_message_id TEXT,
    content TEXT NOT NULL,
    content_type TEXT DEFAULT 'text',
    metadata TEXT DEFAULT '{}',
    status TEXT DEFAULT 'sent',
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (channel_session_id) REFERENCES channel_sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_channel_messages_session ON channel_messages(channel_session_id);
```

**Config JSON schemas by channel_type:**
- **feishu**: `{"app_id": "cli_xxx", "app_secret": "xxx"}`
- **slack**: `{"bot_token": "xoxb-xxx", "app_token": "xapp-xxx"}`
- **discord**: `{"bot_token": "xxx", "guild_id": "xxx"}`
- **web_widget**: `{"allowed_origins": ["https://..."], "widget_theme": "light"}`

### 1.2 Backend Module Structure

**New files:**
```
backend/channels/
├── __init__.py              # Package exports
├── base.py                  # ChannelAdapter ABC, InboundMessage, OutboundMessage
├── gateway.py               # ChannelGateway: lifecycle + routing + access control
├── registry.py              # Adapter type → class registry
└── adapters/
    ├── __init__.py
    ├── feishu.py            # Phase 2
    ├── slack.py             # Phase 3
    ├── discord.py           # Phase 4
    └── web_widget.py        # Phase 5

backend/routers/channels.py  # Channel CRUD + lifecycle + widget endpoints
backend/schemas/channel.py   # Pydantic models
```

**Modified files:**

| File | Change |
|------|--------|
| `backend/database/sqlite.py` | Add 3 tables to SCHEMA, add `SQLiteChannelSessionsTable` class, add `_channels`/`_channel_sessions`/`_channel_messages` instances and properties |
| `backend/database/base.py` | Add abstract properties for new tables |
| `backend/routers/__init__.py` | Add `channels_router` import/export |
| `backend/main.py` | Mount channels router; wire gateway `startup()`/`shutdown()` into lifespan |
| `desktop/src/types/index.ts` | Add Channel, ChannelSession types |
| `desktop/src/App.tsx` | Add `/channels` route |
| `desktop/src/i18n/locales/en.json` | Add `channels` section |
| `desktop/src/i18n/locales/zh.json` | Add `channels` section |
| Sidebar layout component | Add "Channels" nav item |

**New frontend files:**
- `desktop/src/services/channels.ts` — CRUD client (follow `agents.ts` pattern with `toSnakeCase`/`toCamelCase`)
- `desktop/src/pages/ChannelsPage.tsx` — Channel management UI (follow `PluginsPage.tsx` layout)

### 1.3 Channel Adapter Interface

`backend/channels/base.py`:

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Callable, Any
from abc import ABC, abstractmethod


@dataclass
class InboundMessage:
    """Normalized message from an external channel into owork."""
    channel_id: str
    external_chat_id: str          # Feishu chat_id / Slack channel / Discord channel
    external_sender_id: str        # Feishu open_id / Slack user / Discord user
    external_thread_id: str | None = None   # For thread mapping
    external_message_id: str | None = None  # Platform message ID (for replies)
    text: str = ""
    sender_display_name: str | None = None
    attachments: list[dict] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutboundMessage:
    """Message from owork to be sent to an external channel."""
    channel_id: str
    external_chat_id: str
    external_thread_id: str | None = None
    reply_to_message_id: str | None = None  # For threaded replies
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ChannelAdapter(ABC):
    """Base class for channel adapters.

    Each adapter handles the translation between an external platform's
    message format and owork's internal InboundMessage/OutboundMessage.

    Lifecycle:
    1. __init__(channel_id, config, on_message) - created by gateway
    2. start() - begin listening for messages (long-running)
    3. send_message(outbound) - send a response back to the platform
    4. stop() - gracefully shut down
    """

    def __init__(self, channel_id: str, config: dict, on_message: Callable):
        self.channel_id = channel_id
        self.config = config
        self._on_message = on_message

    @abstractmethod
    async def start(self) -> None:
        """Start listening for messages. Called by gateway."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop listening and clean up resources."""
        ...

    @abstractmethod
    async def send_message(self, message: OutboundMessage) -> str | None:
        """Send a message to the external platform. Returns external message ID."""
        ...

    @abstractmethod
    async def validate_config(self) -> tuple[bool, str | None]:
        """Validate channel configuration. Returns (is_valid, error_message)."""
        ...

    @property
    @abstractmethod
    def channel_type(self) -> str:
        """The type identifier of this channel adapter."""
        ...
```

### 1.4 Gateway Core

`backend/channels/gateway.py` — Central orchestrator:

```python
class ChannelGateway:
    """Manages channel adapter lifecycle and routes messages.

    Responsibilities:
    1. Start/stop channel adapters based on DB configuration
    2. Route inbound messages to the correct agent via AgentManager
    3. Map external conversations to internal sessions
    4. Enforce access control (allowlists, rate limits)
    5. Deliver agent responses back through the originating channel
    """

    _adapters: dict[str, ChannelAdapter]   # channel_id → adapter instance
    _tasks: dict[str, asyncio.Task]        # channel_id → background task

    async def start_channel(channel_id: str) -> None
    async def stop_channel(channel_id: str) -> None

    async def handle_inbound_message(msg: InboundMessage) -> None:
        # 1. Load channel config, check status == 'active'
        # 2. Access control (allowlist/blocklist check)
        # 3. Rate limit check
        # 4. _resolve_session(): find or create internal session
        # 5. agent_manager.run_conversation() — consume full async generator
        # 6. Accumulate 'assistant' text from stream events
        # 7. Build OutboundMessage, call adapter.send_message()
        # 8. Log to channel_messages table

    async def _resolve_session(
        channel_id, agent_id, external_chat_id,
        external_sender_id, external_thread_id, sender_display_name
    ) -> str:
        # Look up channel_sessions by (channel_id, external_chat_id, external_thread_id)
        # If found → return existing session_id
        # If not → create new session via session_manager.store_session()
        #        → create channel_sessions mapping
        #        → return new session_id

    async def startup() -> None     # Auto-start active channels on boot
    async def shutdown() -> None    # Stop all channels gracefully
```

**Message flow (key integration point):**
```
InboundMessage
  → gateway.handle_inbound_message()
    → _resolve_session() → session_id
    → agent_manager.run_conversation(agent_id, text, session_id, enable_skills, enable_mcp)
      → async for msg in stream:
          accumulate assistant text from msg['content'] blocks
    → OutboundMessage(text=accumulated_response)
    → adapter.send_message(outbound)
```

**AskUserQuestion handling:** Gateway sends question text back to channel. User's next message in the same thread is treated as the answer → calls `agent_manager.continue_with_answer()`.

**Permission requests:** Auto-deny for channel messages (no good UX for approval dialogs over chat). UI warns when linking an agent with `enable_human_approval: true`.

### 1.5 API Endpoints

`backend/routers/channels.py`:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/channels` | List all channels |
| GET | `/api/channels/{id}` | Get channel details |
| POST | `/api/channels` | Create channel |
| PUT | `/api/channels/{id}` | Update channel config |
| DELETE | `/api/channels/{id}` | Delete (stops if active) |
| GET | `/api/channels/types` | List supported channel types |
| POST | `/api/channels/{id}/start` | Start channel adapter |
| POST | `/api/channels/{id}/stop` | Stop channel adapter |
| POST | `/api/channels/{id}/restart` | Restart after config change |
| GET | `/api/channels/{id}/status` | Runtime status |
| POST | `/api/channels/{id}/test` | Validate config without starting |
| GET | `/api/channels/{id}/sessions` | List channel sessions |

### 1.6 Pydantic Schemas

`backend/schemas/channel.py`:

```python
from pydantic import BaseModel, Field
from typing import Literal, Optional


class ChannelCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    channel_type: Literal["feishu", "slack", "discord", "web_widget"]
    agent_id: str
    config: dict = Field(default_factory=dict)
    access_mode: Literal["open", "allowlist", "api_key"] = "allowlist"
    allowed_senders: list[str] = Field(default_factory=list)
    rate_limit_per_minute: int = Field(default=10, ge=1, le=100)
    enable_skills: bool = False
    enable_mcp: bool = False


class ChannelUpdateRequest(BaseModel):
    name: str | None = None
    config: dict | None = None
    agent_id: str | None = None
    access_mode: Literal["open", "allowlist", "api_key"] | None = None
    allowed_senders: list[str] | None = None
    blocked_senders: list[str] | None = None
    rate_limit_per_minute: int | None = None
    enable_skills: bool | None = None
    enable_mcp: bool | None = None


class ChannelResponse(BaseModel):
    id: str
    name: str
    channel_type: str
    agent_id: str
    agent_name: str | None = None
    config: dict
    status: str
    error_message: str | None = None
    access_mode: str
    allowed_senders: list[str]
    blocked_senders: list[str]
    rate_limit_per_minute: int
    enable_skills: bool
    enable_mcp: bool
    created_at: str
    updated_at: str


class ChannelStatusResponse(BaseModel):
    channel_id: str
    status: str
    uptime_seconds: float | None = None
    messages_processed: int = 0
    active_sessions: int = 0
    error_message: str | None = None


class ChannelSessionResponse(BaseModel):
    id: str
    channel_id: str
    external_chat_id: str
    external_sender_id: str | None
    external_thread_id: str | None
    session_id: str
    sender_display_name: str | None
    message_count: int
    last_message_at: str | None
    created_at: str
```

### 1.7 Frontend Types

`desktop/src/types/index.ts` — add:

```typescript
// ============== Channel Types ==============

export type ChannelType = 'feishu' | 'slack' | 'discord' | 'web_widget';
export type ChannelStatus = 'active' | 'inactive' | 'error' | 'starting';
export type ChannelAccessMode = 'open' | 'allowlist' | 'api_key';

export interface Channel {
  id: string;
  name: string;
  channelType: ChannelType;
  agentId: string;
  agentName?: string;
  config: Record<string, unknown>;
  status: ChannelStatus;
  errorMessage?: string;
  accessMode: ChannelAccessMode;
  allowedSenders: string[];
  blockedSenders: string[];
  rateLimitPerMinute: number;
  enableSkills: boolean;
  enableMcp: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface ChannelCreateRequest {
  name: string;
  channelType: ChannelType;
  agentId: string;
  config?: Record<string, unknown>;
  accessMode?: ChannelAccessMode;
  allowedSenders?: string[];
  rateLimitPerMinute?: number;
  enableSkills?: boolean;
  enableMcp?: boolean;
}

export interface ChannelStatusInfo {
  channelId: string;
  status: ChannelStatus;
  uptimeSeconds?: number;
  messagesProcessed: number;
  activeSessions: number;
  errorMessage?: string;
}

export interface ChannelSession {
  id: string;
  channelId: string;
  externalChatId: string;
  externalSenderId?: string;
  externalThreadId?: string;
  sessionId: string;
  senderDisplayName?: string;
  messageCount: number;
  lastMessageAt?: string;
  createdAt: string;
}
```

---

## Phase 2: Feishu Channel Adapter (First Working Integration)

### 2.1 Feishu API Summary

- **SDK**: `lark-oapi` (official, latest v1.5.3) — `pip install lark-oapi`
- **Connection**: **WebSocket长连接** via `lark.ws.Client(app_id, app_secret, event_handler)` — no public URL needed
- **Auth**: App ID + App Secret (from Feishu Developer Console)
- **Events**: `im.message.receive_v1` → `P2ImMessageReceiveV1` handler
- **Send**: `im.v1.message.create` (send to chat) / `im.v1.message.reply` (reply to specific message)
- **Message types**: text, rich text (post), image, file, interactive cards
- **Constraint**: Must process within 3 seconds of receiving (offload to background task)
- **Lark vs Feishu**: Same SDK, different API base URLs (auto-detected)

### 2.2 Implementation

`backend/channels/adapters/feishu.py`:

```python
import lark_oapi as lark
from lark_oapi.api.im.v1 import *


class FeishuChannelAdapter(ChannelAdapter):
    """Feishu adapter using WebSocket长连接 (no public URL needed).

    Required config:
        app_id: Feishu app ID (cli_xxx)
        app_secret: Feishu app secret
    """
    channel_type = "feishu"

    async def start(self):
        # 1. Build EventDispatcherHandler:
        #    handler = lark.EventDispatcherHandler.builder("", "")
        #        .register_p2_im_message_receive_v1(self._handle_message)
        #        .build()
        #
        # 2. Create lark API client for sending messages:
        #    self._client = lark.Client.builder()
        #        .app_id(config["app_id"])
        #        .app_secret(config["app_secret"])
        #        .build()
        #
        # 3. Create WebSocket client:
        #    self._ws = lark.ws.Client(app_id, app_secret, event_handler=handler)
        #
        # 4. Start in daemon thread (lark SDK ws.Client.start() is blocking):
        #    threading.Thread(target=self._ws.start, daemon=True).start()

    def _handle_message(self, data: lark.im.v1.P2ImMessageReceiveV1):
        # Extract from data:
        #   chat_id = data.event.message.chat_id
        #   open_id = data.event.sender.sender_id.open_id
        #   message_id = data.event.message.message_id
        #   content = json.loads(data.event.message.content)  # {"text": "..."}
        #   text = content.get("text", "")
        #
        # Build InboundMessage and bridge to asyncio:
        #   msg = InboundMessage(
        #       channel_id=self.channel_id,
        #       external_chat_id=chat_id,
        #       external_sender_id=open_id,
        #       external_message_id=message_id,
        #       text=text,
        #   )
        #   loop.call_soon_threadsafe(asyncio.ensure_future, self._on_message(msg))

    async def send_message(self, message: OutboundMessage) -> str:
        # Option A: Reply to specific message
        #   request = ReplyMessageRequest.builder()
        #       .message_id(message.reply_to_message_id)
        #       .request_body(ReplyMessageRequestBody.builder()
        #           .msg_type("text")
        #           .content(json.dumps({"text": message.text}))
        #           .build())
        #       .build()
        #   response = self._client.im.v1.message.reply(request)
        #
        # Option B: Send to chat
        #   request = CreateMessageRequest.builder()
        #       .receive_id_type("chat_id")
        #       .request_body(CreateMessageRequestBody.builder()
        #           .receive_id(message.external_chat_id)
        #           .msg_type("text")
        #           .content(json.dumps({"text": message.text}))
        #           .build())
        #       .build()
        #   response = self._client.im.v1.message.create(request)

    async def stop(self):
        # Stop the WebSocket client thread

    async def validate_config(self):
        # Try to get tenant_access_token with app_id + app_secret
        # Return (True, None) if successful, (False, error_msg) if not
```

**Threading note**: `lark.ws.Client.start()` is blocking, so run in a daemon thread. Message callbacks fire in that thread and need to bridge back to asyncio via `loop.call_soon_threadsafe()`.

### 2.3 Feishu Message Flow

```
Feishu User sends message in group/DM
  │
  ▼
Feishu Server → WebSocket长连接 → lark.ws.Client
  │
  ▼
P2ImMessageReceiveV1 handler fires
  ├── Extract: event.message.chat_id, event.sender.sender_id.open_id
  ├── Extract: event.message.message_id, event.message.content (JSON)
  ├── Parse content: {"text": "user message"} → text
  ├── Build InboundMessage(external_chat_id=chat_id, external_sender_id=open_id, ...)
  └── Call gateway.handle_inbound_message(msg) via asyncio bridge
       │
       ▼
  ChannelGateway.handle_inbound_message()
  ├── Access control check
  ├── Rate limit check
  ├── _resolve_session() → find or create internal session
  ├── agent_manager.run_conversation(agent_id, text, session_id, ...)
  │   └── Consume async generator, accumulate assistant text
  ├── Build OutboundMessage(text=response, reply_to_message_id=message_id)
  ├── adapter.send_message(outbound)
  │   └── im.v1.message.reply(message_id, {"text": response_text})
  └── Log to channel_messages table
```

### 2.4 Feishu Developer Console Setup Guide

1. Go to https://open.feishu.cn/app and create a new app
2. Enable "Bot" (机器人) capability
3. Go to **Events & Callbacks** (事件与回调):
   - Set subscription mode to **长连接** (Long Connection / Persistent Connection)
   - Add event: `im.message.receive_v1` (接收消息)
4. Go to **Permissions** (权限管理):
   - Request `im:message` (获取与发送单聊、群组消息)
   - Request `im:message:send_as_bot` (以应用身份发送消息)
5. Get **App ID** and **App Secret** from credentials page
6. Publish/approve the app (requires tenant admin approval)

### 2.5 Dependencies

Add to `backend/pyproject.toml` as optional:
```toml
[project.optional-dependencies]
feishu = ["lark-oapi>=1.5.0"]
```

---

## Phase 3: Slack Channel

- SDK: `slack_bolt` + `slack_sdk`
- Connection: **Socket Mode** (outbound WebSocket, no public URL needed)
- Config: `bot_token` (xoxb-) + `app_token` (xapp-)
- Events: `message`, `app_mention`
- Send: `client.chat_postMessage(channel, text, thread_ts)`
- Message limit: ~3000 chars (split if longer)
- Markdown → Slack mrkdwn conversion needed

## Phase 4: Discord Channel

- SDK: `discord.py`
- Connection: **Gateway WebSocket** (outbound, no public URL needed)
- Config: `bot_token`, optional `guild_id` for filtering
- Events: `on_message`
- Send: `channel.send(text)` or `message.reply(text)`
- Message limit: 2000 chars (split if longer)

## Phase 5: Web Widget

- No external SDK needed
- Widget-specific HTTP endpoints: POST message, GET SSE stream, GET config
- Embeddable JavaScript bundle for third-party websites
- Auth via API key header or CORS origin validation

---

## Files to Modify (Phase 1+2 Implementation Order)

### Backend — New Files
1. `backend/schemas/channel.py` — Pydantic models
2. `backend/channels/__init__.py` — Package exports
3. `backend/channels/base.py` — ABC + data classes
4. `backend/channels/registry.py` — Adapter registry
5. `backend/channels/gateway.py` — ChannelGateway
6. `backend/channels/adapters/__init__.py`
7. `backend/channels/adapters/feishu.py` — Feishu adapter
8. `backend/routers/channels.py` — API endpoints

### Backend — Modified Files
9. `backend/database/sqlite.py` — Add 3 tables to SCHEMA, `SQLiteChannelSessionsTable`, instances + properties
10. `backend/database/base.py` — Add abstract properties
11. `backend/routers/__init__.py` — Add `channels_router`
12. `backend/main.py` — Mount router, wire gateway into lifespan

### Frontend — New Files
13. `desktop/src/services/channels.ts` — API client
14. `desktop/src/pages/ChannelsPage.tsx` — Management UI

### Frontend — Modified Files
15. `desktop/src/types/index.ts` — Add Channel types
16. `desktop/src/App.tsx` — Add `/channels` route
17. `desktop/src/i18n/locales/en.json` — Add channels i18n keys
18. `desktop/src/i18n/locales/zh.json` — Add channels i18n keys
19. Sidebar layout — Add "Channels" nav item (icon: `hub`)

### Existing Patterns to Reuse
- `backend/database/sqlite.py:SQLiteTable` — base table class for channels/channel_sessions
- `backend/database/sqlite.py:SQLiteMessagesTable` — pattern for `SQLiteChannelSessionsTable` (custom query methods)
- `backend/routers/plugins.py` — CRUD endpoint pattern for channels router
- `backend/core/agent_manager.py:run_conversation()` (line ~992) — integration point where gateway feeds messages
- `backend/core/session_manager.py:store_session()` — for creating internal sessions from channel conversations
- `desktop/src/services/agents.ts:toSnakeCase()/toCamelCase()` — case conversion pattern for channels service

---

## Verification Plan

1. **Backend unit tests:**
   - `pytest tests/test_channels.py` — Adapter interface compliance, session routing, access control
   - Test gateway message handling with mock adapter

2. **Feishu integration test:**
   - Create test Feishu app with bot capability
   - Configure channel via API: `POST /api/channels` with Feishu app_id/secret
   - Start channel: `POST /api/channels/{id}/start`
   - Send message from Feishu → verify agent responds in Feishu
   - Verify session created in DB: `GET /api/channels/{id}/sessions`

3. **Frontend test:**
   - `cd desktop && npm run test` — existing tests still pass
   - Manual: Navigate to Channels page, create Feishu channel, verify start/stop

4. **Regression:**
   - `cd backend && pytest` — all existing tests pass
   - Manual: Desktop chat with existing agent still works unchanged

5. **E2E flow:**
   - Create agent → Create Feishu channel linked to agent → Start channel → Send message from Feishu → Agent responds → View session in UI
