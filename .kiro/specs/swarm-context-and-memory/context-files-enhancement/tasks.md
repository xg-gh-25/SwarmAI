# Implementation Plan: Context Files Enhancement

## Overview

Implement the enhanced context file system across three layers: Template Layer (revised/new templates), Loader Layer (ContextDirectoryLoader changes), and Workspace Layer (SwarmWorkspaceManager changes). Python backend with React/TypeScript frontend. Property-based tests use Hypothesis.

## Tasks

- [x] 1. Refactor ContextFileSpec from NamedTuple to frozen dataclass
  - [x] 1.1 Convert ContextFileSpec to `@dataclass(frozen=True)` with `user_customized: bool = False` and `truncate_from: Literal["head", "tail"] = "tail"` fields in `backend/core/context_directory_loader.py`
    - Replace the existing NamedTuple with the frozen dataclass
    - Add imports for `dataclass` and `Literal`
    - Ensure all existing field-by-name access continues to work
    - _Requirements: 10.1, 14.2, 16.2_

  - [x] 1.2 Update CONTEXT_FILES list with new fields and TOOLS.md entry
    - Add `user_customized` and `truncate_from` values to all existing entries
    - Insert TOOLS.md at priority 6 with `user_customized=True`, `truncatable=True`
    - Shift MEMORY.md to priority 7 with `truncate_from="head"`, KNOWLEDGE.md to 8, PROJECTS.md to 9
    - Mark SWARMAI/IDENTITY/SOUL/AGENT as `user_customized=False`; USER/STEERING/TOOLS/MEMORY/KNOWLEDGE/PROJECTS as `user_customized=True`
    - _Requirements: 6.2, 6.3, 6.4, 9.2, 9.3, 10.2, 14.1, 16.3, 16.4_

- [x] 2. Create prerequisite template files (needed by ensure_directory)
  - [x] 2.1 Create TOOLS.md template in `backend/context/`
    - Sections: Device Names, SSH Hosts, Local Tool Preferences, Network Paths, Environment Notes
    - Include `👤 USER-CUSTOMIZED` marker at top
    - _Requirements: 6.1, 6.4, 13.18_

  - [x] 2.2 Create BOOTSTRAP.md template in `backend/context/`
    - Onboarding instructions: gather name, timezone, language, role, work context, communication style
    - Instruct agent to write to USER.md and delete BOOTSTRAP.md when done
    - _Requirements: 4.2, 4.3, 4.5_

- [x] 3. Implement two-mode copy logic in ensure_directory()
  - [x] 3.1 Implement two-mode copy with permissions in `ensure_directory()`
    - For `user_customized=False`: always-overwrite from template + `chmod 0o444`
    - For `user_customized=True`: copy-only-if-missing + `chmod 0o644`
    - Add content comparison to skip unnecessary writes for system files
    - Remove readonly before overwriting, re-apply after
    - Best-effort chmod (catch OSError for Windows)
    - _Requirements: 9.1, 9.6, 9.7, 10.3, 10.4, 10.5, 10.7, 14.3, 14.4_

  - [ ]* 3.2 Write property test for two-mode copy behavior
    - **Property 1: Two-Mode Copy Behavior**
    - **Validates: Requirements 10.3, 10.4, 10.5, 10.7, 14.3**
    - Test in `backend/tests/test_context_directory_loader.py`
    - Generate random ContextFileSpec entries with random `user_customized` values and random pre-existing file content
    - Verify: system files always match template; user files preserved if existing; user files created from template if missing

  - [ ]* 3.3 Write property test for file permissions
    - **Property 2: File Permissions Match user_customized Flag**
    - **Validates: Requirements 9.1, 9.6, 9.7, 14.4**
    - Test in `backend/tests/test_context_directory_loader.py`
    - After `ensure_directory()`, verify `0o444` for `user_customized=False` and `0o644` for `user_customized=True`

- [x] 4. Implement BOOTSTRAP.md detection and creation
  - [x] 4.1 Add `_maybe_create_bootstrap()` and `_is_empty_template()` methods to ContextDirectoryLoader
    - `_is_empty_template()`: structural detection checking placeholder fields (Name, Timezone, Role)
    - `_maybe_create_bootstrap()`: create BOOTSTRAP.md from template if USER.md is empty and BOOTSTRAP.md doesn't exist
    - Call `_maybe_create_bootstrap()` at end of `ensure_directory()`
    - _Requirements: 4.1, 4.4, 4.6, 14.5_

  - [ ]* 4.2 Write property test for BOOTSTRAP.md creation
    - **Property 3: BOOTSTRAP.md Creation Iff USER.md Is Empty Template**
    - **Validates: Requirements 4.1, 4.6, 14.5**
    - Test in `backend/tests/test_context_directory_loader.py`
    - Generate random USER.md content (empty template variants, populated content)
    - Verify: BOOTSTRAP.md created iff empty template AND not already existing

