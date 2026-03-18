# Implementation Plan: Multi-Session Re-Architecture

## Overview

Decompose the 5,406-line `agent_manager.py` monolith into 4 focused modules (SessionUnit, SessionRouter, PromptBuilder, LifecycleManager) following the phased migration plan. P0 concurrency fixes first, then module extraction (zero behavior change), lifecycle simplification, frontend Zustand migration, and lazy MCP loading. Property-based tests are integrated into each phase near the code they validate.

## Tasks

- [x] 1. P0 Concurrency Fixes (Prerequisites)
  - [x] 1.1 Add `fcntl.flock` file lock to EVOLUTION_CHANGELOG writes
    - Modify `backend/core/session_hooks.py` (or the evolution maintenance hook file) to wrap `_append_changelog` with `fcntl.flock` on a `.lock` sidecar file
    - Acquire `LOCK_EX` before write, release in `finally` block
    - _Requirements: 5.1_

  - [x] 1.2 Write property test for EVOLUTION_CHANGELOG concurrent write safety
    - **Property 14: EVOLUTION_CHANGELOG concurrent write safety**
    - Create `backend/tests/test_p0_fixes_properties.py`
    - Generate lists of entries, write concurrently with `asyncio.gather`, verify all entries present and valid JSONL
    - **Validates: Requirements 5.1**

  - [x] 1.3 Add 10-second timeout to DailyActivity lock acquisition
    - Modify the DailyActivity extraction hook to use `asyncio.wait_for(self._lock.acquire(), timeout=10.0)`
    - On `TimeoutError`, log warning and skip extraction for that cycle
    - _Requirements: 5.2, 5.3_

  - [x] 1.4 Serialize hook pipeline through a single asyncio queue
    - Add a hook serialization queue to `BackgroundHookExecutor` (or create one) so all post-session hooks execute one at a time across sessions
    - Ensure hooks do not block the chat response path (fire-and-forget enqueue)
    - _Requirements: 5.4, 4.3_

  - [x] 1.5 Write property test for hook execution serialization
    - **Property 13: Hook execution serialization**
    - Create integration test: submit hooks from multiple "sessions" via `asyncio.gather`, verify no two hook executions overlap using timestamps
    - **Validates: Requirements 4.3, 5.4**

- [x] 2. Checkpoint — P0 fixes validated
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Phase 1: Extract SessionUnit (~300 LOC, zero behavior change)
  - [x] 3.1 Create `backend/core/session_unit.py` with SessionState enum and SessionUnit dataclass
    - Implement `SessionState` enum (COLD, IDLE, STREAMING, WAITING_INPUT, DEAD)
    - Implement `SessionUnit` dataclass with all fields from design (session_id, agent_id, state, created_at, last_used, _client, _wrapper, _lock, _sdk_session_id, _interrupted, _retry_count)
    - Implement properties: `is_alive`, `is_protected`, `pid`
    - Implement `_transition()` with structured logging
    - _Requirements: 1.1, 1.2, 1.10_

  - [x] 3.2 Implement `SessionUnit.send()` — spawn if COLD, reuse if IDLE
    - Extract subprocess spawn logic from `agent_manager.py` `_run_query_on_client` into `SessionUnit._spawn()` and `SessionUnit.send()`
    - COLD→STREAMING spawns new subprocess under `_env_lock`, IDLE→STREAMING reuses existing
    - Yield SSE events (session_start, assistant, tool_use, tool_result, ask_user_question, cmd_permission_request, result, error)
    - Implement retry logic: up to 3 retries with exponential backoff for retriable errors (exit -9, broken pipe), using `--resume` flag
    - _Requirements: 1.3, 1.4, 1.5, 1.9, 10.3, 10.5_

  - [x] 3.3 Implement `SessionUnit.interrupt()`, `continue_with_answer()`, `continue_with_permission()`, `compact()`, `kill()`
    - `interrupt()`: SDK `interrupt()` with 5s timeout, kill fallback. STREAMING→IDLE on success, STREAMING→DEAD→COLD on timeout
    - `continue_with_answer()`: WAITING_INPUT→STREAMING→IDLE/WAITING_INPUT
    - `continue_with_permission()`: WAITING_INPUT→STREAMING→IDLE/WAITING_INPUT
    - `compact()`: IDLE→IDLE, delegates to subprocess `/compact`
    - `kill()`: any→DEAD→COLD, force-kill subprocess and clean up
    - _Requirements: 1.7, 7.3, 7.4, 11.1, 11.2, 11.3_

  - [x] 3.4 Write property test for state machine transitions
    - **Property 1: State machine transitions follow the defined transition table**
    - Create `backend/tests/test_session_unit_properties.py`
    - Generate random event sequences, verify each transition matches the state transition table
    - `@given(st.lists(st.sampled_from(events)))`
    - **Validates: Requirements 1.2, 1.3, 1.4, 1.5, 1.7**

  - [x] 3.5 Write property test for crash isolation
    - **Property 3: Crash isolation between SessionUnits**
    - Generate pairs of units, crash one, verify other's state/PID/client unchanged
    - **Validates: Requirements 1.8, 10.1, 10.2**

  - [x] 3.6 Write property test for interrupt preserves subprocess
    - **Property 15: Interrupt preserves subprocess for reuse**
    - Interrupt STREAMING unit, verify IDLE + same PID, then send reuses same PID
    - **Validates: Requirements 7.5, 11.2, 11.4**

  - [x] 3.7 Write property test for per-unit retry isolation
    - **Property 18: Per-unit retry with cap and isolation**
    - Generate error sequences for multiple units, verify retry count ≤ 3 and no cross-unit interference
    - **Validates: Requirements 10.3, 10.4**

  - [x] 3.8 Write property test for environment spawn lock scoping
    - **Property 19: Environment spawn lock scoping**
    - Verify `_env_lock` acquired before spawn, released after `wrapper.__aenter__()`, not held during streaming
    - **Validates: Requirements 1.9**

  - [x] 3.9 Write property test for WAITING_INPUT crash
    - **Property 20: WAITING_INPUT crash transitions to DEAD**
    - Crash unit in WAITING_INPUT, verify DEAD→COLD transition and error event delivery
    - **Validates: Requirements 1.7, 10.1**

