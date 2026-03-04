# Requirements Document

## Introduction

This specification defines the re-architecture of the SwarmAI skills system from a database-backed model to a pure filesystem-based approach. The current system stores skill metadata in SQLite, syncs between filesystem and DB, and references skills by DB UUIDs. The new system eliminates the database layer entirely, using a three-tier filesystem hierarchy as the single source of truth, with a symlink projection layer that merges all sources into the Claude SDK's discovery directory.

## Glossary

- **Skill_Directory**: A filesystem directory containing a `SKILL.md` file and optional supporting files, identified by its folder name (kebab-case)
- **SKILL.md**: The canonical skill definition file containing YAML frontmatter (name, description, version) followed by markdown skill content
- **Built_In_Skills**: Skills that ship with the application, stored in `backend/skills/` and version-controlled in the repository
- **User_Skills**: Skills created by the user, stored in `~/.swarm-ai/skills/`
- **Plugin_Skills**: Skills installed via plugins or marketplace, stored in `~/.swarm-ai/plugin-skills/` and managed by the SwarmAI plugin system (read-only to users)
- **Projection_Layer**: The symlink-based mechanism that merges skills from all three tiers into `SwarmWS/.claude/skills/` for Claude SDK discovery
- **Skill_Manager**: The backend component responsible for filesystem-based skill discovery, CRUD operations, and metadata extraction
- **Agent_Sandbox_Manager**: The backend component responsible for setting up per-agent workspace skill projections via symlinks
- **Skills_API**: The FastAPI router providing HTTP endpoints for skill listing, creation, update, and deletion
- **Allowed_Skills_List**: A list of skill folder names (not DB UUIDs) stored on agent configuration records, controlling which skills an agent can access
- **Frontmatter**: YAML metadata block at the top of a SKILL.md file, delimited by `---` markers
- **Skill_Source_Tier**: One of three filesystem locations: built-in (`backend/skills/`), user (`~/.swarm-ai/skills/`), or plugin (`~/.swarm-ai/plugin-skills/`)

## Requirements

### Requirement 1: Three-Tier Filesystem Skill Storage

**User Story:** As a developer, I want skills organized in three distinct filesystem tiers (built-in, user, plugin), so that skill provenance is clear and each tier has appropriate lifecycle management.

#### Acceptance Criteria

1. THE Skill_Manager SHALL discover skills from three Skill_Source_Tiers: `backend/skills/` for Built_In_Skills, `~/.swarm-ai/skills/` for User_Skills, and `~/.swarm-ai/plugin-skills/` for Plugin_Skills
2. WHEN the application starts, THE Skill_Manager SHALL scan all three Skill_Source_Tiers and return a unified list of available skills
3. THE Skill_Manager SHALL identify each skill by its Skill_Directory folder name (kebab-case string), not by a database UUID
4. WHEN two skills in different tiers share the same folder name, THE Skill_Manager SHALL apply a precedence order: built-in overrides user, user overrides plugin, and SHALL log a warning identifying the shadowed skill and its tier
5. IF a Skill_Directory does not contain a valid SKILL.md file, THEN THE Skill_Manager SHALL skip that directory and log a warning
6. WHEN the `~/.swarm-ai/skills/` or `~/.swarm-ai/plugin-skills/` directory does not exist, THE Skill_Manager SHALL create it on first launch
7. WHEN a user creates a skill with a folder name that matches a Built_In_Skill, THE Skills_API SHALL return a 409 Conflict error indicating the name is reserved by a built-in skill

### Requirement 2: SKILL.md Format and Metadata Extraction

**User Story:** As a developer, I want a standardized SKILL.md format with YAML frontmatter, so that skill metadata is self-describing and parseable.

#### Acceptance Criteria

1. THE Skill_Manager SHALL parse SKILL.md files containing YAML Frontmatter delimited by `---` markers followed by markdown content
2. THE Skill_Manager SHALL extract `name`, `description`, and `version` fields from the Frontmatter
3. IF a SKILL.md file contains malformed Frontmatter, THEN THE Skill_Manager SHALL return a descriptive parse error including the file path and the nature of the malformation
4. THE Skill_Manager SHALL format skill metadata back into valid SKILL.md files with correct Frontmatter
5. FOR ALL valid skill metadata objects, parsing a SKILL.md then formatting then parsing again SHALL produce an equivalent metadata object (round-trip property)
6. WHEN a required Frontmatter field (`name` or `description`) is missing, THE Skill_Manager SHALL fall back to the folder name for `name` and a default string `"Skill: {folder_name}"` for `description`, and SHALL log a warning about the missing fields

