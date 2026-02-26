# Requirements Document — Thread-Scoped Cognitive Context (TSCC)

## Introduction

This document defines the requirements for the **Thread-Scoped Cognitive Context (TSCC)** feature — a thread-owned, collapsible cognitive context panel placed directly above the chat input box in SwarmAI. TSCC provides live, thread-specific cognitive context without interrupting chat flow, and is archived with the chat thread.

TSCC answers six key questions for the current thread:
1. Where am I working? (scope clarity)
2. What is the AI doing right now? (live activity)
3. Which agents are involved? (execution transparency)
4. What capabilities are being used? (skills, MCPs, tools)
5. What sources is the AI using? (trust + grounding)
6. What is the current working conclusion? (continuity)

This is **Cadence 5 of the SwarmWS redesign series**, building on:
- Cadence 1: `swarmws-foundation` — Single workspace, folder structure
- Cadence 2: `swarmws-projects` — Project CRUD, templates
- Cadence 3: `swarmws-explorer-ux` — Workspace Explorer UX
- Cadence 4: `swarmws-intelligence` — Context assembly, chat threads, preview API

### Relationship to ContextPreviewPanel (Cadence 4)

TSCC coexists with the `ContextPreviewPanel` introduced in Cadence 4. They serve different purposes:
- **ContextPreviewPanel** = developer/power-user tool showing raw 8-layer context assembly, accessible from project detail view
- **TSCC** = user-facing cognitive panel showing human-readable agent state, always above chat input

### Cross-References

| Cadence | Spec | Focus |
|---------|------|-------|
| 1 | `swarmws-foundation` | Single workspace, folder structure |
| 2 | `swarmws-projects` | Project CRUD, templates |
| 3 | `swarmws-explorer-ux` | Workspace Explorer UX |
| 4 | `swarmws-intelligence` | Context assembly, chat threads, preview API |
| **5** | **`thread-scoped-cognitive-context`** | **TSCC panel, SSE telemetry, snapshots** |

### Design Principles Alignment

- **Chat is the Command Surface** — TSCC enhances the chat interface without replacing it
- **Visible Planning Builds Trust** — TSCC makes agent reasoning and capabilities transparent
- **Multi-Agent Orchestration Should Be Visible** — TSCC shows active agents and their roles
- **Context > Conversation** — TSCC surfaces what context and sources the agent is using
- **Gradual Disclosure** — TSCC is collapsed by default, expands on demand

## Glossary

- **TSCC**: Thread-Scoped Cognitive Context. A collapsible panel anchored above the chat input that displays live, thread-specific cognitive state including scope, agents, capabilities, sources, activity, and summary.
- **TSCC_Panel**: The frontend React component that renders the TSCC collapsed bar and expanded module view.
- **Cognitive_Module**: One of the five information sections within the expanded TSCC: Current Context, Active Agents & Capabilities, What AI is Doing, Active Sources, and Key Summary.
- **TSCC_State**: The live cognitive state object associated with a single chat thread, containing data for all five Cognitive_Modules.
- **TSCC_Snapshot**: A point-in-time capture of the TSCC_State, stored as a JSON file in the thread's snapshot directory and viewable in thread history.
- **Collapsed_Bar**: The single-line summary view of TSCC showing scope, agent count, capability summary, source count, and freshness indicator.
- **Expanded_View**: The full multi-module view of TSCC showing all five Cognitive_Modules.
- **Scope_Type**: The operational scope of a thread — either `workspace` (SwarmWS root, no project) or `project` (bound to a specific project).
- **Freshness_Indicator**: A relative timestamp label (e.g., "Updated just now", "Updated 2m ago") shown in the Collapsed_Bar indicating when TSCC_State was last updated.
- **Telemetry_Event**: A new SSE event type emitted by the backend during agent execution to provide real-time data about agent activity, tool invocations, and capability activation to the TSCC_Panel.
- **Snapshot_Trigger**: A condition that causes the system to capture a TSCC_Snapshot (e.g., plan decomposition completed, decision recorded, multi-step phase completed).
- **Thread_Lifecycle_State**: The current state of a chat thread from TSCC's perspective: `new`, `active`, `paused`, `failed`, `cancelled`, or `idle`.
- **ContextPreviewPanel**: The developer-facing context preview panel from Cadence 4 (Requirement 33) that shows raw 8-layer context assembly. Distinct from TSCC.
- **StreamEvent**: The existing SSE event interface used for chat streaming, defined in `desktop/src/types/index.ts`.
- **ChatPage**: The main orchestrator component for the chat feature, located at `desktop/src/pages/ChatPage.tsx`.

