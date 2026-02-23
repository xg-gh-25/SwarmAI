# Implementation Plan: Workspace Refactor - Daily Work Operating Loop

## Overview

This implementation plan transforms the SwarmAI workspace system from file-tree navigation to section-based navigation following the Daily Work Operating Loop: Signals → Plan → Execute → Communicate → Artifacts → Reflection. The implementation uses Python (FastAPI) for backend and TypeScript (React) for frontend.

**Key Architectural Principles:**
- DB-Canonical: Tasks, ToDos, PlanItems, Communications, ChatThreads stored in SQLite
- Filesystem: Artifacts/, ContextFiles/ for content storage only
- Backend: snake_case (Python/Pydantic), Frontend: camelCase (TypeScript)

## Tasks

- [ ] 1. Database Schema Foundation
  - [x] 1.1 Create todos table with all columns and indexes
    - Add columns: id, workspace_id, title, description, source, source_type, status, priority, due_date, task_id, created_at, updated_at
    - Add indexes on workspace_id, status, due_date
    - Add composite index on (workspace_id, status)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 13.1, 13.5_

  - [x] 1.2 Create plan_items table with all columns and indexes
    - Add columns: id, workspace_id, title, description, source_todo_id, source_task_id, status, priority, scheduled_date, focus_type, sort_order, created_at, updated_at
    - Add indexes on workspace_id, focus_type
    - Add composite index on (workspace_id, focus_type)
    - _Requirements: 22.1, 22.2, 22.3, 22.4_

  - [x] 1.3 Create communications table with all columns and indexes
    - Add columns: id, workspace_id, title, description, recipient, channel_type, status, priority, due_date, ai_draft_content, source_task_id, source_todo_id, sent_at, created_at, updated_at
    - Add indexes on workspace_id, status
    - Add composite index on (workspace_id, status)
    - _Requirements: 23.1, 23.2, 23.3, 23.4_

  - [x] 1.4 Create artifacts and artifact_tags tables
    - artifacts: id, workspace_id, task_id, artifact_type, title, file_path, version, created_by, created_at, updated_at
    - artifact_tags: id, artifact_id, tag, created_at
    - Add indexes on workspace_id, artifact_type
    - _Requirements: 27.2, 27.3, 27.7_

  - [x] 1.5 Create reflections table
    - Add columns: id, workspace_id, reflection_type, title, file_path, period_start, period_end, generated_by, created_at, updated_at
    - Add indexes on workspace_id, reflection_type
    - _Requirements: 28.2, 28.3, 28.4_

  - [x] 1.6 Create chat_threads, chat_messages, and thread_summaries tables
    - chat_threads: id, workspace_id, agent_id, task_id, todo_id, mode, title, created_at, updated_at
    - chat_messages: id, thread_id, role, content, tool_calls, created_at
    - thread_summaries: id, thread_id, summary_type, summary_text, key_decisions, open_questions, updated_at
    - Add indexes on thread_id, workspace_id
    - _Requirements: 30.1, 30.3, 30.9_

  - [x] 1.7 Create workspace configuration tables
    - workspace_skills: id, workspace_id, skill_id, enabled, created_at, updated_at (with UNIQUE constraint)
    - workspace_mcps: id, workspace_id, mcp_server_id, enabled, created_at, updated_at (with UNIQUE constraint)
    - workspace_knowledgebases: id, workspace_id, source_type, source_path, display_name, metadata, excluded_sources (JSON array of IDs), created_at, updated_at
    - audit_log: id, workspace_id, change_type, entity_type, entity_id, old_value, new_value, changed_by, changed_at
    - _Requirements: 19.1, 19.3, 19.5, 25.2_

  - [x] 1.8 Modify existing tables (tasks, skills, mcp_servers, swarm_workspaces)
    - tasks: add workspace_id, source_todo_id, blocked_reason, priority, description columns
    - skills: add is_privileged column (default 0)
    - mcp_servers: add is_privileged column (default 0)
    - swarm_workspaces: add is_archived, archived_at columns
    - _Requirements: 5.1, 13.2, 13.3, 13.4, 19.2, 19.4_

  - [x] 1.9 Create database migration for existing data
    - Map existing task statuses: pending→draft, running→wip, failed→blocked
    - Set workspace_id to SwarmWS.id for existing tasks with NULL workspace_id
    - Use transactions for atomicity
    - _Requirements: 5.4, 13.7, 13.8_

- [x] 2. Backend Pydantic Schemas
  - [x] 2.1 Create ToDo schemas (backend/schemas/todo.py)
    - ToDoStatus enum: pending, overdue, in_discussion, handled, cancelled, deleted
    - ToDoSourceType enum: manual, email, slack, meeting, integration
    - Priority enum: high, medium, low, none
    - ToDoCreate, ToDoUpdate, ToDoResponse models
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [x] 2.2 Write property test for ToDo enum validation
    - **Property 6: Entity enum field validation**
    - Test all enum values are valid for status, source_type, priority
    - **Validates: Requirements 4.2, 4.3, 4.4**

  - [x] 2.3 Create PlanItem schemas (backend/schemas/plan_item.py)
    - PlanItemStatus enum: active, deferred, completed, cancelled
    - FocusType enum: today, upcoming, blocked
    - PlanItemCreate, PlanItemUpdate, PlanItemResponse models
    - _Requirements: 22.1, 22.2, 22.3, 22.4_

  - [x] 2.4 Create Communication schemas (backend/schemas/communication.py)
    - CommunicationStatus enum: pending_reply, ai_draft, follow_up, sent, cancelled
    - ChannelType enum: email, slack, meeting, other
    - CommunicationCreate, CommunicationUpdate, CommunicationResponse models
    - _Requirements: 23.1, 23.2, 23.3, 23.4_

  - [x] 2.5 Create Artifact and Reflection schemas (backend/schemas/artifact.py, reflection.py)
    - ArtifactType enum: plan, report, doc, decision, other
    - ReflectionType enum: daily_recap, weekly_summary, lessons_learned
    - Artifact/Reflection Create, Update, Response models
    - _Requirements: 27.2, 27.3, 28.2, 28.3_

  - [x] 2.6 Create ChatThread and ThreadSummary schemas (backend/schemas/chat_thread.py)
    - ChatMode enum: explore, execute
    - MessageRole enum: user, assistant, tool, system
    - SummaryType enum: rolling, final
    - ChatThread, ChatMessage, ThreadSummary models
    - _Requirements: 30.1, 30.2, 30.3, 30.4, 30.9, 30.10_

  - [x] 2.7 Create Section response schemas (backend/schemas/section.py)
    - SectionGroup generic model with name and items
    - Pagination model with limit, offset, total, has_more
    - SectionResponse generic model with counts, groups, pagination, sort_keys, last_updated_at
    - SectionCounts model with all six sections
    - _Requirements: 7.1, 7.9, 33.1_

  - [x] 2.8 Write property test for section response contract
    - **Property 11: Section endpoint unified response contract**
    - Verify all section responses have required fields
    - **Validates: Requirements 7.1-7.12, 33.1-33.6**

  - [x] 2.9 Create workspace configuration schemas (backend/schemas/workspace_config.py)
    - WorkspaceSkillConfig, WorkspaceMcpConfig, WorkspaceKnowledgebaseConfig models
    - AuditLogEntry model with change_type, entity_type, entity_id, old_value, new_value, changed_by, changed_at
    - PolicyViolation model for 409 responses
    - _Requirements: 19.6, 19.7, 19.8, 25.2_

