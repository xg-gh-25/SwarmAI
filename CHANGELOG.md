# Changelog

All notable changes to SwarmAI will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.3] - 2026-04-08

### Added

- **Proactive OOM Prevention**: RSS-based proactive restart-with-resume — when
  a session's process tree exceeds 1.2GB, compact → kill → lazy resume on next
  send, preventing macOS jetsam from OOM-killing the entire backend
- **Resume Context Overhaul**: Structured checkpoint + recent turns (~600
  tokens) replaces 200K-token raw history dump; LRU cache, independent
  try/except guards, legacy fallback
- **SSE Resilience**: `SessionBusyError` rejects send on active sessions
  instead of force-killing; frontend detects premature disconnect with 30s
  reconnection timeout; `onDisconnect` handler on all streaming paths
- **Persistence Hardening**: SQLite retry on SQLITE_BUSY (3 attempts,
  exponential backoff), ETag caching on messages endpoint with per-session
  `If-None-Match`, crash-safe incremental assistant message persistence
- **Shared Bedrock Client**: Centralized `jobs/bedrock.py` with pre-resolved
  credentials, 30-minute TTL auto-refresh, SSO IdC fallback preferring
  `default` profile, credential eviction + retry on auth errors
- **Estimation Learner**: EMA-based job execution time predictor with batched
  persistence (every 5 records or 60s) and atexit flush
- **Sandbox Defaults**: `~/.swarm-ai/` in default write paths, all known
  seatbelt-blocked commands excluded, tilde expansion via `os.path.expanduser`
- **File Preview Unification**: Single `classifyFileForPreview()` source of
  truth; SVG inline via `<img>`, PDF/Office show info modal with Open/Copy
  Path, lightweight `/workspace/file/meta` endpoint
- **Collection Cap**: Pytest aborts at 300 tests without `--run-all` to prevent
  agent OOM during coding loops
- **Daemon Resource Deployment**: `dev.sh` deploys `resources/` alongside the
  frozen binary so bundle_paths resolves default-agent.json in daemon mode

### Fixed

- **SSE Disconnect Kill Chain**: Force-unstick no longer kills active sessions
  with low stall time; `recover_from_disconnect` transitions STREAMING → IDLE
  with background pipe flush
- **Orphaned Messages on SESSION_BUSY**: Backend deletes the pre-persisted user
  message when `SessionBusyError` is raised, preventing cold resume from
  injecting unsent messages
- **Resume Context Independence**: Resume injection moved outside the
  `ContextDirectoryLoader` try block — upstream exceptions no longer silently
  skip resume context (COE: 2026-04-02)
- **Compact Timeout**: Both proactive restart paths use
  `asyncio.wait_for(compact(), timeout=30)` to prevent compact hangs from
  blocking the restart sequence or maintenance loop
- **Monotonic Clock**: Proactive restart cooldown uses `time.monotonic()`
  instead of `time.time()` — immune to NTP sync and sleep/wake clock jumps
- **SQLite Busy Timeout Alignment**: `busy_timeout` reduced from 5000ms to
  100ms to align with app-level retry budget (50+200+500ms)
- **CI Release Action**: Explicit `tag_name` in `gh-release` action — fixes
  `GITHUB_REF` resolving to `refs/heads/main` instead of tag ref
- **Credential Resolution in Daemon**: `jobs/bedrock.py` pre-resolves
  credentials in-process before creating boto3 client, fixing launchd auth
  failures where `credential_process` fails due to minimal PATH
- **Task Notification Stalls**: AGENT.md rules ensure user requests always win
  over `<task-notification>` XML; never generate text-only acknowledgments
- **Build Script Crash Loop**: `parse_known_args` in frozen backend binary
  ignores unknown CLI flags instead of crashing
- **Dynamic Tab Scaling Tests**: Reference formula and boundary values updated
  from 85% to 90% threshold to match production `resource_monitor.py`
- **OOM Backoff Test**: Updated from flat 30s assertion to exponential
  30/60/120s to match production `compute_backoff` implementation

### Changed

- **Memory Threshold**: Resource monitor thresholds raised from 85%/75% to
  90%/80% — uses `effective_used` (total − available) instead of
  `used` (active + wired) for accurate macOS memory pressure measurement
- **Hook Ordering**: Distillation hook runs before context health hook so
  embedding sync picks up freshly-distilled MEMORY.md entries
- **Memory Embedding Sync**: Always-on (removed `FULL_INJECTION_THRESHOLD`
  gate) — keeps vector index warm for zero cold-start selective injection
- **Knowledge Store FTS5**: OR semantics for keyword queries — "daemon crash
  SIGKILL OOM" now matches chunks containing any term instead of requiring all
- **Bundle Path Priority**: Daemon mode `resources/` path checked before macOS
  `.app` bundle paths

### Removed