## Requirements

### Requirement 1: TSCC Panel Placement and Layout

**User Story:** As a knowledge worker, I want a cognitive context panel anchored above the chat input, so that I can see thread-specific context without scrolling away from my conversation.

#### Acceptance Criteria

1. THE TSCC_Panel SHALL render between the message list and the ChatInput component within the ChatPage component hierarchy.
2. THE TSCC_Panel SHALL be visible in every chat thread regardless of thread state or project association.
3. THE TSCC_Panel SHALL use soft visual separators and a calm background that does not visually compete with chat messages.
4. THE TSCC_Panel SHALL use CSS variables in `--color-*` format for all colors.
5. THE TSCC_Panel SHALL not cause scroll position changes in the message list when expanding or collapsing.
6. THE TSCC_Panel SHALL not block or obscure the ChatInput component in any state.

### Requirement 2: Collapsed State (Default View)

**User Story:** As a knowledge worker, I want a minimal one-line context summary by default, so that I have situational awareness without cognitive overload.

#### Acceptance Criteria

1. THE TSCC_Panel SHALL render in collapsed state by default when a thread is opened.
2. THE Collapsed_Bar SHALL display a single-line summary containing: scope label (workspace or project name), active agent count, capability summary (up to 2 capability names), source count, and Freshness_Indicator.
3. WHEN the user clicks anywhere on the Collapsed_Bar, THE TSCC_Panel SHALL expand to show the Expanded_View.
4. THE Collapsed_Bar SHALL provide a pin toggle that keeps the TSCC_Panel expanded across messages within the same thread.
5. THE Collapsed_Bar SHALL update its content silently as TSCC_State changes without visual disruption.
6. WHEN no agents are active and the thread is idle, THE Collapsed_Bar SHALL display a contextually appropriate summary (e.g., "Context ▸ Workspace: SwarmWS · Ready").

### Requirement 3: Expanded View — Current Context Module

**User Story:** As a knowledge worker, I want to see the operational scope of my current thread, so that I always know where I am working.

#### Acceptance Criteria

1. THE Current Context Cognitive_Module SHALL display the workspace name or project name based on the thread's Scope_Type.
2. THE Current Context Cognitive_Module SHALL display the thread title.
3. THE Current Context Cognitive_Module SHALL display an optional mode tag (e.g., Research, Writing, Debugging, Exploration) when the agent has identified a working mode.
4. WHEN the thread has no associated project (Scope_Type is `workspace`), THE Current Context Cognitive_Module SHALL display "Workspace: SwarmWS (General)" as a positive, intentional scope label.
5. THE Current Context Cognitive_Module SHALL never display negative labels such as "No project selected" or "Project: None".
6. WHEN a project is associated with the thread mid-session, THE Current Context Cognitive_Module SHALL update seamlessly to show the project name without requiring a page reload.

### Requirement 4: Expanded View — Active Agents & Capabilities Module

**User Story:** As a knowledge worker, I want to see which agents, skills, MCPs, and tools are active in my thread, so that I understand the execution resources being used.

#### Acceptance Criteria

1. THE Active Agents & Capabilities Cognitive_Module SHALL display a list of subagents currently engaged in the thread, using human-readable agent names.
2. THE Active Agents & Capabilities Cognitive_Module SHALL group capabilities into three categories: Skills, MCPs, and Tools.
3. THE Active Agents & Capabilities Cognitive_Module SHALL display only entities that have been activated during the current thread's execution.
4. WHEN no subagents or specialized capabilities are active, THE Active Agents & Capabilities Cognitive_Module SHALL display "Using core SwarmAgent only".
5. THE Active Agents & Capabilities Cognitive_Module SHALL update in real-time as new agents or capabilities are activated during streaming, using data from Telemetry_Events.