- [x] 3. Checkpoint - Database and Schemas
  - Run `cd backend && pytest tests/` to verify all schema tests pass
  - Verify database migrations apply cleanly
  - Ask the user if questions arise

- [x] 4. Backend Manager Classes - Core Entities
  - [x] 4.1 Create ToDoManager (backend/core/todo_manager.py)
    - Implement create, get, list, update, delete methods
    - Implement convert_to_task method (updates ToDo status to handled, sets task_id)
    - Implement check_overdue background job method (hourly scan for past due_date with pending status)
    - Default workspace_id to SwarmWS.id when not provided
    - _Requirements: 4.1-4.9, 6.1-6.8_

  - [x] 4.2 Write property test for default workspace assignment
    - **Property 3: Default workspace assignment**
    - Test ToDo/Task without workspace_id gets SwarmWS.id
    - **Validates: Requirements 1.3, 1.4**

  - [x] 4.3 Write property test for overdue detection
    - **Property 7: Overdue detection**
    - Test ToDos with past due_date and pending status become overdue after job runs
    - **Validates: Requirements 4.5, 4.6**

  - [x] 4.4 Write property test for ToDo to Task conversion
    - **Property 8: ToDo to Task conversion round-trip**
    - Test Task.source_todo_id = ToDo.id, ToDo.task_id = Task.id, ToDo.status = handled
    - **Validates: Requirements 4.7, 4.8, 5.6**

  - [x] 4.5 Create SectionManager (backend/core/section_manager.py)
    - Implement get_section_counts method returning SectionCounts
    - Implement get_signals, get_plan, get_execute, get_communicate, get_artifacts, get_reflection methods
    - Support workspace_id="all" for aggregation across non-archived workspaces
    - Return unified SectionResponse with counts, groups, pagination, sort_keys, last_updated_at
    - _Requirements: 7.1-7.12_

  - [x] 4.6 Create PlanItemManager (backend/core/plan_item_manager.py)
    - Implement CRUD methods
    - Implement linked task completion cascade (when Task completes, PlanItem completes)
    - Support reordering within focus_type category via sort_order
    - _Requirements: 22.1-22.12_

  - [x] 4.7 Write property test for PlanItem linked task completion
    - **Property 29: PlanItem linked task completion cascade**
    - Test PlanItem.status becomes completed when linked Task.status becomes completed
    - **Validates: Requirements 22.7**

  - [x] 4.8 Create CommunicationManager (backend/core/communication_manager.py)
    - Implement CRUD methods
    - Implement sent timestamp update (set sent_at when status changes to sent)
    - Support ai_draft_content storage
    - _Requirements: 23.1-23.11_

  - [x] 4.9 Write property test for Communication sent timestamp
    - **Property 30: Communication sent timestamp**
    - Test sent_at is set when status changes to sent
    - **Validates: Requirements 23.6**

  - [x] 4.10 Update TaskManager for new fields and status mapping (backend/core/task_manager.py)
    - Add workspace_id parameter to create/list methods
    - Implement _map_legacy_status for backward compatibility (pending→draft, running→wip, failed→blocked)
    - Add blocked_reason handling (preserve failure context)
    - Default workspace_id to SwarmWS.id when not provided
    - _Requirements: 5.1-5.8_

  - [x] 4.11 Write property test for task status backward compatibility
    - **Property 9: Task status backward compatibility**
    - Test legacy status mapping: pending→draft, running→wip, failed→blocked
    - **Validates: Requirements 5.4**

  - [x] 4.12 Write property test for blocked task preserves reason
    - **Property 10: Blocked task preserves reason**
    - Test blocked_reason is non-empty when status transitions to blocked from failure
    - **Validates: Requirements 5.5**

