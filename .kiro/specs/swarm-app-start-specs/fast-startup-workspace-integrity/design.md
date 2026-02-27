<!-- PE-REVIEWED -->
# Fast Startup Workspace Integrity Bugfix Design

## Overview

The fast startup path in `backend/main.py` `lifespan()` skips workspace filesystem verification entirely when `data.db` exists. The `ensure_default_workspace()` call (which invokes `verify_integrity()`) only runs inside `run_full_initialization()`, which is never reached on the fast path. This means returning users and seed-sourced first launches never get missing SwarmWS folders or system files recreated. The fix adds a lightweight `ensure_default_workspace()` call to the fast startup path, right after `initialize_database(skip_schema=True)`, so that `verify_integrity()` heals any missing items on every startup.

## Glossary

- **Bug_Condition (C)**: The app starts via the fast path (`_ensure_database_initialized()` returns `True`) and `verify_integrity()` is never called, leaving missing workspace filesystem items unrepaired.
- **Property (P)**: On every startup — fast or full — the workspace filesystem is verified and any missing system-managed folders/files are recreated.
- **Preservation**: The fast path must remain fast. Schema DDL, migrations, agent/skill/MCP registration must continue to be skipped. Existing user files must not be overwritten. The full initialization path must remain unchanged.
- **`lifespan()`**: The FastAPI lifespan handler in `backend/main.py` that orchestrates startup. Contains the fast path vs full path branch.
- **`ensure_default_workspace(db)`**: Method on `SwarmWorkspaceManager` that either creates the workspace config + folder structure (first time) or calls `verify_integrity()` (subsequent times). Already idempotent.
- **`verify_integrity(workspace_path)`**: Method that checks all system-managed folders, root files, section context files, and per-project system items, recreating only what's missing. Does NOT overwrite existing files.
- **`_cached_workspace_path`**: In-memory cache on `InitializationManager` storing the expanded workspace path so downstream code avoids repeated DB lookups.

## Bug Details

### Fault Condition

The bug manifests when the app starts via the fast path (`_ensure_database_initialized()` returns `True`). In this branch, `lifespan()` calls `initialize_database(skip_schema=True)` and then starts the channel gateway — but never calls `ensure_default_workspace()` or `verify_integrity()`. Any missing system-managed folders or files remain missing forever, since every subsequent startup also takes the fast path.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type StartupContext
  OUTPUT: boolean
  
  RETURN input.data_db_exists == True
         AND input.startup_path == "fast"
         AND workspace_has_missing_system_items(input.workspace_path)
         AND verify_integrity_was_NOT_called()