### Requirement 5: Expanded View — What AI is Doing Module

**User Story:** As a knowledge worker, I want to see what the AI is currently doing described in human language, so that I understand its activity without reading technical telemetry.

#### Acceptance Criteria

1. THE What AI is Doing Cognitive_Module SHALL display 2 to 4 bullet points describing current agent activity in human-readable language.
2. THE What AI is Doing Cognitive_Module SHALL avoid technical stage names, internal pipeline jargon, and raw error codes.
3. WHEN the agent is idle and waiting for user input, THE What AI is Doing Cognitive_Module SHALL display "Waiting for your input".
4. THE What AI is Doing Cognitive_Module SHALL update in real-time during agent execution using data from Telemetry_Events.
5. WHEN an error occurs during execution, THE What AI is Doing Cognitive_Module SHALL describe the issue in human-readable language (e.g., "I couldn't retrieve external data due to a connection issue") rather than exposing raw error details.

### Requirement 6: Expanded View — Active Sources Module

**User Story:** As a knowledge worker, I want to see which sources the AI is referencing in this thread, so that I can trust and verify the grounding of its reasoning.

#### Acceptance Criteria

1. THE Active Sources Cognitive_Module SHALL display a list of source files and materials referenced during the current thread's execution.
2. THE Active Sources Cognitive_Module SHALL display an origin tag for each source indicating its provenance (Project, Knowledge Base, Notes, Memory, or External MCP).
3. THE Active Sources Cognitive_Module SHALL display only sources relevant to the current thread.
4. WHEN no external sources have been referenced, THE Active Sources Cognitive_Module SHALL display "Using conversation context only".
5. THE Active Sources Cognitive_Module SHALL display workspace-relative paths for filesystem sources, never absolute paths.

### Requirement 7: Expanded View — Key Summary Module

**User Story:** As a knowledge worker, I want to see a working conclusion for the current thread, so that I can maintain continuity across turns without re-reading the full conversation.

#### Acceptance Criteria

1. THE Key Summary Cognitive_Module SHALL display 3 to 5 bullet points representing the current working conclusion of the thread.
2. THE Key Summary Cognitive_Module SHALL represent an evolving working conclusion, not a final report.
3. WHEN the agent has not yet produced enough context for a summary, THE Key Summary Cognitive_Module SHALL display "No summary yet — ask me to summarize this thread".
4. THE Key Summary Cognitive_Module SHALL update after significant agent turns (e.g., plan completion, decision recording, multi-step phase completion).

### Requirement 8: Scope Model — Workspace Root vs Project Scope

**User Story:** As a knowledge worker, I want TSCC to correctly reflect whether my thread is operating at workspace root or within a project, so that scope is always clear and transitions are seamless.

#### Acceptance Criteria

1. WHEN a thread has no associated project (`project_id` is NULL), THE TSCC_Panel SHALL display Scope_Type as `workspace` with the label "Workspace: SwarmWS (General)".
2. WHEN a thread is associated with a project, THE TSCC_Panel SHALL display Scope_Type as `project` with the project's display name.
3. WHEN a project is assigned to a thread mid-session (via the thread binding API from Cadence 4, Requirement 35), THE TSCC_Panel SHALL update the scope display seamlessly without a disruptive modal or page reload.
4. THE TSCC_Panel SHALL never display negative or empty scope indicators.

### Requirement 9: Thread Lifecycle State Handling

**User Story:** As a knowledge worker, I want TSCC to gracefully reflect the current state of my thread, so that I understand whether the AI is working, waiting, paused, or has encountered an issue.

#### Acceptance Criteria

