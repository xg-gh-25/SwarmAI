# Implementation Plan: Memory System Robustness

## Overview

Implement five reliability improvements to SwarmAI's memory system: per-file advisory locking for concurrent write protection, a 2000-token cap on DailyActivity injection into the system prompt, a structured distillation skill, YAML frontmatter-based processing state, and session-start Open Threads review. All changes are in the Python backend (`backend/core/`, `backend/routers/`, `backend/skills/`, `backend/context/`). No frontend changes.

## Tasks

- [x] 1. Create frontmatter parser utility (`backend/core/frontmatter.py`)
  - [x] 1.1 Implement `parse_frontmatter` and `write_frontmatter` functions
    - Create `backend/core/frontmatter.py` with module-level docstring
    - `parse_frontmatter(content: str) -> tuple[dict, str]`: detect `---` delimiters, parse YAML with `yaml.safe_load()`, return `(metadata, body)`. Return `({}, content)` for missing/malformed frontmatter, log warning for malformed YAML
    - `write_frontmatter(metadata: dict, body: str) -> str`: produce `---\nYAML\n---\n\nbody`. If metadata is empty, return just the body (no empty frontmatter block)
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.7_

  - [ ]* 1.2 Write property test: frontmatter round-trip (Property 7)
    - **Property 7: Frontmatter round-trip**
    - `parse_frontmatter(write_frontmatter(metadata, body))` produces equivalent metadata and body for all valid inputs
    - Use Hypothesis with `st.dictionaries` for metadata and `st.text` for body
    - Test file: `backend/tests/test_frontmatter.py`
    - **Validates: Requirements 6.6**

  - [ ]* 1.3 Write property test: frontmatter output format invariant (Property 8)
    - **Property 8: Frontmatter output format invariant**
    - For non-empty metadata: output starts with `---` on line 1, contains closing `---`, blank line before body
    - Test file: `backend/tests/test_frontmatter.py`
    - **Validates: Requirements 6.7**

  - [ ]* 1.4 Write unit tests for frontmatter edge cases
    - No frontmatter returns `({}, content)` (Req 6.3)
    - Malformed YAML returns `({}, content)` and logs warning (Req 6.5)
    - Empty string input returns `({}, "")` (Req 4.2)
    - Frontmatter with `distilled: true` parses correctly (Req 4.1)
    - Frontmatter with no closing `---` treated as no frontmatter
    - Test file: `backend/tests/test_frontmatter.py`
    - _Requirements: 6.3, 6.5, 4.1, 4.2_

- [x] 2. Create locked_write helper script (`backend/scripts/locked_write.py`)
  - [x] 2.1 Implement `locked_write.py` CLI script
    - Create `backend/scripts/locked_write.py` with module-level docstring
    - Self-contained script — inlines `fcntl.flock()` locking (no separate module)
    - CLI: `python locked_write.py --file PATH --section SECTION --append TEXT`
    - Acquires flock on `{path}.lock` sibling file, reads target, modifies section, writes back, releases in finally
    - If section not found, append under `## Distilled` fallback
    - If target file doesn't exist, create it
    - Lock timeout: 5 seconds, exit code 1 on failure
    - _Requirements: 1.1, 1.3, 1.6, 1.7, 1.8_

  - [ ]* 2.2 Write tests for locked_write.py
    - Concurrent invocations preserve all writes (Property 1)
    - Lock timeout exits with code 1
    - Section not found → fallback section
    - Target file doesn't exist → created
    - Test file: `backend/tests/test_locked_write.py`
    - _Requirements: 1.1, 1.3, 1.6_

- [x] 3. Checkpoint — Core modules complete
  - Ensure all tests pass for `test_frontmatter.py` and `test_locked_write.py`, ask the user if questions arise.

- [x] 4. Implement DailyActivity token cap in system prompt
  - [x] 4.1 Add `_truncate_daily_content` helper and token cap to `_build_system_prompt`
    - In `backend/core/agent_manager.py`, add module-level constants: `TOKEN_CAP_PER_DAILY_FILE = 2000`, `TRUNCATION_MARKER = "[Truncated: kept newest ~2000 tokens]"`
    - Implement `_truncate_daily_content(content: str, cap: int) -> str` as a module-level helper
    - Use word-based truncation: keep last N words where N = `cap * 3 / 4` (inverse of the 4/3 token estimation heuristic from `ContextDirectoryLoader.estimate_tokens`)
    - Prepend `TRUNCATION_MARKER` when truncation occurs
    - Modify the DailyActivity reading block in `_build_system_prompt()` to call `ContextDirectoryLoader.estimate_tokens()` on each file's content and apply `_truncate_daily_content` when the estimate exceeds `TOKEN_CAP_PER_DAILY_FILE`
    - Disk files are never modified — truncation is ephemeral, applied only to the injected content
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [ ]* 4.2 Write property test: token cap with tail preservation (Property 6)
    - **Property 6: Token cap with tail preservation**
    - For any content string: (a) estimated tokens of result ≤ 2000 + marker overhead, (b) if original exceeded cap, result starts with truncation marker, (c) non-marker portion is a contiguous suffix of original
    - Use `st.text(min_size=100, max_size=10000)` for DailyActivity content
    - Test file: `backend/tests/test_daily_token_cap.py`
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.6**

  - [ ]* 4.3 Write unit tests for token cap edge cases
    - Content under cap passes through unchanged
    - Content exactly at cap passes through unchanged
    - Empty content returns empty string
    - Single very long line handled gracefully (best effort)
    - Disk file is not modified after prompt assembly (Req 2.4)
    - Test file: `backend/tests/test_daily_token_cap.py`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.6_

