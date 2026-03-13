# Requirements Document

## Introduction

Redesign the SwarmAI right sidebar from a multi-panel toggle system (TodoRadar / ChatHistory / FileBrowser) into a persistent HUD-style "Radar" sidebar with two modes: Radar (default) and History. The sidebar is always visible — no toggle icons in ChatHeader. Radar mode shows four stacked collapsible sections (ToDo, Artifacts, Sessions, Jobs). History mode shows a searchable, time-grouped session list. Items in Radar sections support drag-to-chat, which populates ChatInput without auto-sending. The Artifacts section displays recently modified files from the workspace git tree as a read-only view — no new database tables or SSE hooks needed.

## Glossary

- **Radar_Sidebar**: The persistent right sidebar component (`RadarSidebar.tsx`) that renders either Radar mode or History mode.
- **Radar_Mode**: The default sidebar view displaying four stacked collapsible sections: ToDo, Artifacts, Sessions, Jobs.
- **History_Mode**: The alternate sidebar view displaying a searchable, time-grouped list of all chat sessions.
- **Collapsible_Section**: A reusable UI component (`CollapsibleSection.tsx`) that wraps each Radar section with expand/collapse behavior, count badge, and one-line status hint.
- **ToDo_Section**: The Radar section displaying active ToDo items as a read-only list with drag handles.
- **Artifacts_Section**: The Radar section displaying recently modified files from the workspace git tree, derived from `git log`.
- **Sessions_Section**: The Radar section displaying currently open chat tabs and their live status derived from `tabMapRef`.
- **Jobs_Section**: The Radar section displaying background autonomous job status.
- **Artifact**: A recently modified file in the workspace git tree, displayed as a read-only entry in the Artifacts_Section. Derived from `git log` — no separate database storage.
- **Artifacts_API**: The FastAPI endpoint that reads recent file changes from the workspace git tree and returns them to the frontend.
- **Drop_Payload**: A typed union describing the data transferred when a Radar item is dragged onto ChatInput: `file`, `radar-todo`, or `radar-artifact`.
- **Chat_Input**: The chat message input component (`ChatInput.tsx`) that receives drop payloads and populates its value with context text.
- **Drag_Handle**: A grip UI element (`DragHandle.tsx`) attached to draggable Radar items.
- **Mode_Toggle**: A small icon button in the Radar_Sidebar header that switches between Radar_Mode and History_Mode.
- **Collapse_Persistence**: The mechanism that saves each Collapsible_Section's expand/collapse state to `localStorage`, keyed by section name.
- **Chat_Header**: The top bar component (`ChatHeader.tsx`) containing session tabs and action buttons.
- **Tab_Map_Ref**: The authoritative React ref (`tabMapRef`) holding per-tab state; the single source of truth for tab status (not React `useState`).
- **Input_Value_Map_Ref**: The per-tab draft text storage ref (`inputValueMapRef`) that holds each tab's ChatInput draft value. Drop operations write to the entry keyed by the currently active tab ID.
- **Tab_Scoped_Drop**: The invariant that each drag-drop action targets the tab active at drop time, using synchronous reads from Tab_Map_Ref to prevent cross-tab contamination.
- **Feature_Tip**: A single consolidated tooltip or popover in the Radar_Sidebar header (💡 icon) that provides a brief overview of all Radar features for new users. Dismissal state is persisted to `localStorage`.
- **Display_Limit**: The maximum number of items shown by default in a section (5 for ToDo), with a "See more" expansion mechanism that resets on mount.

## Requirements

### Requirement 1: Persistent Sidebar Shell

**User Story:** As a user, I want the right sidebar to always be visible so that I have constant awareness of my work context without toggling panels.

#### Acceptance Criteria

