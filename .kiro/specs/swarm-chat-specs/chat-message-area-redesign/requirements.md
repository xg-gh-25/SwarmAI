# Requirements Document

## Introduction

Redesign the chat message area in the SwarmAI desktop app to improve visual clarity, space efficiency, and branding consistency. The redesign covers three areas: simplified user message bubbles, enhanced assistant response messages with collapsible tool calls and left-aligned layout, and a branded welcome screen for new chat tabs. The prototype reference is `assets/swarmai-prototype.html`.

## Glossary

- **Chat_Message_Area**: The scrollable region in the center panel where user and assistant messages are displayed within a chat session.
- **Message_Bubble**: The React component (`MessageBubble.tsx`) responsible for rendering a single chat message (user or assistant).
- **User_Message**: A message with `role: 'user'` in the `Message` type, representing input from the user.
- **Assistant_Message**: A message with `role: 'assistant'` in the `Message` type, representing a response from SwarmAI.
- **Tool_Call_Block**: A content block of type `tool_use` within an assistant message, rendered by `ToolUseBlock.tsx`.
- **Tool_Result_Block**: A content block of type `tool_result` within an assistant message, showing the output of a tool call.
- **Welcome_Screen**: A branded landing view displayed in a new chat tab before the user sends the first message.
- **SwarmAI_Icon**: A branded icon representing the SwarmAI assistant, displayed alongside assistant messages and on the welcome screen.
- **Content_Block_Renderer**: The component (`ContentBlockRenderer.tsx`) that dispatches rendering for different content block types (text, tool_use, tool_result, ask_user_question).
- **Expansion_Toggle**: A clickable UI element that expands or collapses truncated content.

## Requirements

### Requirement 1: Simplified User Message Display

**User Story:** As a user, I want my messages to appear as minimal text bubbles with a light background, so that the chat area feels clean and space-efficient.

#### Acceptance Criteria

1. THE Message_Bubble SHALL render User_Message content with a light background color and no avatar icon.
2. THE Message_Bubble SHALL render User_Message content without a timestamp display.
3. WHEN a User_Message text content exceeds 5 visible lines, THE Message_Bubble SHALL truncate the display to 5 lines and show an Expansion_Toggle.
4. WHEN the user clicks the Expansion_Toggle on a truncated User_Message, THE Message_Bubble SHALL reveal the full message content.
5. WHEN the user clicks the Expansion_Toggle on an expanded User_Message, THE Message_Bubble SHALL collapse the content back to 5 lines.

### Requirement 2: Redesigned Assistant Message Header

**User Story:** As a user, I want assistant responses to show a branded SwarmAI identity with an animated bee icon (🐝), so that the AI feels like a distinct team member.

#### Acceptance Criteria

1. THE Message_Bubble SHALL display the SwarmAI_Icon (🐝), the title "SwarmAI", and the timestamp on a single line for Assistant_Message entries.
2. THE SwarmAI_Icon SHALL replace the current `smart_toy` material icon with a branded SwarmAI icon.
3. THE SwarmAI_Icon SHALL include a CSS animation (pulse or glow effect) while the assistant message is streaming.
4. WHEN the assistant message finishes streaming, THE SwarmAI_Icon SHALL stop the animation and display in a static state.

### Requirement 3: Left-Aligned Assistant Response Layout

**User Story:** As a user, I want assistant responses to be left-aligned with no indentation, so that the full width of the message area is utilized for response content.

#### Acceptance Criteria

1. THE Message_Bubble SHALL render Assistant_Message content left-aligned with the left edge of the Chat_Message_Area.
2. THE Message_Bubble SHALL remove the left-side avatar indentation gap for Assistant_Message entries, allowing content to start at the left margin below the header line.
3. THE Message_Bubble SHALL preserve the existing maximum width constraint for readability of text content.

### Requirement 4: Collapsible Tool Call Display

**User Story:** As a user, I want tool call blocks to be collapsed by default showing only an icon and title, so that I can focus on the actual response content without visual clutter.

#### Acceptance Criteria