- [x] 5. Implement dynamic token budget and truncation direction
  - [x] 5.1 Add `compute_token_budget()` method to ContextDirectoryLoader
    - Public method: >=200K→40000, >=64K and <200K→25000, <64K→self.token_budget
    - Handle None/0 model_context_window with DEFAULT_TOKEN_BUDGET fallback
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 14.6_

  - [x] 5.2 Update `load_all()` to use dynamic budget
    - Call `compute_token_budget(model_context_window)` before `_assemble_from_sources()`
    - Pass computed budget to `_assemble_from_sources()` and `_enforce_token_budget()`
    - _Requirements: 11.6, 11.7, 14.6_

  - [x] 5.3 Extend `_enforce_token_budget()` with `truncate_from` support
    - Add `truncate_from` to section tuples (5th element)
    - When `truncate_from="tail"`: keep first N words (existing behavior)
    - When `truncate_from="head"`: keep last N words, prepend `[Truncated]`
    - _Requirements: 16.1, 16.3, 16.4, 16.5_

  - [ ]* 5.4 Write property test for truncation direction
    - **Property 5: Truncation Direction Matches truncate_from Field**
    - **Validates: Requirements 16.1, 16.3, 16.4, 16.5**
    - Test in `backend/tests/test_context_directory_loader.py`
    - Generate random section content and truncate_from values
    - Verify: tail keeps first N words, head keeps last N words with `[Truncated]` prefix

  - [ ]* 5.5 Write property test for dynamic token budget tiers
    - **Property 4: Dynamic Token Budget Tiers**
    - **Validates: Requirements 11.1, 11.2, 11.3, 11.4, 11.6, 11.7, 14.6**
    - Test in `backend/tests/test_context_directory_loader.py`
    - Use `@given(st.integers(min_value=0, max_value=500_000))` for model_context_window
    - Verify: >=200K→40000, >=64K→25000, <64K→instance default

- [x] 6. Implement L1 cache budget-tier awareness
  - [x] 6.1 Add budget tier to L1 cache header and freshness check
    - Store budget in L1 cache header: `<!-- budget:40000 -->`
    - Update `_write_l1_cache()` to accept and write budget parameter
    - Update `_load_l1_if_fresh()` to accept `expected_budget` and compare against header
    - Return None (stale) if budget mismatch
    - _Requirements: 11.6, 11.7, 14.12_

  - [ ]* 6.2 Write property test for L1 cache budget-tier consistency
    - **Property 11: L1 Cache Budget-Tier Consistency**
    - **Validates: Requirements 11.6, 11.7, 14.12**
    - Test in `backend/tests/test_context_directory_loader.py`
    - Generate cache with budget B1, request with budget B2
    - Verify: returns None when B1 ≠ B2, returns content when B1 == B2

- [x] 7. Checkpoint — Loader Layer
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Update SwarmWorkspaceManager Knowledge directory structure
  - [x] 8.1 Update `KNOWLEDGE_SUBDIRS` and `SYSTEM_MANAGED_FOLDERS` constants
    - Set `KNOWLEDGE_SUBDIRS = ["Notes", "Reports", "Meetings", "Library", "Archives", "DailyActivity"]`
    - Expand `SYSTEM_MANAGED_FOLDERS` with all six `Knowledge/` subdir paths
    - _Requirements: 12.1, 12.8_

  - [x] 8.2 Update `create_folder_structure()` to create all six subdirectories
    - Create Notes, Reports, Meetings, Library, Archives, DailyActivity under Knowledge/
    - _Requirements: 12.3_

  - [ ]* 8.3 Write property test for Knowledge subdirectory creation
    - **Property 6: Knowledge Subdirectory Creation**
    - **Validates: Requirements 12.1, 12.3**
    - Test in `backend/tests/test_swarm_workspace_manager.py`
    - Generate random workspace paths (tmp dirs), verify all 6 subdirs exist after `create_folder_structure()`

  - [x] 8.4 Implement legacy `Knowledge Base/` → `Library/` migration in `_cleanup_legacy_content()`
    - Move files from `Knowledge Base/` to `Library/`, skip if dest exists
    - Remove empty `Knowledge Base/` directory after migration
    - _Requirements: 12.2, 12.4, 12.5_

  - [ ]* 8.5 Write property test for legacy migration
    - **Property 8: Legacy Knowledge Base Migration Preserves Files**
    - **Validates: Requirements 12.2, 12.4**
    - Test in `backend/tests/test_swarm_workspace_manager.py`
    - Generate random file sets in `Knowledge Base/`, run cleanup, verify all in `Library/` with identical content

  - [x] 8.6 Update `verify_integrity()` to self-heal all six Knowledge subdirectories
    - Check and recreate any missing subdirs without modifying existing ones
    - _Requirements: 12.10_

  - [ ]* 8.7 Write property test for verify_integrity self-healing
    - **Property 7: verify_integrity Self-Healing**
    - **Validates: Requirements 12.10**
    - Test in `backend/tests/test_swarm_workspace_manager.py`
    - Generate random subsets of 6 subdirs to delete, run `verify_integrity()`, verify all restored

