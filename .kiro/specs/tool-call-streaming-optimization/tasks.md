# Implementation Plan: Tool Call Streaming Optimization

## Overview

Replace full tool call JSON inputs with short human-readable summaries and truncate large tool result content in the SSE/DB pipeline. The Claude Agent SDK manages full conversation context internally — these changes only affect the UI replay path (SSE stream → React state → SQLite). Implementation proceeds backend-first (summarizer module → agent_manager integration), then frontend (types → components → hooks), with property tests alongside each layer.

## Tasks

- [x] 1. Create `backend/core/tool_summarizer.py` module
  - [x] 1.1 Implement `_sanitize_command()`, constants, and category name sets
    - Define `MAX_SUMMARY_LENGTH` (200), `DEFAULT_TRUNCATION_LIMIT` (500), `SENSITIVE_PATTERNS` regex list
    - Read `TOOL_RESULT_TRUNCATION_LIMIT` env var ONCE at module load time with fallback to `DEFAULT_TRUNCATION_LIMIT`; store as `TRUNCATION_LIMIT`
    - Define lowercase category sets: `_BASH_NAMES`, `_READ_NAMES`, `_WRITE_NAMES`, `_SEARCH_NAMES`, `_TODOWRITE_NAMES` (all lowercase — matching uses `name.lower()`)
    - Implement `_sanitize_command(command)` that redacts sensitive tokens (password, api_key, secret_key, token patterns)
    - Add `import logging` and `logger = logging.getLogger(__name__)` for debug-level tracing
    - Include module-level docstring per SwarmAI documentation standards
    - _Requirements: 1.8, 1.10_

  - [x] 1.2 Implement `summarize_tool_use(name, input_data)`
    - Use `name.lower()` for case-insensitive category matching against lowercase sets
    - Route tool name to correct category (bash → "Running: {cmd}", read/readfile/view → "Reading {path}", write/writefile/create/edit → "Writing to {path}", grep/search/find/glob → "Searching for {pattern}", todowrite → "Writing {N} todos", fallback → "Using {name}")
    - Extract the appropriate input field (`command`, `path`/`file_path`, `pattern`/`query`, `todos`) per category
    - Call `_sanitize_command()` for Bash tools before building summary
    - Handle None/empty input_data gracefully (fall back to "Using {name}")
    - Truncate final summary to `MAX_SUMMARY_LENGTH` characters
    - Add `logger.debug()` logging for the chosen category to aid troubleshooting
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10_

  - [x] 1.3 Implement `truncate_tool_result(content, limit)`
    - If content is None or empty, return `("", False)`
    - If `len(content) <= limit`, return `(content, False)`
    - If `len(content) > limit`, return `(content[:limit], True)`
    - Default `limit` parameter uses module-level `TRUNCATION_LIMIT` (read from env var at load time)
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ]* 1.4 Write property tests for `summarize_tool_use` — summary length invariant
    - **Property 1: Summary length invariant**
    - For any tool name and any input dict, `summarize_tool_use()` returns a non-empty string of length ≤ 200
    - Use Hypothesis with `st.text()` for names, `st.fixed_dictionaries()` for inputs, min 100 iterations
    - **Validates: Requirements 1.1, 1.2**

  - [ ]* 1.5 Write property tests for `summarize_tool_use` — category-correct prefix
    - **Property 2: Category-correct summary prefix**
    - For any known-category tool name with expected input field, summary starts with correct prefix
    - For any unknown tool name, summary starts with "Using "
    - Use Hypothesis with `st.sampled_from()` for known names, `st.text()` for fallback, min 100 iterations
    - **Validates: Requirements 1.3, 1.4, 1.5, 1.6, 1.7**

  - [ ]* 1.6 Write property test for `_sanitize_command` — sensitive token redaction
    - **Property 3: Sensitive token redaction**
    - For any bash command containing a sensitive token (password=X, api_key=X, secret_key=X, token=X), the sanitized output shall not contain the original sensitive value
    - Use Hypothesis with `st.from_regex()` to inject sensitive tokens into random command strings, min 100 iterations
    - **Validates: Requirements 1.8**

  - [ ]* 1.7 Write property test for `truncate_tool_result` — truncation round-trip correctness
    - **Property 5: Truncation round-trip correctness**
    - For any content string and any positive limit: if `len(content) <= limit` then returns `(content, False)`; if `len(content) > limit` then returned string has length ≤ limit and flag is `True`
    - Use Hypothesis with `st.text(min_size=0, max_size=10000)` and `st.integers(min_value=1, max_value=5000)`, min 100 iterations
    - **Validates: Requirements 3.1, 3.2, 3.5**

  - [ ]* 1.8 Write unit tests for summarizer edge cases
    - Test each category with concrete examples (Bash with command, Read with path, Write with file_path, Search with query, fallback)
    - Test None input_data, empty dict, missing expected keys
    - Test `DEFAULT_TRUNCATION_LIMIT` is 500
    - Test env var override for truncation limit
    - _Requirements: 1.3, 1.4, 1.5, 1.6, 1.7, 3.3, 3.4_