### Requirement 3: Symlink Projection Layer

**User Story:** As a developer, I want all skills from the three tiers merged into a single directory via symlinks, so that the Claude SDK discovers them without system prompt changes.

#### Acceptance Criteria

1. THE Projection_Layer SHALL create symlinks in `SwarmWS/.claude/skills/` pointing to Skill_Directories from all three Skill_Source_Tiers
2. WHEN the Projection_Layer merges skills, THE Projection_Layer SHALL apply the same precedence order as the Skill_Manager: built-in overrides user, user overrides plugin
3. WHEN a symlink target no longer exists, THE Projection_Layer SHALL remove the stale symlink and log a warning
4. THE Projection_Layer SHALL re-project symlinks when skills are added, removed, or modified via the Skills_API
5. IF the `SwarmWS/.claude/skills/` directory does not exist, THEN THE Projection_Layer SHALL create it before projecting symlinks
6. THE Projection_Layer SHALL project all Built_In_Skills unconditionally, plus any User_Skills and Plugin_Skills that the current agent is allowed to access based on the Allowed_Skills_List


### Requirement 4: Agent Skill Access Control via Folder Names

**User Story:** As a developer, I want agent skill access controlled by folder name lists instead of database UUIDs, so that access control is filesystem-native and does not depend on database state.

#### Acceptance Criteria

1. THE Agent_Sandbox_Manager SHALL read an `allowed_skills` field from agent configuration containing a list of Skill_Directory folder names
2. WHEN an agent has `allow_all_skills` set to true, THE Agent_Sandbox_Manager SHALL project all available skills from all tiers into the agent workspace
3. WHEN an agent has a specific Allowed_Skills_List, THE Agent_Sandbox_Manager SHALL project only the listed skills into the agent workspace
4. THE Agent_Sandbox_Manager SHALL validate that each folder name in the Allowed_Skills_List corresponds to an existing Skill_Directory in at least one Skill_Source_Tier
5. IF a folder name in the Allowed_Skills_List does not match any existing Skill_Directory, THEN THE Agent_Sandbox_Manager SHALL log a warning and skip that entry
6. THE PreToolUse security hook SHALL validate skill access by comparing the invoked skill folder name against the Allowed_Skills_List, not against database UUIDs

### Requirement 5: Skills API Rewrite for Filesystem Operations

**User Story:** As a frontend developer, I want the Skills API to perform filesystem operations directly, so that the UI reflects the true state of skills on disk without database synchronization.

#### Acceptance Criteria

1. WHEN the `GET /skills` endpoint is called, THE Skills_API SHALL return a unified list of skills with metadata extracted from SKILL.md Frontmatter, using an in-memory cache that is invalidated on skill CRUD operations
2. WHEN the `GET /skills/{folder_name}` endpoint is called, THE Skills_API SHALL locate the skill by folder name across all tiers (respecting precedence) and return its metadata and content
3. WHEN the `POST /skills` endpoint is called with a skill name and content, THE Skills_API SHALL create a new Skill_Directory in `~/.swarm-ai/skills/` and write the SKILL.md file, and SHALL invalidate the skills cache
4. WHEN the `PUT /skills/{folder_name}` endpoint is called, THE Skills_API SHALL update the SKILL.md file in the existing Skill_Directory and invalidate the skills cache
5. WHEN the `DELETE /skills/{folder_name}` endpoint is called, THE Skills_API SHALL remove the Skill_Directory only if the skill is a User_Skill, and SHALL invalidate the skills cache
6. IF a delete or modify request targets a Built_In_Skill, THEN THE Skills_API SHALL return a 403 error indicating built-in skills are immutable
7. IF a modify request targets a Plugin_Skill, THEN THE Skills_API SHALL return a 403 error indicating plugin skills are managed by the plugin system
8. IF an uninstall request targets a Plugin_Skill, THE Skills_API SHALL delegate to the plugin system to remove the skill from `~/.swarm-ai/plugin-skills/`
7. WHEN any skill CRUD operation completes, THE Skills_API SHALL trigger the Projection_Layer to re-project symlinks
8. THE Skills_API SHALL include a `source_tier` field in each skill response indicating whether the skill is built-in, user, or plugin
9. THE Skills_API SHALL expose a `POST /skills/rescan` endpoint that invalidates the in-memory skills cache and returns the freshly scanned skill list
10. THE Skills_API SHALL register fixed-path routes (`/skills/rescan`, `/skills/generate-with-agent`) before parameterized `/{folder_name}` routes to prevent FastAPI from matching fixed paths as folder names
11. THE `GET /skills` endpoint SHALL return skills sorted by `folder_name` alphabetically for deterministic ordering across platforms
12. THE `GET /skills` endpoint SHALL omit the `content` field from list responses to avoid transferring large markdown bodies; only `GET /skills/{folder_name}` SHALL include `content`

