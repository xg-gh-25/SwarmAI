# Requirements Document

## Introduction

Replace SwarmAI's fragmented MCP server configuration system (5 sources of truth across DB tables, JSON files, and UI-created records) with a deterministic 2-layer JSON file configuration system. The new architecture uses zero database storage for MCP definitions: two JSON files in `.claude/mcps/` provide a layered, mergeable config that is transparent, git-friendly, and eliminates the DB CRUD overhead. A thin REST validation layer remains server-side to enforce security guards (e.g. blocking system DB paths in env vars).

## MCP E2E Flows Overview

This section documents every way an MCP server enters, loads, and runs in SwarmAI — from the user's perspective.

### Flow 1: Catalog MCP (Browse → Enable → Use)

**User journey:** User opens MCP Settings → sees catalog list (Slack, Email, Playwright, etc.) → toggles "enabled" → fills env vars (API tokens) → saves → next chat session picks it up.

**Current path:** Frontend `MCPCatalogModal` → `POST /mcp/catalog/install` → writes to DB `mcp_servers` table → `build_mcp_config()` reads DB via `mcp_ids[]` → passed to `ClaudeAgentOptions.mcp_servers`.

**New path:** Frontend `MCP_Settings_Panel` → `PATCH /mcp/catalog/{id}` → validates env → writes `enabled: true` + env to `mcp-catalog.json` → `MCP_Config_Loader` reads file at session start → `add_mcp_server_to_dict()` → `ClaudeAgentOptions.mcp_servers`.

### Flow 2: Personal/Dev MCP (Manual Add → Use)

**User journey:** User opens MCP Settings → clicks "Add MCP" → fills name, command, args, env → saves → next session picks it up. Or user edits `mcp-dev.json` directly in their editor.

**Current path:** Frontend `MCPPage` form → `POST /mcp` → writes to DB `mcp_servers` table → `build_mcp_config()` reads DB via `mcp_ids[]`. OR user edits `~/.swarm-ai/user-mcp-servers.json` or `desktop/resources/user-mcp-servers.json` → `merge_user_local_mcp_servers()` reads file directly (bypasses DB entirely).

**New path:** Frontend `MCP_Settings_Panel` → `POST /mcp/dev` → validates → writes to `mcp-dev.json` → `MCP_Config_Loader` reads file at session start. Or user edits `mcp-dev.json` directly — same result.

### Flow 3: Marketplace Plugin MCP (Install Plugin → MCP Auto-Registered)

**User journey:** User browses Plugin Marketplace → installs a plugin that bundles `.mcp.json` → plugin's MCP servers appear in MCP Settings with a "Plugin" badge → user can toggle enabled but not edit config → uninstalling plugin removes its MCPs.

**Current path:** `PluginManager.install_plugin()` parses `.mcp.json` → stores server names in `installed_mcp_servers` on the plugin DB record → but these MCPs are **never actually registered** into `mcp_servers` table or any config file. This is a known gap — plugin MCPs are tracked but not loaded into sessions.

**New path:** `PluginManager.install_plugin()` parses `.mcp.json` → writes Config_Entry objects to `mcp-dev.json` with `source: "plugin"` and `plugin_id` fields → `MCP_Config_Loader` loads them like any other dev entry → `uninstall_plugin()` removes entries by `plugin_id`. This closes the current gap.

### Flow 4: Channel MCP (Runtime Injection — No Config File)

**User journey:** User sends a message via Feishu/Slack channel → SwarmAI injects a `channel-tools` MCP server at runtime so the agent can reply back to the channel. User never sees or configures this — it's invisible infrastructure.

**Current path:** `inject_channel_mcp()` called after `build_mcp_config()` → adds `channel-tools` entry to `mcp_servers` dict with channel-specific env vars (app_id, chat_id, etc.) → lives only in memory for that session.

**New path:** Unchanged. `inject_channel_mcp()` called after `MCP_Config_Loader` merges the 2 files → same runtime injection. No config file involvement.

