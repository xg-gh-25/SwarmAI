# SwarmAI — System Flowcharts

Mermaid diagrams for the key flows in SwarmAI.

---

## 1. App Startup Flow

```mermaid
flowchart TD
    A[Tauri App Launch] --> B[Rust: Start Python sidecar]
    B --> C[Rust: Assign dynamic port via portpicker]
    C --> D[Python: FastAPI lifespan starts]
    D --> E{data.db exists?}

    E -->|YES| F[Fast Path]
    F --> F1[initialize_database - skip_schema=True]
    F1 --> F2[ensure_default_workspace]
    F2 --> F3[Cache workspace path]
    F3 --> G[channel_gateway.startup]

    E -->|NO| H[Full Init Path]
    H --> H1[Copy seed.db from bundled resources]
    H1 --> H2[initialize_database - DDL + migrations]
    H2 --> H3[run_full_initialization]
    H3 --> H4[Register agents, skills, MCPs]
    H4 --> G

    G --> I[Backend ready on dynamic port]
    I --> J[React frontend loads]
    J --> K[Frontend discovers port via Tauri IPC]
    K --> L[restoreFromFile - load open_tabs.json]
    L --> M{Tabs found?}
    M -->|YES| N[Hydrate tabs + set activeTabId]
    N --> O[Load active tab messages from API]
    M -->|NO| P[Show default tab with welcome message]
```

---

## 2. Multi-Tab Chat — Message Send Flow

```mermaid
sequenceDiagram
    participant User
    participant ChatPage
    participant TabMap as useUnifiedTabState
    participant StreamHook as useChatStreamingLifecycle
    participant API as FastAPI Backend
    participant AM as AgentManager
    participant SDK as ClaudeSDKClient
    participant Claude as Claude API / Bedrock

    User->>ChatPage: Type message + Send
    ChatPage->>TabMap: Append user message to active tab
    ChatPage->>StreamHook: setIsStreaming(true, tabId)
    ChatPage->>API: POST /api/chat/stream (SSE)

    API->>AM: run_conversation(session_id, message)

    alt New conversation (no session_id)
        AM->>SDK: Create new ClaudeSDKClient
        SDK-->>AM: init message with SDK session_id
        AM-->>API: yield session_start(sdk_session_id)
    else Resume (session_id exists)
        alt Active client in memory
            AM->>SDK: Reuse existing client
        else Backend restarted (no client)
            AM->>SDK: Create fresh client
            Note over AM: Map SDK session → app session
        end
        AM-->>API: yield session_start(app_session_id)
    end

    AM->>SDK: query(message)
    SDK->>Claude: API request

    loop SSE Streaming
        Claude-->>SDK: Response chunks
        SDK-->>AM: Parse messages
        AM-->>API: yield assistant / tool_use / tool_result events
        API-->>ChatPage: SSE events
        ChatPage->>StreamHook: Update messages (functional updater)
        StreamHook->>TabMap: Write to tabMapRef
    end

    AM-->>API: yield result event
    API-->>ChatPage: result event
    ChatPage->>StreamHook: setIsStreaming(false, tabId)
    ChatPage->>TabMap: updateTabStatus(tabId, 'idle')
```

---

## 3. Tab Switching Flow

```mermaid
flowchart TD
    A[User clicks Tab B] --> B[handleTabSelect]
    B --> C[Save Tab A state to tabMapRef]
    C --> D[selectTab - set activeTabId to Tab B]
    D --> E{Tab B in tabMapRef?}

    E -->|YES| F{Has sessionId + messages?}
    F -->|YES - cached| G[Restore from map: setMessages, setSessionId]
    F -->|sessionId but empty messages| H[loadSessionMessages from API]
    H --> H1[Sync messages back into tabMapRef]
    H1 --> G
    F -->|No sessionId| I[Show welcome message]

    E -->|NO| J{Tab has sessionId?}
    J -->|YES| K[Fetch from API + initTabState]
    J -->|NO| L[Create fresh tab state]
```

---

## 4. Tab Persistence Flow

```mermaid
flowchart LR
    subgraph "Runtime (every 500ms debounced)"
        A[Tab mutation] --> B[renderCounter bumps]
        B --> C[Save effect fires]
        C --> D[Serialize tabs + activeTabId]
        D --> E[PUT /api/settings/open-tabs]
        E --> F[Write ~/.swarm-ai/open_tabs.json]
    end

    subgraph "Startup"
        G[App launches] --> H[restoreFromFile]
        H --> I[GET /api/settings/open-tabs]
        I --> J[Read open_tabs.json]
        J --> K[Hydrate tabs with messages=empty]
        K --> L[Set activeTabId from file]
        L --> M[Lazy-load active tab messages]
    end
```

