# Requirements Document

## Introduction

This feature optimizes the chat window experience in the SwarmAI desktop app by reclaiming vertical space currently consumed by TSCC (Thread-Scoped Cognitive Context) UI elements. Two changes are made: (1) TSCC Snapshot Cards are removed entirely from the chat message timeline, and (2) the full-width TSCC Panel between the message area and chat input is replaced with a compact icon button in the ChatInput bottom row that opens a popover showing TSCC details.

## Glossary

- **Chat_Timeline**: The scrollable message area in ChatPage that renders user and assistant messages in chronological order.
- **TSCC_Snapshot_Card**: A collapsible card component (`TSCCSnapshotCard.tsx`) that renders a point-in-time capture of TSCC state inline within the Chat_Timeline.
- **TSCC_Panel**: The current full-width collapsible bar component (`TSCCPanel.tsx`) rendered between the message area and the ChatInput, showing live TSCC cognitive module data.
- **Chat_Input**: The input component (`ChatInput.tsx`) containing the text area, send button, file attachment button, and command hint row.
- **Bottom_Row**: The row at the bottom of Chat_Input containing the file attachment button and the "Type / for commands" hint.
- **TSCC_Icon_Button**: A new compact icon button to be placed in the Bottom_Row that provides access to TSCC state information.
- **TSCC_Popover**: A popover or overlay that opens when the TSCC_Icon_Button is clicked, displaying the five TSCC cognitive modules (Current Context, Active Agents, What AI is Doing, Active Sources, Key Summary).
- **Cognitive_Modules**: The five information sections displayed in the TSCC_Popover: Current Context, Active Agents, What AI is Doing, Active Sources, and Key Summary.
- **ChatPage**: The main chat page component (`ChatPage.tsx`) that orchestrates the layout of ChatHeader, message area, TSCC_Panel, and Chat_Input.

## Requirements

### Requirement 1: Remove TSCC Snapshot Cards from Chat Timeline

**User Story:** As a user, I want the chat timeline to show only messages without TSCC snapshot cards, so that I have more vertical space for reading conversation content.

#### Acceptance Criteria

1. THE ChatPage SHALL render only message items in the Chat_Timeline, excluding all TSCC_Snapshot_Card elements.
2. WHEN the ChatPage builds the timeline array, THE ChatPage SHALL include only message items and exclude snapshot items from the rendered output.
3. THE ChatPage SHALL retain the ability to fetch TSCC snapshot data from the backend without rendering snapshot cards in the Chat_Timeline.
4. WHEN a user scrolls through the Chat_Timeline, THE Chat_Timeline SHALL display a continuous sequence of MessageBubble components with no TSCC_Snapshot_Card components interspersed.

### Requirement 2: Remove TSCC Panel from Between Messages and Input

**User Story:** As a user, I want the space between the message area and chat input to be free of the TSCC panel bar, so that the chat layout is more compact.

#### Acceptance Criteria

1. THE ChatPage SHALL stop rendering the TSCC_Panel component between the message area and the Chat_Input.
2. WHEN the ChatPage layout renders, THE ChatPage SHALL place the Chat_Input directly after the message area with no TSCC_Panel in between.


### Requirement 3: Add TSCC Icon Button to ChatInput Bottom Row

**User Story:** As a user, I want a small TSCC icon button in the chat input's bottom row, so that I can access TSCC state information without it consuming permanent vertical space.

#### Acceptance Criteria

1. THE Chat_Input SHALL render a TSCC_Icon_Button in the Bottom_Row, positioned after the "Type / for commands" hint text.
2. THE TSCC_Icon_Button SHALL display a recognizable icon (such as a brain or psychology Material Symbol) indicating TSCC context availability.
3. THE TSCC_Icon_Button SHALL be keyboard accessible and include an accessible label describing its purpose.
4. THE TSCC_Icon_Button SHALL include `aria-haspopup="true"` and `aria-expanded` attributes reflecting the current popover open/close state, following WAI-ARIA popover button patterns.
5. WHEN no TSCC state data is available (null state), THE TSCC_Icon_Button SHALL appear in a muted or disabled visual state.
6. WHEN TSCC state data is available, THE TSCC_Icon_Button SHALL appear in an active visual state distinguishable from the disabled state.

