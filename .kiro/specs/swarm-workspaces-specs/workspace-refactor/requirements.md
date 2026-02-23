# Requirements Document

## Introduction

This document defines the requirements for refactoring the SwarmAI workspace system to implement the "Daily Work Operating Loop" architecture. The refactor transforms the current file-tree-based workspace explorer into a section-based navigation system following the six phases of the Daily Work Operating Loop: Signals → Plan → Execute → Communicate → Artifacts → Reflection.

The goal is to create a unified work operating system where SwarmWS serves as the permanent root workspace (Global Daily Work Operating System) and custom workspaces provide focused domain/project environments. This refactor includes both UI/UX changes and backend data model enhancements.

**Key Architectural Principle**: The database is canonical for structured entities (Tasks, ToDos, PlanItems, Communications, ChatThreads). The filesystem stores content only (Artifacts, Reflections, Context files). The UI uses section-based navigation, not file-tree navigation.

## Glossary

- **SwarmWS**: The built-in, non-deletable Root Workspace that serves as the Global Daily Work Operating System. Always pinned at the top of the workspace list.
- **Custom_Workspace**: User-created workspaces for focused domain or project work (e.g., TestWS). Can be created, edited, archived, and deleted. Archived workspaces are read-only and excluded from default aggregation.
- **Daily_Work_Operating_Loop**: The six-phase cycle that structures knowledge work: Signals → Plan → Execute → Communicate → Artifacts → Reflection.
- **Signal**: The UI display term for incoming work items in the Signals section. Technically stored as ToDo entities in the database. UI rule: Display "Signal" label in cards and section headers; use "ToDo" for developer/API documentation only.
- **ToDo**: The database entity representing a structured intent signal with state tracking (Pending, Overdue, In Discussion, Handled, Cancelled, Deleted). "Signal" is the UI term; "ToDo" is the technical entity name used in API endpoints and code.
- **Task**: A database entity representing an execution thread with agent orchestration. States: Draft, WIP, Blocked, Completed, Cancelled. DB-canonical, not file-based.
- **PlanItem**: A database entity representing prioritized work items. States: Today's Focus, Upcoming, Blocked, Deferred. DB-canonical, not file-based. Can be workspace-scoped (local) or SwarmWS-scoped (global/cross-domain).
- **Communication**: A database entity for tracking alignment work with stakeholders. States: Pending Reply, AI Draft, Follow-up, Sent. DB-canonical, not file-based.
- **Artifact**: A durable knowledge output produced from task execution (Plans, Reports, Docs, Decision records). Content stored as files in filesystem; metadata tracked in database.
- **Reflection**: A structured review item capturing progress, insights, and lessons learned. Content stored as files; metadata in database. Types: Daily Recap, Weekly Summary, Lessons Learned.
- **ChatThread**: A database entity for conversation threads bound to a workspace and optionally to a Task or ToDo. Supports explore (lightweight) and execute (structured) modes. DB-canonical.
- **ThreadSummary**: A database entity containing AI-generated summaries of chat threads. Used for search indexing instead of raw messages.
- **Workspace_Explorer**: The middle column UI component that displays workspace navigation and section-based content.
- **Section_Navigation**: The six collapsible sections within each workspace representing the Daily Work Operating Loop phases.
- **Workspace_Scope**: The currently selected workspace context that filters displayed content.
- **Skill**: A modular capability package (SKILL.md file) that extends agent functionality. Skills are stored globally and enabled/disabled per workspace. May be marked as privileged (requiring explicit enablement).
- **MCP_Server**: Model Context Protocol server providing external tool integrations. MCPs are stored globally and enabled/disabled per workspace. May be marked as privileged.
- **Knowledgebase**: A collection of indexed knowledge sources available to agents. Uses union model with optional exclusions.
- **Effective_Configuration**: The computed runtime configuration for a workspace, derived from inheritance rules.
- **Context_File**: Markdown files in the workspace's ContextFiles/ folder that provide workspace-specific context to agents.
- **Privileged_Capability**: A Skill or MCP marked as requiring explicit user confirmation to enable due to elevated permissions or risk.


## Configuration Inheritance Models

The workspace configuration system uses two inheritance models:

### 1. Intersection Model (Skills & MCPs)

Custom workspaces can only disable capabilities available in SwarmWS, never add new ones.

```
effective = swarmws_allowed ∩ workspace_allowed
```

**Privileged Capabilities**: Some Skills/MCPs are marked as `is_privileged = true`. These require explicit user confirmation to enable, even in SwarmWS. SwarmWS enables all "safe-by-default" capabilities automatically; privileged ones require explicit enablement.

### 2. Union Model with Exclusions (Knowledgebases)

Custom workspaces can add additional knowledge sources and optionally exclude inherited sources.

**Two-step algorithm:**
1. Start with union: `effective = swarmws_sources ∪ workspace_sources`
2. Apply workspace exclusions: `effective = effective - workspace_excluded_sources`

The `workspace_knowledgebases` table includes an `excluded_sources` field to track which SwarmWS sources are excluded. UI shows "Inherited sources" that can be toggled off if policy allows.

This ensures custom workspaces are always "stricter" for execution capabilities while allowing domain-specific knowledge enrichment with controlled inheritance.

## Requirements

### Requirement 1: SwarmWS Root Workspace Behavior

**User Story:** As a knowledge worker, I want SwarmWS to always be available as my global work hub, so that I have a persistent cockpit for managing all my daily work.

#### Acceptance Criteria

1. THE System SHALL display SwarmWS as the first workspace in the workspace list, always pinned at the top.
2. THE System SHALL prevent deletion of SwarmWS and display a visual indicator (lock icon) showing it is non-deletable.
3. WHEN a new Signal or ToDo is created without workspace assignment, THE System SHALL assign it to SwarmWS by default.
4. WHEN a new Task is created without workspace assignment, THE System SHALL assign it to SwarmWS by default.
5. THE System SHALL create SwarmWS automatically on first application launch if it does not exist.
6. THE System SHALL display SwarmWS with a distinctive icon (🏠) and subtitle "Your Global Work Hub".


### Requirement 2: Custom Workspace Management

**User Story:** As a user, I want to create custom workspaces for specific projects or domains, so that I can organize my work into focused environments.

#### Acceptance Criteria

1. THE System SHALL allow users to create custom workspaces with name, context description, and optional icon.
2. THE System SHALL create a TestWS workspace with mock data for demonstration purposes during development.
3. WHEN a custom workspace is created, THE System SHALL create the internal storage layout folders: Artifacts/ (with type subfolders: Plans/, Reports/, Docs/, Decisions/), ContextFiles/.
4. THE System SHALL allow users to edit custom workspace name, context, and icon.
5. THE System SHALL allow users to delete custom workspaces (excluding SwarmWS).
6. WHEN a custom workspace is deleted, THE System SHALL prompt for confirmation and warn about data loss.
7. THE System SHALL NOT create filesystem folders for DB-canonical entities (Tasks, ToDos, PlanItems, Communications, ChatThreads are stored in database only).