---

## 5. Session ID Mapping — Resume After Restart

```mermaid
sequenceDiagram
    participant Tab as Chat Tab
    participant FE as Frontend
    participant BE as Backend
    participant SDK as ClaudeSDKClient

    Note over Tab: App Session ID = "abc-123"

    Tab->>FE: Send message (sessionId="abc-123")
    FE->>BE: POST /api/chat/stream (session_id="abc-123")

    BE->>BE: Check _active_sessions["abc-123"]

    alt Client found (no restart)
        BE->>SDK: Reuse existing client
        BE-->>FE: session_start(sessionId="abc-123")
    else Client missing (backend restarted)
        BE->>SDK: Create fresh client
        SDK-->>BE: init(sdk_session_id="xyz-789")
        Note over BE: Map "xyz-789" → "abc-123"
        BE->>BE: Save messages under "abc-123"
        BE->>BE: Key _active_sessions by "abc-123"
        BE-->>FE: session_start(sessionId="abc-123")
    end

    Note over Tab: Tab always sees "abc-123"
```

---

## 6. Tool Use & Permission Flow

```mermaid
sequenceDiagram
    participant User
    participant FE as Frontend
    participant BE as Backend
    participant SDK as ClaudeSDKClient
    participant Tool as Tool (Bash/Read/Write/etc.)

    SDK->>BE: tool_use event (toolName, input)

    alt PreToolUse hook blocks
        BE-->>FE: error event
    else PreToolUse hook allows
        BE-->>FE: tool_use event (display in UI)
    end

    alt Permission required (cmd_permission_request)
        BE-->>FE: cmd_permission_request event
        FE->>User: Show permission modal
        User->>FE: Approve / Deny
        FE->>BE: POST /api/chat/permission
        BE->>SDK: set_permission_decision()
    end

    SDK->>Tool: Execute tool
    Tool-->>SDK: Result
    SDK-->>BE: tool_result event
    BE-->>FE: tool_result event (display in UI)
```

---

## 7. SwarmWS Workspace Integrity Flow

```mermaid
flowchart TD
    A[ensure_default_workspace] --> B{Workspace record in DB?}
    B -->|NO| C[Create workspace record]
    B -->|YES| D[Load workspace record]
    C --> D
    D --> E[Expand file_path placeholder]
    E --> F[verify_integrity]
    F --> G{All system folders exist?}
    G -->|YES| H[Done - workspace healthy]
    G -->|NO| I[Create missing folders]
    I --> J[Write context-L0.md / context-L1.md]
    J --> K[Write system-prompts.md]
    K --> H


---

## 8. Skill Execution Flow

```mermaid
sequenceDiagram
    participant Claude as Claude Code CLI
    participant Hook as PreToolUse Hook
    participant Skill as Skill Tool (MCP)

    Claude->>Hook: PreToolUse(Skill, skill_id)
    Hook->>Hook: Check skill_id in agent's allowed skills

    alt Skill not authorized
        Hook-->>Claude: Block tool use
    else Skill authorized
        Hook-->>Claude: Allow
        Claude->>Skill: Invoke skill with task input
        Skill-->>Claude: Return instructions + context
        Claude->>Claude: Follow skill instructions to complete task
    end
```

---

## 9. Build Pipeline Flow

```mermaid
flowchart LR
    A[npm run build:all] --> B[npm run prebuild]
    B --> C[python generate_seed_db.py]
    C --> D[seed.db → desktop/resources/]

    A --> E[bash build-backend.sh]
    E --> F[PyInstaller packages backend]
    F --> G[Binary → src-tauri/binaries/]

    A --> H[npm run tauri:build]
    H --> I[Vite builds React frontend]
    H --> J[Rust compiles Tauri shell]
    J --> K{Platform}
    K -->|macOS| L[.dmg / .app]
    K -->|Windows| M[.msi / .exe]
    K -->|Linux| N[.deb / .AppImage]
```

---

## Diagram Legend

| Symbol | Meaning |
|--------|---------|
| Solid arrow | Synchronous call or data flow |
| Dashed arrow | Async response or SSE event |
| Diamond | Decision point |
| Rectangle | Process or component |
| Cylinder | Database or storage |
