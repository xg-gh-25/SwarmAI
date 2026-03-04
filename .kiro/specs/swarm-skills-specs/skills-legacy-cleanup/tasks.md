# Implementation Plan: Skills Legacy Cleanup

## Tasks

- [x] 1. Remove db.skills from backend source code
  - [x] 1.1 Update `backend/routers/plugins.py` — replace `db.skills` with filesystem operations via SkillManager
  - [x] 1.2 Update `backend/routers/workspace_config.py` — replace `db.skills.list()` with `SkillManager.get_cache()`
  - [x] 1.3 Update `backend/core/context_manager.py` — replace `db.skills.list()` with `SkillManager.get_cache()`
  - [x] 1.4 Fix stale comment in `backend/core/agent_defaults.py`

- [x] 2. Remove skills property from SQLiteDatabase
  - [x] 2.1 Remove `skills` property accessor from `backend/database/sqlite.py`
  - [x] 2.2 Remove `skill_versions` property accessor if still present
  - [x] 2.3 Remove `workspace_skills` property accessor if still present
  - [x] 2.4 Verify no remaining `SQLiteSkillsTable` references

- [x] 3. Update frontend Agent types and components
  - [x] 3.1 Update `desktop/src/types/index.ts` — `skillIds` → `allowedSkills` in Agent interfaces
  - [x] 3.2 Update `desktop/src/components/common/AgentFormModal.tsx` — `skillIds` → `allowedSkills`, use Skills API
  - [x] 3.3 Verify `desktop/src/services/agents.ts` maps `allowed_skills` ↔ `allowedSkills`

- [x] 4. Update backend tests
  - [x] 4.1 Fix `backend/tests/conftest.py` — `skill_ids` → `allowed_skills` in fixtures
  - [x] 4.2 Fix `backend/tests/test_agents.py` — remove `db.skills` usage, use `allowed_skills`
  - [x] 4.3 Fix `backend/tests/test_swarm_agent_properties.py` — remove system skill protection tests, use `allowed_skills`
  - [x] 4.4 Fix `backend/tests/test_workspace_config_router.py` — replace `db.skills.put()` with filesystem fixtures
  - [x] 4.5 Fix `backend/tests/test_e2e_integration.py` — replace `db.skills.put()` with filesystem fixtures
  - [x] 4.6 Fix `backend/tests/test_wiring_integration.py` — replace `db.skills.put()` with filesystem fixtures
  - [x] 4.7 Fix `backend/tests/test_integration_wiring.py` — replace `db.skills.put()` with filesystem fixtures
  - [x] 4.8 Fix `backend/tests/test_property_policy_enforcement.py` — replace `db.skills.put()` with filesystem fixtures
  - [x] 4.9 Fix `backend/tests/test_task_data_migration.py` — `skill_ids` → `allowed_skills` in schema
  - [x] 4.10 Fix `backend/tests/test_system.py` — `skill_ids` → `allowed_skills` in mock data
  - [x] 4.11 Fix `backend/tests/test_system_status_properties.py` — `skill_ids` → `allowed_skills`

- [x] 5. Update frontend tests
  - [x] 5.1 Fix `desktop/src/components/chat/SwarmAgent.property.test.tsx` — `skillIds` → `allowedSkills`
  - [x] 5.2 Fix `desktop/src/components/modals/AgentsModal.property.test.tsx` — `skillIds` → `allowedSkills`

- [x] 6. Final verification
  - [x] 6.1 Run `grep -rn "skill_ids\|db\.skills\|skillIds\|SQLiteSkillsTable" backend/ desktop/src/` to confirm zero legacy references
  - [x] 6.2 Run `cd backend && pytest` to verify all tests pass
  - [x] 6.3 Run `cd desktop && npm test -- --run` to verify frontend tests pass
  - [x] 6.4 Run seed DB generator: `cd backend && python scripts/generate_seed_db.py`
