# Requirements Document — SwarmWS Foundation (Cadence 1 of 4)

## Introduction

This is **Cadence 1 of 4** for the SwarmWS redesign. It covers the foundational breaking changes required before any other cadence can proceed: single workspace enforcement, the new folder structure (including Memory/ for persistent semantic memory), shared knowledge (Knowledge domain), context layering (L0/L1), system vs user content ownership, depth guardrails, the backend single-workspace data model, SwarmWorkspaceManager refactor, filesystem operations with depth enforcement, onboarding sample data, legacy data cleanup (clean-slate), dead code removal, system prompts file, workspace initialization/startup integrity, and folder structure round-trip integrity.

See the parent spec at `.kiro/specs/swarmws-redesign/requirements.md` for the full architectural context, key design shifts, and complete requirement set.

## Cross-References

This spec is part of the SwarmWS Redesign, split into 4 implementation cadences:

| Cadence | Spec | Requirements | Focus |
|---------|------|-------------|-------|
| 1 | `swarmws-foundation` | 1, 2, 3, 6, 7, 8, 17, 19, 20, 23, 24, 25, 28, 29, 30 | Single workspace, folder structure, backend data model, dead code removal |
| 2 | `swarmws-projects` | 4, 5, 18, 21, 22, 27, 31, 32 | Project CRUD, template, metadata, frontend types/services |
| 3 | `swarmws-explorer-ux` | 9, 10, 11, 12, 13, 14, 15 | Workspace Explorer UX redesign |
| 4 | `swarmws-intelligence` | 16, 26, 33 | Context assembly, chat threads, preview API |

Parent spec: `.kiro/specs/swarmws-redesign/requirements.md`

## Glossary

- **SwarmWS**: The single, non-deletable root workspace. Serves as the persistent, local-first knowledge and project operating root for all SwarmAI work. Located at `{app_data_dir}/SwarmWS`.
- **Project**: A self-contained execution and knowledge container under `Projects/`. Each project has its own context files, instructions, chats, research, and reports. Replaces the concept of custom workspaces.
- **Knowledge**: The shared knowledge domain at the workspace root representing workspace-level shared semantic memory. Contains `Knowledge Base/` for durable reusable assets, `Notes/` for evolving working knowledge, and `Memory/` for persistent semantic memory distilled from user interactions. Replaces the former `Artifacts/` and `Notebooks/` folders.
- **Knowledge_Base**: A subfolder under `Knowledge/` for durable, reusable knowledge assets accessible across all projects.
- **Notes**: A subfolder under `Knowledge/` for ongoing notes, references, and working documents that span projects.
- **Memory**: A subfolder under `Knowledge/` for persistent semantic memory automatically distilled from user chat history and interactions. Contains long-term, user-specific memory reflecting preferences, patterns, recurring goals, and accumulated insights derived from conversations. Memory items are concise and semantically meaningful, stable once validated, and editable by users to correct or refine extracted understanding.
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

### Requirement 1: Single Workspace Enforcement

**User Story:** As a knowledge worker, I want SwarmWS to be the only workspace in SwarmAI, so that I have a single persistent memory container without the overhead of managing multiple workspaces.

#### Acceptance Criteria

1. THE System SHALL operate with exactly one workspace named "SwarmWS" located at `{app_data_dir}/SwarmWS`.
2. THE System SHALL create SwarmWS automatically on first application launch if the workspace does not exist.
3. THE System SHALL prevent deletion of SwarmWS through any UI or API interaction.
4. THE System SHALL remove all backend API endpoints related to creating, listing, switching, archiving, and unarchiving multiple workspaces.
5. THE System SHALL remove the `swarm_workspaces` database table and replace it with a single workspace record or configuration entry.
6. THE System SHALL remove all frontend components related to multi-workspace management: workspace dropdown selector, "New Workspace" button, "Show Archived Workspaces" checkbox, and the Global|SwarmWS toggle switch.
7. WHEN the application starts with an existing multi-workspace database, THE System SHALL drop all legacy workspace data (the `swarm_workspaces` table and associated filesystem directories) and initialize a fresh SwarmWS structure. No data migration is performed since the product is still under active development.

### Requirement 2: Workspace Folder Structure

**User Story:** As a knowledge worker, I want SwarmWS to have a clear, organized folder structure, so that my work is naturally organized into shared knowledge and active projects.

#### Acceptance Criteria

1. THE System SHALL create the following top-level structure inside SwarmWS on initialization:
   - Root files: `system-prompts.md`, `context-L0.md`, `context-L1.md`
   - Shared Knowledge folder: `Knowledge/` (with `Knowledge Base/`, `Notes/`, and `Memory/` sub-folders)
   - Active Work folder: `Projects/`
