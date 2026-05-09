---
inclusion: always
---

# SwarmAI Development Rules

Rules for writing code in this project. For architecture overview, see `AGENTS.md`.

## Platform Rules

- Port 18321 is FIXED (no portpicker, no dynamic allocation). Dev uses 8000.
- No `import fcntl` at module top level — use `from utils.file_lock import flock_exclusive`
- No `lsof` in scripts — use `nc -z 127.0.0.1 PORT` (lsof hangs on macOS)
- `/shutdown` returns 403 in daemon/hive mode — never kill background services via API
- Channels (Slack) only run in daemon/hive mode — mode guard in main.py

## API Naming Convention

- Backend: `snake_case` (Python/Pydantic)
- Frontend: `camelCase` (TypeScript)
- ALWAYS update `toCamelCase()` functions in `desktop/src/services/*.ts` when adding fields

## Development Commands

```bash
./dev.sh                        # Full dev: backend (port 8000) + frontend + Tauri
./dev.sh backend                # Backend only
./prod.sh build                 # PyInstaller + verify + deploy to daemon
./prod.sh release               # Full release cycle
./prod.sh status                # Daemon health + versions
cd desktop && npm run build:all # Frontend production build
```

## Test Execution Rules

- NEVER run full test suite proactively — xdist deadlock risk
- Targeted: `cd backend && python -m pytest tests/test_<module>.py -v --timeout=60`
- Last-failed: `cd backend && python -m pytest --lf --timeout=60`
- Full suite only with explicit request: `SWARMAI_SUITE=1 python -m pytest --timeout=120`
- Before modifying a function: `grep -rn "function_name(" tests/ --include="*.py"`
- NEVER pipe pytest through `| tail` — causes buffering issues
- Frontend: `cd desktop && npm test -- --run`

## Code Documentation Standards

ALL code files MUST have a module-level docstring/comment:

- **Python**: Triple-quoted docstring. One-line summary + bulleted list of key public symbols.
- **TypeScript/React**: `/** */` block comment. File purpose + key exports.
- **Test files**: What is tested, methodology, key properties/invariants.

## File Writing Method

- ALWAYS use `fsWrite` + `fsAppend` (small chunks, ~40 lines each)
- NEVER use `executeBash` with heredoc (`cat > file << 'EOF'`) — hangs the agent
- NEVER write files larger than ~50 lines in a single `fsWrite` — split into chunks
- For edits: prefer `strReplace` over rewriting the whole file

## Regression-Prone Areas

Dedicated steering files with detailed invariants. Consult BEFORE modifying:

| Area | Steering File | Key Files |
|------|--------------|-----------|
| Multi-tab chat isolation | `multi-tab-isolation-principles.md` | ChatPage.tsx, useChatStreamingLifecycle.ts, useUnifiedTabState.ts |
| Session identity & backend isolation | `session-identity-and-backend-isolation.md` | session_router.py, session_unit.py, session_registry.py, permission_manager.py, chat.py |
| Context & memory safety | `context-and-memory-safety.md` | context_directory_loader.py, context_injector.py, system_prompt.py, hooks/*.py, locked_write.py |
| Self-evolution guardrails | `self-evolution-guardrails.md` | s_self-evolution/*, EVOLUTION.md, chat.py, evolution_maintenance_hook.py |

## Global Anti-Patterns

1. **Shared mutable state between sessions**: Never add module-level mutable state not keyed by session ID.
2. **React useState for cross-tab decisions**: Always read from `tabMapRef`. React state is display mirror only.
3. **Overwriting user files**: Never overwrite files with `user_customized=True` in `ensure_directory()`.
4. **Global permission queue**: Use `get_session_queue(session_id)`, not deprecated `get_permission_queue()`.
5. **Direct MEMORY.md writes**: Always use `locked_write.py`. Concurrent writes corrupt the file.
6. **Heredoc file writes**: Use `fsWrite` + `fsAppend`, not `cat > file << 'EOF'`.

## Session Lifecycle Invariants

Violations cause orphan processes, failed stops, lost messages, or unresumable sessions.

1. **Transient vs persistent stores**: `_clients` is transient (popped after streaming). `_active_sessions` is persistent (2h TTL). Code outside the streaming loop MUST check `_active_sessions` first.
2. **Early registration**: Register client in `_active_sessions` BEFORE streaming, not after.
3. **Abort-first, stop-second**: Frontend aborts fetch THEN sends stop. `finally` pops `_clients` before stop arrives.
4. **Deferred save**: Don't save user message until client path (PATH A vs B) is determined.
5. **`_env_lock` through spawn**: Hold lock through `wrapper.__aenter__()` so subprocess inherits correct env.
6. **Hooks never block chat**: Fire via `BackgroundHookExecutor` (fire-and-forget). Never synchronous.

## Frontend Tab Isolation Invariants

7. **Capture tabId at call time**: In async callbacks, closure-captured tabId goes stale on tab switch. Use `useRef` mirror.
8. **Stream handlers capture tabId at creation**: Background tab events write to `tabMapRef` only.
9. **setIsStreaming is synchronous**: Mutates `tabMapRef.isStreaming` immediately to close race window.

## Code Hygiene

10. **Remove imports when removing usage**: TypeScript `noUnusedLocals` catches this — never suppress.
11. **Remove re-exports when replacing hooks**: Delete old file + remove from `hooks/index.ts`.
12. **Bump test budgets when content grows**: Hardcoded token limits must match actual content size.
13. **L1 cache budget-tier matching**: Cache header budget must match current constants.

## Release & Version

- Source of truth: `VERSION` (root). Synced to 5 other files via `./scripts/sync-version.sh`
- CI: 4 jobs on every push to main (backend, backend-windows, frontend, version-check)
- Release: `commit → push → CI green → tag → release.yml builds DMG + Windows + Hive`
- Never tag with red CI. No branches. No PRs.