### Requirement 3: Workspace Explorer Section Navigation

**User Story:** As a user, I want to navigate my workspace through the six Daily Work Loop sections, so that I can manage my work according to the natural flow of knowledge work.

#### Acceptance Criteria

1. THE Workspace_Explorer SHALL replace the current file tree with a structured layout containing: Header Area, Overview/Context Card, and Six Collapsible Section Headers (Signals, Plan, Execute, Communicate, Artifacts, Reflection).
2. THE Workspace_Explorer Header Area SHALL contain:
   - Workspace selector dropdown (SwarmWS pinned at top with 🏠 icon, custom workspaces with 📁 icon)
   - View/Scope toggle (for SwarmWS: "Global (All Workspaces)" vs "SwarmWS-only"; for custom workspaces: "This Workspace" vs "All Workspaces")
   - Global search bar with placeholder "Search… (threads, tasks, signals, artifacts)"
3. THE Workspace_Explorer SHALL display an Overview/Context Card below the header showing:
   - Goal: workspace goal statement
   - Focus: current focus area
   - Context: workspace description
   - Priorities: bullet list of current priorities
   - [Edit Context] button that syncs to ContextFiles/context.md
4. WHEN a workspace is selected, THE Workspace_Explorer SHALL display the six sections with item counts for each section.
5. WHEN a section header is clicked, THE Workspace_Explorer SHALL expand or collapse that section to show or hide its contents.
6. THE Workspace_Explorer SHALL display the Signals section (🔔 icon) with sub-categories: Pending, Overdue, In Discussion, and a "+ Quick Capture" action.
7. THE Workspace_Explorer SHALL display the Plan section (🗓️ icon) with sub-categories: Today's Focus, Upcoming, Blocked.
8. THE Workspace_Explorer SHALL display the Execute section (▶️ icon) with sub-categories: Draft, WIP, Blocked, Completed.
9. THE Workspace_Explorer SHALL display the Communicate section (💬 icon) with sub-categories: Pending Replies, AI Drafts, Follow-ups.
10. THE Workspace_Explorer SHALL display the Artifacts section (📦 icon) with sub-categories: Plans, Reports, Docs, Decisions, and a collapsible File Tree sub-section for browsing workspace filesystem.
11. THE Workspace_Explorer SHALL display the Reflection section (🧠 icon) with sub-categories: Daily Recap, Weekly Summary, Lessons Learned.
12. WHEN "All Workspaces" scope is selected, THE Workspace_Explorer SHALL aggregate items from all non-archived workspaces grouped by section.
13. THE UI SHALL use "Signals" as the section header but display individual items with "Signal" label in cards (use "ToDo" for developer/API documentation only).
14. THE Workspace_Explorer Footer Area SHALL contain:
    - "+ New Workspace" button that opens the existing WorkspacesModal from left sidebar
    - "⚙️ Workspace Settings" button that opens the existing SettingsModal from left sidebar
    - For custom workspaces: "Archive / Delete Workspace" option in a context menu (⋯)


### Requirement 4: Signals/ToDo Data Model

**User Story:** As a developer, I want a proper data model for Signals/ToDos, so that the system can track incoming work items with appropriate state management.

#### Acceptance Criteria

1. THE System SHALL store ToDo entities in the database (DB-canonical) with fields: id, workspace_id, title, description, source, source_type, status, priority, due_date, created_at, updated_at.
2. THE System SHALL support ToDo status values: pending, overdue, in_discussion, handled, cancelled, deleted.
3. THE System SHALL support ToDo source_type values: manual, email, slack, meeting, integration.
4. THE System SHALL support ToDo priority values: high, medium, low, none.
5. PRIMARY MECHANISM: THE System SHALL run an hourly background job that scans all ToDos where due_date has passed and status is "pending", updating their status to "overdue" in the database.
6. SECONDARY MECHANISM (UI consistency): WHEN reading a ToDo where due_date has passed but status is still "pending", THE API MAY temporarily mark it as "overdue" in the response AND asynchronously trigger a DB update to correct the status.
7. THE System SHALL allow converting a ToDo to a Task, linking the original ToDo to the created Task.
8. WHEN a ToDo is converted to a Task, THE System SHALL update the ToDo status to handled and store the task_id reference.
9. THE System SHALL NOT store ToDos as files in the filesystem (database is canonical).

### Requirement 5: Task Data Model Enhancement

**User Story:** As a developer, I want an enhanced Task data model that supports the Execute section workflow, so that tasks can be properly tracked through their lifecycle.

#### Acceptance Criteria

1. THE System SHALL store Task entities in the database (DB-canonical) with fields: id, workspace_id, agent_id, session_id, title, description, status, priority, source_todo_id, blocked_reason, created_at, started_at, completed_at, updated_at.
2. THE System SHALL support Task status values: draft, wip, blocked, completed, cancelled (replacing current pending, running, completed, failed, cancelled).
3. THE System SHALL support Task priority values: high, medium, low, none.
4. THE System SHALL maintain backward compatibility by mapping existing task statuses: pending→draft, running→wip, failed→blocked.
5. WHEN a Task status is set to blocked (from failed), THE System SHALL preserve the failure context in blocked_reason field.
6. WHEN a Task is created from a ToDo, THE System SHALL store the source_todo_id reference.
7. THE System SHALL allow Tasks to be assigned to a specific workspace_id.
8. THE System SHALL NOT store Tasks as files in the filesystem (database is canonical).


### Requirement 6: Backend API Endpoints for Signals/ToDos

**User Story:** As a frontend developer, I want API endpoints for managing Signals/ToDos, so that the UI can perform CRUD operations on incoming work items.

#### Acceptance Criteria

1. THE API SHALL provide GET /api/todos endpoint to list all ToDos with optional workspace_id and status filters.
2. THE API SHALL provide POST /api/todos endpoint to create a new ToDo.
3. THE API SHALL provide GET /api/todos/{id} endpoint to retrieve a specific ToDo.
4. THE API SHALL provide PUT /api/todos/{id} endpoint to update a ToDo.
5. THE API SHALL provide DELETE /api/todos/{id} endpoint to soft-delete a ToDo (set status to deleted).
6. THE API SHALL provide POST /api/todos/{id}/convert-to-task endpoint to convert a ToDo to a Task.
7. THE API SHALL return responses using snake_case field names (Python/Pydantic convention).
8. THE API SHALL support pagination with limit/offset parameters for list endpoints.

### Requirement 7: Backend API Endpoints for Section Data

**User Story:** As a frontend developer, I want API endpoints that return section-organized data, so that the UI can efficiently render the Daily Work Loop sections.

#### Acceptance Criteria