2. THE System SHALL create `context-L0.md` and `context-L1.md` inside `Knowledge/` and `Projects/` as section-level context files.
3. THE System SHALL create `index.md` and `knowledge-map.md` inside `Knowledge/` as system default files.
4. THE System SHALL mark `Knowledge/`, `Knowledge/Knowledge Base/`, `Knowledge/Notes/`, `Knowledge/Memory/`, `Projects/`, and all root-level files as System_Managed_Items.
5. THE System SHALL allow users to add files and subfolders inside any folder, subject to Depth_Guardrail limits.
6. THE System SHALL recreate any missing System_Managed_Items on application startup without overwriting existing content.

### Requirement 3: Shared Knowledge — Knowledge Domain

**User Story:** As a knowledge worker, I want a central Knowledge folder for reusable assets, working documents, and persistent memory, so that I can store durable knowledge outputs, ongoing notes, and accumulated insights distilled from my interactions across all projects.

#### Acceptance Criteria

1. THE System SHALL maintain a `Knowledge/` folder at the workspace root containing `context-L0.md`, `context-L1.md`, `index.md`, and `knowledge-map.md` as System_Managed_Items.
2. THE System SHALL maintain `Knowledge Base/`, `Notes/`, and `Memory/` sub-folders inside `Knowledge/` as System_Managed_Items.
3. THE System SHALL allow users to create subfolders and files inside `Knowledge Base/`, `Notes/`, and `Memory/`.
4. THE System SHALL enforce a maximum folder depth of 3 levels within `Knowledge/`.
5. WHEN a user attempts to create a folder that would exceed the 3-level depth limit inside `Knowledge/`, THE System SHALL block the creation and display a descriptive message.
6. THE System SHALL prevent deletion of the `Knowledge/` folder, its `Knowledge Base/`, `Notes/`, and `Memory/` sub-folders, and its system-managed files (`context-L0.md`, `context-L1.md`, `index.md`, `knowledge-map.md`).
7. THE System SHALL allow users to edit content within Memory/ items to correct or refine extracted understanding, while preventing deletion of the Memory/ folder itself.

### Requirement 6: Context Layering — L0 and L1 Files

**User Story:** As a knowledge worker, I want hierarchical context files at workspace, section, and project levels, so that agents can efficiently understand scope and relevance at each level without reading all content.

#### Acceptance Criteria

1. THE System SHALL maintain `context-L0.md` and `context-L1.md` at the workspace root level.
2. THE System SHALL maintain `context-L0.md` and `context-L1.md` inside `Knowledge/` and `Projects/`.
3. THE System SHALL maintain `context-L0.md` and `context-L1.md` inside each individual project directory.
4. THE System SHALL initialize `context-L0.md` files with a template prompting the user to provide a concise abstract (~1000 tokens).
5. THE System SHALL initialize `context-L1.md` files with a structured template containing sections for scope, goals, key knowledge, and relationships (~4k tokens).
6. THE System SHALL allow users to edit context file content but prevent deletion of context files.
7. THE System SHALL mark all context files as System_Managed_Items.

### Requirement 7: System vs User Content Ownership

**User Story:** As a knowledge worker, I want clear visual and behavioral distinction between system-managed and user-created content, so that I understand what I can modify freely and what the system maintains.

#### Acceptance Criteria

1. THE Workspace_Explorer SHALL display System_Managed_Items with a neutral icon and a non-deletable visual indicator (e.g., lock badge or muted styling).
2. THE Workspace_Explorer SHALL display User_Managed_Items with an accent icon and full CRUD action controls (add, rename, delete).
3. WHEN a user attempts to delete a System_Managed_Item, THE System SHALL block the deletion and display a tooltip: "This item is system-managed and cannot be deleted."
4. WHEN a user attempts to rename a System_Managed_Item, THE System SHALL block the rename and display a tooltip: "This item is system-managed and cannot be renamed."
5. THE System SHALL show CRUD actions (add, rename, delete) only on hover over User_Managed_Items to minimize visual clutter.
6. THE System SHALL allow users to edit the content of system-managed files (e.g., `context-L0.md`, `instructions.md`) while preventing structural changes (delete, rename).

### Requirement 8: Depth Guardrails Enforcement

**User Story:** As a knowledge worker, I want the system to enforce folder depth limits, so that the workspace remains navigable and agents can reason about the structure consistently.

#### Acceptance Criteria

1. THE System SHALL enforce a maximum folder depth of 3 levels within `Knowledge/`.
2. THE System SHALL enforce a maximum folder depth of 2 levels within project system folders (`chats/`, `research/`, `reports/`).
3. THE System SHALL enforce a maximum folder depth of 3 levels within user-created project subfolders.
4. WHEN a folder creation request would exceed the applicable depth limit, THE System SHALL reject the request and display a descriptive error message indicating the maximum allowed depth.
5. THE System SHALL enforce depth guardrails in both the UI (explorer) and the backend API (filesystem operations).

