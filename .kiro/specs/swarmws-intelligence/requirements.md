# Requirements Document — SwarmWS Intelligence (Cadence 4 of 4)

## Introduction

This is **Cadence 4 of 4** for the SwarmWS redesign. It covers the intelligence layer: the 8-layer context assembly engine for agent runtime, chat thread association with projects, and the context assembly preview API. This cadence depends on Cadences 1–3 (`swarmws-foundation`, `swarmws-projects`, `swarmws-explorer-ux`) being completed first.

See the parent spec at `.kiro/specs/swarmws-redesign/requirements.md` for the full glossary and architectural context.

## Cross-References

This spec is part of the SwarmWS Redesign, split into 4 implementation cadences:

| Cadence | Spec | Requirements | Focus |
|---------|------|-------------|-------|
| 1 | `swarmws-foundation` | 1, 2, 3, 6, 7, 8, 17, 19, 20, 23, 24, 25, 28, 29, 30 | Single workspace, folder structure, Knowledge domain, backend data model, dead code removal |
| 2 | `swarmws-projects` | 4, 5, 18, 21, 22, 27, 31, 32 | Project CRUD, template, metadata, frontend types/services |
| 3 | `swarmws-explorer-ux` | 9, 10, 11, 12, 13, 14, 15 | Workspace Explorer UX redesign |
| 4 | `swarmws-intelligence` | 16, 26, 33, 34, 35, 36, 37, 38 | Context assembly, chat threads, preview API, caching, binding, observability |

Parent spec: `.kiro/specs/swarmws-redesign/requirements.md`

## Glossary

- **SwarmWS**: The single, non-deletable root workspace. Serves as the persistent memory container for all SwarmAI work. Located at `{app_data_dir}/SwarmWS`.
- **Project**: A self-contained execution and knowledge container under `Projects/`. Each project has its own context files, instructions, chats, research, and reports. Replaces the concept of custom workspaces.
- **Knowledge**: The shared knowledge domain at the workspace root representing workspace-level shared semantic memory. Contains `Knowledge Base/` for durable reusable assets, `Notes/` for evolving working knowledge, and `Memory/` for persistent semantic memory distilled from user interactions. Replaces the former `Artifacts/` and `Notebooks/` folders.
- **Knowledge_Base**: A subfolder under `Knowledge/` for durable, reusable, high-confidence knowledge assets.
- **Notes**: A subfolder under `Knowledge/` for evolving working knowledge and exploratory documents.
- **Memory**: A subfolder under `Knowledge/` for persistent semantic memory automatically distilled from user chat history and interactions. Contains long-term, user-specific memory reflecting preferences, patterns, recurring goals, and accumulated insights derived from conversations.
- **Context_L0**: An ultra-concise semantic abstract file (~1000 tokens) used for fast relevance detection and routing decisions. Named `context-L0.md`. Contains YAML frontmatter with `tags` and `active_domains` fields for tag-based filtering.
- **Context_L1**: A structured overview file (~4k tokens) describing scope, structure, goals, key knowledge, and relationships. Named `context-L1.md`.
- **Depth_Guardrail**: A maximum folder nesting limit enforced by the system to maintain usability and agent reasoning consistency.
- **System_Managed_Item**: A file or folder that is created and maintained by the system. System_Managed_Items cannot be deleted or structurally renamed by users. Users may edit the content of system-managed files.
- **User_Managed_Item**: A file or folder created by the user. User_Managed_Items support full CRUD operations within depth guardrail limits.
- **Semantic_Zone**: A visual grouping in the workspace explorer that organizes the tree into two conceptual areas: Shared Knowledge and Active Work.
- **Focus_Mode**: A project-centric explorer view that auto-expands the active project and keeps Knowledge visible.
- **Project_Metadata**: A hidden `.project.json` file inside each project directory containing system metadata (creation date, status, tags, UUID).
- **Workspace_Explorer**: The middle-column UI component that displays the SwarmWS tree structure with semantic zone grouping.
- **Sample_Data**: Realistic onboarding content pre-populated in SwarmWS on first launch to demonstrate intended usage of Knowledge and project structure.
- **Context_Version**: A lightweight integer counter on chat threads, incremented when bindings or context-affecting state changes occur. Used for cache invalidation.
- **Thread_Binding**: The association of a chat thread with a task and/or ToDo, which can be established at creation or mid-session via drag-drop.