### Requirement 4: TSCC Popover Display

**User Story:** As a user, I want to click the TSCC icon button to see a popover with TSCC cognitive module details, so that I can inspect the AI's current context on demand.

#### Acceptance Criteria

1. WHEN the user clicks the TSCC_Icon_Button, THE TSCC_Popover SHALL open and display the five Cognitive_Modules: Current Context, Active Agents, What AI is Doing, Active Sources, and Key Summary.
2. WHEN the TSCC_Popover is open and the user clicks the TSCC_Icon_Button again, THE TSCC_Popover SHALL close.
3. WHEN the TSCC_Popover is open and the user clicks outside the TSCC_Popover, THE TSCC_Popover SHALL close.
4. WHEN the user presses the Escape key while the TSCC_Popover is open, THE TSCC_Popover SHALL close.
5. THE TSCC_Popover SHALL open above the Chat_Input area, anchored to the TSCC_Icon_Button position.
6. THE TSCC_Popover SHALL display the same Cognitive_Modules content that the existing TSCC_Panel ExpandedView displays (Current Context, Active Agents, What AI is Doing, Active Sources, Key Summary).
7. THE TSCC_Popover SHALL update its displayed content in real time as TSCC state changes from telemetry events, without requiring the user to close and reopen the popover.
8. WHEN the TSCC_Popover click-outside detection fires, THE detection logic SHALL exclude clicks on the TSCC_Icon_Button itself to prevent the toggle click from being treated as an outside click.
9. WHEN the Chat_Input component unmounts while the TSCC_Popover is open (e.g., during tab switch), THE TSCC_Popover SHALL clean up all document-level event listeners.
10. WHEN the user switches tabs (causing the underlying session/threadId to change), THE TSCC_Popover SHALL automatically close and the TSCC_Icon_Button SHALL reflect the newly active tab's TSCC state — never stale data from a previous tab.
11. WHEN the TSCC state transitions from non-null to null while the TSCC_Popover is open (e.g., session deleted), THE TSCC_Popover SHALL automatically close and the TSCC_Icon_Button SHALL transition to the disabled state.

### Requirement 5: Pass TSCC State to ChatInput

**User Story:** As a developer, I want the TSCC state passed from ChatPage to ChatInput, so that the TSCC icon button and popover can function correctly.

#### Acceptance Criteria

1. THE ChatPage SHALL pass the current TSCC state object to the Chat_Input component as a prop.
2. WHEN the TSCC state updates due to telemetry events, THE Chat_Input SHALL receive the updated state and reflect changes in the TSCC_Popover if it is open.
3. THE TSCC_Popover open/close state SHALL be managed locally within the TSCCPopoverButton component, NOT passed from ChatPage, so that the popover always starts closed on mount and tab switches.

### Requirement 6: Preserve TSCC State Hook Functionality

**User Story:** As a developer, I want the useTSCCState hook to continue functioning correctly after the UI changes, so that TSCC state management remains intact.

#### Acceptance Criteria

1. THE useTSCCState hook SHALL continue to fetch initial TSCC state, apply telemetry events, and manage expand/collapse and pin preferences without modification to its public API.
2. THE ChatPage SHALL continue to call useTSCCState and pass its return values to the appropriate child components.
3. WHEN telemetry events arrive during streaming, THE useTSCCState hook SHALL apply state updates that are reflected in the TSCC_Popover content.

### Requirement 7: Clean Up Unused TSCC Timeline Code

**User Story:** As a developer, I want unused TSCC snapshot timeline code removed from ChatPage, so that the codebase remains clean and maintainable.

#### Acceptance Criteria

1. THE ChatPage SHALL remove the timeline merging logic that combines messages with TSCC snapshots into a single sorted array.
2. THE ChatPage SHALL remove the threadSnapshots state variable and the useEffect that fetches snapshots via listSnapshots.
3. THE ChatPage SHALL remove the import of TSCCSnapshotCard from its import statements.
4. THE ChatPage SHALL remove the import of listSnapshots from the TSCC service.
5. WHEN the cleanup is complete, THE ChatPage SHALL render messages directly from the messages array instead of the timeline array.