### Requirement 17: Backend — Single Workspace Data Model

**User Story:** As a developer, I want the backend data model simplified to support a single workspace, so that the codebase is cleaner and there is no ambiguity about workspace identity.

#### Acceptance Criteria

1. THE Backend SHALL remove the `swarm_workspaces` table from the SQLite database.
2. THE Backend SHALL store SwarmWS configuration (name, file_path, icon, context) in a `workspace_config` table with a single row or in the application configuration.
3. THE Backend SHALL remove all CRUD API endpoints for workspace management (`POST /swarm-workspaces`, `DELETE /swarm-workspaces/{id}`, `PUT /swarm-workspaces/{id}`, archive/unarchive endpoints).
4. THE Backend SHALL provide a single `GET /api/workspace` endpoint returning the SwarmWS configuration.
5. THE Backend SHALL provide a `PUT /api/workspace` endpoint for updating SwarmWS settings (context, icon).
6. THE Backend SHALL update all existing entities that reference `workspace_id` to either remove the foreign key or default it to a constant SwarmWS identifier.
7. THE Backend SHALL provide API endpoints for project CRUD operations: `GET /api/projects`, `POST /api/projects`, `GET /api/projects/{id}`, `PUT /api/projects/{id}`, `DELETE /api/projects/{id}`. Project `id` (UUID) SHALL be the primary path parameter for stability; `name` is a mutable display field.

### Requirement 19: Backend — Filesystem Operations with Depth Enforcement

**User Story:** As a developer, I want all filesystem operations to enforce depth guardrails, so that the workspace structure remains consistent regardless of how folders are created.

#### Acceptance Criteria

1. THE Backend SHALL validate folder depth before creating any new directory within SwarmWS.
2. THE Backend SHALL reject folder creation requests that would exceed the applicable depth limit and return an HTTP 400 error with a descriptive message.
3. THE Backend SHALL validate depth limits based on the parent context: 3 levels for Knowledge and user project subfolders, 2 levels for project system folders.
4. THE Backend SHALL prevent deletion of System_Managed_Items via API and return an HTTP 403 error with a descriptive message.
5. THE Backend SHALL prevent renaming of System_Managed_Items via API and return an HTTP 403 error with a descriptive message.

### Requirement 20: Backend — SwarmWorkspaceManager Refactor

**User Story:** As a developer, I want the SwarmWorkspaceManager refactored to support the single-workspace + projects model, so that the manager correctly initializes and maintains the new folder structure.

#### Acceptance Criteria

1. THE SwarmWorkspaceManager SHALL update `FOLDER_STRUCTURE` to include all new folders: `Knowledge/`, `Knowledge/Knowledge Base/`, `Knowledge/Notes/`, `Knowledge/Memory/`, `Projects/`, and their context files, plus Knowledge default files (`index.md`, `knowledge-map.md`).
2. THE SwarmWorkspaceManager SHALL remove all methods related to multi-workspace management: `archive()`, `unarchive()`, `delete()`, `list_non_archived()`, `list_all()`.
3. THE SwarmWorkspaceManager SHALL update `ensure_default_workspace()` to initialize SwarmWS with the new folder structure and root-level files.
4. THE SwarmWorkspaceManager SHALL add a `create_project(project_name)` method that scaffolds a new project using the Standard Project Template and returns the generated project metadata (including the UUID `id`).
5. THE SwarmWorkspaceManager SHALL add a `delete_project(project_id)` method that locates a project by its UUID and removes the project directory.
6. THE SwarmWorkspaceManager SHALL add a `get_project(project_id)` method that returns a single project's metadata by its UUID.
7. THE SwarmWorkspaceManager SHALL add a `list_projects()` method that returns metadata for all projects.
8. THE SwarmWorkspaceManager SHALL add a `validate_depth(target_path)` method that checks whether a new folder would exceed the applicable depth guardrail.

### Requirement 23: Onboarding — Sample Data

**User Story:** As a new user, I want SwarmWS to come pre-populated with realistic sample data on first launch, so that I can immediately understand how the Knowledge and project structure is intended to be used.

#### Acceptance Criteria

