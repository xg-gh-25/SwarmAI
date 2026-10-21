# Requirements

**Run** `run_a0447441` · **profile** full · **project** SwarmAI

> 第一条绝对不可接受 不然streaming 就失去了意义 而且信息是错乱的 misleading 用户 你系统化的看下 我们到底还有什么类似的问题 一起从根上解决

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

**Verdict** GO · **Scope** complex · **Scores** strategic 9, priority 9, feasibility 6

## Requirements

| ID | Requirement | Source | Status |
|---|---|---|---|
| FR-1 | Per-session live event channel: the backend publishes every turn event of a session, with a monotonically increasing per-session sequence number, to a channel a desktop tab subscribes to independently of any POST. | user-stated | confirmed |
| FR-2 | Turn lifetime belongs to the session, not the HTTP request: an SSE/connection drop no longer interrupts the turn; reconnect resumes from the last sequence number. | derived | confirmed |
| FR-3 | Questions, permission requests and ui_command events from any turn reach the subscribed tab live. | derived | confirmed |
| FR-4 | Polling/DB reconcile becomes a safety net; per-cause frontend rescues that existed only because there was no channel are removed. | user-stated | confirmed |

**FR-1 acceptance**
- Given a tab subscribed to session S · when any turn of S produces an event · then the tab receives it live, tagged with S's next sequence number

**FR-2 acceptance**
- Given a turn streaming to a tab · when the tab's connection drops and reconnects · then the turn was never interrupted and the tab shows every event once

**FR-3 acceptance**
- Given an injected or drained turn asks a question · when the CLI emits the AskUserQuestion · then the tab shows the question prompt within 1s

**FR-4 acceptance**
- Given the channel is live · when a drained pending turn runs · then it renders from the channel; the busy-poll DB surface is not the delivery path

### Acceptance criteria

- AC1: A tab subscribed to a session receives, live and in order, the events of EVERY turn of that session (user POST turn, drained pending turn, auto-continue turn, answer/permission continuation, CLI-initiated injected turn) within 1s of the backend producing them, with no dependency on GET /streaming-state polling.
- AC2: A CLI-initiated turn that starts and ends in under 3s while the tab is idle shows its spinner and its streamed content live, not on a later reload.
- AC3: Closing or dropping the HTTP connection of a tab (network drop, reload, frontend stall timer) does not interrupt the backend turn; on reconnect the tab replays every event after its last received sequence number and the live view converges to the DB view with no duplicated or missing bubble.
- AC4: AskUserQuestion / permission requests and ui_command events raised inside a turn that has no POST stream reach the subscribed tab live.
- AC5: GET /streaming-state polling and DB reconcile remain as the safety net only; a lost subscription is detected and re-established without user action.
- AC6: The frontend has one live-delivery path for turn events; the per-cause polling rescues it replaces (busy-poll drain surface, symmetric re-arm as primary delivery) are removed or demoted to the safety net, with net reduction of frontend reconcile code.

## Understanding (Gate 0)

