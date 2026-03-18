# Requirements Document

## Introduction

Multi-Session Re-Architecture decomposes the 5,406-line `agent_manager.py` god object into 4 focused modules built around the SessionUnit model. The goal is to achieve parallel, isolated, and stable multi-tab chat sessions while reducing total LOC by ~71%. The work spans 4 phases: module extraction (zero behavior change), lifecycle simplification, frontend single-store migration, and lazy MCP loading. Three P0 concurrency fixes are prerequisites.

Reference: `Knowledge/Architecture/MULTI_SESSION_REARCHITECTURE_v4.md` (approved v4, co-authored by Swarm and Kiro).

## Glossary

- **SessionUnit**: A self-contained state machine owning one tab's complete subprocess lifecycle. States: COLD, IDLE, STREAMING, WAITING_INPUT, DEAD.
- **SessionRouter**: Thin routing layer that dispatches chat requests to SessionUnits and enforces the concurrency cap.
- **PromptBuilder**: Pure-function module that assembles system prompts and ClaudeAgentOptions from agent config, context files, and runtime state.
- **LifecycleManager**: Single background loop responsible for TTL-based cleanup, serialized hook execution, and startup orphan reaping.
- **AgentManager**: The current 5,406-line monolith (`backend/core/agent_manager.py`) being decomposed.
- **ClaudeSDKClient**: The Claude Agent SDK client that owns one subprocess and one active query.
- **Concurrency_Cap**: The MAX_CONCURRENT=2 integer limit on simultaneously alive subprocesses.
- **TTL**: Time-to-live for idle sessions before automatic cleanup (target: 12 hours).
- **Hook_Pipeline**: The ordered sequence of post-session lifecycle hooks (auto-commit, daily activity extraction, distillation, evolution maintenance).
- **TabMapRef**: The current authoritative frontend tab store (`useRef<Map<string, UnifiedTab>>`).
- **Zustand_Store**: The target single-store replacement for the dual-state (tabMapRef + useState) frontend pattern.
- **MCP**: Model Context Protocol servers spawned as child processes of the Claude CLI subprocess.
- **EVOLUTION_CHANGELOG**: The JSONL file (`EVOLUTION_CHANGELOG.jsonl`) tracking self-evolution changes, currently lacking file-level locking.
- **DailyActivity_Lock**: The asyncio lock used during daily activity extraction, currently missing a timeout.

## Requirements

### Requirement 1: SessionUnit State Machine

**User Story:** As a developer, I want each chat tab to be managed by an independent state machine with well-defined states, so that tab lifecycle is predictable and crash isolation is guaranteed.

#### Acceptance Criteria

1. THE SessionUnit SHALL implement exactly 5 states: COLD, IDLE, STREAMING, WAITING_INPUT, and DEAD.
2. WHEN a tab is created with no subprocess allocated, THE SessionUnit SHALL initialize in the COLD state.
3. WHEN a chat message is sent to a COLD SessionUnit, THE SessionUnit SHALL spawn a new ClaudeSDKClient subprocess and transition to STREAMING.
4. WHEN a streaming response completes, THE SessionUnit SHALL transition from STREAMING to IDLE.
5. WHEN the ClaudeSDKClient emits a permission prompt or continue_with_answer request, THE SessionUnit SHALL transition to WAITING_INPUT.
6. WHILE a SessionUnit is in STREAMING or WAITING_INPUT state, THE SessionRouter SHALL NOT evict that SessionUnit.
7. WHEN a subprocess crashes or is killed, THE SessionUnit SHALL transition to DEAD, clean up its own resources, and then transition to COLD.
8. IF a SessionUnit transitions to DEAD, THEN THE SessionUnit SHALL NOT affect any other SessionUnit's state or subprocess.
9. THE SessionUnit SHALL hold the environment spawn lock (`_env_lock`) only during subprocess creation and release the lock after the subprocess has inherited its environment copy.
10. THE SessionUnit module SHALL be approximately 300 lines of code and contain no prompt-building, routing, or hook-execution logic.

### Requirement 2: SessionRouter Concurrency Management

**User Story:** As a user with multiple chat tabs, I want my requests to be routed correctly and queued when both subprocess slots are busy, so that all tabs work without rejection.

#### Acceptance Criteria

1. THE SessionRouter SHALL enforce a maximum of 2 concurrently alive subprocesses (Concurrency_Cap).
2. WHEN a chat request arrives and fewer than 2 subprocesses are alive, THE SessionRouter SHALL dispatch the request to the target SessionUnit immediately.
3. WHEN a chat request arrives and both subprocess slots are occupied by STREAMING or WAITING_INPUT SessionUnits, THE SessionRouter SHALL enqueue the request with a 60-second timeout.
4. WHEN a queued request's 60-second timeout expires, THE SessionRouter SHALL return a timeout error to the caller.
5. WHEN a subprocess slot becomes available, THE SessionRouter SHALL dispatch the oldest queued request.
6. WHEN a chat request arrives and an IDLE SessionUnit occupies a slot needed by the requesting tab, THE SessionRouter SHALL reclaim the IDLE SessionUnit's subprocess before dispatching.
7. THE SessionRouter SHALL route `interrupt_session`, `continue_with_answer`, and `continue_with_cmd_permission` requests to the correct SessionUnit by session ID.
8. THE SessionRouter module SHALL be approximately 300 lines of code and contain no subprocess lifecycle, prompt-building, or hook logic.

