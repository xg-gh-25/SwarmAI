# Agent Platform Architecture Design Document

**Version:** 4.0 (Claude Agent SDK)
**Last Updated:** December 2025
**Status:** Production-Ready Design (Claude Agent SDK Implementation)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Overview](#2-system-overview)
3. [Architecture Layers](#3-architecture-layers)
4. [Core Components](#4-core-components)
5. [Data Models](#5-data-models)
6. [API Design](#6-api-design)
7. [Technology Stack](#7-technology-stack)
8. [Deployment Architecture](#8-deployment-architecture)
9. [Security & Best Practices](#9-security--best-practices)
10. [Performance & Scalability](#10-performance--scalability)
11. [Future Enhancements](#11-future-enhancements)

---

## 1. Executive Summary

The Agent Platform is a conversational AI system that enables users to interact with customizable AI agents powered by **Claude Agent SDK**. The platform uses the official Anthropic Claude Agent SDK to spawn and manage Claude Code CLI instances in the FastAPI backend, providing a sophisticated skill and tool ecosystem through Skills and Model Context Protocol (MCP) servers.

### Architecture: Claude Agent SDK

**Key Change**: The core implementation now uses the **Claude Agent SDK** (`claude-agent-sdk`) which manages Claude Code CLI processes. This provides:
- Built-in tool support (Bash, Read, Write, Edit, Glob, Grep, WebFetch, etc.)
- Native MCP server integration (stdio, SSE, HTTP)
- Conversation session management with `ClaudeSDKClient`
- Custom tool definitions via `@tool` decorator
- Hook system for event interception
- Streaming responses via async iterators

### Key Capabilities

- **Agent Management**: Create and configure custom agents with different models, parameters, and capabilities
- **Claude Agent SDK Execution**: Agents managed via `ClaudeSDKClient` with conversation continuity
- **Session-based Conversations**: Maintains conversation context across multiple exchanges
- **Dynamic Skill System**: Extensible architecture supporting custom tools via `@tool` decorator
- **MCP Integration**: First-class support for MCP servers using Claude Agent SDK's `mcp_servers` config
- **Real-time Streaming**: SSE-based streaming using async iterators from Claude Agent SDK
- **Rich Media Support**: Handle text, images, markdown, code blocks, and structured tool outputs

### Design Principles

1. **Simplicity**: Leverage Claude Agent SDK's managed CLI process for reliable agent execution
2. **Modularity**: Clean separation between frontend, API, business logic, and data layers
3. **Extensibility**: Custom tools via `@tool` decorator and MCP server integration
4. **Observability**: Comprehensive logging via hooks and message processing
5. **Security**: Sandbox mode, tool permissions, and proper authentication
6. **Performance**: Efficient streaming and session management

---

## 2. System Overview

### 2.1 High-Level Architecture (Claude Agent SDK)

```
┌─────────────────────────────────────────┐
│  React Frontend (Vite + Tailwind)       │
│  • Chat Interface • Agent Mgmt           │
│  • Skill Mgmt • MCP Mgmt                 │
└────────────────┬────────────────────────┘
                 │ HTTP/REST + SSE
┌────────────────▼────────────────────────┐
│  FastAPI Backend (uvicorn)               │
│  • REST endpoints • SSE streaming        │
│  • Claude Agent SDK (ClaudeSDKClient)    │
│  • Custom Tools (@tool decorator)        │
│  • MCP Server Configurations             │
│  • Session-based Conversation Storage    │
└─────┬───────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────┐
│  Claude Code CLI (managed by SDK)        │
│  ┌─────────────────────────────────────┐│
│  │  Built-in Tools                      ││
│  │  • Bash • Read • Write • Edit        ││
│  │  • Glob • Grep • WebFetch            ││
│  │  • TodoWrite • NotebookEdit          ││
│  └─────────────────────────────────────┘│
│  ┌─────────────────────────────────────┐│
│  │  Custom Tools (@tool decorator)      ││
│  │  • Skill Tool • Domain-specific      ││
│  └─────────────────────────────────────┘│
│  ┌─────────────────────────────────────┐│
│  │  MCP Servers (stdio/sse/http)        ││
│  │  • Database • Filesystem • API       ││
│  └─────────────────────────────────────┘│
└─────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────┐
│  AWS Services                            │
│  ┌─────────────┐   ┌──────────────────┐ │
│  │  SQLite     │   │  Claude API      │ │
│  │  • agents   │   │  (via Claude     │ │
│  │  • skills   │   │   Code CLI)      │ │
│  │  • mcp_cfg  │   └──────────────────┘ │
│  └─────────────┘                         │
└─────────────────────────────────────────┘
```

**Key Changes from Previous Architecture**:
- Uses Claude Agent SDK instead of Strands Agents SDK
- Claude Code CLI manages model invocation (not direct Bedrock API calls)
- Built-in tools provided by Claude Code CLI
- Custom tools defined via `@tool` decorator
- `ClaudeSDKClient` for session management

### 2.2 Data Flow Architecture (Claude Agent SDK)

```
User Input (Text/Image)
        │
        ▼
┌───────────────────┐
│  Frontend         │  1. User selects agent
│  Validation       │  2. Sends message via HTTP POST
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  FastAPI          │  3. Validates input payload
│  Backend          │  4. Loads agent config from database
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  Agent Manager    │  5. Gets/creates ClaudeSDKClient session
│  (Claude SDK)     │  6. Configures allowed tools & MCP servers
│                   │  7. Applies system prompt and hooks
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  ClaudeSDKClient  │  8. Sends query with prompt
│  (Claude Code CLI)│  9. Claude Code invokes tools
│                   │  10. Generates response via Claude API
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  Session Manager  │  11. Stores session_id for continuity
│  (in-memory)      │  12. Tracks conversation state
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  SSE Streaming    │  13. Streams messages via async iterator
│  Response         │  14. Includes tool use events
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  Frontend         │  15. Renders streaming response
│  Display          │  16. Formats tool calls, code blocks
│                   │  17. Updates conversation UI
└───────────────────┘
```

---

## 3. Architecture Layers

### 3.1 Presentation Layer (Frontend)

**Technology**: React 18+ with Vite, TypeScript, Tailwind CSS

**Design System Reference**: `/home/ubuntu/workspace/strands_demos/awesome-skills-platform/ui_design/`

#### Design Specifications

The UI design follows a consistent design system with:

- **Typography**: Space Grotesk font family (weights: 300-700)
- **Color Palette**:
  - Primary: `#2b6cee` (blue for interactive elements)
  - Background Light: `#f6f6f8` (light mode)
  - Background Dark: `#101622` (dark mode)
  - Text: `#ffffff` (dark mode), `#101622` (light mode)
  - Muted: `#9da6b9` (secondary text)
- **Icons**: Material Symbols Outlined
- **Layout**: Sidebar navigation with main content area
- **Dark Mode**: Tailwind CSS dark mode with `dark:` prefix

#### Four Main UI Modules

**1. Agent Chat Interface**
   - Location: `/home/ubuntu/workspace/strands_demos/awesome-skills-platform/ui_design/agent对话主界面/`
   - Left sidebar: Chat history list with active conversation highlighting
   - Main area: Conversation view with message bubbles
   - Tool call display: Visual indicators for skill/MCP invocations
   - Input area: Text input with file attachment button
   - Skill/MCP toggles: Checkboxes to enable skills and MCP servers
   - "New Chat" button in sidebar

**2. Agent Management**
   - Location: `/home/ubuntu/workspace/strands_demos/awesome-skills-platform/ui_design/agent定制管理/`
   - Left panel: Table view with agent list (name, model, status)
   - Right panel: Configuration panel for editing agent settings
   - Configuration options:
     - Model selector dropdown
     - Max tokens slider
     - Permission mode selector
     - Skills multi-select checkboxes
     - MCP servers multi-select checkboxes
   - "Create New Agent" button

**3. Skill Management**
   - Location: `/home/ubuntu/workspace/strands_demos/awesome-skills-platform/ui_design/skill管理/`
   - Top toolbar: Search bar and filter controls
   - Action buttons:
     - "Upload ZIP" button for skill package upload
     - "Create with Agent" button for AI-assisted skill generation
   - Skill table: Name, description, created date, actions
   - Actions per skill: Edit, Delete icons

**4. MCP Management**
   - Location: `/home/ubuntu/workspace/strands_demos/awesome-skills-platform/ui_design/mcp管理/`
   - Top toolbar: Search and filter controls
   - MCP table: Name, connection type, status, actions
   - Status indicators:
     - Online (green)
     - Offline (gray)
     - Error (red)
   - Actions: Add, Edit, Delete, Test Connection
   - Visual health monitoring

#### Component Architecture

```
src/
├── App.tsx                          # Root component with routing
├── main.tsx                         # Entry point
├── components/
│   ├── chat/
│   │   ├── ChatInterface.tsx        # Main chat container
│   │   ├── MessageList.tsx          # Scrollable message display
│   │   ├── MessageItem.tsx          # Individual message rendering
│   │   ├── InputArea.tsx            # Text/image input controls
│   │   ├── ChatHistorySidebar.tsx   # Left sidebar with conversation list
│   │   ├── SkillMCPToggles.tsx      # Checkboxes for skills/MCP
│   │   └── StreamingIndicator.tsx   # Typing indicator
│   ├── agents/
│   │   ├── AgentTable.tsx           # Table view of agents
│   │   ├── AgentRow.tsx             # Individual agent row
│   │   ├── AgentConfigPanel.tsx     # Right-side configuration panel
│   │   ├── ModelSelector.tsx        # Model dropdown
│   │   ├── PermissionModeSelector.tsx  # Permission mode controls
│   │   └── ToolsSelector.tsx        # Multi-select for skills/MCPs
│   ├── skills/
│   │   ├── SkillTable.tsx           # Table view of skills
│   │   ├── SkillRow.tsx             # Individual skill row
│   │   ├── SkillToolbar.tsx         # Search and filter controls
│   │   ├── SkillUploadModal.tsx     # ZIP upload interface
│   │   └── SkillGeneratorModal.tsx  # AI-assisted skill creation
│   ├── mcp/
│   │   ├── MCPTable.tsx             # List of MCP configurations
│   │   ├── MCPRow.tsx               # Individual MCP row with status
│   │   ├── MCPToolbar.tsx           # Search and filter controls
│   │   ├── MCPForm.tsx              # Create/edit MCP form
│   │   └── MCPStatusIndicator.tsx   # Status badge component
│   └── common/
│       ├── Layout.tsx               # App shell with navigation
│       ├── Sidebar.tsx              # Navigation menu
│       ├── SearchBar.tsx            # Universal search component
│       └── LoadingSpinner.tsx       # Loading states
├── hooks/
│   ├── useSSE.ts                    # SSE connection manager
│   ├── useStreamingChat.ts          # Chat streaming logic
│   ├── useAgentAPI.ts               # Agent CRUD operations
│   ├── useSkillAPI.ts               # Skill CRUD operations
│   └── useMCPAPI.ts                 # MCP CRUD operations
├── services/
│   ├── api.ts                       # Axios client configuration
│   ├── sse.ts                       # SSE client wrapper
│   └── storage.ts                   # Local storage utilities
├── types/
│   ├── agent.types.ts               # Agent-related TypeScript types
│   ├── skill.types.ts               # Skill-related types
│   ├── message.types.ts             # Message/chat types
│   └── mcp.types.ts                 # MCP configuration types
└── utils/
    ├── formatters.ts                # Text formatting utilities
    ├── validators.ts                # Input validation
    └── markdown.ts                  # Markdown rendering helpers
```

#### State Management Strategy

**Global State**: TanStack Query (React Query) for server state
- Agent configurations cached and synced
- Skill metadata cached with automatic revalidation
- MCP server status cached and monitored

**Local State**: React hooks (`useState`, `useReducer`) for UI state
- Active conversation
- Input field state
- Modal visibility
- Selection states

**SSE State**: Custom `useSSE` hook
- Connection status
- Message handling
- Reconnection logic

---

### 3.2 API Layer (Backend)

**Technology**: FastAPI + Uvicorn

#### API Structure

```python
# src/api/main.py
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize DB, load skills, initialize agent sessions
    await startup_event()
    yield
    # Shutdown: Close sessions, cleanup
    await shutdown_event()

app = FastAPI(title="Agent Platform API", version="4.0.0", lifespan=lifespan)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
from routers import agents, skills, mcp, chat
app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
app.include_router(skills.router, prefix="/api/skills", tags=["skills"])
app.include_router(mcp.router, prefix="/api/mcp", tags=["mcp"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])

# Health check
@app.get("/health")
async def health():
    return {"status": "healthy", "version": "4.0.0", "sdk": "claude-agent-sdk"}
```

#### Router Organization

```
src/api/
├── main.py                  # FastAPI app initialization
├── routers/
│   ├── agents.py            # Agent CRUD endpoints
│   ├── skills.py            # Skill CRUD endpoints
│   ├── mcp.py               # MCP CRUD endpoints
│   └── chat.py              # Chat SSE streaming endpoint
├── middleware/
│   ├── auth.py              # JWT authentication
│   ├── logging.py           # Request/response logging
│   └── error_handler.py     # Global exception handling
├── dependencies/
│   ├── auth.py              # Auth dependency injection
│   └── sqlite.py            # SQLite database
└── schemas/
    ├── agent.py             # Pydantic models for agents
    ├── skill.py             # Pydantic models for skills
    ├── mcp.py               # Pydantic models for MCP
    └── message.py           # Pydantic models for messages
```

---

### 3.3 Business Logic Layer

**Technology**: Claude Agent SDK + Custom Python Modules

#### Core Modules

```
src/core/
├── agent_manager.py         # Agent lifecycle management (ClaudeSDKClient)
├── skill_manager.py         # Skill loading and custom tool generation
├── mcp_manager.py           # MCP server configuration handling
├── session_manager.py       # Session storage and management
└── streaming_handler.py     # SSE streaming coordinator
```

#### Agent Manager (Claude Agent SDK)

```python
# src/core/agent_manager.py
from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    tool,
    create_sdk_mcp_server,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ResultMessage
)
from typing import Dict, Optional, Any, AsyncIterator
import logging

from src.database.sqlite import db_client
from src.core.skill_manager import skill_manager
from src.core.mcp_manager import mcp_manager
from src.core.session_manager import session_manager

class AgentManager:
    """Manages agent lifecycle using Claude Agent SDK"""

    def __init__(self):
        self._clients: Dict[str, ClaudeSDKClient] = {}
        self._logger = logging.getLogger(__name__)

    async def get_or_create_client(
        self,
        agent_id: str,
        session_id: Optional[str] = None,
    ) -> ClaudeSDKClient:
        """Get cached client or create new one from database config"""
        cache_key = f"{agent_id}:{session_id}" if session_id else agent_id

        if cache_key in self._clients:
            return self._clients[cache_key]

        # Load config from database
        agent_config = db_client.get_agent(agent_id)
        if not agent_config:
            raise ValueError(f"Agent {agent_id} not found")

        client = await self._create_client_from_config(agent_config, session_id)
        self._clients[cache_key] = client

        return client

    async def _create_client_from_config(
        self,
        config: dict,
        session_id: Optional[str] = None
    ) -> ClaudeSDKClient:
        """Create ClaudeSDKClient from configuration"""

        # Build options from config
        options = self._build_options(config)

        # Create client
        client = ClaudeSDKClient(options=options)

        # Connect (optionally with resume for existing session)
        if session_id and session_manager.session_exists(session_id):
            # Resume existing session
            options.resume = session_id
        await client.connect()

        return client

    def _build_options(self, config: dict) -> ClaudeAgentOptions:
        """Build ClaudeAgentOptions from agent configuration"""
        skill_ids = config.get("skillIds", [])
        mcp_ids = config.get("mcpIds", [])

        # Build allowed tools list
        allowed_tools = config.get("allowedTools", [])

        # Add built-in tools if needed
        if config.get("enableBashTool", True):
            allowed_tools.append("Bash")
        if config.get("enableFileTools", True):
            allowed_tools.extend(["Read", "Write", "Edit", "Glob", "Grep"])
        if config.get("enableWebTools", False):
            allowed_tools.extend(["WebFetch", "WebSearch"])

        # Load custom skill tools
        custom_tools = []
        mcp_servers = {}

        if skill_ids:
            skill_server = skill_manager.create_skill_mcp_server(skill_ids)
            mcp_servers["skills"] = skill_server
            # Add skill tools to allowed list
            for skill_id in skill_ids:
                allowed_tools.append(f"mcp__skills__{skill_id}")

        # Load MCP server configurations
        for mcp_id in mcp_ids:
            mcp_config = mcp_manager.get_mcp_config(mcp_id)
            if mcp_config:
                mcp_servers[mcp_id] = mcp_config["server_config"]
                # Add MCP tools to allowed list
                if mcp_config.get("allowed_tools"):
                    for tool_name in mcp_config["allowed_tools"]:
                        allowed_tools.append(f"mcp__{mcp_id}__{tool_name}")

        # Build system prompt
        system_prompt = config.get("systemPrompt")
        if system_prompt:
            system_prompt_config = {
                "type": "preset",
                "preset": "claude_code",
                "append": system_prompt
            }
        else:
            system_prompt_config = {
                "type": "preset",
                "preset": "claude_code"
            }

        # Build permission mode
        permission_mode = config.get("permissionMode", "default")

        # Build hooks for logging and interception
        hooks = self._build_hooks(config)

        return ClaudeAgentOptions(
            system_prompt=system_prompt_config,
            allowed_tools=allowed_tools,
            mcp_servers=mcp_servers,
            permission_mode=permission_mode,
            model=config.get("model"),
            max_turns=config.get("maxTurns"),
            cwd=config.get("workingDirectory", "/workspace"),
            hooks=hooks,
            include_partial_messages=True,
        )

    def _build_hooks(self, config: dict) -> dict:
        """Build hooks for event interception"""
        from src.core.hooks import (
            pre_tool_logger,
            post_tool_logger,
            dangerous_command_blocker
        )

        hooks = {}

        # Pre-tool hooks
        pre_tool_hooks = []
        if config.get("enableToolLogging", True):
            pre_tool_hooks.append({"hooks": [pre_tool_logger]})
        if config.get("enableSafetyChecks", True):
            pre_tool_hooks.append({
                "matcher": "Bash",
                "hooks": [dangerous_command_blocker]
            })
        if pre_tool_hooks:
            hooks["PreToolUse"] = pre_tool_hooks

        # Post-tool hooks
        if config.get("enableToolLogging", True):
            hooks["PostToolUse"] = [{"hooks": [post_tool_logger]}]

        return hooks if hooks else None

    async def run_conversation(
        self,
        agent_id: str,
        user_message: str,
        session_id: Optional[str] = None
    ) -> AsyncIterator[dict]:
        """Run conversation with agent and stream responses"""
        client = await self.get_or_create_client(agent_id, session_id)

        # Send query
        await client.query(user_message)

        # Stream responses
        async for message in client.receive_response():
            yield self._format_message(message)

            # Store session_id from result
            if isinstance(message, ResultMessage):
                if session_id is None:
                    session_id = message.session_id
                session_manager.store_session(session_id, agent_id)
                yield {
                    "type": "result",
                    "session_id": message.session_id,
                    "duration_ms": message.duration_ms,
                    "total_cost_usd": message.total_cost_usd,
                    "num_turns": message.num_turns
                }

    def _format_message(self, message) -> dict:
        """Format SDK message to API response format"""
        if isinstance(message, AssistantMessage):
            content_blocks = []
            for block in message.content:
                if isinstance(block, TextBlock):
                    content_blocks.append({
                        "type": "text",
                        "text": block.text
                    })
                elif isinstance(block, ToolUseBlock):
                    content_blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input
                    })
            return {
                "type": "assistant",
                "content": content_blocks,
                "model": message.model
            }
        # Handle other message types...
        return {"type": "unknown", "data": str(message)}

    async def disconnect_client(self, agent_id: str, session_id: Optional[str] = None):
        """Disconnect and cleanup client"""
        cache_key = f"{agent_id}:{session_id}" if session_id else agent_id
        client = self._clients.pop(cache_key, None)
        if client:
            await client.disconnect()


# Global instance
agent_manager = AgentManager()
```

#### Skill Manager with Custom Tools

```python
# src/core/skill_manager.py
from claude_agent_sdk import tool, create_sdk_mcp_server
from typing import Dict, List, Any, Optional
import yaml
from pathlib import Path

from src.database.sqlite import db_client

class SkillManager:
    """Manages skills as custom tools for Claude Agent SDK"""

    def __init__(self):
        self._skills: Dict[str, dict] = {}
        self._skill_tools = []

    def load_skills(self, skill_ids: List[str]) -> None:
        """Load skill definitions from database and filesystem"""
        for skill_id in skill_ids:
            skill_meta = db_client.get_skill(skill_id)
            if skill_meta:
                skill_path = Path(f"/app/skills/{skill_meta['skill_name']}/SKILL.md")
                if skill_path.exists():
                    skill_content = self._parse_skill_file(skill_path)
                    self._skills[skill_id] = {
                        "meta": skill_meta,
                        "content": skill_content
                    }

    def _parse_skill_file(self, skill_path: Path) -> dict:
        """Parse SKILL.md file with YAML frontmatter"""
        content = skill_path.read_text()

        # Parse YAML frontmatter
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = yaml.safe_load(parts[1])
                body = parts[2].strip()
                return {
                    "name": frontmatter.get("name"),
                    "description": frontmatter.get("description"),
                    "instructions": body
                }
        return {"name": skill_path.parent.name, "description": "", "instructions": content}

    def create_skill_mcp_server(self, skill_ids: List[str]):
        """Create an MCP server with skill tools"""
        self.load_skills(skill_ids)

        skill_tools = []
        for skill_id, skill_data in self._skills.items():
            skill_tool = self._create_skill_tool(skill_id, skill_data)
            skill_tools.append(skill_tool)

        return create_sdk_mcp_server(
            name="skills",
            version="1.0.0",
            tools=skill_tools
        )

    def _create_skill_tool(self, skill_id: str, skill_data: dict):
        """Create a tool function for a skill"""
        content = skill_data["content"]

        @tool(
            name=skill_id,
            description=content.get("description", f"Use the {skill_id} skill"),
            input_schema={"task": str}
        )
        async def skill_handler(args: Dict[str, Any]) -> Dict[str, Any]:
            """Handle skill invocation by returning instructions"""
            task = args.get("task", "")
            instructions = content.get("instructions", "")

            return {
                "content": [{
                    "type": "text",
                    "text": f"""
# Skill: {content.get('name', skill_id)}

## Instructions
{instructions}

## User Task
{task}

Please follow the skill instructions above to complete the user's task.
"""
                }]
            }

        return skill_handler


# Global instance
skill_manager = SkillManager()
```

#### MCP Manager

```python
# src/core/mcp_manager.py
from typing import Dict, List, Optional, Any
from src.database.sqlite import db_client

class MCPManager:
    """Manages MCP server configurations for Claude Agent SDK"""

    def __init__(self):
        self.configs: Dict[str, dict] = {}
        self.connection_status: Dict[str, str] = {}

    def get_mcp_config(self, mcp_id: str) -> Optional[dict]:
        """Get MCP configuration from database"""
        if mcp_id in self.configs:
            return self.configs[mcp_id]

        mcp_meta = db_client.get_mcp(mcp_id)
        if not mcp_meta:
            return None

        config = self._build_mcp_config(mcp_meta)
        self.configs[mcp_id] = config
        return config

    def _build_mcp_config(self, mcp_meta: dict) -> dict:
        """Build MCP server config for Claude Agent SDK"""
        connection_type = mcp_meta.get("connection_type", "stdio")
        server_config = mcp_meta.get("config", {})

        if connection_type == "stdio":
            return {
                "server_config": {
                    "type": "stdio",
                    "command": server_config.get("command"),
                    "args": server_config.get("args", []),
                    "env": server_config.get("env", {})
                },
                "allowed_tools": mcp_meta.get("allowed_tools"),
                "rejected_tools": mcp_meta.get("rejected_tools")
            }
        elif connection_type == "sse":
            return {
                "server_config": {
                    "type": "sse",
                    "url": server_config.get("url"),
                    "headers": server_config.get("headers", {})
                },
                "allowed_tools": mcp_meta.get("allowed_tools"),
                "rejected_tools": mcp_meta.get("rejected_tools")
            }
        elif connection_type == "http":
            return {
                "server_config": {
                    "type": "http",
                    "url": server_config.get("url"),
                    "headers": server_config.get("headers", {})
                },
                "allowed_tools": mcp_meta.get("allowed_tools"),
                "rejected_tools": mcp_meta.get("rejected_tools")
            }
        else:
            raise ValueError(f"Unsupported connection type: {connection_type}")

    async def test_connection(self, mcp_id: str) -> Dict[str, Any]:
        """Test MCP connection (via Claude Agent SDK)"""
        # Connection testing is handled by Claude Code CLI
        # We can verify config validity here
        config = self.get_mcp_config(mcp_id)
        if not config:
            return {"status": "error", "error": "Configuration not found"}
        return {"status": "configured", "config": config}


# Global instance
mcp_manager = MCPManager()
```

#### Session Manager

```python
# src/core/session_manager.py
from typing import Optional, Dict
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class SessionInfo:
    """Information about a conversation session"""
    session_id: str
    agent_id: str
    created_at: datetime = field(default_factory=datetime.now)
    last_accessed: datetime = field(default_factory=datetime.now)

class SessionManager:
    """Manages conversation sessions for Claude Agent SDK"""

    def __init__(self):
        self._sessions: Dict[str, SessionInfo] = {}

    def store_session(self, session_id: str, agent_id: str):
        """Store session information"""
        if session_id in self._sessions:
            self._sessions[session_id].last_accessed = datetime.now()
        else:
            self._sessions[session_id] = SessionInfo(
                session_id=session_id,
                agent_id=agent_id
            )

    def session_exists(self, session_id: str) -> bool:
        """Check if session exists"""
        return session_id in self._sessions

    def get_session(self, session_id: str) -> Optional[SessionInfo]:
        """Get session information"""
        return self._sessions.get(session_id)

    def delete_session(self, session_id: str) -> bool:
        """Delete session"""
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    def list_sessions(self, agent_id: Optional[str] = None) -> list:
        """List all sessions, optionally filtered by agent_id"""
        sessions = list(self._sessions.values())
        if agent_id:
            sessions = [s for s in sessions if s.agent_id == agent_id]
        return sessions


# Global instance
session_manager = SessionManager()
```

#### Hooks for Event Interception

```python
# src/core/hooks.py
from typing import Any, Dict
from claude_agent_sdk import HookContext
import logging

logger = logging.getLogger(__name__)

async def pre_tool_logger(
    input_data: Dict[str, Any],
    tool_use_id: str | None,
    context: HookContext
) -> Dict[str, Any]:
    """Log all tool usage before execution"""
    tool_name = input_data.get('tool_name', 'unknown')
    tool_input = input_data.get('tool_input', {})
    logger.info(f"[PRE-TOOL] Tool: {tool_name}, Input: {tool_input}")
    return {}

async def post_tool_logger(
    input_data: Dict[str, Any],
    tool_use_id: str | None,
    context: HookContext
) -> Dict[str, Any]:
    """Log results after tool execution"""
    tool_name = input_data.get('tool_name', 'unknown')
    logger.info(f"[POST-TOOL] Completed: {tool_name}")
    return {}

async def dangerous_command_blocker(
    input_data: Dict[str, Any],
    tool_use_id: str | None,
    context: HookContext
) -> Dict[str, Any]:
    """Block dangerous bash commands"""
    if input_data.get('tool_name') == 'Bash':
        command = input_data.get('tool_input', {}).get('command', '')

        dangerous_patterns = [
            'rm -rf /',
            'rm -rf ~',
            'dd if=/dev/zero',
            ':(){:|:&};:',  # Fork bomb
            '> /dev/sda',
        ]

        for pattern in dangerous_patterns:
            if pattern in command:
                logger.warning(f"[BLOCKED] Dangerous command: {command}")
                return {
                    'hookSpecificOutput': {
                        'hookEventName': 'PreToolUse',
                        'permissionDecision': 'deny',
                        'permissionDecisionReason': f'Dangerous command blocked: {pattern}'
                    }
                }
    return {}
```

---

### 3.4 Data Layer

#### Database Schema (SQLite)

**Database Tables Design**

```python
# agents table
{
    "PK": "AGENT#{agent_id}",                        # Partition key
    "SK": "METADATA",                                 # Sort key
    "agent_name": str,
    "description": str,
    "model": str,                                     # Claude model name
    "permission_mode": str,                           # default, acceptEdits, plan, bypassPermissions
    "max_turns": int,
    "system_prompt": str,
    "allowed_tools": List[str],                       # Built-in tools to enable
    "skill_ids": List[str],                           # List of skill IDs
    "mcp_ids": List[str],                             # List of MCP server IDs
    "working_directory": str,                         # CWD for Claude Code
    "enable_bash_tool": bool,
    "enable_file_tools": bool,
    "enable_web_tools": bool,
    "enable_tool_logging": bool,
    "enable_safety_checks": bool,
    "created_at": str,                                # ISO 8601
    "updated_at": str,
    "GSI1PK": "USER#{user_id}",                      # GSI for user queries
    "GSI1SK": "AGENT#{agent_id}"
}

# skills table
{
    "PK": "SKILL#{skill_id}",                        # Partition key
    "SK": "METADATA",                                 # Sort key
    "skill_name": str,
    "description": str,
    "s3_location": str,                               # S3 URI for ZIP package
    "created_by": str,                                # User ID
    "created_at": str,
    "updated_at": str,
    "version": str,
    "is_system": bool,                                # System vs user-created
    "GSI1PK": "USER#{user_id}",                      # GSI for user queries
    "GSI1SK": "SKILL#{created_at}"
}

# mcp_servers table
{
    "PK": "MCP#{server_id}",                         # Partition key
    "SK": "CONFIG",                                   # Sort key
    "server_name": str,
    "description": str,
    "connection_type": str,                           # stdio, sse, http
    "config": Dict,                                   # Connection-specific config
    "status": str,                                    # online, offline, error
    "allowed_tools": List[str],                      # Whitelist (optional)
    "rejected_tools": List[str],                     # Blacklist (optional)
    "created_at": str,
    "updated_at": str,
    "GSI1PK": "USER#{user_id}",
    "GSI1SK": "MCP#{server_id}"
}
```

---

## 4. Core Components

### 4.1 Agent Manager

**Responsibilities**:
- Manage ClaudeSDKClient instances
- Build ClaudeAgentOptions from database configuration
- Coordinate tool loading (skills + MCP + built-in)
- Handle session continuity via resume
- Stream responses via async iterators

**Key Methods**:
- `get_or_create_client(agent_id, session_id) -> ClaudeSDKClient`
- `run_conversation(agent_id, user_message, session_id) -> AsyncIterator`
- `disconnect_client(agent_id, session_id)`

---

### 4.2 Skill Manager

**Responsibilities**:
- Load skill definitions from database and filesystem
- Parse SKILL.md YAML frontmatter and markdown content
- Create custom tools via `@tool` decorator
- Generate MCP server with skill tools via `create_sdk_mcp_server`
- Validate skill packages structure

**Key Methods**:
- `load_skills(skill_ids) -> None`
- `create_skill_mcp_server(skill_ids) -> McpSdkServerConfig`
- `_create_skill_tool(skill_id, skill_data) -> SdkMcpTool`

---

### 4.3 MCP Manager

**Responsibilities**:
- Load MCP server configurations from database
- Build MCP server configs for Claude Agent SDK (stdio, SSE, HTTP)
- Apply tool filtering (allowed/rejected lists)
- Track connection status

**Key Methods**:
- `get_mcp_config(mcp_id) -> dict`
- `_build_mcp_config(mcp_meta) -> dict`
- `test_connection(mcp_id) -> dict`

---

### 4.4 Session Manager

**Responsibilities**:
- Track conversation sessions by session_id
- Enable session resume for conversation continuity
- Manage session lifecycle

**Key Methods**:
- `store_session(session_id, agent_id)`
- `session_exists(session_id) -> bool`
- `get_session(session_id) -> SessionInfo`
- `delete_session(session_id) -> bool`

---

### 4.5 Streaming Handler

**Responsibilities**:
- Transform Claude Agent SDK messages to SSE events
- Coordinate async streaming from ClaudeSDKClient
- Handle connection management

**Event Types**:
- `text`: Incremental response text
- `tool_use`: Tool invocation details
- `tool_result`: Tool execution results
- `result`: Final result with cost and usage info
- `error`: Error messages

---

## 5. Data Models

### 5.1 Agent Configuration

```python
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from uuid import UUID

class AgentConfig(BaseModel):
    id: Optional[UUID] = None
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    model: Optional[str] = Field(
        default=None,
        description="Claude model to use (defaults to Claude Code default)"
    )
    permission_mode: Literal["default", "acceptEdits", "plan", "bypassPermissions"] = "default"
    max_turns: Optional[int] = Field(default=None, ge=1, le=100)
    system_prompt: Optional[str] = None
    allowed_tools: List[str] = Field(default_factory=list)
    skill_ids: List[str] = Field(default_factory=list)
    mcp_ids: List[str] = Field(default_factory=list)
    working_directory: str = Field(default="/workspace")
    enable_bash_tool: bool = True
    enable_file_tools: bool = True
    enable_web_tools: bool = False
    enable_tool_logging: bool = True
    enable_safety_checks: bool = True

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Data Analyst Agent",
                "description": "Specialized in data analysis and visualization",
                "model": "sonnet",
                "permission_mode": "acceptEdits",
                "max_turns": 20,
                "skill_ids": ["xlsx-skill", "docx-skill"],
                "mcp_ids": ["postgres-mcp"],
                "enable_web_tools": True
            }
        }
```

### 5.2 Skill Definition

```python
class SkillMetadata(BaseModel):
    id: Optional[str] = None
    name: str = Field(..., min_length=1, max_length=255)
    description: str
    s3_location: str  # S3 URI
    created_by: str  # User ID
    created_at: Optional[str] = None
    version: str = Field(default="1.0.0")
    is_system: bool = Field(default=False)

class SkillUploadRequest(BaseModel):
    name: str
    file: UploadFile  # FastAPI type

class SkillGenerateRequest(BaseModel):
    description: str = Field(
        ...,
        description="Natural language description of the skill to generate"
    )
    examples: Optional[List[str]] = None
```

### 5.3 MCP Server Configuration

```python
class MCPConfig(BaseModel):
    id: Optional[str] = None
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    connection_type: Literal["stdio", "sse", "http"]
    config: Dict[str, Any] = Field(
        ...,
        description="Connection-specific configuration"
    )
    allowed_tools: Optional[List[str]] = None  # Whitelist
    rejected_tools: Optional[List[str]] = None  # Blacklist
    status: str = Field(default="offline")

    class Config:
        json_schema_extra = {
            "example": {
                "name": "PostgreSQL MCP",
                "description": "Database query tool",
                "connection_type": "stdio",
                "config": {
                    "command": "uvx",
                    "args": ["mcp-server-postgres", "postgresql://..."]
                },
                "allowed_tools": ["query_database", "list_tables"]
            }
        }
```

### 5.4 Message Structures (Claude Agent SDK Types)

```python
from pydantic import BaseModel
from typing import List, Optional, Any, Literal

class TextContent(BaseModel):
    type: Literal["text"] = "text"
    text: str

class ToolUseContent(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: Dict[str, Any]

class ToolResultContent(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: Optional[str] = None
    is_error: bool = False

ContentBlock = TextContent | ToolUseContent | ToolResultContent

class AssistantMessageResponse(BaseModel):
    type: Literal["assistant"] = "assistant"
    content: List[ContentBlock]
    model: str

class ResultMessageResponse(BaseModel):
    type: Literal["result"] = "result"
    session_id: str
    duration_ms: int
    total_cost_usd: Optional[float] = None
    num_turns: int
    is_error: bool = False
```

---

## 6. API Design

### 6.1 REST Endpoints

#### Agent Endpoints

```
GET    /api/agents                    # List all agents for current user
GET    /api/agents/{agent_id}         # Get specific agent
POST   /api/agents                    # Create new agent
PUT    /api/agents/{agent_id}         # Update agent
DELETE /api/agents/{agent_id}         # Delete agent
GET    /api/agents/default            # Get default system agent
```

#### Skill Endpoints

```
GET    /api/skills                    # List all skills
GET    /api/skills/{skill_id}         # Get specific skill
POST   /api/skills/upload             # Upload skill ZIP
POST   /api/skills/generate           # AI-generate skill using agent
DELETE /api/skills/{skill_id}         # Delete skill
GET    /api/skills/system             # List system-provided skills
```

#### MCP Endpoints

```
GET    /api/mcp                       # List all MCP servers
GET    /api/mcp/{mcp_id}              # Get specific MCP server
POST   /api/mcp                       # Create MCP server config
PUT    /api/mcp/{mcp_id}              # Update MCP server
DELETE /api/mcp/{mcp_id}              # Delete MCP server
POST   /api/mcp/{mcp_id}/test         # Test MCP connection
```

### 6.2 Chat SSE Endpoint

**Endpoint**: `POST /api/chat/stream`

**Request Body**:
```json
{
  "agent_id": "agent-uuid",
  "message": "User's message",
  "session_id": "session-uuid"  // Optional, for conversation continuity
}
```

**Response**: Server-Sent Events stream

```python
# src/api/routers/chat.py
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from src.core.agent_manager import agent_manager
import json

router = APIRouter()

@router.post("/stream")
async def chat_stream(request: Request):
    """Stream chat responses via SSE"""
    body = await request.json()
    agent_id = body["agent_id"]
    message = body["message"]
    session_id = body.get("session_id")

    async def generate():
        try:
            async for msg in agent_manager.run_conversation(
                agent_id=agent_id,
                user_message=message,
                session_id=session_id
            ):
                yield f"data: {json.dumps(msg)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )
```

**SSE Event Format**:

```json
// Text content
{
  "type": "assistant",
  "content": [{"type": "text", "text": "I'll help you..."}],
  "model": "claude-sonnet-4-20250514"
}

// Tool use
{
  "type": "assistant",
  "content": [{
    "type": "tool_use",
    "id": "toolu_123",
    "name": "Read",
    "input": {"file_path": "/workspace/file.py"}
  }],
  "model": "claude-sonnet-4-20250514"
}

// Result
{
  "type": "result",
  "session_id": "session-uuid",
  "duration_ms": 1234,
  "total_cost_usd": 0.05,
  "num_turns": 3
}
```

---

## 7. Technology Stack

### Frontend

| Component | Technology | Justification |
|-----------|-----------|---------------|
| Framework | React 18+ | Component-based, rich ecosystem |
| Build Tool | Vite | Fast HMR, optimized builds |
| Language | TypeScript | Type safety, better DX |
| Styling | Tailwind CSS | Rapid styling, consistency, dark mode support |
| Design System | Space Grotesk font + Material Symbols | Modern, clean aesthetic |
| State Management | TanStack Query | Server state caching, automatic refetching |
| Routing | React Router v6 | Standard routing solution |
| SSE Client | native EventSource API | Built-in browser support |
| HTTP Client | Axios | Interceptors, request cancellation |

### Backend

| Component | Technology | Justification |
|-----------|-----------|---------------|
| Framework | FastAPI | Async support, auto-generated docs |
| ASGI Server | Uvicorn | High performance, SSE support |
| Agent SDK | Claude Agent SDK | Official Anthropic SDK for Claude Code |
| Runtime | Claude Code CLI | Managed by SDK, built-in tools |
| Database | SQLite | Lightweight, embedded, zero-config |
| Validation | Pydantic v2 | FastAPI native, runtime validation |

### AI/ML Services

| Component | Service | Description |
|-----------|---------|-------------|
| LLM Provider | Claude API | Via Claude Code CLI |
| Agent Execution | Claude Agent SDK | `ClaudeSDKClient` for sessions |
| Custom Tools | `@tool` decorator | SDK MCP server |
| MCP Integration | Claude Agent SDK | stdio, SSE, HTTP protocols |
| Built-in Tools | Claude Code CLI | Bash, Read, Write, Edit, Glob, Grep, etc. |

### Infrastructure

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Containerization | Docker | Consistent environments |
| Backend Orchestration | ECS Fargate | Serverless containers for FastAPI |
| Load Balancer | ALB | Traffic distribution |
| Secrets | AWS Secrets Manager | Secure credential storage |
| Monitoring | CloudWatch | Logs, metrics, traces |

---

## 8. Deployment Architecture

### 8.1 AWS Deployment

```
Internet
   │
   ▼
[ALB: Application Load Balancer]
   │
   ├────────► [S3: React Static Files]
   │           • index.html
   │           • JS/CSS bundles
   │           • Assets
   │
   └────────► [ECS Fargate: FastAPI Backend]
               • REST API endpoints
               • SSE streaming
               • Claude Agent SDK (ClaudeSDKClient)
               • Claude Code CLI (spawned processes)
               │
               ├─► [SQLite Database]
               │    • agents
               │    • skills
               │    • mcp_servers
               │
               ├─► [Claude API]
               │    (via Claude Code CLI)
               │
               └─► [MCP Servers] (optional)
                    • External tools
                    • API integrations
```

### 8.2 Container Configuration

**Dockerfile (Backend)**:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Claude Agent SDK
RUN pip install claude-agent-sdk

# Copy application code
COPY src/ ./src/
COPY pyproject.toml .

# Create workspace directory
RUN mkdir -p /workspace

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run application
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**requirements.txt**:

```
claude-agent-sdk>=0.1.0
fastapi>=0.110.0
uvicorn[standard]>=0.27.0
boto3>=1.34.0
aioboto3>=12.0.0
pydantic>=2.5.0
python-multipart>=0.0.6
python-jose[cryptography]>=3.3.0
pyyaml>=6.0.0
```

---

## 9. Security & Best Practices

### 9.1 Claude Agent SDK Security

**Permission Modes**:
- `default`: Standard permission behavior
- `acceptEdits`: Auto-accept file edits
- `plan`: Planning mode - no execution
- `bypassPermissions`: Bypass all checks (use with caution)

**Tool Permissions**:
```python
async def can_use_tool(tool: str, input: dict) -> dict:
    """Custom tool permission handler"""
    if tool == "Bash" and input.get("dangerouslyDisableSandbox"):
        return {
            "behavior": "deny",
            "message": "Sandbox bypass not allowed"
        }
    return {"behavior": "allow", "updatedInput": input}

options = ClaudeAgentOptions(
    can_use_tool=can_use_tool,
    permission_mode="default"
)
```

**Sandbox Configuration**:
```python
sandbox_settings = {
    "enabled": True,
    "autoAllowBashIfSandboxed": True,
    "excludedCommands": ["docker"],
    "network": {
        "allowLocalBinding": True,
        "allowUnixSockets": ["/var/run/docker.sock"]
    }
}

options = ClaudeAgentOptions(sandbox=sandbox_settings)
```

### 9.2 Hook-based Security

```python
async def dangerous_command_blocker(
    input_data: dict,
    tool_use_id: str | None,
    context: HookContext
) -> dict:
    """Block dangerous bash commands via hooks"""
    if input_data.get('tool_name') == 'Bash':
        command = input_data.get('tool_input', {}).get('command', '')
        if any(p in command for p in ['rm -rf /', 'dd if=/dev/zero']):
            return {
                'hookSpecificOutput': {
                    'hookEventName': 'PreToolUse',
                    'permissionDecision': 'deny',
                    'permissionDecisionReason': 'Dangerous command blocked'
                }
            }
    return {}

options = ClaudeAgentOptions(
    hooks={
        'PreToolUse': [
            HookMatcher(matcher='Bash', hooks=[dangerous_command_blocker])
        ]
    }
)
```

### 9.3 Authentication & Authorization

Same as previous architecture - JWT-based authentication with AWS Cognito integration.

---

## 10. Performance & Scalability

### 10.1 Session Management

**ClaudeSDKClient Sessions**:
- Each session maintains conversation context
- Sessions can be resumed via `session_id`
- Memory usage scales with active sessions

**Session Cleanup**:
```python
# Cleanup inactive sessions periodically
async def cleanup_sessions():
    now = datetime.now()
    for session_id, info in list(session_manager._sessions.items()):
        if (now - info.last_accessed).total_seconds() > 3600:  # 1 hour
            await agent_manager.disconnect_client(info.agent_id, session_id)
            session_manager.delete_session(session_id)
```

### 10.2 Streaming Optimization

**Async Iterator Processing**:
```python
async for message in client.receive_response():
    # Process messages as they arrive
    # No buffering needed - direct streaming to SSE
    yield format_sse_event(message)
```

### 10.3 Tool Execution

**Built-in Tools**: Executed by Claude Code CLI process
- Efficient subprocess management
- Built-in caching and optimization
- Automatic cleanup

**Custom Tools**: Executed in-process via `@tool` decorator
- Async execution
- Minimal overhead

---

## 11. Future Enhancements

### Phase 2 Features

1. **Multi-Modal Inputs**:
   - PDF upload and parsing
   - Image input via Read tool
   - Document analysis

2. **Advanced Session Management**:
   - Persistent session storage (SQLite)
   - Session forking (`fork_session` option)
   - Cross-device session resume

3. **Enhanced Observability**:
   - Cost tracking per session
   - Tool usage analytics
   - Performance metrics via hooks

4. **Structured Outputs**:
   - JSON Schema validation via `output_format`
   - Type-safe responses

### Phase 3 Features

1. **Multi-Agent Workflows**:
   - Subagent definitions via `agents` option
   - Agent orchestration patterns

2. **Plugin System**:
   - Custom plugins via `plugins` option
   - Extensible tool ecosystem

3. **Enterprise Features**:
   - SSO integration
   - Role-based access control
   - Audit logging via hooks

---

## Appendix

### A. Claude Agent SDK vs Strands Agents SDK Comparison

| Feature | Claude Agent SDK | Strands Agents SDK |
|---------|-----------------|-------------------|
| Runtime | Claude Code CLI (managed) | In-process (direct API) |
| Built-in Tools | Yes (Bash, Read, Write, etc.) | No (custom tools only) |
| MCP Support | Native (stdio, SSE, HTTP) | Via MCPClient |
| Session Management | ClaudeSDKClient | Manual |
| Streaming | Async iterators | Event callbacks |
| Custom Tools | @tool decorator | Custom functions |
| Hooks | PreToolUse, PostToolUse, etc. | Interceptors |
| Sandbox | Built-in | Not available |

### B. Key Dependencies

**Backend**:
```
claude-agent-sdk>=0.1.0
fastapi>=0.110.0
uvicorn[standard]>=0.27.0
boto3>=1.34.0
pydantic>=2.5.0
pyyaml>=6.0.0
```

**Frontend**:
```json
{
  "react": "^18.2.0",
  "react-router-dom": "^6.20.0",
  "@tanstack/react-query": "^5.14.0",
  "axios": "^1.6.2",
  "tailwindcss": "^3.3.0",
  "typescript": "^5.3.0"
}
```

### C. Code Examples

**Basic Chat with Agent**:

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

async def chat_with_agent():
    options = ClaudeAgentOptions(
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": "You are a helpful coding assistant."
        },
        allowed_tools=["Read", "Write", "Bash"],
        permission_mode="acceptEdits"
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query("Create a Python hello world script")

        async for message in client.receive_response():
            if hasattr(message, 'content'):
                for block in message.content:
                    if hasattr(block, 'text'):
                        print(block.text)
```

**Custom Skill Tool**:

```python
from claude_agent_sdk import tool, create_sdk_mcp_server

@tool("analyze_data", "Analyze data from a CSV file", {"file_path": str, "analysis_type": str})
async def analyze_data(args):
    file_path = args["file_path"]
    analysis_type = args["analysis_type"]

    # Return instructions for Claude to follow
    return {
        "content": [{
            "type": "text",
            "text": f"""
Please analyze the data in {file_path} using the following approach:

1. Read the file using the Read tool
2. Perform {analysis_type} analysis
3. Generate a summary report

Focus on key insights and actionable recommendations.
"""
        }]
    }

# Create MCP server with custom tools
data_server = create_sdk_mcp_server(
    name="data_tools",
    version="1.0.0",
    tools=[analyze_data]
)
```

**MCP Server Integration**:

```python
options = ClaudeAgentOptions(
    mcp_servers={
        "filesystem": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
        },
        "postgres": {
            "type": "stdio",
            "command": "uvx",
            "args": ["mcp-server-postgres", "postgresql://localhost/mydb"]
        }
    },
    allowed_tools=[
        "mcp__filesystem__read_file",
        "mcp__filesystem__write_file",
        "mcp__postgres__query"
    ]
)
```

---

**Document Control**

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-11-01 | Architecture Team | Initial design document |
| 2.0 | 2025-11-01 | Architecture Team | Updated with AgentCore Runtime |
| 3.0 | 2025-12-15 | Architecture Team | Simplified: agents run in-process |
| 4.0 | 2025-12-15 | Architecture Team | **Claude Agent SDK**: Replaced Strands with official Claude SDK |

**Review & Approval**

| Role | Name | Signature | Date |
|------|------|-----------|------|
| Technical Architect | TBD | | |
| Lead Engineer | TBD | | |
| Product Manager | TBD | | |