- [x] 5. Backend Manager Classes - Workspace Configuration
  - [x] 5.1 Create WorkspaceConfigResolver (backend/core/workspace_config_resolver.py)
    - Implement get_effective_skills using intersection model: swarmws_enabled ∩ workspace_enabled
    - Implement get_effective_mcps using intersection model: swarmws_enabled ∩ workspace_enabled
    - Implement get_effective_knowledgebases using union model: (swarmws ∪ workspace) - excluded
    - Implement update_skill_config, update_mcp_config methods
    - Implement validate_execution_policy method (returns PolicyViolation list)
    - _Requirements: 16.1-16.11, 17.1-17.11, 18.1-18.9, 24.1-24.7_

  - [x] 5.2 Write property test for Skills intersection model
    - **Property 12: Skills configuration intersection model**
    - Test effective_skills = swarmws_enabled ∩ workspace_enabled
    - **Validates: Requirements 16.1-16.11, 21.1-21.7, 24.3**

  - [x] 5.3 Write property test for MCPs intersection model
    - **Property 13: MCPs configuration intersection model**
    - Test effective_mcps = swarmws_enabled ∩ workspace_enabled
    - **Validates: Requirements 17.1-17.11, 21.1-21.7, 24.4**

  - [x] 5.4 Write property test for Knowledgebases union model
    - **Property 14: Knowledgebases union model with exclusions**
    - Test effective_kbs = (swarmws ∪ workspace) - excluded
    - **Validates: Requirements 18.1-18.9, 24.5**

  - [x] 5.5 Write property test for privileged capability enablement
    - **Property 15: Privileged capability requires explicit enablement**
    - Test is_privileged=true capabilities are not auto-enabled
    - **Validates: Requirements 16.2, 16.11, 17.2, 17.11**

  - [x] 5.6 Create ContextManager (backend/core/context_manager.py)
    - Implement get_context, update_context methods (read/write ContextFiles/context.md)
    - Implement compress_context method (generate compressed-context.md)
    - Implement inject_context with token budget (default 4000 tokens)
    - Prefer compressed-context.md if fresh (<24 hours), fallback to context.md
    - _Requirements: 14.1-14.9, 29.1-29.10_

  - [x] 5.7 Write property test for context file creation
    - **Property 21: Context file creation**
    - Test workspace creation creates ContextFiles/context.md
    - **Validates: Requirements 29.1-29.10**

  - [x] 5.8 Create AuditManager (backend/core/audit_manager.py)
    - Implement log_change method (create audit_log entry)
    - Implement get_audit_log with pagination
    - Support change_type: enabled, disabled, added, removed, updated
    - Support entity_type: skill, mcp, knowledgebase, workspace_setting
    - _Requirements: 25.1-25.8_

  - [x] 5.9 Write property test for audit logging
    - **Property 16: Configuration change audit logging**
    - Test all config changes create audit_log entries with required fields
    - **Validates: Requirements 25.1-25.8**

- [x] 6. Checkpoint - Backend Managers
  - Run `cd backend && pytest tests/` to verify all manager tests pass
  - Verify property tests pass with 100+ iterations
  - Ask the user if questions arise

- [x] 7. Backend Manager Classes - Artifacts and Search
  - [x] 7.1 Create ArtifactManager (backend/core/artifact_manager.py)
    - Implement CRUD methods with hybrid storage (DB metadata + filesystem content)
    - Store content in Artifacts/{type}/ folder (Plans/, Reports/, Docs/, Decisions/)
    - Implement versioning logic: {filename}_v{NNN}.{ext} format
    - Implement tagging support via artifact_tags table
    - _Requirements: 27.1-27.11_

  - [x] 7.2 Write property test for artifact hybrid storage
    - **Property 18: Artifact hybrid storage**
    - Test content stored in filesystem, metadata in database
    - **Validates: Requirements 27.1-27.11**

  - [x] 7.3 Write property test for artifact versioning
    - **Property 19: Artifact versioning**
    - Test version increments, new file created, previous preserved
    - **Validates: Requirements 27.4, 27.5**

  - [x] 7.4 Create ReflectionManager (backend/core/reflection_manager.py)
    - Implement CRUD methods with hybrid storage
    - Store content in Artifacts/Reports/ with naming: {reflection_type}_{date}.md
    - Implement daily recap generation (aggregate completed tasks, handled signals)
    - Implement weekly summary generation (aggregate daily recaps)
    - _Requirements: 28.1-28.11_

  - [x] 7.5 Write property test for reflection hybrid storage
    - **Property 20: Reflection hybrid storage**
    - Test content stored in filesystem, metadata in database
    - **Validates: Requirements 28.1-28.11**

  - [x] 7.6 Create ChatThreadManager (backend/core/chat_thread_manager.py)
    - Implement thread creation with workspace binding
    - Implement message storage (ChatMessages table)
    - Implement thread summary generation (ThreadSummaries table)
    - Inherit workspace_id from ToDo or Task when applicable
    - _Requirements: 30.1-30.14_

  - [x] 7.7 Write property test for ChatThread workspace binding
    - **Property 22: ChatThread workspace binding**
    - Test ChatThread inherits workspace_id from ToDo/Task
    - **Validates: Requirements 30.1-30.14**

  - [x] 7.8 Create SearchManager (backend/core/search_manager.py)
    - Implement search across entity types (ToDos, Tasks, PlanItems, Communications, Artifacts, Reflections)
    - Implement thread search via ThreadSummary (NOT raw ChatMessages)
    - Support scope filtering (workspace_id or "all")
    - Limit results to 50 per entity type
    - _Requirements: 31.1-31.7, 38.1-38.12_

  - [x] 7.9 Write property test for ThreadSummary search indexing
    - **Property 23: ThreadSummary search indexing**
    - Test search queries ThreadSummary.summary_text, NOT ChatMessages.content
    - **Validates: Requirements 31.1-31.7**

  - [x] 7.10 Write property test for search scope
    - **Property 28: Search respects scope**
    - Test scope=workspace_id returns only matching items, scope="all" returns all non-archived
    - **Validates: Requirements 38.1-38.12**

