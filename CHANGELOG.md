# Changelog

All notable changes to SwarmAI will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.5.1] - 2026-04-13

### Fixed

- **Hook DB API mismatch**: `SkillMetricsHook` and `UserObserverHook` called `db.messages.list(filters={"session_id": ...})` which doesn't exist — replaced with `db.messages.list_by_session(session_id)`
- **Orphan reaper test brittleness**: Watchdog tests mocked `ppid=1` check directly, but reaper now uses `_is_owned_orphan` (SWARMAI_OWNER_PID env check) — tests updated to mock the ownership check instead
- **Proactive restart test thresholds**: OOM restart threshold was raised from 1.2GB to 1.8GB in production code but tests still used 1.2GB/1.3GB/1.5GB values — updated all test values to 2.0GB (above 1.8GB threshold)

## [1.5.0] - 2026-04-13

### Added — Self-Evolution Goes Live

The evolution pipeline went from "observes but never acts" to **production deployment** — skills are now automatically optimized and deployed based on user correction patterns.

**LLM-as-Optimizer (replaces heuristic-only)**
- **LLM Optimizer**: New `llm_optimizer.py` — sends skill text + correction evidence to Bedrock Opus, receives semantically-aware TextChange proposals instead of blind text append/remove
- **Pre-validation**: LLM-proposed changes are validated against actual skill text before acceptance — approximate quotes that don't match character-for-character are dropped
- **Config-gated**: `config.evolution.optimizer` supports `"auto"` (LLM → heuristic fallback), `"llm"` (LLM only), `"heuristic"` (original v2.1 behavior)
- **Cost controls**: `max_llm_skills_per_cycle` (default 5) caps LLM calls per evolution cycle; skills ranked by confidence — highest-value skills get LLM budget first; skills below `med_threshold` and oversized skills (>15KB) automatically use heuristic
- **Token tracking**: Per-skill and per-cycle LLM token usage recorded in `skill_health.json` and `EVOLUTION_CHANGELOG.jsonl`
- **Bedrock client TTL**: 1-hour TTL with `reset_bedrock_client()` called at cycle start for credential rotation safety; `read_timeout=30s`, `connect_timeout=10s`, `max_attempts=1`

**Evolution Pipeline v2.1 — Confidence Tuning**
- **Tuned thresholds**: HIGH lowered from 0.7→0.35, MED from 0.3→0.15 — calibrated to real-world correction data where max reachable confidence was 0.2 at old thresholds
- **Config-driven thresholds**: `config.evolution.high_confidence` and `med_confidence` override defaults without code changes
- **Confidence tiers**: Added n≥2 evidence band (0.5) and >0.05 density band (0.2) — real-world skills with 1-3 corrections now produce meaningful confidence scores
- **Regression gate**: Post-deploy fitness check — auto-reverts if deployed skill degrades below previous score
- **`optimizer_used` field**: `SkillHealthEntry` now tracks `"llm" | "heuristic" | "none"` — distinguishes LLM optimization from heuristic fallback from no-op
- **First real deployment**: `save-memory` skill optimized (score 0.27 → 0.71), verified, zero rollbacks

**Memory Architecture v2 — Transcript Indexing + Temporal Validity**
- **TranscriptStore**: New `transcript_indexer.py` — indexes Claude Code JSONL session transcripts into FTS5 + sqlite-vec for verbatim recall (MemPalace benchmark: 96.6% vs 84.2% for summaries)
- **Temporal validity**: Memory entries carry `valid_from` / `superseded_by` metadata; superseded entries auto-downweighted (0.1×) in recall scoring
- **Knowledge Library sync**: Moved vector+FTS5 indexing outside git-rev gate — Knowledge/ files written by hooks and jobs (DailyActivity, Signals) are now indexed even without git commits
- **Transcript dir scoping**: Derives project dirs from config (`workspace_path`, `swarmai_dir`) instead of hardcoded paths; warns on slug mismatch; falls back to full scan

**SessionMiner Improvements**
- **Single-pass mining**: 56× fewer file reads via consolidated transcript scanning
- **Eval overwrite**: `save_evals` switched from append to overwrite mode — prevents unbounded eval file growth across cycles
- **Public property**: `last_transcripts_scanned` exposed as `@property` (replaces fragile `getattr` access)

**Pytest Safety System**
- **PreToolUse hook**: `pytest-safety-hook.sh` blocks full test suite execution unless `SWARMAI_SUITE=1`; allows targeted tests (`test_*.py`, `--lf`, `-k`), make targets (`make test-*`)
- **Sanitization**: Strips `| tail` / `| head` pipes and normalizes `.venv/bin/python` paths in pytest commands

