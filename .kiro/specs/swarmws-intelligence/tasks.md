# Implementation Plan: SwarmWS Intelligence (Cadence 4 of 4)

## Overview

This plan implements the intelligence layer: the 8-layer context assembly engine with tag-based L0 filtering, 3-stage progressive truncation, Layer 2 bounding, context snapshot caching, mid-session thread binding, chat thread project association, and context assembly preview API with ETag caching. Implementation follows bottom-up order: schemas → DB changes → core engine → caching → binding → API endpoints → frontend types/services → frontend components → agent manager integration → verification.

Depends on Cadences 1–3 (`swarmws-foundation`, `swarmws-projects`, `swarmws-explorer-ux`) being completed first.

## Tasks

- [ ] 1. Add context assembly schemas and chat thread schema updates
  - [ ] 1.1 Create `backend/schemas/context.py` with Pydantic models
    - Include module-level docstring per project code documentation standards
    - Define `ContextLayerResponse` with fields: `layer_number`, `name`, `source_path` (workspace-relative), `token_count`, `content_preview`, `truncated`, `truncation_stage`
    - Define `ContextPreviewResponse` with fields: `project_id`, `thread_id`, `layers`, `total_token_count`, `budget_exceeded`, `token_budget`, `truncation_summary`, `etag`
    - Define `ThreadBindRequest` with fields: `task_id`, `todo_id`, `mode` (replace|add), optional `force` (bool)
    - Define `ThreadBindResponse` with fields: `thread_id`, `task_id`, `todo_id`, `context_version`
    - All field names use snake_case per backend convention
    - _Requirements: 33.2, 33.3, 33.7, 35.1_

  - [ ] 1.2 Update `backend/schemas/chat_thread.py` to add `project_id` and `context_version` fields
    - Add optional `project_id: Optional[str]` field to `ChatThreadCreate` and `ChatThreadResponse` schemas
    - Add `context_version: int = 0` field to `ChatThreadResponse`
    - Default `project_id` to `None` (NULL = global SwarmWS chat)
    - Include docstring updates explaining the project association and version semantics
    - _Requirements: 26.5, 26.6_

- [ ] 2. Update database layer for chat thread project association and version tracking
  - [ ] 2.1 Update `backend/database/sqlite.py` to add `project_id` and `context_version` columns to `chat_threads` table
    - Add `project_id TEXT DEFAULT NULL` column to the `chat_threads` table definition (CREATE TABLE for clean installs)
    - Add `context_version INTEGER DEFAULT 0` column to the `chat_threads` table definition
    - Apply safe schema evolution: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for existing DBs
    - Add index `idx_chat_threads_project_id` on the `project_id` column
    - _Requirements: 26.5, 26.6, 37.1, 37.2, 37.3_

  - [ ] 2.2 Add `list_by_project()`, `list_global()`, `bind_thread()`, and `increment_context_version()` methods to `SQLiteChatThreadsTable`
    - `list_by_project(project_id)` — returns threads WHERE `project_id = ?`
    - `list_global()` — returns threads WHERE `project_id IS NULL`
    - `bind_thread(thread_id, task_id, todo_id, mode)` — updates task_id/todo_id per mode, increments context_version
    - `increment_context_version(thread_id)` — increments and returns new context_version
    - Update existing insert/create methods to accept and store `project_id`
    - _Requirements: 26.1, 26.4, 26.5, 26.6, 35.1, 35.2, 35.3, 35.4_

  - [ ] 2.3 Write unit tests for chat thread project_id schema, queries, and binding in `backend/tests/test_chat_thread_project.py`
    - Include module-level docstring describing what is tested
    - Test creating a thread with a `project_id`, creating a global thread (NULL), `list_by_project`, `list_global`
    - Test that `project_id` defaults to NULL when not provided
    - Test `bind_thread` with replace mode and add mode
    - Test `increment_context_version` returns incremented value
    - Test safe schema evolution (ALTER TABLE on existing DB)
    - _Requirements: 26.1, 26.4, 26.5, 26.6, 35.1, 37.1_

