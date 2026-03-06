# Requirements Document

## Introduction

This feature addresses two UX gaps in the SwarmAI chat interface that become apparent when the agent performs many tool calls in a single response:

1. **Consecutive Tool Call Grouping** — When the agent invokes the same tool category multiple times in a row (e.g., 7 consecutive WebFetch calls), the chat UI renders 7 separate MergedToolBlock rows. This is visually noisy and hard to scan. Consecutive same-category tool calls should be collapsed into a single summary row (e.g., "Fetched 7 URLs") with an expand toggle to reveal individual calls.

2. **Source Citations in Final Response** — When the agent fetches content from URLs (web_fetch) or reads files (read), the final text response includes no source attribution. Users cannot tell which URLs or files informed the response. A "Sources" footer should appear below the final text block with clickable links to the original URLs and file paths.

Both sub-features build on the `category` and `summary` fields added by the tool-call-streaming-optimization spec.

## Glossary

- **Merged_Tool_Block**: The existing React component that renders a single tool_use + tool_result pair as one visual row (icon + summary + status).
- **Tool_Group**: A new visual component that collapses consecutive Merged_Tool_Blocks of the same category into a single summary row with an expand/collapse toggle.
- **Category**: A string field on ToolUseContent blocks identifying the tool type: `bash`, `read`, `write`, `search`, `web_fetch`, `web_search`, `list_dir`, `todowrite`, `fallback`.
- **Assistant_Message_View**: The React component that renders a complete assistant message, including building the resultMap and iterating over content blocks.
- **Content_Block_Renderer**: The React component that routes individual content blocks to type-specific renderers.
- **Source_Citation**: A reference to a URL or file path that the agent accessed during the response, displayed as a clickable link.
- **Sources_Footer**: A UI section rendered below the final text block in an assistant message, listing all Source_Citations collected during that message.
- **Summary_Field**: The human-readable string on ToolUseContent blocks (e.g., "Fetching https://bbc.com/news", "Reading src/main.ts").

## Requirements

### Requirement 1: Consecutive Tool Call Group Detection

**User Story:** As a developer using SwarmAI, I want consecutive tool calls of the same category to be detected and grouped together, so that the chat UI can render them as a single collapsed unit instead of many separate rows.

#### Acceptance Criteria

1. WHEN two or more consecutive tool_use content blocks share the same `category` value, THE Assistant_Message_View SHALL identify them as a Tool_Group.
2. THE grouping logic SHALL preserve the original order of tool_use blocks within each Tool_Group.
3. WHEN a tool_use block has a different `category` than the preceding tool_use block, THE Assistant_Message_View SHALL start a new Tool_Group.
4. WHEN a non-tool_use content block (text, ask_user_question) appears between tool_use blocks, THE Assistant_Message_View SHALL break the current Tool_Group.
5. WHEN a Tool_Group contains exactly one tool_use block, THE Assistant_Message_View SHALL render it as a standard Merged_Tool_Block without group chrome.
6. THE grouping logic SHALL operate on the `message.content` array at the Assistant_Message_View level, using the same resultMap used for tool_use/tool_result pairing.

### Requirement 2: Tool Group Collapsed View

**User Story:** As a developer using SwarmAI, I want grouped tool calls to display as a single collapsed row showing the count and category, so that I can quickly scan what the agent did without visual clutter.

#### Acceptance Criteria

1. WHEN a Tool_Group is collapsed, THE Tool_Group component SHALL display a single summary row containing the category icon, a count label (e.g., "Fetched 7 URLs", "Read 3 files"), and a collapse/expand toggle.
2. THE collapsed summary label SHALL use a human-readable verb derived from the category: "Fetched" for `web_fetch`, "Read" for `read`, "Wrote" for `write`, "Ran" for `bash`, "Searched" for `search` and `web_search`, "Listed" for `list_dir`, "Updated" for `todowrite`, "Used" for `fallback`.
3. THE collapsed summary label SHALL include the count of tool_use blocks in the group (e.g., "Fetched 7 URLs", "Read 3 files").
4. THE Tool_Group component SHALL default to the collapsed state.
5. WHEN all tool_use blocks in a Tool_Group are still pending (streaming), THE collapsed row SHALL display a spinning/loading indicator instead of a success/error icon.
6. WHEN any tool_use block in a Tool_Group has an error result, THE collapsed row SHALL display an error count indicator (e.g., "Fetched 7 URLs (2 errors)").

### Requirement 3: Tool Group Expanded View

**User Story:** As a developer using SwarmAI, I want to expand a grouped tool call row to see the individual tool calls and their results, so that I can inspect specific calls when needed.

#### Acceptance Criteria

1. WHEN the user clicks the expand toggle on a collapsed Tool_Group, THE Tool_Group component SHALL expand to show all individual Merged_Tool_Block components within the group.
2. WHEN the Tool_Group is expanded, THE individual Merged_Tool_Blocks SHALL render with the same behavior as ungrouped Merged_Tool_Blocks (summary line, inline/collapsible result, status icons).
3. WHEN the user clicks the collapse toggle on an expanded Tool_Group, THE Tool_Group component SHALL return to the collapsed summary row.
4. THE expand/collapse state SHALL be independent per Tool_Group within a message.
5. THE expand/collapse toggle SHALL be keyboard-accessible (activatable via Enter or Space key).
6. THE Tool_Group component SHALL use `aria-expanded` to communicate the expand/collapse state to assistive technologies.

### Requirement 4: Source Tracking from Tool Calls

**User Story:** As a developer using SwarmAI, I want the frontend to automatically track which URLs and file paths the agent accessed during a response, so that source citations can be generated without backend changes.

#### Acceptance Criteria

