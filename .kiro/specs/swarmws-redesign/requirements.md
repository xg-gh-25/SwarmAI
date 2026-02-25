# Requirements Document

## Introduction

This document defines the requirements for redesigning the SwarmAI workspace system from a multi-workspace model to a **single-workspace + projects** model. SwarmWS becomes the sole, non-deletable workspace serving as the user's persistent, local-first knowledge and project operating root.

The redesign replaces the current multi-workspace CRUD system and file-tree explorer with a unified workspace structure organized into two semantic zones: Shared Knowledge (Knowledge) and Active Work (Projects). It introduces hierarchical context layering (L0/L1), depth guardrails, and a project-centric UX with focus mode.

This spec supersedes the previous workspace-refactor requirements at `.kiro/specs/swarm-workspaces-specs/workspace-refactor/requirements.md`, which was based on the older 6-phase Daily Work Operating Loop with multi-workspace + section navigation.

**Key Architectural Shifts:**
- Multi-workspace → Single workspace (SwarmWS)
- File-tree explorer → Semantic zone explorer with depth guardrails
- ContextFiles/ folder → Hierarchical L0/L1 context layering at workspace, section, and project levels
- Custom workspaces → Projects as primary organization unit
- Artifacts/ + Notebooks/ → Knowledge/ (with Knowledge Base/, Notes/, and Memory/ sub-folders)
- Transcripts/ → Removed (chats live inside projects)

## Glossary

- **SwarmWS**: The single, non-deletable root workspace. Serves as the persistent memory container for all SwarmAI work. Located at `{app_data_dir}/SwarmWS`.
- **Project**: A self-contained execution and knowledge container under `Projects/`. Each project has its own context files, instructions, chats, research, and reports. Replaces the concept of custom workspaces.
- **Knowledge**: The shared knowledge domain at the workspace root representing workspace-level shared semantic memory. Contains `Knowledge Base/` for durable reusable assets, `Notes/` for evolving working knowledge, and `Memory/` for persistent semantic memory distilled from user interactions. Replaces the former `Artifacts/` and `Notebooks/` folders.
- **Knowledge_Base**: A subfolder under `Knowledge/` for durable, reusable, high-confidence knowledge assets. Stores finalized or stable artifacts broadly reusable across projects and over time (frameworks, playbooks, templates, SOPs, approved strategy documents). Characterized by long-term validity, low volatility, and canonical reference status.
- **Notes**: A subfolder under `Knowledge/` for evolving working knowledge and exploratory documents. Captures in-progress thinking, research notes, drafts, and intermediate insights that may later graduate into durable Knowledge Base assets. Characterized by high iteration frequency and partial or exploratory content.
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
3. THE System SHALL mark `Knowledge/`, `Knowledge/Knowledge Base/`, `Knowledge/Notes/`, `Knowledge/Memory/`, `Projects/`, and all root-level files as System_Managed_Items.
4. THE System SHALL allow users to add files and subfolders inside any folder, subject to Depth_Guardrail limits.
5. THE System SHALL recreate any missing System_Managed_Items on application startup without overwriting existing content.

### Requirement 3: Shared Knowledge — Knowledge Domain
**User Story:** As a knowledge worker, I want a central Knowledge folder for reusable assets, working documents, and persistent memory, so that I can store durable knowledge outputs, ongoing notes, and accumulated insights distilled from my interactions across all projects.

#### Acceptance Criteria

1. THE System SHALL maintain a `Knowledge/` folder at the workspace root containing `context-L0.md`, `context-L1.md`, `index.md`, and `knowledge-map.md` as System_Managed_Items.
2. THE System SHALL maintain `Knowledge Base/`, `Notes/`, and `Memory/` sub-folders inside `Knowledge/` as System_Managed_Items.
3. THE System SHALL allow users to create subfolders and files inside `Knowledge Base/`, `Notes/`, and `Memory/`.
4. THE System SHALL enforce a maximum folder depth of 3 levels within `Knowledge/`.
5. WHEN a user attempts to create a folder that would exceed the 3-level depth limit inside `Knowledge/`, THE System SHALL block the creation and display a descriptive message.
6. THE System SHALL prevent deletion of the `Knowledge/` folder, its `Knowledge Base/`, `Notes/`, and `Memory/` sub-folders, and its system-managed files (`context-L0.md`, `context-L1.md`, `index.md`, `knowledge-map.md`).
7. THE System SHALL allow users to edit content within `Memory/` items to correct or refine extracted understanding, while preventing deletion of the `Memory/` folder itself.

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

### Requirement 9: Workspace Explorer Redesign — Header and Layout

**User Story:** As a knowledge worker, I want the workspace explorer to clearly present SwarmWS as my single workspace with easy search access, so that I can navigate my work efficiently.

#### Acceptance Criteria

