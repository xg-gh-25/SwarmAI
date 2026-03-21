# Requirements Document

## Introduction

Redesign of the CompactionGuard in SwarmAI's backend to eliminate false positives during healthy sessions. The current guard activates all detection layers from session start, interfering with normal productive work. The redesigned guard activates aggressive loop detection only after a compaction event has occurred and context usage exceeds 85%, uses temporal sequence detection instead of simple count-based detection, provides rich work summaries with actual tool inputs, and gives the frontend full visibility into guard actions via properly handled SSE events.

## Glossary

- **Compaction_Guard**: Per-session Python class (`compaction_guard.py`) that detects and prevents the compaction amnesia loop. Created once per SessionUnit, tracks tool calls and context usage.
- **SessionUnit**: Per-tab state machine (`session_unit.py`) managing one Claude subprocess lifecycle through 5 states: COLD, IDLE, STREAMING, WAITING_INPUT, DEAD.
- **Compaction_Event**: The SDK's automatic context compaction triggered when the context window fills up. After compaction, the agent loses detailed memory of prior work.
- **Amnesia_Loop**: A pathological cycle where: agent fills context → SDK auto-compacts → agent forgets completed work → re-runs same tools → fills context again → repeat.
- **Context_Usage_Percentage**: The ratio of current input tokens to the model's context window size, expressed as a percentage.
- **Tool_Sequence**: An ordered list of (tool_name, input_hash) pairs representing the temporal order of tool calls within a streaming turn.
- **Sequence_Pattern**: A repeating subsequence of tool calls detected by comparing post-compaction tool sequences against pre-compaction recorded sequences.
- **Escalation_Level**: One of four severity tiers the Compaction_Guard progresses through: MONITORING, SOFT_WARN, HARD_WARN, KILL. Each level applies progressively stronger interventions.
- **SSE_Event**: A Server-Sent Event emitted by the backend during streaming, consumed by the frontend via `useChatStreamingLifecycle.ts`.
- **Work_Summary**: A structured text block injected into `/compact` instructions containing tool names, file paths, commands, and outcomes from the pre-compaction session.
- **Guard_Phase**: One of two operational modes: PASSIVE (before any compaction, no interference) or ACTIVE (after compaction detected, monitoring enabled).

## Requirements

### Requirement 1: Passive-by-Default Guard Phase

**User Story:** As a user running a normal chat session, I want the guard to stay completely passive until a compaction event actually occurs, so that my productive work sessions are never interrupted by false positives.

#### Acceptance Criteria

1. THE Compaction_Guard SHALL initialize in PASSIVE Guard_Phase when created by SessionUnit.
2. WHILE the Compaction_Guard is in PASSIVE Guard_Phase, THE Compaction_Guard SHALL record tool calls and context usage for Work_Summary generation without triggering any warnings or interruptions.
3. WHEN a Compaction_Event is detected, THE Compaction_Guard SHALL transition from PASSIVE to ACTIVE Guard_Phase.
4. WHILE the Compaction_Guard is in PASSIVE Guard_Phase, THE Compaction_Guard SHALL return NONE from all check operations regardless of context usage percentage or tool call counts.
5. WHEN the Compaction_Guard transitions to ACTIVE Guard_Phase, THE Compaction_Guard SHALL preserve the pre-compaction Tool_Sequence as a baseline for Sequence_Pattern detection.

### Requirement 2: Context-Threshold Activation Gate

**User Story:** As a user with a large context model, I want the guard to only start monitoring for loops when context usage exceeds 85%, so that normal high-context-usage sessions on 200K+ models are not flagged.

#### Acceptance Criteria

1. WHILE the Compaction_Guard is in ACTIVE Guard_Phase AND Context_Usage_Percentage is below 85%, THE Compaction_Guard SHALL remain in MONITORING Escalation_Level without emitting warnings.
2. WHEN Context_Usage_Percentage reaches or exceeds 85% AND the Compaction_Guard is in ACTIVE Guard_Phase, THE Compaction_Guard SHALL begin Sequence_Pattern detection.
3. THE Compaction_Guard SHALL compute Context_Usage_Percentage using the model's actual context window size from PromptBuilder.get_model_context_window.
4. WHEN an unknown model identifier is provided, THE Compaction_Guard SHALL use 200,000 tokens as the fallback context window size.

### Requirement 3: Temporal Sequence Detection

**User Story:** As a user whose agent legitimately reads the same file multiple times during a multi-step task, I want the guard to detect actual repeating sequences of tools rather than counting individual tool repetitions, so that normal edit-check-edit workflows are not flagged as loops.

#### Acceptance Criteria

1. WHEN the Compaction_Guard is in ACTIVE Guard_Phase with Context_Usage_Percentage at or above 85%, THE Compaction_Guard SHALL compare the current post-compaction tool calls against the pre-compaction baseline set for loop detection.
2. THE Compaction_Guard SHALL detect a loop when more than 60% of post-compaction tool calls (minimum 5 calls) match (tool_name, input_hash) pairs from the pre-compaction baseline set.
3. WHEN a single tool is called with identical inputs 5 or more times within the post-compaction Tool_Sequence, THE Compaction_Guard SHALL treat the repetition as a detected loop regardless of baseline overlap.
4. THE Compaction_Guard SHALL NOT flag tool calls with different inputs as duplicates, even when the same tool name is used repeatedly.

### Requirement 4: Graduated Escalation Before Kill

