# Implementation Plan: Filesystem Skills Re-Architecture

## Overview

Replace the database-backed skills system with a pure filesystem-based architecture. Implementation proceeds bottom-up: data models and parsing first, then the core SkillManager, ProjectionLayer, API rewrite, migration, and finally frontend adaptation. Each step builds on the previous, with property tests validating correctness at each layer.

## Tasks

- [x] 1. Define Pydantic schemas and SKILL.md parsing utilities
  - [x] 1.1 Rewrite `backend/schemas/skill.py` with new Pydantic models
    - Replace existing DB-specific models with `SkillResponse`, `SkillCreateRequest`, `SkillUpdateRequest`
    - `SkillResponse`: `folder_name`, `name`, `description`, `version`, `source_tier`, `read_only`, `content` (optional)
    - `SkillCreateRequest`: `folder_name` (pattern-validated, max 128), `name`, `description`, `content` (max 500KB)
    - `SkillUpdateRequest`: optional `name`, `description`, `content`
    - _Requirements: 5.8, 11.1, 11.7_

  - [x] 1.2 Implement `SkillInfo` dataclass and `parse_skill_md` / `format_skill_md` / `validate_folder_name` static methods in `backend/core/skill_manager.py`
    - Create `SkillInfo` with fields: `folder_name`, `name`, `description`, `version`, `source_tier`, `path`, `content`
    - `parse_skill_md`: parse YAML frontmatter delimited by `---`, extract `name`, `description`, `version`, fall back to folder name / default description for missing fields, log warnings
    - `format_skill_md`: produce valid SKILL.md string from metadata + content
    - `validate_folder_name`: accept `^[a-zA-Z0-9][a-zA-Z0-9_-]*$`, max 128 chars, reject path separators, `..`, null bytes
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.6, 11.1, 11.2, 11.6_

  - [ ]* 1.3 Write property test: SKILL.md Round-Trip (Property 3)
    - **Property 3: SKILL.md Round-Trip**
    - Generate random valid metadata (name, description, version, content), format→parse round-trip, assert equivalence
    - **Validates: Requirements 2.1, 2.2, 2.4, 2.5**

  - [ ]* 1.4 Write property test: Malformed Frontmatter Produces Descriptive Errors (Property 4)
    - **Property 4: Malformed Frontmatter Produces Descriptive Errors**
    - Generate malformed YAML strings, verify `parse_skill_md` raises errors with file path and malformation description
    - **Validates: Requirements 2.3**

  - [ ]* 1.5 Write property test: Missing Frontmatter Fields Fall Back to Defaults (Property 5)
    - **Property 5: Missing Frontmatter Fields Fall Back to Defaults**
    - Generate SKILL.md with missing `name`/`description`, verify fallback to folder name and `"Skill: {folder_name}"`
    - **Validates: Requirements 2.6**

  - [ ]* 1.6 Write property test: Folder Name Validation (Property 11)
    - **Property 11: Folder Name Validation**
    - Generate random strings, verify validator accepts only `^[a-zA-Z0-9][a-zA-Z0-9_-]*$` (max 128), rejects path separators, `..`, null bytes
    - **Validates: Requirements 11.1, 11.2**


