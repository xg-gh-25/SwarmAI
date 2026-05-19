# Contributing to SwarmAI

## Architecture at a Glance

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SwarmAI Architecture                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────┐    ┌─────────────────────────────────────────┐    │
│  │   Desktop   │    │            Backend (FastAPI)             │    │
│  │  React 19   │    │                                         │    │
│  │  Tauri 2.0  │◄──►│  ┌─────────┐  ┌──────────┐  ┌──────┐  │    │
│  │             │ SSE│  │ Session  │  │ Context  │  │ Chat │  │    │
│  └─────────────┘    │  │  Unit    │  │  Loader  │  │Router│  │    │
│                     │  └────┬─────┘  └────┬─────┘  └──┬───┘  │    │
│                     │       │             │            │       │    │
│                     │       ▼             ▼            ▼       │    │
│                     │  ┌─────────────────────────────────┐    │    │
│                     │  │      Claude Agent SDK (CLI)      │    │    │
│                     │  └─────────────────────────────────┘    │    │
│                     │                                         │    │
│                     │  ┌────────────────────────────────────┐ │    │
│                     │  │         Extensions Layer           │ │    │
│                     │  │  ┌──────┐ ┌──────┐ ┌───────────┐  │ │    │
│                     │  │  │Skills│ │ Jobs │ │  Pipeline  │  │ │    │
│                     │  │  │(68)  │ │System│ │(autonomous)│  │ │    │
│                     │  │  └──────┘ └──────┘ └───────────┘  │ │    │
│                     │  │  ┌──────┐ ┌──────┐ ┌───────────┐  │ │    │
│                     │  │  │Hooks │ │ DDD  │ │ Channels  │  │ │    │
│                     │  │  │(11)  │ │Engine│ │  (Slack)   │  │ │    │
│                     │  │  └──────┘ └──────┘ └───────────┘  │ │    │
│                     │  └────────────────────────────────────┘ │    │
│                     └─────────────────────────────────────────┘    │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                    Data Layer                                  │  │
│  │  SQLite (WAL) · ~/.swarm-ai/ filesystem · .context/ files    │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### Core vs Extensions

| Layer | LOC | What it does | Safe to ignore? |
|-------|-----|-------------|-----------------|
| **Core** (~11K) | `session_unit.py`, `session_router.py`, `context_directory_loader.py`, `prompt_builder.py`, `lifecycle_manager.py`, `chat.py`, `main.py` | Session state machine, context assembly, streaming, process lifecycle | No — this is the spine |
| **Extensions** (~90K) | `skills/`, `hooks/`, `jobs/`, `channels/`, `core/code_intel/`, `core/proactive_*.py` | All features — skills, jobs, DDD cultivation, code intelligence, channels | Yes — each is independent |
| **Frontend** (~67K) | `desktop/src/` | React UI, chat interface, workspace explorer | Only if touching UI |
| **Tests** (~76K) | `backend/tests/` | Full test coverage | Read when modifying core |

**The 80/20 rule:** Understanding 5 files (the Core) lets you reason about the entire system. The other 600+ files are extensions that plug into those 5.

### Five Core Files (Read These First)

| File | Lines | Role |
|------|-------|------|
| [`session_unit.py`](./backend/core/session_unit.py) | 3,048 | The state machine. One per chat session. COLD→STREAMING→IDLE→DEAD. |
| [`context_directory_loader.py`](./backend/core/context_directory_loader.py) | 1,058 | Assembles the 11-file context system into one system prompt. |
| [`session_router.py`](./backend/core/session_router.py) | 1,431 | Routes requests to sessions, manages slots, handles eviction. |
| [`routers/chat.py`](./backend/routers/chat.py) | 1,134 | SSE streaming endpoint. Frontend ↔ Backend contract. |
| [`main.py`](./backend/main.py) | 1,676 | FastAPI app setup, platform detection, scheduler, gateway startup. |

### Extension Points (Where New Features Go)

| Extension Point | How to Add | Example |
|-----------------|-----------|---------|
| **Skill** | Add folder to `backend/skills/s_<name>/` with `SKILL.md` | Any of the 68 existing skills |
| **Hook** | Add file to `backend/hooks/` implementing `run()` | `daily_activity_extraction.py` |
| **Job** | Add entry to `user-jobs.yaml` | Any scheduled background task |
| **Router** | Add file to `backend/routers/` + register in `main.py` | `code_intel.py` |
| **Channel** | Add adapter to `backend/channels/adapters/` | `slack_adapter.py` |

---

## Getting Started (5 minutes)

### Prerequisites

