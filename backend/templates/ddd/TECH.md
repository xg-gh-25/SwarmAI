# SwarmAI -- Technical Context

## Architecture

Desktop app with three layers: a Tauri 2.0 shell (Rust), a React frontend (TypeScript), and a Python FastAPI backend running as a sidecar process. The backend spawns Claude Agent SDK subprocesses for AI capabilities via AWS Bedrock.

```
+------------------------------------------+
|  Tauri Shell (Rust)                       |
|  - Window management, native APIs        |
|  - Sidecar lifecycle (start/stop/health) |
|  - Random port assignment (portpicker)   |
+------------------------------------------+
         |                    |
         v                    v
+-----------------+  +------------------------+
| React Frontend  |  | Python Backend Sidecar |
| - Chat UI       |  | - FastAPI + asyncio    |
| - Workspace     |  | - Session management   |
|   Explorer      |  | - Claude Agent SDK     |
| - Radar/ToDo    |  |   (CLI subprocess)     |
| - Settings      |  | - SQLite (WAL mode)    |
| - SSE streaming |  | - Skill loader         |
+-----------------+  | - MCP server manager   |
                     | - Context pipeline     |
                     +------------------------+
                              |
                     +------------------------+
                     | MCP Servers (external)  |
                     | - GitHub, Slack, etc.   |
                     | - stdio / SSE / HTTP    |
                     +------------------------+
```

## Stack

| Layer | Technology |
|-------|-----------|
| **Shell** | Tauri 2.0 (Rust) |
| **Frontend** | React 18, Vite 6, TanStack Query, Tailwind CSS, CodeMirror 6 |
| **Backend** | Python 3.12, FastAPI, asyncio, Pydantic v2 |
| **AI** | Claude Agent SDK, Claude 4.6 (Opus/Sonnet) via AWS Bedrock, 1M context window |
| **Database** | SQLite (WAL mode) at `~/.swarm-ai/data.db` |
| **Testing** | pytest + Hypothesis (backend), vitest (frontend) |
| **Build** | PyInstaller (backend bundle), Tauri CLI (app package) |
| **License** | AGPL v3 + Commercial dual-license |

## Codebase Location

- **Local:** `/Users/gawan/Desktop/SwarmAI-Workspace/swarmai/`
- **GitHub:** https://github.com/xg-gh-25/SwarmAI
- **Clone:** `git clone https://github.com/xg-gh-25/SwarmAI.git`

## Dev Commands

```bash
# Full dev (starts backend + Vite + Tauri window):
cd desktop && npm run tauri:dev
# or from project root:
./dev.sh

# Backend only (after Python changes):
./dev.sh backend

# Frontend tests:
cd desktop && npm test -- --run

# Backend tests:
cd backend && pytest

# Production build:
cd desktop && npm run build:all
```

## Key Subsystems

### Session System (v7)

4-component architecture replacing the original monolithic AgentManager:

| Component | File | Responsibility |
|-----------|------|---------------|
| **SessionRouter** | `session_router.py` | Slot acquisition, IDLE eviction, queue timeout (60s). Maps session_id to SessionUnit. |
| **SessionUnit** | `session_unit.py` | 5-state machine (COLD/STREAMING/IDLE/WAITING_INPUT/DEAD). Subprocess spawn, 3x retry with `--resume`, streaming. |
| **LifecycleManager** | `lifecycle_manager.py` | Background loop (60s). TTL kill (12hr), health check, orphan reaper. |
| **SessionRegistry** | `session_registry.py` | Module singletons. Wires all components at startup. |

Key invariants: MAX_CONCURRENT=2, protected states (STREAMING, WAITING_INPUT) never evicted, retry uses `--resume` for conversation continuity.

### Context System

11 context files (P0-P10) assembled into the system prompt with token budget enforcement:

| Priority | File | Domain |
|----------|------|--------|
| P0 | SWARMAI.md | Core identity (never truncated) |
| P1 | IDENTITY.md | Agent name, avatar |
| P2 | SOUL.md | Personality, tone |
| P3 | AGENT.md | Behavioral directives |
| P4 | USER.md | User preferences |
| P5 | STEERING.md | Session overrides |
| P6 | TOOLS.md | Tool guidance |
| P7 | MEMORY.md | Cross-session memory |
| P8 | EVOLUTION.md | Self-evolution registry |
| P9 | KNOWLEDGE.md | Domain knowledge |
| P10 | PROJECTS.md | Active projects index |

