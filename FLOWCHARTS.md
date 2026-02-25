# AI Agent Platform - 流程图文档

本文档包含系统主要流程的 Mermaid 图表。

## 1. 整体系统架构流程

```mermaid
graph TB
    subgraph "Frontend (React + Vite)"
        A[用户界面] --> B[React Components]
        B --> C[TanStack Query]
        C --> D[Axios API Client]
        D --> E[SSE Stream Handler]
    end

    subgraph "Backend (FastAPI)"
        F[API Routes] --> G[Agent Manager]
        G --> H[Session Manager]
        G --> I[Claude SDK Client]
        I --> J[Claude Code CLI]
    end

    subgraph "Data Layer"
        K[Mock Database]
        K --> L[(Agents)]
        K --> M[(Skills)]
        K --> N[(MCP Servers)]
    end

    subgraph "External Services"
        O[Anthropic API]
        P[MCP Servers]
        Q[Skills Repository]
    end

    E -->|HTTP/SSE| F
    G --> K
    I --> O
    J --> P
    J --> Q

    style A fill:#2b6cee,color:#fff
    style J fill:#ff6b6b,color:#fff
    style O fill:#4ecdc4,color:#fff
```

## 2. 聊天会话流程（核心流程）

```mermaid
sequenceDiagram
    participant User as 用户
    participant Frontend as React Frontend
    participant API as FastAPI Backend
    participant AM as Agent Manager
    participant SM as Session Manager
    participant SDK as Claude SDK Client
    participant CLI as Claude Code CLI
    participant Claude as Anthropic API

    User->>Frontend: 输入消息
    Frontend->>API: POST /api/chat/stream
    Note over API: SSE Connection

    API->>AM: run_conversation()
    AM->>SM: get_session() 或 create_session()
    SM-->>AM: session_id

    AM->>AM: _build_options()
    Note over AM: 加载 Agent 配置<br/>- 系统提示词<br/>- 允许的工具<br/>- MCP 配置<br/>- 技能配置

    AM->>SDK: 创建 ClaudeSDKClient
    SDK->>CLI: 启动 Claude Code CLI 进程

    AM->>SDK: query(message)
    SDK->>CLI: 发送用户消息
    CLI->>Claude: API 请求

    loop 流式响应
        Claude-->>CLI: 流式响应块
        CLI-->>SDK: 解析消息
        SDK-->>AM: 异步迭代器 yield
        AM-->>API: SSE event
        API-->>Frontend: data: {...}
        Frontend-->>User: 更新 UI

        alt 工具调用
            CLI->>CLI: 执行工具 (Bash/Read/Write/etc)
            Note over CLI: 可能触发 PreToolUse Hook
            CLI->>Claude: 工具结果
            Note over CLI: 可能触发 PostToolUse Hook
        end
    end

    SDK-->>AM: 会话结束
    AM->>SM: 更新 session
    AM-->>API: result event
    API-->>Frontend: 会话统计数据
    Frontend-->>User: 显示完成状态
```

## 3. Agent 配置与初始化流程

```mermaid
flowchart TD
    A[开始创建 Agent] --> B[用户输入配置]
    B --> C{验证配置}
    C -->|无效| D[返回错误]
    C -->|有效| E[保存到数据库]

    E --> F[生成 Agent ID]
    F --> G[存储配置项]

    G --> H[基础配置]
    G --> I[工具配置]
    G --> J[Skills 配置]
    G --> K[MCP 配置]

    H --> L[model<br/>permission_mode<br/>max_turns<br/>system_prompt]

    I --> M{启用的工具}
    M --> N[Bash Tool]
    M --> O[File Tools<br/>Read/Write/Edit]
    M --> P[Web Tools<br/>WebFetch/Grep]
    M --> Q[Skill Tool]

    J --> R{Skill IDs}
    R --> S[从数据库加载<br/>Skill 配置]
    S --> T[设置 setting_sources]

    K --> U{MCP IDs}
    U --> V[从数据库加载<br/>MCP 配置]
    V --> W[转换为 SDK 格式]
    W --> X[stdio/sse/http]

    L --> Y[构建 ClaudeAgentOptions]
    N --> Y
    O --> Y
    P --> Y
    Q --> Y
    T --> Y
    X --> Y

    Y --> Z[Agent 创建成功]
    D --> AA[结束]
    Z --> AA

    style A fill:#2b6cee,color:#fff
    style Z fill:#4ecdc4,color:#fff
    style D fill:#ff6b6b,color:#fff
```