### Requirement 12: Concurrent Filesystem Access

**User Story:** As a developer, I want the skills system to handle concurrent filesystem modifications gracefully, so that manual edits or external processes do not corrupt skill state.

#### Acceptance Criteria

1. WHEN the skills cache is invalidated and a rescan is triggered, THE Skill_Manager SHALL re-read all Skill_Source_Tiers from disk and rebuild the cache atomically (readers see either the old or new cache, never a partial state)
2. IF a Skill_Directory is deleted or modified externally while the application is running, THE Skill_Manager SHALL detect the change on the next cache invalidation or rescan and update accordingly
3. THE Projection_Layer SHALL handle the case where a symlink target is deleted between scan and access by catching the resulting OSError and logging a warning
4. WHEN multiple API requests trigger concurrent cache invalidations, THE Skill_Manager SHALL serialize cache rebuilds to avoid race conditions

### Requirement 6: Built-In Skills Management

**User Story:** As a developer, I want built-in skills to ship with the application in `backend/skills/` and be automatically available to all agents, so that core capabilities are always present without manual setup.

#### Acceptance Criteria

1. THE Skill_Manager SHALL treat all Skill_Directories under `backend/skills/` as Built_In_Skills
2. THE Projection_Layer SHALL always project Built_In_Skills into the agent workspace regardless of the Allowed_Skills_List
3. WHEN the application is packaged for distribution, THE Built_In_Skills SHALL be included in the application bundle from `backend/skills/`
4. THE Skills_API SHALL mark Built_In_Skills as read-only in API responses
5. IF a user attempts to modify or delete a Built_In_Skill via the Skills_API, THEN THE Skills_API SHALL return a 403 error with a message indicating built-in skills are immutable
6. THE Skill_Manager SHALL resolve the built-in skills path using a configurable base path (defaulting to the `backend/skills/` directory relative to the application root), so that the path is correct in both development and packaged (Tauri bundle) environments
7. THE application configuration SHALL expose a `BUILTIN_SKILLS_PATH` setting that can be overridden for packaged distributions where the relative path differs from development
8. THE SkillManager constructor SHALL accept optional `user_skills_path` and `plugin_skills_path` parameters (defaulting to `~/.swarm-ai/skills/` and `~/.swarm-ai/plugin-skills/` respectively) to enable isolated testing


### Requirement 7: Database Removal and Migration

**User Story:** As a developer, I want all database-backed skill storage removed, so that the system has a single source of truth (filesystem) and no synchronization complexity.

#### Acceptance Criteria

1. THE Skill_Manager SHALL NOT read from or write to the SQLite `skills` table for any skill operations
2. THE Skill_Manager SHALL NOT read from or write to the SQLite `skill_versions` table
3. THE Skill_Manager SHALL NOT read from or write to the SQLite `workspace_skills` table for skill configuration
4. WHEN the application starts after migration, THE Skill_Manager SHALL operate exclusively against the filesystem without requiring any database tables for skill data
5. THE agent configuration schema SHALL replace the `skill_ids` field (list of UUIDs) with an `allowed_skills` field (list of folder name strings)
6. WHEN an existing database contains `skill_ids` on agent records, THE migration logic SHALL first resolve each UUID to its corresponding folder name using the existing `skills` table, populate the `allowed_skills` field, and only then may the `skills` table be dropped
7. IF a `skill_ids` UUID cannot be resolved to a folder name during migration, THEN THE migration logic SHALL log a warning and skip that entry
8. THE migration logic SHALL execute the UUID-to-folder-name resolution as a single migration step that runs before any schema changes that would remove the `skills` table
9. THE migration logic SHALL verify all agent records have been successfully updated before dropping any skill-related tables; if verification fails, the migration SHALL abort without dropping tables
10. THE migration logic SHALL be idempotent — re-running when `allowed_skills` already exists SHALL be a no-op

