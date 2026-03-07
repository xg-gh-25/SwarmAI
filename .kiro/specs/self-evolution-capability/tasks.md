# Implementation Plan: Self-Evolution Capability

## Overview

Implement SwarmAI's self-evolution capability using a prompt-driven architecture. The core Evolution Engine is a skill (`s_self-evolution/SKILL.md`) that instructs the agent to detect triggers, execute evolution loops, and persist results. Backend additions are minimal: context file loading, config defaults, `locked_write.py` extensions for EVOLUTION.md management, and SSE event helpers. Frontend MVP renders evolution events as styled chat messages.

Implementation language: Python (backend), TypeScript (frontend).

## Tasks

- [x] 1. Backend infrastructure: context files and config
  - [x] 1.1 Create `backend/context/GROWTH_PRINCIPLES.md` default template
    - Write the 8 growth principles as specified in the design (Try before you ask, Reuse before you build, Small fix over big system, Verify before you declare, Leave a trail, Know when to stop, If it works but it's ugly make it better, If you're stuck step back and switch)
    - File is loaded by ContextDirectoryLoader and copied to `.context/` via `ensure_directory()`
    - _Requirements: 1.1, 1.2, 1.4_

  - [x] 1.2 Create `backend/context/EVOLUTION.md` empty template
    - Write the default template with three section headers: "Capabilities Built", "Optimizations Learned", "Failed Evolutions"
    - Each section has placeholder italic text indicating no entries yet
    - _Requirements: 7.1_

  - [x] 1.3 Update `backend/core/context_directory_loader.py` to add 2 new ContextFileSpec entries
    - Add `GROWTH_PRINCIPLES.md` at priority 3 (after SOUL.md, before AGENT.md) with `user_customized=True`, `truncatable=True`, `truncate_from="tail"`
    - Add `EVOLUTION.md` at priority 9 (after MEMORY.md, before KNOWLEDGE.md) with `user_customized=True`, `truncatable=True`, `truncate_from="head"`
    - Renumber existing entries' priorities to accommodate the 2 new files (AGENT→P4, USER→P5, STEERING→P6, TOOLS→P7, MEMORY→P8, KNOWLEDGE→P10, PROJECTS→P11)
    - _Requirements: 1.1, 7.1, 7.3_

  - [x] 1.4 Add `evolution` config key to `backend/core/app_config_manager.py` DEFAULT_CONFIG
    - Add the full evolution config block: `enabled`, `max_retries`, `verification_timeout_seconds`, `auto_approve_skills`, `auto_approve_scripts`, `auto_approve_installs`, `proactive_enabled`, `stuck_detection_enabled`, `max_triggers_per_session`, `same_type_cooldown_seconds`, `max_active_entries`, `deprecation_days`
    - All defaults as specified in design: enabled=True, max_retries=3, verification_timeout_seconds=120, auto_approve_*=False, proactive_enabled=True, stuck_detection_enabled=True, max_triggers_per_session=3, same_type_cooldown_seconds=60, max_active_entries=30, deprecation_days=30
    - _Requirements: 10.1, 10.2_

  - [ ]* 1.5 Write property test for config defaults (Property 6)
    - **Property 6: Evolution config defaults are complete**
    - **Validates: Requirements 10.2**
    - Create `backend/tests/test_evolution_config.py`
    - Verify fresh AppConfigManager returns all evolution keys with correct default values

  - [ ]* 1.6 Write property test for context file assembly (Property 1)
    - **Property 1: Context file assembly includes new evolution files**
    - **Validates: Requirements 1.1, 7.3**
    - Create `backend/tests/test_evolution_context_loader.py`
    - Generate random GROWTH_PRINCIPLES.md and EVOLUTION.md content, verify `load_all()` output contains both sections

- [x] 2. Checkpoint - Verify context files and config
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Extend `locked_write.py` for EVOLUTION.md operations
  - [x] 3.1 Add `--increment-field` mode to `backend/scripts/locked_write.py`
    - Implement `_increment_field(content, section, entry_id, field_name)` function
    - Find entry by ID pattern (E001, O001, F001) within the specified section
    - Increment the numeric value of the specified field by 1
    - Exit with code 1 and descriptive stderr message if entry not found or field is non-numeric
    - Add `--increment-field`, `--entry-id` CLI arguments
    - _Requirements: 7.8, 7.9_

  - [x] 3.2 Add `--set-field` mode to `backend/scripts/locked_write.py`
    - Implement `_set_field(content, section, entry_id, field_name, value)` function
    - Find entry by ID pattern within the specified section
    - Set the specified field to the given value
    - Exit with code 1 and descriptive stderr message if entry not found
    - Add `--set-field`, `--value` CLI arguments
    - _Requirements: 7.10_

  - [ ]* 3.3 Write property test for usage count increment (Property 4)
    - **Property 4: Usage count increment is monotonic**
    - **Validates: Requirements 7.8**
    - Add to `backend/tests/test_evolution_locked_write.py`
    - Generate EVOLUTION.md with random entries and usage counts, increment a random entry, verify only that entry's count changed by +1

  - [ ]* 3.4 Write property test for deprecation set-field (Property 5)
    - **Property 5: Deprecation marks stale entries correctly**
    - **Validates: Requirements 7.10**
    - Add to `backend/tests/test_evolution_locked_write.py`
    - Generate entries with random dates and usage counts, apply set-field for deprecation, verify only target entry's status changed

  - [ ]* 3.5 Write property test for sequential ID integrity (Property 7)
    - **Property 7: Sequential ID integrity after multiple writes**
    - **Validates: Requirements 11.6**
    - Add to `backend/tests/test_evolution_locked_write.py`
    - Generate random sequences of 1-20 append operations, verify sequential IDs and valid Markdown structure

  - [ ]* 3.6 Write unit tests for locked_write.py extensions
    - Add to `backend/tests/test_evolution_locked_write.py`
    - Test increment from 0 to 1, nonexistent entry error, set-field status change, preserves other entries, append to empty EVOLUTION.md gets correct first ID (E001/O001/F001)
    - _Requirements: 7.8, 7.9, 7.10_