1. THE API SHALL provide GET /api/workspaces/{id}/sections endpoint returning aggregated counts for all six sections.
2. THE API SHALL provide GET /api/workspaces/{id}/sections/signals endpoint returning ToDos grouped by status sub-category.
3. THE API SHALL provide GET /api/workspaces/{id}/sections/plan endpoint returning PlanItems grouped by focus_type sub-category.
4. THE API SHALL provide GET /api/workspaces/{id}/sections/execute endpoint returning Tasks grouped by status sub-category.
5. THE API SHALL provide GET /api/workspaces/{id}/sections/communicate endpoint returning Communications grouped by status sub-category.
6. THE API SHALL provide GET /api/workspaces/{id}/sections/artifacts endpoint returning Artifacts grouped by artifact_type sub-category.
7. THE API SHALL provide GET /api/workspaces/{id}/sections/reflection endpoint returning Reflections grouped by reflection_type sub-category.
8. WHEN workspace_id is "all", THE API SHALL aggregate data across all non-archived workspaces the user has access to.
9. THE API SHALL include item counts in section responses for badge display.
10. ALL section list endpoints SHALL support pagination with limit/offset parameters (default limit: 50).
11. ALL section endpoints SHALL return the unified response contract as defined in Requirement 33.
12. THE "All Workspaces" aggregation SHALL use database indexes for performance.


### Requirement 8: Frontend TypeScript Types

**User Story:** As a frontend developer, I want TypeScript interfaces for the new data models, so that I can build type-safe UI components.

#### Acceptance Criteria

1. THE Frontend SHALL define a ToDo interface with camelCase field names matching the backend snake_case fields.
2. THE Frontend SHALL define ToDoStatus type: 'pending' | 'overdue' | 'inDiscussion' | 'handled' | 'cancelled' | 'deleted'.
3. THE Frontend SHALL define ToDoSourceType type: 'manual' | 'email' | 'slack' | 'meeting' | 'integration'.
4. THE Frontend SHALL define Priority type: 'high' | 'medium' | 'low' | 'none'.
5. THE Frontend SHALL define WorkspaceSection type: 'signals' | 'plan' | 'execute' | 'communicate' | 'artifacts' | 'reflection'.
6. THE Frontend SHALL define SectionCounts interface with counts for each section and sub-category.
7. THE Frontend SHALL update the Task interface to include workspaceId, sourceTodoId, and blockedReason fields.
8. THE Frontend SHALL update toCamelCase functions in services to handle new ToDo fields.
9. THE Frontend SHALL define SectionResponse interface with standard shape: { counts, groups, pagination, sortKeys, lastUpdatedAt }.

### Requirement 9: Workspace Explorer UI Redesign

**User Story:** As a user, I want a redesigned workspace explorer that shows the Daily Work Loop sections with visible context, so that I can navigate my work intuitively and always know what I'm working on.

#### Acceptance Criteria

1. THE Workspace_Explorer SHALL display a workspace selector dropdown at the top showing SwarmWS (🏠 pinned) and custom workspaces (📁 icon).
2. THE Workspace_Explorer SHALL display a View/Scope toggle next to the workspace selector:
   - For SwarmWS: "Global (All Workspaces)" (default) vs "SwarmWS-only"
   - For custom workspaces: "This Workspace" (default) vs "All Workspaces"
3. THE Workspace_Explorer SHALL display a global search bar below the workspace selector with placeholder text "Search… (threads, tasks, signals, artifacts)" that searches across all entity types.
4. THE Workspace_Explorer SHALL display an Overview/Context Card below the search bar containing:
   - Goal field (editable)
   - Focus field (editable)
   - Context/Description field (editable)
   - Priorities list (editable, bullet points)
   - [Edit Context] button that opens inline editing and syncs changes to ContextFiles/context.md
5. THE Workspace_Explorer SHALL display six section headers below the context card, each with an icon and item count badge.
6. THE Workspace_Explorer SHALL use the following icons for sections: Signals (🔔), Plan (🗓️), Execute (▶️), Communicate (💬), Artifacts (📦), Reflection (🧠).
7. WHEN a section is expanded, THE Workspace_Explorer SHALL display sub-category items with their respective counts and sample item titles (max 2-3 items per sub-category).
8. WHEN an item in a section is clicked, THE System SHALL navigate to the appropriate detail view or page.
9. THE Workspace_Explorer SHALL highlight the currently active section.
10. THE Workspace_Explorer SHALL support keyboard navigation between sections and items.
11. THE Artifacts section SHALL include a collapsible "File Tree" sub-section that displays the workspace filesystem (Artifacts/, ContextFiles/, Transcripts/) for direct file browsing.
12. THE Workspace_Explorer Footer SHALL display:
    - "+ New Workspace" button that opens the existing WorkspacesModal
    - "⚙️ Workspace Settings" (or "⚙️ SwarmWS Settings" for root workspace) button that opens the existing SettingsModal
13. FOR custom workspaces, THE Footer SHALL include a context menu (⋯) with "Archive Workspace" and "Delete Workspace" options.
14. THE Workspace_Explorer counts and badges SHALL reflect the current View/Scope mode:
    - Global View: aggregated totals across all non-archived workspaces
    - Scoped View: totals for the selected workspace only

### Requirement 10: TasksPage Rename and Reposition

**User Story:** As a user, I want the current Tasks page to be clearly identified as the Execute section view, so that I understand it shows agent execution tasks.

#### Acceptance Criteria

1. THE System SHALL rename the current TasksPage component to ExecuteTasksPage internally.
2. THE System SHALL update the page title from "Tasks" to "Execute" or "Execution Tasks".
3. THE System SHALL update navigation to access ExecuteTasksPage from the Execute section in the workspace explorer.
4. THE System SHALL add workspace_id filter to ExecuteTasksPage to show tasks for the selected workspace.
5. WHEN "All Workspaces" is selected, THE ExecuteTasksPage SHALL display tasks from all workspaces.
6. THE System SHALL update the task status filter to use new status values: draft, wip, blocked, completed, cancelled.


### Requirement 11: SignalsPage Creation

**User Story:** As a user, I want a dedicated Signals page to manage incoming work items, so that I can triage and process my work signals.

#### Acceptance Criteria

1. THE System SHALL create a new SignalsPage component for managing ToDos.
2. THE SignalsPage SHALL display ToDos in a table with columns: Title, Source, Status, Priority, Due Date, Actions.
3. THE SignalsPage SHALL provide filters for status and priority.
4. THE SignalsPage SHALL provide a search bar for filtering by title or description.
5. THE SignalsPage SHALL provide a "Quick Capture" button to create new ToDos.
6. THE SignalsPage SHALL provide actions: Edit, Convert to Task, Delete for each ToDo.
7. WHEN "Convert to Task" is clicked, THE System SHALL open a dialog to configure the new Task and create it.
8. THE SignalsPage SHALL add workspace_id filter to show ToDos for the selected workspace.