### Flow 5: Session Load (All MCPs → Claude SDK)

**User journey:** User starts a chat → all enabled MCPs from all sources are merged and passed to the Claude SDK → agent can use MCP tools during the conversation.

**Current path:** `build_mcp_config()` → (1) iterate `agent_config.mcp_ids[]`, look up each in DB → (2) `merge_user_local_mcp_servers()` reads 2 JSON files → (3) `inject_channel_mcp()` → result passed to `ClaudeAgentOptions(mcp_servers=..., disallowed_tools=...)`.

**New path:** `MCP_Config_Loader.load()` → (1) read `mcp-catalog.json`, filter enabled → (2) read `mcp-dev.json`, override by id → (3) `inject_channel_mcp()` → same `ClaudeAgentOptions` output. No DB, no `mcp_ids[]`.

### Flow Summary Table

| Flow | Source | Config File | Enabled By | Editable By User | Persists Across Sessions |
|------|--------|-------------|------------|-------------------|--------------------------|
| Catalog MCP | Product-seeded | `mcp-catalog.json` | User toggle | enabled + env only | Yes |
| Dev/Personal MCP | User-created | `mcp-dev.json` | Default true | Full control | Yes |
| Plugin MCP | Marketplace plugin | `mcp-dev.json` (tagged) | Default true | enabled toggle only | Until plugin uninstalled |
| Channel MCP | Runtime injection | None (in-memory) | Automatic | No | Per-session only |

## Glossary

- **MCP_Config_Loader**: Backend module that reads, validates, and merges the two JSON config layers into a single resolved MCP server list at session start.
- **MCP_Settings_Panel**: Single frontend component replacing MCPPage, MCPCatalogModal, MCPServersModal, McpsTab, and mcp.ts service. Renders both layers in a unified settings view.
- **Catalog_Layer**: The `.claude/mcps/mcp-catalog.json` file seeded by the product with public catalog entries. User edits `enabled` and `env` fields; product merges new entries on upgrade while preserving user overrides.
- **Dev_Layer**: The `.claude/mcps/mcp-dev.json` file owned entirely by the user for personal/internal MCP servers. Also holds plugin-installed MCPs (tagged with `source: "plugin"`). Never shipped, never overwritten, git-ignored.
- **Config_Entry**: A single MCP server definition within any layer, identified by a unique `id` field.
- **Validation_Service**: Server-side REST endpoint that validates MCP config changes (env var security, schema correctness) before writing to disk.
- **Migration_Service**: One-time backend process that converts existing DB MCP records and `~/.swarm-ai/user-mcp-servers.json` into the new file-based format.

## Requirements

### Requirement 1: Two-Layer File Loading

**User Story:** As a developer, I want MCP server configuration loaded from two layered JSON files, so that configuration is transparent, mergeable, and does not require a database.

#### Acceptance Criteria

1. WHEN the application starts a session, THE MCP_Config_Loader SHALL read MCP definitions from two files in order: `.claude/mcps/mcp-catalog.json` (Catalog_Layer), `.claude/mcps/mcp-dev.json` (Dev_Layer).
2. WHEN both layers contain a Config_Entry with the same `id`, THE MCP_Config_Loader SHALL use the entry from the Dev_Layer (higher priority).
3. IF a layer file does not exist, THEN THE MCP_Config_Loader SHALL skip that layer without error and continue loading the remaining layer.
4. IF a layer file contains invalid JSON, THEN THE MCP_Config_Loader SHALL log a warning identifying the file, skip that layer, and continue loading the remaining layer.
5. THE MCP_Config_Loader SHALL produce a merged dict of MCP server configs and a `disallowed_tools` list, preserving the format expected by `ClaudeAgentOptions`.
6. WHEN the merged list is produced, THE MCP_Config_Loader SHALL exclude any Config_Entry where `enabled` is explicitly set to `false`.
7. THE MCP_Config_Loader SHALL retain the existing `add_mcp_server_to_dict()` function for converting Config_Entry objects into SDK format, handling name dedup and `rejected_tools` → `disallowed_tools` conversion.