## Requirements

### Requirement 16: Context Assembly Order (Agent Runtime)

**User Story:** As a knowledge worker, I want agents to assemble context in a predictable priority order, so that task-specific context takes precedence over global memory without losing long-term knowledge.

#### Acceptance Criteria

1. WHEN an agent executes within a project, THE System SHALL assemble context in the following order (highest priority first):
   1. Base system prompt (`system-prompts.md`)
   2. Current live work context (active chat thread, ToDos, tasks, files) — bounded and summarized
   3. Project intent and instructions (`instructions.md`)
   4. Project semantic context (`context-L0.md`, `context-L1.md`)
   5. Shared knowledge semantic context (`Knowledge/context-L0.md`, `Knowledge/context-L1.md`)
   6. Persistent semantic memory (`Knowledge/Memory/` — user preferences, recurring themes, historical decisions)
   7. Global workspace semantic context (`SwarmWS/context-L0.md`, `SwarmWS/context-L1.md`)
   8. Optional scoped retrieval within SwarmWS
2. THE System SHALL use L0 context files for tag-based relevance filtering before loading L1 context. L0 files SHALL contain YAML frontmatter with `tags` and `active_domains` fields. The filter SHALL perform token intersection between L0 tags and keywords extracted from Layer 2 live context.
3. THE System SHALL respect a configurable maximum token budget for total injected context (default: 10K tokens).
4. IF the total assembled context exceeds the token budget, THE System SHALL apply 3-stage progressive truncation starting from layer 8 upward: (a) truncate within layer keeping headers and top N tokens, (b) remove least important files/snippets inside the layer, (c) drop entire layer only as last resort. Layers 1–2 SHALL never be fully dropped.
5. WHEN Layer 2 (live work context) is assembled, THE System SHALL bound it to a configurable token limit (default: 1200 tokens). The bounded content SHALL include thread title, last user message, last assistant message, and bound task/todo summary. Older messages SHALL be summarized.
6. THE System SHALL guarantee deterministic assembly: identical inputs (same project, thread, budget, file contents, DB state) SHALL produce identical output.
7. WHEN any layers are truncated, THE System SHALL inject a truncation summary marker (e.g., `[Context truncated: ...]`) into the assembled context so the agent is aware that context was omitted.
8. THE System SHALL resolve project filesystem paths using `project_id` (UUID) rather than project display name, ensuring path stability when projects are renamed.

### Requirement 26: Chat Threads and Projects

**User Story:** As a knowledge worker, I want chat threads to live inside projects, so that conversations are contextually bound to the work they relate to.

#### Acceptance Criteria

1. WHEN a chat is initiated from within a project context, THE System SHALL store the chat thread under the project's `chats/` directory.
2. THE System SHALL organize chat threads in subdirectories within `chats/` (e.g., `chats/thread_001/`).
3. THE System SHALL enforce the 2-level depth guardrail within the `chats/` folder.
4. WHEN a chat is initiated outside of any project context (e.g., from the workspace root), THE System SHALL associate the chat with SwarmWS globally rather than a specific project.
5. THE System SHALL update the chat thread database records to reference a `project_id` (UUID from `.project.json`) instead of a `workspace_id`. Threads not associated with any project SHALL have `project_id` set to NULL, indicating a global SwarmWS chat.
6. THE System SHALL maintain a `context_version` integer counter on each chat thread record, incremented whenever bindings or context-affecting state changes occur.

### Requirement 33: Context Assembly Preview API

**User Story:** As a knowledge worker, I want to preview the context that an agent would see for a given project and chat thread, so that I can understand and trust what information the agent is working with (Visible Planning Builds Trust).

#### Acceptance Criteria