- [x] 2. Implement SkillManager core: three-tier scanning and cache
  - [x] 2.1 Implement `SkillManager.__init__`, `scan_all`, `get_cache`, `invalidate_cache` in `backend/core/skill_manager.py`
    - Constructor accepts optional `builtin_path` (defaults to `backend/skills/` relative to app root), configurable via `BUILTIN_SKILLS_PATH`
    - User skills path: `~/.swarm-ai/skills/` (injectable via `user_skills_path` for testing)
    - Plugin skills path: `~/.swarm-ai/plugin-skills/` (injectable via `plugin_skills_path` for testing)
    - `scan_all`: walk built-in (`backend/skills/`), user (`~/.swarm-ai/skills/`), plugin (`~/.swarm-ai/plugin-skills/`) directories; skip dirs without valid SKILL.md; apply precedence (built-in > user > plugin); log warnings for shadowed skills and invalid dirs
    - `get_cache`: return `_cache` if `_cache_valid`, else acquire `asyncio.Lock`, rescan, atomic swap `_cache = new_dict`, set `_cache_valid = True`
    - `invalidate_cache`: set `_cache_valid = False`
    - Create `~/.swarm-ai/skills/` and `~/.swarm-ai/plugin-skills/` on first launch if missing
    - Do not follow symlinks within skill directories (prevent symlink-based escape)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 6.1, 6.6, 6.7, 11.3, 11.6, 12.1, 12.4_

  - [ ]* 2.2 Write property test: Three-Tier Discovery Completeness (Property 1)
    - **Property 1: Three-Tier Discovery Completeness**
    - Generate random skill directories across 3 tiers with `tmp_path`, verify `scan_all` returns correct union with precedence, correct `source_tier` labels
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 5.8, 6.1**

  - [ ]* 2.3 Write property test: Invalid Directories Are Skipped (Property 2)
    - **Property 2: Invalid Directories Are Skipped**
    - Generate mix of valid/invalid skill directories, verify only valid ones returned, count matches
    - **Validates: Requirements 1.5**

  - [ ]* 2.4 Write property test: Path Containment (Property 12)
    - **Property 12: Path Containment**
    - Generate paths with traversal attempts (`..`, symlinks outside tiers), verify all resolved paths stay within tier directories
    - **Validates: Requirements 11.3, 11.5**

  - [ ]* 2.5 Write property test: Cache Atomicity Under Concurrent Access (Property 15)
    - **Property 15: Cache Atomicity Under Concurrent Access**
    - Run concurrent `get_cache` + `invalidate_cache` calls via `asyncio.gather`, verify every read returns a complete consistent snapshot
    - **Validates: Requirements 12.1, 12.4**

  - [ ]* 2.6 Write property test: External Change Detection on Rescan (Property 16)
    - **Property 16: External Change Detection on Rescan**
    - Modify filesystem externally (add/remove/modify skill dirs), invalidate cache, verify rescan reflects current state
    - **Validates: Requirements 12.2**

- [x] 3. Implement SkillManager CRUD operations
  - [x] 3.1 Implement `get_skill`, `create_skill`, `update_skill`, `delete_skill` in `backend/core/skill_manager.py`
    - `get_skill`: look up by folder name from cache, load content from disk on demand
    - `create_skill`: validate folder name, check no name collision across all tiers (409 if built-in match, 409 if existing user match), write SKILL.md to `~/.swarm-ai/skills/{folder_name}/`, invalidate cache
    - `update_skill`: validate skill exists and is user-tier (403 for built-in/plugin), update SKILL.md, invalidate cache
    - `delete_skill`: validate skill exists and is user-tier (403 for built-in/plugin), remove directory, invalidate cache
    - All path operations resolve to canonical form and verify containment within tier directory
    - _Requirements: 5.2, 5.3, 5.4, 5.5, 5.6, 1.7, 11.3, 11.4_

  - [ ]* 3.2 Write property test: Tier-Based Mutability (Property 9)
    - **Property 9: Tier-Based Mutability**
    - Generate skills with random tiers, verify only user-tier allows create/update/delete, built-in returns 403, plugin returns 403, `read_only` is correct
    - **Validates: Requirements 5.3, 5.4, 5.5, 5.6, 6.4, 6.5**

  - [ ]* 3.3 Write property test: Name Conflict Prevention (Property 10)
    - **Property 10: Name Conflict Prevention**
    - Generate existing skill names across tiers, verify creation fails with 409 for collisions
    - **Validates: Requirements 1.7, 10.4**

- [x] 4. Checkpoint — Core SkillManager complete
  - Ensure all tests pass, ask the user if questions arise.


- [x] 5. Implement ProjectionLayer
  - [x] 5.1 Create `backend/core/projection_layer.py` with `ProjectionLayer` class
    - `__init__`: accept `SkillManager` instance
    - `project_skills(workspace_path, allowed_skills, allow_all)`: create `SwarmWS/.claude/skills/` if missing, project symlinks for all built-in skills unconditionally + allowed user/plugin skills, clean up stale symlinks, validate symlink targets resolve within tier directories
    - `_cleanup_stale_symlinks(skills_dir, target_names)`: remove symlinks not in target set, log warnings for stale links
    - `_validate_symlink_target(target)`: verify target resolves within a known tier directory
    - Handle `OSError` when symlink target deleted between scan and access
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 11.5_

  - [ ]* 5.2 Write property test: Projection Reflects Allowed Skills (Property 6)
    - **Property 6: Projection Reflects Allowed Skills**
    - Generate skills + allowed_skills list, verify projection dir contains symlinks for all built-in + only allowed user/plugin skills; verify `allow_all=True` projects everything
    - **Validates: Requirements 3.1, 3.6, 4.2, 4.3, 6.2**

  - [ ]* 5.3 Write property test: Projection Precedence Matches Discovery (Property 7)
    - **Property 7: Projection Precedence Matches Discovery**
    - Generate name collisions across tiers, verify symlink targets point to highest-precedence tier
    - **Validates: Requirements 3.2**