- [x] 4. Checkpoint - Verify locked_write extensions
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Create SSE event helpers
  - [x] 5.1 Create `backend/core/evolution_events.py` with 4 event helper functions
    - Implement `evolution_start_event(trigger_type, description, strategy, attempt_number, principle)` returning dict with `event` and `data` keys
    - Implement `evolution_result_event(outcome, duration_ms, capability_created, evolution_id, failure_reason)` returning dict
    - Implement `evolution_stuck_event(signals, summary, escape_strategy)` returning dict
    - Implement `evolution_help_request_event(task_summary, trigger_type, attempts, suggested_next_step)` returning dict
    - All `data` fields in camelCase as specified in design
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

  - [x] 5.2 Add evolution marker parsing to `backend/routers/chat.py`
    - Parse `<!-- EVOLUTION_EVENT: {...} -->` markers from agent output
    - Extract JSON payload and emit as typed SSE events using the helper functions
    - Ignore malformed markers gracefully (no crash, no event emitted)
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

  - [ ]* 5.3 Write property test for SSE event helpers (Property 3)
    - **Property 3: SSE event helper field completeness**
    - **Validates: Requirements 9.1, 9.2, 9.3, 9.4**
    - Create `backend/tests/test_evolution_events.py`
    - Generate random valid inputs for each helper, verify output dict has correct `event` key and all required `data` fields in camelCase

  - [ ]* 5.4 Write unit tests for SSE event helpers and marker parsing
    - Add to `backend/tests/test_evolution_events.py`
    - Test specific example outputs for each event type (start, result success, result failure, stuck, help_request)
    - Test marker parsing: valid marker extraction, malformed marker ignored, no marker in output
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

