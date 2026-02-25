# Requirements Document — SwarmWS Projects (Cadence 2 of 4)

## Introduction

This is **Cadence 2 of 4** for the SwarmWS redesign. It covers project CRUD operations, the standard project template, project metadata (`.project.json`), schema versioning, update history tracking, and frontend type/service layer updates. This cadence depends on Cadence 1 (`swarmws-foundation`) being completed first.

See the parent spec at `.kiro/specs/swarmws-redesign/requirements.md` for the full glossary and architectural context.

## Cross-References

This spec is part of the SwarmWS Redesign, split into 4 implementation cadences:

| Cadence | Spec | Requirements | Focus |
|---------|------|-------------|-------|
| 1 | `swarmws-foundation` | 1, 2, 3, 6, 7, 8, 17, 19, 20, 23, 24, 25, 28, 29, 30 | Single workspace, folder structure, Knowledge domain, backend data model, dead code removal |
| 2 | `swarmws-projects` | 4, 5, 18, 21, 22, 27, 31, 32 | Project CRUD, template, metadata, frontend types/services |
| 3 | `swarmws-explorer-ux` | 9, 10, 11, 12, 13, 14, 15 | Workspace Explorer UX redesign |
| 4 | `swarmws-intelligence` | 16, 26, 33 | Context assembly, chat threads, preview API |

Parent spec: `.kiro/specs/swarmws-redesign/requirements.md`

## Glossary

- **SwarmWS**: The single, non-deletable root workspace. Serves as the persistent memory container for all SwarmAI work. Located at `{app_data_dir}/SwarmWS`.
- **Project**: A self-contained execution and knowledge container under `Projects/`. Each project has its own context files, instructions, chats, research, and reports. Replaces the concept of custom workspaces.
- **Knowledge**: The shared knowledge domain at the workspace root representing workspace-level shared semantic memory. Contains `Knowledge Base/` for durable reusable assets, `Notes/` for evolving working knowledge, and `Memory/` for persistent semantic memory distilled from user interactions. Replaces the former `Artifacts/` and `Notebooks/` folders.
- **Knowledge_Base**: A subfolder under `Knowledge/` for durable, reusable, high-confidence knowledge assets.
- **Notes**: A subfolder under `Knowledge/` for evolving working knowledge and exploratory documents.
- **Memory**: A subfolder under `Knowledge/` for persistent semantic memory automatically distilled from user chat history and interactions. Contains long-term, user-specific memory reflecting preferences, patterns, recurring goals, and accumulated insights derived from conversations.
- **Context_L0**: An ultra-concise semantic abstract file (~1000 tokens) used for fast relevance detection and routing decisions. Named `context-L0.md`.
- **Context_L1**: A structured overview file (~4k tokens) describing scope, structure, goals, key knowledge, and relationships. Named `context-L1.md`.
- **Depth_Guardrail**: A maximum folder nesting limit enforced by the system to maintain usability and agent reasoning consistency.
- **System_Managed_Item**: A file or folder that is created and maintained by the system. System_Managed_Items cannot be deleted or structurally renamed by users. Users may edit the content of system-managed files.
- **User_Managed_Item**: A file or folder created by the user. User_Managed_Items support full CRUD operations within depth guardrail limits.
- **Semantic_Zone**: A visual grouping in the workspace explorer that organizes the tree into two conceptual areas: Shared Knowledge and Active Work.
- **Focus_Mode**: A project-centric explorer view that auto-expands the active project and keeps Knowledge visible.
- **Project_Metadata**: A hidden `.project.json` file inside each project directory containing system metadata (creation date, status, tags).
- **Workspace_Explorer**: The middle-column UI component that displays the SwarmWS tree structure with semantic zone grouping.
- **Sample_Data**: Realistic onboarding content pre-populated in SwarmWS on first launch to demonstrate intended usage of Knowledge and project structure.

## Requirements

### Requirement 4: Projects as Primary Organization Unit

**User Story:** As a knowledge worker, I want to organize all my active work into Projects, so that each project acts as a self-contained execution and knowledge container.

#### Acceptance Criteria

1. THE System SHALL maintain a `Projects/` folder at the workspace root containing `context-L0.md` and `context-L1.md` as System_Managed_Items.
2. THE System SHALL allow users to create new projects inside `Projects/` via the UI.
3. WHEN a new project is created, THE System SHALL scaffold the project using the Standard Project Template (see Requirement 5).
4. THE System SHALL enforce a maximum folder depth of 3 levels within each project directory (excluding system-managed subfolders).
5. WHEN a user attempts to create a folder that would exceed the 3-level depth limit inside a project, THE System SHALL block the creation and display a descriptive message.
6. THE System SHALL allow users to delete user-created projects (with confirmation dialog warning about data loss).
7. THE System SHALL allow users to rename projects.
8. THE System SHALL prevent deletion of the `Projects/` folder itself and its `context-L0.md` and `context-L1.md` files.