- [x] 8. Backend Manager Classes - Workspace Lifecycle
  - [x] 8.1 Update SwarmWorkspaceManager for archive functionality (backend/core/swarm_workspace_manager.py)
    - Implement archive method (set is_archived=true, archived_at=now)
    - Implement unarchive method (set is_archived=false, archived_at=null)
    - Implement list_non_archived method
    - Ensure SwarmWS cannot be archived (is_default=true check)
    - _Requirements: 36.1-36.11_

  - [x] 8.2 Write property test for archived workspace read-only
    - **Property 24: Archived workspace read-only**
    - Test write operations fail on archived workspaces, read operations succeed
    - **Validates: Requirements 36.1-36.11**

  - [x] 8.3 Write property test for archived workspace aggregation exclusion
    - **Property 25: Archived workspace excluded from aggregation**
    - Test "all" aggregation excludes is_archived=true workspaces
    - **Validates: Requirements 36.5**

  - [x] 8.4 Write property test for archived workspace conversion suggestion
    - **Property 26: Archived workspace not suggested for conversion**
    - Test archived workspaces not in suggestion list, selection blocked
    - **Validates: Requirements 32.6, 32.7**

  - [x] 8.5 Update SwarmWorkspaceManager for SwarmWS behavior
    - Ensure SwarmWS is created on first launch (is_default=true)
    - Ensure SwarmWS cannot be deleted (check is_default before delete)
    - Ensure SwarmWS is always first in list (sort by is_default desc)
    - _Requirements: 1.1-1.6_

  - [x] 8.6 Write property test for SwarmWS always first
    - **Property 1: SwarmWS always first in workspace list**
    - Test workspace list always has is_default=true at index 0
    - **Validates: Requirements 1.1**

  - [x] 8.7 Write property test for SwarmWS deletion prevention
    - **Property 2: SwarmWS deletion prevention**
    - Test delete on is_default=true raises ForbiddenError
    - **Validates: Requirements 1.2**

  - [x] 8.8 Update workspace creation to create required folders only
    - Create Artifacts/ with subfolders (Plans/, Reports/, Docs/, Decisions/)
    - Create ContextFiles/ with context.md
    - Create Transcripts/ folder (sibling to Artifacts/, for optional export)
    - Do NOT create Tasks/, ToDos/, Plans/, Communications/ folders
    - _Requirements: 2.3, 2.7, 35.1-35.6_

  - [x] 8.9 Write property test for workspace folder creation
    - **Property 4: Workspace creation creates required folders**
    - Test only Artifacts/, ContextFiles/, Transcripts/ exist, no DB-entity folders
    - **Validates: Requirements 2.3, 2.7, 4.9, 5.8, 35.1-35.6**

  - [x] 8.10 Write property test for custom workspace deletion
    - **Property 5: Custom workspace deletion**
    - Test delete on is_default=false succeeds
    - **Validates: Requirements 2.5**

- [x] 9. Checkpoint - Backend Managers Complete
  - Run `cd backend && pytest tests/` to verify all tests pass
  - Verify all 30 property tests pass with 100+ iterations each
  - Ask the user if questions arise

- [x] 10. Backend API Routers - ToDos and Sections
  - [x] 10.1 Create todos router (backend/routers/todos.py)
    - GET /api/todos - list with workspace_id, status filters, limit/offset pagination
    - POST /api/todos - create new ToDo
    - GET /api/todos/{id} - get specific ToDo
    - PUT /api/todos/{id} - update ToDo
    - DELETE /api/todos/{id} - soft delete ToDo (set status to deleted)
    - POST /api/todos/{id}/convert-to-task - convert to Task
    - Return snake_case field names
    - _Requirements: 6.1-6.8_

  - [x] 10.2 Create sections router (backend/routers/sections.py)
    - GET /api/workspaces/{id}/sections - aggregated counts for all six sections
    - GET /api/workspaces/{id}/sections/signals - ToDos grouped by status
    - GET /api/workspaces/{id}/sections/plan - PlanItems grouped by focus_type
    - GET /api/workspaces/{id}/sections/execute - Tasks grouped by status
    - GET /api/workspaces/{id}/sections/communicate - Communications grouped by status
    - GET /api/workspaces/{id}/sections/artifacts - Artifacts grouped by artifact_type
    - GET /api/workspaces/{id}/sections/reflection - Reflections grouped by reflection_type
    - Support workspace_id="all" for aggregation
    - Support limit, offset, sort_by, sort_order query parameters
    - Return unified SectionResponse contract
    - _Requirements: 7.1-7.12_

  - [x] 10.3 Write unit tests for todos router endpoints
    - Test CRUD operations
    - Test conversion to task
    - Test pagination and filtering
    - Test error responses (404, 400)
    - _Requirements: 6.1-6.8_

  - [x] 10.4 Write unit tests for sections router endpoints
    - Test section counts
    - Test grouped responses
    - Test "all" workspace aggregation
    - Test pagination
    - _Requirements: 7.1-7.12_

- [x] 11. Backend API Routers - Entity CRUD
  - [x] 11.1 Create plan_items router (backend/routers/plan_items.py)
    - GET /api/workspaces/{id}/plan-items - list with filters
    - POST /api/workspaces/{id}/plan-items - create
    - PUT /api/workspaces/{id}/plan-items/{item_id} - update
    - DELETE /api/workspaces/{id}/plan-items/{item_id} - delete
    - _Requirements: 22.8_

  - [x] 11.2 Create communications router (backend/routers/communications.py)
    - GET /api/workspaces/{id}/communications - list with filters
    - POST /api/workspaces/{id}/communications - create
    - PUT /api/workspaces/{id}/communications/{comm_id} - update
    - DELETE /api/workspaces/{id}/communications/{comm_id} - delete
    - _Requirements: 23.8_

  - [x] 11.3 Create artifacts router (backend/routers/artifacts.py)
    - GET /api/workspaces/{id}/artifacts - list with filters
    - POST /api/workspaces/{id}/artifacts - create (with file upload)
    - PUT /api/workspaces/{id}/artifacts/{artifact_id} - update (creates new version)
    - DELETE /api/workspaces/{id}/artifacts/{artifact_id} - delete
    - _Requirements: 27.8_

  - [x] 11.4 Create reflections router (backend/routers/reflections.py)
    - GET /api/workspaces/{id}/reflections - list with filters
    - POST /api/workspaces/{id}/reflections - create
    - PUT /api/workspaces/{id}/reflections/{reflection_id} - update
    - DELETE /api/workspaces/{id}/reflections/{reflection_id} - delete
    - _Requirements: 28.9_

  - [x] 11.5 Create search router (backend/routers/search.py)
    - GET /api/search - search across entity types with query, scope, entity_types params
    - GET /api/search/threads - search via ThreadSummary
    - Limit 50 results per entity type
    - _Requirements: 31.7, 38.10_