## 4. 工具调用与 Hook 流程

```mermaid
sequenceDiagram
    participant Claude as Claude API
    participant CLI as Claude Code CLI
    participant Hook as Pre/Post Hooks
    participant Tool as Tool Executor
    participant System as System/Shell

    Claude->>CLI: tool_use 请求
    Note over Claude,CLI: type: tool_use<br/>name: Bash<br/>input: {command: "ls"}

    CLI->>Hook: 触发 PreToolUse Hook

    alt 安全检查失败
        Hook->>Hook: 检测到危险命令<br/>(例如: rm -rf /)
        Hook-->>CLI: permissionDecision: deny
        CLI-->>Claude: 返回错误
    else 安全检查通过
        Hook-->>CLI: 允许执行
        CLI->>Tool: 执行工具

        alt Bash Tool
            Tool->>System: 执行 Shell 命令
            System-->>Tool: stdout/stderr
        else File Tool (Read/Write/Edit)
            Tool->>System: 文件操作
            System-->>Tool: 文件内容/成功状态
        else Skill Tool
            Tool->>System: 调用 Skill
            System-->>Tool: Skill 输出
        end

        Tool-->>CLI: 工具结果
        CLI->>Hook: 触发 PostToolUse Hook
        Hook->>Hook: 记录日志
        Hook-->>CLI: 继续
        CLI-->>Claude: tool_result
    end

    Claude->>Claude: 处理结果
    Note over Claude: 可能继续调用其他工具<br/>或返回最终响应
```

## 5. Skills 上传与使用流程

```mermaid
flowchart LR
    subgraph "Skills 上传"
        A[用户上传 ZIP] --> B[POST /api/skills/upload]
        B --> C[解压验证]
        C --> D{包含 skill.yaml?}
        D -->|否| E[返回错误]
        D -->|是| F[解析 skill.yaml]
        F --> G[存储到 S3/本地]
        G --> H[保存元数据到数据库]
        H --> I[返回 skill_id]
    end

    subgraph "Skills 使用"
        J[Agent 配置] --> K[指定 skill_ids]
        K --> L[加载 Skill 配置]
        L --> M[设置 setting_sources]
        M --> N[启用 Skill Tool]
        N --> O[Claude 可调用 Skill]

        O --> P{Skill 调用}
        P --> Q[Claude Code CLI]
        Q --> R[读取 .claude/skills/]
        R --> S[执行 Skill 逻辑]
        S --> T[返回结果给 Claude]
    end

    I -.->|引用| K

    style A fill:#2b6cee,color:#fff
    style I fill:#4ecdc4,color:#fff
    style E fill:#ff6b6b,color:#fff
    style T fill:#4ecdc4,color:#fff
```

## 6. MCP Server 集成流程