- [x] 9. Implement system-managed folder protection
  - [x] 9.1 Add delete/rename rejection for system-managed folders in workspace API
    - Return HTTP 403 for delete/rename operations on paths in `SYSTEM_MANAGED_FOLDERS`
    - Error message: "Cannot delete/rename system-managed directory: {path}"
    - _Requirements: 12.9_

  - [ ]* 9.2 Write property test for system-managed folder protection
    - **Property 9: System-Managed Folder Protection**
    - **Validates: Requirements 12.9**
    - Test in `backend/tests/test_swarm_workspace_manager.py`
    - Generate random paths from `SYSTEM_MANAGED_FOLDERS`, attempt delete/rename, verify rejection

- [x] 10. Checkpoint — Workspace Layer
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Revise remaining template files
  - [x] 11.1 Revise SOUL.md template
    - Add "你不是聊天机器人" framing
    - Add Good vs Bad response examples
    - Add Continuity section (context files ARE memory)
    - Preserve ⚙️ SYSTEM DEFAULT marker
    - _Requirements: 13.1, 13.2, 13.3, 13.18, 13.19_

  - [x] 11.2 Revise IDENTITY.md template
    - Add avatar field (workspace-relative path, URL, or data URI)
    - Add evolving identity guidance
    - Preserve ⚙️ SYSTEM DEFAULT marker
    - _Requirements: 13.4, 13.5, 13.18_

  - [x] 11.3 Revise AGENT.md template
    - Add "写下来，不要心理笔记" directive
    - Add "trash > rm" safety rule (recoverable > permanent)
    - Add Channel Behavior section (Feishu, CLI, Web rules)
    - Add Req 15 writing rules: write to DailyActivity during session, "remember this" → MEMORY.md
    - Preserve ⚙️ SYSTEM DEFAULT marker
    - _Requirements: 2.1, 2.2, 2.3, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 13.6, 13.7, 13.8, 13.18, 13.19, 15.1, 15.2, 15.3_

  - [x] 11.4 Revise USER.md template
    - Add Background section (what user cares about, projects, what makes them tick)
    - Add humanistic footer with Chinese text
    - Preserve 👤 USER-CUSTOMIZED marker
    - _Requirements: 3.1, 3.2, 3.3, 13.9, 13.10, 13.18_

  - [x] 11.5 Revise STEERING.md template
    - Rewrite Memory Protocol: "写下来。文件 > 大脑" directive, two-tier model
    - Remove "note important discoveries mentally"
    - Add Req 15 coordination rules: write DailyActivity during session, distill to MEMORY.md periodically, read both at session start
    - Update SwarmWS Directory Structure with new Knowledge subdirs
    - Add File Saving & Knowledge Organization rules
    - Retain session start/end behaviors
    - Preserve 👤 USER-CUSTOMIZED marker
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 13.11, 13.12, 13.13, 15.4, 15.5, 15.6, 15.8, 15.14_

  - [x] 11.6 Revise MEMORY.md template
    - Add two-tier model guidance (DailyActivity vs curated memory)
    - Add distillation instructions
    - Preserve 🤖 AGENT-MANAGED marker
    - _Requirements: 13.14, 13.15_

  - [x] 11.7 Revise KNOWLEDGE.md template
    - Restructure as Knowledge Directory index
    - Remove Tech Stack, Coding Conventions, Architecture Notes, Reference sections
    - Add sections for each subfolder (Notes, Reports, Meetings, Library, Archives, DailyActivity)
    - Add guidance to update index when creating files
    - Preserve 👤 USER-CUSTOMIZED marker
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 13.16_

  - [x] 11.8 Revise PROJECTS.md template
    - Add project folder linking guidance (SwarmWS/Projects/ ↔ PROJECTS.md entries)
    - Preserve 👤 USER-CUSTOMIZED marker
    - _Requirements: 13.17, 13.18_

  - [x]* 11.9 Write unit tests for template content verification
    - Create `backend/tests/test_context_templates.py`
    - Verify each template contains required markers (⚙️/👤/🤖)
    - Verify required sections and directives exist
    - Verify removed content is absent
    - Verify total system-default token count ≤ 3,000 tokens
    - _Requirements: 13.18, 13.19, 13.20_