1. THE Radar_Sidebar SHALL render as a persistent right-side panel that is always visible when the chat page is mounted.
2. THE Radar_Sidebar SHALL display a header containing the mode label and the Mode_Toggle icon.
3. WHEN the user clicks the Mode_Toggle, THE Radar_Sidebar SHALL switch between Radar_Mode and History_Mode.
4. THE Radar_Sidebar SHALL default to Radar_Mode on initial render. THE Mode_Toggle state (Radar/History) SHALL NOT be persisted to `localStorage` — the sidebar always starts in Radar_Mode on mount. History_Mode is a transient browse action, not a user preference.
5. WHEN the Radar_Sidebar is in History_Mode and the user clicks the Mode_Toggle or a back arrow, THE Radar_Sidebar SHALL return to Radar_Mode.
6. THE Radar_Sidebar SHALL support horizontal resizing via a left-edge drag handle.
7. THE Radar_Sidebar SHALL persist its width to `localStorage` and restore the saved width on mount.

### Requirement 2: Legacy Panel Removal and Cleanup

**User Story:** As a developer, I want the old multi-panel toggle system completely removed — code, tests, styles, and persisted data — so that the codebase has a single, clean sidebar implementation with no dead code.

#### Acceptance Criteria

1. THE Chat_Header SHALL remove the three right-sidebar toggle icon buttons (checklist, history, folder) and the `activeSidebar` / `onOpenSidebar` props.
2. THE System SHALL delete the `useRightSidebarGroup` hook (`useRightSidebarGroup.ts`), its unit test file (`useRightSidebarGroup.test.ts`), and its property test file (`useRightSidebarGroup.property.test.ts`).
3. THE System SHALL delete the `RIGHT_SIDEBAR_IDS` constant, the `RightSidebarId` type, the `RIGHT_SIDEBAR_WIDTH_CONFIGS` constant, the `SidebarWidthConfig` interface, and the `DEFAULT_ACTIVE_SIDEBAR` constant from `constants.ts`.
4. THE System SHALL delete the `FileBrowserSidebar` component and all its associated files (component, styles, tests).
5. THE System SHALL delete the `QuickAddTodo` component and all its associated files.
6. THE System SHALL remove ToDo action buttons (start, edit, complete, cancel, delete) from the ToDo_Section UI.
7. THE System SHALL remove the `WaitingInputList` standalone component and fold its display logic into Sessions_Section.
8. THE System SHALL remove the `ChatHistorySidebar` component and all its associated files (component, styles, tests). Its session-fetching and time-grouping logic moves into History_Mode inside Radar_Sidebar.
9. THE System SHALL delete the `SwarmRadar` component (`SwarmRadar.tsx`, `SwarmRadar.css`) and all its associated files from the `radar/` directory. The new `RadarSidebar` replaces it entirely.
10. THE System SHALL remove all legacy localStorage keys on first mount of the new RadarSidebar: `todoRadarSidebarWidth`, `chatSidebarWidth`, `rightSidebarWidth`, `chatSidebarCollapsed`, `rightSidebarCollapsed`, `todoRadarSidebarCollapsed`.
11. THE System SHALL remove the `SwarmRadar` import and its conditional rendering block from `ChatPage.tsx`, replacing it with the single `RadarSidebar` component.
12. THE System SHALL remove the `useRightSidebarGroup` import and its `rightSidebars` usage from `ChatPage.tsx`.
13. THE System SHALL delete any test files that exclusively test removed components. Test files for shared utilities (e.g., time-grouping) that are reused by the new sidebar SHALL be preserved.

### Requirement 3: Collapsible Section Behavior

**User Story:** As a user, I want each Radar section to be collapsible so that I can focus on the sections most relevant to me.

#### Acceptance Criteria

1. THE Collapsible_Section SHALL display a header row containing the section icon, section name, item count badge, and a one-line status hint.
2. WHEN the user clicks a Collapsible_Section header, THE Collapsible_Section SHALL toggle between expanded and collapsed states.
3. WHILE a Collapsible_Section is collapsed, THE Collapsible_Section SHALL display the item count and one-line status hint in the header row.
4. THE Collapsible_Section SHALL persist its expand/collapse state to `localStorage` keyed by section name.
5. WHEN the Radar_Sidebar mounts, THE Collapsible_Section SHALL restore its expand/collapse state from `localStorage`.
6. IF no saved state exists in `localStorage` for a Collapsible_Section, THEN THE ToDo_Section SHALL default to expanded and all other sections SHALL default to collapsed.

### Requirement 4: ToDo Section (Read-Only)