- [x] 2. Checkpoint — Verify summarizer module
  - Ensure all tests pass (`cd backend && pytest tests/test_tool_summarizer.py`), ask the user if questions arise.

- [x] 3. Integrate summarizer into `backend/core/agent_manager.py`
  - [x] 3.1 Modify `_format_message()` to use `summarize_tool_use` for ToolUseBlock
    - Import `summarize_tool_use` and `truncate_tool_result` from `core.tool_summarizer` (NOT `backend.core.tool_summarizer` — backend runs as standalone FastAPI app)
    - In the `ToolUseBlock` branch (after the AskUserQuestion early return), replace `"input": block.input` with `"summary": summarize_tool_use(block.name, block.input)`
    - Omit the `input` key entirely from the emitted dict
    - AskUserQuestion special case remains unchanged (returns early before this code)
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.2 Modify `_format_message()` to use `truncate_tool_result` for ToolResultBlock
    - In the `ToolResultBlock` branch, call `truncate_tool_result(block_content)` to get `(truncated_content, was_truncated)`
    - Replace `"content": block_content` with `"content": truncated_content`
    - Add `"truncated": was_truncated` to the emitted dict
    - Preserve existing `is_error` field
    - _Requirements: 3.1, 3.2, 3.5, 7.1, 7.2, 7.3, 7.4, 7.5_

  - [ ]* 3.3 Write property test for tool_use output shape
    - **Property 4: tool_use output shape**
    - For any ToolUseBlock with name != "AskUserQuestion", the formatted output dict contains exactly keys `{type, id, name, summary}` and does not contain key `input`
    - Mock ToolUseBlock objects with Hypothesis-generated names and inputs, min 100 iterations
    - **Validates: Requirements 2.1, 2.2**

- [x] 4. Checkpoint — Verify backend integration
  - Ensure all backend tests pass (`cd backend && pytest`), ask the user if questions arise.

- [x] 5. Update frontend types in `desktop/src/types/index.ts`
  - [x] 5.1 Update `ToolUseContent` interface
    - Remove `input: Record<string, unknown>` field
    - Add `summary: string` field
    - _Requirements: 4.1, 4.2_

  - [x] 5.2 Update `ToolResultContent` interface
    - Add `truncated: boolean` field
    - _Requirements: 4.3_

- [x] 6. Update frontend components
  - [x] 6.1 Create `MergedToolBlock.tsx` component
    - New component at `desktop/src/pages/chat/components/MergedToolBlock.tsx`
    - Props: `name`, `summary`, `toolUseId`, `resultContent?`, `resultTruncated?`, `resultIsError?`, `isPending`
    - Define `INLINE_RESULT_LIMIT = 200` constant
    - When `isPending`: show summary line with spinning indicator, no result section
    - When result is short (≤200 chars, not truncated): show inline beneath summary, no toggle
    - When result is long/truncated: collapsible section, collapsed by default
    - When error: show error icon on summary line, show error content inline
    - When orphaned (!isPending && no result): show summary with neutral "—" indicator, no spinner
    - Success/error status indicator on the summary line (green check / red X)
    - Include module-level docstring per SwarmAI documentation standards
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9_

  - [x] 6.2 Update `ContentBlockRenderer.tsx` with merged routing
    - Accept `resultMap` (pre-built `Map<toolUseId, ToolResultContent>`) and `isStreaming` as additional props
    - For `tool_use` blocks: look up matching result via `resultMap.get(block.id)` (O(1) instead of O(n) scan)
    - Render `MergedToolBlock` with paired result data (or `isPending=true` if no result yet during streaming)
    - For `tool_result` blocks: check if already rendered by a preceding `MergedToolBlock` → return null
    - Orphaned `tool_result` (no matching `tool_use`): render standalone `ToolResultBlock` as fallback
    - Remove TodoWrite special case (input no longer exists; summarizer handles it)
    - _Requirements: 5.1, 5.2, 6.1_

  - [x] 6.3 Update `AssistantMessageView.tsx` to pass `resultMap` and `isStreaming` to `ContentBlockRenderer`
    - Build `resultMap` via `useMemo` from `message.content` — `Map<toolUseId, ToolResultContent>`
    - Pass `resultMap`, `allBlocks` (message.content), and `isStreaming` as props
    - _Requirements: 5.1_

  - [x] 6.4 Simplify `ToolUseBlock.tsx` as fallback
    - Change props from `{ name, input }` to `{ name, summary }`
    - Remove expand/collapse toggle, copy button, and JSON serialization
    - Render single-line: icon + summary text (fall back to name if summary is empty)
    - Remove `useState` and `useMemo` hooks (no longer needed)
    - _Requirements: 5.2 (summary as label)_

  - [x] 6.5 Update `ToolResultBlock.tsx` as standalone fallback
    - Add `truncated: boolean` to props interface
    - When `truncated === true`, display a "Content truncated" visual indicator
    - Keep existing expand/collapse and error/success status indicator behavior
    - _Requirements: 6.1, 6.2, 6.3_

  - [x] 6.6 Simplify `deriveStreamingActivity()` in `useChatStreamingLifecycle.ts`
    - Replace `extractToolContext(toolInput)` call with direct `summary` field access from the last tool_use block
    - Delete `extractToolContext()` and `sanitizeCommand()` functions (dead code — no backward compat needed)
    - Delete the `extractToolContext` test block (15 tests) and `sanitizeCommand` test block (9 tests) from `desktop/src/__tests__/useChatStreamingLifecycle.test.ts`
    - Update all `tool_use` block fixtures in the following test files to use `summary` instead of `input`:
      - `desktop/src/__tests__/useChatStreamingLifecycle.test.ts` (12 occurrences of `input:`)
      - `desktop/src/pages/__tests__/ChatPageSpinner.property.test.tsx` (6 occurrences)
      - `desktop/src/__tests__/chat-experience-cleanup/persistence.property.test.ts` (1 occurrence)
      - `desktop/src/__tests__/streaming-lifecycle-preservation.test.ts` (1 occurrence)
    - Also add `truncated: false` to any `tool_result` fixtures that don't have it
    - _Requirements: 5.2_

  - [ ]* 6.7 Write unit tests for `MergedToolBlock` and merged routing
    - Test MergedToolBlock renders pending spinner when no result and streaming
    - Test MergedToolBlock renders short result inline (≤200 chars, not truncated)
    - Test MergedToolBlock renders long/truncated result in collapsible section
    - Test MergedToolBlock renders error result with error icon
    - Test MergedToolBlock renders success result with success icon
    - Test MergedToolBlock renders orphaned state (no result, not streaming) with neutral indicator
    - Test ContentBlockRenderer returns null for tool_result consumed by MergedToolBlock
    - Test ContentBlockRenderer renders orphaned tool_result as standalone ToolResultBlock
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 6.1, 6.2, 6.3_

