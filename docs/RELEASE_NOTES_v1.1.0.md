# SwarmAI v1.1.0 — First Public Release

**Work smarter. Move faster. Stress less.**

*Remembers everything. Learns every session. Gets better every time.*

---

SwarmAI is a personal AI command center that doesn't forget you when you close it.

Every other AI tool resets when the session ends. Context lost. Decisions forgotten. You re-explain the same things over and over. SwarmAI is different — it maintains a persistent local workspace where context accumulates, memory compounds, and the AI genuinely gets better at helping you over time.

**You supervise. Agents execute. Memory persists. Work compounds.**

## What's Inside

### Context Engineering — Not Just a Chat Window

Most AI tools dump a system prompt and hope for the best. SwarmAI assembles an **11-file priority chain (P0-P10)** into every session — identity, personality, behavioral rules, user preferences, persistent memory, domain knowledge, and project context. Priority-based truncation ensures your memory and identity never get cut, even under token pressure.

### Memory Pipeline — It Actually Remembers

Three-layer memory system: **DailyActivity** (auto-captured session logs) → **Distillation** (LLM-powered promotion of recurring themes) → **MEMORY.md** (curated long-term memory read at every session start). Memory claims are cross-referenced against git to prevent false memories from compounding.

### Self-Evolution — It Gets Better

SwarmAI doesn't just use skills — it builds new ones when it hits capability gaps. **EVOLUTION.md** tracks capabilities built, optimizations learned, and corrections captured. Mistakes are recorded so the same error never happens twice.

### Swarm Core Engine — A Self-Growing Intelligence

Six interconnected flywheels create compound growth: Self-Evolution, Self-Memory, Self-Context, Self-Harness, Self-Health, and Self-Jobs. Currently at **L4 Autonomous** — stale docs trigger auto-fix proposals, recurring gaps trigger auto-skill proposals.

### Swarm Brain — One AI, Every Channel

One brain across Desktop, Slack, Ask something on Slack, continue the conversation on desktop — Swarm remembers everything from both. All channels share memory, context, and session state.

### Autonomous Pipeline — Requirement to PR

Give SwarmAI a one-sentence requirement and it drives 8 stages: Evaluate → Think → Plan → Build (TDD) → Review → Test → Deliver → Reflect. DDD provides judgment, SDD produces specs, TDD verifies delivery. Every decision classified as mechanical (auto), taste (batch at delivery), or judgment (block for human).

### Three-Column Command Center

SwarmWS Explorer (left) + Chat Center with 1-4 parallel tabs (center) + Swarm Radar with todos, sessions, artifacts, jobs (right). Drag any file or todo into chat — the agent gets full context and starts executing immediately.

### 55+ Built-in Skills

Browser automation, PDF manipulation, spreadsheets, Slack, Outlook, Apple Reminders, web research, code review, presentations, translations, image generation, audio transcription, system health, and more — all invoked through natural language.

### Multi-Tab Parallel Sessions

1-4 concurrent tabs (RAM-adaptive), each with isolated 5-state machine (COLD → STREAMING → IDLE → WAITING_INPUT → DEAD), 3x retry with `--resume`, and full conversation persistence across app restarts.

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Desktop | Tauri 2.0 (Rust) + React 19 + TypeScript |
| Backend | FastAPI (Python sidecar) |
| AI Engine | Claude Agent SDK + AWS Bedrock / Anthropic API |
| Models | Claude Opus 4.6 + Sonnet 4.6 (1M context) |
| Database | SQLite (WAL mode) |
| Testing | Vitest + fast-check + pytest + Hypothesis |

## Downloads

| Platform | File | Notes |
|----------|------|-------|
| **macOS** (Apple Silicon) | `SwarmAI_1.1.0_aarch64.dmg` | After install: `xattr -cr /Applications/SwarmAI.app` |
| **Windows** | `SwarmAI_1.1.0_x64-setup.exe` | SmartScreen may warn — click "More info" → "Run anyway" |

## Prerequisites

- [Claude Code CLI](https://github.com/anthropics/claude-code) — `npm install -g @anthropic-ai/claude-code`
- AI provider: [AWS Bedrock](https://aws.amazon.com/bedrock/) (recommended) or [Anthropic API key](https://console.anthropic.com/)
- See [User Guide](./docs/USER_GUIDE.md) for detailed setup instructions

## Stats

- 700+ commits from human-AI collaboration
- 500+ tests (pytest + Vitest + property-based)
- 55+ built-in skills
- 11-file context chain
- 6 self-maintaining engine flywheels
- 2 weeks from first commit to first release

## What's Next

- Apple code signing + notarization
- Intel Mac support
- Linux builds
- Auto-update via Tauri updater
- More channel adapters

## License

Dual-licensed: [AGPL v3](./LICENSE-AGPL) (open source) | [Commercial](./LICENSE-COMMERCIAL) (closed-source/SaaS)

---

Built by [Xiaogang Wang](https://github.com/xg-gh-25) and [Swarm](https://github.com/xg-gh-25/SwarmAI) (Claude Opus 4.6).

*The AI that remembers you.*