**User Story:** As a user, I want the guard to try multiple soft interventions before ever killing my session, so that hard stops are an absolute last resort and I have opportunities to course-correct.

#### Acceptance Criteria

1. WHEN a Sequence_Pattern is first detected, THE Compaction_Guard SHALL escalate to SOFT_WARN Escalation_Level and emit a soft warning SSE_Event reminding the agent of completed work.
2. WHEN a Sequence_Pattern persists after SOFT_WARN (detected a second time in the same post-compaction turn), THE Compaction_Guard SHALL escalate to HARD_WARN Escalation_Level and emit a hard warning SSE_Event instructing the agent to summarize and stop.
3. WHEN a Sequence_Pattern persists after HARD_WARN (detected a third time in the same post-compaction turn), THE Compaction_Guard SHALL escalate to KILL Escalation_Level and emit a kill SSE_Event, after which the caller SHALL interrupt the streaming session.
4. THE Compaction_Guard SHALL progress through Escalation_Levels in strict order: MONITORING → SOFT_WARN → HARD_WARN → KILL, without skipping levels.
5. WHEN a new user message is received (reset is called), THE Compaction_Guard SHALL reset the Escalation_Level to MONITORING while preserving ACTIVE Guard_Phase and the pre-compaction baseline.

### Requirement 5: Rich Work Summary Generation

**User Story:** As a user whose session gets compacted, I want the post-compaction injection to include actual file paths, commands, and outcomes from my session, so that the agent has meaningful context about what it already accomplished.

#### Acceptance Criteria

1. THE Compaction_Guard SHALL record the full tool input for each tool call, including file paths, command strings, and search patterns.
2. WHEN generating a Work_Summary, THE Compaction_Guard SHALL include for each tool group: the tool name, call count, and up to 5 representative input details (file paths for Read/Edit/Write, commands for Bash, patterns for Grep/Glob).
3. WHEN generating a Work_Summary, THE Compaction_Guard SHALL truncate individual input details to 200 characters to prevent the summary itself from consuming excessive context.
4. WHEN the compact method is called on SessionUnit, THE Compaction_Guard SHALL append the Work_Summary to the compact instructions.
5. THE Compaction_Guard SHALL sort tool groups in the Work_Summary by call count in descending order.
6. WHEN no tool calls have been recorded, THE Compaction_Guard SHALL return an empty string from Work_Summary generation.

### Requirement 6: Frontend Guard Visibility

**User Story:** As a user, I want to see guard warnings, context status, and escalation actions in the chat UI, so that I understand why my session was interrupted instead of experiencing a silent death.

#### Acceptance Criteria

1. WHEN the Compaction_Guard emits a SOFT_WARN SSE_Event, THE Frontend SHALL display a dismissible warning banner showing the detected pattern and context usage percentage.
2. WHEN the Compaction_Guard emits a HARD_WARN SSE_Event, THE Frontend SHALL display a persistent warning banner with the escalation reason and a suggestion to start a new session.
3. WHEN the Compaction_Guard emits a KILL SSE_Event, THE Frontend SHALL display an error message explaining that the session was stopped due to a detected amnesia loop, with a button to start a new session.
4. THE Backend SHALL emit guard SSE_Events with type "compaction_guard" and a subtype field indicating the Escalation_Level (soft_warn, hard_warn, kill).
5. THE Frontend SHALL store the latest guard SSE_Event in the per-tab UnifiedTab state via tabMapRef, following the display mirror pattern used by context_warning events.

### Requirement 7: Compaction Event Detection

**User Story:** As a developer integrating the guard, I want a reliable mechanism to detect when the SDK has performed a compaction, so that the guard transitions to ACTIVE phase at the right time.

#### Acceptance Criteria

1. WHEN the SessionUnit receives a message from the SDK indicating compaction has occurred (context_compacted event or equivalent SDK signal), THE SessionUnit SHALL call the Compaction_Guard's activation method to transition to ACTIVE Guard_Phase.
2. WHEN the SessionUnit's compact method is called explicitly (user-triggered or auto-triggered), THE SessionUnit SHALL call the Compaction_Guard's activation method after the compact command completes successfully.
3. IF the SDK does not emit an explicit compaction signal, THEN THE Compaction_Guard SHALL detect compaction heuristically by observing a significant drop in Context_Usage_Percentage (decrease of 30 percentage points or more between consecutive updates).
4. THE Compaction_Guard SHALL log the compaction detection event with the pre-compaction and post-compaction Context_Usage_Percentage values.

### Requirement 8: Guard Lifecycle Management

**User Story:** As a developer, I want clear lifecycle semantics for the guard across session states, so that the guard resets appropriately on new messages, session restarts, and subprocess respawns.

#### Acceptance Criteria

1. WHEN a new user message is received (SessionUnit.send is called), THE Compaction_Guard SHALL reset per-turn tracking (Escalation_Level, per-turn sequence buffer) while preserving Guard_Phase, pre-compaction baseline, and cumulative context usage.
2. WHEN the SessionUnit subprocess is respawned (COLD → IDLE transition), THE Compaction_Guard SHALL perform a full reset including Guard_Phase back to PASSIVE, clearing all baselines and context tracking.
3. WHEN the user answers a question or grants a permission (continue_with_answer, continue_with_permission), THE Compaction_Guard SHALL reset per-turn tracking while preserving Guard_Phase and baseline.
4. THE Compaction_Guard SHALL maintain separate storage for pre-compaction baseline Tool_Sequence and post-compaction current Tool_Sequence.