END FUNCTION
```

### Examples

- **Returning user, missing folder**: User has `data.db`, deletes `~/.swarm-ai/SwarmWS/Signals/` folder, restarts app → folder is NOT recreated, workspace tree shows incomplete structure.
- **Seed-sourced first launch, no filesystem**: `seed.db` is copied to `data.db`, app starts fast path → SwarmWS folder structure is never created at all, workspace explorer is empty.
- **Returning user, missing system file**: User has `data.db`, `system-prompts.md` is deleted, restarts app → file is NOT recreated, agent context loading may fail.
- **Non-bug case**: User has no `data.db` and no `seed.db` (dev mode) → full init path runs, `ensure_default_workspace()` is called, everything works correctly.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- The full initialization path (dev-mode fallback) must continue to run `run_full_initialization()` including schema DDL, migrations, agent/skill/MCP registration, and workspace creation.
- The fast path must continue to skip schema DDL, migrations, and agent/skill/MCP registration.
- `verify_integrity()` must continue to be idempotent — it only creates missing items, never overwrites existing user files.
- The atomic `seed.db` copy with WAL mode and busy_timeout pragmas must remain unchanged.
- Mouse/keyboard interactions, API endpoints, and all frontend behavior are unaffected.

**Scope:**
All inputs that do NOT involve the fast startup path are completely unaffected by this fix. This includes:
- Dev-mode startup (no `data.db`, no `seed.db`)
- Runtime API calls after startup
- Frontend workspace operations
- Agent/skill/MCP registration flows

## Hypothesized Root Cause

Based on the bug description and code analysis, the root cause is straightforward:

1. **Missing `ensure_default_workspace()` call on fast path**: The `lifespan()` function has two branches. The full init branch calls `run_full_initialization()` which internally calls `ensure_default_workspace(db)`. The fast path branch only calls `initialize_database(skip_schema=True)` and starts the channel gateway — it never touches the workspace filesystem at all.

2. **No workspace path caching on fast path**: The `initialization_manager._cached_workspace_path` is only set during `run_full_initialization()`. On the fast path it remains `None`, forcing `get_cached_workspace_path()` to fall back to computing from `DEFAULT_WORKSPACE_CONFIG` every time it's called.

3. **Permanent fast-path lock-in**: Once `data.db` exists (either from a previous full init or from seed copy), `_ensure_database_initialized()` always returns `True`, so the fast path is taken on every subsequent startup. The workspace filesystem is never verified again after the initial full init.

## Correctness Properties

Property 1: Fault Condition - Workspace Integrity on Fast Startup

_For any_ startup where `data.db` exists (fast path is taken) and the workspace has missing system-managed folders or files, the fixed `lifespan()` function SHALL call `ensure_default_workspace()` which invokes `verify_integrity()` to recreate all missing system-managed items before the app becomes ready to serve requests.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5**

Property 2: Preservation - Fast Path Performance

_For any_ startup where `data.db` exists (fast path is taken), the fixed code SHALL NOT run schema DDL, migrations, or agent/skill/MCP registration, preserving the fast startup performance characteristics. The only addition is the lightweight, idempotent `ensure_default_workspace()` call.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

Property 3: Fault Condition - Seed DB Without Workspace Config

_For any_ startup where `data.db` exists via seed copy but the seed DB contains no `workspace_config` row, the fixed `lifespan()` function SHALL call `ensure_default_workspace()` which will insert the default workspace config row, create the full folder structure on disk, and populate sample data — producing a complete workspace before the app becomes ready.

**Validates: Requirements 2.2, 2.5**

Property 4: Graceful Degradation on Workspace Integrity Failure

_For any_ startup where `data.db` exists (fast path is taken) and `ensure_default_workspace()` raises an exception, the app SHALL log the error, continue startup, and become ready to serve requests. The workspace may be incomplete, but the app does not crash. The workspace will be healed on the next restart or when the user triggers a reset.

**Validates: Requirements 3.2 (fast path remains non-blocking)**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `backend/main.py`

**Function**: `lifespan()`

**Specific Changes**:

1. **Add workspace integrity check to fast path**: After `initialize_database(skip_schema=True)`, import and call `ensure_default_workspace(db)` on the `swarm_workspace_manager` singleton. This is the same call that `run_full_initialization()` makes internally, so behavior is identical.

2. **Cache the workspace path on fast path**: After `ensure_default_workspace()` returns the workspace config dict, expand the `file_path` and store it via `initialization_manager.set_cached_workspace_path()`. This mirrors what `run_full_initialization()` does, ensuring `get_cached_workspace_path()` returns immediately without fallback computation.

3. **Add public setter to `InitializationManager`**: Add a `set_cached_workspace_path(path: str)` method to avoid direct access to the private `_cached_workspace_path` attribute from `main.py`. Update `run_full_initialization()` to use this setter internally as well.

4. **Add error handling**: Wrap the new call in a try/except. If `ensure_default_workspace()` fails, log an error but do NOT block startup — the app can still serve requests, and the workspace will be healed on the next restart or when the user triggers a reset.

**Note on migration safety**: The fast path skips schema DDL and migrations via `initialize_database(skip_schema=True)`. This is safe because the fast path only executes when `data.db` already exists — either from a previous full init (where migrations already ran) or from a pre-migrated `seed.db`. The `ensure_default_workspace()` call does not depend on migrations.

**Note on seed DB without workspace_config row**: If the seed DB does not contain a `workspace_config` row, `ensure_default_workspace()` will create one from scratch (insert DB row + create full folder structure + populate sample data). This is the correct first-time creation behavior and is handled transparently.

**Pseudocode for the fast path change:**
```python
if skip_init_pipeline:
    # Fast startup path — seed-sourced or returning user.
    logger.info("Fast startup — skipping schema DDL, migrations, and full init")
    await initialize_database(skip_schema=True)
    logger.info("Database instance created (schema skipped)")

    # NEW: Ensure workspace filesystem integrity on fast path
    # Also setup skill symlinks and templates — these are lightweight,
    # idempotent operations that mirror what run_full_initialization() does.
    # Without them, a seed-sourced first launch would have no skills or
    # templates until a full init runs (PE Fix #6).
    try:
        from pathlib import Path
        from core.swarm_workspace_manager import swarm_workspace_manager
        from core.agent_sandbox_manager import agent_sandbox_manager
        from database import db
        workspace = await swarm_workspace_manager.ensure_default_workspace(db)
        workspace_path = swarm_workspace_manager.expand_path(workspace["file_path"])
        initialization_manager.set_cached_workspace_path(workspace_path)
        logger.info("Workspace integrity verified on fast path")

        # Skill symlinks and templates (non-fatal if they fail)
        try:
            await agent_sandbox_manager.setup_workspace_skills(Path(workspace_path))
            agent_sandbox_manager.ensure_templates_in_directory(Path(workspace_path))
            logger.info("Skill symlinks and templates ensured on fast path")
        except Exception as e:
            logger.warning("Skill/template setup failed on fast path (non-fatal): %s", e)
    except Exception as e:
        logger.error("Workspace integrity check failed on fast path: %s", e)
