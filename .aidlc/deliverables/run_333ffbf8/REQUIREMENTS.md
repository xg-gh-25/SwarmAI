# Requirements

**Run** `run_333ffbf8` · **profile** ? · **project** SwarmAI

> 为什么关上 我们要解决真实的问题

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

**Verdict** GO · **Scope** complex · **Scores** strategic 9, priority 8, feasibility 7

## Requirements

| ID | Requirement | Source | Status |
|---|---|---|---|
| FR-1 | One long-lived reader per CLI subprocess consumes the SDK message stream from spawn to kill | user-stated | confirmed |
| FR-2 | Turns are paired to user messages by the replayed uuid, not by order | derived | confirmed |
| FR-3 | CLI-initiated turns are persisted and shown through the existing DB + streaming-state path | derived | confirmed |
| FR-4 | User messages wait in the pending queue while the CLI is not quiescent | derived | confirmed |
| FR-5 | A unit with a live background task is protected from eviction | derived | confirmed |

**FR-1 acceptance**
- Given an IDLE session whose CLI starts a turn after a background task · when the turn's first tool call needs a PreToolUse hook · then the hook is answered and the tool runs without a user message

**FR-2 acceptance**
- Given a CLI-initiated turn followed or interleaved by a user message · when frames arrive · then the user stream gets only frames from its own echo through the next result

**FR-3 acceptance**
- Given a CLI-initiated turn · when it completes · then its rows are in the session's messages and the tab shows them after reconcile

**FR-4 acceptance**
- Given a CLI-initiated turn running · when the user sends · then the message is pending and is delivered after the next idle

**FR-5 acceptance**
- Given IDLE unit with a running background Bash · when eviction runs · then the unit is kept

### Acceptance criteria

- AC1: with no send() in progress, a hook request that follows 100+ unconsumed SDK frames is answered (a CLI-initiated turn's first tool call runs without waiting for a user message)
- AC2: a CLI-initiated turn is persisted to the session's messages with its own client_id and label injected, and the unit reports streaming while it runs and idle after
- AC3: a user send() receives exactly its own turn: frames from the claim point of its replayed uuid through the next ResultMessage; a preceding CLI-initiated turn's result never ends it
- AC4: while the CLI is not quiescent (not idle, or a task notification arrived since the last idle) a user message is stored as pending and delivered at the next idle
- AC5: a user message folded into a CLI-initiated turn (replay echo mid-turn) is claimed from its echo; both the sink and the user stream end on the same ResultMessage
- AC6: an IDLE unit whose CLI has a live background task is not evicted
- AC7: session_state idle with an open turn or an unechoed uuid ends them with TurnAbandoned instead of hanging; a reader error ends the open turn and the next claim with the same exception
- AC8: the existing streaming, compact, leak-continuation and Stop behaviour of user turns is unchanged (their current tests stay green)

## Understanding (Gate 0)

- **Present state:** When the Claude CLI starts a turn on its own (a background command or background subagent finished), no daemon code reads the SDK message stream, so the SDK's single reader parks on its full 100-message buffer, the PreToolUse hook request of that turn's first tool call is never read, and the tool waits until the user's next send() drives receive_response again; the turn is invisible in the UI and its messages are persisted under the user's next turn
- **Evidence:** REPRO gate: probe in session 97df0539 (SDK transcript ff64a808): task-notification enqueued 06:44:31.6, model tool_use 06:44:46.0, tool_result 06:46:46.5 = the user's next message enqueue 06:46:46.0; the tool's own `date` printed 14:46:46. DB messages rows of that autonomous turn are stamped 14:46:46 under the user's row. 14-day transcript scan (/tmp/run_7044cde6/scan.py, scan2.py): 36 CLI-initiated turns, 10 stalled at their first tool call until the next user message (103s-4511s). Source: claude_agent_sdk/_internal/query.py:229-231 max_buffer_size=100, :409-415 control_request dispatch and …
- **Alternative framing considered:** The tool itself ran for two minutes: rejected, its own date output shows it started at 14:46:46, two minutes after the tool_use was emitted.
- **Gate-0 skeptic:** SUPPORTED

## Root cause

W1. the CLI-initiated turn's first tool call does not start until the user's next message arrives  
   _evidence:_ transcript ff64a808: tool_use 06:44:46.0, tool_result 06:46:46.5, user enqueue 06:46:46.0; the tool's own date output 14:46:46
W2. its PreToolUse hook is a control_request that the SDK's single reader must read from stdout, and that reader is parked on a full 100-slot stream nobody consumes  
   _evidence:_ claude_agent_sdk/_internal/query.py:229-231, :409-415, :505; core/prompt_builder.py:2509 include_partial_messages=True
W3. the session design assumes every CLI turn starts with the daemon's own query(): the stream is read only inside a send()-driven turn and the frontend gets events only on the POST that started that turn  
   _evidence:_ core/streaming_orchestrator.py:1352 receive_response inside stream_query; core/session_unit.py:570-582 _client_io serializes those per-turn consumers; routers/chat.py:437 /stream is the only chat SSE

- **Root layer:** module-design
- **Missed invariant:** the CLI owns turn boundaries: its output stream must be consumed for the whole subprocess life, not only during turns the daemon started
- **Searched:** grep -rn -e receive_response -e receive_messages backend/core backend/routers; /tmp/run_7044cde6/scan.py and scan2.py over 14 days of SwarmWS transcripts; research run run_7044cde6 REPORT.md
- **Prior instances:** 2:
  - 10 of 36 CLI-initiated turns in 14 days stalled at their first tool call until the next user message (103s to 4511s)
  - streaming_orchestrator.py:1134 _interrupt_draining exists because the same 100-slot buffer blocked the interrupt control response on user Stop
- **Siblings (same shape elsewhere):** 4:
  - C1 claude_agent_sdk/client.py:566-606 receive_response — ends at the first ResultMessage with no turn correlation, so a CLI-initiated text-only turn's result ends the next user turn's read early
  - C2 core/session_unit.py:4537-4541 compact() — also drives receive_response and would consume a CLI-initiated turn's frames as compaction output
  - C3 core/streaming_orchestrator.py:887-888 _subagent_progress_event — drops Task* frames not tied to a step of the current turn
  - C4 core/session_unit.py:1178-1183 is_protected — an IDLE unit with a live background task can be evicted, killing the task

## Pre-mortem

- the new turn bus changes timing of user turns and breaks an existing streaming guard (heartbeat, stall watchdog, permission race) that silently relied on reading receive_response directly
- a CLI behaviour the routing relies on (replay echo position, idle re-emission) differs in a path the spike did not exercise, e.g. compact or --resume with a pending notification, and a user turn is mis-claimed
- the injected-turn transition IDLE to STREAMING races the drain worker or a user send at the IDLE edge and either double-sends or strands a pending message
