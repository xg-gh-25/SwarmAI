<!-- PE-REVIEWED -->
# Built-in Defaults Refresh Bugfix Design

## Overview

Built-in context files and skills don't refresh in the runtime workspace after rebuild/restart. The fix is simple: always overwrite built-in files on startup, and ensure the refresh runs on every startup (not just first run). No checksums, no hash comparison. Built-in files are developer-managed defaults — they should always reflect the latest source. User-created files live in separate locations and are never touched.

## Glossary

- **`ContextDirectoryLoader.ensure_directory()`**: Method in `backend/core/context_directory_loader.py` that copies built-in context templates to `~/.swarm-ai/SwarmWS/.context/`. Currently skips any file that already exists.
- **`SkillManager.builtin_path`**: Resolves to `backend/skills/` via `Path(__file__).resolve().parent.parent / "skills"`. Breaks inside PyInstaller bundles.
- **`ProjectionLayer.project_skills()`**: Creates symlinks from `SwarmWS/.claude/skills/` to skill source directories. Claude SDK discovers skills via these symlinks (`setting_sources=["project"]` → scans `{cwd}/.claude/skills/`).
- **Quick validation path**: On subsequent startups, `main.py` takes `run_quick_validation()` which only checks DB records exist. It already calls `project_skills()` but does NOT call `scan_all()` first, so the skill cache is stale. It also does NOT re-copy context files.

## Bug Details

### Fault Condition

```
FUNCTION isBugCondition(input)
  // Bug 1: Quick validation calls project_skills() WITHOUT scan_all() first,
  //         so skill cache is stale and new built-in skills are not projected
  skill_cache_stale := input.initialization_complete == True
                       AND input.startup_path == "quick_validation"
                       AND NOT input.scan_all_called_before_projection

  // Bug 2: Quick validation does not refresh context files at all
  context_not_refreshed := input.initialization_complete == True
                           AND input.startup_path == "quick_validation"

  // Bug 3: ensure_directory() skips existing built-in files
  context_stale := input.dest_file EXISTS AND input.file_is_builtin

  // Bug 4: PyInstaller datas references deleted templates/ directory
  build_wrong := input.pyinstaller_datas CONTAINS ('templates', 'templates')

  // Bug 5: Skills not bundled in PyInstaller
  skills_missing := input.is_frozen AND 'skills' NOT IN input.pyinstaller_datas

  // Bug 6: Frozen bundle resolves builtin_path via __file__
  path_wrong := input.is_frozen AND NOT input.builtin_skills_path.exists()

  RETURN skill_cache_stale OR context_not_refreshed OR context_stale
         OR build_wrong OR skills_missing OR path_wrong
END FUNCTION
```

### Skill Discovery Chain (critical path)

For built-in skills to be available to the model during chat, the full chain must work:

1. `SkillManager.scan_all()` scans `backend/skills/` (built-in tier) → populates cache
2. `ProjectionLayer.project_skills()` creates symlinks: `SwarmWS/.claude/skills/{name}` → `backend/skills/{name}`
3. Claude SDK reads `setting_sources=["project"]` → scans `{cwd}/.claude/skills/` → discovers skills via symlinks
4. Model sees skills in system prompt and can invoke them

**Critical ordering**: `scan_all()` MUST run before `project_skills()`. The quick validation path in `main.py` currently calls `project_skills()` but NOT `scan_all()`, so the skill cache is empty/stale and new built-in skills are never projected.

### Examples

- Developer adds new skill `s_code-review/` to `backend/skills/`, restarts app → skill not discovered because `scan_all()` is not called on quick validation path, so cache is stale and `project_skills()` doesn't know about the new skill
- Developer edits `backend/context/IDENTITY.md`, restarts app → old content persists because `ensure_directory()` skips existing files
- PyInstaller build fails because `backend/templates/` no longer exists

## Hypothesized Root Cause

1. **Quick validation calls `project_skills()` without `scan_all()` first**: `main.py` lines 210-218 already call `project_skills()` on the quick path, but the `SkillManager` cache is empty/stale because `scan_all()` was never called. Projection uses stale data, so new built-in skills are invisible.
2. **Quick validation does not refresh context files**: No call to `ensure_directory()` on the quick path.
3. `ensure_directory()` has `if dest.exists(): continue` — skips ALL existing files unconditionally
4. `build-backend.sh` references deleted `('templates', 'templates')` instead of `('context', 'context')`
5. `('skills', 'skills')` missing from PyInstaller datas section
6. `SkillManager.__init__()` uses `Path(__file__)`-relative resolution that breaks in frozen bundles

## Fix Implementation

### Change 1: Always overwrite built-in context files

**File**: `backend/core/context_directory_loader.py`
**Method**: `ensure_directory()`

Remove the `if dest.exists(): continue` guard. Always copy from source, overwriting existing files. User-created files are safe because we only iterate over source filenames.