- **Present state:** The only backend-to-tab push channel today is the SSE response of a user's POST (/chat/stream, /answer-question, /cmd-permission-continue), and a turn's lifetime is bound to that request: every turn that runs without such a request (drained pending turn, auto-continue after a disconnect, CLI-initiated injected turn) reaches the UI only through GET /streaming-state polling (3s while a tab streams, 15s idle) plus DB reloads at whole-message granularity, and a dropped connection interrupts the running turn.
- **Evidence:** Characterization by turn source, traced in source: (1) user POST turn: live via SSE (routers/chat.py:442 /stream, StreamingResponse at :611). (2) SSE drop mid-turn: _recover_streaming_on_disconnect (routers/chat.py:49, called :544/:684/:1492) transitions STREAMING->IDLE, interrupts the CLI, then core/auto_continue.py schedules a continuation with PROMPT 'Continue the task where you left off.'; daemon logs 2026-09-29..2026-10-21 hold ~100 'SSE disconnect recovery' + ~85 'Chat stream cancelled (client disconnected)' lines. The frontend itself cancels at STALL_TIMEOUT_MS=90_000 (desktop/src/serv…
- **Alternative framing considered:** Shorten the idle poll to ~1s and keep per-cause rescues: rejected because it still loses sub-second turns, keeps whole-message granularity (no live tokens) for every background turn, does nothing for ui_command/questions, keeps the disconnect->interrupt->synthetic-continue chain, and adds load; it is the 34th patch on the same layer.
- **Gate-0 skeptic:** SUPPORTED

## Root cause

W1. A short CLI-initiated turn never shows a spinner and its content appears only on a later reload, because the tab learns about it only from a 15s idle poll of GET /streaming-state followed by a DB fetch.  
   _evidence:_ streaming-guards.ts:651-652 RECONCILE_FAST_MS/RECONCILE_IDLE_MS; useChatStreamingLifecycle.ts ~:1532 shouldArmSpinnerFromBackend re-arm
W2. Chat has no way to push that turn's events to the tab: the only chat push channel is the SSE body of a POST the tab itself made, and a turn not started by such a POST has no response to write to. (Tasks already have a GET subscription stream; chat does not.)  
   _evidence:_ routers/chat.py SSE endpoints :611/:710/:1451/:1518 are all POST responses; session_router.run_injected_turn :3114 and drain_pending :3218 consume events without any subscriber; contrast routers/tasks.py:212 GET /{task_id}/stream over task_manager.subscribe (task_manager.py:415: buffered events the…
W3. The streaming design binds a turn to the HTTP request that started it (request = stream = turn), so a dropped request also ends the turn and every non-request turn source needs its own frontend rescue.  
   _evidence:_ routers/chat.py:49 _recover_streaming_on_disconnect interrupts the CLI on disconnect; core/auto_continue.py re-drives it; frontend busy-poll, B1 drain surface, symmetric re-arm and force-clear each rescue one source (useChatStreamingLifecycle.ts ~:1475, ~:1532, ~:3498)

- **Root layer:** architecture
- **Missed invariant:** A session's event stream is owned by the session and is subscribable: every event of every turn is published once with a per-session sequence number, and clients attach/detach without affecting the turn.
- **Searched:** git log --oneline -S'shouldArmSpinnerFromBackend'; git log --oneline -S'auto_continue'; MEMORY.md Open Threads: Frontend reconcile race (33 prior patches), Streaming looks stuck / backend offline; grep -n 'Root-1 deferred live token re-attach' desktop/src/hooks/useChatStreamingLifecycle.ts; grep -rn 'StreamingResponse(' backend/routers (found tasks.py GET subscribe); git grep -n -E '\.(run_conversation|continue_with_answer|continue_with_cmd_permission|run_injected_turn)\(' -- backend  (all 13 turn entry callers classified)
- **Prior instances:** 5:
  - Frontend reconcile race open thread: 33 patches on reconcile timing/guards
  - B1 drain-aware busy recovery added because drained turns were invisible (useChatStreamingLifecycle.ts ~:3483 comment)
  - B1 drain SURFACE added because drained responses were invisible until reload (~:1475 comment, 'Root-1 DEFERRED live re-attach')
  - symmetric re-arm added for restored tabs whose backend resume-streams (~:1532 comment)
  - core/auto_continue.py added 2026-10-21 because a disconnect ended the turn
- **Siblings (same shape elsewhere):** 7:
  - C1 backend/core/session_router.py:3218 drain_pending — runs a turn whose events no tab receives
  - C2 backend/core/auto_continue.py — runs a continuation turn with no POST stream
  - C3 backend/core/session_router.py:3114 run_injected_turn — runs a CLI-initiated turn with no POST stream
  - C4 backend/core/streaming_orchestrator.py:2436 build_ui_command_event — ui_command delivered only on the POST SSE path
  - C5 backend/routers/tasks.py:212 + backend/core/task_manager.py:415 — the invariant is already met for tasks (GET subscribe: replay buffer + live per-subscriber queue, no sequence numbers or resume-from-seq); the chat channel should reuse/generalise this pattern, not i…
  - C6 backend/channels/gateway.py:1913 run_conversation, :2040 continue_with_answer — channel-initiated (Slack etc.) turns run with no tab POST; a desktop channel tab showing that session sees them only by polling/DB — same shape as C1-C3, covered by publishing at the router sink
  - C7 backend/core/skill_creator.py:123 (routers/skills.py:344 POST SSE), backend/core/task_manager.py:256/:349 (routers/tasks.py:212 GET subscribe) — turns already delivered by their own channel; NOT affected — publishing at the router sink adds a chat-session event they do not consume

## Pre-mortem

- Two delivery paths during the strangler period (POST SSE and the subscription) render the same event twice or out of order; mitigated by one sequence number per session and client-side dedup by seq, with POST SSE reduced to an ack once the channel is live.
- Removing the disconnect interrupt leaves a CLI turn running with no client for a long time, or the stale-pipe problem the interrupt existed for returns; the continuous reader (17b14358) already owns the pipe, which is what makes the interrupt removable — must be proven by a test, not assumed.
- The frontend reconcile hot zone (#1 recurring bug source) regresses while rescues are removed; mitigated by demoting rescues to the safety net first and deleting only after the channel passes E2E smoke on drain, injected, disconnect-reconnect and question paths.