- [ ] 3. Implement the 8-layer context assembly engine
  - [ ] 3.1 Create `backend/core/context_assembler.py` with `ContextAssembler`, `ContextLayer`, `AssembledContext`, and `TruncationInfo`
    - Include module-level docstring per project code documentation standards
    - Define layer priority constants `LAYER_SYSTEM_PROMPT` through `LAYER_SCOPED_RETRIEVAL` (1–8)
    - Define `DEFAULT_TOKEN_BUDGET = 10_000`, `LAYER_2_TOKEN_LIMIT = 1_200`, `LAYER_2_MAX_MESSAGES = 10`
    - Implement `ContextLayer`, `AssembledContext`, and `TruncationInfo` dataclasses
    - Implement `ContextAssembler.__init__(workspace_path, token_budget)`
    - Implement `estimate_tokens()` static method using word-based heuristic (1 token ≈ 0.75 words)
    - Implement `_to_workspace_relative()` — converts absolute paths to workspace-relative (PE Fix #8)
    - Implement `_resolve_project_path()` — resolves project path from project_id, not name (PE Fix #7)
    - _Requirements: 16.1, 16.3, 16.8_

  - [ ] 3.2 Implement L0 tag-based filtering methods in `ContextAssembler`
    - `_extract_l0_tags(l0_content)` — parses YAML frontmatter to extract `tags` and `active_domains`
    - `_extract_live_context_keywords(layer_2_content)` — extracts keywords from thread title, task titles, todo descriptions, recent messages
    - `_is_l0_relevant(l0_content, live_context_keywords)` — performs token intersection between L0 tags and live context keywords; falls back to legacy non-empty check if no YAML frontmatter
    - _Requirements: 16.2_

  - [ ] 3.3 Implement layer loading methods in `ContextAssembler`
    - `_load_layer_1_system_prompt()` — reads `system-prompts.md`
    - `_load_layer_2_live_work(project_id, thread_id)` — loads chat thread, ToDos, tasks from DB; calls `_summarize_layer_2()` to bound output
    - `_summarize_layer_2(thread_data, tasks, todos)` — produces bounded summary within LAYER_2_TOKEN_LIMIT; includes thread title, last user/assistant messages, task/todo summary, summarized older messages
    - `_load_layer_3_instructions(project_path)` — reads `Projects/{project_id}/instructions.md`
    - `_load_layer_4_project_semantic(project_path, live_context_keywords)` — L0 tag-based filter then L1 load
    - `_load_layer_5_knowledge_semantic(live_context_keywords)` — L0 tag-based filter then L1 load
    - `_load_layer_6_memory()` — loads all `.md` files from `Knowledge/Memory/` in sorted order (determinism)
    - `_load_layer_7_workspace_semantic(live_context_keywords)` — L0 tag-based filter then L1 load
    - `_load_layer_8_scoped_retrieval(project_id)` — placeholder returning None for future RAG
    - Each method returns `Optional[ContextLayer]` with workspace-relative source_path, gracefully skips on errors
    - _Requirements: 16.1, 16.2, 16.5, 16.6, 16.8_

  - [ ] 3.4 Implement 3-stage progressive truncation and assembly orchestration
    - `_truncate_within_layer(layer, target_tokens)` — Stage 1: keep headers + top N tokens
    - `_remove_snippets_from_layer(layer, target_tokens)` — Stage 2: remove least important sections
    - `_enforce_token_budget(layers)` — 3-stage progressive truncation from layer 8 upward; layers 1–2 never fully dropped; returns AssembledContext with truncation_log
    - `_build_truncation_summary(truncation_log)` — builds human-readable summary for agent injection (PE Enhancement A)
    - `assemble(project_id, thread_id)` — calls all layer loaders in order, enforces budget, injects truncation summary, returns AssembledContext
    - Assembly is deterministic: same inputs → identical output
    - Add structured logging for layer sizes, truncation decisions (PE Enhancement B)
    - _Requirements: 16.1, 16.3, 16.4, 16.6, 16.7, 38.1, 38.2_

  - [ ] 3.5 Write property test: Context assembly priority ordering (`backend/tests/test_context_assembler.py`)
    - **Property 1: Context assembly priority ordering**
    - Generate random sets of context files across layers 1–8 with random content sizes
    - Verify assembled layers appear in strictly ascending `layer_number` order
    - Tag: `Feature: swarmws-intelligence, Property 1: Context assembly priority ordering`
    - **Validates: Requirements 16.1**

  - [ ] 3.6 Write property test: Tag-based L0 fast-filter gating (`backend/tests/test_context_assembler.py`)
    - **Property 2: Tag-based L0 fast-filter gating**
    - Generate random L0 YAML frontmatter with random tags/active_domains and random live context keywords
    - Verify L1 is loaded iff tag intersection is non-empty; verify legacy fallback when no frontmatter
    - Tag: `Feature: swarmws-intelligence, Property 2: Tag-based L0 fast-filter gating`
    - **Validates: Requirements 16.2**

  - [ ] 3.7 Write property test: Token budget invariant (`backend/tests/test_context_assembler.py`)
    - **Property 3: Token budget invariant**
    - Generate random context across all layers with random token budgets (100–50,000)
    - Verify `total_token_count` never exceeds the configured budget
    - Tag: `Feature: swarmws-intelligence, Property 3: Token budget invariant`
    - **Validates: Requirements 16.3**

  - [ ] 3.8 Write property test: Progressive truncation respects priority with summary (`backend/tests/test_context_assembler.py`)
    - **Property 4: Progressive truncation respects priority and produces summary**
    - Generate random oversized contexts exceeding budget
    - Verify truncation proceeds from layer 8 upward through stages 1→2→3
    - Verify stage 3 never applied to layers 1–2
    - Verify `truncation_summary` is non-empty when any truncation occurs
    - Tag: `Feature: swarmws-intelligence, Property 4: Progressive truncation respects priority and produces summary`
    - **Validates: Requirements 16.4, 16.7**

  - [ ] 3.9 Write property test: Layer 2 bounding invariant (`backend/tests/test_context_assembler.py`)
    - **Property 5: Layer 2 bounding invariant**
    - Generate random thread data with varying message counts (0–1000) and varying task/todo sizes
    - Verify Layer 2 token count never exceeds LAYER_2_TOKEN_LIMIT
    - Verify bounded content always includes thread title and last user message
    - Tag: `Feature: swarmws-intelligence, Property 5: Layer 2 bounding invariant`
    - **Validates: Requirements 16.5**

  - [ ] 3.10 Write property test: Deterministic assembly (`backend/tests/test_context_assembler.py`)
    - **Property 10: Deterministic assembly**
    - Generate random but fixed inputs (project files, thread data, budget)
    - Call assemble() twice with identical inputs
    - Verify byte-identical output (same layers, same order, same content, same token counts)
    - Tag: `Feature: swarmws-intelligence, Property 10: Deterministic assembly`
    - **Validates: Requirements 16.6**

  - [ ] 3.11 Write property test: Stable project pathing (`backend/tests/test_context_assembler.py`)
    - **Property 13: Stable project pathing**
    - Generate random project names, create projects with UUIDs, rename display names
    - Verify assembly still resolves correct files via project_id path
    - Tag: `Feature: swarmws-intelligence, Property 13: Stable project pathing`
    - **Validates: Requirements 16.8**

- [ ] 4. Implement context snapshot cache
  - [ ] 4.1 Create `backend/core/context_snapshot_cache.py` with `ContextSnapshotCache`, `VersionCounters`, `CacheEntry`
    - Include module-level docstring per project code documentation standards
    - Implement `VersionCounters` dataclass with `thread_version`, `task_version`, `todo_version`, `project_files_version`, `memory_version` and `compute_hash()` method
    - Implement `CacheEntry` dataclass
    - Implement `ContextSnapshotCache` with `get_or_assemble()`, `_read_version_counters()`, `invalidate()`, `clear()`, `_make_key()`
    - LRU eviction with configurable max entries (default 50)
    - Add structured logging for cache hits/misses (PE Enhancement B)
    - _Requirements: 34.1, 34.2, 34.3, 34.4, 34.5, 38.1_

  - [ ] 4.2 Add version counter increment hooks to existing managers
    - Increment `thread_version` when messages are added (in chat message handler)
    - Increment `task_version` when tasks are created/updated/deleted (in task manager)
    - Increment `todo_version` when todos are created/updated/deleted (in todo manager)
    - Increment `project_files_version` when project files change (in workspace API)
    - Increment `memory_version` when Memory/ files are written (in workspace manager)
    - _Requirements: 34.2_

  - [ ] 4.3 Write property test: Cache correctness (`backend/tests/test_context_snapshot_cache.py`)
    - **Property 11: Cache correctness**
    - Generate random version counters
    - Verify cache returns cached result when all counters unchanged
    - Verify cache triggers fresh assembly when any counter changes
    - Tag: `Feature: swarmws-intelligence, Property 11: Cache correctness`
    - **Validates: Requirements 34.3, 34.4**

  - [ ] 4.4 Write unit tests for cache in `backend/tests/test_context_snapshot_cache.py`
    - Test cache hit, cache miss, cache invalidation on version change
    - Test LRU eviction when max entries exceeded
    - Test `compute_hash()` determinism
    - _Requirements: 34.1, 34.5_

- [ ] 5. Checkpoint — Ensure all backend core tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 6. Implement context preview API endpoint with ETag caching
  - [ ] 6.1 Create `backend/routers/context.py` with `GET /api/projects/{project_id}/context` endpoint
    - Include module-level docstring per project code documentation standards
    - Accept path param `project_id`, query params `thread_id` (optional), `token_budget` (default 10000), `preview_limit` (default 500), `since_version` (optional)
    - Accept `If-None-Match` header for ETag-based caching
    - Instantiate `ContextAssembler` via `ContextSnapshotCache`, call `get_or_assemble()`, map result to `ContextPreviewResponse`
    - Truncate each layer's content to `preview_limit` chars for the `content_preview` field
    - Return workspace-relative paths only in `source_path` (never absolute paths)
    - Include `ETag` header in response derived from context version hash
    - Return 304 Not Modified if `If-None-Match` matches current ETag
    - Return 404 if project not found, 200 with empty layers if workspace path missing
    - _Requirements: 33.1, 33.2, 33.4, 33.7, 36.1, 36.2, 36.3_

  - [ ] 6.2 Register the context router in `backend/main.py`
    - Import and include the new context router
    - _Requirements: 33.1_

  - [ ] 6.3 Write unit tests for context preview API in `backend/tests/test_context_preview_api.py`
    - Include module-level docstring describing what is tested
    - Test valid project returns 200 with layers, invalid project returns 404
    - Test with and without `thread_id` query param
    - Test `preview_limit` truncation of `content_preview`
    - Test `budget_exceeded` flag when context exceeds budget
    - Test ETag header present in response
    - Test 304 Not Modified when If-None-Match matches
    - Test workspace-relative paths (no absolute paths in source_path)
    - Test `truncation_summary` present when layers truncated
    - _Requirements: 33.1, 33.2, 33.3, 33.4, 36.1, 36.2_

  - [ ]* 6.4 Write property test: Context preview layer completeness and path safety (`backend/tests/test_context_preview_api.py`)
    - **Property 8: Context preview layer completeness and path safety**
    - Generate random assembled contexts, map to preview responses
    - Verify every layer has non-empty `name`, workspace-relative `source_path` (no absolute prefix), non-negative `token_count`, and `content_preview` length ≤ preview limit
    - Tag: `Feature: swarmws-intelligence, Property 8: Context preview layer completeness and path safety`
    - **Validates: Requirements 33.2**

  - [ ] 6.5 Write property test: Token count consistency (`backend/tests/test_context_preview_api.py`)
    - **Property 9: Token count consistency**
    - Generate random assembled contexts, map to preview responses
    - Verify `total_token_count` equals sum of all layer `token_count` values
    - Verify `budget_exceeded` is True iff at least one layer has `truncated = true`
    - Tag: `Feature: swarmws-intelligence, Property 9: Token count consistency`
    - **Validates: Requirements 33.3**

- [ ] 7. Implement chat thread project association and mid-session binding
  - [ ] 7.1 Update `backend/core/chat_thread_manager.py` to support `project_id` and binding
    - Update thread creation to accept and pass `project_id` to the DB layer
    - Add `list_threads_by_project(project_id)` method
    - Add `list_global_threads()` method
    - Add `bind_thread(thread_id, task_id, todo_id, mode, force)` method with cross-project guardrail
    - Ensure threads created from a project context store the project's UUID
    - Ensure threads created outside a project context store `project_id = NULL`
    - Add structured logging for binding changes (PE Enhancement B)
    - _Requirements: 26.1, 26.4, 26.5, 35.1, 35.2, 35.3, 35.4, 35.5, 35.6, 38.3_

  - [ ] 7.2 Update `backend/routers/chat.py` to accept `project_id` in thread creation and add binding/project-filtered endpoints
    - Update create-thread endpoint to accept optional `project_id` in request body
    - Add `GET /api/projects/{project_id}/threads` endpoint to list threads by project
    - Add `GET /api/threads/global` endpoint to list global (unassociated) threads
    - Add `POST /api/chat_threads/{thread_id}/bind` endpoint for mid-session binding
    - Implement cross-project binding guardrail: return 409 if task.project_id != thread.project_id unless `force=true` (PE Enhancement C)
    - _Requirements: 26.1, 26.4, 26.5, 35.1, 35.6_

  - [ ] 7.3 Write property test: Chat thread project_id semantics (`backend/tests/test_chat_thread_project.py`)
    - **Property 6: Chat thread project_id semantics**
    - Generate random thread creation requests with and without `project_id`
    - Verify threads with project context have non-null `project_id` matching a valid UUID
    - Verify threads without project context have `project_id = NULL`
    - Tag: `Feature: swarmws-intelligence, Property 6: Chat thread project_id semantics`
    - **Validates: Requirements 26.4, 26.5**

  - [ ] 7.4 Write property test: Thread binding increments version (`backend/tests/test_chat_thread_project.py`)
    - **Property 12: Thread binding increments version**
    - Generate random binding requests (replace and add modes)
    - Verify `context_version` is strictly greater after binding than before
    - Verify task_id/todo_id reflect the request mode (replace overwrites, add fills NULLs only)
    - Tag: `Feature: swarmws-intelligence, Property 12: Thread binding increments version`
    - **Validates: Requirements 26.6, 35.4**

- [ ] 8. Checkpoint — Ensure all backend tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Add frontend types and context service
  - [ ] 9.1 Add TypeScript interfaces to `desktop/src/types/index.ts`
    - `ContextLayer`: `layerNumber`, `name`, `sourcePath`, `tokenCount`, `contentPreview`, `truncated`, `truncationStage`
    - `ContextPreview`: `projectId`, `threadId`, `layers`, `totalTokenCount`, `budgetExceeded`, `tokenBudget`, `truncationSummary`, `etag`
    - `ThreadBindRequest`: `taskId?`, `todoId?`, `mode`
    - `ThreadBindResponse`: `threadId`, `taskId`, `todoId`, `contextVersion`
    - All fields use camelCase per frontend convention
    - _Requirements: 33.2, 33.5, 35.1_

  - [ ] 9.2 Create `desktop/src/services/context.ts` with `getContextPreview()`, `bindThread()`, and `toCamelCase` conversion
    - Include file-level JSDoc comment per project code documentation standards
    - Implement `toCamelCase()` for `ContextPreviewResponse` → `ContextPreview` (snake_case → camelCase)
    - Implement `layerToCamelCase()` for individual layer conversion
    - Implement `getContextPreview(projectId, threadId?, tokenBudget?)` — calls `GET /api/projects/{id}/context` with ETag support (If-None-Match header, handle 304)
    - Implement `bindThread(threadId, request)` — calls `POST /api/chat_threads/{threadId}/bind`
    - _Requirements: 33.1, 33.5, 35.1, 36.1, 36.2_

  - [ ] 9.3 Write unit tests for context service in `desktop/src/services/__tests__/context.test.ts`
    - Test `toCamelCase` correctly converts snake_case API response to camelCase TypeScript types (including new fields: truncationSummary, etag, truncationStage)
    - Test `getContextPreview` constructs correct URL with query params
    - Test ETag handling: sends If-None-Match, handles 304 response
    - Test `bindThread` sends correct request body with snake_case conversion
    - _Requirements: 33.5, 35.1_

- [ ] 10. Implement context preview panel frontend component
  - [ ] 10.1 Create `desktop/src/components/workspace/ContextPreviewPanel.tsx`
    - Include file-level JSDoc comment per project code documentation standards
    - Accept props: `projectId: string`, `threadId?: string`
    - Render collapsible panel header with total token count badge
    - Render truncation summary banner when `truncationSummary` is non-empty
    - Render list of context layers with: layer number, name, workspace-relative source path, token count badge, truncation indicator with stage info, expandable content preview
    - Use CSS variables (`--color-*`) for all theming — never hardcode colors
    - Implement 5-second polling interval with ETag — skip re-render on 304 (PE Fix #6)
    - _Requirements: 33.5, 33.6, 36.1_

  - [ ] 10.2 Write unit tests for `ContextPreviewPanel` in `desktop/src/components/__tests__/ContextPreviewPanel.test.tsx`
    - Test panel renders with mock context data
    - Test collapsible behavior (expand/collapse)
    - Test layer list rendering with correct token counts
    - Test truncation indicator display with stage info
    - Test truncation summary banner display
    - _Requirements: 33.5, 33.6_

- [ ] 11. Integrate context assembler into AgentManager
  - [ ] 11.1 Update `backend/core/agent_manager.py` `_build_system_prompt()` to use `ContextAssembler` via `ContextSnapshotCache`
    - Import `ContextAssembler`, `DEFAULT_TOKEN_BUDGET` from `context_assembler`
    - Import `context_cache` from `context_snapshot_cache`
    - Add `_resolve_project_id(agent_config, channel_context)` helper method
    - In `_build_system_prompt()`, use `context_cache.get_or_assemble()` for cached assembly
    - Inject assembled layers into the system prompt, including truncation summary if present
    - Wrap entire assembly in try/except — log warning on failure, never block agent execution
    - _Requirements: 16.1, 16.3, 16.4, 16.7, 34.3_

  - [ ]* 11.2 Write unit tests for AgentManager context assembly integration in `backend/tests/test_context_assembler.py`
    - Test that `_build_system_prompt` calls `ContextAssembler.assemble()` via cache when project_id is available
    - Test that agent execution proceeds even if context assembly fails (graceful degradation)
    - Test that assembled context layers are injected into the system prompt string
    - Test that truncation summary is included in system prompt when truncation occurred
    - _Requirements: 16.1, 16.7_

- [ ] 12. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.
  - Verify context assembly produces correct 8-layer output with tag-based L0 filtering
  - Verify 3-stage progressive truncation works correctly
  - Verify Layer 2 bounding keeps token count within limit
  - Verify context snapshot cache returns cached results when versions unchanged
  - Verify chat threads can be created with and without `project_id`
  - Verify mid-session thread binding increments context_version
  - Verify context preview API returns ETag and handles 304
  - Verify all paths in preview responses are workspace-relative
  - Verify truncation summary is injected when layers are truncated

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability (Req 16, 26, 33, 34, 35, 36, 37, 38)
- Checkpoints ensure incremental validation at backend-core and full-stack boundaries
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- Schema evolution uses safe ALTER TABLE for non-clean environments (Req 37)
- The old `ContextManager` (`backend/core/context_manager.py`) is NOT modified; the new `ContextAssembler` is a parallel implementation that will eventually replace it
- PE review fixes incorporated: #1 (tag-based L0), #2 (progressive truncation), #3 (Layer 2 bounding), #4 (snapshot caching), #5 (mid-session binding), #6 (ETag preview), #7 (stable pathing), #8 (path safety), #9 (determinism), #10 (schema evolution)
- PE enhancements incorporated: A (truncation summary), B (observability), C (cross-project guardrail)