1. THE Workspace_Explorer SHALL display "SwarmWS" as the header title, replacing the previous "Explorer" header.
2. THE Top_Bar SHALL display a centered global search bar for fuzzy search across projects, folders, and files within SwarmWS (consistent with the three-column layout spec where the Top Bar spans the full application width above all three columns).
3. THE Workspace_Explorer SHALL remove the workspace dropdown selector.
4. THE Workspace_Explorer SHALL remove the "Show Archived Workspaces" checkbox.
5. THE Workspace_Explorer SHALL remove the Global|SwarmWS toggle switch.
6. THE Workspace_Explorer SHALL remove the "New Workspace" button.
7. THE Workspace_Explorer SHALL remove the add-context area that previously appeared under the workspace selector.

### Requirement 10: Workspace Explorer — Semantic Zone Grouping

**User Story:** As a knowledge worker, I want the workspace tree organized into semantic zones, so that I can quickly distinguish between shared knowledge and my active projects.

#### Acceptance Criteria

1. THE Workspace_Explorer SHALL display the workspace tree grouped into two Semantic_Zones with visual separators:
   - "Shared Knowledge" — containing `Knowledge/`
   - "Active Work" — containing `Projects/`
2. THE Workspace_Explorer SHALL display zone labels as subtle, non-interactive separators between groups.
3. THE Workspace_Explorer SHALL display root-level files (`system-prompts.md`, `context-L0.md`, `context-L1.md`) above the first zone separator.
4. THE Workspace_Explorer SHALL collapse all subfolders by default on initial load.
5. THE Workspace_Explorer SHALL persist expand/collapse state per session.

### Requirement 11: Workspace Explorer — Progressive Disclosure

**User Story:** As a knowledge worker, I want the explorer to start simple and reveal detail on demand, so that I am not overwhelmed by the full workspace structure.

#### Acceptance Criteria

1. THE Workspace_Explorer SHALL display only top-level sections and zone separators in the default collapsed view.
2. WHEN a folder is clicked, THE Workspace_Explorer SHALL expand or collapse that folder to show or hide its contents.
3. THE Workspace_Explorer SHALL use subtle expand/collapse animations (150–200ms duration).
4. THE Workspace_Explorer SHALL preserve scroll position when expanding or collapsing folders.
5. THE Workspace_Explorer SHALL lazy-load deep folder contents to maintain responsiveness.

### Requirement 12: Workspace Explorer — Focus Mode

**User Story:** As a knowledge worker, I want a Focus Mode that highlights my current project, so that I can concentrate on active work without distraction from other sections.

#### Acceptance Criteria

1. WHEN a user opens or selects a project, THE Workspace_Explorer SHALL auto-expand the selected project's tree.
2. WHEN Focus_Mode is active, THE Workspace_Explorer SHALL collapse non-active project trees.
3. WHEN Focus_Mode is active, THE Workspace_Explorer SHALL keep the `Knowledge/` folder visible (collapsed but accessible).
4. THE Workspace_Explorer SHALL provide a toggle control labeled "Focus on Current Project" to enable or disable Focus_Mode.
5. WHEN Focus_Mode is disabled, THE Workspace_Explorer SHALL restore the previous expand/collapse state.

### Requirement 13: Workspace Explorer — Search

**User Story:** As a knowledge worker, I want a global search bar in the explorer, so that I can quickly find projects, folders, and files by name.

#### Acceptance Criteria

1. THE Top_Bar SHALL display a centered global search bar (see Requirement 9, AC 2). Search results SHALL be reflected in the Workspace_Explorer tree.
2. THE Search SHALL support fuzzy matching across project names, folder names, and file names within the SwarmWS filesystem. THE Search scope SHALL NOT include DB-canonical entities (chat threads, ToDos, tasks).
3. WHEN search results are displayed, THE Workspace_Explorer SHALL auto-expand the path to each matched node.
4. THE Workspace_Explorer SHALL highlight matched nodes in the tree.
5. WHEN the search query is cleared, THE Workspace_Explorer SHALL restore the previous expand/collapse state.

### Requirement 14: Workspace Explorer — Visual Design

**User Story:** As a knowledge worker, I want the explorer to feel calm and readable, so that I can work without visual fatigue.

#### Acceptance Criteria

1. THE Workspace_Explorer SHALL use consistent indentation per depth level with optional indentation guides.
2. THE Workspace_Explorer SHALL use slight font-weight differences to distinguish hierarchy levels.
3. THE Workspace_Explorer SHALL use calm, neutral background tones with soft separators instead of heavy borders.
4. THE Workspace_Explorer SHALL reserve accent colors for User_Managed_Items only.
5. THE Workspace_Explorer SHALL use a minimal icon set (e.g., `+`, `⋯`) for actions, shown only on hover.
6. THE Workspace_Explorer SHALL use CSS variables in `--color-*` format for all colors (no hardcoded color values).

### Requirement 15: Workspace Explorer — Scalability