Also copy ALL files from `templates_dir` (not just those in `CONTEXT_FILES` + cache filenames). This ensures files like `USER.example.md` that exist in `backend/context/` but are not in the `CONTEXT_FILES` list are also refreshed.

```python
# BEFORE (broken):
files_to_copy = [spec.filename for spec in CONTEXT_FILES]
files_to_copy.append(L0_CACHE_FILENAME)
files_to_copy.append(L1_CACHE_FILENAME)
for filename in files_to_copy:
    dest = self.context_dir / filename
    if dest.exists():
        continue  # <-- Bug: skips existing files
    ...

# AFTER (fixed):
# Copy ALL files from templates_dir, overwriting only when content differs
for src in self.templates_dir.iterdir():
    if not src.is_file():
        continue
    dest = self.context_dir / src.name
    try:
        src_bytes = src.read_bytes()
        # Skip write if content is identical (avoids unnecessary I/O on most startups)
        if dest.exists() and dest.read_bytes() == src_bytes:
            continue
        dest.write_bytes(src_bytes)
    except OSError as exc:
        logger.warning("Failed to copy %s → %s: %s", src, dest, exc)
```

**Performance note**: On a typical startup where nothing has changed, this reads ~12 small markdown files (~50KB total) and compares bytes in memory. No writes occur. The read overhead is negligible compared to the DB init and skill scan that already happen on every startup. Only when content actually differs does a write happen.

### Change 2: Run built-in refresh on EVERY startup

**File**: `backend/core/initialization_manager.py`
**New method**: `refresh_builtin_defaults()`

Extract the skill scan + projection + context refresh logic into a dedicated method. Each step is wrapped in its own try/except so one failure doesn't block the others.

```python
async def refresh_builtin_defaults(self) -> None:
    """Refresh built-in skills and context files.

    Called on every startup (both full init and quick validation).
    Each step is independent — failure in one does not block others.
    """
    workspace_path = self.get_cached_workspace_path()

    # Step 1: Re-scan skills (MUST run before projection)
    try:
        from core.skill_manager import skill_manager as _sm
        await _sm.scan_all()
        logger.info("SkillManager scan completed")
    except Exception as e:
        logger.error("Skill scan failed (non-fatal): %s", e)

    # Step 2: Re-project skill symlinks (uses cache from step 1)
    try:
        from core.projection_layer import ProjectionLayer
        from core.skill_manager import skill_manager as _sm
        _projection = ProjectionLayer(_sm)
        await _projection.project_skills(Path(workspace_path), allow_all=True)
        logger.info("Skill symlinks projected")
    except Exception as e:
        logger.error("Skill projection failed (non-fatal): %s", e)

    # Step 3: Refresh built-in context files
    try:
        from core.context_directory_loader import ContextDirectoryLoader
        loader = ContextDirectoryLoader(
            context_dir=Path(workspace_path) / ".context",
            templates_dir=Path(__file__).resolve().parent.parent / "context",
        )
        loader.ensure_directory()
        logger.info("Context directory refreshed")
    except Exception as e:
        logger.error("Context refresh failed (non-fatal): %s", e)
```

**File**: `backend/main.py` (lifespan startup)

Replace the existing inline `project_skills()` call on the quick validation path with `refresh_builtin_defaults()`:

```python
# BEFORE (in main.py, after quick validation passes):
try:
    from core.projection_layer import ProjectionLayer
    from core.skill_manager import skill_manager as _sm
    workspace_path = initialization_manager.get_cached_workspace_path()
    _projection = ProjectionLayer(_sm)
    await _projection.project_skills(Path(workspace_path), allow_all=True)
except Exception as e:
    logger.error("Failed to project skills during quick validation: %s", e)

# AFTER:
await initialization_manager.refresh_builtin_defaults()
```

Also call `refresh_builtin_defaults()` from `run_full_initialization()` to deduplicate the logic.

### Change 3: Fix PyInstaller build script

**File**: `desktop/scripts/build-backend.sh`

```python
# BEFORE:
datas += [('templates', 'templates')]

# AFTER:
datas += [('context', 'context')]
datas += [('skills', 'skills')]
```

### Change 4: PyInstaller-aware path resolution for SkillManager

**File**: `backend/core/skill_manager.py` — `SkillManager.__init__()`

Note: `bundle_paths.py` handles Tauri bundle resource paths (Contents/Resources/_up_/resources/) — these are resources bundled by Tauri's build system and placed in the macOS .app bundle structure. PyInstaller-bundled data files use `sys._MEIPASS` which points to a temporary extraction directory where PyInstaller unpacks its frozen data. These are two separate bundling mechanisms that coexist in the final app: Tauri bundles the outer shell + resources, PyInstaller bundles the Python sidecar + its data files. Do not use `bundle_paths.py` for PyInstaller data, and do not use `sys._MEIPASS` for Tauri resources.

```python
import sys
if getattr(sys, 'frozen', False):
    self.builtin_path = Path(sys._MEIPASS) / "skills"
else:
    self.builtin_path = Path(__file__).resolve().parent.parent / "skills"
```

