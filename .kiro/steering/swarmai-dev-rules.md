---
inclusion: always
---

# SwarmAI Development Rules

## Architecture
- Desktop app: Tauri 2.0 + React + Python FastAPI sidecar
- Backend uses Claude Agent SDK with ClaudeSDKClient
- SQLite database, local filesystem for skills
- Data dirs: `~/.swarm-ai/` (all platforms)

## Storage Model (CRITICAL)
- **DB-Canonical**: Tasks, ToDos, PlanItems, Communications, ChatThreads (query via API, NOT filesystem)
- **Filesystem**: Artifacts/, ContextFiles/ (content storage only)
- **Hybrid**: Artifacts and Reflections have DB metadata + filesystem content

## API Naming Convention (CRITICAL)
- Backend: `snake_case` (Python/Pydantic)
- Frontend: `camelCase` (TypeScript)
- ALWAYS update `toCamelCase()` functions in `desktop/src/services/*.ts` when adding fields

## Development Commands
```bash
# Desktop dev
cd desktop && npm run tauri:dev

# Backend dev
cd backend && uv sync && source .venv/bin/activate && python main.py

# Run tests
cd desktop && npm test -- --run
cd backend && pytest

# Build
cd desktop && npm run build:all
```

## Key Patterns
- SSE streaming for chat responses
- Skills are SKILL.md files with YAML frontmatter
- MCP servers: stdio/sse/http connection types
- Theme: CSS variables in `--color-*` format, never hardcode colors
- Default SwarmWorkspace path: `{app_data_dir}/swarm-workspaces/SwarmWS`

## Adding New Agent Fields
1. `backend/schemas/agent.py` - Pydantic model
2. `backend/database/sqlite.py` - DB column
3. `backend/core/agent_manager.py` - Use in `_build_options()`
4. `desktop/src/types/index.ts` - TypeScript interface
5. `desktop/src/services/agents.ts` - Both `toSnakeCase` AND `toCamelCase`
6. `desktop/src/pages/AgentsPage.tsx` - UI

## Key Files
- `backend/config.py` - `get_app_data_dir()` returns platform-specific data directory
- `backend/utils/bundle_paths.py` - Tauri bundle resource path detection (dev vs production)
- `backend/core/swarm_workspace_manager.py` - SwarmWorkspace management, `DEFAULT_WORKSPACE_CONFIG`
- `backend/core/agent_manager.py` - Agent lifecycle, `ensure_default_agent()`
- `backend/core/initialization_manager.py` - App initialization logic

## Reference Docs
- Security: `.kiro/specs/SECURITY.md`
- Skills: `.kiro/specs/SKILLS_GUIDE.md`
- Build: `.kiro/specs/DESKTOP_BUILD_GUIDE.md`

## Code Documentation Standards (CRITICAL)
When creating or modifying code files, ALWAYS include a detailed module-level docstring at the top of the file. Follow the style established in `backend/core/agent_manager.py`:

- **Python files**: Use a triple-quoted docstring as the first statement. Include:
  - One-line summary of the module's purpose
  - Description of what was extracted/refactored and why (if applicable)
  - Bulleted list of key public symbols (classes, functions, constants) with brief descriptions
  - Note on re-exports or backward compatibility if relevant

- **TypeScript/React files**: Use a `/** */` block comment at the top. Include:
  - One-line summary of the file's purpose
  - List of key exports (components, hooks, utilities) with brief descriptions

- **Test files**: Use a module docstring describing:
  - What is being tested (module, class, function)
  - Testing methodology (property-based, unit, integration)
  - Key properties or invariants being verified

Example (Python):
```python
"""Claude SDK environment configuration and client wrapper.

This module was extracted from ``agent_manager.py`` to isolate environment
setup concerns.  It is responsible for:

- ``_configure_claude_environment``    — Reads API settings from the database
- ``_ClaudeClientWrapper``             — Async context-manager wrapper
- ``AuthenticationNotConfiguredError`` — Pre-flight validation exception

All public symbols are re-exported by ``agent_manager.py`` for backward
compatibility.
"""
```

This rule applies to ALL code file changes — new files and modifications to existing files that lack proper module-level documentation.