### Requirement 12: Mock Data Generation

**User Story:** As a developer, I want mock data for testing the Daily Work Loop features, so that I can verify the UI and functionality work correctly.

#### Acceptance Criteria

1. THE System SHALL generate mock ToDos for SwarmWS with various statuses and priorities.
2. THE System SHALL generate mock Tasks for SwarmWS with various statuses.
3. THE System SHALL create a TestWS workspace with its own set of mock ToDos and Tasks.
4. THE Mock data SHALL include realistic titles and descriptions representing knowledge work scenarios.
5. THE Mock data generation SHALL be triggered via a development-only API endpoint or initialization flag.
6. IF mock data already exists, THE System SHALL skip generation to avoid duplicates.

### Requirement 13: Database Schema Updates

**User Story:** As a developer, I want the database schema updated to support the new data models, so that data can be persisted correctly.

#### Acceptance Criteria

1. THE Database SHALL create a todos table with columns: id, workspace_id, title, description, source, source_type, status, priority, due_date, task_id, created_at, updated_at.
2. THE Database SHALL add workspace_id column to the tasks table.
3. THE Database SHALL add source_todo_id column to the tasks table.
4. THE Database SHALL add blocked_reason column to the tasks table.
5. THE Database SHALL create indexes on todos.workspace_id and todos.status for query performance.
6. THE Database SHALL create indexes on tasks.workspace_id for query performance.
7. THE Database migration SHALL preserve existing task data during schema update.
8. THE Database migration SHALL set workspace_id to SwarmWS.id for all existing tasks that have NULL workspace_id.


### Requirement 14: Workspace Context Injection

**User Story:** As a user, I want my workspace context automatically included when chatting with agents, so that agents understand my current work context.

#### Acceptance Criteria

1. WHEN a chat is initiated from a workspace section, THE System SHALL include the workspace context in the agent's system prompt.
2. THE System SHALL read context from the workspace's ContextFiles/context.md file.
3. IF the workspace has a ContextFiles/compressed-context.md file, THE System SHALL prefer it over context.md for injection.
4. IF compressed-context.md is not present or stale, THE System SHALL fall back to context.md (if under token budget).
5. THE Context injection SHALL be limited to a configurable maximum token budget (default: 4000 tokens) to prevent context overflow.
6. THE Context injection token budget SHALL be configurable per workspace via workspace settings.
7. THE Context injection SHALL be prefixed with "Current Workspace: {workspace_name}" header.
8. THE System SHALL include the workspace's effective Skills, MCPs, and Knowledgebases summary in the context injection.
9. THE System SHALL regenerate compressed-context.md when: context.md is updated, on execution start if stale (>24 hours), or manually triggered.

### Requirement 15: Navigation and Routing

**User Story:** As a user, I want consistent navigation between workspace sections and their detail pages, so that I can move through my work efficiently.

#### Acceptance Criteria

1. THE System SHALL update routing to include /signals path for the SignalsPage.
2. THE System SHALL update routing to support workspace-scoped paths: /workspaces/{id}/signals, /workspaces/{id}/execute.
3. WHEN navigating from workspace explorer to a section, THE System SHALL preserve the selected workspace scope.
4. THE System SHALL update the sidebar navigation to reflect the new page structure.
5. THE System SHALL support deep linking to specific workspace sections.

### Requirement 16: Workspace-Specific Skills Configuration

**User Story:** As a user, I want each workspace to have its own set of Skills, so that I can customize agent capabilities for different projects or domains.

#### Acceptance Criteria

1. THE System SHALL enable all "safe-by-default" Skills for SwarmWS (Root Workspace) automatically.
2. THE System SHALL mark some Skills as privileged (is_privileged = true) requiring explicit user confirmation to enable.
3. THE System SHALL allow users to configure which Skills are enabled or disabled for custom workspaces.
4. THE System SHALL enforce that custom workspaces can only disable Skills available in SwarmWS, not enable Skills beyond SwarmWS.
5. WHEN an agent executes in a workspace, THE System SHALL compute effective Skills as the intersection of SwarmWS allowed Skills and workspace allowed Skills.
6. THE System SHALL store Skills configuration per workspace using a junction table (workspace_skills) referencing global Skills.
7. THE System SHALL provide a UI in workspace settings to manage Skills enablement with toggle switches.
8. WHEN a Skill is disabled in SwarmWS, THE System SHALL automatically disable it in all custom workspaces.
9. WHEN a new Skill is installed, THE System SHALL automatically enable it in SwarmWS only if not privileged.
10. WHEN a new Skill is installed, THE System SHALL NOT automatically enable it in custom workspaces (user must explicitly enable).
11. WHEN enabling a privileged Skill, THE System SHALL display a confirmation dialog explaining the elevated permissions.


### Requirement 17: Workspace-Specific MCP Server Configuration

**User Story:** As a user, I want each workspace to have its own MCP server configurations, so that I can connect different tools and integrations per project.

#### Acceptance Criteria

1. THE System SHALL enable all "safe-by-default" MCP servers for SwarmWS (Root Workspace) automatically.
2. THE System SHALL mark some MCP servers as privileged (is_privileged = true) requiring explicit user confirmation to enable.
3. THE System SHALL allow users to enable or disable specific MCP servers for custom workspaces.
4. THE System SHALL enforce that custom workspaces can only disable MCP servers available in SwarmWS, not add new ones beyond SwarmWS.
5. WHEN an agent executes in a workspace, THE System SHALL compute effective MCP servers as the intersection of SwarmWS allowed MCPs and workspace allowed MCPs.
6. THE System SHALL store MCP server configuration per workspace using a junction table (workspace_mcps) referencing global MCP servers.
7. THE System SHALL provide a UI in workspace settings to manage MCP server enablement with toggle switches.
8. WHEN an MCP server is disabled in SwarmWS, THE System SHALL automatically disable it in all custom workspaces.
9. WHEN a new MCP server is configured, THE System SHALL automatically enable it in SwarmWS only if not privileged.
10. WHEN a new MCP server is configured, THE System SHALL NOT automatically enable it in custom workspaces (user must explicitly enable).
11. WHEN enabling a privileged MCP server, THE System SHALL display a confirmation dialog explaining the elevated permissions.

### Requirement 18: Workspace-Specific Knowledgebase Configuration

**User Story:** As a user, I want each workspace to have its own Knowledgebase sources, so that agents can access domain-specific knowledge for different projects.

#### Acceptance Criteria

