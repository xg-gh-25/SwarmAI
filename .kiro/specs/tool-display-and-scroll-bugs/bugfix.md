# Bugfix Requirements Document

## Introduction

Three related UI bugs in the SwarmAI desktop app degrade the chat experience during tool-heavy agent interactions. (1) All tool loading labels display "Searching web for..." regardless of actual tool type, (2) per-tool loading spinners don't stop until the entire conversation turn ends even after their individual `tool_result` arrives, and (3) scroll position in one chat tab contaminates another tab's scroll position when switching tabs. Together these bugs make multi-tool streaming sessions confusing and multi-tab workflows unreliable.

## Bug Analysis

### Current Behavior (Defect)

**Bug 1 — Tool loading labels all show "Searching web for..."**

1.1 WHEN the AI agent invokes a non-web-search tool (e.g., Bash, Read, Write, Edit, Grep, Glob, ListDirectory, WebFetch, or any MCP tool) THEN the system displays "Searching web for..." as the loading label on the tool block row instead of a label specific to the actual tool type

1.2 WHEN the AI agent invokes an MCP tool whose name follows the `mcp__ServerName__tool_name` format THEN the system displays "Searching web for..." because the full lowered MCP tool name does not match any category set via exact membership check, and the fallback branch picks up a `query` field present in many tool inputs

**Bug 2 — Per-tool loading spinner doesn't stop until ALL tools finish**

1.3 WHEN a `tool_result` SSE event arrives for a specific tool_use block during streaming THEN the corresponding MergedToolBlock's spinner continues spinning because the `tool_result` content block's `tool_use_id` (snake_case from backend) is not converted to `toolUseId` (camelCase) before being stored in the message content array, so the `resultMap` lookup in AssistantMessageView fails to pair it with the `tool_use` block

1.4 WHEN multiple tools are invoked sequentially in a single conversation turn THEN all tool block spinners keep spinning until the entire turn completes and `isStreaming` becomes false, rather than each spinner stopping independently when its own result arrives

**Bug 3 — Cross-tab scroll contamination**

1.5 WHEN the user scrolls to a specific position in Chat Tab A and then switches to Chat Tab B THEN Tab B's scroll position is affected by Tab A's scroll position because the `messagesContainerRef` DOM ref is shared across all tabs and no scroll state is saved/restored during tab switching

1.6 WHEN the user switches back to Chat Tab A after viewing Chat Tab B THEN Tab A's scroll position is lost because no per-tab scroll position is stored in the `UnifiedTab` interface or saved in `handleTabSelect()` before the switch

### Expected Behavior (Correct)

**Bug 1 — Tool loading labels should reflect actual tool type**

2.1 WHEN the AI agent invokes a non-web-search tool THEN the system SHALL display a label specific to the tool's category (e.g., "Running: npm test" for Bash, "Reading src/app.ts" for Read, "Writing to config.json" for Write/Edit, "Searching for pattern" for Grep/Glob, "Listing src/" for ListDirectory, "Fetching https://..." for WebFetch)

2.2 WHEN the AI agent invokes an MCP tool whose name follows the `mcp__ServerName__tool_name` format THEN the system SHALL display a meaningful fallback label using the tool name and any available context from the input data (e.g., "mcp__GitHub__create_issue: Fix login bug"), and SHALL NOT match the web search category

**Bug 2 — Per-tool spinner should stop when its own result arrives**

2.3 WHEN a `tool_result` SSE event arrives for a specific tool_use block during streaming THEN the system SHALL correctly convert the `tool_use_id` field to `toolUseId` so the `resultMap` in AssistantMessageView pairs it with the corresponding `tool_use` block, causing that specific MergedToolBlock's spinner to stop immediately

2.4 WHEN multiple tools are invoked sequentially in a single conversation turn THEN each tool block's spinner SHALL stop independently as soon as its own `tool_result` arrives, while other tool blocks that have not yet received results SHALL continue showing their spinners

**Bug 3 — Each tab should have independent scroll state**

2.5 WHEN the user switches from Chat Tab A to Chat Tab B THEN the system SHALL save Tab A's scroll position before the switch and restore Tab B's previously saved scroll position after the switch, so each tab maintains its own independent scroll state

2.6 WHEN the user switches back to Chat Tab A after viewing Chat Tab B THEN the system SHALL restore Tab A's previously saved scroll position so the user returns to exactly where they left off

### Unchanged Behavior (Regression Prevention)

**Bug 1 — Web search label must still work correctly**

3.1 WHEN the AI agent invokes the WebSearch tool (Claude SDK built-in) THEN the system SHALL CONTINUE TO display "Searching web for {query}" as the loading label

3.2 WHEN the AI agent invokes a tool that has no recognizable input fields (no query, path, command, url, etc.) THEN the system SHALL CONTINUE TO display the generic "Using {name}" fallback label

**Bug 2 — Streaming lifecycle must remain correct**

3.3 WHEN a conversation turn is still in progress and a tool_use block has not yet received its tool_result THEN the system SHALL CONTINUE TO show the spinning progress indicator on that tool block (isPending = true)

3.4 WHEN a conversation turn completes (isStreaming becomes false) THEN the system SHALL CONTINUE TO stop all remaining spinners and show the final state for all tool blocks

3.5 WHEN tool_result blocks arrive for background tabs (not the active tab) THEN the system SHALL CONTINUE TO update only the tabMapRef entry for the originating tab without affecting the active tab's display state (per multi-tab isolation Principle 2)

**Bug 3 — Tab switching must preserve all existing per-tab state**

3.6 WHEN the user switches tabs THEN the system SHALL CONTINUE TO save and restore all existing per-tab state (messages, sessionId, pendingQuestion, isExpanded, contextWarning) in addition to the new scroll position state

3.7 WHEN the user switches tabs while a background tab is streaming THEN the system SHALL CONTINUE TO use `bumpStreamingDerivation()` to re-derive isStreaming from the target tab's tabMapRef entry, and SHALL NOT call `setIsStreaming()` directly (per multi-tab isolation Principle 7)

3.8 WHEN auto-scroll is active during streaming (user has not scrolled up) THEN the system SHALL CONTINUE TO auto-scroll to the bottom on new messages, regardless of any saved scroll position
