# SwarmAI — Ubiquitous Language

Canonical terms for skills, agents, and documentation. When writing a skill,
use these terms exactly. When a term has "Avoid" entries, never use those
synonyms — they cause confusion across the 69-skill ecosystem.

---

## Session & Process

- **session** — A single chat conversation between the user and the agent,
  identified by `session_id`. Lifecycle: COLD → STREAMING → IDLE →
  WAITING_INPUT → DEAD. One session per chat tab.
  _Avoid:_ "thread" (Slack-specific), "conversation" (ambiguous with channel DM).
  _Relationship:_ A session runs inside a **session unit** (the subprocess wrapper).

- **session unit** — The `SessionUnit` object that owns a Claude SDK subprocess
  for one session. Manages spawn, retry, streaming, and state transitions.
  _Avoid:_ "agent process", "worker", "subprocess" (too generic — the subprocess
  is one part of the unit).

- **daemon** — The always-on `com.swarmai.backend` launchd process
  (`SWARMAI_MODE=daemon`). Runs Slack, background jobs, and channels 24/7.
  _Avoid:_ "server", "service" (too generic), "backend" (ambiguous — sidecar is
  also a backend).

- **sidecar** — The Python backend spawned by the Tauri desktop app
  (`SWARMAI_MODE=sidecar`). Lives only while the desktop window is open.
  _Avoid:_ "backend" (ambiguous with daemon), "app backend".

## Task & Work Units

- **task** — A background execution unit in the job system (`jobs/`). Has a
  schedule, a handler, and produces results. Runs headless (no chat UI).
  _Avoid:_ "job" when referring to the scheduled entry (a **job** contains a task).

- **job** — A scheduled entry in `user-jobs.yaml` or `system-jobs.yaml`. Defines
  what task to run, when, and with what parameters.
  _Avoid:_ "cron" (implementation detail — jobs use launchd, not cron),
  "scheduled task" (use "job" for the schedule, "task" for the execution).

- **todo** — A Radar sidebar work packet managed by `s_radar-todo`. Self-contained:
  includes title, description, linked files, commits, and acceptance criteria.
  Dragging into chat gives the agent full context to execute.
  _Avoid:_ "task" (that's the job system), "ticket" (that's Taskei/external),
  "issue" (that's GitHub/Linear).

- **ticket** — An external work item in Taskei, GitHub Issues, or Linear.
  Not managed by SwarmAI — queried via MCP tools.
  _Avoid:_ "issue" (when referring to Taskei), "todo" (that's Radar).

## Skills & Evolution

- **skill** — A markdown-defined capability in `backend/skills/s_<name>/`.
  Has `SKILL.md` (frontmatter + instructions) and optionally `INSTRUCTIONS.md`,
  `manifest.yaml`, and scripts. Discovered by the SDK at runtime.
  _Avoid:_ "tool" (that's an MCP/SDK tool), "plugin" (that's an external package),
  "command" (that's a slash command entry point).

- **tool** — A callable function exposed via MCP protocol or Claude SDK tool_use.
  Examples: `Read`, `Edit`, `Bash`, `mcp__slack-mcp__post_message`.
  _Avoid:_ "skill" (skills orchestrate tools, they are not tools themselves).

- **pipeline** — The autonomous 8-stage lifecycle: EVALUATE → THINK → PLAN →
  BUILD → REVIEW → TEST → DELIVER → REFLECT. Tracked via `run.json` + artifacts.
  _Avoid:_ "workflow" (too generic), "process" (too generic).

- **artifact** — A pipeline-stage output stored in `.artifacts/`. Has an `art_`
  ID, type (evaluation, research, design_doc, changeset, etc.), and JSON data.
  _Avoid:_ "output" (too vague), "deliverable" (that's the DELIVER stage product).

- **correction** — A user-identified mistake captured in `EVOLUTION.md` as a
  `C###` entry. Corrections are permanent — never deleted. They drive skill
  improvement via the evolution pipeline.
  _Avoid:_ "error" (generic runtime error), "bug" (code defect, not behavioral).

## Memory & Context

- **memory** — Long-term curated knowledge in `.context/MEMORY.md`. Agent-owned,
  distilled from DailyActivity. Contains: Recent Context, Key Decisions, Lessons
  Learned, COE Registry, Open Threads.
  _Avoid:_ "context" (that's the 11-file system prompt), "history" (that's raw
  chat transcript), "knowledge" (that's `KNOWLEDGE.md`).

- **context** — The assembled system prompt built from 11 `.context/*.md` files
  (P0-P10). Assembled by `ContextDirectoryLoader` + `PromptBuilder`.
  _Avoid:_ "prompt" (too narrow — context includes memory, knowledge, projects),
  "memory" (that's one file within context).

- **knowledge** — Domain facts, architecture docs, and reference material in
  `.context/KNOWLEDGE.md` and `Knowledge/` directories. Auto-refreshed index.
  _Avoid:_ "memory" (curated decisions/lessons), "notes" (that's `Knowledge/Notes/`).

- **DailyActivity** — Raw per-session log at `Knowledge/DailyActivity/YYYY-MM-DD.md`.
  Append-only during sessions, distilled into MEMORY.md periodically.
  _Avoid:_ "journal", "diary", "log" (too generic).

## Projects & DDD

- **project** — A DDD-managed workspace entity in `Projects/<name>/`. Has 4
  required documents (PRODUCT.md, TECH.md, IMPROVEMENT.md, PROJECT.md) and an
  `.artifacts/` directory. SwarmAI is the default project.
  _Avoid:_ "workspace" (that's SwarmWS), "repo" (that's the git repository).

- **DDD docs** — The 4 Domain-Driven Design documents per project. They answer:
  Should we? (PRODUCT) Can we? (TECH) Have we tried? (IMPROVEMENT) What now? (PROJECT).
  _Avoid:_ "project docs" (too vague), "specs" (that's SDD, not DDD).

## Channels & Communication

- **channel** — A communication adapter (Slack, future: Feishu, Discord) managed
  by `ChannelGateway`. Each channel has an adapter that bridges external messages
  to SwarmAI sessions.
  _Avoid:_ "integration" (too vague), "bot" (that's the Slack bot identity,
  not the channel system).

- **notification** — A one-way message sent via `s_notify` to external platforms
  (Feishu, Slack, email, etc.). Config-driven via `notify-channels.yaml`.
  _Avoid:_ "alert" (implies urgency), "message" (too generic — messages flow
  through channels, notifications are fire-and-forget).

## Infrastructure

- **hook** — A post-session callback in `backend/hooks/`. Fires after the agent
  finishes responding. Examples: `context_health_hook`, `evolution_maintenance_hook`.
  _Avoid:_ "listener", "handler" (handlers are in the job system).

- **signal** — An external information item (RSS, GitHub trending, HN) ingested
  by the signal pipeline into `signal_digest.json`. Surfaces in session briefings.
  _Avoid:_ "feed" (that's the source), "news" (too narrow).

---

_This glossary is the single source of truth for terminology across all 69 skills.
When creating or reviewing a skill, verify terminology against this file.
Update this file when new canonical terms emerge — never let skills invent
their own synonyms for existing concepts._