### Requirement 2: Config Entry Schema

**User Story:** As a developer, I want a consistent JSON schema for MCP server entries across both layers, so that tooling and validation work uniformly.

#### Acceptance Criteria

1. THE MCP_Config_Loader SHALL require each Config_Entry to contain: `id` (string, unique within a layer), `name` (string), `connection_type` (one of `stdio`, `sse`, `http`), and `config` (object with connection-specific fields nested inside).
2. THE MCP_Config_Loader SHALL accept optional fields on each Config_Entry: `description`, `enabled` (boolean, defaults to `false` for Catalog_Layer, defaults to `true` for Dev_Layer), `rejected_tools` (array of tool name strings), `category` (string), `_version` (integer for catalog upgrade merge logic), `source` (string, e.g. `"user"`, `"plugin"`), `plugin_id` (string, present only for plugin-installed entries). Catalog_Layer entries additionally support `required_env` (array of `{key, label, placeholder, secret?}` objects), `optional_env` (array of `{key, label, default?}` objects), and `presets` (dict of preset configs with `label`, `env`, `setup_hint`) for the frontend setup flow. The user's actual runtime env values are stored in `config.env` (same location as Dev_Layer entries).
3. WHEN a Config_Entry has `connection_type` of `stdio`, THE MCP_Config_Loader SHALL require `config.command` (string) to be present.
4. WHEN a Config_Entry has `connection_type` of `sse` or `http`, THE MCP_Config_Loader SHALL require `config.url` (string) to be present.
5. THE MCP_Config_Loader SHALL preserve the `config: {}` nesting structure so that `add_mcp_server_to_dict()` can read `config.get("command")`, `config.get("args")`, `config.get("url")`, and `config.get("env")` without modification.

### Requirement 3: Catalog Layer Upgrade Merge

**User Story:** As a product maintainer, I want new catalog entries to be merged into the user's catalog file on upgrade without losing their enabled/env customizations, so that users get new integrations while keeping their settings.

#### Acceptance Criteria

1. WHEN the application starts and detects a bundled catalog template with entries not present in the user's `mcp-catalog.json` (by `id`), THE MCP_Config_Loader SHALL append the new entries to `mcp-catalog.json` with `enabled: false`.
2. WHEN the application starts and detects a bundled catalog entry whose `_version` is higher than the corresponding entry in the user's `mcp-catalog.json`, THE MCP_Config_Loader SHALL update the entry's non-user fields (name, description, config, category, required_env, optional_env, presets) while preserving the user's `enabled` and `env` values.
3. THE MCP_Config_Loader SHALL write the merged result back to `mcp-catalog.json` only when changes are detected (new entries added or version-bumped entries updated).
4. IF the bundled catalog template file does not exist, THEN THE MCP_Config_Loader SHALL skip catalog merge without error.

### Requirement 4: Dev Layer User Ownership

**User Story:** As a developer, I want full control over `mcp-dev.json` without the product ever overwriting it, so that my personal MCP servers persist across upgrades.

#### Acceptance Criteria

1. THE System SHALL add `.claude/mcps/mcp-dev.json` to the workspace `.gitignore` during initialization. The `mcp-catalog.json` file SHALL remain git-tracked so teams can share which catalog MCPs are enabled.
2. THE MCP_Config_Loader SHALL treat all entries in `mcp-dev.json` as `enabled: true` by default (unless explicitly set to `false`).
3. THE System SHALL provide the Dev_Layer file as the target for user-created MCP servers added through the MCP_Settings_Panel.
4. THE MCP_Config_Loader SHALL load Dev_Layer entries last so they override any same-id entries from Catalog_Layer.
5. THE System SHALL create the `.claude/mcps/` directory at workspace initialization (alongside `.claude/skills/` and `.claude/settings/`), creating parent directories as needed.

### Requirement 5: Server-Side Validation Endpoint

**User Story:** As a developer, I want MCP config changes validated server-side before being written to disk, so that security guards (like blocking system DB paths) are enforced regardless of client.