1. WHEN SwarmWS is initialized for the first time, THE System SHALL create at least one sample project under `Projects/` with realistic content in `instructions.md`, `research/`, and `reports/`.
2. WHEN SwarmWS is initialized for the first time, THE System SHALL populate `Knowledge/Knowledge Base/` with at least one sample knowledge asset file demonstrating intended usage.
3. WHEN SwarmWS is initialized for the first time, THE System SHALL populate `Knowledge/Notes/` with at least one sample note file demonstrating intended usage.
4. WHEN SwarmWS is initialized for the first time, THE System SHALL populate `Knowledge/Memory/` with at least one sample memory item demonstrating how persistent semantic memory is stored (e.g., a user preference or recurring theme extracted from interactions).
5. WHEN SwarmWS is initialized for the first time, THE System SHALL populate root-level `context-L0.md` and `context-L1.md` with meaningful default content describing the workspace.
6. WHEN SwarmWS is initialized for the first time, THE System SHALL populate `system-prompts.md` with a default system prompt template.
7. IF SwarmWS already contains user content, THE System SHALL skip sample data generation to avoid overwriting existing work.

### Requirement 24: Legacy Data Cleanup

**User Story:** As a developer, I want the system to cleanly remove legacy multi-workspace data on upgrade, so that the single-workspace model starts from a clean state without carrying over obsolete structures.

> **Note:** Since the product is still under active development with no production user data worth preserving, this requirement opts for a clean-slate approach instead of complex data migration.

#### Acceptance Criteria

1. WHEN the application detects an existing `swarm_workspaces` table, THE System SHALL drop the table and remove all associated legacy workspace directories from the filesystem.
2. WHEN the application detects existing chat thread records with `workspace_id` references, THE System SHALL clear the `workspace_id` field (set to NULL) so threads become global SwarmWS chats.
3. THE System SHALL log all cleanup actions for debugging purposes.
4. AFTER legacy data cleanup completes, THE System SHALL initialize a fresh SwarmWS structure as defined in Requirement 2.

### Requirement 25: Codebase Hygiene — Dead Code Removal

**User Story:** As a developer, I want all dead code related to the multi-workspace model removed, so that the codebase is clean and maintainable.

#### Acceptance Criteria

1. THE System SHALL remove all backend code related to multi-workspace CRUD: workspace creation, deletion, archiving, unarchiving, and listing multiple workspaces.
2. THE System SHALL remove all frontend components related to multi-workspace management: WorkspaceSelector, WorkspaceDropdown, WorkspaceCreateModal, ArchiveWorkspace controls, and related state management.
3. THE System SHALL remove all database migration code that is no longer needed after the single-workspace migration completes.
4. THE System SHALL remove the `swarmWorkspacesService` frontend service file.
5. THE System SHALL update all import statements and references that pointed to removed modules.
6. THE System SHALL remove or update all test files that test multi-workspace functionality.
7. THE System SHALL update all specification documents under `.kiro/specs/` that reference the old multi-workspace model to note they are superseded by this spec.

### Requirement 28: System Prompts File

**User Story:** As a knowledge worker, I want a `system-prompts.md` file at the workspace root, so that I can customize the base system prompt used by agents operating within SwarmWS.

#### Acceptance Criteria

1. THE System SHALL maintain a `system-prompts.md` file at the SwarmWS root as a System_Managed_Item.
2. THE System SHALL initialize `system-prompts.md` with a default system prompt template on first launch.
3. THE System SHALL allow users to edit the content of `system-prompts.md`.
4. THE System SHALL prevent deletion of `system-prompts.md`.
5. WHEN assembling agent context, THE System SHALL read `system-prompts.md` as the base system prompt (layer 1 in the context assembly order).

### Requirement 29: Workspace Initialization and Startup Integrity

**User Story:** As a knowledge worker, I want the system to verify and repair the workspace structure on every startup, so that missing system files or folders are automatically restored.

#### Acceptance Criteria

1. WHEN the application starts, THE System SHALL verify that all System_Managed_Items exist in SwarmWS.
2. IF any System_Managed_Item is missing, THE System SHALL recreate it with default content without overwriting existing files.
3. THE System SHALL log each recreated item for debugging purposes.
4. THE System SHALL complete workspace integrity verification before the UI becomes interactive.
5. IF the SwarmWS root directory does not exist, THE System SHALL create the entire workspace structure from scratch including sample data.

### Requirement 30: Folder Structure Round-Trip Integrity

**User Story:** As a developer, I want the workspace folder structure to be idempotent on initialization, so that running initialization multiple times produces the same result without data loss.

#### Acceptance Criteria

1. FOR ALL valid SwarmWS states, running workspace initialization followed by a second initialization SHALL produce an equivalent workspace structure (idempotence property).
2. THE System SHALL create missing System_Managed_Items without modifying existing ones during re-initialization.
3. THE System SHALL preserve all User_Managed_Items during re-initialization.
4. FOR ALL projects, reading `.project.json`, serializing it, and parsing it back SHALL produce an equivalent Project_Metadata object (round-trip property).