- [x] 5. Create locked_write helper script
  - [x] 5.1 Create `backend/scripts/locked_write.py` helper
    - A small CLI script that acquires `file_lock`, reads the target file, applies a modification (append to section or replace section), writes back, releases lock
    - Usage: `python locked_write.py --file MEMORY.md --section "Key Decisions" --append "- New decision here"`
    - The distillation skill and save-memory skill instruct the agent to call this script for MEMORY.md writes
    - DailyActivity writes use standard `>>` append (no lock needed)
    - _Requirements: 1.1, 1.3, 1.8_

- [x] 6. Checkpoint — Token cap and locking integration complete
  - Ensure all tests pass for `test_daily_token_cap.py`, ask the user if questions arise.

- [x] 7. Create distillation skill and update templates
  - [x] 7.1 Create `backend/skills/s_memory-distill/SKILL.md`
    - Create directory `backend/skills/s_memory-distill/`
    - Write `SKILL.md` with YAML frontmatter (name: Memory Distill, description)
    - Include structured sections: Detection (scan DailyActivity, count files without `distilled: true`, exit if ≤7), Extraction (key decisions, lessons, themes, corrections, error resolutions), Writing (lock MEMORY.md, read-modify-write to appropriate sections with fallback `## Distilled` section), Marking (add `distilled: true` and `distilled_date: YYYY-MM-DD` frontmatter to each processed file), Archiving (move >30d files to Archives, delete >90d archived files), Open Threads (cross-reference for completions)
    - All operations silent — no announcements, no permission requests
    - Instruct agent to use standard file write tools (locking is transparent at API level)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 4.1, 4.3, 4.4, 4.5, 4.7, 5.4_

  - [x] 7.2 Update AGENT.md template for session-start Open Threads review
    - In `backend/context/AGENT.md`, locate the "Every Session" section
    - Add a step after reading context: "Review MEMORY.md's Open Threads section and update based on completed work since last session"
    - Ensure the directive says "At session start", not "At session end"
    - _Requirements: 5.1, 5.2_

  - [x] 7.3 Update STEERING.md template for session-start memory protocol
    - In `backend/context/STEERING.md`, replace the "At session end (if asked)" block with an "At session start" block
    - New block: read MEMORY.md silently, read DailyActivity, review Open Threads — mark completed items, add new ones, don't announce
    - Update the "Distillation" sub-section: change "move processed files to Archives with `distilled: true` frontmatter" to "mark processed files with `distilled: true` frontmatter in place; files stay in DailyActivity until 30-day auto-prune"
    - _Requirements: 5.1, 5.2, 5.3, 5.5_

  - [x]* 7.4 Write unit tests for template and skill verification
    - AGENT.md contains "At session start" directive for Open Threads (Req 5.2)
    - STEERING.md contains "At session start" block, not "At session end" (Req 5.3)
    - `s_memory-distill/SKILL.md` exists and contains required sections (Req 3.1)
    - `s_memory-distill/SKILL.md` references `distilled: true` frontmatter marking (Req 3.6)
    - Test file: `backend/tests/test_context_templates.py` (extend existing)
    - _Requirements: 3.1, 5.2, 5.3_

- [x] 8. Add `.lock` pattern to `.gitignore`
  - Add `*.lock` pattern to the workspace `.gitignore` so lock sentinel files don't appear in git status
  - If no `.gitignore` exists at workspace root, create one
  - _Requirements: 1.5 (design: lock file convention)_

- [x] 9. Final checkpoint — Ensure all tests pass
  - Run full test suite: `cd backend && pytest tests/test_locked_write.py tests/test_frontmatter.py tests/test_daily_token_cap.py tests/test_context_templates.py -v`
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests use Hypothesis with `@settings(max_examples=100)`
- All new Python files require module-level docstrings per project conventions
- Backend uses `snake_case`; no frontend changes in this spec
- Locking is transparent at the API level — the agent never invokes locks directly
- Token cap is ephemeral (prompt-assembly only) — disk files are never modified
- The distillation skill is a Markdown file (SKILL.md), not Python code