1. WHEN a tool_use content block has `category` equal to `web_fetch`, THE source tracker SHALL extract the URL from the `summary` field and record it as a Source_Citation.
2. WHEN a tool_use content block has `category` equal to `read`, THE source tracker SHALL extract the file path from the `summary` field and record it as a Source_Citation.
3. THE source tracker SHALL deduplicate Source_Citations by URL or file path within a single assistant message (the same URL fetched twice produces one citation).
4. THE source tracker SHALL preserve the order of first appearance for Source_Citations.
5. THE source tracker SHALL operate at the Assistant_Message_View level by scanning the `message.content` array.
6. IF a tool_use block with `category` `web_fetch` or `read` has an error result (`isError` is true), THEN THE source tracker SHALL exclude that source from the citations list.

### Requirement 5: Source Citation URL/Path Extraction

**User Story:** As a developer using SwarmAI, I want URLs and file paths to be reliably extracted from tool_use summary strings, so that citations link to the correct resources.

#### Acceptance Criteria

1. WHEN the summary field contains a URL (starting with `http://` or `https://`), THE extractor SHALL parse the full URL from the summary string.
2. WHEN the summary field is in the format "Reading {file_path}" or "Fetching {url}", THE extractor SHALL extract the path or URL portion after the verb prefix.
3. WHEN the summary field contains a file path without a verb prefix, THE extractor SHALL treat the entire trimmed string as the file path.
4. THE extractor SHALL handle summary strings with trailing whitespace or punctuation by trimming non-path characters.
5. FOR ALL valid URLs extracted, parsing the URL then formatting it back SHALL produce an equivalent string (round-trip property).
6. FOR ALL valid file paths extracted, the path SHALL contain no leading or trailing whitespace.

### Requirement 6: Sources Footer Rendering

**User Story:** As a developer using SwarmAI, I want a "Sources" section to appear below the final text block in an assistant message, so that I can see and click through to the URLs and files that informed the response.

#### Acceptance Criteria

1. WHEN an assistant message contains one or more Source_Citations, THE Assistant_Message_View SHALL render a Sources_Footer below the last content block.
2. THE Sources_Footer SHALL display a "Sources" heading label.
3. THE Sources_Footer SHALL list each Source_Citation as a clickable link.
4. WHEN a Source_Citation is a URL, THE Sources_Footer SHALL render it as an external hyperlink that opens in the default browser.
5. WHEN a Source_Citation is a file path, THE Sources_Footer SHALL render it as a clickable link that opens the file in the editor (using the existing file-open mechanism).
6. WHEN an assistant message contains zero Source_Citations, THE Assistant_Message_View SHALL NOT render a Sources_Footer.
7. THE Sources_Footer SHALL NOT appear while the assistant message is still streaming (render only after streaming completes).
8. THE Sources_Footer SHALL display a maximum of 10 Source_Citations, with a "and N more" indicator when the total exceeds 10.

### Requirement 7: Source Citation Display Format

**User Story:** As a developer using SwarmAI, I want source citations to be displayed in a compact, readable format, so that I can quickly identify each source without visual noise.

#### Acceptance Criteria

1. WHEN a Source_Citation is a URL, THE Sources_Footer SHALL display the domain name and path (e.g., "bbc.com/news/article-123") rather than the full URL with protocol.
2. WHEN a Source_Citation is a file path, THE Sources_Footer SHALL display the filename with its immediate parent directory (e.g., "core/agent_manager.py") rather than the full absolute path.
3. THE Sources_Footer SHALL display a category icon next to each citation: the `language` icon for URLs and the `description` icon for file paths (matching the existing CATEGORY_ICONS in MergedToolBlock).
4. WHEN a URL display label exceeds 60 characters, THE Sources_Footer SHALL truncate it with an ellipsis and show the full URL in a tooltip on hover.
5. WHEN a file path display label exceeds 60 characters, THE Sources_Footer SHALL truncate it with an ellipsis and show the full path in a tooltip on hover.

### Requirement 8: Streaming Compatibility

**User Story:** As a developer using SwarmAI, I want tool grouping and source tracking to work correctly during streaming, so that the UI updates progressively as new tool calls arrive.

#### Acceptance Criteria

1. WHILE the assistant message is streaming, THE grouping logic SHALL re-compute Tool_Groups as new tool_use blocks arrive.
2. WHILE the assistant message is streaming, THE source tracker SHALL accumulate Source_Citations as new tool_use blocks arrive (but the Sources_Footer is not rendered until streaming completes per Requirement 6.7).
3. WHILE the assistant message is streaming and a Tool_Group is still accumulating (the next block has not yet arrived), THE Tool_Group collapsed row SHALL display a spinning/loading indicator.
4. WHEN a new tool_use block arrives with the same category as the current group, THE Tool_Group SHALL update its count without resetting the expand/collapse state.
5. WHEN a new tool_use block arrives with a different category, THE previous Tool_Group SHALL finalize and a new group SHALL begin.

### Requirement 9: Accessibility

**User Story:** As a developer using assistive technology, I want tool groups and source citations to be accessible, so that I can navigate and understand them with a screen reader or keyboard.

#### Acceptance Criteria

1. THE Tool_Group expand/collapse toggle SHALL have an `aria-label` describing the action (e.g., "Expand 7 web fetch tool calls" / "Collapse web fetch tool calls").
2. THE Tool_Group component SHALL use `role="group"` with an `aria-label` describing the group content (e.g., "7 web fetch tool calls").
3. THE Sources_Footer SHALL use a `nav` element with `aria-label="Sources"` to identify the landmark.
4. WHEN a Source_Citation link is a URL, THE link SHALL include `aria-label` text describing the destination (e.g., "Open bbc.com/news in browser").
5. WHEN a Source_Citation link is a file path, THE link SHALL include `aria-label` text describing the action (e.g., "Open agent_manager.py in editor").
