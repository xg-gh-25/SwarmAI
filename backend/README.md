# Agent Platform Backend

FastAPI backend for the AI Agent Platform using **Claude Agent SDK**.

## Features

- **Agent Management**: CRUD operations for AI agents
- **Skill Management**: Upload and AI-generate skills
- **MCP Server Management**: Configure MCP connections (stdio, SSE, HTTP)
- **Chat Streaming**: SSE streaming with Claude Agent SDK
- **Custom Tools**: Define tools using `@tool` decorator
- **Hooks**: PreToolUse and PostToolUse hooks for safety and logging

## Setup

```bash
# Create virtual environment with uv
uv sync
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Run the server
python main.py
```

## Environment Variables

Create a `.env` file (or copy from `.env.example`):

### Required

```env
ANTHROPIC_API_KEY=your-api-key-here
```

### Optional - Claude API Configuration

```env
# Custom Anthropic API base URL (for proxies or custom endpoints)
ANTHROPIC_BASE_URL=https://your-proxy.example.com

# Use AWS Bedrock instead of Anthropic API
# Set to true if you want to use Bedrock (requires AWS credentials)
CLAUDE_CODE_USE_BEDROCK=false

# Disable experimental beta features in Claude Code
CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=false

# Default Claude model to use
DEFAULT_MODEL=claude-sonnet-4-5-20250929
```

### Optional - Server Configuration

```env
DEBUG=true
HOST=0.0.0.0
PORT=8000
```

See `.env.example` for a complete list of configuration options.

## API Documentation

After starting the server, visit http://localhost:8000/docs for the OpenAPI documentation.

## Project Structure

```
backend/
├── main.py              # FastAPI application
├── config.py            # Settings and configuration
├── routers/             # API route handlers
│   ├── agents.py        # Agent CRUD
│   ├── skills.py        # Skill CRUD
│   ├── mcp.py           # MCP server CRUD
│   └── chat.py          # Chat streaming (SSE)
├── core/                # Business logic
│   ├── agent_manager.py # Claude Agent SDK integration
│   └── session_manager.py
├── database/            # Data layer
│   ├── base.py          # Base database interfaces
│   └── sqlite.py        # SQLite implementation
├── schemas/             # Pydantic models
└── pyproject.toml       # Dependencies
```

## Claude Agent SDK Integration

The backend uses the official Claude Agent SDK for:

- **ClaudeSDKClient**: Multi-turn conversations with Claude
- **ClaudeAgentOptions**: Configure tools, permissions, and hooks
- **@tool decorator**: Define custom in-process tools
- **create_sdk_mcp_server**: Create SDK MCP servers
- **HookMatcher**: Pre/Post tool use hooks for security

Example:
```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

options = ClaudeAgentOptions(
    allowed_tools=["Read", "Write", "Bash"],
    permission_mode="acceptEdits",
    system_prompt="You are a helpful assistant."
)

async with ClaudeSDKClient(options=options) as client:
    await client.query("Hello!")
    async for msg in client.receive_response():
        print(msg)
```