**OOM Prevention**
- **Spawn cost model**: Corrected RSS estimation for subprocess spawning — prevents cascade where restart attempts consume more memory than they free
- **Proactive restart threshold**: Triggers restart-with-resume before macOS jetsam kills the process

### Fixed

- **Sender retry crash**: `send_with_retry` used `attachments` before assignment in retry loop — reordered to build attachments before body
- **Pytest hook bypass**: `make\s+test(-\w+)?` regex let bare `make test` (full suite) bypass Guard 1 — tightened to `make\s+test-\w+`
- **Changelog `skills_checked` bug**: Was writing `transcripts_scanned` as `skills_checked` — now correctly writes both fields
- **`.bak` file cleanup**: Stale SKILL.md.bak files from previous evolution cycles now cleaned up after successful cycle completion
- **Correction quality gates**: Added snake_case identifier rejection, agent monologue pattern detection, and code fragment filtering to prevent garbage from leaking into skill instructions
- **`memory_health` temporal superseding**: Stale decisions now actually trigger superseding instead of being silently skipped
- **SessionMiner race condition**: Fixed concurrent transcript access during mining
- **SkillMetricsHook**: Extracts skill name from summary text when tool input is absent
- **Knowledge indexing gap**: Only 1/160 Knowledge files were being indexed due to git-rev gate — moved sync outside gate
- **save-memory leaked code fragments**: Removed 15 lines of leaked code analysis text from SKILL.md
- **save-memory append-only contradiction**: Test entry removal instruction now has explicit exception in the append-only rule
- **DSPy guard**: Added `import dspy` / `from dspy` assertions to prevent accidental DSPy references in evolution_optimizer

### Changed

- **README rewrite**: Condensed from ~500 lines to ~280 lines — table-based layout, approximate numbers ("55+ skills", "3,000+ tests", "800+ commits"), synchronized EN/ZH versions
- **CMHK skills gitignored**: Internal AWS skills moved to `s_cmhk-*` prefix and added to `.gitignore` — local-only, not in public repo
- **Evolution confidence formula**: Evidence step function recalibrated with n≥2 band; density boost recalibrated with >0.05 band
- **Heuristic scoring path**: `optimize_skill` now uses heuristic's pre-built `new_text` directly for scoring instead of reconstructing from changes (avoids regex vs exact-match divergence)

## [1.4.0] - 2026-04-10

### Added — Next-Gen Agent Intelligence

Three-phase delivery building a closed-loop self-improvement pipeline for the agent.

**Phase 1 — Safety + Observability**
- **MemoryGuard**: Pre-write scanner for MEMORY.md, EVOLUTION.md, USER.md — detects and redacts AWS keys, OpenAI keys, Bearer tokens, PEM blocks, passwords; rejects prompt injection, role hijack, and exfiltration patterns; strips invisible Unicode characters
- **SkillMetrics**: SQLite-backed per-skill invocation tracking (outcome, duration, user satisfaction) with `get_evolution_candidates()` for optimization targeting
- **Section Budget Caps**: MEMORY.md sections capped (Key Decisions 30, Lessons Learned 25, COE Registry 15, Recent Context 30, Open Threads 10) with overflow archived to `Knowledge/Archives/` instead of deleted
- **Entry Relationships**: Auto-detected `refs:` field in memory index entries linking cross-references between COE/KD/RC/LL/OT entries

**Phase 2 — Understanding + Recall**
- **UserObserver**: Post-session hook extracts correction patterns, expertise indicators, and language preferences (CJK/Kana/Hangul) from user messages; consolidates observations in JSONL; surfaces USER.md update suggestions via proactive briefing
- **SessionRecall**: FTS5-based full-text search across session messages with multi-signal relevance ranking (match density, recency, content richness), character budget distribution, sentence-boundary truncation, and word-boundary topic matching
- **SkillRegistry**: Compact skill index injected into system prompt — scans `s_*/SKILL.md`, categorizes into 11 groups, caches with mtime-based hash invalidation
- **SkillGuard**: Static analysis scanner for skill content with 4 trust levels (BUILTIN/USER_CREATED/AGENT_CREATED/EXTERNAL) and 5 pattern categories (exfiltration, prompt injection, destructive, persistence, privilege escalation)