**User Story:** As a user, I want to see my active ToDo items in the sidebar so that I maintain awareness of pending work.

#### Acceptance Criteria

1. THE ToDo_Section SHALL display active ToDo items (status `pending` or `overdue`) as a read-only list.
2. THE ToDo_Section SHALL display each ToDo item with its title, priority indicator, and a Drag_Handle.
3. THE ToDo_Section SHALL fetch ToDo data from the existing `radarService.fetchActiveTodos` API.
4. WHEN no active ToDo items exist, THE ToDo_Section SHALL display an empty-state message.
5. THE ToDo_Section SHALL display the count of active ToDo items in the Collapsible_Section header badge.

### Requirement 5: Sessions Section

**User Story:** As a user, I want to see my open chat tabs and their live status in the sidebar so that I can monitor active work.

#### Acceptance Criteria

1. THE Sessions_Section SHALL display the list of currently open chat tabs.
2. THE Sessions_Section SHALL derive tab status from Tab_Map_Ref (the authoritative source), not from React `useState`.
3. THE Sessions_Section SHALL display each session with its title, agent name, and current status (idle, streaming, waiting for input, error).
4. WHEN a session has a pending question or permission request, THE Sessions_Section SHALL display a visual indicator on that session entry.
5. WHEN the user clicks a session entry, THE Sessions_Section SHALL switch the active tab to that session.
6. THE Sessions_Section SHALL display a "Chat History" link in its header area.
7. WHEN the user clicks the "Chat History" link, THE Radar_Sidebar SHALL switch to History_Mode.
8. THE Sessions_Section SHALL display the count of open tabs in the Collapsible_Section header badge.

### Requirement 6: Artifacts Read-Only View

**User Story:** As a user, I want to see recently changed files in my workspace so that I can quickly review and reference what the agent has produced.

#### Acceptance Criteria

1. THE Artifacts_API SHALL expose a GET endpoint at `/api/artifacts/recent` that returns recently modified files in the workspace git tree, sorted by modification time (newest first).
2. THE Artifacts_API SHALL derive the file list from `git log --diff-filter=ACMR --name-only` (or equivalent) scoped to the active workspace path, returning files that were Added, Copied, Modified, or Renamed.
3. THE Artifacts_API SHALL accept an optional query parameter: `limit` (default 20, max 50) to control the number of returned artifacts.
4. THE Artifacts_API SHALL return each artifact with: `path` (relative to workspace root), `title` (filename), `type` (derived from file extension: code, document, config, image, other), `modifiedAt` (ISO timestamp from git log).
5. THE Artifacts_API SHALL return artifact records with snake_case field names, and the frontend service layer SHALL convert them to camelCase using an `artifactToCamelCase` function in `radar.ts`.
6. THE System SHALL NOT create any new database tables, INSERT hooks, SSE event emitters, or pruning logic for artifacts. Artifacts are read-only views of the existing git-tracked workspace files.

### Requirement 7: Artifacts Section (Frontend)

**User Story:** As a user, I want to see a chronological feed of recently changed files in the sidebar so that I can quickly review and access agent output.

#### Acceptance Criteria

1. THE Artifacts_Section SHALL display artifacts in reverse chronological order (most recently modified first).
2. THE Artifacts_Section SHALL display each artifact with its title (filename), type icon (based on file extension), relative timestamp, and a Drag_Handle.
3. WHEN the user clicks an artifact entry, THE Artifacts_Section SHALL open a preview of the file (using the existing file preview mechanism).
4. THE Artifacts_Section SHALL fetch data from `radarService.fetchRecentArtifacts` on mount and when the sidebar becomes visible.
5. WHEN no artifacts exist, THE Artifacts_Section SHALL display an empty-state message.
6. THE Artifacts_Section SHALL display the count of artifacts in the Collapsible_Section header badge.

### Requirement 8: Drag-to-Chat

**User Story:** As a user, I want to drag items from the Radar sidebar into the chat input so that I can quickly reference ToDos and artifacts in my messages.

#### Acceptance Criteria