### Requirement 5: Standard Project Template

**User Story:** As a knowledge worker, I want each new project to come with a consistent internal structure, so that agents and I have a predictable layout for project work.

#### Acceptance Criteria

1. WHEN a new project is created, THE System SHALL create the following System_Managed_Items inside the project directory:
   - `context-L0.md` — Project abstract (~1000 tokens)
   - `context-L1.md` — Project overview (~4k tokens)
   - `instructions.md` — Editable project instructions
   - `chats/` — Chat thread storage
   - `research/` — Research materials
   - `reports/` — Project reports
   - `.project.json` — Hidden system metadata
2. THE System SHALL allow users to edit the content of `context-L0.md`, `context-L1.md`, and `instructions.md` but prevent their deletion.
3. THE System SHALL allow users to create additional subfolders and files inside the project directory.
4. THE System SHALL enforce a maximum folder depth of 2 levels within `chats/`, `research/`, and `reports/`.
5. THE System SHALL initialize `.project.json` with metadata fields: name, created_at, status, and tags.
6. THE System SHALL prevent deletion or structural renaming of System_Managed_Items within a project.

### Requirement 18: Backend — Project Management

**User Story:** As a developer, I want backend support for project lifecycle management, so that the frontend can create, list, update, and delete projects with proper validation.

#### Acceptance Criteria

1. THE Backend SHALL provide a `POST /api/projects` endpoint that creates a new project directory using the Standard Project Template and returns the project metadata (including the generated `id`).
2. THE Backend SHALL validate that project names are unique within `Projects/` and contain only filesystem-safe characters.
3. THE Backend SHALL provide a `GET /api/projects` endpoint that lists all projects with their metadata from `.project.json`.
4. THE Backend SHALL provide a `GET /api/projects/{id}` endpoint that returns a single project's metadata by its UUID.
5. THE Backend SHALL provide a `PUT /api/projects/{id}` endpoint that updates project metadata (name, status, tags, priority, description) and renames the project directory if the name changes.
6. THE Backend SHALL provide a `DELETE /api/projects/{id}` endpoint that deletes a project directory after confirmation.
7. THE Backend SHALL enforce depth guardrails when creating folders within projects via API.
8. THE Backend SHALL return responses using snake_case field names (Python/Pydantic convention).
9. THE Backend SHALL support looking up a project by name via query parameter: `GET /api/projects?name={name}` for human-readable access.
10. THE Backend SHALL acquire a per-project concurrency lock before deleting a project directory, ensuring no concurrent read or write operation is in progress during deletion.
11. THE Backend SHALL use `model_dump(exclude_unset=True)` (or equivalent) when processing PUT requests, so that omitting a nullable field leaves it unchanged while explicitly sending `null` clears it.
12. THE Backend SHALL validate project names using the same validation rules (length, allowed characters, reserved names, case-insensitive collision) for both project creation and project rename operations.

### Requirement 21: Frontend — TypeScript Type Updates

**User Story:** As a frontend developer, I want updated TypeScript interfaces reflecting the single-workspace + projects model, so that the UI is type-safe against the new data model.

#### Acceptance Criteria

1. THE Frontend SHALL remove the `SwarmWorkspace` interface and replace it with a `WorkspaceConfig` interface containing: name, filePath, icon, and context fields.
2. THE Frontend SHALL define a `Project` interface with fields: id, name, description, path, createdAt, updatedAt, status, priority, tags, schemaVersion, version, and contextL0 and contextL1 summaries.
3. THE Frontend SHALL define a `ProjectCreateRequest` interface with fields: name.
4. THE Frontend SHALL define a `ProjectUpdateRequest` interface with optional fields: name, description, status, tags, and priority.
5. THE Frontend SHALL remove `SwarmWorkspaceCreateRequest`, `SwarmWorkspaceUpdateRequest`, and all multi-workspace related types.
6. THE Frontend SHALL update `toCamelCase()` functions in service files to handle new Project fields.

### Requirement 22: Frontend — Service Layer Updates

**User Story:** As a frontend developer, I want updated service functions for the single-workspace + projects model, so that the UI can interact with the new backend API.

#### Acceptance Criteria

1. THE Frontend SHALL remove the `swarmWorkspacesService` and replace it with a `workspaceService` that provides `getConfig()` and `updateConfig()` methods.
2. THE Frontend SHALL create a `projectsService` with methods: `list()`, `get(id)`, `create(data)`, `update(id, data)`, `delete(id)`, and `getHistory(id)`. All methods that target a specific project SHALL use the project's UUID (`id`) as the identifier.
3. THE Frontend SHALL update all components that previously consumed `swarmWorkspacesService` to use the new services.
4. THE Frontend SHALL use camelCase field names in all TypeScript interfaces and convert to/from snake_case when communicating with the backend API.

### Requirement 27: Project Metadata (.project.json)

