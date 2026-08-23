# SwarmAI -- Technical Context

> **This is section ② Knowledge of a six-section DDD** (① Identity ② Knowledge
> ③ Gates ④ Capabilities ⑤ Delivery ⑥ Refresher). This doc answers *"Can we?"* —
> architecture, stack, and constraints. Replace with YOUR project's technical context.
> Keep it at architecture-shape depth; deep subsystem internals belong in the grown
> DDD, not this starter seed.

## Architecture

Desktop app with three layers: a Tauri 2.0 shell (Rust), a React frontend (TypeScript),
and a Python FastAPI backend running as a launchd daemon (24/7). The backend spawns
Claude Agent SDK subprocesses for AI capabilities via AWS Bedrock.

```
+------------------------------------------+
|  Tauri Shell (Rust)                       |
|  - Window management, native APIs        |
|  - Backend lifecycle (start/stop/health) |
+------------------------------------------+
         |                    |
         v                    v
+-----------------+  +------------------------+
| React Frontend  |  | Python Backend          |
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
| **Frontend** | React 19, Vite 7, TanStack Query 5, Tailwind CSS 4 |
| **Backend** | Python 3.11+, FastAPI, asyncio, Pydantic v2 |
| **AI** | Claude Agent SDK via AWS Bedrock, 1M-context models |
| **Database** | SQLite (WAL mode) at `~/.swarm-ai/data.db` |
| **Testing** | pytest + Hypothesis (backend), vitest (frontend) |
| **Build** | PyInstaller (backend bundle), Tauri CLI (app package) |
| **License** | MIT |

> **Model choice is a mechanism, not a pinned version.** The active model is resolved at
> runtime from `config.json` `default_model` (settable in the Settings UI), falling back
> to the code default in `app_config_manager.py`. Never hardcode a model version in a
> cognitive store — it drifts (record the resolution mechanism instead).

## Codebase Location

- **Local:** the source repository working tree
- **GitHub:** https://github.com/xg-gh-25/SwarmAI

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

## Key Subsystems (architecture shape)

### Session System

A multi-component architecture (router → per-session unit → lifecycle manager →
registry) replacing an earlier monolithic manager. Each session is a state machine
(cold → streaming → idle → waiting-input → dead) that owns its own subprocess spawn,
retry-with-resume, and streaming. Key invariants:

- **Backend admission is gated by real-RAM spawn budget**, not a fixed tab count — the
  system admits a new session when memory allows, and applies a concurrent-streaming cap
  separately. (Any UI "max tabs" number is a frontend affordance, not the backend gate.)
- **Protected states are never evicted** — a streaming or input-waiting session is never
  killed to reclaim a slot.
- **Retry uses `--resume`** to restore conversation context across a respawn.

### Context System

The governed context files are assembled into the system prompt with a live token
budget. The set is an explicit allowlist (a file absent from it is never injected). Slots
are priority-ordered; the highest-priority identity/personality files are never
truncated, and size is bounded on the WRITE side (distillation / archival valves) rather
than by injection-time truncation.

Ownership model:

| Category | Source of Truth | Write Access |
|----------|-----------------|--------------|
| System-owned (identity, personality, rules) | codebase template (`backend/context/`) | code changes only |
| User-owned (profile, steering, tools) | workspace `.context/` | user edits freely |
| Agent-owned (memory, evolution) | workspace `.context/` | agent via governed writes |
| Auto-generated (knowledge index) | workspace `.context/` | rebuilt from filesystem |

### Autonomous Pipeline (AIDLC)

`s_autonomous-pipeline` — full lifecycle orchestrator from requirement to delivery.
Methodology: DDD (*should we?*) → SDD (*what exactly?*) → TDD (*did we?*). The pipeline
selects a **profile** (full / trivial / research / docs / bugfix / goal) that determines
the stage set, and runs through **three quality gates**: framing (diagnose-before-build),
plan (skeptic + structural-vs-symptom), and an adversarial build review before delivery.
BUILD is test-first: RED (tests from acceptance criteria) → GREEN (code until pass) →
VERIFY (no regressions). Fix code, not tests.

### Self-Growing Intelligence

The compound loop: a session happens → memory captures it → evolution detects recurring
patterns → the harness verifies accuracy → context assembles smarter prompts → the next
session performs better. Background jobs (scheduled, independent of any chat) and
between-session hooks keep the loop turning without being asked.

## Conventions

- **Backend (Python):** snake_case; Pydantic models with `from_attributes=True`.
- **Frontend (TypeScript):** camelCase; keep the API-boundary converter in sync when
  adding fields.
- **API boundary:** backend sends snake_case, frontend converts to camelCase.
- **Files:** date-prefixed for sortability: `YYYY-MM-DD-description.md`.
- **Commits:** conventional format.
- **Testing:** property-based (Hypothesis / fast-check) preferred; new code needs tests.
- **Large modules:** strangler-fig for refactoring — no big-bang rewrites.

## Runtime Traps

_Environment-specific gotchas, non-obvious failure modes, and "it works locally but not
X" traps. DDD cultivation routes technical trap/daemon/env/path lessons here — this
section MUST exist in the scaffold or cultivation auto-creates it per-project (template
drift)._

_No entries yet. Entries are added as runtime traps are discovered._

## Environment Notes

- The Claude Agent SDK spawns a CLI subprocess per session; each subprocess plus its MCP
  servers costs on the order of a few hundred MB of RAM — the driver behind the real-RAM
  spawn budget above.
- SQLite runs in WAL mode at `~/.swarm-ai/data.db`.
- Multiple credential chains may coexist (e.g. an SSO/IdC chain for the CLI vs a separate
  chain for direct SDK calls) — validate the chain your code actually uses.