#### Acceptance Criteria

1. WHEN the MCP_Settings_Panel submits a new or updated Config_Entry, THE Validation_Service SHALL validate the entry against the Config_Entry schema before writing to the target layer file.
2. WHEN a Config_Entry contains `config.env` values that resolve to SwarmAI's internal database path (`~/.swarm-ai/data.db` or any `.db` file inside `~/.swarm-ai/`), THE Validation_Service SHALL reject the request with a descriptive error identifying the offending env key.
3. WHEN a Config_Entry has `connection_type` of `stdio` and `config.command` is empty or missing, THE Validation_Service SHALL reject the request with a validation error.
4. WHEN a Config_Entry has `connection_type` of `sse` or `http` and `config.url` is empty or missing, THE Validation_Service SHALL reject the request with a validation error.
5. THE Validation_Service SHALL expose REST endpoints for: reading all layers merged (GET), updating a catalog entry's `enabled`/`env` (PATCH), creating a dev entry (POST), updating a dev entry (PUT), and deleting a dev entry (DELETE).

### Requirement 6: Migration from DB to File

**User Story:** As an existing user, I want my current MCP server configurations automatically migrated to the new file-based system on first load, so that I do not lose any configured integrations.

#### Acceptance Criteria

1. WHEN the application starts and `mcp-dev.json` does not exist, THE Migration_Service SHALL check for existing MCP records in the `mcp_servers` DB table and `~/.swarm-ai/user-mcp-servers.json`.
2. WHEN DB records with `source_type` of `user` or `marketplace` (non-system) exist, THE Migration_Service SHALL write them to `mcp-dev.json` preserving their `id`, `name`, `connection_type`, `config`, and `rejected_tools` fields.
3. WHEN `~/.swarm-ai/user-mcp-servers.json` or `desktop/resources/user-mcp-servers.json` (source tree) exist and contain entries not already present in the migrated set (by `id` or `name`), THE Migration_Service SHALL append those entries to `mcp-dev.json`. Both file locations are checked since the current `merge_user_local_mcp_servers()` reads from both.
4. WHEN migration completes, THE Migration_Service SHALL log a summary indicating the number of entries migrated from each source.
5. IF DB records exist but cannot be migrated (e.g. missing required fields), THEN THE Migration_Service SHALL log a warning for each unmigrated entry identifying the record and the reason.
6. THE Migration_Service SHALL run migration at most once per workspace; subsequent starts with an existing `mcp-dev.json` SHALL skip migration.

### Requirement 7: Plugin-Installed MCP Integration

**User Story:** As a plugin developer, I want plugin-bundled MCP servers to be automatically registered when a plugin is installed, and removed when uninstalled, so that plugin MCPs actually work (closing the current gap where they are tracked but never loaded).

#### Acceptance Criteria

1. WHEN a plugin is installed and its `.mcp.json` contains MCP server definitions, THE PluginManager SHALL convert each server from the Claude Code format (`{"mcpServers": {"name": {command, args, env}}}`) into a Config_Entry object and write it to `mcp-dev.json` with `source: "plugin"` and `plugin_id` set to the plugin's ID. The server name key becomes the `id` and `name` fields; `command`, `args`, and `env` are nested under `config`.
2. WHEN a plugin is uninstalled, THE PluginManager SHALL remove all Config_Entry objects from `mcp-dev.json` where `plugin_id` matches the uninstalled plugin's ID.
3. WHEN a plugin's `.mcp.json` defines a server with an `id` that already exists in `mcp-dev.json` (from a different source), THE PluginManager SHALL log a warning and skip that server to avoid overwriting user config.
4. THE MCP_Settings_Panel SHALL display plugin-installed entries with a distinct "Plugin" badge and prevent users from editing the `config` fields (only `enabled` toggle is allowed).
5. WHEN the MCP_Config_Loader loads a plugin-installed entry, it SHALL treat it identically to any other Dev_Layer entry for SDK config generation purposes.

