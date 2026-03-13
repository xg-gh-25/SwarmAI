# Implementation Plan: MCP File-Based Configuration

## Overview

Replace SwarmAI's fragmented MCP server configuration (DB tables, multiple JSON files, UI-created records) with a deterministic two-layer JSON file system. Implementation follows a bottom-up dependency order: shared utils → core loader → catalog merge → migration → validation router → schemas → wiring → plugin integration → frontend → legacy cleanup → tests.

## Tasks

- [x] 1. Create shared validation utilities
  - [x] 1.1 Create `backend/utils/mcp_validation.py` with `validate_env_no_system_db()` and `validate_config_entry()`
    - Extract `_validate_env_no_system_db()` from `backend/routers/mcp.py` (lines 27–82) into `validate_env_no_system_db()` (public, no underscore prefix)
    - Add `validate_config_entry(entry: dict) -> list[str]` that checks required fields (`id`, `name`, `connection_type`, `config`), `stdio` requires `config.command`, `sse`/`http` requires `config.url`, and env vars pass `validate_env_no_system_db()`
    - Include module-level docstring per project conventions
    - _Requirements: 5.2, 5.3, 5.4, 9.7_

  - [ ]* 1.2 Write property test for env var security (Property 4)
    - **Property 4: Env var security — system DB paths rejected**
    - **Validates: Requirements 5.2**

  - [ ]* 1.3 Write property test for connection-type field validation (Property 3)
    - **Property 3: Connection-type-specific field validation**
    - **Validates: Requirements 2.1, 2.3, 2.4, 5.3, 5.4**

- [x] 2. Implement MCP_Config_Loader core
  - [x] 2.1 Create `backend/core/mcp_config_loader.py` with `read_layer()`, `merge_layers()`, `load_mcp_config()`, `get_mcp_file_paths()`
    - `read_layer(path, default_enabled)` — reads a single JSON layer file, returns `[]` on missing/invalid JSON (logs warning), applies `default_enabled` to entries without explicit `enabled`
    - `merge_layers(catalog_entries, dev_entries)` — dev overrides catalog by `id`, filters out `enabled=False`
    - `load_mcp_config(workspace_path, enable_mcp)` — entry point: reads both layers, merges, converts via `add_mcp_server_to_dict()`, returns `(mcp_servers, disallowed_tools)`
    - `get_mcp_file_paths(workspace_path)` — returns `(catalog_path, dev_path)` tuple
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.3, 2.4, 2.5, 4.2, 4.4_

  - [x] 2.2 Move `add_mcp_server_to_dict()` and `inject_channel_mcp()` from `mcp_config_builder.py` to `mcp_config_loader.py`
    - Copy both functions with unchanged logic
    - `add_mcp_server_to_dict()` handles name dedup, connection type dispatch, env expansion, `rejected_tools` → `disallowed_tools`
    - `inject_channel_mcp()` handles channel-specific MCP injection (Feishu, etc.)
    - _Requirements: 1.7, 10.1, 10.2, 10.3_

  - [ ]* 2.3 Write property test for dev-layer override (Property 1)
    - **Property 1: Dev layer overrides catalog by id**
    - **Validates: Requirements 1.2, 4.4**

  - [ ]* 2.4 Write property test for enabled filtering with layer-specific defaults (Property 2)
    - **Property 2: Enabled filtering with layer-specific defaults**
    - **Validates: Requirements 1.6, 4.2**

- [x] 3. Implement catalog merge logic
  - [x] 3.1 Add `merge_catalog_template()` to `mcp_config_loader.py`
    - Reads bundled template (`desktop/resources/optional-mcp-servers.json`) and user's `mcp-catalog.json`
    - Appends new entries (by `id`) with `enabled: false`
    - Updates entries where `template._version > existing._version`, preserving user's `enabled` and `config.env`
    - Writes only when changes detected; uses atomic write pattern (`.tmp` → `os.replace()`)
    - Skips silently if template file doesn't exist
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ]* 3.2 Write property test for catalog upgrade merge (Property 5)
    - **Property 5: Catalog upgrade merge preserves user customizations**
    - **Validates: Requirements 3.1, 3.2**

- [x] 4. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement migration service
  - [x] 5.1 Create `backend/core/mcp_migration.py` with `migrate_if_needed()`
    - Runs only when `mcp-dev.json` does not exist (idempotent guard)
    - Reads from: (1) DB `mcp_servers` table (`source_type != 'system'`), (2) `~/.swarm-ai/user-mcp-servers.json`, (3) `desktop/resources/user-mcp-servers.json`
    - Deduplicates by `id` then by `name`
    - Writes to `mcp-dev.json` using atomic write pattern
    - Logs summary of migrated entries per source; logs warnings for unmigrated entries
    - Creates `.claude/mcps/` directory if needed
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

  - [ ]* 5.2 Write property test for migration deduplication (Property 6)
    - **Property 6: Migration produces deduplicated union from all sources**
    - **Validates: Requirements 6.2, 6.3, 6.6**