### Requirement 8: Seed Database Generation Cleanup

**User Story:** As a developer, I want skill seeding removed from the seed database generation script, so that built-in skills are sourced exclusively from `backend/skills/` at runtime.

#### Acceptance Criteria

1. THE seed database generator SHALL NOT insert skill records into the `skills` table
2. THE seed database generator SHALL NOT reference skill UUIDs when creating the default agent record
3. WHEN the default agent is created, THE seed database generator SHALL set `allowed_skills` to an empty list (built-in skills are always available without explicit listing)
4. THE existing default skill files in `desktop/resources/default-skills/` SHALL be migrated to `backend/skills/` as proper Skill_Directories with SKILL.md format

### Requirement 9: Frontend Adaptation for Filesystem-Based Skills

**User Story:** As a frontend developer, I want the Skills page to work with filesystem-based API responses, so that users can manage skills without any database synchronization UI.

#### Acceptance Criteria

1. THE frontend skills service SHALL use folder name as the primary skill identifier instead of database UUID
2. THE frontend Skills page SHALL display the `source_tier` (built-in, user, plugin) for each skill
3. THE frontend Skills page SHALL disable edit and delete actions for Built_In_Skills and Plugin_Skills
4. THE frontend Skills page SHALL replace the "Refresh/Sync" button with a lightweight "Rescan" button that triggers a `POST /skills/rescan` endpoint to invalidate the skills cache and return the fresh skill list
5. WHEN a user creates a new skill, THE frontend SHALL send the skill name and content to the `POST /skills` endpoint and display the result
6. WHEN a user deletes a skill, THE frontend SHALL call the `DELETE /skills/{folder_name}` endpoint and remove the skill from the displayed list
7. THE frontend skills service `toCamelCase()` function SHALL be updated to map the new filesystem-based response fields including `source_tier` and `folder_name`

### Requirement 10: Skill Generation with Agent Adaptation

**User Story:** As a user, I want the AI-powered skill generation flow to create skills directly on the filesystem, so that generated skills are immediately available without database registration.

#### Acceptance Criteria

1. WHEN a skill is generated via the agent conversation flow, THE Skills_API SHALL write the generated SKILL.md and supporting files directly to `~/.swarm-ai/skills/{skill-name}/`
2. WHEN skill generation completes, THE Skills_API SHALL trigger the Projection_Layer to project the new skill
3. THE Skills_API SHALL remove the finalize endpoint since skills no longer need database registration after generation
4. IF the target Skill_Directory already exists during generation, THEN THE Skills_API SHALL return an error indicating a name conflict

### Requirement 11: Security and Path Validation

**User Story:** As a developer, I want all filesystem operations validated against path traversal attacks, so that skill operations cannot access files outside designated skill directories.

#### Acceptance Criteria

1. THE Skill_Manager SHALL validate that all skill folder names contain only alphanumeric characters, hyphens, and underscores
2. THE Skill_Manager SHALL reject folder names containing path separators (`/`, `\`), parent directory references (`..`), or null bytes
3. THE Skill_Manager SHALL resolve all file paths to their canonical form and verify they remain within the expected Skill_Source_Tier directory before performing any read or write operation
4. IF a path traversal attempt is detected, THEN THE Skill_Manager SHALL return a 400 error and log the attempt as a security warning
5. THE Projection_Layer SHALL validate symlink targets resolve to paths within one of the three Skill_Source_Tier directories before creating the symlink
6. THE Skill_Manager SHALL NOT follow symlinks within Skill_Directories when reading skill files; only regular files and directories shall be accessed to prevent symlink-based escape from tier boundaries
6. THE Skill_Manager SHALL enforce a maximum folder name length of 128 characters
7. THE Skills_API SHALL enforce a maximum content size of 500KB for skill creation and update requests to prevent disk exhaustion
