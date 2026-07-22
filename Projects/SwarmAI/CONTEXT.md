# SwarmAI — Domain Language (CONTEXT.md)

> Canonical terminology for the SwarmAI codebase. All skills, DDD docs, and agent instructions
> SHOULD use these terms exactly. When the agent generates code, comments, or documentation,
> it references this glossary for consistency.
>
> Inspired by DDD's "Ubiquitous Language" pattern (mattpocock/skills CONTEXT.md).

---

## Core Concepts

**Swarm**
The AI agent persona. Not a chatbot — a colleague that remembers, evolves, and acts.
_Avoid:_ "the AI", "the assistant", "the bot" (except when referring to Slack bot specifically).

**Session**
A single conversation between user and Swarm in one chat tab. Has a 5-state lifecycle (COLD → STREAMING → IDLE → WAITING_INPUT → DEAD). Maps 1:1 to a Claude SDK subprocess.
_Avoid:_ "chat", "thread" (Slack-specific), "conversation" (ambiguous — could be multi-session).

**Tab**
A UI container for one Session. Multiple Tabs run in parallel (max 4: 3 chat + 1 channel). Each Tab is fully isolated — no shared state between Tabs.
_Avoid:_ "window" (Tauri term for the app frame), "instance" (too generic).

**Channel**
A non-chat communication pathway (Slack DM, future: email, webhook). Channels have their own Session lifecycle but different UX (no chat UI, push-based).
_Avoid:_ "integration" (too broad), "adapter" (implementation detail — the adapter IS inside a channel).

**Daemon**
The Python backend process running 24/7 via launchd (macOS) or systemd (Hive). Survives app close. Owns Channels, Jobs, and the API server.
_Avoid:_ "sidecar" (deprecated architecture term), "backend" alone (ambiguous — could mean the code or the process), "server" (implies multi-tenant).

**Hive**
A cloud-deployed SwarmAI instance on EC2. Single-tenant, managed from Desktop. Runs the same daemon, no Tauri shell.
_Avoid:_ "cloud version", "SaaS" (explicitly not SaaS), "remote instance".

---

## Memory & Knowledge

**Memory (MEMORY.md)**
Curated cross-session knowledge: key decisions, lessons learned, COEs, open threads. Agent-owned, user-directed. The most valuable context file — never delegated to a platform.
_Avoid:_ "history" (that's raw transcripts), "notes" (that's Knowledge/Notes/).

**DailyActivity**
Raw per-session log. Written automatically. Source material for Memory distillation. NOT curated — that's Memory's job.
_Avoid:_ "journal", "diary", "session transcript" (transcripts are JSONL, DailyActivity is structured markdown).

**Distillation**
The process of promoting recurring themes from DailyActivity into curated MEMORY.md entries. Automatic (≥3 unprocessed files). Cross-references git before promoting.
_Avoid:_ "summarization" (that's lossy — distillation preserves signal), "archival" (that's what Archives/ does).