**User Story:** As a developer, I want each project to have a hidden metadata file with version control and update history, so that the system can track project state, evolution, and change provenance without cluttering the user-visible file tree.

#### Acceptance Criteria

1. WHEN a new project is created, THE System SHALL create a `.project.json` file in the project root directory.
2. THE `.project.json` SHALL contain the following core fields:
   - `id` — Unique project identifier (UUID v4)
   - `name` — Project display name (string)
   - `description` — Optional project description (string, default empty)
   - `created_at` — Creation timestamp (ISO 8601)
   - `updated_at` — Last modification timestamp (ISO 8601, auto-updated on any metadata change)
   - `status` — Project lifecycle status: `active`, `archived`, or `completed`
   - `tags` — Array of user-defined tag strings
   - `priority` — Optional priority level: `low`, `medium`, `high`, or `critical`
3. THE `.project.json` SHALL contain the following version control fields:
   - `schema_version` — Metadata schema version (semver string, e.g., `"1.0.0"`)
   - `version` — Project metadata revision counter (integer, starting at 1, incremented on each metadata update)
4. THE `.project.json` SHALL contain an `update_history` array tracking metadata changes, where each entry contains:
   - `version` — The metadata revision number after this change (integer)
   - `timestamp` — When the change occurred (ISO 8601)
   - `action` — The type of change: `created`, `updated`, `status_changed`, `renamed`, `archived`, `restored`, `tags_modified`, or `priority_changed`
   - `changes` — Object describing what changed (e.g., `{"status": {"from": "active", "to": "archived"}}`)
   - `source` — Who or what initiated the change: `user`, `agent`, `system`, or `migration`
5. THE System SHALL cap `update_history` at the most recent 50 entries. WHEN the cap is exceeded, THE System SHALL remove the oldest entries.
6. THE Workspace_Explorer SHALL hide `.project.json` from the file tree display (hidden file convention).
7. THE Backend SHALL read `.project.json` when listing projects to return metadata.
8. THE Backend SHALL update `.project.json` when project metadata is modified via the API, auto-incrementing `version`, updating `updated_at`, and appending to `update_history`.
9. THE System SHALL mark `.project.json` as a System_Managed_Item (non-deletable by users).
10. THE System SHALL validate `.project.json` against the expected `schema_version` on read and apply forward-compatible migrations if the schema version is older than the current application version.

### Requirement 31: Project Update History Tracking

**User Story:** As a knowledge worker, I want the system to automatically track changes to my project metadata, so that I can understand how a project evolved over time and who or what made changes.

#### Acceptance Criteria

1. WHEN any project metadata field is modified (status, name, tags, priority, description), THE System SHALL append an entry to the `update_history` array in `.project.json`.
2. THE update history entry SHALL record the `version`, `timestamp`, `action`, `changes` (with before/after values), and `source` of the modification.
3. WHEN a project is created, THE System SHALL record an initial `update_history` entry with action `created` and source `user` or `migration`.
4. WHEN an agent modifies project metadata (e.g., auto-archiving, status transitions), THE System SHALL record the source as `agent`.
5. WHEN the system performs automatic maintenance (e.g., schema migration, integrity repair), THE System SHALL record the source as `system`.
6. THE Backend SHALL provide a `GET /api/projects/{id}/history` endpoint that returns the `update_history` array from `.project.json`, where `{id}` is the project UUID (consistent with all other project API endpoints).
7. THE Frontend SHALL display project history in a timeline or log view accessible from the project detail panel.
8. THE System SHALL ensure `update_history` entries are append-only — existing entries SHALL NOT be modified or deleted except by the cap enforcement rule (Requirement 27, AC 5).
9. WHEN a directory rename fails during a project update, THE System SHALL log the original OS error at ERROR level and chain it in the raised exception for full traceback preservation.

### Requirement 32: Project Metadata Schema Versioning

**User Story:** As a developer, I want `.project.json` to carry a schema version, so that the system can gracefully handle metadata format changes across application updates.

#### Acceptance Criteria

1. THE `.project.json` SHALL include a `schema_version` field using semantic versioning (e.g., `"1.0.0"`).
2. WHEN the application reads a `.project.json` with a `schema_version` older than the current expected version, THE System SHALL apply forward-compatible migrations to bring the metadata up to date.
3. WHEN a schema migration is applied, THE System SHALL append an `update_history` entry with action `schema_migrated`, source `system`, and changes describing the migration (e.g., `{"schema_version": {"from": "1.0.0", "to": "1.1.0"}}`).
4. THE System SHALL NOT modify `.project.json` files with a `schema_version` newer than the current application version (forward compatibility — read but do not downgrade).
5. THE System SHALL define schema migration functions in a dedicated module (e.g., `backend/core/project_schema_migrations.py`) to keep migration logic isolated and testable.
6. FOR ALL supported schema versions, migrating from version N to version N+1 and then reading the result SHALL produce a valid `.project.json` conforming to version N+1 (migration correctness property).