- [x] 12. Backend API Routers - Workspace Configuration
  - [x] 12.1 Create workspace_config router (backend/routers/workspace_config.py)
    - GET /api/workspaces/{id}/skills - get effective skills
    - PUT /api/workspaces/{id}/skills - update skill configs
    - GET /api/workspaces/{id}/mcps - get effective MCPs
    - PUT /api/workspaces/{id}/mcps - update MCP configs
    - GET /api/workspaces/{id}/knowledgebases - get knowledgebases
    - POST /api/workspaces/{id}/knowledgebases - add knowledgebase
    - PUT /api/workspaces/{id}/knowledgebases/{kb_id} - update knowledgebase
    - DELETE /api/workspaces/{id}/knowledgebases/{kb_id} - delete knowledgebase
    - GET /api/workspaces/{id}/context - get context.md content
    - PUT /api/workspaces/{id}/context - update context.md content
    - POST /api/workspaces/{id}/context/compress - trigger compression
    - GET /api/workspaces/{id}/audit-log - get audit log with pagination
    - _Requirements: 19.6, 19.7, 19.8, 25.5, 29.9, 29.10_

  - [x] 12.2 Implement policy enforcement in task execution
    - Validate required skills/MCPs before execution
    - Return 409 Conflict with policy_violations array when blocked
    - Include suggestedAction in error response
    - _Requirements: 26.1-26.7, 34.1-34.7_

  - [x] 12.3 Write property test for policy enforcement
    - **Property 17: Policy enforcement blocks execution**
    - Test disabled skill/MCP returns 409 with policy_violations
    - **Validates: Requirements 26.1-26.7, 34.1-34.7**

  - [x] 12.4 Write unit tests for workspace config endpoints
    - Test skills/MCPs/knowledgebases CRUD
    - Test context management
    - Test audit log retrieval
    - Test privileged capability confirmation
    - _Requirements: 19.6-19.9_

- [x] 13. Backend API Routers - Global View and Archive
  - [x] 13.1 Update workspace endpoints for archive support
    - POST /api/workspaces/{id}/archive - archive workspace
    - POST /api/workspaces/{id}/unarchive - unarchive workspace
    - Filter archived workspaces from default list (add include_archived param)
    - Return 403 for write operations on archived workspaces
    - _Requirements: 36.1-36.11_

  - [x] 13.2 Implement SwarmWS Global View aggregation
    - Support workspace_id="all" in section endpoints
    - Implement "Recommended" group for SwarmWS Global View (top N by priority desc, updated_at desc)
    - Distinguish opinionated (SwarmWS Global) vs neutral ("all" scope) aggregation
    - _Requirements: 37.1-37.12_

  - [x] 13.3 Write property test for SwarmWS Global View aggregation
    - **Property 27: SwarmWS Global View aggregation**
    - Test Global View aggregates all non-archived workspaces
    - **Validates: Requirements 37.1-37.12**

- [x] 14. Checkpoint - Backend API Complete
  - Run `cd backend && pytest tests/` to verify all tests pass
  - Test API endpoints manually with curl or Postman
  - Verify error responses follow standard format
  - Ask the user if questions arise

- [x] 15. Frontend TypeScript Types
  - [x] 15.1 Create ToDo types (desktop/src/types/todo.ts)
    - ToDoStatus type: 'pending' | 'overdue' | 'inDiscussion' | 'handled' | 'cancelled' | 'deleted'
    - ToDoSourceType type: 'manual' | 'email' | 'slack' | 'meeting' | 'integration'
    - Priority type: 'high' | 'medium' | 'low' | 'none'
    - ToDo, ToDoCreateRequest, ToDoUpdateRequest interfaces (camelCase)
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

  - [x] 15.2 Create PlanItem types (desktop/src/types/plan-item.ts)
    - PlanItemStatus type: 'active' | 'deferred' | 'completed' | 'cancelled'
    - FocusType type: 'today' | 'upcoming' | 'blocked'
    - PlanItem interface
    - _Requirements: 22.1-22.4_

  - [x] 15.3 Create Communication types (desktop/src/types/communication.ts)
    - CommunicationStatus type: 'pendingReply' | 'aiDraft' | 'followUp' | 'sent' | 'cancelled'
    - ChannelType type: 'email' | 'slack' | 'meeting' | 'other'
    - Communication interface
    - _Requirements: 23.1-23.4_

  - [x] 15.4 Create Artifact and Reflection types (desktop/src/types/artifact.ts, reflection.ts)
    - ArtifactType type: 'plan' | 'report' | 'doc' | 'decision' | 'other'
    - ReflectionType type: 'dailyRecap' | 'weeklySummary' | 'lessonsLearned'
    - Artifact, Reflection interfaces
    - _Requirements: 27.10, 28.11_

  - [x] 15.5 Create ChatThread types (desktop/src/types/chat-thread.ts)
    - ChatMode type: 'explore' | 'execute'
    - MessageRole type: 'user' | 'assistant' | 'tool' | 'system'
    - ChatThread, ChatMessage, ThreadSummary interfaces
    - _Requirements: 30.12_

  - [x] 15.6 Create Section types (desktop/src/types/section.ts)
    - WorkspaceSection type: 'signals' | 'plan' | 'execute' | 'communicate' | 'artifacts' | 'reflection'
    - SectionCounts interface with counts for each section and sub-category
    - SectionGroup<T>, Pagination, SectionResponse<T> generic interfaces
    - _Requirements: 8.5, 8.6, 8.9_

  - [x] 15.7 Create workspace configuration types (desktop/src/types/workspace-config.ts)
    - WorkspaceSkillConfig, WorkspaceMcpConfig, WorkspaceKnowledgebaseConfig interfaces
    - PolicyViolation interface
    - _Requirements: 19.9_

  - [x] 15.8 Update Task interface with new fields (desktop/src/types/index.ts)
    - Add workspaceId, sourceTodoId, blockedReason, priority, description
    - Update TaskStatus type: 'draft' | 'wip' | 'blocked' | 'completed' | 'cancelled'
    - _Requirements: 8.7_

  - [x] 15.9 Update SwarmWorkspace interface (desktop/src/types/index.ts)
    - Add isArchived: boolean, archivedAt?: string fields
    - _Requirements: 36.2_