```mermaid
graph TB
    subgraph "MCP 配置"
        A[创建 MCP 配置] --> B{连接类型}
        B -->|stdio| C[配置命令<br/>command + args]
        B -->|sse| D[配置 SSE URL]
        B -->|http| E[配置 HTTP URL]

        C --> F[保存到数据库]
        D --> F
        E --> F
    end

    subgraph "Agent 使用 MCP"
        G[Agent 配置 mcp_ids] --> H[加载 MCP 配置]
        H --> I[转换为 SDK 格式]

        I --> J{mcp_servers dict}
        J --> K["stdio 示例:<br/>{type: 'stdio',<br/>command: 'uvx',<br/>args: [...]}"]
        J --> L["sse 示例:<br/>{type: 'sse',<br/>url: 'http://...'}"]
        J --> M["http 示例:<br/>{type: 'http',<br/>url: 'http://...'}"]
    end

    subgraph "运行时"
        N[Claude Code CLI 启动] --> O[初始化 MCP 连接]

        O --> P{stdio}
        O --> Q{sse}
        O --> R{http}

        P --> S[启动子进程]
        Q --> T[建立 SSE 连接]
        R --> U[HTTP 请求/响应]

        S --> V[MCP 工具可用]
        T --> V
        U --> V

        V --> W[Claude 调用 MCP 工具]
        W --> X[MCP Server 执行]
        X --> Y[返回结果]
    end

    F --> H

    style A fill:#2b6cee,color:#fff
    style V fill:#4ecdc4,color:#fff
    style Y fill:#4ecdc4,color:#fff
```

## 7. 会话状态管理流程

```mermaid
stateDiagram-v2
    [*] --> Idle: 用户打开聊天界面

    Idle --> CreatingSession: 发送第一条消息
    CreatingSession --> Active: session_id 创建成功

    Active --> Streaming: Claude 开始响应
    Streaming --> ToolExecution: 检测到 tool_use
    ToolExecution --> Streaming: 工具执行完成

    Streaming --> Active: 消息流结束
    Active --> Active: 继续对话

    Active --> Idle: 用户切换 Agent
    Active --> [*]: 会话结束

    note right of CreatingSession
        Session Manager 创建
        - session_id
        - agent_id
        - messages: []
        - created_at
    end note

    note right of Streaming
        SSE 流式传输
        - type: assistant
        - content: [...]
        - 实时更新 UI
    end note

    note right of ToolExecution
        工具执行
        - Pre Hook 安全检查
        - 工具运行
        - Post Hook 日志记录
    end note
```

## 8. 错误处理流程

```mermaid
flowchart TD
    A[开始请求] --> B{验证请求}
    B -->|无效| C[400 Bad Request]
    B -->|有效| D{Agent 存在?}

    D -->|否| E[404 Not Found]
    D -->|是| F[初始化 Agent Manager]

    F --> G{构建 Options}
    G -->|失败| H[500 Internal Error]
    G -->|成功| I[创建 SDK Client]

    I --> J{启动 Claude Code CLI}
    J -->|失败| K[503 Service Unavailable]
    J -->|成功| L[运行对话]

    L --> M{执行过程}
    M -->|工具调用错误| N[记录错误日志]
    M -->|API 限流| O[429 Rate Limited]
    M -->|API 错误| P[502 Bad Gateway]
    M -->|超时| Q[504 Gateway Timeout]
    M -->|成功| R[返回结果]

    N --> S{可恢复?}
    S -->|是| L
    S -->|否| T[返回错误给用户]

    C --> U[SSE: error event]
    E --> U
    H --> U
    K --> U
    O --> U
    P --> U
    Q --> U
    T --> U

    R --> V[SSE: result event]

    U --> W[Frontend 显示错误]
    V --> X[Frontend 显示成功]

    W --> Y[结束]
    X --> Y

    style A fill:#2b6cee,color:#fff
    style R fill:#4ecdc4,color:#fff
    style C fill:#ff6b6b,color:#fff
    style E fill:#ff6b6b,color:#fff
    style H fill:#ff6b6b,color:#fff
    style K fill:#ff6b6b,color:#fff
    style O fill:#ffa500,color:#fff
    style P fill:#ff6b6b,color:#fff
    style Q fill:#ff6b6b,color:#fff
```

## 9. 前端状态管理流程

