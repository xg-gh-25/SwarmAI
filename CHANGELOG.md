# Changelog

All notable changes to SwarmAI will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.9.1] - 2026-04-30

### Added

- **CI Pipeline (4-gate)**: New `ci.yml` workflow — backend (Linux smoke import + tests), backend-windows (auto-discover module import), frontend (tsc + build), version-check (6 files). Branch protection on main with required status checks
- **Shared AI Context (AGENTS.md)**: Rewritten as "AI emergency manual" — 7 known landmines, process topology, debug flowchart, DDD pointers. Auto-synced to SwarmWS via symlink
- **Hive Manager Skill**: `s_hive-manager` for deploying, updating, and managing Hive instances via chat
- **CONTEXT.md Glossary**: DDD-inspired ubiquitous language — 19 canonical terms across 5 domains (session, task, skill, memory, project)

### Fixed

- **v1.9.0 P0 — App Won't Start**: `isDesktop()` checked Tauri 1.x `__TAURI__` instead of 2.x `__TAURI_INTERNALS__` — all API calls hit SPA fallback returning HTML. Frontend startup chain now has diagnostic logging (`[Platform]`, `[Health Check]`)
- **v1.9.0 P0 — GitHub Release Failed**: 4 independent CI bugs — `import fcntl` (Unix-only) in sqlite.py, BSD `sed -i ''` on Windows, build-hive missing pip install, triple workflow race on tag push
- **Cross-Platform File Locking**: Moved `file_lock.py` from `core/` to `utils/` (eliminates circular import), migrated all 13 bare `import fcntl` sites to `utils.file_lock`, added `fd.seek(0)` for correct Windows mutex
- **Backend Log Rotation**: `FileHandler` → `RotatingFileHandler` (10MB × 3 backups), daemon/sidecar write separate log files (no multi-process rotation race)
- **SQLite busy_timeout**: 100ms → 5000ms — eliminates "database is locked" under async test teardown
- **Release Pipeline**: 9 gaps fixed — removed triple tag trigger, `npm install` → `npm ci`, `build-backend.sh` exits non-zero on verify failure, version sync before frontend build, `package-lock.json` in sync-version.sh
- **Slash Command Picker**: 9 functional bugs fixed across 3 commits
- **Hive E2E**: 3 P0 bugs (deploy + start), 8 settings bugs, error handling improvements
- **SkillsPage SSE**: Error stuck streaming + OnboardingPage retry

### Changed

- **Release Flow**: Stay on main → push → CI green → tag. No branches, no PRs. STEERING.md scope gate (≤20 commits without sign-off)
- **Pipeline DELIVER**: CI health check as blocking gate before declaring delivery complete
- **dev.sh**: Logs to `backend-dev.log` (not shared `backend.log`), added `verify_build.py` to build command

## [1.9.0] - 2026-04-29

### Added

- **Hive Cloud Deployment**: Full EC2 deployment lifecycle — boto3 provisioner (deploy/stop/start/update/delete), CloudFront CDN, Caddy reverse proxy with basic auth, passphrase password generation, reset-password API, Manager UI with deploy progress + live polling
- **Unified FileViewer**: Modular renderer architecture replacing monolithic BinaryPreviewModal — 7 format-specific renderers (Image, PDF, CSV, HTML, Audio, Video, Unsupported), tabbed navigation, status bar, proper type system
- **Unified Release Pipeline**: `prod.sh release-all` builds Desktop + Hive package + verification + GitHub Release in one command, with CI/CD workflow (build-macos, build-windows, build-hive jobs)
- **Skill Platform Filtering**: SKILL.md `platform: all | macos | desktop` field — Hive mode auto-excludes platform-specific skills (7 tagged: apple-reminders, peekaboo, sonos, system-health, whisper-transcribe, podcast-gen, video-gen)
- **Thinking Toolkit**: 4 pipeline upgrades — T1 grill protocol (stress-test plans), T2 constraint surfacing, T3 depth calibration, T4 caveman mode (70% token reduction)
- **Desktop Update System**: DB migration runner + daemon sync + update toast UX with safety checks
- **GitHub Trending Skill**: Daily trending repos adapter + skill with star/language/relevance analysis
- **Daily Todo Resolution**: Auto-resolve stale Radar Todos via scheduled job
- **Pipeline Quality Gates**: Review completeness validator (8d), pre-mortem gate in EVALUATE, DDD auto-apply for mechanical proposals, MEMORY.md stale RC auto-archival, EVOLUTION.md quality gate (garbage competence removal)
- **Signature Skill Highlights**: ⭐ badge + tagline for flagship pipeline skills
- **Colored Accent Borders**: Visual section differentiation across Explorer, Radar, and Welcome Screen

