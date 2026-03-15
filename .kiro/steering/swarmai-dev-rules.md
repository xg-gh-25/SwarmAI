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
| Context & memory safety | `context-and-memory-safety.md` | context_directory_loader.py, context_injector.py, system_prompt.py, hooks/*.py, locked_write.py |
| Self-evolution guardrails | `self-evolution-guardrails.md` | s_self-evolution/*, EVOLUTION.md, chat.py (SSE parsing), evolution_maintenance_hook.py, evolution_trigger_hook.py |

## Global Anti-Patterns

These anti-patterns apply across the entire codebase:

1. **Shared mutable state between sessions**: Never add module-level mutable state (dicts, lists, sets) that isn't keyed by session ID. Use per-session data structures or the existing `_active_sessions` / `_session_locks` patterns.
2. **React useState for cross-tab decisions**: Never read React `useState` values to make decisions about a specific tab. Always read from `tabMapRef` (authoritative source). React state is a display mirror only.
3. **Overwriting user files**: Never overwrite files with `user_customized=True` in `ensure_directory()`. User edits are sacred.
4. **Global permission queue**: Never use `permission_manager.get_permission_queue()` (deprecated). Use `get_session_queue(session_id)`.
5. **Direct MEMORY.md writes**: Never write to MEMORY.md without `locked_write.py`. Concurrent writes from hooks + skills can corrupt the file.
6. **Heredoc file writes**: Never use `cat > file << 'EOF'` in bash — it hangs the agent process. Use `fsWrite` + `fsAppend`.

## Session Lifecycle Invariants (CRITICAL)

These rules govern the backend session lifecycle. Violations cause orphan processes, failed stops, lost messages, or unresumable sessions.

1. **Transient vs persistent client stores**: `_clients` is transient (exists only during active streaming, popped in `_run_query_on_client` finally block). `_active_sessions` is persistent (survives between turns, 2h TTL). Any code that needs to find a client OUTSIDE the streaming loop (e.g. `interrupt_session`, `compact_session`) MUST check `_active_sessions` first, fall back to `_clients`.
2. **Early registration before streaming**: Register the client in `_active_sessions` BEFORE entering `_run_query_on_client`, not after. The streaming loop can be interrupted at any point (user stop, tab switch, SSE abort, watchdog timeout). Resources registered only after the loop are invisible during it.
3. **Abort-first, stop-second ordering**: The frontend aborts the fetch (triggering the `finally` block) THEN sends the stop request. The `finally` block pops `_clients` before the stop arrives. Design all interrupt/stop code for this ordering.
4. **Deferred save pattern**: `session_start` events and user message saves are deferred until after the client path (PATH A vs PATH B) is determined. The `deferred_user_content = None` guard after save prevents double-save across PATH B → PATH A retry. Never save eagerly before the path is known.
5. **`_env_lock` through subprocess spawn**: `os.environ` mutations must be held under `_env_lock` through `wrapper.__aenter__()` so the spawned subprocess inherits correct env vars. Release after spawn, not before.
6. **Hooks must not block chat**: Lifecycle hooks (DailyActivity, auto-commit, distillation) fire via `BackgroundHookExecutor` (fire-and-forget). Never call hooks synchronously in the chat response path. Even the fallback path uses `asyncio.create_task`.

## Frontend Tab Isolation Invariants

7. **Capture tabId at call time, not closure time**: In async callbacks (especially `addFiles` with `await readFileAsBase64`), the closure-captured `tabId` goes stale if the user switches tabs during the await. Use a `useRef` mirror and read `.current` at each async boundary.
8. **Stream handlers capture tabId at creation**: `createStreamHandler`, `createErrorHandler`, `createCompleteHandler` all capture `tabId` when created. Background tab events write to `tabMapRef` only. Only the active tab's events update `useState`.
9. **setIsStreaming is synchronous**: `setIsStreaming(true)` synchronously mutates `tabMapRef.isStreaming` to close the race window between the guard check and the actual streaming start. The `pendingStreamTabs` Set covers the gap before `session_start`.

## Code Hygiene Rules

10. **Remove imports when removing usage**: When deleting a variable/function, always remove its import. TypeScript `noUnusedLocals` catches this at build time — never suppress, always fix.
11. **Remove re-exports when replacing hooks**: When a hook is replaced (e.g. `useFileAttachment` → `useUnifiedAttachments`), remove the old export from `hooks/index.ts` and delete the file.
12. **Bump test budgets when content grows**: Context files (AGENT.md) grow over time. Test assertions with hardcoded token limits must be updated when content legitimately grows. Guard against accidental bloat, not intentional growth.
13. **L1 cache budget-tier matching**: The L1 cache header stores the budget tier. When budget constants change, tests with hardcoded cache budget values must match the new constants or the cache is rejected as stale.