Note: `ContextDirectoryLoader` does NOT need a `sys._MEIPASS` fallback because `initialization_manager.py` always passes `templates_dir` explicitly. The `_MEIPASS` fix is only needed in `SkillManager` where `builtin_path` defaults from `__file__`.

### Change 5: Update stale local_modules in build script

**File**: `desktop/scripts/build-backend.sh`

Replace the entire `local_modules` list with the current module structure. The existing list is stale — it references deleted modules and misses many current ones. Missing modules cause silent `ImportError` at runtime in the bundled app.

```python
local_modules = [
    # Main entry
    'main',
    'config',
    # Routers
    'routers',
    'routers.agents',
    'routers.auth',
    'routers.autonomous_jobs',
    'routers.channels',
    'routers.chat',
    'routers.dev',
    'routers.mcp',
    'routers.plugins',
    'routers.projects',
    'routers.search',
    'routers.settings',
    'routers.skills',
    'routers.system',
    'routers.tasks',
    'routers.todos',
    'routers.tscc',
    'routers.workspace',
    'routers.workspace_api',
    'routers.workspace_config',
    # Schemas
    'schemas',
    'schemas.agent',
    'schemas.auth',
    'schemas.autonomous_job',
    'schemas.channel',
    'schemas.chat_thread',
    'schemas.context',
    'schemas.error',
    'schemas.marketplace',
    'schemas.mcp',
    'schemas.message',
    'schemas.permission',
    'schemas.project',
    'schemas.search',
    'schemas.settings',
    'schemas.skill',
    'schemas.task',
    'schemas.todo',
    'schemas.tscc',
    'schemas.workspace',
    'schemas.workspace_config',
    # Database
    'database',
    'database.base',
    'database.sqlite',
    # Core
    'core',
    'core.agent_defaults',
    'core.agent_manager',
    'core.app_config_manager',
    'core.audit_manager',
    'core.auth',
    'core.chat_thread_manager',
    'core.claude_environment',
    'core.cmd_permission_manager',
    'core.content_accumulator',
    'core.context_directory_loader',
    'core.credential_validator',
    'core.exceptions',
    'core.initialization_manager',
    'core.permission_manager',
    'core.plugin_manager',
    'core.project_schema_migrations',
    'core.projection_layer',
    'core.search_manager',
    'core.security_hooks',
    'core.session_manager',
    'core.skill_manager',
    'core.skill_migration',
    'core.swarm_workspace_manager',
    'core.system_prompt',
    'core.task_manager',
    'core.todo_manager',
    'core.tool_summarizer',
    'core.tscc_state_manager',
    # Middleware
    'middleware',
    'middleware.auth',
    'middleware.error_handler',
    'middleware.rate_limit',
]
```

## Correctness Properties

Property 1: Built-in context files always match source on startup

_For any_ app startup (first run or subsequent), every file in `templates_dir` SHALL be copied to `context_dir`, overwriting any existing content. Files in `context_dir` that do not exist in `templates_dir` SHALL remain untouched.

**Validates: Requirements 2.1, 3.1**

Property 2: User-created files are never modified

_For any_ app startup, files in `context_dir` whose filenames do NOT exist in `templates_dir` SHALL remain untouched — no modification, no deletion.

**Validates: Requirements 3.1**

Property 3: Built-in skills are always discoverable by the model

_For any_ app startup (first run or subsequent), `SkillManager.scan_all()` SHALL run BEFORE `ProjectionLayer.project_skills()`, and `project_skills()` SHALL create symlinks for all built-in skills in `SwarmWS/.claude/skills/`, ensuring the Claude SDK can discover them.

**Validates: Requirements 2.3**

Property 4: Bundled app resolves built-in paths correctly

_For any_ PyInstaller-bundled startup, `SkillManager.builtin_path` SHALL resolve to `sys._MEIPASS / "skills"` and the build script SHALL bundle both `context/` and `skills/` directories.

**Validates: Requirements 2.2, 2.4**

Property 5: Refresh steps are independent

_For any_ app startup, failure in one refresh step (scan, projection, or context copy) SHALL NOT prevent the other steps from executing.

**Validates: Requirements 3.3**

## Testing Strategy

### Unit Tests

- `ensure_directory()` overwrites existing built-in files with source content
- `ensure_directory()` copies ALL files from templates_dir (including USER.example.md)
- `ensure_directory()` does not delete or modify user-created files
- `refresh_builtin_defaults()` calls scan_all BEFORE project_skills, then ensure_directory
- `refresh_builtin_defaults()` continues if scan_all fails (project_skills and ensure_directory still run)
- `SkillManager.__init__()` resolves `builtin_path` via `sys._MEIPASS` when frozen
- `SkillManager.__init__()` resolves `builtin_path` via `__file__` when not frozen

### Property-Based Tests

- Generate random sets of filenames (mix of built-in and user-created) with random content → verify only built-in files are overwritten, user files untouched
- Generate random source/dest content pairs → verify dest always matches source after `ensure_directory()`