- [x] 6. Rewrite Skills API router
  - [x] 6.1 Rewrite `backend/routers/skills.py` with filesystem-based endpoints
    - Register fixed-path routes (`/skills/rescan`, `/skills/generate-with-agent`) BEFORE parameterized `/{folder_name}` routes
    - `GET /skills`: return cached list sorted by `folder_name`, `content=None` omitted
    - `GET /skills/{folder_name}`: return single skill with content loaded from disk
    - `POST /skills`: create user skill, invalidate cache, trigger projection
    - `PUT /skills/{folder_name}`: update user skill, invalidate cache, trigger projection
    - `DELETE /skills/{folder_name}`: delete user skill (403 for built-in/plugin), invalidate cache, trigger projection
    - `POST /skills/rescan`: invalidate cache, return fresh list
    - Remove all DB imports and SQLAlchemy session dependencies
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10, 5.11, 5.12_

  - [ ]* 6.2 Write property test: CRUD Triggers Projection Update (Property 8)
    - **Property 8: CRUD Triggers Projection Update**
    - Perform random CRUD ops, verify projection directory reflects each change (new symlink on create, removed on delete, valid target on update)
    - **Validates: Requirements 3.4, 5.7, 10.2**

- [x] 7. Migrate built-in skills and update agent defaults
  - [x] 7.1 Create `backend/skills/` directory and migrate default skills from `desktop/resources/default-skills/`
    - Convert `desktop/resources/default-skills/DOCUMENT.md` → `backend/skills/document/SKILL.md` with proper YAML frontmatter
    - Convert `desktop/resources/default-skills/RESEARCH.md` → `backend/skills/research/SKILL.md` with proper YAML frontmatter
    - _Requirements: 6.1, 6.3, 8.4_

  - [x] 7.2 Update `backend/core/agent_defaults.py`
    - Remove `_register_default_skills` DB logic
    - Add `expand_allowed_skills_with_plugins()` function that combines explicit `allowed_skills` with plugin skill folder names via `PluginManager` mapping
    - _Requirements: 4.1, 4.6_

  - [x] 7.3 Update `backend/core/agent_manager.py` — `skill_ids` → `allowed_skills`
    - Replace all references to `skill_ids` (UUID list) with `allowed_skills` (folder name list)
    - Update agent creation/update flows to use folder names
    - Pass folder names directly to security hooks instead of resolving UUIDs
    - _Requirements: 4.1, 7.5_

- [x] 8. Update security hooks and agent sandbox manager
  - [x] 8.1 Update `backend/core/security_hooks.py`
    - Ensure `create_skill_access_checker` validates by folder name against `allowed_skills`
    - Built-in skills are always allowed regardless of `allowed_skills` list
    - _Requirements: 4.6_

  - [ ]* 8.2 Write property test: Security Hook Enforcement (Property 13)
    - **Property 13: Security Hook Enforcement**
    - Generate skill names + allowed_skills lists, verify hook grants access for built-in skills always, grants for listed skills, denies for unlisted non-built-in skills
    - **Validates: Requirements 4.6**

  - [x] 8.3 Simplify `backend/core/agent_sandbox_manager.py`
    - Remove skill symlink projection logic (now in `ProjectionLayer`)
    - Keep template responsibilities (`TEMPLATE_FILES`, `ensure_templates_in_directory`)
    - Update to read `allowed_skills` field from agent config instead of `skill_ids`
    - Delegate skill projection to `ProjectionLayer`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

- [x] 9. Checkpoint — Backend core complete
  - Ensure all tests pass, ask the user if questions arise.