1. THE Drag_Handle SHALL be rendered on each draggable item in ToDo_Section and Artifacts_Section.
2. THE Drag_Handle SHALL render as a vertical grip icon (⋮⋮) visible on hover of the parent item row only (hidden by default to keep the list clean).
3. THE Drag_Handle SHALL include `aria-label="Drag to chat"` and `role="button"` for accessibility.
4. THE Drag_Handle SHALL set `draggable="true"` and use the HTML5 drag-and-drop API (`dataTransfer`) to transfer Drop_Payload data.
5. WHILE dragging, THE dragged item SHALL display a semi-transparent ghost preview.
6. WHEN the user drags a ToDo item, THE Drag_Handle SHALL set the Drop_Payload to type `radar-todo` with the ToDo `id`, `title`, and optional `context`.
7. WHEN the user drags an artifact item, THE Drag_Handle SHALL set the Drop_Payload to type `radar-artifact` with the artifact `path` and `title`.
8. WHEN a Drop_Payload of type `radar-todo` is dropped on Chat_Input, THE Chat_Input SHALL populate its value with the ToDo title and context, then focus the input cursor.
9. WHEN a Drop_Payload of type `radar-artifact` is dropped on Chat_Input, THE Chat_Input SHALL populate its value with the artifact title and path reference, then focus the input cursor.
10. THE Chat_Input SHALL accept Drop_Payload items without auto-sending the message — the user decides when to send.
11. THE existing file-drop behavior in ChatDropZone SHALL continue to function for Drop_Payload items of type `file`.
12. THE Chat_Input `handleDragOver` SHALL be extended to accept `application/json` dataTransfer type (in addition to `Files`) so the drop zone activates for radar drag items.
13. THE Chat_Input component SHALL receive new props: `activeTabIdRef`, `inputValueMapRef`, and `onInputValueChange` to support tab-scoped radar drops.

### Requirement 9: History Mode

**User Story:** As a user, I want to browse and search my full chat history from within the sidebar so that I can find and resume past conversations.

#### Acceptance Criteria

1. WHEN the Radar_Sidebar is in History_Mode, THE Radar_Sidebar SHALL display a search input field at the top.
2. THE History_Mode SHALL display chat sessions grouped by time period: Today, Yesterday, This Week, This Month, Older.
3. WHEN the user types in the search input, THE History_Mode SHALL filter the session list to show sessions whose title contains the search text (case-insensitive).
4. THE History_Mode SHALL display each session with its title, agent name, and relative timestamp.
5. WHEN the user clicks a session in History_Mode, THE Radar_Sidebar SHALL switch to Radar_Mode and activate the selected session tab.
6. WHEN the user clicks the back arrow or Mode_Toggle in History_Mode, THE Radar_Sidebar SHALL return to Radar_Mode.
7. THE History_Mode SHALL reuse the existing session-fetching and time-grouping logic from the current `ChatHistorySidebar` component.

### Requirement 10: Jobs Section

**User Story:** As a user, I want to see the status of background autonomous jobs in the sidebar so that I know what the system is doing on my behalf.

#### Acceptance Criteria

1. THE Jobs_Section SHALL display autonomous jobs fetched from the existing `radarService.fetchAutonomousJobs` API.
2. THE Jobs_Section SHALL display each job with its name, status indicator (running, paused, error, completed), and category (system, user-defined).
3. WHEN no autonomous jobs exist, THE Jobs_Section SHALL display an empty-state message.
4. THE Jobs_Section SHALL display the count of active (non-completed) jobs in the Collapsible_Section header badge.

### Requirement 11: Collapse Persistence

**User Story:** As a user, I want my section expand/collapse preferences remembered across sessions so that the sidebar layout stays how I left it.

#### Acceptance Criteria

1. WHEN the user expands or collapses a Collapsible_Section, THE Collapse_Persistence mechanism SHALL write the new state to `localStorage` using the key format `radar-section-{sectionName}`.
2. WHEN the Radar_Sidebar mounts, THE Collapse_Persistence mechanism SHALL read saved states from `localStorage` and apply them to each Collapsible_Section.
3. IF a `localStorage` key for a section is missing or corrupt, THEN THE Collapse_Persistence mechanism SHALL apply the default state (expanded for ToDo, collapsed for others).
4. FOR ALL valid collapse state values, reading then writing then reading the state SHALL produce the same value (round-trip property).