**Phase 3 — Autonomous Evolution**
- **SessionMiner**: Mines Claude Code JSONL transcripts for per-skill eval datasets with two-stage filtering (keyword heuristic → structured extraction) and MemoryGuard secret scrubbing
- **SkillFitness**: Multi-signal heuristic scoring (Jaccard 30% + bigram overlap 30% + containment 40%) replacing pure Jaccard; adaptive threshold scaling with example count
- **EvolutionOptimizer**: Heuristic skill optimization via correction pattern analysis ("don't X" → remove, "should Y" → add) with constraint gates (15KB size, 20% growth, SkillGuard injection scan), SKILL.md backup before deploy, EVOLUTION.md audit logging
- **Evolution Cycle**: End-to-end pipeline (mine → score → optimize → deploy) triggered by session-close hook (7-day interval) and weekly scheduled job (Thursday 04:00 UTC) as fallback
- **Retention Policies**: DailyActivity >90d archived, Archives >365d deleted (MEMORY-archive-* preserved), resolved Open Threads >7d removed from MEMORY.md and archived under fcntl.flock
- **SkillMetricsHook**: Post-session hook detecting Skill tool_use blocks and "Using Skill:" text patterns, recording invocations with correction detection

### Fixed

- **MemoryGuard bypass**: All MEMORY.md write paths (distillation, context_health, memory_health) now sanitize through MemoryGuard — not just locked_write.py
- **FTS5 missing UPDATE trigger**: `messages_fts` now has INSERT + DELETE + UPDATE triggers; edited messages no longer return stale search results
- **Password regex false positives**: Tightened to require quoted values — `password: myconfig_value` no longer triggers redaction
- **Non-atomic observation writes**: UserObserverHook uses tempfile + atomic rename instead of clear + append
- **SessionRecall duplicate FTS5 setup**: Removed redundant table/trigger creation; DB migration in sqlite.py is single source of truth
- **DB migration skip_init path**: Returning users (seed-sourced DB) now run migrations on startup — skill_metrics table and messages_fts FTS5 table are created
- **Transcript directory resolution**: Picks most-recently-active project subdir instead of first-alphabetical
- **Shared correction patterns**: Extracted `CORRECTION_PATTERNS` to `extraction_patterns.py` — imported by both session_miner and skill_metrics_hook
- **Dead code removal**: Deleted unused `SkillCreatorTool` (zero production callers); removed dead `_estimate_duration` loop; replaced DSPy aspirational references
- **Build script**: Added all 12 new modules to PyInstaller hidden imports; renamed Owork → SwarmAI in startup log paths

## [1.3.0] - 2026-04-09

### Added

- **Markdown-to-PDF Pipeline**: New `md2pdf.sh` using pandoc + tectonic
  (XeLaTeX) with professional and minimal LaTeX templates — full CJK support,
  syntax-highlighted code blocks, styled blockquotes, booktabs tables, and
  optional TOC generation
- **Broad File Attachment Support**: File picker and paste handler now accept
  40+ file types (Office docs, audio, video, code files, non-native images)
  instead of just images and plain text
- **Smart File-Type Hints**: Backend generates file-type-specific agent
  guidance (e.g. "use /s_pptx skill" for .pptx, "use /s_whisper-transcribe"
  for audio) instead of generic "use Read tool"
- **SVG Editor Preview**: SVG files route to the text editor with a visual
  Preview toggle (same UX pattern as markdown preview)
- **Expanded Binary File Coverage**: BinaryPreviewModal now handles ~20 more
  formats (aac, m4a, webm, pyc, class, sqlite, fonts, jar/war, etc.)

### Fixed

- **Binary File Crash**: Files attached via File Picker no longer crash with
  "Prompt is too long" — all non-native types now route through the backend's
  save-to-Attachments pipeline with base64 encoding instead of inline text
- **Non-Native Multimodal Handling**: When SDK supports multimodal, non-native
  blocks (Office, audio, video) are correctly converted to path hints while
  Claude-native formats (jpeg/png/gif/webp/pdf) pass through natively
- **Size Limit Accuracy**: PDF/document limit reduced from 25MB to 23MB to
  account for base64 overhead under 32MB Bedrock payload limit; text/csv
  raised to 5MB since they now use path_hint instead of inline tokens
- **Unsupported File Modal Resilience**: BinaryPreviewModal shows friendly
  file-info UI even when metadata fetch fails (e.g. 404), with selectable
  file path display
- **Radar Sidebar**: in_discussion todos now appear in the Radar sidebar

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

[1.3.0]: https://github.com/xg-gh-25/SwarmAI/releases/tag/v1.3.0
[1.2.3]: https://github.com/xg-gh-25/SwarmAI/releases/tag/v1.2.3
[1.2.0]: https://github.com/xg-gh-25/SwarmAI/releases/tag/v1.2.0
[1.1.2]: https://github.com/xg-gh-25/SwarmAI/releases/tag/v1.1.2
[1.1.1]: https://github.com/xg-gh-25/SwarmAI/releases/tag/v1.1.1
[1.1.0]: https://github.com/xg-gh-25/SwarmAI/releases/tag/v1.1.0
[1.0.0]: https://github.com/xg-gh-25/SwarmAI/releases/tag/v1.0.0