1. WHEN a new chat session is created, THE TSCC_Panel SHALL display Thread_Lifecycle_State `new` with the Collapsed_Bar showing scope, "New thread", and "Ready".
2. WHEN the agent is actively executing, THE TSCC_Panel SHALL display Thread_Lifecycle_State `active` with a Freshness_Indicator of "Updated just now".
3. WHEN the agent is waiting for user input, THE TSCC_Panel SHALL display Thread_Lifecycle_State `paused` with the Collapsed_Bar showing "Paused · Waiting for your input".
4. WHEN agent execution encounters an error, THE TSCC_Panel SHALL display Thread_Lifecycle_State `failed` with a human-readable explanation and suggested recovery options (retry, continue, adjust plan) in the Expanded_View.
5. WHEN the user cancels execution, THE TSCC_Panel SHALL display Thread_Lifecycle_State `cancelled` with the Collapsed_Bar showing "Execution stopped · Partial progress saved" and a summary of completed work in the Expanded_View.
6. WHEN a previously cancelled thread resumes execution, THE TSCC_Panel SHALL transition directly to Thread_Lifecycle_State `active` and display a transient "Resumed · Continuing previous analysis" indicator in the Collapsed_Bar for 5 seconds before reverting to the normal `active` display.
7. WHEN the thread is idle after completion, THE TSCC_Panel SHALL display Thread_Lifecycle_State `idle` with the Collapsed_Bar showing "Idle · Ready for next task" and the final working summary in the Expanded_View.

### Requirement 10: TSCC Snapshot Creation and Storage

**User Story:** As a knowledge worker, I want periodic snapshots of the cognitive context archived with my thread, so that I can review the AI's state at key decision points in the thread history.

#### Acceptance Criteria

1. THE System SHALL capture a TSCC_Snapshot when any of the following Snapshot_Triggers occur: plan decomposition completed, decision recorded, user requests a recap, or a multi-step execution phase completed.
2. THE System SHALL store each TSCC_Snapshot as a JSON file in the `chats/{thread_id}/snapshots/` directory within the thread's storage location.
3. EACH TSCC_Snapshot SHALL contain: timestamp, active_agents list, active_capabilities (skills, mcps, tools), what_ai_doing list, active_sources list with origin tags, key_summary list, and the trigger reason.
4. THE System SHALL generate snapshot filenames using the pattern `snapshot_{timestamp_iso}.json` to ensure chronological ordering and uniqueness.
5. THE System SHALL not create duplicate snapshots for the same trigger event within a 30-second window.

### Requirement 11: TSCC Snapshot Viewing in Thread History

**User Story:** As a knowledge worker, I want to view archived cognitive context snapshots within the thread history, so that I can understand the AI's state at past decision points.

#### Acceptance Criteria

1. THE Frontend SHALL display TSCC_Snapshots inline within the thread's message history at the chronological position where the snapshot was captured.
2. THE Frontend SHALL render each TSCC_Snapshot as a collapsible card, collapsed by default, showing the timestamp and trigger reason.
3. WHEN a user expands a TSCC_Snapshot card, THE Frontend SHALL display the snapshot's agents, capabilities, sources, activity description, and key summary.
4. THE Backend SHALL provide a `GET /api/chat_threads/{thread_id}/snapshots` endpoint that returns all snapshots for a thread in chronological order.
5. THE Backend SHALL return snapshot responses using snake_case field names.

### Requirement 12: Thread Switching Behavior

**User Story:** As a knowledge worker, I want TSCC to instantly switch state when I switch threads, so that I always see context for the active thread with no cross-thread leakage.

#### Acceptance Criteria

1. WHEN the user switches to a different thread via the session tab bar, THE TSCC_Panel SHALL instantly replace its displayed state with the target thread's TSCC_State.
2. THE TSCC_Panel SHALL preserve the user's expand/collapse preference per thread, restoring it when switching back.
3. THE TSCC_Panel SHALL display only agents, capabilities, sources, and summary data belonging to the active thread. No data from previously viewed threads SHALL appear.
4. WHEN switching to a thread that has no prior TSCC_State (e.g., a restored historical thread), THE TSCC_Panel SHALL display the `new` or `idle` Thread_Lifecycle_State as appropriate.