- [x] 4. Phase 1: Extract PromptBuilder (~500 LOC, zero behavior change)
  - [x] 4.1 Create `backend/core/prompt_builder.py` with PromptBuilder class
    - Extract `_build_system_prompt` from `agent_manager.py` into `PromptBuilder.build_system_prompt()`
    - Extract `_build_options` into `PromptBuilder.build_options()`
    - Extract helper methods: `resolve_model()`, `resolve_allowed_tools()`, `build_mcp_config()`, `merge_user_local_mcp_servers()`, `inject_channel_mcp()`, `build_sandbox_config()`
    - Implement `compute_watchdog_timeout()` with formula: `clamp(base + (tokens/100K * per_100K) + (turns * per_turn), base, max)`
    - Implement `build_context_warning()` with warn/critical threshold levels
    - IO-at-boundaries: reads context files via ContextDirectoryLoader, no subprocess ops or network calls
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

  - [x] 4.2 Write property test for PromptBuilder determinism
    - **Property 7: PromptBuilder determinism**
    - Generate agent configs, call `build_system_prompt` twice with identical inputs, verify identical output
    - **Validates: Requirements 3.1**

  - [x] 4.3 Write property test for MCP server merge union
    - **Property 8: MCP server merge is a union**
    - Generate two MCP server dicts, verify merged result contains every server from both sets
    - **Validates: Requirements 3.3**

  - [x] 4.4 Write property test for channel MCP injection
    - **Property 9: Channel MCP injection**
    - Generate MCP config + non-null channel context, verify all original servers preserved plus channel server added
    - **Validates: Requirements 3.4**

  - [x] 4.5 Write property test for watchdog timeout formula
    - **Property 10: Watchdog timeout formula**
    - Generate non-negative token counts and turn counts, verify formula: `clamp(180 + (tokens/100K * 30) + (turns * 5), 180, 600)`
    - **Validates: Requirements 3.5**

  - [x] 4.6 Write property test for context warning thresholds
    - **Property 11: Context warning thresholds**
    - Generate token counts and models, verify warning levels (warn/critical/ok) and percentage calculation
    - **Validates: Requirements 3.6**