### Requirement 12: Component Tree Structure

**User Story:** As a developer, I want a clean component hierarchy so that the sidebar is maintainable and each section is independently testable.

#### Acceptance Criteria

1. THE System SHALL organize sidebar components under a `RightSidebar/` directory containing: `RadarSidebar.tsx`, `RadarView.tsx`, `HistoryView.tsx`, and a `shared/` subdirectory.
2. THE `RadarView.tsx` component SHALL compose `TodoSection.tsx`, `ArtifactsSection.tsx`, `SessionsSection.tsx`, and `JobsSection.tsx`.
3. THE `shared/` directory SHALL contain `CollapsibleSection.tsx` and `DragHandle.tsx`.
4. THE Radar_Sidebar SHALL render either `RadarView` or `HistoryView` based on the current mode state.

### Requirement 13: Tab-Scoped Drag-Drop Isolation

**User Story:** As a user, I want drag-drop actions to always target the currently active tab so that switching tabs between drops does not cause content to appear in the wrong chat.

#### Acceptance Criteria

1. WHEN a Drop_Payload is dropped on Chat_Input, THE drop handler SHALL read the active tab ID synchronously from Tab_Map_Ref at drop time (not from a stale React state closure).
2. THE drop handler SHALL write the populated text to the per-tab draft storage (Input_Value_Map_Ref) keyed by the active tab ID read at drop time.
3. THE drop handler SHALL only update the visible Chat_Input value if the tab ID read at drop time matches the currently rendered tab.
4. IF the user switches tabs between drag-start and drop, THEN THE drop handler SHALL use the tab that is active at drop time, not at drag-start time.
5. THE drop handler SHALL NOT use any async operations (no `await`, no `setTimeout`, no `setState` callback) between reading the active tab ID and writing the draft — the read-and-write SHALL be a single synchronous operation.
6. FOR ANY sequence of N drops interleaved with tab switches, each drop's content SHALL appear only in the tab that was active at the moment of that specific drop (isolation property).

### Requirement 14: ToDo List Display Limits

**User Story:** As a user, I want the ToDo section to show a manageable number of items by default so that the sidebar stays compact and scannable.

#### Acceptance Criteria

1. THE ToDo_Section SHALL display a maximum of 5 ToDo items by default.
2. WHEN more than 5 active ToDo items exist, THE ToDo_Section SHALL display a "See more ({remaining} more)" link below the visible items.
3. WHEN the user clicks "See more", THE ToDo_Section SHALL expand to show all active ToDo items.
4. WHEN the ToDo_Section is expanded beyond the default Display_Limit, THE ToDo_Section SHALL display a "Show less" link to collapse back to the top 5.
5. THE ToDo_Section SHALL sort items by priority (highest first), then by creation date (newest first), so the top 5 are always the most important.
6. THE "See more" / "Show less" state SHALL NOT be persisted to `localStorage` — the state resets to collapsed (top 5) on each mount.

### Requirement 15: Consolidated Feature Tip

**User Story:** As a new user, I want a single onboarding tip in the Radar sidebar header so that I understand how to use SwarmRadar features without reading documentation.

#### Acceptance Criteria

1. THE Radar_Sidebar header SHALL display a single 💡 icon next to the mode label.
2. WHEN the user hovers over or clicks the 💡 icon, THE Radar_Sidebar SHALL display a popover with a consolidated overview of all Radar features.
3. THE Feature_Tip popover content SHALL read: "SwarmRadar keeps you in the loop. ToDos show your pending tasks — ask your agent to create them or pull from Slack and email. Artifacts show recently changed files in your workspace — drag any item to chat. Sessions show your open tabs and their live status. Jobs display background automations. Drag items from ToDo or Artifacts into chat to reference them."
4. THE Feature_Tip popover SHALL include a "Don't show again" option that persists the dismissal to `localStorage` using the key `radar-tip-dismissed`.
5. WHEN the Feature_Tip has been dismissed via "Don't show again", THE 💡 icon SHALL still be visible but the popover SHALL NOT auto-show — the popover only appears on explicit click.
