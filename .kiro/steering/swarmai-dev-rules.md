---
inclusion: always
---

# SwarmAI Development Rules

## Architecture
- Desktop app: Tauri 2.0 + React + Python FastAPI sidecar
- Backend uses Claude Agent SDK with ClaudeSDKClient
- SQLite database, local filesystem for skills
- Data dirs: macOS `~/Library/Application Support/SwarmAI/`, Linux `~/.local/share/SwarmAI/`, Windows `%LOCALAPPDATA%\SwarmAI\`

## API Naming Convention (CRITICAL)
- Backend: `snake_case` (Python/Pydantic)
- Frontend: `camelCase` (TypeScript)
- ALWAYS update `toCamelCase()` functions in `desktop/src/services/*.ts` when adding fields

## Security (4-Layer Defense)
1. Workspace Isolation: Per-agent dirs in `<app_data_dir>/workspaces/{agent_id}/`
2. Skill Access Control: PreToolUse hook validates authorized skills
3. File Tool Access Control: Permission handler validates file paths
4. Bash Command Protection: Blocks absolute paths outside workspace

## Development Commands
```bash
# Desktop dev
cd desktop && npm run tauri:dev

# Backend dev
cd backend && uv sync && source .venv/bin/activate && python main.py

# Build
cd desktop && npm run build:all
```

## Key Patterns
- SSE streaming for chat responses
- Skills are SKILL.md files with YAML frontmatter
- MCP servers: stdio/sse/http connection types
- Theme: CSS variables in `--color-*` format, never hardcode colors

## Adding New Agent Fields
1. `backend/schemas/agent.py` - Pydantic model
2. `backend/database/sqlite.py` - DB column
3. `backend/core/agent_manager.py` - Use in `_build_options()`
4. `desktop/src/types/index.ts` - TypeScript interface
5. `desktop/src/services/agents.ts` - Both `toSnakeCase` AND `toCamelCase`
6. `desktop/src/pages/AgentsPage.tsx` - UI

## Reference Docs
- Security: `.kiro/specs/SECURITY.md`
- Skills: `.kiro/specs/SKILLS_GUIDE.md`
- Build: `.kiro/specs/DESKTOP_BUILD_GUIDE.md`