- [x] 5. Phase 1: Extract SessionRouter (~300 LOC, zero behavior change)
  - [x] 5.1 Create `backend/core/session_router.py` with SessionRouter class
    - Implement `SessionRouter` with `_units` dict, `_prompt_builder` reference, `_queue`, `_slot_available` event
    - Implement `get_unit()`, `get_or_create_unit()`, `alive_count` property
    - Implement `_acquire_slot()`: count alive units, evict oldest IDLE if at cap, queue with 60s timeout if all protected
    - Implement `_evict_idle()`: select oldest IDLE unit, call `kill()`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.8_

  - [x] 5.2 Implement SessionRouter public API matching AgentManager surface
    - `run_conversation()`: build options via PromptBuilder, acquire slot, dispatch to SessionUnit.send(), yield SSE events
    - `interrupt_session()`: delegate to SessionUnit.interrupt()
    - `continue_with_answer()`: delegate to SessionUnit.continue_with_answer()
    - `continue_with_cmd_permission()`: delegate to SessionUnit.continue_with_permission()
    - `compact_session()`: delegate to SessionUnit.compact()
    - `disconnect_all()`: kill all alive SessionUnits
    - `has_active_session()`: check if session has alive subprocess
    - _Requirements: 6.1, 6.2, 6.3, 2.7_

  - [x] 5.3 Write property test for eviction targets only IDLE
    - **Property 2: Eviction targets only IDLE units**
    - Generate unit sets with mixed states, verify eviction only selects IDLE units
    - **Validates: Requirements 1.6, 2.6**

  - [x] 5.4 Write property test for concurrency cap invariant
    - **Property 4: Concurrency cap invariant**
    - Generate request sequences, verify `alive_count ≤ MAX_CONCURRENT` after each `_acquire_slot`
    - **Validates: Requirements 2.1**

  - [x] 5.5 Write property test for FIFO queue dispatch
    - **Property 5: FIFO queue dispatch ordering**
    - Generate queued requests, verify dispatch order matches enqueue order
    - **Validates: Requirements 2.5**

  - [x] 5.6 Write property test for routing by session ID
    - **Property 6: Correct routing by session ID**
    - Generate unit sets, route by random ID, verify correct unit reached
    - **Validates: Requirements 2.7**

- [x] 6. Phase 1: Extract LifecycleManager (~400 LOC, zero behavior change)
  - [x] 6.1 Create `backend/core/lifecycle_manager.py` with LifecycleManager class
    - Implement `LifecycleManager` with `_router` reference, `_hook_executor`, `_hook_queue` (maxsize=100)
    - Implement `start()`: run `_reap_orphans()` then start `_maintenance_loop()` as asyncio task
    - Implement `stop()`: cancel loop task, drain pending hooks
    - Implement `enqueue_hooks()`: add (session_id, HookContext) to queue
    - Implement `_maintenance_loop()`: every 60s, run `_check_ttl()` + `_drain_hooks()`
    - Implement `_check_ttl()`: iterate units via router, kill units idle > 43200s (12hr TTL)
    - Implement `_drain_hooks()`: execute queued hooks one at a time (serialized)
    - Implement `_reap_orphans()`: one-shot startup, find/kill unowned claude CLI processes by binary path
    - _Requirements: 4.1, 4.2, 4.4, 4.5, 4.6_

  - [x] 6.2 Write property test for TTL-based cleanup
    - **Property 12: TTL-based cleanup**
    - Generate units with random timestamps, verify units idle > TTL marked for cleanup, units within TTL not marked
    - **Validates: Requirements 4.2**

- [x] 7. Phase 1: Wire modules together and replace AgentManager
  - [x] 7.1 Update `backend/core/__init__.py` to export new modules
    - Export SessionUnit, SessionRouter, PromptBuilder, LifecycleManager
    - Add backward-compatible re-exports from agent_manager.py if needed
    - _Requirements: 6.1_

  - [x] 7.2 Update `backend/routers/chat.py` to use SessionRouter instead of AgentManager
    - Replace AgentManager references with SessionRouter
    - Verify zero changes to request/response contracts
    - Wire PromptBuilder and LifecycleManager initialization
    - _Requirements: 6.1, 6.2, 6.3_

  - [x] 7.3 Verify dependency graph is acyclic
    - Confirm: `chat.py → session_router → session_unit → ClaudeSDKClient`, `session_router → prompt_builder`, `lifecycle_manager → session_router`
    - No circular imports between the 4 modules
    - _Requirements: 6.5_

- [x] 8. Checkpoint — Phase 1 complete, zero behavior change verified
  - Ensure all tests pass, ask the user if questions arise.
  - Verify SSE event sequence unchanged (6.2)
  - Verify combined LOC of 4 modules is ~1,600 lines ±15% (6.4)

- [x] 9. Phase 2: Simplify Lifecycle
  - [x] 9.1 Remove SIGSTOP/SIGCONT signal handling from SessionUnit
    - Delete all `os.kill(pid, signal.SIGSTOP)` and `os.kill(pid, signal.SIGCONT)` calls
    - Remove freeze/thaw state tracking
    - SessionUnit manages subprocess as binary: alive (IDLE/STREAMING/WAITING_INPUT) or dead (COLD/DEAD)
    - _Requirements: 7.1, 7.2_

  - [x] 9.2 Remove global PID tracking from LifecycleManager
    - Delete `_tracked_pids`, `_pid_spawn_times`, `_streaming_pids` sets/dicts
    - Remove periodic orphan sweep loops (keep only startup orphan reaper)
    - _Requirements: 7.6, 7.7_

  - [x] 9.3 Implement SDK `interrupt()` for Stop button with 5s kill fallback
    - `SessionUnit.interrupt()` calls `ClaudeSDKClient.interrupt()` via `asyncio.wait_for(timeout=5.0)`
    - On success: STREAMING→IDLE, subprocess stays warm
    - On timeout: force-kill subprocess, STREAMING→DEAD→COLD
    - _Requirements: 7.3, 7.4, 7.5, 11.1, 11.2, 11.3, 11.4_

  - [x] 9.4 Simplify to 12-hour TTL, remove multi-tier timeout system
    - Remove 5min freeze timeout, 2hr kill timeout, 8hr TTL tiers
    - Single TTL = 43200s (12 hours) for idle session cleanup
    - _Requirements: 7.8_