### Fixed

- **PE Review — 32 Security Fixes**: Data integrity, correctness, and security hardening across 15+ files over 4 review rounds
- **Hive Security Hardening**: Restrict SG to CloudFront prefix list (eliminates DyePack alerts), block unknown URL schemes in webview, Caddy multiline reverse_proxy blocks, CloudFront DNS origin fix, external URLs open in system browser
- **3 P0 Fixes**: system_prompt coercion crash, retention policy test failures, PostUpdateToast race condition
- **DevOps E2E Audit**: 5 issues across release pipeline + pipeline lessons I1-I4 (RP25, blast radius trace, verify hardening)
- **Pipeline Structural Gaps**: Post-mortem fixes from run_91a6fb7e — dependency-scoped testing replaces full suite
- **react-pdf v10 CSS**: Import path `dist/esm/` → `dist/` for AnnotationLayer/TextLayer CSS
- **Hive Product Readiness**: 7 P0 gaps — setup wizard auth, settings panel, IMDS auto-fill, version resolution from GitHub API
- **Hook Lifecycle Wiring**: Dead methods wired into hook lifecycle, flock DDD writes, protect undistilled files

### Changed

- **Hive Manager Desktop-Only**: Management UI hidden in Hive mode (managed instances don't manage themselves)
- **Staleness Thresholds**: Working stale days 5d → 14d, pending 21d, split by status
- **Evolution Threshold**: Word count instead of char count for competence garbage detection

## [1.8.4] - 2026-04-27

### Fixed

- **Hot News Section Empty**: Schema migration gap in `signal_digest` merge left `feed_id` field missing — Welcome Screen Hot News section rendered empty
- **Welcome Screen ToDo Missing**: Workspace ID mismatch between backend and frontend constants caused ToDo section to show no items
- **Stocks Briefing Date Fallback**: Stocks section crashed when no report existed for today — now falls back to most recent available date
- **Stocks Section UX**: Collapsed by default to de-emphasize personal finance data; Pollinate output now organized in date-prefix directories
- **Zlib Decompression Retry**: Classified zlib decompression errors as retriable — corrupted transcript segments trigger auto-retry instead of permanent failure
- **Pollinate File Links**: File links now open in editor instead of broken paths; output routed to `Knowledge/Pollinate/`; 小红书 multi-image format support

## [1.8.3] - 2026-04-26

### Fixed

- **Version Downgrade Bug**: VERSION file (source of truth) was missing from release workflow — `dev.sh`/`prod.sh` silently reverted all versions on every startup
- **CI Release Workflow**: Replaced per-file `jq` hacks with `sync-version.sh` — single path for all version updates across local, prod, and CI
- **Token Usage Display**: Consumption sum excluded cache metrics — display was inflated ~350x

## [1.8.2] - 2026-04-26

### Added

- **Design Intelligence Database** (`s_frontend-design`): 67 visual styles, 161 color palettes, 57 font pairings, 161 industry-specific rules integrated into frontend-design and web-design-review skills
- **Deep Research Phase 0 Intent Planner**: Auto-classifies research depth (quick/standard/deep/exhaustive) from query complexity before executing rounds
- **Learn Content Depth Calibration**: Auto-adjusts extraction depth based on content type (article vs reference vs tutorial)

### Fixed

- **Deep Research 5 Gaps**: Improved source deduplication, citation formatting, search query refinement, synthesis structure, and error recovery
- **Learn Content Gaps**: Better URL validation, duplicate card detection, metadata extraction, KNOWLEDGE.md index updates, and cross-reference linking

## [1.8.1] - 2026-04-26

### Added

- **Release Skill** (`s_release`): 10-step release workflow — version bump across 4 files, CHANGELOG, README sync (EN + CN), lockfile regen, git tag, GitHub Release. Patch shortcut and rollback procedures included

### Fixed

- **WelcomeScreen Responsive Layout**: Sections now reflow to 1-2 columns based on screen size, TODO data source corrected to use Radar API, Radar section order fixed
- **Release Skill Gaps**: Pre-flight checks (clean tree, tag collision, 4-file sync), Cargo.lock + package-lock.json regeneration, README sync step, rollback instructions

### Changed

- **Explorer Tree Node Font**: Reduced font size for clearer visual hierarchy in SwarmWS file tree

## [1.8.0] - 2026-04-26

### Added

- **Pollinate Media Engine** (`s_pollinate`): 8-stage content pipeline (EVALUATE→REFLECT) transforms any message into poster (SVG/PNG), short video (4K MP4), podcast (TTS + BGM), or narrative. 77 files, template-driven layouts per format × audience, multi-platform publishing scripts (`publish_meta.py`, `publish_poster.py`)
- **Engine-Aware SSML Optimization**: Polly TTS SSML strategy adapts per engine — neural voices get prosody/emphasis, generative voices get plain text (SSML degrades quality). English pronunciation for CJK voices ("API" sounds correct, not "ā-pī-ài")
- **Briefing Hub v2**: 2-column Welcome screen with grouped signal sections (Hot News, Stocks, Working status) + unified RadarSidebar with section-based layout. Slack morning briefing template with channel-specific formatting
- **SwarmWS Explorer 3-Tier Redesign**: Primary sections (Knowledge, Projects, Attachments) with accent backgrounds, Secondary directories, System section. Section headers with SVG navigation icons. Replaced flat zone layout
- **Session Pre-Warming** (MeshClaw pattern): Daemon pre-spawns IDLE subprocess with full system prompt (including `channel_context`) at startup via `prewarm_channel_session()`. `adopt_prewarmed_unit()` re-keys on owner's first DM — eliminates ~4s cold-start latency
- **Slack 3-Tier Delivery**: `webhook → bot API → CLI` fallback chain for all Slack messaging. Signal notifications, morning briefings, and DMs route through optimal path. Pre-flight config check validates webhook URL and bot token
- **Autonomous Pipeline v2**: 57KB monolith split into 12 self-contained modules (`evaluate.py`, `think.py`, `plan.py`, `build.py`, `review.py`, `test_stage.py`, `deliver.py`, `reflect.py`, `confidence_score.py`, `shared_context.py`, `stage_runner.py`, `pipeline_orchestrator.py`). No cross-skill dependencies. Blocking budget check before every checkpoint
- **Suggested Focus Auto-Close**: Focus items dismissed after action + dismiss button in Welcome Screen
- **Signal-Notify-Slack**: Automatic Slack notification after daily signal digest completion

### Fixed

- **OOM Race Condition**: Comprehensive code review found and fixed race between `compute_max_tabs()` and concurrent session spawns — `_spawn_lock` now held through entire budget check + spawn sequence
- **SSML Injection Prevention**: User-provided text sanitized before embedding in SSML tags — strips `<`, `>`, `&` to prevent tag injection into Polly requests
- **Voice Map Safety**: TTS voice selection validates against known Polly voice IDs — unknown `voice_id` falls back to default instead of sending invalid parameter to Polly API
- **Ref Safety**: React refs checked for `null` before access in audio playback, voice recorder, and Explorer scroll handlers
- **State Machine Races**: Same-state transitions (`cold→cold`, `idle→idle`) now no-op — eliminates class of races where kill/spawn overlap produced invalid transitions
- **Session Kill Race**: `cold→cold` transition during concurrent `kill()` calls no longer crashes — state check before transition prevents `InvalidTransitionError`
- **Slack `msg_too_long`** (42 occurrences): Messages exceeding 40K chars now auto-truncated with `[truncated]` suffix instead of failing silently
- **`skill_metrics_hook` zlib Crash** (7 occurrences): Graceful handling of corrupted/truncated transcript JSONL files — skip instead of crash
- **Tauri `target="_blank"` Silent Failure**: Replaced with `plugin-opener` — WKWebView ignores `target="_blank"`, links now open in system browser
- **`asyncio` Import Shadowing**: Removed local `import asyncio` in `session_unit.py` that shadowed module-level import
- **Explorer User Issues**: 4 post-launch fixes for section spacing, icon alignment, scroll behavior, and hover states
- **Female Generative TTS**: Fixed persona selection + `cmn-CN` language code (was `zh-CN`) + turn-2 silence bug from stale audio context
- **Timezone-Aware JobResult**: Timestamps now use local timezone instead of UTC — morning briefing shows correct "today" boundary
- **Pipeline Confidence Scorer**: Now accepts explicit artifact paths for production use outside pipeline context

### Changed

- **Autonomous Pipeline Architecture**: From single 57KB `INSTRUCTIONS.md` to 12 focused modules with shared context. Each stage is independently testable and maintainable
- **RadarSidebar Visual**: 2px accent left-border per section for visual grouping
- **Pipeline Budget Rule**: Blocking budget check added before every pipeline checkpoint — prevents starting stages that can't complete within remaining context budget

## [1.7.0] - 2026-04-25

### Added

- **Voice Conversation Mode**: Bidirectional voice chat — speak → auto-transcribe → send → stream response → sentence-by-sentence TTS via Amazon Polly → auto re-open mic. 6-state machine (`off → listening → processing → thinking → speaking → interrupted`), barge-in interrupt support, VAD silence detection (1.5s auto-stop), per-sentence language detection (en/zh), GainNode click-free audio with sequential playback queue
- **AudioKeepAlive**: Prevents WKWebView from tearing down CoreAudio session on idle/background — loops a 0.001-volume silent WAV at app root (VoiceBox pattern)
- **Sentence Splitter**: Streaming text-to-sentence parser for TTS — handles abbreviations (23 entries), decimal numbers, URLs, fenced code blocks, CJK punctuation, markdown stripping. Min 10 chars, max 3000 chars (Polly neural limit)
- **Chinese Trending News Feed**: 11-platform hot-search adapter (Weibo, Zhihu, Toutiao, Baidu, Douyin, Bilibili, WallStreetCN, ThePaper, CLS, iFeng, Tieba) via newsnow public API. Interruptible rate-limiting sleep via `threading.Event` for graceful shutdown
- **Multi-Channel Notification Skill**: 9 channels (Feishu, DingTalk, WeCom, Telegram, Email/SMTP, ntfy, Bark, Slack, generic Webhook) with JSON injection prevention in webhook templates. Auto Slack notification after signal digest
- **WelcomeScreen Redesign**: Live session briefing with Suggested Focus (P0/P1/P2), External Signals (48h freshness), Recent Jobs (24h), Radar Todos (priority-sorted), Learning Insight. Interactive — focus items send as chat, signals ask Swarm, jobs open result files, todos send work packets
- **Radar Todos in Briefing**: Pending/overdue todos surfaced in system prompt and Welcome Screen via direct SQLite read (WAL mode safe). Priority ordering with overdue badges
- **DailyActivity JSONL Sidecar**: Dual-write (markdown + JSONL) — structured sidecar consumed by distillation hook directly, eliminating regex parsing. Best-effort write doesn't block primary markdown. New fields: `signal_driven_actions`, `process_reflection`
- **Learn Content Skill**: Structured knowledge card ingestion from URLs/text/files. 3-tier fetch chain (WebFetch → curl with WeChat UA → user paste). Cross-references MEMORY.md keys and existing cards. KNOWLEDGE.md index auto-update
- **Token Usage Tracking**: Per-session token consumption persisted to SQLite, API endpoint for history, TopBar display
- **Signal Digest Causal Links**: Signal → session decision causal chain recorded in DailyActivity
- **Job Result Sidecar**: `.job-results.jsonl` with structured output for Welcome Screen job results section
- **macOS Entitlements**: `com.apple.security.device.audio-input` for microphone, `NSMicrophoneUsageDescription` for TCC dialog

### Fixed

- **Voice TTS Replay on Turn Change**: `lastProcessedLenRef` now snaps to `latestTextContent.length` (not 0) in all 6 reset sites — prevents re-processing previous response through TTS when next turn starts
- **Voice Mode Persists on Internal Tab Switch**: Track `sessionId` changes to detect SwarmAI tab switches (browser `visibilitychange` only fires on window focus loss). Exits voice mode, stops audio, releases mic
- **Signal Digest L4 JSON Empty**: Two-tier eviction — 48h soft (only when new items arrive, prevents empty Welcome Screen) + 7-day hard (always runs, prevents indefinitely stale data). Dedup cache raised 500 → 2000 URLs
- **Trending API 403**: Added browser-like `User-Agent` + `Referer` headers required by newsnow API
- **Slack `#N` Channel Mention**: Changed `#N` → `Top N` in trending summary — Slack parses `#` as channel mention
- **`asyncio.get_event_loop()` Deprecation**: Replaced with `get_running_loop()` in `voice_synthesize.py` (deprecated in Python 3.12+)
- **Dead Async Block in Briefing**: Removed convoluted async→sync fallback in `build_session_briefing_data` todo fetch — only the direct SQLite path ever executed
- **Trending `url` Variable Shadowing**: Renamed to `api_url` (API endpoint) and `item_url` (result item) for clarity
- **Proactive Intelligence Dead Code**: Removed self-referential `_estimate_thread_age.__wrapped__` assignment — single clean delegation to `_raw_estimate_thread_age`
- **Polly Client Region Mismatch**: Removed `region` parameter from `_get_polly_client()` — `lru_cache(maxsize=1)` is now safe (no parameter variation)
- **Voice Router Unused Import**: Removed `Request` from FastAPI imports
- **Rate Limit Docstring Drift**: Updated module and endpoint docstrings from "60" to "120" after rate limit bump

### Changed

- **Voice TTS Rate Limit**: 60 → 120 requests/minute — supports sustained voice conversation (20-sentence response = 20 calls in ~30s)
- **Voice Input Validation**: Added Pydantic regex patterns — `language: ^[a-z]{2}(-[A-Z]{2})?$`, `voice_id: ^[A-Za-z]{2,20}$`
- **Hooks Barrel Exports**: Added `useVoiceConversation`, `useAudioPlayer` and their types to `hooks/index.ts` for consistency
- **PyInstaller Verification**: `voice_synthesize` added to critical module checklist in `verify_build.py`
- **Autonomous Pipeline**: Added RP18 (integration trace calling convention) and RP19 runtime patterns
- **Tool-Failure-Exhaustion Rule**: Strengthened in AGENT.md context

## [1.6.3] - 2026-04-22

### Added

- **Voice Input E2E**: Mic button in chat input records audio via MediaRecorder API, sends to backend, transcribes via Amazon Transcribe Streaming SDK using existing AWS SSO credentials. Backend `POST /api/chat/transcribe` with ffmpeg PCM conversion, 25MB upload cap, 30s ffmpeg timeout, 60s Transcribe timeout. Frontend `useVoiceRecorder` hook with idle→recording→processing state machine, track death detection, recorder error handler, unmount cleanup, and accessibility support (`aria-pressed`, `aria-label`)
- **Slack HTTP Polling Fallback**: Automatic fallback from Socket Mode WebSocket to HTTP polling via `conversations.history()` when WS thread dies 3+ times consecutively. 5s polling interval, DM-first channel discovery, periodic WS reconnection attempts every 5 minutes. Solves VPN-blocked environments (SSLEOFError, DNS failure, proxy 403)
- **Memory Frequency Gate**: Entries must appear in ≥2 DailyActivity files before promotion to MEMORY.md. One-off observations stay in DailyActivity; recurring themes graduate. Cold-start safe (single DA file passes unconditionally)
- **Memory Usage-Based Eviction**: `context_health_hook` tracks memory key references (`[RC04]`, `[KD05]`, etc.) in recent DailyActivity files, writes counts to `.context/.memory-usage.json`. Distillation evicts lowest-usage entries first when section caps are exceeded, replacing oldest-first ordering
- **Pipeline User-Path Trace**: Mandatory BUILD step traces each acceptance criterion through real production code paths — catches bugs that TDD + unit tests miss (mock data mismatches, cross-component competing paths, state reset on object recreation)
- **Pipeline PROBE Step**: For new API endpoints consumed by frontend, writes integration test via `httpx.AsyncClient` through real ASGI stack — catches Content-Type and serialization bugs that TestClient misses
- **Pipeline Runtime Pattern Checklist**: Expanded to 12 patterns (RP1-RP12) covering subprocess orphans, missing binaries, React hook cleanup, stale closures, FormData Content-Type, setTimeout leaks, API boundary naming, barrel exports, SDK handler reassignment, and unstable callback refs
- **Pipeline Cross-Boundary Wire Test**: Verifies Content-Type match, field name match, response shape match, and error shape match when changeset spans frontend + backend
- **Pipeline REFLECT Auto-Maintenance**: Post-pipeline review findings auto-classified as existing pattern missed or new pattern — pipeline learns from every review cycle

### Fixed

- **OOM on 16GB Machines**: `spawn_budget()` now takes `alive_count` with concurrent penalty factor (0.5× per alive session). Cost inflates: 0 alive = 1200MB, 1 alive = 1800MB, 2 alive = 2400MB — blocks 3rd session on 16GB at 12.4GB+ used. 36GB machines unaffected
- **Slack Polling Never Activates**: Transport errors in WS thread fired `_on_error` → gateway destroyed adapter → fresh `_ws_fail_count=0` → counter never reached threshold. Fix: only escalate auth errors to gateway; transport errors stay internal for health monitor
- **Slack Polled Messages Missing Channel**: `conversations.history` messages lack `channel` field → empty `external_chat_id` → routing broken. Fix: inject `channel_id` into polled message data before normalizing
- **Slack Handler Leak on Reconnect**: `_try_socket_mode_reconnect` now closes old `self._handler` before reassigning; `_ws_health_monitor` resets `self._handler = None` before restart to prevent reusing crashed handler state
- **Voice Recorder WKWebView Crash**: Removed `sampleRate: 16000` constraint — WKWebView (Tauri/Safari engine) can't reconfigure hardware capture away from 44.1/48kHz. Backend ffmpeg resamples to 16kHz anyway
- **Global Content-Type Breaking FormData**: Removed default `Content-Type: application/json` from axios instance — was overriding Axios auto-detection and breaking multipart uploads for voice transcription
- **TranscribeResult Naming Convention**: `duration_ms` → `durationMs` with proper `TranscribeRawResponse` → `TranscribeResult` snake→camel conversion in `chat.ts`
- **Voice Recorder Barrel Export**: Added `useVoiceRecorder` + `VoiceState` to `hooks/index.ts`
- **Upload Form Resource Leak**: Added `await form.close()` in `/transcribe` endpoint finally block — releases `SpooledTemporaryFile` promptly
- **Unstable Voice Callbacks**: Wrapped `onTranscript`/`onError` in `useCallback` — stable references avoid unnecessary hook re-creation
- **Test Warnings**: Sync mock for `kill()` (os.kill is synchronous), cancel monitor/poll tasks on teardown

### Changed

- **Cargo.lock Dependencies**: Bumped rand 0.8.6, tokio 1.52.1, uuid 1.23.1, webpki-roots 1.0.7
- **Info.plist**: Added `NSMicrophoneUsageDescription` for macOS microphone permission dialog
- **PyInstaller Bundle**: Added `amazon-transcribe` + `awscrt` with 3 new capability checks (41 total)
- **save-memory Skill**: Strengthened verification and token-budget directives

## [1.6.1] - 2026-04-15

### Fixed

- **sqlite_vec + 18 missing modules in PyInstaller build**: Vector search (recall_engine, embedding_client, knowledge_store, memory_embeddings, transcript_indexer, etc.) was silently disabled in production for 5 days — try/except ImportError masked the failure. Hybrid memory recall now works: 0.6×vector + 0.4×keyword scoring
- **Spawn cost model 5× overestimate**: `record_spawn_cost` recorded tree RSS (CLI + 7 MCPs = 979MB) instead of main process RSS (300-400MB). Combined with 1200MB floor, `compute_max_tabs()` returned 2 instead of 4 — blocked 3rd chat tab on 36GB machines
- **Stale PyInstaller hiddenimports**: Removed passlib (4 entries), jose (2), pyyaml (1) — all produced build ERRORs every run, packages were not used
- **MCP auth failure detection**: Jobs now detect auth errors in agent output, mark as `auth_failed`, and retry on next scheduler tick instead of incrementing failure count

### Added

- **Post-build capability verification** (`verify_build.py`): Launches the built binary, checks 38 capabilities (imports, data files, native extensions) against a manifest. Critical failures block release. Runs automatically at end of `build-backend.sh`
- **Verification endpoints**: `/api/system/verify-import`, `/api/system/verify-data`, `/api/system/verify-native` (gated behind `SWARMAI_VERIFY_BUILD=1`), `/api/system/capabilities` (always-on for runtime diagnostics)

### Changed

- **Auto-discovered local modules**: Replaced 200-line hardcoded module list in `build-backend.sh` with `glob.glob` auto-discovery. New .py files are automatically included in builds — the class of bug where modules are forgotten is now structurally impossible

## [1.6.0] - 2026-04-15

### Added — Lazy Skills, Inline Review, RecallEngine, TCC Protection

**Lazy Skill Loading + Manifest System**
- **Tiered skill loading**: 15 `always` skills inject full SKILL.md (~100 tok each), 46 `lazy` skills inject minimal stubs (~25 tok each) with "Read INSTRUCTIONS.md" directive. ~3,650 tokens/session saved (49% reduction in skill listing)
- **manifest.yaml**: 16 skills with complex scripts now declare entry points in YAML manifests; `manifest_loader.py` provides Pydantic models + cached YAML parser
- **Migration**: `migrate_skills.py` (idempotent, `--dry-run`) splits SKILL.md → SKILL.md stub + INSTRUCTIONS.md for lazy skills, adds `tier:` frontmatter
- **Skill Registry**: `generate_compact_registry()` + `_read_tier()` utility; SDK handles discovery via `.claude/skills/` projection

**RecallEngine L2/L3 Activation**
- **Post-first-message recall injection**: After the user's first message in a session, RecallEngine queries vec+FTS5 indexes and injects top-N relevant memories into the next system prompt refresh
- **Hybrid scoring**: 0.6×vector + 0.4×keyword; temporal decay; superseded-entry downweighting

**Inline Comments on Diff View**
- **CommentPopover**: Click line numbers in DiffView to leave review comments; comments persist to sessionStorage across tab switches
- **ReviewModeGutter**: Responsive scroll tracking, Escape key propagation, discoverability hints

**Pipeline Quality Gates**
- **SMOKE and integration trace**: Now BLOCK (not WARN) — pipeline fails if smoke check or replace-parity trace detects regressions
- **Conditional UX Review**: Pipeline REVIEW stage includes UX review when frontend files are changed

**macOS TCC Protection**
- **PreToolUse hook**: Blocks Claude Code tool calls that would trigger macOS TCC "Desktop access" permission popups
- **Trash safety**: Replaced `osascript` trash with `shutil.move` — eliminates Finder access TCC popup

### Fixed

- **Shadow recall used plain sqlite3**: Vector search was silently broken — recall queries bypassed vec0 extension. Fixed: all recall paths use the shared `get_db_connection()` with sqlite_vec loaded
- **Keyword extraction**: Missed English words adjacent to CJK characters (e.g., "AWS用量" → only "用量", missing "AWS")
- **Context window noise**: Skip empty templates, single DailyActivity file, remove duplicate skill registry from system prompt
- **Review comments lost on tab switch**: Comments persisted to sessionStorage with session-scoped keys
- **Cache key shadow**: Manifest mtime hash collision with skill name — added path component to cache key
- **PE review rounds 1-3**: 25+ findings across recall injection, manifest loader, pipeline, dead code, conn leaks, CJK handling, growth checks, JSONL rotation

### Changed

- **Dead code removal**: TokenPayload, decode_token, frontmatter module, artifact injection — all unused after prior refactors
- **Slack auth error detection**: Circuit-breaking on persistent auth failures in channel adapter

## [1.5.4] - 2026-04-14

### Fixed

- **Shadow recall sqlite3 connection**: vec0 extension not loaded on shadow recall path — vector search returned empty results silently. Fixed: uses shared connection factory

### Changed

- **Auto-deploy built binary**: `dev.sh build` now copies binary to daemon directory and restarts daemon automatically

## [1.5.3] - 2026-04-14

### Added

- **Memory & Evolution E2E gaps**: 4 structural fixes — G1 recommend visibility (proactive intelligence surfaces medium-confidence evolution recommendations in session briefing), G2 real-data tests, G3 shadow recall (recall engine tested against actual indexed data), G4 heuristic-first optimizer (falls back from LLM when evidence is thin)

### Fixed

- **PE review findings**: Engine scope leak, unused imports, tautology test, embed json alias
- **PE review round 2**: 6 findings across G1/G3/G4 modules
- **PE review round 3**: Connection leak, CJK handling, growth check, JSONL rotation
- **SKILL.md stale wording**: "deploy" → "review changes" across evolution pipeline skill docs

## [1.5.2] - 2026-04-13

### Fixed

- **Daemon restart timeout**: FTS5 `rebuild` ran on every startup (30-50s) — now only rebuilds when FTS5 index is empty but messages exist; startup db phase drops from ~35s to <1s
- **macOS TCC "Desktop access" popup**: Transcript indexer fell back to scanning `~/.claude/projects/` base dir (contains Desktop-path dirs) — now uses `initialization_manager` as authoritative source, never scans base dir
- **Transcript slug mismatch**: Old slug computation (`lstrip("/").replace("/", "-")`) produced `Users-gawan-.swarm-ai-SwarmWS` but Claude SDK uses `-Users-gawan--swarm-ai-SwarmWS` (leading `-`, `.` → `-`) — fixed `_path_to_slug` to match actual SDK format
- **UserObserver TypeError**: `observe_session` crashed with `sequence item 0: expected str instance, list found` when message content was a list of content blocks (Claude SDK format) — new `_extract_text()` handles both str and list content
- **workspace_path never persisted**: `initialization_manager` now writes `workspace_path` to `config.json` at startup — other modules can read it without circular imports
- **Version strings stale**: `backend/config.py` was `1.0.0`, `package.json` and `tauri.conf.json` were `1.1.1` — all bumped to `1.5.1`

### Changed

- **dev.sh daemon lifecycle overhaul**: Restart uses `bootout` → `_wait_port_free` → `bootstrap` (replaces broken `kickstart -k`); 90s smart health check with phase detection; failure diagnostics (launchd state, port, binary version, stderr); binary version tracking via `.version` file with staleness warnings
- **swarmai_backend.sh**: Logs binary version from `.version` file on startup

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