**Knowledge Card**
A structured note in `Knowledge/Learned/`. Source URL + key insights + tags + cross-references. Index pointer with enough context to decide whether to re-read the original.
_Avoid:_ "bookmark" (no insights), "article" (that's the source, not our note).

**Thesis (THESIS.md)**
A bet we're making — a claim backed by convergent evidence from multiple Knowledge Cards and Key Decisions. Strengthened or challenged over time. Not a fact — a directional conviction.
_Avoid:_ "belief" (too weak), "rule" (that's STEERING.md), "principle" (that's SOUL.md).

**Context File**
One of 11 `.context/*.md` files assembled into the system prompt. P0-P10 priority. System-owned (overwritten), user-owned (preserved), agent-owned (MEMORY, EVOLUTION).
_Avoid:_ "prompt file" (only partially true), "config file" (not configuration).

---

## Self-Evolution

**Flywheel**
One of 6 interconnected compound loops: Self-Evolution, Self-Memory, Self-Context, Self-Harness, Self-Health, Self-Jobs. Each feeds at least one other.
_Avoid:_ "module" (a flywheel is a behavior pattern, not a code module), "feature" (flywheels are emergent, not features).

**Correction (EVOLUTION.md)**
A user correction that reveals a systematic agent failure. Highest-value evolution entry — never deleted. Pattern: what happened → root cause → structural fix.
_Avoid:_ "bug" (implies code error — corrections are behavioral/judgment errors), "feedback" (too generic).

**Skill**
A reusable agent capability with a SKILL.md spec. Lazy (stub in prompt, Read INSTRUCTIONS.md on invoke) or Always (full in prompt). Lives in `backend/skills/s_<name>/`.
_Avoid:_ "tool" (MCP tools are different), "command" (too imperative), "plugin" (implies third-party).

**Skill Proposer**
L4.1 autonomous system that detects recurring capability gaps and proposes new Skills. Uses Opus for reasoning-heavy skill design. Never auto-deploys.
_Avoid:_ "skill generator" (it proposes, human deploys), "auto-skill" (misleading — not automatic).

**Evolution Pipeline**
4-phase system (MINE → ASSESS → ACT → AUDIT) that mines session transcripts for correction patterns, scores fitness, and optimizes underperforming skills. Confidence-gated deployment.
_Avoid:_ "self-improvement" (too vague), "training" (we don't fine-tune models).

---

## Autonomous Pipeline

**Pipeline**
The 8-stage autonomous delivery lifecycle: EVALUATE → THINK → PLAN → BUILD → REVIEW → TEST → DELIVER → REFLECT. Turns a one-sentence requirement into PR-ready code.
_Avoid:_ "workflow" (too generic), "CI/CD" (that's infrastructure — pipeline is development methodology).

**Stage**
One step in the Pipeline. Has: purpose, inputs, outputs, decision classification, DDD context. Stages run sequentially; each publishes an artifact.
_Avoid:_ "step" (use for sub-stage actions), "phase" (use for AIDLC phases 1/2/3).

**Profile**
A Pipeline configuration that skips stages based on task type: full, trivial, research, docs, bugfix. Selected at EVALUATE.
_Avoid:_ "template" (profiles are behavior modes, not fill-in-the-blank).

**Artifact**
A typed output from a Pipeline Stage, stored in `.artifacts/runs/<run_id>/`. Registry-tracked. Used by downstream stages and delivery.
_Avoid:_ "output" (too generic), "file" (artifacts have metadata beyond file content).

**Decision Classification**
Every Pipeline decision is: **mechanical** (auto-approve, no human needed), **taste** (batched at delivery gate), or **judgment** (blocks for human L2 BLOCK).
_Avoid:_ "approval" (only judgment decisions need approval), "choice" (too informal).

**Feedback Loop**
A verification cycle that produces a testable signal: write test → run → interpret result → fix → repeat. The fundamental unit of engineering quality. BUILD uses TDD loops. REVIEW uses pattern-matching loops. REFLECT uses learning loops.
_Avoid:_ "iteration" (too generic — feedback loops have OBSERVABLE SIGNALS, iterations are just repetition).

**Convergence Loop**
Post-DELIVER quality gate: 6-layer verification × agent self-assessment × goal alignment. Iterates until all pass or max attempts reached. The pipeline's "am I actually done?" mechanism.
_Avoid:_ "final check" (implies one-shot — convergence is iterative), "QA" (that's the TEST stage).

**DDD (Domain-Driven Design Documents)**
4 project knowledge docs: PRODUCT.md (Should we?), TECH.md (Can we?), IMPROVEMENT.md (Have we tried?), PROJECT.md (Should we now?). The substrate for autonomous judgment.
_Avoid:_ "documentation" (DDD docs are decision-making tools, not reference docs), "specs" (that's SDD).

---

## Infrastructure

**MCP (Model Context Protocol)**
External tool servers that expose capabilities to the agent via JSON-RPC. Each runs as a subprocess per session. Examples: slack-mcp, builder-mcp, aws-outlook-mcp.
_Avoid:_ "plugin" (MCP is a protocol, not a plugin system), "extension" (too generic).

**Job**
A scheduled background task. System jobs (in code) or user jobs (in YAML). Runs headless via Claude CLI or script. Types: signal_fetch, digest, agent_task, script, maintenance.
_Avoid:_ "cron job" (implementation detail — jobs have more lifecycle than cron), "task" (Radar ToDos are tasks).

**Signal**
A scored news/update item from the signal pipeline. 13 feeds → dedup → LLM score → digest. Appears in Welcome Screen briefing and Slack.
_Avoid:_ "notification" (signals are information, not alerts), "news" (too casual).

**Radar Todo**
A tracked work packet in the sidebar. Self-contained: when dragged into a Tab, the agent has all context to execute immediately. Different from Apple Reminders (time-based) and Tasks (broader scope).
_Avoid:_ "task" alone (ambiguous with background tasks), "item" (too generic).

---

## Architecture Patterns

**Subprocess (Claude SDK)**
The Claude Agent SDK runs as a spawned CLI subprocess per Session. System prompt injected at spawn. Process persists across turns (IDLE state). Killed on session end or eviction.
_Avoid:_ "API call" (it's not HTTP — it's a long-running process), "agent" alone (the subprocess IS the agent runtime, but "agent" usually means Swarm).

**Spawn**
Creating a new SDK subprocess for a Session. Serialized via `_spawn_lock`. Only happens in COLD state. Cost: ~1500MB RSS + 2-5s latency.
_Avoid:_ "start" (too generic), "launch" (that's the Tauri app).

**Eviction**
Killing an IDLE Session's subprocess to free a slot for a new Session. Memory-aware: highest-RSS first. Protected states (STREAMING, WAITING_INPUT) are NEVER evicted.
_Avoid:_ "close" (user action), "terminate" (too permanent — evicted sessions can re-spawn), "kill" (use for crash/force-kill).

**Resume**
Restoring a Session's conversation context after its subprocess was killed (eviction, OOM, crash). Uses `--resume` flag + 5-layer context enrichment (up to 150K tokens).
_Avoid:_ "restore" (too broad), "reconnect" (that's WebSocket/SSE reconnection, not conversation resume).

**Hook**
A post-session function that fires asynchronously after a Session completes. Never blocks the request path. Examples: DailyActivityExtractionHook, EvolutionMaintenanceHook, ContextHealthHook.
_Avoid:_ "callback" (too generic), "middleware" (hooks are post-hoc, not in-path), "listener" (implies event subscription — hooks are explicitly registered).

**ProjectionLayer**
Startup-time file copier: `backend/skills/s_*/` → `.claude/skills/s_*/`. Applies platform filtering (Hive excludes macOS-only skills). The skill deployment mechanism.
_Avoid:_ "installer" (projection is idempotent, not installation), "sync" (implies bidirectional — projection is one-way).

---

## Development

**Direct Mode**
Coding without pipeline ceremony: read → code → test → commit. For bug fixes, typos, config changes. Still requires post-task scan.
_Avoid:_ "quick mode" (implies lower quality — Direct still has standards).

**TDD-Only Mode**
RED → GREEN → VERIFY. Tests before code. For extending existing patterns with identical shape. No pipeline artifacts.
_Avoid:_ "test mode" (TDD is a development methodology, not a testing mode).

**Full Pipeline Mode**
Default for all coding. 8 stages. Adversarial sub-agent mandatory. REPORT.md generated. For anything that introduces new behavior.
_Avoid:_ "formal mode" (implies bureaucracy — pipeline is quality, not formality).

**PE Review**
Post-execution engineering review. Human audits the agent's work for "things not done" (open-world). Complements pipeline's "things done wrong" (closed-world). Two perspectives: builder + reviewer.
_Avoid:_ "code review" (PE review is broader — covers architecture, design, completeness).

---

## Relationships

```
User ──creates──> Session ──via──> Tab
User ──receives──> Signal ──from──> Job
Session ──spawns──> Subprocess ──uses──> MCP
Session ──fires──> Hook ──writes──> DailyActivity ──distills──> Memory
Skill ──invoked-by──> Session ──produces──> Artifact
Pipeline ──orchestrates──> Stage ──publishes──> Artifact
Evolution Pipeline ──mines──> Transcript ──detects──> Correction
Correction ──may-become──> Steering Rule (via steeringify)
Skill Proposer ──reads──> Gap ──proposes──> Skill
DDD docs ──inform──> Pipeline judgment (EVALUATE, THINK, REFLECT)
```

---

## Flagged Ambiguities (Resolved)

| Term | Ambiguity | Resolution |
|------|-----------|------------|
| "backend" | The code? The process? The daemon? | Use "daemon" for the process, "backend/" for the code directory, "backend" only in compound terms like "backend API" |
| "agent" | The AI persona? The subprocess? The SDK? | "Swarm" = persona. "subprocess" = Claude SDK runtime. "agent" = avoid alone, always qualify |
| "memory" | MEMORY.md? The concept? RAM? | "Memory" (capital) = MEMORY.md system. "memory" (lowercase in tech context) = RAM. Spell out when ambiguous |
| "pipeline" | Autonomous Pipeline? Signal Pipeline? Evolution Pipeline? | Always qualify: "autonomous pipeline", "signal pipeline", "evolution pipeline". Never bare "pipeline" |
| "context" | Context files? Context window? Context directory? | "context files" = the 11 .context/*.md. "context window" = model token limit. "context" alone = avoid |
| "mode" | SWARMAI_MODE? Coding mode? Pipeline profile? | "platform mode" = daemon/subprocess/hive/dev. "execution mode" = direct/tdd/pipeline. "profile" = pipeline config |
| "skill" | The concept? The directory? The SKILL.md file? | "skill" = the capability. "skill directory" = `s_<name>/`. "SKILL.md" = the spec file |

---

_Last updated: 2026-05-13. Update this file when new domain terms emerge or ambiguities cause confusion._
_Source of truth: this file. When TECH.md or PRODUCT.md uses a term differently, this file wins._