### Requirement 3: PromptBuilder Pure Functions

**User Story:** As a developer, I want system prompt assembly and SDK option construction to be isolated in pure functions, so that prompt logic is testable without subprocess or network dependencies.

#### Acceptance Criteria

1. THE PromptBuilder SHALL assemble the system prompt from context files, runtime state, and agent configuration. It reads context files via ContextDirectoryLoader (IO-at-boundaries) but performs no subprocess operations or network calls.
2. THE PromptBuilder SHALL construct ClaudeAgentOptions (model, allowed tools, MCP config, sandbox config, hooks) from agent configuration and return a complete options object.
3. WHEN the PromptBuilder receives an agent configuration with MCP servers, THE PromptBuilder SHALL merge user-local MCP servers with agent-configured MCP servers.
4. WHEN the PromptBuilder receives a channel-bound session, THE PromptBuilder SHALL inject the channel MCP server into the options.
5. THE PromptBuilder SHALL compute the dynamic watchdog timeout based on session token count and user turn count.
6. THE PromptBuilder SHALL generate context window warnings when input token usage exceeds defined thresholds.
7. THE PromptBuilder module SHALL be approximately 500 lines of code and contain no subprocess lifecycle, routing, or hook logic.

### Requirement 4: LifecycleManager Background Loop

**User Story:** As a system operator, I want a single background loop managing session TTL, hook execution, and orphan cleanup, so that resource management is centralized and predictable.

#### Acceptance Criteria

1. THE LifecycleManager SHALL run a single background loop that checks session TTL and performs cleanup at a regular interval.
2. WHEN a SessionUnit has been IDLE for longer than 12 hours (TTL), THE LifecycleManager SHALL trigger cleanup of that SessionUnit.
3. THE LifecycleManager SHALL serialize all post-session lifecycle hooks (auto-commit, daily activity, distillation, evolution maintenance) through a single queue.
4. WHEN the application starts, THE LifecycleManager SHALL run an orphan reaper that detects and kills Claude CLI subprocesses not owned by any active SessionUnit.
5. WHILE the LifecycleManager is executing hooks for one session, THE LifecycleManager SHALL NOT block the chat response path for any other session.
6. THE LifecycleManager module SHALL be approximately 400 lines of code and contain no prompt-building, routing, or subprocess spawn logic.

### Requirement 5: P0 Concurrency Fixes

**User Story:** As a system operator, I want critical concurrency bugs fixed before the module extraction, so that the new architecture inherits a safe foundation.

#### Acceptance Criteria

1. THE Evolution_Maintenance_Hook SHALL acquire an `fcntl.flock` file lock before writing to EVOLUTION_CHANGELOG.jsonl.
2. THE DailyActivity_Extraction_Hook SHALL acquire its asyncio lock with a 10-second timeout.
3. IF the DailyActivity lock acquisition exceeds 10 seconds, THEN THE DailyActivity_Extraction_Hook SHALL log a warning and skip the extraction for that cycle.
4. THE Hook_Pipeline SHALL serialize all hook executions through a single asyncio queue, preventing concurrent hook runs across sessions.

### Requirement 6: Zero Behavior Change Module Extraction (Phase 1)

**User Story:** As a developer, I want the 4-module extraction to produce identical observable behavior to the current agent_manager.py, so that the refactor introduces no regressions.

#### Acceptance Criteria

1. THE SessionRouter SHALL expose the same public API surface as the current AgentManager for `run_conversation`, `continue_with_answer`, `continue_with_cmd_permission`, `interrupt_session`, `compact_session`, and `disconnect_all`.
2. WHEN the 4 modules replace AgentManager, THE SSE streaming event sequence (session_start, assistant, tool_use, tool_result, ask_user_question, cmd_permission_request, result, error) SHALL remain identical.
3. WHEN the 4 modules replace AgentManager, THE backend API endpoints in `routers/chat.py` SHALL require zero changes to their request/response contracts.
4. THE combined line count of the 4 new modules SHALL be approximately 1,600 lines (±15%), down from 5,406 lines.
5. THE dependency graph SHALL be acyclic: `chat.py → session_router → session_unit → ClaudeSDKClient`, `session_router → prompt_builder`, `lifecycle_manager → session_router`.


### Requirement 7: Lifecycle Simplification (Phase 2)

**User Story:** As a developer, I want the complex 5-tier lifecycle (SIGSTOP/SIGCONT freeze/thaw, global PID tracking, orphan sweeps) removed, so that session lifecycle is binary (alive or dead) and easier to reason about.

#### Acceptance Criteria