### Requirement 13: SSE Telemetry Events for Agent Activity

**User Story:** As a system, I want the backend to emit new SSE telemetry events during agent execution, so that the TSCC_Panel receives real-time data about active agents, tool invocations, and capability activation.

#### Acceptance Criteria

1. THE Backend SHALL emit an `agent_activity` Telemetry_Event via SSE when an agent begins or completes a reasoning step, containing the agent name and a human-readable activity description.
2. THE Backend SHALL emit a `tool_invocation` Telemetry_Event via SSE when a tool is invoked during agent execution, containing the tool name and a human-readable description of the invocation purpose.
3. THE Backend SHALL emit a `capability_activated` Telemetry_Event via SSE when a skill or MCP connector is activated during agent execution, containing the capability type (skill, mcp, or tool), the capability name, and a human-readable label.
4. THE Backend SHALL emit a `sources_updated` Telemetry_Event via SSE when the agent references a new source file or material, containing the source path (workspace-relative) and origin tag.
5. THE Backend SHALL emit a `summary_updated` Telemetry_Event via SSE when the agent's working conclusion changes, containing the updated key summary bullet points.
6. ALL Telemetry_Events SHALL include a `thread_id` field to ensure the frontend can route events to the correct TSCC_Panel instance.
7. THE Backend SHALL use snake_case field names in all Telemetry_Event payloads.

### Requirement 14: Frontend StreamEvent Integration

**User Story:** As a frontend developer, I want the TSCC_Panel to consume new SSE telemetry events through the existing streaming infrastructure, so that TSCC updates in real-time during agent execution.

#### Acceptance Criteria

1. THE Frontend SHALL extend the existing `StreamEvent` interface in `desktop/src/types/index.ts` to include the new Telemetry_Event types: `agent_activity`, `tool_invocation`, `capability_activated`, `sources_updated`, and `summary_updated`.
2. THE Frontend SHALL route incoming Telemetry_Events to the TSCC_Panel's state management based on the `thread_id` field.
3. THE Frontend SHALL update the TSCC_State incrementally as Telemetry_Events arrive, without replacing the entire state on each event.
4. THE Frontend SHALL ignore Telemetry_Events whose `thread_id` does not match the currently active thread.

### Requirement 15: TSCC Backend State API

**User Story:** As a frontend developer, I want backend API endpoints for TSCC state retrieval and snapshot management, so that the TSCC_Panel can load initial state and manage snapshots.

#### Acceptance Criteria

1. THE Backend SHALL provide a `GET /api/chat_threads/{thread_id}/tscc` endpoint that returns the current TSCC_State for a thread.
2. THE Backend SHALL provide a `POST /api/chat_threads/{thread_id}/snapshots` endpoint that creates a TSCC_Snapshot with a specified trigger reason.
3. THE Backend SHALL provide a `GET /api/chat_threads/{thread_id}/snapshots/{snapshot_id}` endpoint that returns a single snapshot by its identifier.
4. THE Backend SHALL return 404 when a thread or snapshot does not exist.
5. THE Backend SHALL return all API responses using snake_case field names.
6. THE Frontend SHALL convert snake_case response fields to camelCase using `toCamelCase()` functions in the TSCC service layer.

### Requirement 16: TSCC Interaction Rules

**User Story:** As a knowledge worker, I want TSCC to update silently during normal chat flow and only auto-expand for high-signal events, so that it provides context without being disruptive.

#### Acceptance Criteria

1. THE TSCC_Panel SHALL not auto-expand during normal chat message streaming.
2. THE TSCC_Panel SHALL auto-expand only for the following high-signal events: first plan creation in the thread, a blocking issue requiring user input, or an explicit user request to show context, sources, or agents (e.g., via slash command or message).
3. WHEN TSCC_State changes during normal operation, THE TSCC_Panel SHALL update the Collapsed_Bar content and Freshness_Indicator silently without visual disruption.
4. THE TSCC_Panel SHALL not cause layout shifts, scroll jumps, or input focus loss when updating.