1. THE System SHALL provide SwarmWS access to global/shared Knowledgebase sources.
2. THE System SHALL allow custom workspaces to add workspace-specific Knowledgebase sources.
3. THE System SHALL support Knowledgebase source types: local_file, url, indexed_document, context_file, vector_index.
4. WHEN an agent executes in a workspace, THE System SHALL compute effective Knowledgebase using the two-step algorithm: (1) union of SwarmWS and workspace sources, (2) minus workspace excluded sources.
5. THE System SHALL store Knowledgebase configuration per workspace in the database with source_type, source_path, and metadata fields.
6. THE System SHALL store excluded_sources field in workspace_knowledgebases to track which inherited sources are excluded.
7. THE System SHALL provide a UI in workspace settings to add, edit, and remove Knowledgebase sources.
8. THE System SHALL display "Inherited sources" from SwarmWS with toggle to exclude them (if policy allows).
9. WHEN retrieving Knowledgebase sources, THE System SHALL prioritize workspace-specific sources over SwarmWS sources when conflicts exist.s when conflicts exist.


### Requirement 19: Workspace Configuration Data Model

**User Story:** As a developer, I want a data model for workspace configurations, so that Skills, MCPs, and Knowledgebases can be persisted per workspace.

#### Acceptance Criteria

1. THE Database SHALL create a workspace_skills table with columns: id, workspace_id, skill_id, enabled, created_at, updated_at.
2. THE Database SHALL add is_privileged column to the skills table (default false).
3. THE Database SHALL create a workspace_mcps table with columns: id, workspace_id, mcp_server_id, enabled, created_at, updated_at.
4. THE Database SHALL add is_privileged column to the mcp_servers table (default false).
5. THE Database SHALL create a workspace_knowledgebases table with columns: id, workspace_id, source_type, source_path, display_name, metadata, excluded_sources (JSON array storing KnowledgebaseSource IDs as integers, NOT file paths), created_at, updated_at.
6. THE API SHALL provide CRUD endpoints for workspace Skills configuration: GET/PUT /api/workspaces/{id}/skills.
7. THE API SHALL provide CRUD endpoints for workspace MCP configuration: GET/PUT /api/workspaces/{id}/mcps.
8. THE API SHALL provide CRUD endpoints for workspace Knowledgebase configuration: GET/POST/PUT/DELETE /api/workspaces/{id}/knowledgebases.
9. THE Frontend SHALL define TypeScript interfaces: WorkspaceSkillConfig, WorkspaceMcpConfig, WorkspaceKnowledgebaseConfig.

### Requirement 20: Workspace Configuration UI

**User Story:** As a user, I want a settings panel for each workspace to configure Skills, MCPs, and Knowledgebases, so that I can customize my workspace environment.

#### Acceptance Criteria

1. THE System SHALL provide workspace settings accessible from the workspace dropdown context menu or a settings icon.
2. THE Workspace settings panel SHALL display three tabs: Skills, MCPs, Knowledgebases.
3. THE Skills tab SHALL display all available Skills with toggle switches for enabling/disabling, with privileged Skills marked with a warning icon.
4. THE MCPs tab SHALL display all configured MCP servers with toggle switches for enabling/disabling, with privileged MCPs marked with a warning icon.
5. THE Knowledgebases tab SHALL display current sources with add/edit/remove actions, and inherited sources with exclude toggles.
6. THE System SHALL display visual indicators distinguishing inherited settings (from SwarmWS) versus workspace-specific overrides.
7. WHEN viewing SwarmWS settings, THE System SHALL show "All enabled" state with toggles disabled for core items that cannot be disabled.
8. THE System SHALL display a warning when disabling Skills or MCPs that may affect agent functionality.
9. WHEN enabling a privileged capability, THE System SHALL show a confirmation dialog explaining the risks.


### Requirement 21: Agent Context Injection with Workspace Configuration

**User Story:** As a user, I want agents to automatically use my workspace's configured Skills, MCPs, and Knowledgebases, so that execution respects my workspace customization.

#### Acceptance Criteria

1. WHEN an agent executes in a workspace, THE System SHALL only make enabled Skills available to the agent.
2. WHEN an agent executes in a workspace, THE System SHALL only connect enabled MCP servers.
3. WHEN an agent executes in a workspace, THE System SHALL include workspace Knowledgebase sources in the agent's context.
4. THE System SHALL inject workspace configuration metadata into the agent's system prompt, including workspace name and enabled capabilities summary.
5. THE System SHALL log which Skills, MCPs, and Knowledgebases were used for each agent execution in the audit trail.
6. IF a required Skill or MCP is disabled for a workspace, THE System SHALL block execution and inform the user with a clear error message.
7. THE System SHALL validate workspace configuration before agent execution and report any configuration conflicts.

### Requirement 22: Plan Section Data Model

**User Story:** As a developer, I want a proper data model for Plan items, so that the system can track prioritized work items with appropriate state management.

#### Acceptance Criteria

1. THE System SHALL store PlanItem entities in the database (DB-canonical) with fields: id, workspace_id, title, description, source_todo_id, source_task_id, status, priority, scheduled_date, focus_type, created_at, updated_at.
2. THE System SHALL support PlanItem status values: active, deferred, completed, cancelled.
3. THE System SHALL support PlanItem focus_type values: today, upcoming, blocked.
4. THE System SHALL support PlanItem priority values: high, medium, low, none.
5. THE System SHALL allow PlanItems to be linked to source ToDos or Tasks via source_todo_id or source_task_id.
6. THE System SHALL allow PlanItems to be reordered within their focus_type category.
7. WHEN a linked Task is completed, THE System SHALL automatically update the PlanItem status to completed.
8. THE API SHALL provide CRUD endpoints for PlanItems: GET/POST/PUT/DELETE /api/workspaces/{id}/plan-items.
9. THE API SHALL provide GET /api/workspaces/{id}/sections/plan endpoint returning PlanItems grouped by focus_type sub-category.
10. PlanItems SHALL be workspace-scoped: SwarmWS contains global/cross-domain plan items; custom workspaces contain local/domain-specific plan items.
11. Cross-workspace planning SHALL be achieved by creating PlanItems in SwarmWS that link to underlying ToDos/Tasks in other workspaces.
12. THE System SHALL NOT store PlanItems as files in the filesystem (database is canonical).


### Requirement 23: Communicate Section Data Model

**User Story:** As a developer, I want a proper data model for Communication items, so that the system can track stakeholder alignment work with appropriate state management.

#### Acceptance Criteria

1. THE System SHALL store Communication entities in the database (DB-canonical) with fields: id, workspace_id, title, description, recipient, channel_type, status, priority, due_date, ai_draft_content, sent_at, created_at, updated_at.
2. THE System SHALL support Communication status values: pending_reply, ai_draft, follow_up, sent, cancelled.
3. THE System SHALL support Communication channel_type values: email, slack, meeting, other.
4. THE System SHALL support Communication priority values: high, medium, low, none.
5. THE System SHALL allow Communications to store AI-generated draft content in ai_draft_content field.
6. WHEN a Communication is sent, THE System SHALL update status to sent and record sent_at timestamp.
7. THE System SHALL allow Communications to be linked to source Tasks or ToDos for context.
8. THE API SHALL provide CRUD endpoints for Communications: GET/POST/PUT/DELETE /api/workspaces/{id}/communications.
9. THE API SHALL provide GET /api/workspaces/{id}/sections/communicate endpoint returning Communications grouped by status sub-category.
10. Communications SHALL be workspace-scoped: SwarmWS contains global communications; custom workspaces contain domain-specific communications.
11. THE System SHALL NOT store Communications as files in the filesystem (database is canonical).