1. WHEN an Assistant_Message contains a Tool_Call_Block, THE Content_Block_Renderer SHALL render the tool call in a collapsed state by default, showing only the tool icon and tool name on a single line with a subtle light-gray background color to minimize visual distraction.
2. THE Tool_Call_Block collapsed view SHALL display an Expansion_Toggle icon to indicate expandability.
3. WHEN the user clicks the Expansion_Toggle on a collapsed Tool_Call_Block, THE Tool_Call_Block SHALL expand to reveal the full tool call input content and copy button.
4. WHEN the user clicks the Expansion_Toggle on an expanded Tool_Call_Block, THE Tool_Call_Block SHALL collapse back to the single-line summary.
5. WHEN an Assistant_Message contains a Tool_Result_Block, THE Content_Block_Renderer SHALL render the tool result in a collapsed state by default, consistent with the Tool_Call_Block collapsed style.

### Requirement 5: Branded Welcome Screen

**User Story:** As a user, I want to see a branded SwarmAI welcome screen when I open a new chat tab, so that the product feels polished and I understand the value proposition immediately.

#### Acceptance Criteria

1. WHEN a new chat tab is opened and no messages exist in the session, THE Chat_Message_Area SHALL display the Welcome_Screen centered on the page.
2. THE Welcome_Screen SHALL display a circular SwarmAI icon (generated as a round variant with transparent background from the source icon `desktop/src-tauri/icons/swarmai-icon-3.png`, since the original has a black background that is unsuitable for direct use), the text "Welcome to SwarmAI!", the slogan "Your AI Team, 24/7", the tagline "Work smarter, move faster, and enjoy the journey.", and use a visually appealing layout referencing the style in `assets/swarmai-prototype.html`.
3. THE Welcome_Screen SHALL NOT render as a default assistant response message bubble.
4. WHEN the user sends the first message in the session, THE Welcome_Screen SHALL disappear and the Chat_Message_Area SHALL transition to the normal message display.
5. THE Welcome_Screen SHALL be displayed for every new tab, regardless of whether other tabs have active sessions.

### Requirement 6: Error Message Styling Preservation

**User Story:** As a user, I want error messages to remain visually distinct with their red border styling, so that I can immediately identify when something went wrong.

#### Acceptance Criteria

1. WHEN an Assistant_Message has the `isError` flag set to true, THE Message_Bubble SHALL continue to render the message with a red border and error background, regardless of the new layout changes.
2. THE Message_Bubble SHALL apply the redesigned assistant header (SwarmAI_Icon, title, timestamp) to error messages in the same manner as non-error assistant messages.

### Requirement 7: Expansion State Idempotence

**User Story:** As a user, I want expand/collapse toggles to behave predictably, so that repeated clicks always produce the expected result.

#### Acceptance Criteria

1. FOR ALL Expansion_Toggle interactions, THE Message_Bubble SHALL ensure that toggling expand then collapse returns the component to its original visual state (idempotence property).
2. FOR ALL Tool_Call_Block entries, THE Content_Block_Renderer SHALL ensure that the collapsed state is the default on initial render, regardless of the content length.

### Requirement 8: Responsive Truncation Re-evaluation

**User Story:** As a user, I want the "Show more" toggle on my messages to correctly appear or disappear when the chat area resizes (e.g., sidebar toggle), so that truncation behavior stays accurate.

#### Acceptance Criteria

1. WHEN the Chat_Message_Area width changes (e.g., due to sidebar toggle or window resize), THE UserMessageView SHALL re-evaluate whether the content exceeds 5 lines and update the Expansion_Toggle visibility accordingly.

### Requirement 9: Icon Treatment Differentiation

**User Story:** As a user, I want the welcome screen to show the polished brand icon and assistant messages to show the 🐝 bee emoji, so that branding is prominent on first impression while chat messages feel lightweight and thematic.

#### Acceptance Criteria

1. THE Welcome_Screen SHALL display the SwarmAI brand icon as an image element (circular PNG), NOT the 🐝 emoji.
2. THE AssistantHeader SHALL display the 🐝 emoji as the icon, NOT the brand image icon.