- [x] 6. Replace validation service router and schemas
  - [x] 6.1 Replace `backend/schemas/mcp.py` with file-config-oriented schemas
    - `CatalogUpdateRequest` — PATCH catalog entry (`enabled`, `env`)
    - `DevCreateRequest` — POST new dev entry (all required fields)
    - `DevUpdateRequest` — PUT existing dev entry (partial update)
    - `ConfigEntryResponse` — unified response with `layer` field, catalog-only fields (`required_env`, `optional_env`, `presets`)
    - _Requirements: 2.1, 2.2, 5.1_

  - [x] 6.2 Replace `backend/routers/mcp.py` with file-based validation endpoints
    - `GET /mcp` — merged view from both layers
    - `GET /mcp/catalog` — raw catalog layer entries
    - `PATCH /mcp/catalog/{entry_id}` — toggle enabled, update env (validates via `validate_env_no_system_db`, writes `mcp-catalog.json`)
    - `GET /mcp/dev` — raw dev layer entries
    - `POST /mcp/dev` — create dev entry (validates schema + env, writes `mcp-dev.json`)
    - `PUT /mcp/dev/{entry_id}` — update dev entry
    - `DELETE /mcp/dev/{entry_id}` — delete non-plugin dev entry (403 for plugin entries)
    - All writes use atomic write pattern; import from `mcp_config_loader` and `mcp_validation`
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 9.2_

- [x] 7. Wire into agent_manager.py and initialization_manager.py
  - [x] 7.1 Update `backend/core/agent_manager.py` to delegate to `mcp_config_loader`
    - Change import: `from core.mcp_config_loader import load_mcp_config, inject_channel_mcp`
    - Remove import: `from core.mcp_config_builder import build_mcp_config, inject_channel_mcp` (or the aliased `_build_mcp_config_fn`)
    - Update `_build_mcp_config()` to call `load_mcp_config(workspace_path, enable_mcp)` — synchronous, no `await` needed
    - Call `inject_channel_mcp()` after `load_mcp_config()` with existing channel_context logic
    - _Requirements: 1.1, 1.5, 9.5, 10.1_

  - [x] 7.2 Update `backend/core/initialization_manager.py` to call `merge_catalog_template()` and `migrate_if_needed()`
    - In `run_full_initialization()`, after `ensure_default_workspace()` and before `refresh_builtin_defaults()`:
      1. Call `migrate_if_needed(workspace_path)` — one-time DB→file migration
      2. Call `merge_catalog_template(workspace_path, template_path)` — catalog upgrade merge
    - Ensure `.claude/mcps/` directory is created during workspace initialization
    - _Requirements: 3.1, 4.5, 6.1_

  - [x] 7.3 Simplify `backend/core/agent_defaults.py`
    - Remove `_register_default_mcp_servers()` function entirely
    - Remove `system_mcp_ids` logic from `ensure_default_agent()`
    - Remove `mcp_ids` management (no longer read at session start)
    - Keep non-MCP agent bootstrap logic (name, description, skills, permissions)
    - _Requirements: 9.4, 9.8_

- [x] 8. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Plugin integration
  - [x] 9.1 Add `write_plugin_mcps()` and `remove_plugin_mcps()` to `mcp_config_loader.py`
    - `write_plugin_mcps(workspace_path, mcp_data, plugin_id)` — converts `.mcp.json` `mcpServers` entries to Config_Entry objects, appends to `mcp-dev.json` with `source: "plugin"` and `plugin_id`; skips entries whose `id` already exists from a different source (logs warning)
    - `remove_plugin_mcps(workspace_path, plugin_id)` — removes all entries where `plugin_id` matches, returns count
    - Both use atomic write pattern
    - _Requirements: 7.1, 7.2, 7.3, 7.5_

  - [x] 9.2 Update `backend/core/plugin_manager.py` to call `write_plugin_mcps` / `remove_plugin_mcps`
    - In `install_plugin()`, after the existing `.mcp.json` parsing block (~line 830): call `write_plugin_mcps(workspace_path, mcp_data, plugin_name)`
    - In `uninstall_plugin()`: call `remove_plugin_mcps(workspace_path, plugin_name)`
    - _Requirements: 7.1, 7.2_

  - [ ]* 9.3 Write property test for plugin format conversion (Property 7)
    - **Property 7: Plugin MCP format conversion**
    - **Validates: Requirements 7.1**

  - [ ]* 9.4 Write property test for plugin uninstall (Property 8)
    - **Property 8: Plugin uninstall removes exactly matching entries**
    - **Validates: Requirements 7.2**

  - [ ]* 9.5 Write property test for plugin install skip existing (Property 9)
    - **Property 9: Plugin install skips existing ids from different sources**
    - **Validates: Requirements 7.3**