### Requirement 24: Agent-Workspace Execution Relationship

**User Story:** As a developer, I want a clear relationship between Agents and Workspaces during execution, so that agents correctly inherit workspace configuration at runtime.

#### Acceptance Criteria

1. THE System SHALL maintain Agents as global entities not bound to specific workspaces.
2. WHEN an Agent executes a Task, THE System SHALL inherit the workspace configuration from the Task's workspace_id.
3. THE System SHALL compute effective Skills for execution as: SwarmWS enabled Skills ∩ Task's workspace enabled Skills.
4. THE System SHALL compute effective MCPs for execution as: SwarmWS enabled MCPs ∩ Task's workspace enabled MCPs.
5. THE System SHALL compute effective Knowledgebases for execution as: (SwarmWS Knowledgebases ∪ Task's workspace Knowledgebases) - workspace excluded sources.
6. IF a Task has no workspace_id, THE System SHALL use SwarmWS configuration as the default.
7. THE System SHALL pass the computed effective configuration to the agent at execution start.


### Requirement 25: Workspace Configuration Audit Trail

**User Story:** As a user, I want an audit trail of workspace configuration changes, so that I can track who changed what and when for governance purposes.

#### Acceptance Criteria

1. THE System SHALL log all workspace configuration changes including Skills, MCPs, and Knowledgebase modifications.
2. THE System SHALL store audit entries with fields: id, workspace_id, change_type, entity_type, entity_id, old_value, new_value, changed_by, changed_at.
3. THE System SHALL support change_type values: enabled, disabled, added, removed, updated.
4. THE System SHALL support entity_type values: skill, mcp, knowledgebase, workspace_setting.
5. THE API SHALL provide GET /api/workspaces/{id}/audit-log endpoint to retrieve audit entries with pagination.
6. THE System SHALL retain audit log entries for at least 90 days.
7. THE System SHALL display recent configuration changes in the workspace settings UI.
8. THE System SHALL include the user identifier (changed_by) for all configuration changes.

### Requirement 26: Skill/MCP Dependency Validation

**User Story:** As a user, I want the system to validate Skill and MCP dependencies before execution, so that I'm informed when required capabilities are disabled.

#### Acceptance Criteria

1. WHEN a Task requires a specific Skill that is disabled in the workspace, THE System SHALL block task execution.
2. WHEN a Task requires a specific MCP that is disabled in the workspace, THE System SHALL block task execution.
3. THE System SHALL display a clear error message listing which Skills or MCPs are required but disabled.
4. THE System SHALL offer to enable the required Skills or MCPs with user confirmation.
5. IF the user confirms enabling required capabilities, THE System SHALL update workspace configuration and proceed with execution.
6. THE System SHALL log dependency validation failures in the audit trail.
7. THE UI SHALL show why execution is blocked (e.g., "Blocked: requires [SkillName] which is disabled in this workspace").


### Requirement 27: Artifact Data Model (Hybrid Storage)

**User Story:** As a developer, I want a data model for Artifacts that uses filesystem for content and database for metadata, so that artifacts are portable while maintaining structured relationships.

#### Acceptance Criteria

1. THE System SHALL store Artifact content as files in the workspace's filesystem under the Artifacts/ folder with type subfolders (Plans/, Reports/, Docs/, Decisions/).
2. THE System SHALL store Artifact metadata in the database with fields: id, workspace_id, task_id (nullable), artifact_type, title, file_path, version, created_by, created_at, updated_at.
3. THE System SHALL support artifact_type values: plan, report, doc, decision, other.
4. THE System SHALL support automatic versioning with format: {filename}_v{NNN}.{ext} (e.g., project-plan_v001.md, project-plan_v002.md).
5. WHEN a new version of an artifact is created, THE System SHALL increment the version number and create a new file while preserving previous versions.
6. THE System SHALL track artifact provenance including source task_id and created_by (user or agent identifier).
7. THE System SHALL support artifact tagging with a separate artifact_tags junction table.
8. THE API SHALL provide CRUD endpoints for Artifacts: GET/POST/PUT/DELETE /api/workspaces/{id}/artifacts.
9. THE API SHALL provide GET /api/workspaces/{id}/sections/artifacts endpoint returning Artifacts grouped by artifact_type sub-category.
10. THE Frontend SHALL define TypeScript interfaces: Artifact, ArtifactType, ArtifactMetadata.
11. NOTE: Artifact (document output) is distinct from PlanItem (planning queue object). PlanItem is a DB entity for work prioritization; Artifact Plan is a document file output.

### Requirement 28: Reflection Section Data Model (Hybrid Storage)

**User Story:** As a developer, I want a data model for Reflection items that uses filesystem for content and database for metadata, so that reflections are portable while maintaining structured relationships.

#### Acceptance Criteria

1. THE System SHALL store Reflection content as markdown files in the workspace's filesystem under the Artifacts/Reports/ folder with naming convention: {reflection_type}_{date}.md (e.g., daily-recap_2026-02-21.md).
2. THE System SHALL store Reflection metadata in the database with fields: id, workspace_id, reflection_type, title, file_path, period_start, period_end, generated_by, created_at, updated_at.
3. THE System SHALL support reflection_type values: daily_recap, weekly_summary, lessons_learned.
4. THE System SHALL support generated_by values: user, agent, system.
5. THE System SHALL allow Reflections to reference related Tasks, ToDos, and Artifacts via junction tables.
6. WHEN generating a daily recap, THE System SHALL aggregate completed tasks, handled signals, and communications from that day.
7. WHEN generating a weekly summary, THE System SHALL aggregate daily recaps and highlight key accomplishments and blockers.
8. THE System SHALL allow users to edit AI-generated reflections and promote lessons learned to workspace context.
9. THE API SHALL provide CRUD endpoints for Reflections: GET/POST/PUT/DELETE /api/workspaces/{id}/reflections.
10. THE API SHALL provide GET /api/workspaces/{id}/sections/reflection endpoint returning Reflections grouped by reflection_type sub-category.
11. THE Frontend SHALL define TypeScript interfaces: Reflection, ReflectionType.


### Requirement 29: Workspace Context File Management

**User Story:** As a user, I want my workspace context files to be automatically managed, so that agents have access to relevant context without manual file management.

#### Acceptance Criteria

1. THE System SHALL create a context.md file in the workspace's ContextFiles/ folder when a workspace is created.
2. THE System SHALL create a compressed-context.md file in the workspace's ContextFiles/ folder for summarized context.
3. THE context.md file SHALL contain: workspace name, description, goals, key priorities, and user-defined notes.
4. THE compressed-context.md file SHALL contain: AI-generated summary of workspace context optimized for agent consumption (max 4000 tokens).
5. THE System SHALL provide a UI for editing context.md content directly from workspace settings.
6. THE System SHALL automatically regenerate compressed-context.md when: context.md is updated, on execution start if stale (>24 hours), or manually triggered.
7. WHEN an agent executes in a workspace, THE System SHALL prefer compressed-context.md if present and fresh; fall back to context.md if small enough.
8. THE System SHALL support context file templates for different workspace types (project, domain, personal).
9. THE API SHALL provide GET/PUT /api/workspaces/{id}/context endpoint for reading and updating context.md content.
10. THE API SHALL provide POST /api/workspaces/{id}/context/compress endpoint to trigger compressed-context.md regeneration.

### Requirement 30: Chat Thread Model (Agent-Workspace Binding)

**User Story:** As a developer, I want a chat thread model that binds conversations to the Agent → Task/ToDo → Workspace relationship, so that chat context is properly scoped and retrievable.

#### Acceptance Criteria

1. THE System SHALL store ChatThread entities in the database (DB-canonical) with fields: id, workspace_id, agent_id, task_id (nullable), todo_id (nullable), mode, title, created_at, updated_at.
2. THE System SHALL support ChatThread mode values: explore, execute.
3. THE System SHALL store ChatMessage entities in the database with fields: id, thread_id, role, content, tool_calls (nullable), created_at.
4. THE System SHALL support ChatMessage role values: user, assistant, tool, system.
5. WHEN a chat is initiated from a workspace, THE System SHALL create a ChatThread bound to that workspace_id.
6. WHEN a chat is promoted to a Task, THE System SHALL update the ChatThread with the task_id reference.
7. WHEN a chat is initiated from a ToDo, THE System SHALL create a ChatThread bound to that todo_id and inherit the workspace_id.
8. THE System SHALL inherit workspace configuration (Skills, MCPs, Knowledgebases) for all ChatThreads bound to that workspace.
9. THE System SHALL store ThreadSummary entities with fields: id, thread_id, summary_type, summary_text, key_decisions, open_questions, updated_at.
10. THE System SHALL support ThreadSummary summary_type values: rolling, final.
11. THE API SHALL provide endpoints for ChatThread management: GET/POST /api/workspaces/{id}/threads, GET /api/threads/{id}/messages.
12. THE Frontend SHALL define TypeScript interfaces: ChatThread, ChatMessage, ThreadSummary, ChatMode.
13. THE System SHALL NOT store ChatThreads or ChatMessages as files in the filesystem (database is canonical).
14. THE System SHALL optionally support exporting chat transcripts to the workspace's Transcripts/ folder (a sibling folder to Artifacts/, NOT inside Artifacts/) on user request.


### Requirement 31: Chat Thread Retention and Search Indexing

**User Story:** As a user, I want efficient search across my chat history without excessive storage overhead, so that I can find past conversations quickly.

#### Acceptance Criteria

1. THE System SHALL use ThreadSummary for default search indexing, NOT raw ChatMessages.
2. THE System SHALL NOT index raw ChatMessages by default to reduce storage and improve search performance.
3. THE System SHALL retain all ChatMessages locally in the database (no automatic pruning).
4. THE System SHALL support optional export of chat transcripts to filesystem on user request.
5. THE System SHALL provide search endpoint that queries ThreadSummary.summary_text and ThreadSummary.key_decisions.
6. THE System SHALL support future optional pruning/archival policies (not implemented in initial release).
7. THE API SHALL provide GET /api/search/threads endpoint with query parameter searching ThreadSummary content.

### Requirement 32: Workspace Routing Suggestion for ToDo Conversion

**User Story:** As a user, I want the system to suggest an appropriate workspace when converting a ToDo to a Task, so that I can quickly route work to the right context.

#### Acceptance Criteria

1. WHEN converting a ToDo to a Task, THE System SHALL suggest a target workspace based on: source context, tags, recent activity in workspaces.
2. THE System SHALL display the suggested workspace with a "Suggested" badge in the workspace selector.
3. THE System SHALL allow the user to confirm the suggestion or override with a different workspace selection.
4. THE System SHALL learn from user overrides to improve future suggestions (optional, future enhancement).
5. IF no strong signal exists, THE System SHALL default to suggesting the current workspace or SwarmWS.
6. THE System SHALL NOT suggest archived workspaces as conversion targets.
7. WHEN user selects an archived workspace in the conversion dialog, THE UI SHALL block the selection and display an explanation: "Archived workspaces cannot accept new tasks. Please unarchive the workspace first or select a different workspace."

### Requirement 33: Unified Section Endpoint Response Contract

**User Story:** As a frontend developer, I want all section endpoints to return a consistent response shape, so that I can build reusable UI components.

#### Acceptance Criteria

1. ALL section list endpoints SHALL return a standard response shape:
```json
{
  "counts": { "total": number, "byStatus": { ... } },
  "groups": [ { "name": string, "items": [...] } ],
  "pagination": { "limit": number, "offset": number, "total": number, "hasMore": boolean },
  "sortKeys": [ "created_at", "updated_at", "priority", ... ],
  "lastUpdatedAt": "ISO8601 timestamp"
}
```
2. THE Frontend SHALL define a generic SectionListResponse<T> TypeScript interface for this shape.
3. ALL section endpoints SHALL support `limit` and `offset` query parameters for pagination.
4. ALL section endpoints SHALL support `sort_by` and `sort_order` query parameters.
5. THE default pagination limit SHALL be 50 items per request.
6. THE "All Workspaces" aggregation endpoints SHALL use the same response contract.


### Requirement 34: Policy Enforcement Hooks for Execution

**User Story:** As a user, I want the system to prevent execution when policy conflicts exist, so that I understand why a task cannot run and can resolve the issue.

#### Acceptance Criteria

1. THE System SHALL validate all policy requirements before starting task execution.
2. IF a policy conflict exists (disabled skill, disabled MCP, missing permission), THE System SHALL block execution.
3. THE System SHALL display a clear error message explaining why execution is blocked (e.g., "Blocked: requires [SkillName] which is disabled").
4. THE UI SHALL show a "Policy Conflict" indicator on tasks that cannot execute due to configuration issues.
5. THE System SHALL provide a "Resolve" action that navigates to workspace settings to enable required capabilities.
6. THE System SHALL log all policy enforcement blocks in the audit trail.
7. THE API SHALL return a 409 Conflict status with detailed policy_violations array when execution is blocked.

### Requirement 35: Internal Storage Layout vs Structured Entities Separation

**User Story:** As a developer, I want clear separation between filesystem storage and database entities, so that the architecture is consistent and maintainable.

#### Acceptance Criteria

1. THE System SHALL use the following filesystem structure for workspace content storage:
```
<swarm-workspace>/
├── Artifacts/
│   ├── Plans/
│   ├── Reports/
│   ├── Docs/
│   └── Decisions/
├── ContextFiles/
│   ├── context.md
│   └── compressed-context.md
└── Transcripts/  (optional, export only)
```
2. THE System SHALL NOT create filesystem folders for: Tasks, ToDos, PlanItems, Communications, ChatThreads, Historical-Chats.
3. THE System SHALL store all structured entities (Tasks, ToDos, PlanItems, Communications, ChatThreads) in the SQLite database only.
4. THE System SHALL store content files (Artifacts, Reflections, Context files) in the filesystem with metadata in the database.
5. WHEN migrating from old folder structure, THE System SHALL ignore legacy folders (Tasks/, ToDos/, Plans/, Historical-Chats/) and use database as source of truth.
6. THE documentation SHALL clearly distinguish "Internal Storage Layout" (filesystem) from "Structured Entities" (database).


### Requirement 36: Workspace Archive Behavior

**User Story:** As a user, I want to archive workspaces I'm no longer actively using, so that they don't clutter my workspace list while preserving their data for future reference.

#### Acceptance Criteria

1. THE System SHALL allow users to archive custom workspaces (SwarmWS cannot be archived).
2. WHEN a workspace is archived, THE System SHALL set an `archived_at` timestamp and `is_archived = true` flag.
3. Archived workspaces SHALL NOT appear in the default workspace list (hidden by default).
4. THE System SHALL provide a "Show Archived" toggle to display archived workspaces in the workspace selector.
5. Archived workspaces SHALL NOT participate in "All Workspaces" aggregation by default.
6. Archived workspaces SHALL be read-only: users can view items but cannot create new ToDos, Tasks, PlanItems, or Communications.
7. Archived workspaces SHALL NOT accept new signals or route new items to them.
8. Agent execution SHALL be disabled for archived workspaces (tasks cannot be started or resumed).
9. Items in archived workspaces SHALL remain searchable via global search.
10. THE System SHALL allow users to unarchive a workspace, restoring full functionality.
11. THE UI SHALL display archived workspaces with a visual indicator (e.g., muted color, archive icon).


### Requirement 37: SwarmWS Global vs Local View Behavior

**User Story:** As a user, I want SwarmWS to act as my global work cockpit by default, while still allowing me to view SwarmWS-only items when needed, so that I have a unified view of all my work.

#### Acceptance Criteria

1. WHEN SwarmWS is selected, THE System SHALL default to Global View that aggregates items across all non-archived workspaces in each section (Signals/Plan/Execute/Communicate/Artifacts/Reflection).
2. WHEN SwarmWS is selected, THE System SHALL provide a toggle with two modes:
   - **Global (All Workspaces)**: Aggregates items from all accessible workspaces
   - **SwarmWS-only**: Shows only items where workspace_id = swarmws_id
3. THE default mode SHALL be Global (All Workspaces).
4. WHEN the user switches modes, THE System SHALL persist the selection for future sessions.
5. WHEN SwarmWS-only mode is active, THE System SHALL only display items where workspace_id = swarmws_id.
6. WHEN a Custom_Workspace is selected, THE System SHALL display Scoped View by default (only items where workspace_id = selected_workspace_id).
7. THE System MAY allow switching to Global View from a custom workspace via workspace scope selector (optional), but default remains scoped.
8. WHEN "All Workspaces" scope is selected (from workspace selector or filter), THE System SHALL aggregate items across all accessible non-archived workspaces using the same grouping rules as SwarmWS Global View.
9. WHEN SwarmWS Global View is active, THE API SHALL accept workspace_id=all for section endpoints and return aggregated results.
10. WHEN SwarmWS-only mode is active, THE API SHALL return results scoped to workspace_id=swarmws_id.
11. THE Workspace_Explorer counts and badges SHALL reflect the current mode:
    - Global View counts reflect aggregated totals
    - SwarmWS-only counts reflect SwarmWS-only totals
12. THE Global View aggregation SHALL support pagination and indexed queries as defined in the unified section response contract.

**SwarmWS Global View Aggregation Rules:**
- **Signals**: All ToDos from all workspaces (including unassigned and routed)
- **Plan**: SwarmWS global PlanItems + summary cards of top N upcoming items from each workspace
- **Execute**: All Tasks from all workspaces, grouped by status and priority
- **Communicate**: All Communications from all workspaces
- **Artifacts**: Recent artifacts across all workspaces, filterable by workspace
- **Reflection**: SwarmWS generates cross-domain recap aggregating events from all workspaces

**Distinction between SwarmWS Global and "All Workspaces" scope:**
- **SwarmWS Global**: Opinionated cockpit view with smart prioritization and recommendations. SHALL include a "Recommended" group showing top N items (default N=3, configurable) based on priority desc, then updated_at desc.
- **All Workspaces scope**: Neutral aggregated data view without recommendation ranking. SHALL NOT apply recommendation ranking; default sort = updated_at desc.


### Requirement 38: Global Search Functionality

**User Story:** As a user, I want to search across all entity types from the workspace explorer, so that I can quickly find threads, tasks, signals, and artifacts without navigating to each section.

#### Acceptance Criteria

1. THE Workspace_Explorer SHALL display a global search bar in the header area with placeholder text "Search… (threads, tasks, signals, artifacts)".
2. THE Search SHALL query across all entity types: ChatThreads (via ThreadSummary), Tasks, ToDos/Signals, Artifacts, PlanItems, Communications, and Reflections.
3. THE Search SHALL respect the current Workspace_Scope:
   - In Global View: search across all non-archived workspaces
   - In Scoped View: search within the selected workspace only
4. THE Search results SHALL be grouped by entity type with section headers (Threads, Tasks, Signals, Artifacts, etc.).
5. THE Search SHALL support full-text search on: title, description, summary_text (for threads), and content (for artifacts).
6. THE Search results SHALL display: item title, entity type badge, workspace name (in Global View), and last updated timestamp.
7. WHEN a search result is clicked, THE System SHALL navigate to the appropriate detail view for that entity.
8. THE Search SHALL support keyboard navigation (arrow keys to navigate results, Enter to select).
9. THE Search SHALL debounce input with 300ms delay to prevent excessive API calls.
10. THE API SHALL provide GET /api/search endpoint with query, scope (workspace_id or "all"), and entity_types filter parameters.
11. THE Search results SHALL be limited to 50 items per entity type by default, with "Show more" option.
12. Items in archived workspaces SHALL be included in search results but marked with an "Archived" badge.
