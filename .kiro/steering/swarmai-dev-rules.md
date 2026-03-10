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

## File Writing Method (CRITICAL)
When creating or modifying code files, ALWAYS use `fsWrite` + `fsAppend` (small chunks, ~40 lines each). 
- **NEVER** use `executeBash` with heredoc (`cat > file << 'EOF'`) — this hangs/stalls the agent process
- **NEVER** write files larger than ~50 lines in a single `fsWrite` call — split into `fsWrite` for the first chunk, then `fsAppend` for subsequent chunks
- For edits to existing files, prefer `strReplace` or `editCode` over rewriting the whole file
- After writing, verify with `executeBash` using `head` or `wc -l` to confirm the file landed correctly

## Regression-Prone Areas — Steering Cross-References

The following areas have dedicated steering files with detailed invariants and regression checklists. Always consult the relevant steering file before modifying code in these areas:

| Area | Steering File | Key Files |
|------|--------------|-----------|
| Multi-tab chat isolation | `multi-tab-isolation-principles.md` | ChatPage.tsx, useChatStreamingLifecycle.ts, useUnifiedTabState.ts |
| Session identity & backend isolation | `session-identity-and-backend-isolation.md` | agent_manager.py, session_manager.py, permission_manager.py, chat.py |
| Context & memory safety | `context-and-memory-safety.md` | context_directory_loader.py, system_prompt.py, hooks/*.py, locked_write.py |
| Self-evolution guardrails | `self-evolution-guardrails.md` | s_self-evolution/*, EVOLUTION.md, chat.py (SSE parsing) |

## Global Anti-Patterns

These anti-patterns apply across the entire codebase:

1. **Shared mutable state between sessions**: Never add module-level mutable state (dicts, lists, sets) that isn't keyed by session ID. Use per-session data structures or the existing `_active_sessions` / `_session_locks` patterns.
2. **React useState for cross-tab decisions**: Never read React `useState` values to make decisions about a specific tab. Always read from `tabMapRef` (authoritative source). React state is a display mirror only.
3. **Overwriting user files**: Never overwrite files with `user_customized=True` in `ensure_directory()`. User edits are sacred.
4. **Global permission queue**: Never use `permission_manager.get_permission_queue()` (deprecated). Use `get_session_queue(session_id)`.
5. **Direct MEMORY.md writes**: Never write to MEMORY.md without `locked_write.py`. Concurrent writes from hooks + skills can corrupt the file.
6. **Heredoc file writes**: Never use `cat > file << 'EOF'` in bash — it hangs the agent process. Use `fsWrite` + `fsAppend`.