- [x] 10. Database migration and seed cleanup
  - [x] 10.1 Write migration logic for `skill_ids` → `allowed_skills`
    - Read `skill_ids` (UUIDs) from each agent record
    - Join against `skills` table to resolve UUIDs → folder names
    - Write `allowed_skills` list on each agent record
    - Log warning and skip unresolvable UUIDs
    - Verify all agent records updated before dropping tables
    - If verification fails, abort without dropping tables
    - Migration is idempotent: no-op if `allowed_skills` already exists
    - Drop `skills`, `skill_versions`, `workspace_skills` tables only after successful verification
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10_

  - [ ]* 10.2 Write property test: Migration UUID Resolution (Property 14)
    - **Property 14: Migration UUID Resolution**
    - Generate UUID→folder_name mappings and agent records, verify migration produces correct `allowed_skills` lists, unresolvable UUIDs are skipped
    - **Validates: Requirements 7.6**

  - [x] 10.3 Update `backend/scripts/generate_seed_db.py`
    - Remove skill record insertion into `skills` table
    - Remove skill UUID references from default agent record
    - Set `allowed_skills` to empty list on default agent (built-in skills always available without explicit listing)
    - _Requirements: 8.1, 8.2, 8.3_

- [x] 11. Wire initialization and dependency injection
  - [x] 11.1 Update `backend/core/initialization_manager.py`
    - Create `SkillManager` singleton during `run_full_initialization`
    - Create `ProjectionLayer` singleton, injecting `SkillManager`
    - Register both as dependencies for FastAPI injection
    - Trigger initial `scan_all` and `project_skills` on startup
    - _Requirements: 1.2, 3.1_

  - [x] 11.2 Remove `backend/core/local_skill_manager.py`
    - Delete the file (functionality merged into new `SkillManager`)
    - Remove all imports of `LocalSkillManager` across the codebase
    - _Requirements: 7.1_

  - [x] 11.3 Update skill generation flow in Skills API
    - Update `/skills/generate-with-agent` to write generated SKILL.md directly to `~/.swarm-ai/skills/{skill-name}/`
    - Remove finalize endpoint (no DB registration needed)
    - Invalidate cache and trigger projection after generation completes
    - Return 409 if target directory already exists
    - _Requirements: 10.1, 10.2, 10.3, 10.4_

- [x] 12. Checkpoint — Backend migration and wiring complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 13. Frontend adaptation
  - [x] 13.1 Update `desktop/src/services/skills.ts`
    - Update `toCamelCase()` to map new response fields: `folder_name` → `folderName`, `source_tier` → `sourceTier`, `read_only` → `readOnly`
    - Change API methods from UUID-based to folder-name-based: `get(folderName)`, `delete(folderName)`, `update(folderName, ...)`
    - Replace `refresh()` with `rescan()` calling `POST /skills/rescan`
    - Remove `finalize()` method
    - Update `Skill` interface: `folderName` (primary ID), `name`, `description`, `version`, `sourceTier`, `readOnly`, `content`
    - _Requirements: 9.1, 9.7_

  - [ ]* 13.2 Write property test: Frontend Field Mapping Round-Trip (Property 17)
    - **Property 17: Frontend Field Mapping Round-Trip**
    - Generate random backend `SkillResponse` objects (snake_case), apply `toCamelCase()`, verify every field correctly mapped, no fields lost
    - Use `fast-check` library in vitest
    - **Validates: Requirements 9.7**

  - [x] 13.3 Update `desktop/src/pages/SkillsPage.tsx`
    - Display `sourceTier` (built-in, user, plugin) for each skill
    - Disable edit and delete actions for built-in and plugin skills (based on `readOnly`)
    - Replace "Refresh/Sync" button with "Rescan" button calling `rescan()`
    - _Requirements: 9.2, 9.3, 9.4_

  - [x] 13.4 Update `desktop/src/components/workspace-settings/SkillsTab.tsx`
    - Replace UUID-based skill references with folder name identifiers
    - Update skill selection/display to use `folderName` as key
    - _Requirements: 9.1_

- [x] 14. Final checkpoint — All tests pass, integration verified
  - Ensure all backend tests pass: `cd backend && pytest`
  - Ensure all frontend tests pass: `cd desktop && npm test -- --run`
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document (Properties 1–17)
- Backend uses Python/FastAPI with `snake_case`; frontend uses TypeScript/React with `camelCase`
- All new/modified files must include module-level docstrings per project standards
- Use `fsWrite` + `fsAppend` for file creation, never heredoc
