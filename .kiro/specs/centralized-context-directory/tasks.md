# Implementation Plan: Centralized Context Directory

## Overview

Implement the `ContextDirectoryLoader` module and integrate it into the system prompt pipeline, replacing all legacy context loading. The work proceeds bottom-up: data models and token estimation first, then core assembly logic, caching, integration with AgentManager/SystemPromptBuilder, and finally legacy cleanup.

## Tasks

- [x] 1. Create ContextDirectoryLoader module with data models and token estimation
  - [x] 1.1 Create `backend/core/context_directory_loader.py` with module docstring, imports, constants, `ContextFileSpec` namedtuple, `CONTEXT_FILES` list, and `DEFAULT_TOKEN_BUDGET`
    - Define all 9 `ContextFileSpec` entries with correct priority, section_name, and truncatable flag
    - Define `L1_CACHE_FILENAME`, `L0_CACHE_FILENAME`, `THRESHOLD_USE_L1`, `THRESHOLD_SKIP_LOW_PRIORITY` constants
    - _Requirements: 2.1, 10.4_
  - [x] 1.2 Implement `estimate_tokens()` static method
    - Use word-based formula: `max(1, int(len(text.split()) * 4 / 3))` for non-empty text, `0` for empty/whitespace-only (consistent with `ContextAssembler.estimate_tokens()`)
    - _Requirements: 10.1, 10.3, 10.4, 13.1, 13.2_
  - [ ]* 1.3 Write property test for token estimation (Property 7)
    - **Property 7: Token estimation proportionality**
    - **Validates: Requirements 10.1, 10.3**
  - [x] 1.4 Implement `ContextDirectoryLoader.__init__()` accepting `context_dir`, `token_budget`, and `templates_dir`
    - Store paths and budget as instance attributes
    - _Requirements: 3.1_

- [x] 2. Implement directory initialization and template copying
  - [x] 2.1 Implement `ensure_directory()` method
    - Create `~/.swarm-ai/.context/` if it doesn't exist
    - Copy all 9 source file defaults + L0 + L1 from `backend/context/`
    - Preserve existing files — only copy templates for missing files
    - Log warning and continue on individual file copy errors
    - _Requirements: 1.1, 1.2, 1.3, 1.4_
  - [ ]* 2.2 Write property test for template preservation (Property 6)
    - **Property 6: Template initialization preserves existing files**
    - **Validates: Requirements 1.2, 9.3**
  - [ ]* 2.3 Write unit tests for directory initialization
    - `test_ensure_directory_creates_all_templates` — empty dir → all 11 files created
    - `test_ensure_directory_filesystem_error` — permission error on one template, others still created
    - `test_load_all_returns_empty_on_dir_failure` — context dir cannot be created → returns `""`
    - _Requirements: 1.1, 1.3, 1.4, 11.1_

- [x] 3. Implement context file assembly and token budget enforcement
  - [x] 3.1 Implement `_assemble_from_sources()` method
    - Read all 9 source files from context directory in ascending priority order
    - Include section header (`## {section_name}`) for each non-empty file
    - Skip empty or missing files (no empty section headers)
    - Separate sections with double newlines
    - For models < 32K, exclude KNOWLEDGE.md and PROJECTS.md entirely
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 6.4_
  - [x] 3.2 Implement `_enforce_token_budget()` method
    - Truncate sections from lowest priority (8) upward when total exceeds budget
    - Never truncate SWARMAI.md (P0), IDENTITY.md (P1), SOUL.md (P2)
    - Append truncation indicator `[Truncated: X → Y tokens]` to truncated sections
    - Include section headers and separator whitespace in token count
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 10.2_
  - [ ]* 3.3 Write property test for assembly ordering (Property 1)
    - **Property 1: Assembly ordering and format**
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4**
  - [ ]* 3.4 Write property test for budget enforcement invariant (Property 2)
    - **Property 2: Token budget enforcement invariant**
    - **Validates: Requirements 3.1, 3.2, 3.4, 3.5**
  - [ ]* 3.5 Write property test for non-truncatable preservation (Property 3)
    - **Property 3: Non-truncatable files are never modified**
    - **Validates: Requirements 3.3**

- [x] 4. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement L1 cache (mtime-based freshness)
  - [x] 5.1 Implement `_is_l1_fresh()` method
    - Compare L1 cache file mtime against all 9 source file mtimes
    - Return True only if L1 mtime >= every source file mtime
    - _Requirements: 4.2, 4.3_
  - [x] 5.2 Implement `_write_l1_cache()` method
    - Write assembled content to `L1_SYSTEM_PROMPTS.md` in context directory
    - Log warning and continue if write fails (permissions, I/O error)
    - _Requirements: 4.1, 4.5_
  - [x] 5.3 Implement `_load_l1_if_fresh()` with TOCTOU mitigation (PE Fix)
    - Read L1 content, then re-check mtime freshness after read
    - If any source file changed during read, discard cached content and return None
    - _Requirements: 4.3, 4.4, 14.1, 14.2_
  - [ ]* 5.3 Write property test for L1 round-trip (Property 4)
    - **Property 4: L1 cache round-trip**
    - **Validates: Requirements 2.5, 4.1, 4.3, 4.4**
  - [ ]* 5.4 Write unit tests for L1 cache
    - `test_l1_freshness_check` — L1 older than source → re-assemble; newer → use cache
    - `test_l1_write_failure_returns_content` — L1 path read-only, content still returned
    - _Requirements: 4.2, 4.3, 4.4, 4.5_

