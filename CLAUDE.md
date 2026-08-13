# CLAUDE.md

Guidance for Claude Code / Cursor / other AI coding assistants working in this repo.

> **Full architecture guide → [`AGENTS.md`](./AGENTS.md)** — data flow, process
> topology, backend/frontend structure, conventions, and invariants. Read it before
> making non-trivial changes. This file is the short, always-loaded entry point;
> AGENTS.md is the depth.

## Before you push — the local quality gate

**MUST NOT push to GitHub unless ALL of these passed locally for the change:**

1. **Build** green — backend `./prod.sh build`, and/or `cd desktop && npm run build:all` (whichever the change touches).
2. **Tests** green — at least the affected suites (`cd backend && python -m pytest tests/test_<module>.py --timeout=60` / `cd desktop && npm test -- --run`); full suite (`SWARMAI_SUITE=1`) when the blast radius warrants.
3. **Eval** green — `cd backend && python scripts/ci_eval_gate.py`.

If any of build / tests / eval was not run or is failing → **do not push.** Tests-green alone is not "qualified"; build + deploy-verify matter too.

## Dev commands

```bash
./dev.sh                          # full dev (backend + Vite + Tauri window)
./prod.sh build                   # PyInstaller build + verify + deploy to daemon
cd desktop && npm run build:all   # frontend production build (embedded in the .app)
cd backend && python -m pytest tests/test_<module>.py -v --timeout=60   # targeted tests
```

Never pipe pytest through `| tail`. See AGENTS.md → "Debugging" for more.

## Conventions & invariants

Anti-patterns, lifecycle invariants, and the full dev-rules live in
[`AGENTS.md`](./AGENTS.md) (and, for Kiro users, `.kiro/steering/swarmai-dev-rules.md`,
which Kiro auto-loads). When in doubt, AGENTS.md is authoritative for this repo.