- [x] 6. Checkpoint - Verify SSE events
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Create the core self-evolution skill
  - [x] 7.1 Create `backend/skills/s_self-evolution/SKILL.md`
    - Write YAML frontmatter with name ("Self-Evolution Engine") and description
    - Write "When to Use" section: always active, loaded into every session
    - Write "Trigger Detection Rules" section covering all 3 trigger types:
      - Reactive: tool failure analysis, missing skill detection, command unavailability, gap types (missing_skill, missing_tool, knowledge_gap), transient error vs capability gap logic (Req 3.6)
      - Proactive (MVP): EVOLUTION.md pattern matching (known_better_approach), MEMORY.md lesson applicability (applicable_lesson)
      - Stuck: 5 signal patterns (repeated_error, rewrite_loop, silent_tool_chain, self_revert, cosmetic_retry) with specific thresholds from Req 5.1-5.5
    - Write "Priority and Cooldown" section: stuck > reactive > proactive, max 3 triggers/session, 60s same-type cooldown, deferred proactive triggers
    - Write "Evolution Loop Protocol" section with per-trigger strategy sequences:
      - Reactive: compose_existing → build_new → research_and_build
      - Proactive: optimize_in_place → build_replacement → research_best_practice_and_rebuild
      - Stuck: completely_different_approach → simplify_to_mvp → research_and_new_approach
    - Write "Capability Building Instructions" section: how to create Skills (SKILL.md format in `.claude/skills/s_xxx/`), Scripts (`.swarm-ai/scripts/`), install tools
    - Write "Verification Protocol" section: re-attempt original task, timeout awareness (check config `verification_timeout_seconds`)
    - Write "EVOLUTION.md Write Protocol" section: always use `locked_write.py`, entry format for E/O/F entries, section targeting, sequential ID generation
    - Write "Help Request Format" section: structured output when all 3 attempts fail (task summary, trigger type, all strategies tried with failure reasons, suggested next step)
    - Write "SSE Event Emission" section: instruct agent to output `<!-- EVOLUTION_EVENT: {...} -->` markers for evolution_start, evolution_result, evolution_stuck_detected, evolution_help_request
    - Write "Growth Principles Reference" section: instruct agent to reference GROWTH_PRINCIPLES.md when making evolution decisions and record which principle guided each decision
    - Write "Config Awareness" section: instruct agent to check `evolution.*` config values before acting (respect enabled, auto_approve_*, proactive_enabled, stuck_detection_enabled toggles)
    - Write "Session Startup Review" section: review EVOLUTION.md for deprecated entries (30-day check), mark stale entries via locked_write
    - Write "DailyActivity Logging" section: write summary of significant evolution events to DailyActivity log
    - Write "Rules" section: hard constraints (3-attempt limit, always verify, always record, respect config, return to user's task after evolution)
    - _Requirements: 1.1, 1.2, 1.3, 1.5, 2.1-2.6, 3.1-3.6, 4.1-4.3, 5.1-5.8, 6.1-6.10, 7.1-7.11, 8.1-8.5, 10.3-10.8_

  - [ ]* 7.2 Write property test for SKILL.md format validity (Property 8)
    - **Property 8: Generated SKILL.md format validity**
    - **Validates: Requirements 6.7**
    - Add to `backend/tests/test_evolution_locked_write.py` or a new test file
    - Verify the created SKILL.md has valid YAML frontmatter with non-empty `name` and `description` fields, followed by markdown body

  - [ ]* 7.3 Write property test for EVOLUTION.md entry completeness (Property 2)
    - **Property 2: EVOLUTION.md entry completeness**
    - **Validates: Requirements 1.5, 4.3, 6.8, 7.2, 8.2, 8.5**
    - Add to `backend/tests/test_evolution_locked_write.py`
    - Generate random E/O/F entries with all required fields, write via locked_write --append, parse resulting file and verify all fields present per entry type

- [x] 8. Checkpoint - Verify skill and backend integration
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Frontend MVP: Evolution event rendering
  - [x] 9.1 Create `desktop/src/services/evolution.ts` with evolution config API and toCamelCase
    - Define TypeScript interfaces for evolution event payloads (EvolutionStartEvent, EvolutionResultEvent, EvolutionStuckEvent, EvolutionHelpRequestEvent)
    - Implement `toCamelCase()` conversion for evolution event fields (snake_case → camelCase)
    - Add evolution config fetch/update API functions using existing API patterns from `desktop/src/services/settings.ts`
    - _Requirements: 9.1-9.4, 10.1_

  - [x] 9.2 Create `desktop/src/components/chat/EvolutionMessage.tsx` component
    - Implement EvolutionEventProps interface with eventType and data fields
    - Render evolution events as styled chat messages with:
      - Icon per trigger type (⚡ reactive, 🔍 proactive, 🔄 stuck)
      - Colored left border (orange=reactive, blue=proactive, red=stuck)
      - Compact summary text showing trigger type, strategy, and attempt number
      - Click-to-expand details section showing full event context
    - Handle all 4 event types: evolution_start, evolution_result, evolution_stuck_detected, evolution_help_request
    - For help_request events, integrate with existing `ask_user_question` UI flow
    - _Requirements: 9.5_

  - [x] 9.3 Wire EvolutionMessage into the chat message stream
    - Modify the chat message rendering logic to detect evolution SSE events and render them using EvolutionMessage component
    - Ensure evolution messages appear inline in the conversation flow without disrupting normal messages
    - _Requirements: 9.5_

  - [ ]* 9.4 Write frontend tests for EvolutionMessage component
    - Create `desktop/src/components/chat/__tests__/EvolutionMessage.test.tsx`
    - Test reactive event renders with correct icon (⚡) and orange border
    - Test stuck event renders with correct icon (🔄) and red border
    - Test click expands details section
    - Test toCamelCase conversion for evolution event fields
    - _Requirements: 9.5_

- [x] 10. Checkpoint - Verify frontend MVP
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Frontend Phase 2: Enhanced UI
  - [x] 11.1 Add collapsible evolution event groups to EvolutionMessage
    - Group evolution_start + evolution_result events into a single collapsible element
    - Show collapsed summary (trigger type + outcome) with expand to see full details
    - _Requirements: 9.5_

  - [x] 11.2 Add evolution session badge to Swarm Radar panel
    - Display count of successful evolutions in current session, broken down by trigger_type
    - Use existing Swarm Radar component patterns from `desktop/src/services/radar.ts`
    - _Requirements: 9.6_

  - [x] 11.3 Add Self-Evolution section to `desktop/src/pages/SettingsPage.tsx`
    - Add a "Self-Evolution" section with toggle controls for all evolution config options
    - Include master enable/disable toggle, per-trigger-type toggles (proactive_enabled, stuck_detection_enabled), auto-approve toggles, and numeric inputs (max_retries, verification_timeout_seconds)
    - Use existing settings page patterns and API from `desktop/src/services/settings.ts`
    - _Requirements: 10.7_

  - [ ]* 11.4 Write frontend tests for Phase 2 components
    - Test collapsible event groups expand/collapse behavior
    - Test settings page renders all evolution config toggles
    - Test settings page saves config changes via API
    - _Requirements: 9.5, 9.6, 10.7_

- [x] 12. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation after each major component
- Property tests validate the 8 correctness properties from the design document
- The core skill (task 7.1) is the largest single task — it's the heart of the feature
- Backend is Python, frontend is TypeScript/React — matching existing codebase conventions
- All EVOLUTION.md writes go through `locked_write.py` for concurrent-safety
