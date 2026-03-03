# Requirements: Skills Legacy Cleanup

## Introduction

After the filesystem skills re-architecture (spec: `filesystem-skills-rearchitecture`), many files still reference the old DB-backed skill system (`skill_ids`, `db.skills`, `SQLiteSkillsTable`). This spec tracks removing ALL legacy references for a clean break — no backward compatibility needed.

## Requirements

### Requirement 1: Remove db.skills from Plugin System

**User Story:** As a developer, I want the plugin system to track skill attribution via filesystem instead of DB.

#### Acceptance Criteria

1. `backend/routers/plugins.py` SHALL NOT reference `db.skills` for any operations
2. Plugin skill installation SHALL write to `~/.swarm-ai/plugin-skills/` directly
3. Plugin skill uninstallation SHALL remove from `~/.swarm-ai/plugin-skills/` directly
4. Plugin→skill attribution SHALL be tracked via plugin metadata files, not DB records

### Requirement 2: Remove db.skills from Workspace Config and Context Manager

**User Story:** As a developer, I want workspace config and context injection to use SkillManager instead of DB.

#### Acceptance Criteria

1. `backend/routers/workspace_config.py` SHALL use `SkillManager.get_cache()` instead of `db.skills.list()`
2. `backend/core/context_manager.py` SHALL use `SkillManager.get_cache()` instead of `db.skills.list()`

### Requirement 3: Remove skills Property from SQLiteDatabase

**User Story:** As a developer, I want the database class to have no skill-related properties.

#### Acceptance Criteria

1. `backend/database/sqlite.py` SHALL NOT have `skills`, `skill_versions`, or `workspace_skills` properties
2. No `SQLiteSkillsTable`, `SQLiteSkillVersionsTable`, or `SQLiteWorkspaceSkillsTable` classes SHALL exist

### Requirement 4: Update Frontend Agent Types

**User Story:** As a frontend developer, I want the Agent type to use `allowedSkills` instead of `skillIds`.

#### Acceptance Criteria

1. `desktop/src/types/index.ts` Agent interface SHALL use `allowedSkills: string[]` instead of `skillIds: string[]`
2. `desktop/src/components/common/AgentFormModal.tsx` SHALL use `allowedSkills` and the new Skills API
3. `desktop/src/services/agents.ts` SHALL map `allowed_skills` ↔ `allowedSkills`

### Requirement 5: Update Backend Tests

**User Story:** As a developer, I want all tests to use the filesystem-based skill system.

#### Acceptance Criteria

1. Tests SHALL NOT use `db.skills.put()` — use filesystem fixtures instead
2. Tests SHALL use `allowed_skills` instead of `skill_ids` in agent data
3. System skill protection tests SHALL be removed (built-in skills are always available)
4. Test fixtures in `conftest.py` SHALL use `allowed_skills`

### Requirement 6: Update Frontend Tests

**User Story:** As a frontend developer, I want all frontend tests to use the new skill field names.

#### Acceptance Criteria

1. `SwarmAgent.property.test.tsx` SHALL use `allowedSkills` instead of `skillIds`
2. `AgentsModal.property.test.tsx` SHALL use `allowedSkills` instead of `skillIds`

### Requirement 7: Remove Stale Comments and Dead Code

**User Story:** As a developer, I want no stale comments referencing the old skill system.

#### Acceptance Criteria

1. All comments referencing `skill_ids` (except in migration code) SHALL be updated
2. `skill_migration.py` SHALL be kept (handles migration for existing users)
3. No dead imports of removed skill classes SHALL remain