1. THE SessionUnit SHALL NOT use SIGSTOP or SIGCONT signals for subprocess management.
2. THE SessionUnit SHALL manage subprocess state as binary: alive (IDLE, STREAMING, WAITING_INPUT) or dead (COLD, DEAD).
3. WHEN a user clicks Stop, THE SessionUnit SHALL call the SDK `interrupt()` method with a 5-second timeout.
4. IF the SDK `interrupt()` call does not complete within 5 seconds, THEN THE SessionUnit SHALL fall back to killing the subprocess.
5. WHEN a Stop completes via `interrupt()`, THE SessionUnit SHALL keep the subprocess alive and transition to IDLE.
6. THE LifecycleManager SHALL NOT maintain global PID tracking sets (`_tracked_pids`, `_pid_spawn_times`, `_streaming_pids`).
7. THE LifecycleManager SHALL NOT run periodic orphan sweep loops after startup; only the startup orphan reaper is retained.
8. THE SessionUnit SHALL use a 12-hour TTL for idle session cleanup, replacing the previous multi-tier timeout system (5min freeze, 2hr kill, 8hr TTL).

### Requirement 8: Frontend Single Store Migration (Phase 3)

**User Story:** As a frontend developer, I want a single Zustand store replacing the dual-state pattern (tabMapRef + useState), so that tab state is consistent and the synchronization bugs are eliminated.

#### Acceptance Criteria

1. THE Zustand_Store SHALL be the single source of truth for all tab state, replacing both `tabMapRef` and React `useState` for tab data.
2. THE Zustand_Store SHALL store per-tab state including: session ID, agent ID, messages, streaming status, pending state, and context warnings.
3. WHEN a tab's state changes, THE Zustand_Store SHALL notify subscribed React components via Zustand selectors without manual render-counter bumping.
4. THE Zustand_Store SHALL persist open tab metadata to `~/.swarm-ai/open_tabs.json` with debounced writes (500ms).
5. WHEN the application starts, THE Zustand_Store SHALL restore tab state from `~/.swarm-ai/open_tabs.json`.
6. THE Zustand_Store SHALL load messages lazily from the backend API when a tab becomes active, not pre-load for all tabs.
7. WHEN a background tab receives SSE events, THE Zustand_Store SHALL update that tab's state without triggering re-renders in the active tab's components.
8. THE frontend migration SHALL remove `useUnifiedTabState.ts` and the render-counter pattern after the Zustand store is validated.

### Requirement 9: Lazy MCP Loading (Phase 4)

**User Story:** As a user, I want MCP servers loaded only when needed, so that subprocess startup is faster and memory usage is lower for sessions that do not use all MCP tools.

#### Acceptance Criteria

1. WHEN a new subprocess is spawned, THE PromptBuilder SHALL configure only the default MCP server (builder-mcp) in the initial ClaudeAgentOptions.
2. WHEN the Claude CLI requires a tool from a non-loaded MCP server, THE SessionUnit SHALL trigger an MCP hot-swap (reclaim subprocess + respawn with the additional MCP server).
3. THE PromptBuilder SHALL support configuring a subset of available MCP servers per session based on agent configuration and runtime demand.
4. WHEN lazy MCP loading is active, THE SessionUnit SHALL reduce per-session memory footprint by not loading unused MCP servers (outlook-mcp, slack-mcp, taskei-mcp, aws-sentral-mcp) at startup.

### Requirement 10: Crash Isolation and Error Handling

**User Story:** As a user with multiple tabs, I want a crash in one tab to never affect my other tabs, so that I can continue working without interruption.

#### Acceptance Criteria

1. WHEN a SessionUnit's subprocess crashes, THE SessionRouter SHALL deliver an error event only to the affected tab's SSE stream.
2. WHEN a SessionUnit's subprocess crashes, THE SessionRouter SHALL NOT modify, pause, or restart any other SessionUnit.
3. IF a SessionUnit encounters a retriable error (exit -9, broken pipe), THEN THE SessionUnit SHALL retry up to 3 times with backoff, scoped entirely to that SessionUnit.
4. THE SessionRouter SHALL NOT implement a global spawn cooldown; retry backoff is per-SessionUnit only.
5. WHEN a SessionUnit retries after a crash, THE SessionUnit SHALL spawn a fresh subprocess using the `--resume` flag to restore conversation context.

### Requirement 11: Stop Button Behavior

**User Story:** As a user, I want the Stop button to interrupt the current response quickly while keeping the subprocess warm for my next message, so that I do not experience cold-start delays.

#### Acceptance Criteria

1. WHEN the user clicks Stop, THE SessionUnit SHALL call `ClaudeSDKClient.interrupt()` to cancel the active query.
2. WHEN `interrupt()` succeeds, THE SessionUnit SHALL transition from STREAMING to IDLE with the subprocess still alive.
3. IF `interrupt()` does not complete within 5 seconds, THEN THE SessionUnit SHALL kill the subprocess and transition to DEAD then COLD.
4. WHEN the user sends a new message after a successful interrupt, THE SessionUnit SHALL reuse the existing warm subprocess without cold-start delay.
