# Bugfix Requirements Document

## Introduction

When a user sends a chat message that triggers a multi-turn agent conversation (involving tool use like Bash, Grep, Read, Write), the frontend displays only a static "Thinking..." spinner for the entire duration — which can be 2-7 minutes for complex queries. The backend correctly streams SSE events (AssistantMessage with text and tool_use blocks, UserMessage with tool results) throughout the conversation, but the frontend fails to provide real-time visibility into what the model is doing. The root cause is twofold:

1. **The "Thinking..." spinner renders unconditionally** whenever `isStreaming` is true (ChatPage.tsx line 907-912), regardless of whether content blocks have already been received and rendered in the assistant message bubble above it.
2. **The `createStreamHandler` appends content blocks to a single assistant message** created at send time, but intermediate assistant messages between tool calls (new AssistantMessage events after tool results) are merged into the same message rather than shown as progressive updates — making it appear as if nothing is happening until the final result.

The combined effect is that users see "Thinking..." with no feedback, even though the backend is actively streaming 100+ messages with tool invocations and intermediate text.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the backend streams an `assistant` SSE event containing `tool_use` content blocks during a multi-turn conversation THEN the system renders the tool use block inside the assistant message bubble but the "Thinking..." spinner continues to display below it, giving the impression that no progress has been made

1.2 WHEN the backend streams multiple `assistant` SSE events with intermediate text blocks between tool calls THEN the system appends all content to a single assistant message bubble, but the "Thinking..." spinner at the bottom of the message list obscures the fact that content is actively being added

1.3 WHEN a complex query triggers 50+ tool invocations over 2-7 minutes THEN the user sees only "Thinking..." with no indication of which tools are being used, what files are being read, or what commands are being executed

1.4 WHEN the backend streams `assistant` events with `tool_use` blocks containing tool names like "Bash", "Read", "Grep", "Write" THEN the system does not surface a human-readable activity indicator (e.g., "Running Bash command...", "Reading file...") — the tool details are only visible if the user scrolls up to find the collapsed ToolUseBlock widget inside the message bubble

### Expected Behavior (Correct)

2.1 WHEN the backend streams an `assistant` SSE event containing content blocks AND the assistant message bubble already has rendered content THEN the system SHALL hide the "Thinking..." spinner, since visible progress is being shown in the message bubble

2.2 WHEN the backend streams an `assistant` SSE event containing `tool_use` content blocks THEN the system SHALL display a real-time activity indicator near the bottom of the chat (e.g., "Running: Bash command...", "Reading file...", "Searching...") that replaces or augments the "Thinking..." spinner

2.3 WHEN the backend streams `assistant` SSE events with intermediate text blocks between tool calls THEN the system SHALL ensure the message bubble scrolls to show the latest content so the user can see progressive text output

2.4 WHEN the backend streams an `assistant` SSE event with `tool_use` blocks THEN the system SHALL update the activity indicator with the most recent tool name to show what the model is currently doing

### Unchanged Behavior (Regression Prevention)

3.1 WHEN the backend has not yet sent any `assistant` SSE event (initial API wait time) THEN the system SHALL CONTINUE TO display the "Thinking..." spinner as it does today

3.2 WHEN a simple single-turn query completes quickly with one assistant response THEN the system SHALL CONTINUE TO render the response in a single message bubble with no visual difference from current behavior

3.3 WHEN the backend streams an `ask_user_question` event THEN the system SHALL CONTINUE TO pause streaming, hide the spinner, and display the question form as it does today

3.4 WHEN the backend streams a `cmd_permission_request` event THEN the system SHALL CONTINUE TO pause streaming, hide the spinner, and display the permission modal as it does today

3.5 WHEN the backend streams a `result` event THEN the system SHALL CONTINUE TO finalize the conversation, stop streaming, and invalidate radar caches as it does today

3.6 WHEN the user clicks the stop button during streaming THEN the system SHALL CONTINUE TO abort the stream and display the stop confirmation message as it does today


## Bug Condition Derivation

### Bug Condition Function

```pascal
FUNCTION isBugCondition(X)
  INPUT: X of type StreamingState
  OUTPUT: boolean
  
  // The bug manifests when the frontend is streaming (isStreaming=true)
  // AND the assistant message already contains rendered content blocks
  // (text, tool_use, or tool_result), yet the "Thinking..." spinner
  // still displays unconditionally at the bottom of the chat.
  RETURN X.isStreaming = true 
     AND X.assistantMessage.content.length > 0
     AND X.assistantMessage.content HAS blocks of type "text" OR "tool_use" OR "tool_result"
END FUNCTION
```

### Property Specification — Fix Checking

```pascal
// Property: Fix Checking — Spinner reflects actual progress state
FOR ALL X WHERE isBugCondition(X) DO
  ui ← renderChat'(X)
  ASSERT ui.thinkingSpinner.visible = false
     OR  ui.activityIndicator.visible = true
  // When content blocks exist, either the spinner is hidden (replaced by
  // visible content) or an activity indicator shows the current tool name.
END FOR
```

```pascal
// Property: Fix Checking — Tool activity indicator shows current tool
FOR ALL X WHERE isBugCondition(X) AND X.latestBlock.type = "tool_use" DO
  ui ← renderChat'(X)
  ASSERT ui.activityIndicator.toolName = X.latestBlock.name
END FOR
```

### Preservation Goal

```pascal
// Property: Preservation Checking — No-content streaming still shows spinner
FOR ALL X WHERE NOT isBugCondition(X) DO
  // When isStreaming=true but no content blocks exist yet (initial API wait),
  // the spinner must still display as before.
  ASSERT renderChat(X) = renderChat'(X)
END FOR
```

```pascal
// Property: Preservation Checking — Non-streaming states unchanged
FOR ALL X WHERE X.isStreaming = false DO
  ASSERT renderChat(X) = renderChat'(X)
END FOR
```