```mermaid
graph LR
    subgraph "TanStack Query 状态管理"
        A[组件挂载] --> B[useQuery]
        B --> C{缓存存在?}
        C -->|是| D[返回缓存数据]
        C -->|否| E[发起 API 请求]

        E --> F[Loading 状态]
        F --> G{请求结果}
        G -->|成功| H[更新缓存]
        G -->|失败| I[Error 状态]

        H --> J[触发重新渲染]
        I --> J
        D --> J

        K[用户操作] --> L[useMutation]
        L --> M[POST/PUT/DELETE]
        M --> N{成功?}
        N -->|是| O[invalidateQueries]
        N -->|否| P[显示错误]

        O --> B
    end

    subgraph "SSE 流式状态"
        Q[useStreamingChat] --> R[建立 SSE 连接]
        R --> S[监听事件]

        S --> T{事件类型}
        T -->|assistant| U[追加消息]
        T -->|tool_use| V[显示工具调用]
        T -->|result| W[更新统计]
        T -->|error| X[显示错误]

        U --> Y[更新 messages 数组]
        V --> Y
        W --> Z[会话结束]
        X --> Z
    end

    style A fill:#2b6cee,color:#fff
    style H fill:#4ecdc4,color:#fff
    style Z fill:#4ecdc4,color:#fff
```

## 10. 部署架构流程（生产环境）

```mermaid
graph TB
    subgraph "用户层"
        A[用户浏览器]
    end

    subgraph "AWS CDN"
        B[CloudFront<br/>静态资源分发]
    end

    subgraph "AWS Frontend"
        C[S3 Bucket<br/>React App]
    end

    subgraph "AWS Load Balancer"
        D[Application Load Balancer]
    end

    subgraph "AWS Container Service"
        E[ECS Fargate<br/>FastAPI Backend]
        E --> F[Task 1]
        E --> G[Task 2]
        E --> H[Task N]
    end

    subgraph "Database"
        I[SQLite<br/>Agents/Skills/MCP]
    end

    subgraph "Storage"
        J[Local Filesystem<br/>Skills Packages]
    end

    subgraph "AWS Secrets"
        K[Secrets Manager<br/>ANTHROPIC_API_KEY]
    end

    subgraph "External APIs"
        L[Anthropic API<br/>Claude Models]
        M[MCP Servers]
    end

    A --> B
    B --> C
    A --> D
    D --> E

    F --> I
    G --> I
    H --> I

    F --> J
    G --> J
    H --> J

    F --> K
    G --> K
    H --> K

    F --> L
    G --> L
    H --> L

    F --> M
    G --> M
    H --> M

    style A fill:#2b6cee,color:#fff
    style E fill:#ff6b6b,color:#fff
    style L fill:#4ecdc4,color:#fff
```

---

## 图表说明

### 图表 1: 整体系统架构
展示了前端、后端、数据层和外部服务之间的关系。

### 图表 2: 聊天会话流程（核心）
最重要的流程，展示了从用户输入到 Claude 响应的完整数据流。

### 图表 3: Agent 配置流程
展示了如何创建和配置一个 Agent，包括工具、Skills 和 MCP 的集成。

### 图表 4: 工具调用流程
展示了 Claude 调用工具时的 Hook 机制和安全检查。

### 图表 5: Skills 流程
展示了 Skills 的上传、存储和使用流程。

### 图表 6: MCP Server 集成
展示了三种 MCP 连接类型（stdio/sse/http）的配置和使用。

### 图表 7: 会话状态管理
使用状态图展示会话的生命周期。

### 图表 8: 错误处理
展示了各种错误情况的处理逻辑。

### 图表 9: 前端状态管理
展示了 TanStack Query 和 SSE 流式响应的状态管理。

### 图表 10: 生产部署架构
展示了 AWS 生产环境的部署架构。

---

## 使用说明

1. **在 Markdown 编辑器中查看**: 支持 Mermaid 的编辑器（如 VS Code + Mermaid 插件、Typora、GitHub）可以直接渲染这些图表。

2. **在线渲染**: 访问 [Mermaid Live Editor](https://mermaid.live/) 粘贴代码查看。

3. **导出图片**: 使用 Mermaid CLI 或在线工具导出 PNG/SVG 格式。

```bash
# 使用 Mermaid CLI 导出
npm install -g @mermaid-js/mermaid-cli
mmdc -i FLOWCHARTS.md -o flowcharts.pdf
```
