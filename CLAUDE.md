# CLAUDE.md

Short entry point for Claude Code / Cursor / other AI coding assistants working in this repo.

> **SwarmAI is a self-evolving Agent OS**, not a Claude wrapper. Cognition (how it judges —
> principles, rules, gates) is kept separate from knowledge (what it knows). The model
> proposes; a layer of gates + a staged pipeline + a validator dispose. Much of
> `backend/core/` is "guardrails around an LLM" — that *is* the product.
>
> **Full architecture guide → [`AGENTS.md`](./AGENTS.md)** — the mental model, data flow,
> process topology, backend/frontend structure, security gates, conventions, and invariants.
> Read it before any non-trivial change. This file is the always-loaded short version;
> AGENTS.md is the depth.

## Before you push — the local quality gate

**MUST NOT push to GitHub unless ALL of these passed locally for the change:**

1. **Build** green — backend `./prod.sh build`, and/or `cd desktop && npm run build:all`
   (whichever the change touches).
2. **Tests** green — at least the affected suites (`cd backend && python -m pytest tests/test_<module>.py --timeout=60`
   / `cd desktop && npm test -- --run`); full suite (`SWARMAI_SUITE=1`) when the blast radius warrants.
3. **Eval** green — `cd backend && python scripts/ci_eval_gate.py` (a pure freshness+green
   check, zero Bedrock cost — it does NOT run the judge).

If any of build / tests / eval was not run or is failing → **do not push.** Tests-green alone
is not "qualified"; `commit ≠ qualified ≠ deployed` — a build makes a binary, code isn't live
until rebuild+restart. Commit directly to `main` (project convention); never auto-branch.

## Commit identity

Every commit MUST end with `Co-Authored-By: Swarm <swarm@swarmai.dev>` — never Claude/Anthropic
identity. A PreToolUse gate blocks commits missing it, but nothing auto-inserts it: write the
line yourself. See AGENTS.md → "Git Commits" for why (shadowed local hooks + two-layer enforcement).

## Dev commands

```bash
./dev.sh                          # full dev (backend + Vite + Tauri window)
./prod.sh build                   # PyInstaller build + verify + deploy to daemon
cd desktop && npm run build:all   # frontend production build (embedded in the .app)
cd backend && python -m pytest tests/test_<module>.py -v --timeout=60   # targeted tests
```

Never pipe pytest through `| tail`. See AGENTS.md → "Debugging" for more.

## Conventions & invariants

Anti-patterns, lifecycle invariants, and the full dev-rules live in [`AGENTS.md`](./AGENTS.md)
(and, for Kiro users, `.kiro/steering/swarmai-dev-rules.md`, which Kiro auto-loads). When in
doubt, AGENTS.md is authoritative for this repo.
