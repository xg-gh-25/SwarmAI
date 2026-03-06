# Bugfix Requirements Document

## Introduction

Three related bugs prevent developer-updated built-in default files from propagating to the runtime workspace (`~/.swarm-ai/SwarmWS/`) after rebuilding and restarting the app. Context files are never overwritten even when the source has changed, skill symlinks may not refresh correctly in bundled mode, and the PyInstaller build script references a deleted `templates/` directory instead of the current `context/` directory. Together these bugs break the inner development loop for anyone iterating on built-in defaults.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a developer updates a built-in context file in `backend/context/` and restarts the app THEN the system skips copying the updated file to `~/.swarm-ai/SwarmWS/.context/` because `ContextDirectoryLoader.ensure_directory()` unconditionally skips any destination file that already exists (`if dest.exists(): continue`), regardless of whether the source content has changed.

1.2 WHEN the PyInstaller-bundled app starts and attempts to locate built-in context templates THEN the system fails to find them because `build-backend.sh` packages `('templates', 'templates')` in the PyInstaller datas section, but the `backend/templates/` directory no longer exists — it was renamed to `backend/context/` during the centralized-context-directory refactor.

1.3 WHEN a developer updates skill files inside a built-in skill folder under `backend/skills/` and restarts the app in dev mode THEN the system does not re-project the symlink because `ProjectionLayer.project_skills()` compares the resolved symlink target at the folder level and finds it unchanged — the symlink already points to the correct folder, so individual file changes within that folder are invisible to the staleness check.

1.4 WHEN the PyInstaller-bundled app resolves the built-in skills path via `SkillManager.__init__()` using `Path(__file__).resolve().parent.parent / "skills"` THEN the path may not resolve correctly inside a frozen PyInstaller bundle because `__file__` points into the temporary extraction directory rather than the original source tree, and the `skills/` directory is not included in the PyInstaller datas section.

### Expected Behavior (Correct)

2.1 WHEN a developer updates a built-in context file in `backend/context/` and restarts the app THEN the system SHALL overwrite the corresponding file in `~/.swarm-ai/SwarmWS/.context/` with the latest source content, while preserving any user-created files that do not correspond to built-in defaults.

2.2 WHEN the PyInstaller-bundled app starts THEN the system SHALL locate built-in context templates from the bundled `context/` directory because the build script packages `('context', 'context')` in the PyInstaller datas section.

2.3 WHEN a developer updates skill files inside a built-in skill folder under `backend/skills/` and restarts the app THEN the system SHALL ensure the workspace reflects the latest skill content — in dev mode via symlinks (which inherently track file changes), and in bundled mode via correct path resolution.

2.4 WHEN the PyInstaller-bundled app resolves the built-in skills path THEN the system SHALL use `bundle_paths` utilities or equivalent logic to locate the bundled `skills/` directory, and the build script SHALL include `('skills', 'skills')` in the PyInstaller datas section.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a user has created custom files in `~/.swarm-ai/SwarmWS/.context/` that do not correspond to any built-in default filename THEN the system SHALL CONTINUE TO preserve those files without modification or deletion.

3.2 WHEN user-tier or plugin-tier skills exist in `~/.swarm-ai/skills/` or `~/.swarm-ai/plugin-skills/` THEN the system SHALL CONTINUE TO project them via symlinks according to the existing allowed-skills and allow-all logic.

3.3 WHEN the app starts and no built-in context files have changed since the last startup THEN the system SHALL CONTINUE TO complete initialization without errors or unnecessary file I/O beyond the staleness check.

3.4 WHEN `ProjectionLayer.project_skills()` encounters a stale symlink pointing to a skill that no longer exists THEN the system SHALL CONTINUE TO clean it up as it does today.

3.5 WHEN the app runs in development mode (not PyInstaller-bundled) THEN the system SHALL CONTINUE TO resolve built-in paths relative to the source tree as it does today.
