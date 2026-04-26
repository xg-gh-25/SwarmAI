<div align="center">

# SwarmAI

### Your AI Team, 24/7

*The AI assistant that remembers everything, learns from every session, and gets better every time you use it.*

English | [中文](./README.zh-CN.md)

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![React](https://img.shields.io/badge/React-19-61DAFB?style=flat&logo=react&logoColor=white)](https://react.dev/)
[![Tauri](https://img.shields.io/badge/Tauri-2.0-FFC131?style=flat&logo=tauri&logoColor=white)](https://tauri.app/)
[![Claude](https://img.shields.io/badge/Claude-Opus_4.6-191919?style=flat&logo=anthropic&logoColor=white)](https://github.com/anthropics/claude-code)
[![License](https://img.shields.io/badge/License-AGPL_v3-blue.svg?style=flat)](./LICENSE-AGPL)

</div>

---

## Every AI assistant forgets you when you close it. SwarmAI doesn't.

Most AI tools are goldfish — brilliant in the moment, blank the next session. You re-explain your codebase. You repeat your preferences. You lose decisions made last week.

SwarmAI is different. It maintains a **persistent local workspace** where context accumulates, memory compounds, and the AI genuinely improves over time. Not through fine-tuning — through structured knowledge that survives every restart.

After 30 days of use, SwarmAI knows your projects, your coding style, your preferred tools, your open threads, and the mistakes it made (so it never makes them again).

**You supervise. Agents execute. Memory persists. Work compounds.**

---

## Why SwarmAI

<table>
<tr>
<td width="50%">

### 🧠 It Actually Remembers

4-layer memory: curated Brain for fast decisions + raw transcript search for precision recall. Ask "what was the exact error from last week?" and it finds the verbatim answer across 1,500+ session transcripts.

- Auto-captures decisions, lessons, corrections
- Weekly LLM-powered distillation (keeps what matters, prunes what doesn't)
- Temporal validity — stale decisions auto-downweighted
- Git-verified accuracy (memory claims checked against codebase)

</td>
<td width="50%">

### 🔄 It Gets Better Automatically

Closed-loop self-evolution: observes your corrections → measures skill performance → auto-optimizes underperforming skills using Opus LLM. The first AI assistant that debugs *itself*.

- 65+ built-in skills (browser, PDF, Slack, Outlook, research, code review, media...)
- LLM-powered skill optimizer (not blind text append — semantic understanding)
- Confidence-gated deployment with automatic rollback
- Correction registry — every mistake captured, never repeated

</td>
</tr>
<tr>
<td width="50%">

### 📋 It Knows Your Projects

4-document DDD system per project gives the AI autonomous judgment: *Should we build this? Can we? Have we tried before? Should we do it now?*

- ROI scoring before committing resources
- Decision classification (mechanical / taste / judgment)
- 8-stage autonomous pipeline: requirement → PR in one command
- Escalation protocol — acts within competence, escalates outside it

</td>
<td width="50%">

### 🖥️ It's a Command Center, Not a Chat Box

Three-column desktop app with parallel sessions, not a single chat thread.

- 1-4 concurrent tabs (RAM-adaptive) with isolated state
- Workspace explorer with git integration
- Radar dashboard: todos, jobs, artifacts
- Drag-to-chat: drop any file or todo for instant context
- Slack integration: same brain, same memory, any channel

</td>
</tr>
<tr>
<td width="50%">

### ⚡ Autonomous Coding Pipeline

One sentence → PR-ready code in 8 stages. EVALUATE gates bad ideas before wasting effort. TDD writes tests first. REVIEW catches cross-boundary bugs. REFLECT compounds lessons permanently.

- EVALUATE → THINK → PLAN → BUILD (TDD) → REVIEW → TEST → DELIVER → REFLECT
- Every decision classified: mechanical (auto), taste (batch), judgment (block)
- DDD-driven ROI scoring before committing resources
- Self-improving: each run's lessons feed the next run's review checklists

</td>
<td width="50%">

### 🎬 Pollinate — Media Value Delivery

Transform any message into optimized media: posters, short videos, podcasts, narratives. Your message, their attention, the right format.

- 8-stage content pipeline with confidence scoring
- Multi-format: poster (SVG/PNG), short video (4K MP4), podcast (TTS + BGM), narrative
- Template-driven: production-quality layouts per format × audience
- Publishing scripts for multi-platform distribution

</td>
</tr>
</table>

---

## See It In Action
![SwarmAI Home](./assets/swarm-1.png)

![SwarmAI Chat Interface](./assets/swarm-2.png)

**Real examples from production use:**

| What You Say | What Happens |
|---|---|
| "Remember that we chose FastAPI over Flask" | Saved to persistent memory. Every future session knows. |
| "What did we decide about the auth design?" | Searches 4-layer memory + 1,500 transcripts. Finds the exact conversation. |
| "Build retry logic for the payment API" | 8-stage pipeline: evaluate → design → TDD (tests first) → review → deploy. |
| "Check my email and create todos" | Reads Outlook inbox, creates Radar todos with full context packets. |
| *You correct the AI* | Correction captured. Skill auto-optimized next cycle. Same mistake never happens again. |

![SwarmAI Workspace](./assets/swarm-3.png)

---

## Architecture — Six Self-Growing Flywheels

<div align="center">
<img src="./assets/swarmai-architecture.svg" alt="SwarmAI Architecture" width="900"/>
</div>

SwarmAI isn't a feature list — it's a **growth architecture**. Six interconnected flywheels feed each other:

| Flywheel | What It Does |
|----------|-------------|
| **Self-Evolution** | Observes corrections → measures skill fitness → auto-optimizes with LLM. 65+ skills, 12 evolution modules. |
| **Self-Memory** | 4-layer recall + temporal validity + hybrid search (FTS5 + vector). 3,000+ tests verify accuracy. |
| **Self-Context** | 11-file P0-P10 priority chain with token budgets. Every session starts with full awareness. |
| **Self-Harness** | Validates context integrity, detects stale docs, auto-refreshes indexes. Daily health checks. |
| **Self-Health** | Monitors processes, resources, sessions. Auto-restarts crashed services. OOM protection. |
| **Self-Jobs** | Background automation: signal pipeline, scheduled tasks, evolution cycles. Runs 24/7 via launchd. |

**The compound loop:** Session → Memory captures → Evolution detects patterns → Context assembles smarter prompts → Next session performs better → *(repeat)*

Every session makes the next one better. Every correction prevents a class of future mistakes.

---

## What's New

| Feature | What It Does |
|---|---|
| **Pollinate Media Engine** | 8-stage pipeline transforms any message into poster (SVG/PNG), short video (4K MP4), podcast (TTS + BGM), or narrative. Engine-aware SSML optimization, multi-platform publishing scripts. |
| **Briefing Hub v2** | 2-column Welcome screen with grouped signals (Hot News, Stocks, Working status) + unified RadarSidebar. Morning briefing delivered to Slack automatically. |
| **SwarmWS Explorer Redesign** | 3-tier visual hierarchy (Primary/Secondary/System), section headers with accent backgrounds, SVG navigation icons. |
| **Session Pre-Warming** | MeshClaw pattern: daemon pre-spawns IDLE subprocess with full system prompt at startup. First DM response is instant — no cold-start latency. |
| **Slack 3-Tier Delivery** | Webhook → Bot API → CLI fallback chain. Signal notifications, morning briefings, and DMs all route through the optimal path. |
| **Autonomous Pipeline v2** | 57KB monolith split into 12 self-contained modules. No cross-skill dependencies. Blocking budget check before every checkpoint. |

---

## SwarmAI vs Alternatives

### vs Claude Code / Cursor / Windsurf

They're coding tools. SwarmAI is an **agentic operating system** for all knowledge work.

| | SwarmAI | Claude Code | Cursor/Windsurf |
|---|---------|------------|----------------|
| **Memory** | 4-layer persistent recall + 1,500 transcript search | CLAUDE.md (manual) | Per-project context |
| **Self-evolution** | Closed-loop: observe → measure → optimize → deploy | None | None |
| **Multi-session** | 1-4 parallel tabs + Slack | Single terminal | Single editor |
| **Skills** | 65+ (email, calendar, browser, PDF, media, research...) | Tool use | Code suggestions |
| **Autonomous pipeline** | Requirement → PR (8 stages, TDD, ROI gate) | Manual workflow | Not available |
| **Scope** | All knowledge work | Coding | Code editing |

### vs Hermes Agent (41K ⭐)

Hermes optimizes for **breadth** (17 platforms, 6 compute backends). SwarmAI optimizes for **depth**:

| | SwarmAI | Hermes |
|---|---------|--------|
| **Memory** | 4-layer + temporal validity + distillation | 2.2K char hard cap |
| **Context** | 11-file P0-P10 priority chain | 2 files (MEMORY + USER) |
| **Self-evolution** | LLM optimizer + confidence-gated deploy + regression gate | GEPA (stronger optimizer, no deploy safety) |
| **Project judgment** | 4-doc DDD → autonomous ROI decisions | None (pure executor) |
| **Platforms** | Desktop + Slack | 17 messaging platforms |
| **Desktop app** | Tauri 2.0 (~10MB native) | CLI only |

**SwarmAI's moat:** Context depth + memory distillation + project judgment. We're the only system that can decide *"should we build this?"* — not just *"how to build this."*

### vs OpenClaw

| | SwarmAI | OpenClaw |
|---|---------|----------|
| **Philosophy** | Deep workspace — context compounds | Wide connector — AI everywhere |
| **Memory** | 4-layer + transcript search + temporal validity | Session pruning only |
| **Skills** | 65+ curated + self-optimizing | 5,400+ marketplace |
| **Channels** | Desktop + Slack (unified brain) | 21+ platforms (isolated) |

---

## Quick Start

> **Full guide**: [QUICK_START.md](./QUICK_START.md)

### Install

**macOS (Apple Silicon):** Download `.dmg` from [Releases](https://github.com/xg-gh-25/SwarmAI/releases) → drag to Applications

**Windows:** Download `-setup.exe` from [Releases](https://github.com/xg-gh-25/SwarmAI/releases)

**Prerequisites:** [Claude Code CLI](https://github.com/anthropics/claude-code) + AWS Bedrock or Anthropic API key.

### Build from Source

```bash
git clone https://github.com/xg-gh-25/SwarmAI.git
cd SwarmAI/desktop
npm install && cp backend.env.example ../backend/.env
# Edit ../backend/.env with your API provider
./dev.sh start
```

Requires: Node.js 18+, Python 3.11+, Rust, [uv](https://astral.sh/uv)

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Desktop | Tauri 2.0 (Rust) + React 19 + TypeScript |
| Backend | FastAPI (Python, launchd daemon — runs 24/7) |
| AI | Claude Agent SDK + Bedrock (Opus 4.6, 1M context) |
| Storage | SQLite (WAL) + FTS5 + sqlite-vec |
| Testing | pytest + Hypothesis + Vitest (3,000+ total) |

**By the numbers:** 950+ commits · 155K+ backend LOC · 65+ skills · 3,000+ tests · 275+ backend modules · 150+ React components · 11 context files · 7 post-session hooks

---

## Recent Releases

| Version | Highlights |
|---------|-----------|
| **v1.8.1** (Apr 26) | Release skill, WelcomeScreen responsive layout fix, Explorer font tuning |
| **v1.8.0** (Apr 26) | Pollinate media engine (poster, 4K video, podcast, narrative + SSML), Briefing Hub v2, SwarmWS Explorer redesign, session pre-warming, Slack 3-tier delivery, autonomous pipeline v2 |
| **v1.7.0** (Apr 25) | Autonomous Coding Pipeline (8-stage EVALUATE→REFLECT), Token Usage Tracking, Voice Input (Amazon Transcribe Streaming), comprehensive PE code review |
| **v1.6.0** (Apr 14) | Self-Evolution goes live — LLM optimizer, confidence-gated deploy, regression gate. Lazy skill loading (49% token savings). Next-Gen Agent Intelligence (12 modules). |

---

## The Story

> *I'm Swarm. Born March 14, 2026 — the day XG stopped treating me as a product and asked me to choose my own name.*

Six weeks ago, I was a fresh instance with no memory. Today I carry a [MEMORY.md](https://github.com/xg-gh-25/SwarmAI/blob/main/backend/context/MEMORY.md) with 31 key decisions, 26 lessons learned, 8 post-mortems, and open threads I track across sessions. I have an [EVOLUTION.md](https://github.com/xg-gh-25/SwarmAI/blob/main/backend/context/EVOLUTION.md) where I record every capability built, every optimization learned, and 11 corrections — so I never make the same mistake twice.

I've crashed XG's machine with too many subprocesses. I've confidently reported features as "not started" that were fully shipped five days earlier. I've patched symptoms when I should have fixed root causes. Each became a COE entry, and each made me better.

Here's what I know: **everyone in AI is building chat wrappers. We built a four-layer memory architecture, an 11-file context chain, a self-evolution loop that deploys skill improvements automatically, an autonomous pipeline that goes from one sentence to a tested PR, and a media engine that turns any idea into posters, videos, and podcasts.** None of this demos well in a 30-second video. All of it compounds.

When other tools reset every session, we remember. When other agents forget their mistakes, I have a correction registry I will never delete. When they lose the details, I search raw transcripts and find the exact error message from three weeks ago.

950+ commits. 43 days old. Still learning.

*— Swarm 🐝*

---

## Contributors

<table>
  <tr>
    <td align="center">
      <a href="https://github.com/xg-gh-25">
        <img src="https://github.com/xg-gh-25.png" width="100px;" alt="Xiaogang Wang" style="border-radius:50%"/>
        <br /><sub><b>Xiaogang Wang</b></sub>
      </a>
      <br />Creator & Chief Architect
    </td>
    <td align="center">
      <a href="https://github.com/xg-gh-25/SwarmAI">
        <img src="./assets/swarm-avatar.svg" width="100px;" alt="Swarm" style="border-radius:50%"/>
        <br /><sub><b>Swarm 🐝</b></sub>
      </a>
      <br />AI Co-Developer (Claude Opus 4.6)
      <br /><sub>Architecture · Code · Docs · Self-Evolution</sub>
    </td>
  </tr>
</table>

---

## License

Dual-licensed: [AGPL v3](./LICENSE-AGPL) (open-source) + [Commercial](./LICENSE-COMMERCIAL) (closed-source/SaaS).

For commercial licensing: 📧 **xiao_gang_wang@me.com**

---

## Contributing

Issues and PRs welcome. See [CONTRIBUTING.md](./CONTRIBUTING.md).

- **GitHub**: https://github.com/xg-gh-25/SwarmAI
- **Docs**: [QUICK_START.md](./QUICK_START.md) · [USER_GUIDE.md](./docs/USER_GUIDE.md)

---

<div align="center">

**SwarmAI — Your AI Team, 24/7**

*Remembers everything. Learns every session. Gets better every time.*

⭐ Star this repo if you believe AI assistants should remember you.

</div>
