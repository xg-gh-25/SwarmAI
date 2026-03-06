# Requirements Document

## Introduction

This feature optimizes the SwarmAI chat streaming pipeline to reduce SSE payload size, lower React state and SQLite storage bloat, and present tool call information in a human-friendly format. The optimization has three sub-features: (1) replacing full tool call inputs in SSE events with short human-readable summaries, (2) truncating tool result content in SSE events, and (3) merging tool_use + tool_result into a single visual unit in the chat UI (inspired by Kiro's inline tool rendering pattern).

**Key Architecture Insight:** The Claude Agent SDK manages the full conversation context internally. When the SDK processes tool calls, it sends tool_use to Claude, Claude executes the tool, the SDK sends the full tool_result back to Claude as conversation context, and Claude reads the full result to decide the next action. Our app's `_save_message()` is for UI replay only (showing chat history in the frontend). The SDK handles the conversation loop independently, so summaries and truncated previews are sufficient for DB persistence and chat history display.

## Glossary

- **SSE_Stream**: The Server-Sent Events stream from the FastAPI backend to the React frontend that delivers chat content blocks in real time.
- **Tool_Use_Block**: A content block emitted by the Claude Agent SDK representing a tool invocation, containing `name`, `id`, and `input` fields.
- **Tool_Result_Block**: A content block emitted by the Claude Agent SDK representing the output of a tool invocation, containing `tool_use_id`, `content`, and `is_error` fields.
- **Summary**: A short, human-readable string (under 200 characters) describing a tool invocation in plain language (e.g., "Reading backend/core/agent_manager.py").
- **Summarizer**: A backend function that generates a Summary from a Tool_Use_Block's name and input fields.
- **Truncation_Limit**: A configurable maximum character count (default 500) for tool result content sent over the SSE_Stream.
- **Format_Message**: The `_format_message()` method in `AgentManager` that converts Claude SDK messages into SSE event dicts.
- **Save_Message**: The `_save_message()` method in `AgentManager` that persists summarized/truncated message content to SQLite for UI replay purposes only (not for feeding content back to the Claude Agent SDK).
- **Content_Block_Renderer**: The React component that routes content blocks to type-specific renderers (ToolUseBlock, ToolResultBlock, etc.).
- **Merged_Tool_Block**: A single visual UI component that combines a tool_use summary with its corresponding tool_result content inline, eliminating the separate "Tool Result" row. Inspired by Kiro's inline tool rendering pattern.
- **Inline_Result_Limit**: A character threshold (default 200) below which tool result content is shown inline directly beneath the summary line without any expand/collapse chrome.

## Requirements

### Requirement 1: Tool-Specific Summary Generation

**User Story:** As a developer using SwarmAI, I want tool call inputs to be summarized into short human-readable strings, so that I can quickly understand what the agent is doing without parsing raw JSON.

#### Acceptance Criteria

1. WHEN Format_Message processes a Tool_Use_Block, THE Summarizer SHALL generate a Summary string from the tool name and input fields.
2. THE Summarizer SHALL produce a Summary under 200 characters for all supported tool types.
3. WHEN the tool name is "Bash" or "bash", THE Summarizer SHALL extract the `command` field and produce a Summary in the format "Running: {sanitized_command_preview}".
4. WHEN the tool name indicates a file read operation (e.g., "Read", "ReadFile", "View"), THE Summarizer SHALL extract the `path` or `file_path` field and produce a Summary in the format "Reading {file_path}".
5. WHEN the tool name indicates a file write operation (e.g., "Write", "WriteFile", "Create", "Edit"), THE Summarizer SHALL extract the `path` or `file_path` field and produce a Summary in the format "Writing to {file_path}".
6. WHEN the tool name indicates a search operation (e.g., "Grep", "Search", "Find", "Glob"), THE Summarizer SHALL extract the `pattern` or `query` field and produce a Summary in the format "Searching for {pattern}".
7. WHEN the tool name does not match any known category, THE Summarizer SHALL produce a Summary in the format "Using {tool_name}" as a fallback.
8. WHEN the `command` field in a Bash tool input contains sensitive tokens (passwords, keys, secrets), THE Summarizer SHALL sanitize the command preview by redacting those tokens.
9. WHEN the tool name is "TodoWrite", THE Summarizer SHALL extract the `todos` list from the input and produce a Summary in the format "Writing {N} todos" where N is the list length.
10. THE Summarizer SHALL use case-insensitive matching (via `name.lower()`) for tool name categorization.

### Requirement 2: SSE Tool Use Event Optimization

**User Story:** As a frontend developer, I want the SSE stream to send only a summary for tool_use blocks instead of the full input dict, so that SSE payloads are smaller and React state stays lean.

#### Acceptance Criteria

1. WHEN Format_Message processes a Tool_Use_Block, THE SSE_Stream SHALL emit a tool_use content block containing only `type`, `id`, `name`, and `summary` fields.
2. WHEN Format_Message processes a Tool_Use_Block, THE SSE_Stream SHALL omit the full `input` dict from the emitted tool_use content block.
3. WHEN Format_Message processes an "AskUserQuestion" Tool_Use_Block, THE Format_Message method SHALL continue to return the special ask_user_question event with full question data unchanged.

### Requirement 3: Tool Result Content Truncation

**User Story:** As a developer using SwarmAI, I want tool result content to be truncated in the SSE stream, so that large outputs (50KB+) do not bloat the streaming payload and React state.

#### Acceptance Criteria

1. WHEN Format_Message processes a Tool_Result_Block whose content exceeds the Truncation_Limit, THE SSE_Stream SHALL emit a tool_result content block with content truncated to the Truncation_Limit and a `truncated` flag set to true.
2. WHEN Format_Message processes a Tool_Result_Block whose content is within the Truncation_Limit, THE SSE_Stream SHALL emit the full content with the `truncated` flag set to false.
3. THE Truncation_Limit SHALL default to 500 characters.
4. THE Truncation_Limit SHALL be configurable via a constant or environment variable.
5. WHEN a Tool_Result_Block has `is_error` set to true, THE SSE_Stream SHALL include the `is_error` flag in the emitted content block alongside the truncated content.

### Requirement 4: Frontend Type Updates

**User Story:** As a frontend developer, I want the TypeScript types to reflect the new summarized tool_use and truncated tool_result shapes, so that the frontend code is type-safe against the optimized SSE events.

#### Acceptance Criteria

1. THE ToolUseContent interface SHALL include a `summary` field of type `string`.
2. THE ToolUseContent interface SHALL NOT include an `input` field, since the SSE stream and DB both store only the summary.
3. THE ToolResultContent interface SHALL include a `truncated` field of type `boolean`.

### Requirement 5: Merged Tool Call + Result Rendering

**User Story:** As a developer using SwarmAI, I want each tool call and its result to be displayed as a single visual unit in the chat UI, so that I can quickly scan what the agent did and what happened without navigating separate "Tool Use" and "Tool Result" rows.

#### Acceptance Criteria

1. WHEN a tool_use content block is followed by a tool_result content block with a matching `tool_use_id`/`id`, THE Content_Block_Renderer SHALL render them as a single Merged_Tool_Block component.
2. THE Merged_Tool_Block SHALL display the tool_use summary as the primary label line (icon + summary text).
3. WHEN the tool_result content is within the Inline_Result_Limit (≤200 chars) and `truncated` is false, THE Merged_Tool_Block SHALL display the result content inline directly beneath the summary line without any expand/collapse toggle.
4. WHEN the tool_result content exceeds the Inline_Result_Limit OR `truncated` is true, THE Merged_Tool_Block SHALL display the result in a collapsible section beneath the summary line, collapsed by default.
5. WHEN the tool_result has `is_error` set to true, THE Merged_Tool_Block SHALL display an error status indicator (red icon) on the summary line.
6. WHEN the tool_result has `is_error` set to false, THE Merged_Tool_Block SHALL display a success status indicator (green icon) on the summary line.
7. WHEN a tool_use content block does NOT yet have a matching tool_result (still streaming), THE Merged_Tool_Block SHALL display the summary line with a spinning/loading indicator.
8. WHEN a tool_use content block has no matching tool_result at all (orphaned), THE Merged_Tool_Block SHALL display the summary line without any result section and without a spinner.
9. THE Inline_Result_Limit SHALL default to 200 characters.

### Requirement 6: Standalone Tool Result Rendering (Fallback)

**User Story:** As a frontend developer, I want orphaned tool_result blocks (without a preceding tool_use) to still render correctly, so that edge cases in streaming don't produce blank or broken UI.

#### Acceptance Criteria

1. WHEN a tool_result content block appears without a preceding tool_use block with a matching id, THE Content_Block_Renderer SHALL render it as a standalone ToolResultBlock with the existing collapse/expand behavior.
2. WHEN a standalone tool_result has `truncated` set to true, THE ToolResultBlock SHALL display a "Content truncated" visual indicator.
3. WHEN a standalone tool_result has `is_error` set to true, THE ToolResultBlock SHALL display an error status indicator.

### Requirement 7: Database Persistence for UI Replay

**User Story:** As a developer using SwarmAI, I want chat messages saved to the database with summarized/truncated content only, so that the DB stays small and efficient while still supporting chat history replay in the UI.

#### Acceptance Criteria

1. THE Save_Message method SHALL persist tool_use content blocks with the `summary` field only, omitting the full `input` dict.
2. THE Save_Message method SHALL persist tool_result content blocks with truncated content only (respecting the Truncation_Limit), omitting the full untruncated output.
3. THE Save_Message method SHALL persist the `is_error` flag for tool_result content blocks.
4. THE Save_Message method SHALL persist the `truncated` flag for tool_result content blocks.
5. FOR ALL tool invocations, the content saved by Save_Message SHALL match the content emitted over the SSE_Stream (both use the same summarized/truncated representation).

### Requirement 8: Content Block Deduplication Compatibility

**User Story:** As a frontend developer, I want the content block deduplication logic to work correctly with the new summarized and truncated block shapes, so that duplicate blocks are not rendered during streaming.

#### Acceptance Criteria

1. THE blockKey function SHALL continue to use `tool_use:{id}` as the dedup key for tool_use blocks, regardless of whether the block contains `summary`.
2. THE blockKey function SHALL continue to use `tool_result:{toolUseId}` as the dedup key for tool_result blocks, regardless of whether the block is truncated.
3. WHEN the SSE_Stream re-emits a tool_use or tool_result block with the same id/toolUseId, THE updateMessages function SHALL deduplicate the block and not create a duplicate entry.

### Requirement 9: Legacy Session Cleanup

**User Story:** As a developer using SwarmAI, I want old chat sessions with incompatible message formats to be automatically cleaned up, so that the app does not crash when loading old messages that lack the new `summary` and `truncated` fields.

#### Acceptance Criteria

1. WHEN the application starts, THE backend SHALL delete all existing chat messages and sessions from SQLite that were created before this optimization.
2. THE cleanup SHALL be performed as a one-time startup step (not on every restart — use a flag or version marker).
3. THE cleanup SHALL NOT affect non-chat data (agents, skills, MCP servers, tasks, todos, etc.).

## Out of Scope — Follow-Up Improvements

### P1: Inline Permission Approval
Currently, dangerous command approval uses a `PermissionRequestModal` overlay that blocks the entire chat and breaks the immersive experience. Kiro shows "Always Trust / Allow / Deny" buttons inline in the chat flow as part of the message stream. This should be redesigned as an inline chat component instead of a modal popup.