1. THE Backend SHALL provide a `GET /api/projects/{id}/context` endpoint that returns the assembled context for a project, following the context assembly order defined in Requirement 16.
2. THE response SHALL include each context layer with its workspace-relative source path (never absolute filesystem paths), token count, and content preview (truncated to a configurable limit).
3. THE response SHALL indicate the total token count, whether any layers were truncated due to the token budget, and a human-readable truncation summary when truncation occurred.
4. THE Backend SHALL accept an optional `thread_id` query parameter to include the specific chat thread's live context (layer 2 in the assembly order).
5. THE Frontend SHALL display the context preview in a collapsible panel accessible from the project detail view or chat interface.
6. THE context preview SHALL update in near-real-time as context files are modified, using ETag/version-based caching to avoid redundant requests when context is unchanged.
7. THE Backend SHALL return the context assembly response using snake_case field names (Python/Pydantic convention).

### Requirement 34: Context Snapshot Caching

**User Story:** As a system, I want to cache assembled context snapshots so that repeated assembly requests with unchanged inputs avoid redundant database and filesystem reads.

#### Acceptance Criteria

1. THE System SHALL maintain an in-memory context snapshot cache keyed by `(project_id, thread_id, token_budget, context_version)`.
2. THE System SHALL maintain lightweight version counters: `thread_version`, `task_version`, `todo_version`, `project_files_version`, `memory_version`. Each counter SHALL be incremented at the relevant mutation point (e.g., message added, task updated, file changed).
3. WHEN all version counters are unchanged since the last assembly, THE System SHALL return the cached result without re-reading the database or filesystem.
4. WHEN any version counter has changed, THE System SHALL trigger a fresh assembly and update the cache.
5. THE cache SHALL support LRU eviction with a configurable maximum entry count (default: 50).

### Requirement 35: Mid-Session Thread Binding

**User Story:** As a knowledge worker, I want to drag-and-drop a task or ToDo onto an active chat thread mid-session, so that the agent's context is updated to include the newly bound work item.

#### Acceptance Criteria

1. THE Backend SHALL provide a `POST /api/chat_threads/{thread_id}/bind` endpoint accepting `task_id`, `todo_id`, and `mode` (`replace` or `add`).
2. WHEN mode is `replace`, THE System SHALL overwrite the thread's existing `task_id` and/or `todo_id` with the new values.
3. WHEN mode is `add`, THE System SHALL only set fields that are currently NULL, preserving existing bindings.
4. AFTER a successful binding, THE System SHALL increment the thread's `context_version` counter.
5. THE System SHALL trigger context re-assembly on the next agent turn after a binding change.
6. WHEN a user attempts to bind a task/todo from a different project than the thread's project, THE System SHALL return a 409 Conflict with a warning message. The request MAY include a `force: true` flag to override the guardrail.

### Requirement 36: Preview API Scalability

**User Story:** As a system, I want the context preview API to be efficient and avoid wasteful polling when context hasn't changed.

#### Acceptance Criteria

1. THE Backend SHALL include an `ETag` header in context preview responses, derived from the context version hash.
2. WHEN a client sends an `If-None-Match` header matching the current ETag, THE Backend SHALL return 304 Not Modified without re-assembling context.
3. THE Backend SHALL support a `since_version` query parameter for version-based polling (`GET /api/projects/{id}/context?since_version=42`).

### Requirement 37: Schema Evolution Safety

**User Story:** As a developer, I want database schema changes to be applied safely regardless of whether the database is clean-slate or pre-existing.

#### Acceptance Criteria

1. THE System SHALL apply schema changes using `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for safety on both clean and existing databases.
2. THE System SHALL include new columns (`project_id`, `context_version`) in the CREATE TABLE definition for clean installs.
3. THE System SHALL NOT require a complex migration framework at this stage.

### Requirement 38: Context Assembly Observability

**User Story:** As a developer, I want structured logging of context assembly decisions so that I can debug context issues and monitor system behavior.

#### Acceptance Criteria

1. THE System SHALL log layer sizes, truncation decisions, and cache hit/miss status at INFO level during context assembly.
2. THE System SHALL log L0 filter decisions (tags, keywords, overlap result) and truncation stage details at DEBUG level.
3. THE System SHALL log binding changes (thread_id, task_id, todo_id, mode) at DEBUG level.