- [x] 10. Frontend: mcpConfig.ts service and MCPSettingsPanel component
  - [x] 10.1 Create `desktop/src/services/mcpConfig.ts`
    - `listAll()` — GET /mcp (merged view)
    - `listCatalog()` — GET /mcp/catalog
    - `updateCatalogEntry(id, { enabled?, env? })` — PATCH /mcp/catalog/{id}
    - `listDev()` — GET /mcp/dev
    - `createDevEntry(entry)` — POST /mcp/dev
    - `updateDevEntry(id, update)` — PUT /mcp/dev/{id}
    - `deleteDevEntry(id)` — DELETE /mcp/dev/{id}
    - Define TypeScript interfaces: `ConfigEntry`, `DevCreateRequest`, `DevUpdateRequest`
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [x] 10.2 Create `desktop/src/components/workspace-settings/MCPSettingsPanel.tsx`
    - Two sections: "Catalog Integrations" (toggle + env fields) and "Dev / Personal" (full CRUD)
    - Catalog entries: toggle switch for `enabled`, env input fields for `required_env`/`optional_env`, PATCH on change
    - Dev entries: "Add MCP" button (POST), edit form (PUT), delete button (DELETE, non-plugin only)
    - Plugin entries: "Plugin" badge, `enabled` toggle only, no edit/delete
    - Uses `mcpConfigService` from `mcpConfig.ts`
    - Uses `react-query` for data fetching/mutation (consistent with existing patterns)
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 7.4_

- [x] 11. Legacy cleanup
  - [x] 11.1 Delete `backend/core/mcp_config_builder.py`
    - All functions (`build_mcp_config`, `merge_user_local_mcp_servers`, `add_mcp_server_to_dict`, `inject_channel_mcp`) have been moved to `mcp_config_loader.py`
    - _Requirements: 9.5, 9.6_

  - [x] 11.2 Delete legacy frontend files
    - Delete `desktop/src/pages/MCPPage.tsx`
    - Delete `desktop/src/components/modals/MCPCatalogModal.tsx`
    - Delete `desktop/src/components/modals/MCPServersModal.tsx`
    - Delete `desktop/src/components/workspace-settings/McpsTab.tsx`
    - Delete `desktop/src/services/mcp.ts`
    - Update any imports/references to these files (sidebar navigation, workspace settings routing)
    - _Requirements: 8.7, 9.1_

  - [x] 11.3 Remove `mcp_ids` usage from agent startup and update API
    - Remove `mcp_ids` iteration from `_build_mcp_config()` (already replaced in 7.1)
    - Remove `mcp_ids` field management from agent update endpoints and related tests
    - _Requirements: 9.1, 9.8_

- [x] 12. Auto-commit hook category and gitignore update
  - [x] 12.1 Add `".claude/mcps/": "config"` to `COMMIT_CATEGORIES` in `backend/hooks/auto_commit_hook.py`
    - Ensures `mcp-catalog.json` changes get committed with `config:` prefix
    - _Requirements: 4.1_

  - [x] 12.2 Add `.claude/mcps/mcp-dev.json` to workspace `.gitignore`
    - Dev layer is git-ignored (machine-specific, contains secrets)
    - Catalog layer remains git-tracked (team-shared)
    - _Requirements: 4.1_

- [x] 13. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 14. Unit and integration tests
  - [ ]* 14.1 Write unit tests for MCP_Config_Loader
    - Test `read_layer()` with missing file, invalid JSON, valid file
    - Test `merge_layers()` with empty layers, overlapping ids, enabled filtering
    - Test `load_mcp_config()` end-to-end with both layer files
    - Test `merge_catalog_template()` with new entries, version bumps, no-change scenarios
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.6, 3.1, 3.2, 3.3, 3.4_

  - [ ]* 14.2 Write unit tests for migration service
    - Test `migrate_if_needed()` runs when `mcp-dev.json` missing, skips when present
    - Test deduplication across DB + legacy file sources
    - Test handling of entries with missing required fields
    - _Requirements: 6.1, 6.2, 6.3, 6.5, 6.6_

  - [ ]* 14.3 Write unit tests for validation router
    - Test PATCH catalog toggle, POST dev entry, PUT dev entry, DELETE dev entry
    - Test 403 on delete plugin entry, 404 on missing entry
    - Test env var security rejection
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [ ]* 14.4 Write integration tests for full session load path
    - Create both layer files → call `load_mcp_config()` → verify `ClaudeAgentOptions`-compatible output
    - Plugin install → verify entries in `mcp-dev.json` → verify session load
    - Migration → verify `mcp-dev.json` content → verify session load
    - _Requirements: 1.1, 1.5, 7.1, 6.1_

- [x] 15. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties (9 properties from design)
- Unit tests validate specific examples and edge cases
- Backend uses Python, frontend uses TypeScript/React (matching existing codebase)
- All file writes use atomic pattern (`.tmp` → `os.replace()`) to prevent corruption