Pipeline: `ContextDirectoryLoader` (L1 cache, budget tiers) -> `PromptBuilder` (DailyActivity, metadata) -> `SystemPromptBuilder` (identity, safety, datetime).

### Autonomous Pipeline (AIDLC)

`s_autonomous-pipeline` -- full lifecycle orchestrator from requirement to delivery:

| Component | Location | Purpose |
|-----------|----------|---------|
| **SKILL.md** | `backend/skills/s_autonomous-pipeline/` | 8-stage behavioral loop with TDD, decisions, delivery gate |
| **artifact_cli.py** | `backend/scripts/` | 13 CLI commands: publish, discover, run-*, status, resume |
| **pipeline_validator.py** | `backend/scripts/` | 6 structural invariant checks after each stage |
| **pipeline_profiles.py** | `backend/core/` | 5 profiles: full, trivial, research, docs, bugfix |
| **pipelines.py** | `backend/routers/` | GET /api/pipelines dashboard endpoint |

**Methodology:** DDD (should we?) -> SDD (what exactly?) -> TDD (did we?).
**TDD in BUILD:** RED (generate tests from acceptance criteria, all fail) -> GREEN (code until pass) -> VERIFY (full suite, 0 regressions). Fix code, not tests.

### Swarm Core Engine (Self-Growing Intelligence)

The Core Engine is six flywheels feeding each other -- the compound loop that makes Swarm grow smarter over time. All product-level code in `backend/`.

```
Session -> Memory captures -> Evolution detects patterns -> Harness verifies
   ^       -> Context assembles smarter prompts -> Next session better     |
   |_______________________________________________________________________|
```

**Six Flywheels:**

| Flywheel | Key Components | Location |
|----------|----------------|----------|
| **Self-Evolution** | EVOLUTION.md, evolution hooks, gap detection, correction registry | `hooks/evolution_*.py`, `skills/s_self-evolution/` |
| **Self-Memory** | 3-layer distillation, LLM weekly pruning (Haiku), proactive briefing | `hooks/daily_activity_hook.py`, `hooks/distillation_hook.py`, `jobs/handlers/memory_health.py` |
| **Self-Context** | 11-file P0-P10 chain, token budgets, L0/L1 cache, 4-tier ownership | `core/context_directory_loader.py`, `core/prompt_builder.py` |
| **Self-Harness** | Context validation (light + deep), DDD staleness, index refresh | `hooks/context_health_hook.py` |
| **Self-Health** | Service monitoring, auto-restart, resource diagnostics, health alerting | `core/service_manager.py`, `core/resource_monitor.py`, `core/proactive_intelligence.py` |
| **Self-Jobs** | Scheduler, executor, system jobs, signal pipeline, adapters, self-tune | `jobs/` package (16 modules), `routers/jobs.py` |

**Context file ownership model** (enforced in `context_directory_loader.py`):

| Category | Files | Source of Truth | Write Access |
|----------|-------|-----------------|--------------|
| System-owned | SWARMAI, IDENTITY, SOUL, AGENT | `backend/context/` (codebase template) | Code changes only |
| User-owned | USER, STEERING, TOOLS | `.context/` (workspace) | User edits freely |
| Agent-owned | MEMORY, EVOLUTION | `.context/` (workspace) | Agent via hooks/locked_write |
| Auto-generated | KNOWLEDGE, PROJECTS | `.context/` (workspace) | Rebuilt from filesystem |

### Job System (`backend/jobs/`)

Product-level background automation. System jobs in code, user jobs in YAML.