- [x] 10. Checkpoint — Phase 2 complete, lifecycle simplified
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Phase 3: Frontend Zustand Single Store
  - [x] 11.1 Create `desktop/src/stores/tabStore.ts` with Zustand store
    - Install `zustand` dependency if not present
    - Implement `TabStore` interface with `tabs: Record<string, TabState>`, `activeTabId`
    - Implement tab CRUD: `createTab()`, `closeTab()`, `setActiveTab()`
    - Implement per-tab state updates: `setStreaming()`, `appendMessage()`, `updateMessage()`, `setContextWarning()`
    - _Requirements: 8.1, 8.2, 8.3_

  - [x] 11.2 Implement tab persistence and lazy message loading
    - `persistTabs()`: debounced 500ms write to `~/.swarm-ai/open_tabs.json`
    - `restoreTabs()`: load tab metadata from `open_tabs.json` on startup
    - `loadMessages()`: lazy-load messages from backend API when tab becomes active
    - Background tab SSE events update store without triggering active tab re-renders (Zustand selectors)
    - _Requirements: 8.4, 8.5, 8.6, 8.7_

  - [x] 11.3 Write property test for tab state serialization round-trip
    - **Property 16: Tab state serialization round-trip**
    - Create `desktop/src/stores/__tests__/tabStore.property.test.ts`
    - Generate tab states with `fc.record({sessionId: fc.uuid(), ...})`, persist + restore, verify equivalence
    - **Validates: Requirements 8.4, 8.5**

  - [x] 11.4 Migrate ChatPage.tsx and hooks to use Zustand store
    - Replace `tabMapRef` reads with Zustand selectors
    - Replace `useState` tab state with Zustand store reads
    - Replace manual `bumpRender()` calls with Zustand's automatic reactivity
    - Update `useChatStreamingLifecycle.ts` to write to Zustand store instead of tabMapRef
    - _Requirements: 8.1, 8.3_

  - [x] 11.5 Remove `useUnifiedTabState.ts` and render-counter pattern
    - Delete `desktop/src/hooks/useUnifiedTabState.ts`
    - Remove re-export from `desktop/src/hooks/index.ts`
    - Remove all `renderCounter` / `bumpRender` references
    - _Requirements: 8.8_

- [x] 12. Checkpoint — Phase 3 complete, frontend migrated
  - Ensure all tests pass, ask the user if questions arise.

- [x] 13. Phase 4: Lazy MCP Loading
  - [x] 13.1 Implement lazy MCP in PromptBuilder
    - Modify `build_mcp_config(lazy=True)` to return only `builder-mcp` in initial config
    - Support configuring a subset of MCP servers per session based on runtime demand
    - _Requirements: 9.1, 9.3_

  - [x] 13.2 Write property test for MCP subset configuration
    - **Property 17: MCP subset configuration**
    - Generate subsets of available MCP servers, verify `build_mcp_config` returns exactly that subset; when `lazy=True`, verify only `{builder-mcp}`
    - **Validates: Requirements 9.1, 9.3**

  - [x] 13.3 Implement MCP hot-swap in SessionUnit
    - When Claude CLI requires a tool from a non-loaded MCP server, trigger subprocess reclaim + respawn with additional MCP server
    - Reduce per-session memory by not loading unused MCP servers (outlook-mcp, slack-mcp, taskei-mcp, aws-sentral-mcp) at startup
    - _Requirements: 9.2, 9.4_

- [x] 14. Final Checkpoint — All phases complete
  - Ensure all tests pass, ask the user if questions arise.
  - Verify combined LOC of 4 modules is ~1,600 lines ±15% (Req 6.4)
  - Verify SSE event sequence unchanged from original AgentManager (Req 6.2)
  - Verify `routers/chat.py` required zero request/response contract changes (Req 6.3)

## Notes

- All tasks including property-based tests are required — this re-architecture is critical for the whole application
- Each task references specific requirements for traceability
- Property-based tests are placed near the implementation they validate to catch errors early
- Checkpoints at phase boundaries ensure incremental validation
- Phase 1 is the critical path — zero behavior change must be verified before Phase 2+
- Backend: Python with pytest + hypothesis for property tests
- Frontend: TypeScript with vitest + fast-check for property tests