- **react-pdf**: Removed dependency (~1.5MB bundle savings) — PDF preview now
  uses info modal with "Open in Default App" button

## [1.2.0] - 2026-04-02

### Added

- **Memory Architecture v2 Phase 0**: Progressive Memory Disclosure — 3-layer
  recall system (compact index → topic-triggered injection → on-demand read),
  sqlite-vec vector infrastructure, adaptive token budgets, and injection
  validation
- **Daemon/Sidecar Robustness**: boot_id-based restart detection, backend.json
  conflict guard, 10s health watchdog, onboarding nudge for daemon setup
- **Autonomous Pipeline Enhancements**: Smoke Test step in BUILD VERIFY phase,
  Integration Trace in REVIEW stage
- **Signal Pipeline**: Leaders tier, dead feed recovery, retry client with
  exponential backoff
- **ToDo Lifecycle**: Context validation, structured producer metadata,
  automatic lifecycle purge for stale items

### Fixed

- **Chat History Restore**: Full conversation recovery after app restart with
  TTL-based cleanup of expired sessions
- **Message Sync**: Unconditional message sync on result event — eliminates
  dropped messages during streaming completion
- **MCP Loading**: Dual name+id matching for force-load; removed STREAMING
  circuit breaker kill that could terminate active sessions
- **CompactionGuard**: Read-only tool thresholds, grace period before
  escalation, improved error handling for edge cases
- **Sandbox Config**: Single source of truth for sandbox state; explicit disable
  instead of flag omission; cleaned stale `setSandboxEnabled` references
- **Daemon Isolation**: Separated daemon from dev directory to prevent
  dev-mode changes from crashing production daemon; fixed `--log-level` crash
  loop
- **Memory Pipeline**: 6 bug fixes — index wiring, keyword source for
  progressive disclosure, edge case when index is the only content, 5 findings
  from Kiro code review, `_RETRYABLE` intent scoping, `locked_write` guard

### Changed

- **Test Infrastructure**: conftest.py slimmed from 970 → 583 lines with single
  source of truth for xdist config; orphan prevention via `setpgrp` removal +
  atexit child cleanup; xdist worker subprocess enforcement skip; pytest-timeout
  integration
- **Proactive Intelligence**: Signal freshness filtering before slicing;
  truncate hints at word boundaries

### Removed

- **Feishu Adapter**: Removed `feishu.py` channel adapter and `lark-oapi`
  dependency (unused, 12MB saved)

## [1.1.2] - 2026-03-31

### Added

- **Briefing Panel Interactive**: Focus item click-to-chat, job result summaries
  displayed in welcome screen
- **Signal Pipeline Tiers**: Tier-based weighting for signal sources, RSS source
  expansion for broader coverage
- **MEDDPICC Scorecard Skill**: Score Salesforce opportunities against the 8
  AWS MEDDPICC dimensions
- **Daemon-First Architecture**: launchd manages backend 24/7 — backend
  survives Tauri close for always-on channels

### Fixed

- **Daemon Health**: Correct watchdog event handling, fast recovery polling,
  dev.sh port-release race condition
- **Job Scheduler**: Auth pre-check before job execution, ICT-aligned
  schedules, timeout bump for long-running jobs
- **launchctl Compatibility**: Handle exit code 5 (already loaded) alongside 37
- **Briefing Focus**: Only scan recent session blocks, fuzzy dedup to avoid
  repeated suggestions
- **Proactive Signals**: Filter by freshness before slicing, truncate hints at
  word boundaries
- **Channel Sessions**: False "busy" notice fixed — check STREAMING state not
  just `is_alive`; TTL rotation for conversation continuity; owner gets full
  brain context

## [1.1.0] - 2026-03-28

### Added

- **Slack Channel Adapter**: Native Slack bot with Block Kit formatting, owner
  priority queue, sender identity + permission tiers, streaming UX with status
  reactions
- **Backend-as-Daemon**: launchd-managed backend process that survives Tauri
  close, enabling 24/7 Slack bot operation
- **Core Engine L3–L4**: Memory effectiveness tracking, DDD auto-update
  suggestions, growth metrics dashboard, autonomous DDD refresh, skill proposer
- **Autonomous Pipeline**: Full AIDLC pipeline orchestrator (EVALUATE → THINK →
  PLAN → BUILD → REVIEW → TEST → DELIVER → REFLECT) with TDD methodology,
  validator, and REPORT.md generation
- **Escalation Protocol**: L1 CONSULT + Radar todo integration + timeout
  resolution for human-in-the-loop decisions
- **Unified Job System**: Product-level job scheduler with cron support, httpx
  adapters, Claude CLI execution, and Radar JOBS panel
- **Project DDD System**: Domain-Driven Design document structure for projects
  (PRODUCT.md, TECH.md, IMPROVEMENT.md, PROJECT.md) with auto-provisioning
- **Session Briefing**: Welcome screen shows suggested focus, external signals,
  job results, and system health