| Component | File | Purpose |
|-----------|------|---------|
| **scheduler.py** | Core scheduler | Evaluate due jobs, execute, save state |
| **executor.py** | Job dispatcher | Routes to handlers: signal_fetch, digest, agent_task, script, maintenance |
| **system_jobs.py** | System jobs | Code definitions (signal-fetch, digest, self-tune, maintenance, rollup) |
| **handlers/** | signal_fetch, signal_digest, memory_health | Feed adapters, LLM digest, weekly LLM maintenance |
| **adapters/** | RSS, HN, GitHub, web search | httpx-based feed fetchers |
| **paths.py** | Centralized paths | SWARMWS, STATE_FILE, CONFIG_FILE, etc. |

API: `GET /api/jobs/` (list), `POST /api/jobs/run` (force-run), `GET /api/jobs/status` (overview).
Scheduler: single launchd plist (`com.swarmai.scheduler`), hourly trigger.
Sidecar services (e.g. Slack bot): managed by `service_manager.py`, lifecycle tied to app.

### Skill System

3-tier skill loading: built-in (`backend/skills/`), user (`~/.swarm-ai/skills/`), plugin. Each skill is a directory with a `SKILL.md` file that defines trigger patterns, workflow steps, and tool usage. 50+ skills ship built-in.

### MCP Subsystem

External tool servers via Model Context Protocol (stdio/SSE/HTTP). 2-layer file-based config: `mcp-dev.json` for development, `mcp-config.json` for production. Supports GitHub, Slack, Outlook, Sentral, and custom servers.

### Workspace System

SwarmWS (`~/.swarm-ai/SwarmWS/`) is the agent's working directory. Git-tracked filesystem with:
- `Knowledge/` -- Notes, Reports, Meetings, Library, Archives, DailyActivity
- `Projects/` -- DDD-structured project contexts (this directory)
- `Services/` -- Sidecar service definitions (hidden from explorer)
- `Attachments/` -- File uploads and exports
- `.context/` -- 11 context files loaded into system prompt

## File Structure Quick Reference

```
backend/
  main.py                              # FastAPI entry point
  config.py                            # App configuration
  core/
    session_router.py                  # Session routing + slot management
    session_unit.py                    # Per-session state machine
    lifecycle_manager.py               # Background health + TTL
    session_registry.py                # Component wiring
    context_directory_loader.py        # Context file assembly
    prompt_builder.py                  # Prompt composition pipeline
    skill_manager.py                   # Skill discovery + loading
    service_manager.py                 # Sidecar service lifecycle
    mcp_config_loader.py               # MCP server configuration
    swarm_workspace_manager.py         # Workspace provisioning + project CRUD
    proactive_intelligence.py          # Session briefings (L0-L4)
  jobs/                                # Background job system (16 modules)
  routers/                             # FastAPI route handlers
  hooks/                               # Post-session lifecycle hooks
  skills/                              # Built-in skills (50+)
  templates/ddd/                       # DDD document templates
  schemas/                             # Pydantic models

desktop/src/
  pages/ChatPage.tsx                   # Tab orchestration + message rendering
  hooks/useChatStreamingLifecycle.ts   # SSE event processing
  hooks/useUnifiedTabState.ts          # Tab CRUD + persistence
  services/chat.ts                     # SSE connection + backend API
  contexts/ExplorerContext.tsx          # Workspace tree + polling
  components/                          # UI components
```

## Conventions

- **Backend (Python):** snake_case for everything. Pydantic models with `model_config = ConfigDict(from_attributes=True)`.
- **Frontend (TypeScript):** camelCase. Always update `toCamelCase()` in `desktop/src/services/*.ts` when adding API fields.
- **API boundary:** Backend sends snake_case, frontend receives and converts to camelCase.
- **Files:** Date-prefixed for sortability: `YYYY-MM-DD-description.md`
- **Commits:** Conventional format. Co-authored with Swarm.
- **Testing:** Property-based (Hypothesis/fast-check) preferred over example-based. All new code needs tests.
- **Modules >500 lines:** Strangler fig pattern for refactoring. No big-bang rewrites.

## Environment Notes

- Backend port is **random each launch** (Tauri portpicker). Never hardcode ports.
- Claude Agent SDK spawns a CLI subprocess per session. Each CLI + its MCPs costs ~500MB RAM.
- SQLite in WAL mode at `~/.swarm-ai/data.db`. Direct access from agent sandbox is reliable for CRUD.
- Two independent credential chains may coexist: Claude CLI uses AWS SSO IdC tokens, boto3 may use credential_process. Validate the chain your code actually uses.
- Claude Code IS the local proxy when running inside the agent sandbox. Strip proxy vars when spawning subprocesses that manage their own networking.