- [x] 6. Implement L0 compact cache and model-aware selection
  - [x] 6.1 Implement `_load_l0()` method
    - Read `L0_SYSTEM_PROMPTS.md` if it exists
    - Fall back to `_assemble_from_sources()` with aggressive truncation if L0 missing
    - For models < 32K, exclude KNOWLEDGE.md and PROJECTS.md content entirely
    - _Requirements: 5.1, 5.2, 5.3, 6.3, 6.4_
  - [x] 6.2 Implement `load_all()` method with model-aware context selection
    - >= 128K tokens: use L1 cache or source files with full token budget
    - 64K–128K tokens: use L1 with aggressive truncation of KNOWLEDGE.md and PROJECTS.md
    - 32K–64K tokens: use L0 cache
    - < 32K tokens: use L0 cache, exclude KNOWLEDGE.md and PROJECTS.md
    - Wrap entire method in try/except, return `""` on failure
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 11.1_
  - [ ]* 6.3 Write property test for model-aware selection (Property 5)
    - **Property 5: Model-aware context selection**
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.4**
  - [ ]* 6.4 Write unit tests for L0 and model selection
    - `test_l0_fallback_when_missing` — delete L0, request small model → verify assembly from sources with aggressive truncation
    - `test_l0_contains_timestamp` — regenerate L0, verify generation timestamp in content
    - _Requirements: 5.2, 5.4_

- [x] 7. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Integrate with AgentManager
  - [x] 8.1 Add `_get_model_context_window()` helper method to `AgentManager`
    - Define `MODEL_CONTEXT_WINDOWS` dict mapping model IDs to context window sizes
    - Strip Bedrock prefix/suffix for lookup, default to 200K
    - _Requirements: 6.1_
  - [x] 8.2 Modify `AgentManager._build_system_prompt()` to invoke `ContextDirectoryLoader`
    - Instantiate `ContextDirectoryLoader` with `get_app_data_dir() / ".context"` and context dir
    - Call `ensure_directory()`, then `load_all(model_context_window)`
    - Append context text to `agent_config["system_prompt"]` before passing to `SystemPromptBuilder`
    - Wrap in try/except — log warning and proceed with SystemPromptBuilder only on failure
    - Remove all code that reads `agents.system_prompt` DB field for context content
    - Remove all code that invokes legacy `ContextManager` for global chats
    - _Requirements: 7.1, 7.2, 7.3, 7.5, 8.1, 8.2, 11.3, 12.2_
  - [ ]* 8.3 Write unit tests for AgentManager integration
    - `test_agent_manager_catches_loader_exception` — mock loader to raise, verify agent proceeds
    - `test_context_and_assembler_coexist` — verify both CDL and ContextAssembler output in final prompt
    - `test_db_system_prompt_not_read` — verify AgentManager doesn't query DB for system_prompt when CDL active
    - _Requirements: 7.1, 7.5, 11.3, 12.2_

- [x] 9. Simplify SystemPromptBuilder
  - [x] 9.1 Remove file-loading methods from `SystemPromptBuilder`
    - Delete `_section_user_identity()` — was loading `.swarmai/USER.md`
    - Delete `_section_project_context()` — was loading `.swarmai/IDENTITY.md`, `.swarmai/SOUL.md`, `.swarmai/BOOTSTRAP.md`
    - Delete `_section_extra_prompt()` — was injecting DB `agents.system_prompt`
    - Delete `_load_workspace_file()` helper — no longer needed
    - Update `build()` to only call: `_section_identity`, `_section_safety`, `_section_workspace`, `_section_selected_dirs`, `_section_datetime`, `_section_runtime`
    - _Requirements: 7.4_
  - [ ]* 9.2 Write property test for SystemPromptBuilder (Property 8)
    - **Property 8: SystemPromptBuilder has no file-loading methods**
    - Verify `build()` output contains only hardcoded sections, no filesystem content
    - **Validates: Requirements 7.4**
  - [ ]* 9.3 Write unit test for error resilience
    - `test_skip_invalid_utf8_file` — write binary content to one source file, verify it's skipped
    - _Requirements: 11.2_

- [x] 10. Delete legacy code and files
  - [x] 10.1 Delete `backend/templates/` directory and all files within it
    - _Requirements: 16.1_
  - [x] 10.2 Delete `backend/core/agent_sandbox_manager.py` entirely
    - Update all callers of `agent_sandbox_manager` to use `ContextDirectoryLoader.ensure_directory()` or `initialization_manager.get_cached_workspace_path()`
    - Remove the global `agent_sandbox_manager` singleton import from all modules
    - _Requirements: 9.1, 9.2, 9.3_
  - [x] 10.3 Remove agent bootstrap code in `agent_defaults.py` that reads `SWARMAI.md` from templates and writes to DB `agents.system_prompt`
    - The system prompt is now loaded from `~/.swarm-ai/.context/SWARMAI.md` at session start
    - _Requirements: 16.4_
  - [x] 10.4 Verify `SystemPromptBuilder` file-loading methods are fully removed (done in task 9.1)
    - _Requirements: 16.5_
  - [x] 10.5 Search and replace all remaining `backend/templates/` references in production code with `backend/context/`
    - _Requirements: 16.6_
  - [ ]* 10.6 Write unit tests for cleanup verification
    - `test_no_agent_sandbox_manager_imports` — verify no production code imports agent_sandbox_manager
    - `test_agent_defaults_no_db_system_prompt` — verify agent bootstrap doesn't write system_prompt to DB
    - _Requirements: 9.1, 16.1, 16.4_

- [x] 11. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- All code is Python; tests use `pytest` + `hypothesis` for property-based tests
- Test file: `backend/tests/test_context_directory_loader.py`
- Default context files live at `backend/context/`
- Each property test references its design document property number
- Checkpoints ensure incremental validation after each major phase
