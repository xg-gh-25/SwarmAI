# Bugfix Requirements Document

## Introduction

The fast startup path introduced by the `appstart-db-init-hang` bugfix skips workspace filesystem verification entirely. When `data.db` already exists (returning user or seed-sourced), `_ensure_database_initialized()` returns `True` and the `lifespan()` function takes the fast path, which calls `initialize_database(skip_schema=True)` and starts the channel gateway — but never invokes `run_full_initialization()` or `run_quick_validation()`. This means `ensure_default_workspace()` is never called, so `verify_integrity()` never runs. The expected SwarmWS folder structure (Signals, Plan, Execute, Communicate, Reflection, Artifacts, Notebooks, Projects, Knowledge/Memory) and system files (system-prompts.md, context-L0.md, context-L1.md, section-level context files) are never created or repaired on the fast path.

Once `initialization_complete` is set in the DB and `data.db` exists, every subsequent startup takes the fast path forever, meaning the workspace filesystem is never healed.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN `data.db` exists (returning user) AND the app starts up THEN the system takes the fast startup path and never calls `ensure_default_workspace()` or `verify_integrity()`, leaving missing workspace folders and files unrepaired

1.2 WHEN `data.db` is copied from `seed.db` (first launch with seed) AND the app starts up THEN the system takes the fast startup path and never calls `ensure_default_workspace()` or `verify_integrity()`, so the workspace filesystem is never created or verified

1.3 WHEN any system-managed folder (Signals, Plan, Execute, Communicate, Reflection, Artifacts, Notebooks, Projects, Knowledge/Memory) is manually deleted or missing AND the app restarts via the fast path THEN the system does not recreate the missing folder

1.4 WHEN any system-managed root file (system-prompts.md, context-L0.md, context-L1.md) or section-level context file is missing AND the app restarts via the fast path THEN the system does not recreate the missing file

1.5 WHEN the workspace explorer loads after a fast-path startup with missing filesystem items THEN the user sees an incomplete or empty workspace tree

### Expected Behavior (Correct)

2.1 WHEN `data.db` exists (returning user) AND the app starts up via the fast path THEN the system SHALL call `verify_integrity()` on the workspace filesystem to recreate any missing system-managed folders and files

2.2 WHEN `data.db` is copied from `seed.db` (first launch with seed) AND the app starts up via the fast path THEN the system SHALL call `verify_integrity()` on the workspace filesystem to ensure the full folder structure exists

2.3 WHEN any system-managed folder is missing AND the app restarts via the fast path THEN the system SHALL recreate the missing folder without affecting existing user data

2.4 WHEN any system-managed root file or section-level context file is missing AND the app restarts via the fast path THEN the system SHALL recreate the missing file with its default template content without overwriting existing files

2.5 WHEN the workspace explorer loads after a fast-path startup THEN the user SHALL see the complete workspace tree including all system-managed folders and files

2.6 WHEN `data.db` exists via seed copy but the seed DB contains no `workspace_config` row AND the app starts up via the fast path THEN the system SHALL create the default workspace config row, full folder structure, and sample data before the app becomes ready to serve requests

### Unchanged Behavior (Regression Prevention)

3.1 WHEN `data.db` does not exist AND no `seed.db` is available (dev-mode fallback) THEN the system SHALL CONTINUE TO run the full initialization pipeline including schema DDL, migrations, agent/skill/MCP registration, and workspace creation

3.2 WHEN the fast startup path is taken THEN the system SHALL CONTINUE TO skip schema DDL, migrations, and full agent/skill/MCP registration to maintain fast startup performance

3.3 WHEN `verify_integrity()` runs on the fast path THEN the system SHALL CONTINUE TO preserve all existing user-created files and folders without overwriting them (idempotent behavior)

3.4 WHEN the full initialization path is taken (dev-mode or first-time without seed) THEN the system SHALL CONTINUE TO call `run_full_initialization()` which includes `ensure_default_workspace()` and `verify_integrity()`

3.5 WHEN `data.db` is copied from `seed.db` THEN the system SHALL CONTINUE TO perform the atomic copy with WAL mode and busy_timeout pragmas

### PE Review Additions

2.7 WHEN the fast startup path is taken THEN the system SHALL also run skill symlink setup (`setup_workspace_skills()`) and template copying (`ensure_templates_in_directory()`) to ensure the workspace has all skills and templates, mirroring the full initialization path

2.8 WHEN `ensure_default_workspace()` raises an exception on the fast startup path THEN the system SHALL log the error and continue startup without crashing — the workspace may be incomplete but the app remains functional

2.9 WHEN skill symlink setup or template copying fails on the fast startup path THEN the system SHALL log a warning and continue startup — these are non-fatal operations that do not block the app

3.6 WHEN the fast startup path runs skill symlink setup and template copying THEN the system SHALL CONTINUE TO skip schema DDL, migrations, and full agent/MCP registration — only lightweight idempotent workspace operations are added