**User Story:** As a knowledge worker, I want the explorer to remain responsive even with hundreds of projects and thousands of files, so that performance does not degrade as my workspace grows.

#### Acceptance Criteria

1. THE Workspace_Explorer SHALL use virtualized tree rendering to handle large file trees efficiently.
2. THE Workspace_Explorer SHALL maintain smooth scrolling and interaction responsiveness with at least 500 visible tree nodes.
3. THE Workspace_Explorer SHALL use efficient state management to minimize re-renders when expanding or collapsing folders.

### Requirement 16: Context Assembly Order (Agent Runtime)

**User Story:** As a knowledge worker, I want agents to assemble context in a predictable priority order, so that task-specific context takes precedence over global memory without losing long-term knowledge.

1. WHEN an agent executes within a project, THE System SHALL assemble context in the following order (highest priority first):
   1. Base system prompt (`system-prompts.md`)
   2. Current live work context (active chat thread, ToDos, tasks, files)
   3. Project intent and instructions (`instructions.md`)
   4. Project semantic context (`context-L0.md`, `context-L1.md`)
   5. Shared knowledge semantic context (`Knowledge/context-L0.md`, `Knowledge/context-L1.md`)
   6. Persistent semantic memory (`Knowledge/Memory/` — user preferences, recurring themes, historical decisions)
   7. Global workspace semantic context (`SwarmWS/context-L0.md`, `SwarmWS/context-L1.md`)
   8. Optional scoped retrieval within SwarmWS
2. THE System SHALL use L0 context files for fast relevance filtering before loading L1 context.
3. THE System SHALL respect a configurable maximum token budget for total injected context (default: 10K tokens).
4. IF the total assembled context exceeds the token budget, THE System SHALL truncate lower-priority layers first (starting from layer 8 upward).

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

### Requirement 26: Chat Threads and Projects

**User Story:** As a knowledge worker, I want chat threads to live inside projects, so that conversations are contextually bound to the work they relate to.

#### Acceptance Criteria

1. WHEN a chat is initiated from within a project context, THE System SHALL store the chat thread under the project's `chats/` directory.
2. THE System SHALL organize chat threads in subdirectories within `chats/` (e.g., `chats/thread_001/`).
3. THE System SHALL enforce the 2-level depth guardrail within the `chats/` folder.
4. WHEN a chat is initiated outside of any project context (e.g., from the workspace root), THE System SHALL associate the chat with SwarmWS globally rather than a specific project.
5. THE System SHALL update the chat thread database records to reference a `project_id` (UUID from `.project.json`) instead of a `workspace_id`. Threads not associated with any project SHALL have `project_id` set to NULL, indicating a global SwarmWS chat.

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

### Requirement 32: Project Metadata Schema Versioning

**User Story:** As a developer, I want `.project.json` to carry a schema version, so that the system can gracefully handle metadata format changes across application updates.

#### Acceptance Criteria

1. THE `.project.json` SHALL include a `schema_version` field using semantic versioning (e.g., `"1.0.0"`).
2. WHEN the application reads a `.project.json` with a `schema_version` older than the current expected version, THE System SHALL apply forward-compatible migrations to bring the metadata up to date.
3. WHEN a schema migration is applied, THE System SHALL append an `update_history` entry with action `schema_migrated`, source `system`, and changes describing the migration (e.g., `{"schema_version": {"from": "1.0.0", "to": "1.1.0"}}`).
4. THE System SHALL NOT modify `.project.json` files with a `schema_version` newer than the current application version (forward compatibility — read but do not downgrade).
5. THE System SHALL define schema migration functions in a dedicated module (e.g., `backend/core/project_schema_migrations.py`) to keep migration logic isolated and testable.
6. FOR ALL supported schema versions, migrating from version N to version N+1 and then reading the result SHALL produce a valid `.project.json` conforming to version N+1 (migration correctness property).

### Requirement 33: Context Assembly Preview API

**User Story:** As a knowledge worker, I want to preview the context that an agent would see for a given project and chat thread, so that I can understand and trust what information the agent is working with (Visible Planning Builds Trust).

#### Acceptance Criteria

1. THE Backend SHALL provide a `GET /api/projects/{id}/context` endpoint that returns the assembled context for a project, following the context assembly order defined in Requirement 16.
2. THE response SHALL include each context layer with its source path, token count, and content preview (truncated to a configurable limit).
3. THE response SHALL indicate the total token count and whether any layers were truncated due to the token budget.
4. THE Backend SHALL accept an optional `thread_id` query parameter to include the specific chat thread's live context (layer 2 in the assembly order).
5. THE Frontend SHALL display the context preview in a collapsible panel accessible from the project detail view or chat interface.
6. THE context preview SHALL update in near-real-time as context files are modified.
7. THE Backend SHALL return the context assembly response using snake_case field names (Python/Pydantic convention).