- [x] 7. Checkpoint — Verify frontend components
  - Ensure all frontend tests pass (`cd desktop && npm test -- --run`), ask the user if questions arise.

- [ ] 8. Frontend deduplication property tests
  - [ ]* 8.1 Write property test for blockKey stability
    - **Property 6: blockKey stability under new fields**
    - For any tool_use block (with or without `summary`), `blockKey` returns `"tool_use:{id}"`
    - For any tool_result block (with or without `truncated`), `blockKey` returns `"tool_result:{toolUseId}"`
    - Use fast-check with `fc.record()` generators, min 100 iterations
    - **Validates: Requirements 8.1, 8.2**

  - [ ]* 8.2 Write property test for deduplication idempotence
    - **Property 7: Deduplication idempotence**
    - For any message list with an assistant message, calling `updateMessages` with an existing block (same blockKey) shall not increase the content array length
    - Use fast-check with message list generators, min 100 iterations
    - **Validates: Requirements 8.3**

  - [ ]* 8.3 Write property test for merged rendering pairing correctness
    - **Property 8: Merged rendering pairing correctness**
    - For any content block array: a tool_result whose toolUseId matches a tool_use id SHALL be rendered as null (consumed by MergedToolBlock); a tool_result with no matching tool_use SHALL render as standalone ToolResultBlock
    - Use fast-check with generated content block arrays containing mixed tool_use/tool_result/text blocks, min 100 iterations
    - **Validates: Requirements 5.1, 5.2, 6.1**

- [x] 9. Legacy session cleanup
  - [x] 9.1 Add one-time startup cleanup in `backend/main.py`
    - On startup, check for a version marker in `app_settings` table (e.g., `tool_streaming_v1`)
    - If marker is absent: `DELETE FROM chat_messages; DELETE FROM chat_sessions;` then set the marker
    - If marker exists: skip cleanup (idempotent)
    - Do NOT delete agents, skills, MCP servers, tasks, todos, or other non-chat data
    - _Requirements: 9.1, 9.2, 9.3_

- [x] 10. Final checkpoint — Ensure all tests pass
  - Run full backend test suite (`cd backend && pytest`)
  - Run full frontend test suite (`cd desktop && npm test -- --run`)
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Backend implementation (tasks 1–4) is independent of frontend (tasks 5–8) and can be verified separately
- Property tests validate universal correctness properties from the design document
- The Claude Agent SDK conversation loop is unaffected — only the UI replay path (SSE + SQLite) changes
- No database migration needed — the JSON content column simply stores `summary` instead of `input`
- Task 9 (session cleanup) must run before the frontend changes are deployed to avoid runtime type mismatches
- All tool name matching uses `name.lower()` for case-insensitive comparison (PE Review finding #3)
- Import path is `from core.tool_summarizer import ...` (NOT `backend.core.`) since backend runs standalone (PE Review finding #2)