- macOS (primary), Linux (experimental), Windows (experimental)
- [Node.js](https://nodejs.org/) 18+
- [Python](https://www.python.org/) 3.11+
- [Rust](https://rustup.rs/) (latest stable)
- [uv](https://astral.sh/uv) (Python package manager)
- [Claude Code CLI](https://github.com/anthropics/claude-code) (`npm i -g @anthropic-ai/claude-code`)

### Clone & Run

```bash
# 1. Clone
git clone https://github.com/xg-gh-25/SwarmAI.git
cd SwarmAI

# 2. Backend setup (30 seconds)
cd backend
uv sync                    # installs all Python deps
cp .env.example .env       # edit this — set API credentials

# 3. Frontend setup (60 seconds)
cd ../desktop
npm install

# 4. Run everything
npm run tauri:dev          # starts backend + frontend + Tauri window
```

That's it. The app opens at `http://localhost:1420` with backend on port 18321.

### Backend-Only (for API/skill work)

```bash
cd backend
uv sync && source .venv/bin/activate
SWARMAI_MODE=dev python main.py    # port 8000, no channels/jobs
```

### Run Tests

```bash
# Backend (with timeout safety)
cd backend && pytest --timeout=60

# Frontend
cd desktop && npm test -- --run
```

---

## Good First Issues

Skills are the **best entry point** — each is self-contained, independently testable, and doesn't require understanding the core system.

### Skill Contribution (Easiest)

A skill is a folder with 1-2 files:

```
backend/skills/s_my-skill/
├── SKILL.md            # Metadata + description (10-20 lines)
└── INSTRUCTIONS.md     # Full workflow (optional, for complex skills)
```

**To create a skill:**

1. Copy any existing skill folder (e.g., `s_weather/` for a simple one)
2. Edit `SKILL.md` — set name, description, triggers
3. Write the workflow in `INSTRUCTIONS.md`
4. Test: open SwarmAI, type the trigger phrase, verify it works
5. Submit PR

**Skill ideas that would be welcome:**
- Docker/container management skill
- Git interactive rebase helper
- Changelog generator from commits
- API documentation generator
- Database migration helper

### Hook Contribution (Medium)

Hooks run automatically after every session. A hook is a single Python file:

```python
"""My hook — does X after each session."""

async def run(session_id: str, messages: list, **kwargs) -> dict:
    # Your logic here
    return {"status": "success", "summary": "Did X"}
```

### Bug Fixes & Improvements (Any Level)

Look for issues labeled `good first issue` on [GitHub Issues](https://github.com/xg-gh-25/SwarmAI/issues).

**Easy wins that don't touch core:**
- Improve skill descriptions (better trigger phrases)
- Add tests for untested utility functions
- Fix typos/docs
- Add new signal feed adapters (RSS, GitHub, etc.)

---

## Project Structure

```
SwarmAI/
├── backend/                    # Python FastAPI backend
│   ├── core/                   # ⭐ Core (11K LOC) — start here
│   │   ├── session_unit.py     # Session state machine
│   │   ├── session_router.py   # Request routing + slot management
│   │   ├── context_directory_loader.py  # 11-file context assembly
│   │   ├── prompt_builder.py   # System prompt construction
│   │   └── lifecycle_manager.py # Background health + TTL
│   ├── routers/                # API endpoints
│   │   ├── chat.py             # SSE streaming (main endpoint)
│   │   ├── sessions.py         # Session CRUD
│   │   └── code_intel.py       # Code intelligence API
│   ├── hooks/                  # Post-session automation (11 hooks)
│   ├── skills/                 # 68 built-in skills (self-contained)
│   │   ├── s_autonomous-pipeline/  # Coding pipeline
│   │   ├── s_pollinate/        # Content engine
│   │   ├── s_deep-research/    # Research skill
│   │   └── ...                 # Each is independent
│   ├── jobs/                   # Background job system
│   │   ├── scheduler.py        # Cron-based execution
│   │   ├── executor.py         # Job runner (Claude CLI headless)
│   │   └── models.py           # Job/Feed/State definitions
│   ├── channels/               # External integrations (Slack)
│   ├── database/               # SQLite + migrations
│   └── tests/                  # pytest suite (76K LOC)
├── desktop/                    # Tauri 2.0 + React 19 frontend
│   ├── src/                    # React source (67K LOC)
│   │   ├── pages/              # ChatPage, Settings, Skills
│   │   ├── hooks/              # React hooks (streaming, tabs)
│   │   ├── services/           # API layer (camelCase ↔ snake_case)
│   │   └── components/         # UI components
│   └── src-tauri/              # Rust lifecycle (2K LOC)
├── docs/                       # Design docs + post-mortems
└── assets/                     # Screenshots, diagrams, SVGs
```

---

## Development Workflow

### Branch & PR

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Make changes with clear, atomic commits
4. Run tests: `cd backend && pytest --timeout=60`
5. Submit a PR with description of what + why

### Commit Messages

[Conventional Commits](https://www.conventionalcommits.org/):

```
feat(skills): add docker management skill
fix(session): prevent race condition on concurrent send
docs: improve contributing guide
test: add property tests for context loader
```

### Code Style

| Area | Standard | Check |
|------|----------|-------|
| Python | Ruff, type hints required, module docstrings | `ruff check backend/` |
| TypeScript | ESLint, camelCase, functional components | `cd desktop && npm run lint` |
| API | Backend snake_case, frontend camelCase, transform in services/ | Manual review |

---

## FAQ

**Q: The codebase is huge (170K+ LOC). Where do I start?**
A: Read the [5 core files](#five-core-files-read-these-first) (~11K total). Everything else is extensions that plug into them. Skills are the gentlest entry point — each is 50-200 lines and fully independent.

**Q: Do I need to understand the whole system to contribute a skill?**
A: No. Skills are self-contained folders. You don't need to know how sessions, context, or streaming work. Just write `SKILL.md` + `INSTRUCTIONS.md` and test via the chat UI.

**Q: What's the test philosophy?**
A: Targeted tests with `--timeout=60`. Never run the full suite without `SWARMAI_SUITE=1` (known xdist issues). For a new skill, no test required — for core changes, tests are mandatory.

**Q: macOS only?**
A: macOS + Hive (EC2 Linux) are production-grade. Windows and Linux Desktop are experimental — CI runs smoke tests but no active test environment.

---

## License

[MIT License](./LICENSE). By contributing, you agree your work is licensed under the same terms.
