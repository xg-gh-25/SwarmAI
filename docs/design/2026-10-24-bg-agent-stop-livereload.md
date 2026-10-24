---
title: Background agent liveness, Stop cost and idempotency, one confirmed block per streamed block
date: 2026-10-24
created: 2026-10-24
updated: 2026-10-24
status: accepted (v3: D4 by block identity after the code review)
---

# Background agent liveness, Stop cost and idempotency, one confirmed block per streamed block

Evidence: session `6a9d322a` on 2026-10-24 (backend-daemon.log 13:54-14:00, the CLI
transcript `59e71f61…jsonl` + `subagents/agent-af1d6976bc7aaf0a9.jsonl`, the session's
record read through `GET /api/chat/sessions/{id}/messages?turns=8`).

## Problems (each observed)

| # | What the user saw | Cause (verified) |
|---|---|---|
| 1 | The Agents box said "↗ 1 in background" for 5 min with no change; the user took it as finished and pressed Stop | The background sub-agent's frames never reach `BackgroundAgents`: with no turn open `CliStreamReader` drops them (`background_subagent_frames` reached 100); with another turn open they go into that turn. `BackgroundAgents` only learns start and end. |
| 2 | Stop took ~6 s | `begin_stop_receipt` → `turn_stop.agent_transcript` → `leak_transcript.find_transcript`: a glob over `~/.claude/projects/*/<sid>.jsonl` plus a read of the sub-agent transcript, synchronously ON THE EVENT LOOP (starvation warning, stack in `pathlib._scandir`, loop age 3.2 s, host under build load). |
| 3 | 6 stop requests, 5 identical `turn_stop` receipts in the record | Nothing makes a second Stop a no-op; the user clicked again because nothing happened (#2). |
| 4 | The end of the RESULT showed twice, live only (record: the text once, 481 chars) | A second fresh start (`text_start`) of the block already streaming opened a second provisional block; `updateMessages` confirms one provisional block per confirmed block, so the other (the tail) stayed: `[F, P2]`. Reproduced in a unit probe. The frontend cannot tell a repeated start of the same block from a new block: the stream carries only the index, which every model message reuses. Which delivery made the repeated start in this incident is not proven. |

## Design

### D1. Background agent progress (fixes 1)

- `CliStreamReader`: every frame that carries `parent_tool_use_id` is first offered to a
  new `on_bg_frame(parent_tool_use_id, msg)` callback, whether or not a turn is open;
  delivery is unchanged (no turn: dropped as before; a turn open: delivered as before).
- `SessionUnit._on_reader_bg_frame` → `BackgroundAgents.progress(tool_use_id, msg)`: a
  dict lookup; only a RUNNING registered background agent is touched (any other parent,
  e.g. a foreground Agent of the open turn, is a no-op). It counts the tool-use blocks of
  an assistant message (`toolUses`), keeps the newest tool name (`lastTool`) and
  `lastActivityAt` (ms).
- Publishing: launch and terminal transitions call `on_change` at once (unchanged:
  the terminal publish also persists the final Agent result). Progress calls a separate
  `on_progress`, which the unit coalesces per SESSION: leading edge publishes at once,
  then at most one trailing publish per `PROGRESS_PUBLISH_S = 2.0` (`loop.call_later`);
  a terminal change cancels a pending trailing timer and publishes the terminal snapshot.
  The timer is cancelled when the unit's reader goes away.
- Frontend `BackgroundAgent` gains `lastTool`, `lastActivityAt`. `AgentRow` for a
  background agent that runs shows elapsed since `startedAt` (1 s tick only while it
  runs), `N tools`, and `→ lastTool`.

### D2. Stop does no scan (fixes 2)

The receipt stays built and persisted BEFORE the stop runs, synchronously at entry (the
ordering guarantee and the continuation note are unchanged). What changes is the cost:

- `turn_stop.agent_transcript(sid, task_id, cwd)` builds the path directly:
  `<projects>/<encode(cwd)>/<sid>/subagents/agent-<task>.jsonl`, `encode` = every
  character outside `[A-Za-z0-9]` → `-` (the CLI's project-dir name; verified against
  `-Users-gawan--swarm-ai-SwarmWS`). One `is_file`. No glob. Missing → None (the receipt
  just has no transcript path; it never falls back to a scan on the loop).
- `toolCalls`: read from the file only when it is ≤ 2 MB (was 32 MB); else omitted.

### D3. One Stop at a time (fixes 3)

- Backend: `SessionRouter._stops_in_flight: set[str]`; `interrupt_session` returns
  `{"success": True, "message": "Stop already in progress", "receipt": []}` when the
  session is in it; added before the first await, removed in `finally`.
- Frontend: `handleStop` and `handleStopBackground` test-and-set the tab's
  `_stopInFlight` as their FIRST step (before the abort and the `*Stopped*` marker);
  cleared when the request settles. SkillsPage's stop gets the same local guard.

### D4. A repeated start of the same streamed block continues it (fixes 4)

v2 matched a confirmed block to provisional blocks by text prefix; the code review
rejected it (no identity: `[A, A]` confirmed as `A+A` kept one `A`, and a separate block
whose text happened to continue the first one could be dropped). v3 uses identity:

- Backend (`streaming_orchestrator`): a per-turn counter of the main agent's
  `message_start` events; `text_start` / `text_delta` / `thinking_start` /
  `thinking_delta` carry `block = "<run>.<message>:<index>"`: `<run>` is a random id per
  parser invocation (a retry or continuation renders into the same bubble and restarts
  the count; the second review found the collision), and a sub-agent's message_start does
  not count. The index alone is reused by every message, so it is not an identity.
- Frontend (`applyTextDelta` / `applyThinkingDelta`): a fresh start whose `block` equals
  the last provisional block's `_streamBlock` continues that block instead of opening a
  second one. Any other key, or no key (an older backend), opens a new block as before.
  `updateMessages` is unchanged: one provisional block per confirmed block is then right.
- No text comparison anywhere.
- Not changed: reconcile semantics ("more-complete wins" stays; a DB-wins reconcile was
  considered and rejected: Stop rows, absent-session idle and the 50-row tail make it
  lose content).

## Not done

- Which path delivered the second fresh start in this incident (D4 makes it harmless).
- The 5 duplicate receipts already in the record stay (fix forward).

## Tests (written first, red on main)

- T1 reader offers a background frame to `on_bg_frame` with and without a turn open,
  delivery unchanged; `BackgroundAgents.progress` counts tools, ignores unknown/finished
  ids; the unit's coalescer: leading publish, one trailing per window, terminal cancels
  the trailing timer and publishes.
- T2 `agent_transcript` finds `<encode(cwd)>/<sid>/subagents/agent-<task>.jsonl` without a
  glob (glob patched to fail); a missing file → None.
- T3 two concurrent `interrupt_session` calls write ONE receipt; a later one works.
- T4 a repeated start of the same `block` key keeps one provisional block and its
  confirmation leaves nothing; another key is a new block even when its text continues
  the earlier one; a real repeat stays twice; no key behaves as before. Backend: the
  orchestrator's keys (`1:0`, `1:0` again, then `2:0`; sub-agent message_start not counted).
- T5 `AgentRow` shows tools + current tool + elapsed for a running background agent.
- T6 Stop clicked twice in ChatPage sends one request and appends one marker (source
  guard: the test-and-set is the first statement).