### Requirement 17: TSCC and ContextPreviewPanel Coexistence

**User Story:** As a knowledge worker, I want TSCC and the ContextPreviewPanel to serve distinct purposes without confusion, so that I can use the right tool for the right need.

#### Acceptance Criteria

1. THE TSCC_Panel SHALL operate independently from the ContextPreviewPanel (Cadence 4, Requirement 33).
2. THE TSCC_Panel SHALL display human-readable cognitive state (agents, activity, sources, summary) while the ContextPreviewPanel SHALL continue to display raw 8-layer context assembly with token counts.
3. THE TSCC_Panel SHALL be accessible from every chat thread (above chat input), while the ContextPreviewPanel SHALL remain accessible from the project detail view.
4. THE TSCC_Panel and ContextPreviewPanel SHALL not share UI state — expanding or collapsing one SHALL not affect the other.

### Requirement 18: TSCC Data Model

**User Story:** As a developer, I want a well-defined data model for TSCC state and snapshots, so that the frontend and backend have a shared contract for cognitive context data.

#### Acceptance Criteria

1. THE TSCC_State data model SHALL contain the following fields: `thread_id` (string), `project_id` (nullable string), `scope_type` (enum: "workspace" or "project"), `last_updated_at` (ISO 8601 timestamp), and a `live_state` object.
2. THE `live_state` object SHALL contain: `context` (object with `scope_label`, `thread_title`, and optional `mode` fields), `active_agents` (string array), `active_capabilities` (object with `skills`, `mcps`, and `tools` string arrays), `what_ai_doing` (string array, max 4 items), `active_sources` (array of objects with `path` and `origin` fields), and `key_summary` (string array, max 5 items).
3. THE TSCC_Snapshot data model SHALL contain: `snapshot_id` (string), `thread_id` (string), `timestamp` (ISO 8601), `reason` (string describing the Snapshot_Trigger), and the same `active_agents`, `active_capabilities`, `what_ai_doing`, `active_sources`, and `key_summary` fields as the `live_state`.
4. THE Backend SHALL define Pydantic models for TSCC_State and TSCC_Snapshot using snake_case field names.
5. THE Frontend SHALL define TypeScript interfaces for TSCC_State and TSCC_Snapshot using camelCase field names.

### Requirement 19: TSCC Frontend State Management

**User Story:** As a frontend developer, I want a dedicated React hook for TSCC state management, so that the TSCC_Panel has clean, testable state logic separated from the ChatPage orchestrator.

#### Acceptance Criteria

1. THE Frontend SHALL implement a `useTSCCState` hook that manages the TSCC_State for the active thread.
2. THE `useTSCCState` hook SHALL accept a `threadId` parameter and fetch the initial TSCC_State from the backend API on mount or when `threadId` changes.
3. THE `useTSCCState` hook SHALL provide an `applyTelemetryEvent` function that incrementally updates TSCC_State based on incoming Telemetry_Events.
4. THE `useTSCCState` hook SHALL maintain per-thread expand/collapse preference in memory (not persisted to localStorage).
5. THE `useTSCCState` hook SHALL reset state cleanly when `threadId` changes, preventing cross-thread data leakage.

### Requirement 20: TSCC Accessibility

**User Story:** As a knowledge worker using assistive technology, I want the TSCC panel to be keyboard navigable and screen reader compatible, so that I can access cognitive context regardless of how I interact with the application.

#### Acceptance Criteria

1. THE TSCC_Panel SHALL be keyboard navigable: the Collapsed_Bar SHALL be focusable and expandable via Enter or Space key.
2. THE TSCC_Panel SHALL use appropriate ARIA attributes: `role="region"`, `aria-label="Thread cognitive context"`, and `aria-expanded` reflecting the current expand/collapse state.
3. WHEN TSCC_State updates silently, THE TSCC_Panel SHALL use an `aria-live="polite"` region to announce significant state changes (lifecycle transitions, error states) to screen readers.
4. THE Expanded_View Cognitive_Modules SHALL use semantic heading hierarchy for module titles.
