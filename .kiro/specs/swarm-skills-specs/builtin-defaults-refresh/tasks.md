# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Fault Condition** — Stale Built-in Context Files
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate `ensure_directory()` skips existing built-in files
  - **Scoped PBT Approach**: Use Hypothesis to generate random file content pairs (source vs. existing dest with different content). The property asserts that after `ensure_directory()`, every file from `templates_dir` has content matching the source. On unfixed code, this FAILS because `if dest.exists(): continue` skips the overwrite.
  - Test file: `backend/tests/test_property_builtin_refresh_fault.py`
  - Setup: Create a tmp `templates_dir` with Hypothesis-generated files, create a tmp `context_dir` pre-populated with stale (different) content for the same filenames
  - Instantiate `ContextDirectoryLoader(context_dir=..., templates_dir=...)` and call `ensure_directory()`
  - Assert: `dest.read_bytes() == src.read_bytes()` for every file in `templates_dir`
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (dest still has stale content, proving the bug exists)
  - Document counterexamples found (e.g., "file X had stale content b'old' instead of source b'new'")
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 2.1_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** — User-Created Files and No-Change Idempotence
  - **IMPORTANT**: Follow observation-first methodology
  - Test file: `backend/tests/test_property_builtin_refresh_preservation.py`
  - **Property 2a — User files untouched**: Use Hypothesis to generate a set of built-in filenames (in `templates_dir`) and a disjoint set of user-created filenames (only in `context_dir`). After `ensure_directory()`, assert every user-created file has identical content to before the call — no modification, no deletion.
  - **Property 2b — Idempotent when content matches**: Use Hypothesis to generate files that exist in both `templates_dir` and `context_dir` with identical content. After `ensure_directory()`, assert file content is unchanged and no unnecessary writes occurred (verify via mtime or byte comparison).
  - **Property 2c — Stale symlink cleanup preserved**: Observe that `ProjectionLayer.project_skills()` still removes symlinks pointing to non-existent skill directories. Write a test with a mock `SkillManager` that returns a known skill set, create a stale symlink in the projection dir, call `project_skills()`, assert the stale symlink is removed.
  - Observe behavior on UNFIXED code for all three properties
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 3. Fix built-in defaults refresh

  - [x] 3.1 Change 1 — Always overwrite built-in context files in `ensure_directory()`
    - File: `backend/core/context_directory_loader.py`, method `ensure_directory()`
    - Remove the `if dest.exists(): continue` guard
    - Replace hardcoded `files_to_copy` list with iteration over ALL files in `self.templates_dir`
    - Add byte-comparison: read source, compare to dest, skip write if identical (avoids unnecessary I/O)
    - Wrap each file copy in try/except OSError to preserve failure isolation
    - _Bug_Condition: `isBugCondition(input)` where `input.dest_file EXISTS AND input.file_is_builtin` → file skipped_
    - _Expected_Behavior: `dest.read_bytes() == src.read_bytes()` for every file in `templates_dir` after call_
    - _Preservation: User-created files (not in `templates_dir`) are never touched_
    - _Requirements: 1.1, 2.1, 3.1, 3.3_

  - [x] 3.2 Change 2 — Add `refresh_builtin_defaults()` to `InitializationManager`
    - File: `backend/core/initialization_manager.py`
    - Add new async method `refresh_builtin_defaults()` with three independent try/except steps:
      1. `SkillManager.scan_all()` (MUST run first)
      2. `ProjectionLayer.project_skills()` (uses cache from step 1)
      3. `ContextDirectoryLoader.ensure_directory()` (context refresh)
    - Each step logs success/failure independently — one failure does not block others
    - _Bug_Condition: Quick validation calls `project_skills()` without `scan_all()` first; no context refresh on quick path_
    - _Expected_Behavior: `scan_all()` runs before `project_skills()`; context files refreshed on every startup_
    - _Preservation: Existing full-init behavior unchanged_
    - _Requirements: 1.3, 2.1, 2.3, 3.3, 3.5_

  - [x] 3.3 Change 2 (cont.) — Wire `refresh_builtin_defaults()` into `main.py` startup
    - File: `backend/main.py`, inside `lifespan()` function
    - Replace the inline `project_skills()` block on the quick validation path (lines ~218-226) with `await initialization_manager.refresh_builtin_defaults()`
    - Also call `refresh_builtin_defaults()` from `run_full_initialization()` to deduplicate the scan→project→context logic
    - _Bug_Condition: Quick validation path calls `project_skills()` without `scan_all()` and never refreshes context_
    - _Expected_Behavior: Both quick-validation and full-init paths call `refresh_builtin_defaults()`_
    - _Preservation: All other lifespan startup logic (DB init, channel gateway, config managers) unchanged_
    - _Requirements: 1.1, 1.3, 2.1, 2.3_

  - [x] 3.4 Change 3 — Fix PyInstaller datas in build script
    - File: `desktop/scripts/build-backend.sh`
    - Replace `('templates', 'templates')` with `('context', 'context')` in the PyInstaller datas section
    - Add `('skills', 'skills')` to the PyInstaller datas section
    - _Bug_Condition: `datas CONTAINS ('templates', 'templates')` → references deleted directory; `'skills' NOT IN datas` → skills not bundled_
    - _Expected_Behavior: Bundled app includes `context/` and `skills/` directories_
    - _Requirements: 1.2, 1.4, 2.2, 2.4_

  - [x] 3.5 Change 4 — PyInstaller-aware `builtin_path` in `SkillManager`
    - File: `backend/core/skill_manager.py`, method `__init__()`
    - When `builtin_path` is not explicitly provided, check `getattr(sys, 'frozen', False)`
    - If frozen: resolve to `Path(sys._MEIPASS) / "skills"`
    - If not frozen: keep existing `Path(__file__).resolve().parent.parent / "skills"`
    - _Bug_Condition: `input.is_frozen AND NOT input.builtin_skills_path.exists()` → path resolves incorrectly in PyInstaller bundle_
    - _Expected_Behavior: `builtin_path` resolves to `sys._MEIPASS / "skills"` when frozen_
    - _Preservation: Dev-mode path resolution via `__file__` unchanged (Requirement 3.5)_
    - _Requirements: 1.4, 2.4, 3.5_

  - [x] 3.6 Change 5 — Update stale `local_modules` list in build script
    - File: `desktop/scripts/build-backend.sh`
    - Replace the entire `local_modules` list with the current module structure from the design document
    - Includes all current routers, schemas, database, core, and middleware modules
    - Missing modules cause silent `ImportError` at runtime in the bundled app
    - _Requirements: 1.2, 2.2_

  - [x] 3.7 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** — Stale Built-in Context Files
    - **IMPORTANT**: Re-run the SAME test from task 1 — do NOT write a new test
    - The test from task 1 encodes the expected behavior (dest matches source for all built-in files)
    - When this test passes, it confirms the expected behavior is satisfied
    - Run: `cd backend && pytest tests/test_property_builtin_refresh_fault.py -v`
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed — `ensure_directory()` now overwrites stale built-in files)
    - _Requirements: 2.1_

  - [x] 3.8 Verify preservation tests still pass
    - **Property 2: Preservation** — User-Created Files and No-Change Idempotence
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run: `cd backend && pytest tests/test_property_builtin_refresh_preservation.py -v`
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions — user files untouched, idempotent behavior, stale symlink cleanup)
    - Confirm all preservation properties still hold after the fix

- [x] 4. Checkpoint — Ensure all tests pass
  - Run full test suite: `cd backend && pytest tests/test_property_builtin_refresh_fault.py tests/test_property_builtin_refresh_preservation.py -v`
  - Ensure all property tests pass
  - Ensure no regressions in existing tests: `cd backend && pytest --timeout=30 -x -q`
  - Ask the user if questions arise