```

**Note on skill/template setup (PE Fix #6):** The fast path must also run `setup_workspace_skills()` and `ensure_templates_in_directory()` to mirror `run_full_initialization()`. Without these, a seed-sourced first launch would have the folder structure but no skill symlinks or templates. Both operations are idempotent and lightweight — `setup_workspace_skills()` reconciles symlinks against the DB skill set, and `ensure_templates_in_directory()` only writes missing files. They are wrapped in a separate inner try/except so their failure does not block startup or prevent workspace integrity from being cached.

**Supporting change — public setter on `InitializationManager`**: Add a `set_cached_workspace_path(path: str)` method to `InitializationManager` to avoid direct access to the private `_cached_workspace_path` attribute from `main.py`. The existing `run_full_initialization()` should also be updated to use this setter internally.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Fault Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that mock the fast startup path (where `_ensure_database_initialized()` returns `True`) and verify whether `ensure_default_workspace()` or `verify_integrity()` is called. Run these tests on the UNFIXED code to observe failures.

**Test Cases**:
1. **Fast path skips workspace verification**: Mock `_ensure_database_initialized()` returning `True`, run `lifespan()`, assert `ensure_default_workspace()` was called (will fail on unfixed code)
2. **Missing folder not recreated on fast path**: Set up a workspace with a missing `Signals/` folder, run fast path startup, check if folder exists after startup (will fail on unfixed code)
3. **Missing system file not recreated on fast path**: Set up a workspace with missing `system-prompts.md`, run fast path startup, check if file exists after startup (will fail on unfixed code)
4. **Workspace path not cached on fast path**: Run fast path startup, check `initialization_manager.get_cached_workspace_path()` returns the correct path without fallback computation (will fail on unfixed code — returns fallback-computed path, `_cached_workspace_path` is `None`)

**Expected Counterexamples**:
- `ensure_default_workspace()` is never called during fast path startup
- `verify_integrity()` is never invoked, so missing folders/files persist
- `_cached_workspace_path` remains `None` after fast path startup

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := lifespan_fixed(input)
  ASSERT ensure_default_workspace_was_called(result)
  ASSERT all_system_managed_items_exist(result.workspace_path)
  ASSERT cached_workspace_path_is_set(result)
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT lifespan_original(input) = lifespan_fixed(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain
- It catches edge cases that manual unit tests might miss
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: Observe behavior on UNFIXED code first for the full initialization path and verify it remains identical after the fix.

**Test Cases**:
1. **Full init path unchanged**: Verify that when `_ensure_database_initialized()` returns `False`, the full init pipeline (schema DDL, migrations, `run_full_initialization()`) still runs exactly as before
2. **Existing files not overwritten**: Create a workspace with user-modified `system-prompts.md`, run fast path with fix, verify file content is preserved
3. **Idempotent on complete workspace**: Run fast path with fix on a workspace that has all system items present, verify no items are recreated (empty recreated list)
4. **Seed copy behavior unchanged**: Verify the atomic seed.db copy with WAL mode and busy_timeout pragmas still works identically

### Unit Tests

- Test that `lifespan()` fast path calls `ensure_default_workspace()` after `initialize_database(skip_schema=True)`
- Test that `lifespan()` fast path caches the workspace path in `initialization_manager._cached_workspace_path`
- Test that `lifespan()` fast path handles `ensure_default_workspace()` failure gracefully (logs error, does not crash)
- Test that `lifespan()` full init path is unchanged

### Property-Based Tests

- Generate random subsets of system-managed folders/files to delete, run fast path startup, verify all are recreated
- Generate random workspace states (complete, partially missing, fully missing), verify `verify_integrity()` always produces a complete workspace
- Generate random non-system files in the workspace, verify they are never touched by `verify_integrity()`

### Integration Tests

- Test full app startup with fast path and verify workspace tree API returns complete structure
- Test app startup with seed.db copy followed by fast path restart, verify workspace is created
- Test app startup with deliberately corrupted workspace (missing folders), verify self-healing on restart
