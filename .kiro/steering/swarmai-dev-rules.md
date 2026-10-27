---
inclusion: always
---

# SwarmAI Development Rules

Platform rules, coding anti-patterns, and lifecycle invariants for this repo.
Architecture, the push gate, build/test commands, and commit rules live in
`AGENTS.md` (also always loaded) — they are not repeated here.

## Repo Context (overrides global steering)

This is a PERSONAL GitHub project, NOT an Amazon Brazil/internal repo. The
global `amazon-builder/*` steering (Brazil, CRUX, Coral, Taskei, "never push",
internal production-safety) DOES NOT APPLY here. Workspace rules win on conflict.

Commit directly to `main` and `git push origin main` (no branches, no PRs) —
but ONLY after the build/tests/eval gate in AGENTS.md "Before You Push" is green.
Make every code change in your own git worktree, never in the shared main checkout
(AGENTS.md "Code changes outside a pipeline run").

## Platform Rules

- Port 18321 is FIXED (no portpicker, no dynamic allocation). Dev uses 8000.
- No `import fcntl` at module top level — use `from utils.file_lock import flock_exclusive`
- No `lsof` in scripts — use `nc -z 127.0.0.1 PORT` (lsof hangs on macOS)
- `/shutdown` returns 403 in daemon/hive mode — never kill background services via API
- Channels (Slack) only run in daemon/hive mode — mode guard in main.py

## API Naming Convention

- Backend: `snake_case` (Python/Pydantic)
- Frontend: `camelCase` (TypeScript)
- ALWAYS update the `toCamelCase()` mapper in `desktop/src/services/*.ts` when adding
  fields. A payload with two delivery routes (SSE + HTTP) needs ONE shared mapper — a
  TypeScript cast is not a conversion.
- Any field the frontend reads MUST exist on the Pydantic response model:
  `extra='ignore'` silently drops undeclared keys.

## Before Modifying Code

- Find the tests first: `grep -rn "function_name(" backend/tests/ --include="*.py"`
- Test/build commands and timeouts: AGENTS.md "Before You Push".

## Code Documentation Standards

ALL code files MUST have a module-level docstring/comment:

- **Python**: Triple-quoted docstring. One-line summary + bulleted list of key public symbols.
- **TypeScript/React**: `/** */` block comment. File purpose + key exports.
- **Test files**: What is tested, methodology, key properties/invariants.

## File Writing Method

- Create files with `fs_write` + `fs_append` in small chunks (~40 lines each); never write
  more than ~50 lines in one `fs_write`.
- NEVER use `execute_bash` with a heredoc (`cat > file << 'EOF'`) — it hangs the agent.
- For edits, prefer `str_replace` over rewriting the whole file.

## Regression-Prone Areas

Dedicated steering files with detailed invariants (auto-loaded via fileMatch when the
key files are in context). Consult BEFORE modifying:

| Area | Steering File | Key Files |
|------|--------------|-----------|
| Multi-tab chat isolation | `multi-tab-isolation-principles.md` | ChatPage.tsx, useChatStreamingLifecycle.ts, useUnifiedTabState.ts |
| Session identity & backend isolation | `session-identity-and-backend-isolation.md` | session_router.py, session_unit.py, lifecycle_manager.py, permission_manager.py, chat.py |
| Context & memory safety | `context-and-memory-safety.md` | context_directory_loader.py, prompt_builder.py, context_injector.py, hooks/*.py, locked_write.py |
| Self-evolution guardrails | `self-evolution-guardrails.md` | evolution_optimizer.py, evolution_maintenance_hook.py, s_self-evolution/* |

## Global Anti-Patterns

1. **Shared mutable state between sessions**: Never add module-level mutable state not keyed
   by session ID. If you add a per-session dict, clear it in
   `LifecycleManager._release_session_state()` or it grows without bound.
2. **React useState for cross-tab decisions**: Always read from `tabMapRef`. React state is
   a display mirror only (details in `multi-tab-isolation-principles.md`).
3. **Overwriting user files**: Never overwrite files with `user_customized=True` in `ensure_directory()`.
4. **Permission state**: per session only — the prompt board (`open_prompts(session_id)`),
   `approve_command(session_id, …)`. There is no global queue.
5. **Direct MEMORY.md writes**: Always use `locked_write.py`. Concurrent writes corrupt the file.
6. **Raw message writes on the frontend**: all message mutations go through the tab's
   `MessageStore` (e.g. `insertOptimisticMessages`), never a raw `setMessages`.

## Session Lifecycle Invariants

Violations cause orphan processes, failed stops, lost messages, or unresumable sessions.
One `SessionUnit` per app session ID lives in `SessionRouter._units`; details in
`session-identity-and-backend-isolation.md`.

1. **Stop = abort first, then stop, then recycle.** Frontend aborts the SSE fetch
   synchronously, then fires `POST /chat/stop` (fire-and-forget). A user Stop recycles
   the subprocess to COLD with `_sdk_session_id` kept (PIT01: a soft-interrupted CLI is
   poisoned). Never POST `/stop` on an SSE disconnect.
2. **Generation guards.** `interrupt()` captures `_send_generation` at entry and bails if
   a new send started. Never raw-`kill()` a STREAMING/WAITING_INPUT unit from release or
   close paths — use the generation-safe `interrupt()`.
3. **Locks.** `_client_io` is a short barrier at send's IDLE entry, never held across the
   stream; `interrupt()` and flush stay lock-free. `_spawn_lock` then `_env_lock` are held
   through `wrapper.__aenter__()` so the subprocess inherits the right `os.environ`.
4. **Persist before slot.** The user message is written to the DB before slot
   acquisition. On SESSION_BUSY / QUEUE_TIMEOUT it is converted to pending (never
   deleted) and the drain worker delivers it.
5. **Identity survives recycle.** Keep `_sdk_session_id` on every recycle/kill/shutdown
   path; clear it only on refresh, give-up, or a session-not-found `--resume`.
6. **Hooks never block chat.** Post-session hooks go through
   `LifecycleManager.enqueue_hooks` → `BackgroundHookExecutor` (fire-and-forget, one serial
   worker, per-hook timeout). Never call a hook synchronously from the chat path.

## Code Hygiene

1. **Remove imports when removing usage**: TypeScript `noUnusedLocals` catches this — never suppress.
2. **Remove re-exports when replacing hooks**: Delete old file + remove from `hooks/index.ts`.
3. **Bump test budgets when content grows**: Hardcoded token limits must match actual content size.
4. **L1 cache budget-tier matching**: Cache header budget must match current constants.

## Release & Version

- Source of truth: `VERSION` (root). `./scripts/sync-version.sh` syncs it to
  `backend/pyproject.toml`, `desktop/package.json`, `tauri.conf.json`, and `Cargo.toml`
  (the script's `TARGETS` array is authoritative).
- CI (`.github/workflows/ci.yml`) runs 5 jobs on push and PR: backend, frontend,
  backend-windows, version-check, security.
- Release: `commit → push → CI green → tag → release.yml` builds macOS arm64/x64, Windows x64
  and Linux x64 (AppImage), runs `scripts/verify_installer.sh` on each installer, and publishes a
  draft only when every target built and passed. It also builds the Hive package from the clean
  checkout (`hive/release.sh`, no workspace content; `verify_package.sh --public`; then
  `hive/smoke_package.sh` installs and starts it) and attaches it
  with its `.sha256`. Hives can download it only after the draft is published.
  `./hive/release.sh --with-live-ddd` adds this machine's DDD: local only, never published.
- Never tag with red CI.