- [x] 16. Frontend Services
  - [x] 16.1 Create todos service (desktop/src/services/todos.ts)
    - list, get, create, update, delete methods
    - convertToTask method
    - Implement toCamelCase transformation (snake_case → camelCase)
    - Implement toSnakeCase transformation (camelCase → snake_case)
    - _Requirements: 6.1-6.8, 8.8_

  - [x] 16.2 Create sections service (desktop/src/services/sections.ts)
    - getCounts, getSignals, getPlan, getExecute, getCommunicate, getArtifacts, getReflection methods
    - Support "all" workspace scope
    - Transform responses to camelCase
    - _Requirements: 7.1-7.12_

  - [x] 16.3 Create workspaceConfig service (desktop/src/services/workspaceConfig.ts)
    - getSkills, updateSkills, getMcps, updateMcps methods
    - getKnowledgebases, addKnowledgebase, updateKnowledgebase, deleteKnowledgebase methods
    - getContext, updateContext, compressContext methods
    - getAuditLog method
    - _Requirements: 19.6-19.8_

  - [x] 16.4 Create search service (desktop/src/services/search.ts)
    - search method with query, scope, entityTypes params
    - searchThreads method
    - Transform responses to camelCase
    - _Requirements: 38.10_

  - [x] 16.5 Update tasks service for new fields (desktop/src/services/tasks.ts)
    - Add workspace_id parameter to list method
    - Update toCamelCase for: workspaceId, sourceTodoId, blockedReason, priority, description
    - Update toSnakeCase for same fields
    - _Requirements: 10.4, 10.5_

  - [x] 16.6 Update workspaces service for archive (desktop/src/services/workspaces.ts)
    - Add archive, unarchive methods
    - Update list to support includeArchived param
    - Update toCamelCase for: isArchived, archivedAt
    - _Requirements: 36.1-36.11_

  - [x] 16.7 Write unit tests for frontend services
    - Test API calls and transformations
    - Test error handling
    - Test camelCase/snakeCase conversions
    - _Requirements: 8.8_

- [x] 17. Checkpoint - Frontend Types and Services
  - Run `cd desktop && npm test -- --run` to verify all tests pass
  - Verify TypeScript compilation succeeds
  - Ask the user if questions arise

- [x] 18. Frontend Components - Workspace Explorer
  - [x] 18.1 Create WorkspaceExplorer container component (desktop/src/components/workspace-explorer/WorkspaceExplorer.tsx)
    - Layout: Header, OverviewContextCard, SectionNavigation, Footer
    - Manage workspace selection state
    - Manage view/scope toggle state (Global vs Scoped)
    - _Requirements: 3.1, 9.1_

  - [x] 18.2 Create WorkspaceHeader component (desktop/src/components/workspace-explorer/WorkspaceHeader.tsx)
    - Workspace selector dropdown (SwarmWS pinned with 🏠, custom with 📁)
    - View/Scope toggle (Global vs Scoped)
    - Global search bar with placeholder "Search… (threads, tasks, signals, artifacts)"
    - _Requirements: 3.2, 9.1, 9.2, 9.3_

  - [x] 18.3 Create OverviewContextCard component (desktop/src/components/workspace-explorer/OverviewContextCard.tsx)
    - Display Goal, Focus, Context, Priorities
    - Edit Context button with inline editing
    - Sync changes to ContextFiles/context.md via API
    - _Requirements: 3.3, 9.4_

  - [x] 18.4 Create SectionNavigation component (desktop/src/components/workspace-explorer/SectionNavigation.tsx)
    - Six collapsible section headers with icons and counts
    - Section icons: Signals (🔔), Plan (🗓️), Execute (▶️), Communicate (💬), Artifacts (📦), Reflection (🧠)
    - _Requirements: 3.4, 3.5, 9.5, 9.6_

  - [x] 18.5 Create SectionHeader component (desktop/src/components/workspace-explorer/SectionHeader.tsx)
    - Icon, title, count badge
    - Expand/collapse toggle
    - Sub-category counts
    - _Requirements: 3.6-3.11, 9.7_

  - [x] 18.6 Create SectionContent component (desktop/src/components/workspace-explorer/SectionContent.tsx)
    - Display sub-category items with sample titles (max 2-3 per sub-category)
    - Click navigation to detail views
    - _Requirements: 9.8_

  - [x] 18.7 Create WorkspaceFooter component (desktop/src/components/workspace-explorer/WorkspaceFooter.tsx)
    - "+ New Workspace" button (opens WorkspacesModal)
    - "⚙️ Workspace Settings" button (opens SettingsModal)
    - Archive/Delete context menu (⋯) for custom workspaces
    - _Requirements: 3.14, 9.12, 9.13_

  - [x] 18.8 Implement keyboard navigation
    - Arrow keys between sections and items
    - Enter to select
    - _Requirements: 9.10_

  - [x] 18.9 Implement Artifacts section file tree
    - Collapsible file tree sub-section
    - Browse Artifacts/, ContextFiles/, Transcripts/
    - _Requirements: 3.10, 9.11_

  - [x] 18.10 Write unit tests for WorkspaceExplorer components
    - Test rendering and interactions
    - Test state management
    - Test keyboard navigation
    - _Requirements: 3.1-3.14_