- [x] 12. Integrate _build_system_prompt() with new architecture
  - [x] 12.1 Add BOOTSTRAP.md detection and prepend in `_build_system_prompt()`
    - Check for BOOTSTRAP.md in context_dir after loading context
    - If present, prepend as `## Onboarding` section
    - BOOTSTRAP.md content NOT included in L1 cache
    - _Requirements: 4.5, 14.14, 14.15_

  - [x] 12.2 Add DailyActivity reading in `_build_system_prompt()`
    - Read today's and yesterday's `Knowledge/DailyActivity/YYYY-MM-DD.md` files
    - Append as ephemeral context after cached context (not part of L1 cache)
    - _Requirements: 15.5, 15.6_

  - [x] 12.3 Add `user_customized` and `effective_token_budget` to prompt metadata
    - Include `user_customized` field in per-file metadata dict
    - Include `effective_token_budget` in top-level prompt metadata
    - Pass model context window to loader for dynamic budget
    - _Requirements: 11.8, 14.9, 14.10, 14.11_

  - [x] 12.4 Review SystemPromptBuilder for overlap with revised AGENT.md
    - Check `_section_safety()` for duplicated rules (trash > rm, etc.)
    - Remove duplicates, defer operational safety to AGENT.md
    - Keep only core AI safety principles in SystemPromptBuilder
    - _Requirements: 14.7, 14.8_

- [x] 13. Implement readonly API response for context files
  - [x] 13.1 Add `readonly` field to workspace file API response
    - Map `user_customized=False` → `readonly: true` in `GET /workspace/file` response
    - Map `user_customized=True` → `readonly: false`
    - Default to `readonly: false` on read failure
    - _Requirements: 9.4_

  - [x]* 13.2 Write property test for readonly API response
    - **Property 10: Readonly API Response for System Default Files**
    - **Validates: Requirements 9.4**
    - Test in `backend/tests/test_context_directory_loader.py`
    - Generate random ContextFileSpec entries, verify API response `readonly` field matches `user_customized`

- [x] 14. Implement frontend readonly banner
  - [x] 14.1 Add readonly banner to frontend file editor
    - Check `readonly` field from API response
    - Display read-only mode with banner: "⚙️ System Default — This file is managed by SwarmAI and refreshed on every startup. Use STEERING.md to customize behavior."
    - Disable editing when `readonly: true`
    - _Requirements: 9.5_

- [x] 15. Implement Archives auto-pruning
  - [x] 15.1 Add auto-pruning logic for `Knowledge/Archives/` during distillation
    - Delete archived DailyActivity files older than 90 days
    - Parse date from filename (YYYY-MM-DD.md format)
    - Skip non-date filenames and handle IO errors gracefully
    - _Requirements: 7.6, 15.11_

- [x] 16. Checkpoint — Integration
  - Ensure all tests pass, ask the user if questions arise.

- [x] 17. Final wiring and cross-cutting validation
  - [x] 17.1 Verify no remaining references to `Knowledge Base` in templates, tests, and code
    - Grep codebase for "Knowledge Base" — should return zero hits after Tasks 8.4, 11.x
    - _Requirements: 12.5, 12.6_

  - [x] 17.2 Verify CONTEXT_FILES priorities are sequential and complete
    - Ensure no gaps or duplicates in priority ordering (0-9)
    - Verify all 10 entries present with correct fields
    - _Requirements: 14.1, 14.2_

  - [ ]* 17.3 Write integration test for full _build_system_prompt() flow
    - Test with BOOTSTRAP.md present → onboarding section prepended
    - Test metadata includes `user_customized` and `effective_token_budget`
    - Test DailyActivity files appended as ephemeral context
    - _Requirements: 14.9, 14.10, 14.11, 14.14_

- [x] 18. Final checkpoint — All tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests validate universal correctness properties from the design document (11 properties)
- Backend uses Python (snake_case), frontend uses TypeScript/React (camelCase)
- All code files require module-level docstrings per project conventions
- Use `fsWrite` + `fsAppend` for file creation, never heredoc