- **Context Health Harness**: Self-maintaining brain with DDD staleness
  detection, cross-document consistency checks, and checksum tracking
- **Settings Redesign**: Skills and MCP Servers integrated as Settings tabs,
  onboarding bootstrap flow
- **Swarm Radar Enhancements**: ToDo dedup, lifecycle management, drag-to-chat
  binding, auto-complete

### Fixed

- **Streaming Reliability**: Interleave bug fix, seamless indicator during queue
  drain, orphaned queue rescue, null byte defense
- **Security Hardening**: Sandbox non-owner file access, structural tool
  blocking for non-owners, sender-scoped directories
- **SDK Compatibility**: Version-guard `--bare` flag, `--resume` timeout raised
  to 180s, xdist worker cap enforcement
- **Memory Pipeline**: Git cross-reference in DailyActivity, verified
  distillation (COE C005), circuit breaker for runaway memory writes
- **Chat UX**: Streaming completion indicator, chat history after restart,
  global Cmd+F search with auto-scroll

### Changed

- **Model Standardization**: Unified on Claude 4.6 (Opus + Sonnet), removed
  3.5/4.5 model configs, background jobs upgraded to Haiku 4.5
- **Test Infrastructure**: Production-grade conftest with resource watchdog,
  Hypothesis CI profile, memory-safe execution

### Security

- Channel permission tiers (owner/trusted/public) with verified sender identity
- Non-owner sandbox isolation with sender-scoped directories
- Structural tool blocking prevents file/system access for non-owners

## [1.1.1] - 2026-03-28

### Added

- **User Guide**: Comprehensive `docs/USER_GUIDE.md` covering prerequisites,
  installation, configuration, features, troubleshooting, and FAQ

### Changed

- **README Quick Start**: Updated with prerequisites, link to full user guide,
  corrected installer filenames, and simplified dev setup instructions

## [1.0.0] - 2026-03-22

### Added

- **Persistent Agentic Workspace**: Full desktop application with Tauri 2.0,
  React 19, and FastAPI Python sidecar
- **3-Layer Memory Pipeline**: DailyActivity → Distillation → MEMORY.md for
  cross-session knowledge persistence
- **11-File Context System (P0-P10)**: Priority-based context assembly with
  dynamic token budgets and L0/L1 caching
- **Multi-Tab Parallel Sessions**: 1-4 concurrent chat tabs with isolated state,
  independent streaming, and per-tab abort (RAM-adaptive)
- **Self-Evolution Engine**: Automatic skill creation, correction capture, and
  capability registry (EVOLUTION.md)
- **50+ Built-in Skills**: Browser automation, PDF manipulation, spreadsheets,
  Slack, Outlook, Apple Reminders, web research, and more
- **Three-Column Command Center**: SwarmWS Explorer (left), Chat Tabs (center),
  Swarm Radar (right) with drag-to-chat context injection
- **4-Layer Security**: Tool logger → command blocker → human approval → skill
  access control, plus bash sandboxing and workspace isolation
- **AI Provider Support**: AWS Bedrock (default) and Anthropic API with
  automatic credential chain detection
- **SwarmWS Workspace**: Local filesystem workspace with Knowledge/, Projects/,
  Notes/, DailyActivity/, Artifacts/ directories
- **Swarm Radar Dashboard**: ToDos, active sessions, artifacts, and background
  job tracking
- **Session Resilience**: CompactionGuard, orphan reaper, 5-state session
  machine (COLD/IDLE/STREAMING/WAITING_INPUT/DEAD)
- **Internationalization**: English and Chinese (中文) UI support via i18next
- **Dark/Light/System Themes**: CSS custom property-based theming
- **Auto-Commit**: Git-backed workspace with automatic commits after every
  conversation turn
- **MCP Integration**: Model Context Protocol server support for extensible
  tool capabilities
- **Cross-Platform**: macOS (.dmg) and Windows (.msi) installers with
  auto-update support via Tauri updater

### Security

- All data stored locally (`~/.swarm-ai/`) — no cloud storage
- Credential delegation via AWS credential chain — app never stores API keys
- 13 dangerous bash command patterns blocked by default
- Human-in-the-loop approval for destructive operations
- Error sanitization in production mode

[1.2.3]: https://github.com/xg-gh-25/SwarmAI/releases/tag/v1.2.3
[1.2.0]: https://github.com/xg-gh-25/SwarmAI/releases/tag/v1.2.0
[1.1.2]: https://github.com/xg-gh-25/SwarmAI/releases/tag/v1.1.2
[1.1.1]: https://github.com/xg-gh-25/SwarmAI/releases/tag/v1.1.1
[1.1.0]: https://github.com/xg-gh-25/SwarmAI/releases/tag/v1.1.0
[1.0.0]: https://github.com/xg-gh-25/SwarmAI/releases/tag/v1.0.0