- [x] 19. Frontend Components - Section Pages
  - [x] 19.1 Create SignalsPage component (desktop/src/pages/SignalsPage.tsx)
    - Table with columns: Title, Source, Status, Priority, Due Date, Actions
    - Filters for status and priority
    - Search bar
    - Quick Capture button
    - Edit, Convert to Task, Delete actions
    - _Requirements: 11.1-11.8_

  - [x] 19.2 Create ConvertToTaskModal component (desktop/src/components/modals/ConvertToTaskModal.tsx)
    - Task configuration form
    - Workspace suggestion with "Suggested" badge
    - Block archived workspace selection with explanation message
    - _Requirements: 11.7, 32.1-32.7_

  - [x] 19.3 Rename TasksPage to ExecutePage (desktop/src/pages/ExecutePage.tsx)
    - Update page title to "Execute"
    - Add workspace_id filter
    - Update status filter for new values: draft, wip, blocked, completed, cancelled
    - _Requirements: 10.1-10.6_

  - [x] 19.4 Create PlanPage component (desktop/src/pages/PlanPage.tsx)
    - Display PlanItems grouped by focus_type (Today's Focus, Upcoming, Blocked)
    - Support reordering within categories via drag-and-drop
    - _Requirements: 22.6_

  - [x] 19.5 Create CommunicatePage component (desktop/src/pages/CommunicatePage.tsx)
    - Display Communications grouped by status
    - AI draft content display
    - _Requirements: 23.1-23.11_

  - [x] 19.6 Create ArtifactsPage component (desktop/src/pages/ArtifactsPage.tsx)
    - Display Artifacts grouped by type
    - Version history display
    - Tag management
    - _Requirements: 27.1-27.11_

  - [x] 19.7 Create ReflectionPage component (desktop/src/pages/ReflectionPage.tsx)
    - Display Reflections grouped by type
    - Daily recap and weekly summary views
    - _Requirements: 28.1-28.11_

  - [x] 19.8 Write unit tests for section pages
    - Test rendering and interactions
    - Test filtering and pagination
    - _Requirements: 11.1-11.8_

- [x] 20. Frontend Components - Workspace Settings
  - [x] 20.1 Create WorkspaceSettingsModal component (desktop/src/components/modals/WorkspaceSettingsModal.tsx)
    - Three tabs: Skills, MCPs, Knowledgebases
    - _Requirements: 20.1, 20.2_

  - [x] 20.2 Create SkillsTab component (desktop/src/components/workspace-settings/SkillsTab.tsx)
    - Toggle switches for enabling/disabling
    - Warning icon (⚠️) for privileged skills
    - Visual indicator for inherited vs workspace-specific
    - _Requirements: 20.3, 20.6_

  - [x] 20.3 Create McpsTab component (desktop/src/components/workspace-settings/McpsTab.tsx)
    - Toggle switches for enabling/disabling
    - Warning icon (⚠️) for privileged MCPs
    - Visual indicator for inherited vs workspace-specific
    - _Requirements: 20.4, 20.6_

  - [x] 20.4 Create KnowledgebasesTab component (desktop/src/components/workspace-settings/KnowledgebasesTab.tsx)
    - Add/edit/remove sources
    - Inherited sources with exclude toggles
    - _Requirements: 20.5, 20.8_

  - [x] 20.5 Create PrivilegedCapabilityModal component (desktop/src/components/modals/PrivilegedCapabilityModal.tsx)
    - Confirmation dialog for privileged capabilities
    - Explain elevated permissions
    - _Requirements: 20.9_

  - [x] 20.6 Implement warning for disabling capabilities
    - Display warning when disabling may affect agent functionality
    - _Requirements: 20.8_

  - [x] 20.7 Write unit tests for workspace settings components
    - Test toggle interactions
    - Test confirmation dialogs
    - _Requirements: 20.1-20.9_

- [x] 21. Checkpoint - Frontend Components
  - Run `cd desktop && npm test -- --run` to verify all tests pass
  - Verify components render correctly in browser
  - Ask the user if questions arise

- [x] 22. Frontend Components - Search and Navigation
  - [x] 22.1 Create GlobalSearchBar component (desktop/src/components/search/GlobalSearchBar.tsx)
    - Debounced input (300ms)
    - Search across all entity types
    - Respect current workspace scope
    - _Requirements: 38.1, 38.3, 38.9_

  - [x] 22.2 Create SearchResults component (desktop/src/components/search/SearchResults.tsx)
    - Group results by entity type
    - Display title, type badge, workspace name, timestamp
    - Archived badge for archived workspace items
    - _Requirements: 38.4, 38.6, 38.12_

  - [x] 22.3 Implement search result navigation
    - Click to navigate to detail view
    - Keyboard navigation support (arrow keys, Enter)
    - _Requirements: 38.7, 38.8_

  - [x] 22.4 Update routing for new pages (desktop/src/App.tsx)
    - Add /signals route
    - Add workspace-scoped routes: /workspaces/{id}/signals, /workspaces/{id}/execute, etc.
    - _Requirements: 15.1, 15.2_

  - [x] 22.5 Update sidebar navigation
    - Reflect new page structure
    - Support deep linking
    - _Requirements: 15.4, 15.5_

  - [x] 22.6 Write unit tests for search and navigation
    - Test search functionality
    - Test routing
    - Test keyboard navigation
    - _Requirements: 38.1-38.12_

- [x] 23. Frontend - View/Scope Toggle and Aggregation
  - [x] 23.1 Implement View/Scope toggle for SwarmWS
    - "Global (All Workspaces)" vs "SwarmWS-only" modes
    - Persist selection across sessions (localStorage)
    - _Requirements: 37.1-37.4_

  - [x] 23.2 Implement scoped view for custom workspaces
    - Default to "This Workspace" scope
    - Optional "All Workspaces" toggle
    - _Requirements: 37.6, 37.7_

  - [x] 23.3 Update section counts for view mode
    - Global View: aggregated totals
    - Scoped View: workspace-only totals
    - _Requirements: 37.11, 9.14_

  - [x] 23.4 Implement "Recommended" group for SwarmWS Global View
    - Top N items (default N=3) based on priority desc, then updated_at desc
    - Only show in SwarmWS Global View (opinionated), not in "all" scope (neutral)
    - _Requirements: 37 (SwarmWS Global View Aggregation Rules)_

  - [x] 23.5 Write unit tests for view/scope toggle
    - Test mode switching
    - Test count updates
    - Test persistence
    - _Requirements: 37.1-37.12_

- [x] 24. Frontend - Archive Functionality
  - [x] 24.1 Implement archive/unarchive UI
    - Archive option in workspace context menu (⋯)
    - "Show Archived" toggle in workspace selector
    - Visual indicator for archived workspaces (muted color, archive icon)
    - _Requirements: 36.1, 36.4, 36.11_

  - [x] 24.2 Implement read-only mode for archived workspaces
    - Disable create/edit actions
    - Display read-only indicator
    - Show explanation when user attempts write operation
    - _Requirements: 36.6_

  - [x] 24.3 Write unit tests for archive functionality
    - Test archive/unarchive flow
    - Test read-only enforcement
    - Test visual indicators
    - _Requirements: 36.1-36.11_

- [x] 25. Checkpoint - Frontend Complete
  - Run `cd desktop && npm test -- --run` to verify all tests pass
  - Run `cd desktop && npm run build` to verify build succeeds
  - Test full UI flow in browser
  - Ask the user if questions arise

- [x] 26. Integration and Wiring
  - [x] 26.1 Wire WorkspaceExplorer to main layout
    - Replace current file tree with new WorkspaceExplorer
    - Connect to workspace state management
    - _Requirements: 3.1_

  - [x] 26.2 Wire section pages to routing
    - Connect SignalsPage, ExecutePage, PlanPage, CommunicatePage, ArtifactsPage, ReflectionPage
    - Preserve workspace scope on navigation
    - _Requirements: 15.3_

  - [x] 26.3 Wire workspace settings to existing modals
    - Connect WorkspaceSettingsModal to settings button
    - Connect to existing WorkspacesModal for new workspace creation
    - _Requirements: 3.14_

  - [x] 26.4 Wire context injection to agent execution
    - Include workspace context in agent system prompt
    - Apply effective Skills/MCPs/Knowledgebases
    - Respect token budget (default 4000)
    - _Requirements: 14.1-14.9, 21.1-21.7_

  - [x] 26.5 Wire policy enforcement to task execution
    - Validate configuration before execution
    - Display policy conflict UI (409 response handling)
    - Offer "Resolve" action to navigate to workspace settings
    - _Requirements: 34.4, 34.5_

  - [x] 26.6 Write integration tests for wiring
    - Test end-to-end flows
    - Test context injection
    - Test policy enforcement UI
    - _Requirements: 14.1-14.9_

- [x] 27. Mock Data Generation
  - [x] 27.1 Create mock data generation script (backend/scripts/generate_mock_data.py)
    - Generate mock ToDos for SwarmWS with various statuses and priorities
    - Generate mock Tasks for SwarmWS with various statuses
    - Generate mock PlanItems, Communications, Artifacts, Reflections
    - Create TestWS workspace with mock data
    - _Requirements: 12.1-12.6_

  - [x] 27.2 Implement development-only API endpoint for mock data
    - POST /api/dev/generate-mock-data
    - Skip if mock data already exists (check by count)
    - Only available when DEBUG=true
    - _Requirements: 12.5, 12.6_

  - [x] 27.3 Write unit tests for mock data generation
    - Test data generation
    - Test duplicate prevention
    - _Requirements: 12.1-12.6_

- [x] 28. Final Integration Testing
  - [x] 28.1 End-to-end test: ToDo lifecycle
    - Create ToDo → Edit → Convert to Task → Verify linkage
    - _Requirements: 4.7, 4.8_

  - [x] 28.2 End-to-end test: Workspace configuration inheritance
    - Configure SwarmWS skills → Create custom workspace → Verify intersection
    - _Requirements: 16.5, 17.5_

  - [x] 28.3 End-to-end test: Archive workflow
    - Archive workspace → Verify read-only → Verify excluded from aggregation → Unarchive
    - _Requirements: 36.1-36.11_

  - [x] 28.4 End-to-end test: Global View aggregation
    - Create items in multiple workspaces → Switch to Global View → Verify aggregation
    - _Requirements: 37.1-37.12_

  - [x] 28.5 End-to-end test: Search functionality
    - Create items → Search → Verify scope filtering → Navigate to results
    - _Requirements: 38.1-38.12_

- [x] 29. Checkpoint - Integration Complete
  - Run full test suite: `cd backend && pytest && cd ../desktop && npm test -- --run`
  - Verify all 30 property tests pass
  - Manual testing of all user flows
  - Ask the user if questions arise

## Property Test Summary

| Property | Description | Requirements |
|----------|-------------|--------------|
| 1 | SwarmWS always first in workspace list | 1.1 |
| 2 | SwarmWS deletion prevention | 1.2 |
| 3 | Default workspace assignment | 1.3, 1.4 |
| 4 | Workspace creation creates required folders | 2.3, 2.7, 35.1-35.6 |
| 5 | Custom workspace deletion | 2.5 |
| 6 | Entity enum field validation | 4.2, 4.3, 4.4, 5.2, 5.3 |
| 7 | Overdue detection | 4.5, 4.6 |
| 8 | ToDo to Task conversion round-trip | 4.7, 4.8, 5.6 |
| 9 | Task status backward compatibility | 5.4 |
| 10 | Blocked task preserves reason | 5.5 |
| 11 | Section endpoint unified response contract | 7.1-7.12, 33.1-33.6 |
| 12 | Skills configuration intersection model | 16.1-16.11, 21.1-21.7, 24.3 |
| 13 | MCPs configuration intersection model | 17.1-17.11, 21.1-21.7, 24.4 |
| 14 | Knowledgebases union model with exclusions | 18.1-18.9, 24.5 |
| 15 | Privileged capability requires explicit enablement | 16.2, 16.11, 17.2, 17.11 |
| 16 | Configuration change audit logging | 25.1-25.8 |
| 17 | Policy enforcement blocks execution | 26.1-26.7, 34.1-34.7 |
| 18 | Artifact hybrid storage | 27.1-27.11 |
| 19 | Artifact versioning | 27.4, 27.5 |
| 20 | Reflection hybrid storage | 28.1-28.11 |
| 21 | Context file creation | 29.1-29.10 |
| 22 | ChatThread workspace binding | 30.1-30.14 |
| 23 | ThreadSummary search indexing | 31.1-31.7 |
| 24 | Archived workspace read-only | 36.1-36.11 |
| 25 | Archived workspace excluded from aggregation | 36.5 |
| 26 | Archived workspace not suggested for conversion | 32.6, 32.7 |
| 27 | SwarmWS Global View aggregation | 37.1-37.12 |
| 28 | Search respects scope | 38.1-38.12 |
| 29 | PlanItem linked task completion cascade | 22.7 |
| 30 | Communication sent timestamp | 23.6 |