### Requirement 8: Frontend Settings Panel

**User Story:** As a user, I want a single unified settings panel for managing all MCP servers across both layers, so that I have one place to view, enable, configure, and add MCP integrations.

#### Acceptance Criteria

1. THE MCP_Settings_Panel SHALL display all Config_Entry objects from both layers in a single view, grouped into two sections: Catalog (with toggle switches + env fields) and Dev/Personal (with full CRUD).
2. WHEN a user toggles the `enabled` state of a Catalog_Layer entry, THE MCP_Settings_Panel SHALL send a PATCH request to the Validation_Service and update `mcp-catalog.json` with the new `enabled` value.
3. WHEN a user edits the `env` values of a Catalog_Layer entry, THE MCP_Settings_Panel SHALL send a PATCH request to the Validation_Service and update `mcp-catalog.json` with the new `env` values while preserving all other fields.
4. WHEN a user adds a new MCP server through the panel, THE MCP_Settings_Panel SHALL send a POST request to the Validation_Service targeting the Dev_Layer.
5. WHEN a user deletes a Dev_Layer entry (non-plugin), THE MCP_Settings_Panel SHALL send a DELETE request to the Validation_Service and remove the entry from `mcp-dev.json`.
6. THE MCP_Settings_Panel SHALL display plugin-installed entries with a "Plugin" badge and restrict editing to the `enabled` toggle only.
7. THE MCP_Settings_Panel SHALL replace the following existing components: MCPPage.tsx, MCPCatalogModal.tsx, MCPServersModal.tsx, McpsTab.tsx, and the mcp.ts service file.

### Requirement 9: Legacy Code Removal

**User Story:** As a maintainer, I want all obsolete DB-backed MCP code removed after migration, so that the codebase has a single source of truth and no dead code paths.

#### Acceptance Criteria

1. THE System SHALL remove the `mcp_servers` DB table reads from `build_mcp_config()` and the `mcp_ids[]` iteration loop. The DB table itself is left in place for one release (no destructive migration), but is no longer read.
2. THE System SHALL replace the entire `backend/routers/mcp.py` CRUD router with the new Validation_Service router (thin file-based endpoints).
3. THE System SHALL remove `backend/schemas/mcp.py` (MCPConfig, MCPCreateRequest, MCPUpdateRequest, MCPResponse) and replace with file-config-oriented schemas.
4. THE System SHALL remove `agent_defaults._register_default_mcp_servers()` and all DB MCP registration logic from `ensure_default_agent()`, retaining only non-MCP agent bootstrap logic.
5. THE System SHALL remove `mcp_config_builder.merge_user_local_mcp_servers()` and replace `build_mcp_config()` with a call to the MCP_Config_Loader.
6. THE System SHALL preserve `add_mcp_server_to_dict()` and `inject_channel_mcp()` in the refactored `mcp_config_builder.py`.
7. THE System SHALL move `_validate_env_no_system_db()` from `routers/mcp.py` to a shared utility module (`backend/utils/mcp_validation.py`) so both the Validation_Service and MCP_Config_Loader can use it.
8. THE System SHALL remove `agents.mcp_ids[]` field usage from agent startup, agent update API, and all related tests (`test_agents.py`, `test_default_agent_properties.py`).

### Requirement 10: Channel MCP Injection Preservation

**User Story:** As a developer using channel integrations (Feishu, etc.), I want channel-specific MCP servers to continue being injected at runtime, so that channel workflows are unaffected by the config redesign.

#### Acceptance Criteria

1. THE MCP_Config_Loader SHALL call `inject_channel_mcp()` after merging the two file layers, preserving the existing runtime injection behavior for channel-specific MCP servers.
2. THE `inject_channel_mcp()` function SHALL remain unchanged in its interface: accepting `mcp_servers` dict, `channel_context` dict, and `working_directory` string, and returning the updated `mcp_servers` dict.
3. WHEN no `channel_context` is provided, THE MCP_Config_Loader SHALL skip channel injection without error.
