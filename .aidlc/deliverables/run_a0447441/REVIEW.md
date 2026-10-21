# Review — gates and experts

**Run** `run_a0447441` · **profile** full · **project** SwarmAI

> 第一条绝对不可接受 不然streaming 就失去了意义 而且信息是错乱的 misleading 用户 你系统化的看下 我们到底还有什么类似的问题 一起从根上解决

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | SUPPORTED | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | WARN | GATE 1 VERDICT: WARN; 3. Simplicity:      WARN. The problem is real (evidence cites file:line, 13 callers classified), and the hub and wrapper serve the refactor's goal. The extra piece is the POST fallback render mode wi… |
| REVIEW — spec + conformance | approved · litmus PASS | 13 finding(s) |
| Gate 2 — adversarial experts | full · 39 finding(s), 18 open | Red Team: yes |
| Meta-review | RISKS | 4 risk(s) |
| Push-ready | no | findings: 4 unresolved HIGH / MEDIUM(conf>=7); The Gate-2 fixing spec was never accepted by its skeptic (BLOCK twice; fixed per its amendments without a third submission), the 4 findings the re-check… |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| api-contract | 6 | 2 | 4 | 0 | 2 |
| code-quality | 0 | 0 | 0 | 0 | 0 |
| concurrency | 6 | 2 | 3 | 0 | 3 |
| correctness | 7 | 3 | 3 | 0 | 4 |
| fixspec-skeptic | 0 | 0 | 0 | 0 | 0 |
| gate0-skeptic | 0 | 0 | 0 | 0 | 0 |
| gate1-skeptic | 0 | 0 | 0 | 0 | 0 |
| integration | 3 | 2 | 3 | 0 | 0 |
| meta-review | 4 | 3 | 0 | 0 | 4 |
| red-team | 5 | 2 | 3 | 0 | 2 |
| security | 2 | 0 | 1 | 0 | 1 |
| spec-compliance | 0 | 0 | 0 | 0 | 0 |
| state-machine | 6 | 2 | 4 | 0 | 2 |
| ux-test | 0 | 0 | 0 | 0 | 0 |

_Dispatch:_ Agent tool: 7 specialists (correctness, security, concurrency, state-machine, api-contract, integration, red-team) in one parallel batch; re-check round of 6 specialists in one batch, 12/12 items RESOLVED; meta-review RISKS

## Gate 2 findings

| Expert | Severity | Finding | Status |
|---|---|---|---|
| api-contract | HIGH | GET /events calls json.dumps(env) with no guard. The POST path (sse_with_heartbeat, chat.py:253-257) catches TypeError/ValueError, sends SERIALIZATION_ERROR and carries on, which shows unserialisable events are a known case. If one such env | fixed · inv I5 · class fix: subscribe treats a missing log as empty; publish buffers only encodable envelopes; parse_cursors drops non-finite |
| concurrency | HIGH | Cross-session bleed. While a new tab's first send has no session id, the wiring hands it every session's subscription events (useChatStreamingLifecycle.ts 'st.postPending && !tab.sessionId') and routeSubEvent buffers them. The session filte | fixed · inv I7 · class fix: ownership() in sessionTurnRouter applied first in routeSubEvent and on every drain; the listener hands every event to every tab |
| correctness | HIGH | The `seen` shortcut in watch() gives a newly watched session a cursor taken from the last envelope the window saw, and skips the JOIN. Seeing an envelope does not mean the tab saw that turn's turn_start. routeSubEvent only begins a turn on | OPEN |
| correctness | HIGH | subscribe() raises AttributeError when a cursor is >= 0 for a session the hub has no log for, in the same epoch. With log None, head=0 and oldest=1, so cursor 0 passes the resync check (0>0 and 0<0 are both false) and `for env in list(log.b | fixed · inv I5 · class fix: subscribe treats a missing log as empty; publish buffers only encodable envelopes; parse_cursors drops non-finite |
| integration | HIGH | The client's `cursors` map is never pruned. `dispatch` (line 105) adds a cursor for every session the server broadcasts, and `/events` sends every session's events to every subscriber, channel sessions included. `buildUrl` (line 86) puts th | fixed · inv I1 · class fix: watch(ids) from open tabs; cursors kept and sent only for watched sessions; hello seqs baseline |
| red-team | HIGH | A new tab can show another session's turn. While a new tab's first send has no session yet, the subscription effect (useChatStreamingLifecycle.ts:4771-4773) sends it events from EVERY session, and they are held in st.buffer. The sessionId f | fixed · inv I7 · class fix: ownership() in sessionTurnRouter applied first in routeSubEvent and on every drain; the listener hands every event to every tab |
| state-machine | HIGH | A turn whose POST was lost gets marked complete while the backend is still running it. onPostLost (line 227) sets bound.postDone=true for a POST that dropped, not only for one that ended with [DONE]. If the subscription then drops even brie | fixed · inv I1 · class fix: BoundTurn.postLost; only [DONE] sets postDone; completion without turn_end requires postDone |
| api-contract | MED | A JOIN cursor replays the buffered events with eventSeq >= active_start, but nothing checks that the turn_start is still in the 4000-entry deque. Every text_delta counts as an event, so long turns go past 4000. The rejoining tab then gets m | fixed · inv I1 · class fix: hub keeps the active turn whole; JOIN/resync replay it from turn_start; resync names activeTurnId; router rebuilds (reset) instead of compl… |
| api-contract | MED | The fix changed which sessions keep cursors. A cursor is now kept, and sent on reconnect (buildUrl only reads `cursors`/`joins`), only for sessions added by `watch()`. `watch()` runs only inside the reconcile tick, after `await chatService. | OPEN |
| api-contract | MED | The client and server disagree on the cursor limit. The hub sends every session's envelopes to every subscriber, and dispatch adds a cursor for each sessionId it sees (channel, task, drain and old-tab sessions included). That map is never p | fixed · inv I1 · class fix: watch(ids) from open tabs; cursors kept and sent only for watched sessions; hello seqs baseline |
| concurrency | MED | Race between watch() and the hello. connect() builds the URL when it calls fetch, but connected only turns true on events_hello. If watch() adds a new session while that fetch is in flight, the session goes into joins, needJoin is true, and | OPEN |
| concurrency | MED | The cursor map is never pruned: the window gets every session's events and buildUrl sends a cursor for each one, oldest first. parse_cursors (session_events.py:418) keeps only the first 64 entries, so once a window has seen more than 64 ses | fixed · inv I1 · class fix: watch(ids) from open tabs; cursors kept and sent only for watched sessions; hello seqs baseline |
| correctness | MED | buildUrl sends a cursor for every session that has ever published in this window. Cursors are never pruned, and dispatch stores one for all sessions, including channel, task and skill-creator sessions with no tab. parse_cursors (session_eve | fixed · inv I1 · class fix: watch(ids) from open tabs; cursors kept and sent only for watched sessions; hello seqs baseline |
| correctness | MED | A JOIN can be lost if watch() runs while a connect is in flight. connect() builds the URL when the fetch starts. If watch() adds a new session in the window between the fetch starting and events_hello arriving, it does `joins.add(sid)`, but | OPEN |
| correctness | MED | beginSubscriptionTurn removes every assistant message whose metadata.client_id equals turnClientId. For answer and permission continuations, turnClientId is the original turn's `{cid}-asst` (_continuation_client_id, session_router.py:4671). | OPEN |
| integration | MED | `persisted_frames()` in `run_injected_turn` ignores the return value of `_persist_stream_event`. That function's contract is that it returns events to deliver before this one: the `turn_deliverables` and `turn_changes` blocks built on `resu | fixed · inv I1 · class fix: persisted_frames yields the extras before the event like the other callers |
| meta-review | MED | Per-session buffers of 4000 envelopes plus up to 20000 for the active turn hold large tool_result payloads until release; channel sessions are long-lived; _logs keys are never pruned | OPEN |
| meta-review | MED | Detach plus removed auto-continue/recovery: an orphaned hung turn holds a MAX_CONCURRENT_STREAMS slot indefinitely, since there is no turn timeout and STREAMING is TTL-protected | OPEN |
| meta-review | MED | POST streams stay open while subscribed, plus the window's events stream: this can exceed WebKit's 6-per-host HTTP/1.1 cap and starve /chat/stop and polling | OPEN |
| red-team | MED | The same unfiltered drain breaks the tab's next send. Draining another session's events sets st.lastSeq to that session's eventSeq (line 195) while st.sessionId is still undefined. When the next send creates a session, syncTurnStateSession | fixed · inv I7 · class fix: ownership() in sessionTurnRouter applied first in routeSubEvent and on every drain; the listener hands every event to every tab |
| red-team | MED | A resync stops live rendering of a turn that is still running. On resync, a subscription-started turn (or a POST turn whose POST has dropped) is marked completed. All later live events of the same turnId then return [] (line 187), even thou | fixed · inv I1 · class fix: hub keeps the active turn whole; JOIN/resync replay it from turn_start; resync names activeTurnId; router rebuilds (reset) instead of compl… |
| state-machine | MED | New stuck state: `rebuild` is cleared only when the subscription delivers the replayed turn_start (line 234). While it is set, every POST copy of the turn is dropped even with the subscription down (line 161; the test at :181 asserts this), | OPEN |
| state-machine | MED | The client ignores the per-session seqs in events_hello. A session only gets a cursor once one of its events has been dispatched. A session with no cursor yet (a restored tab whose session has been idle since the window loaded) reconnects w | fixed · inv I1 · class fix: watch(ids) from open tabs; cursors kept and sent only for watched sessions; hello seqs baseline |
| state-machine | MED | A JOIN can miss the turn's turn_start. The JOIN branch replays log.buf from active_start but never checks that the turn_start envelope is still in the deque (maxlen 4000). If a turn has produced more than 4000 events, the replay starts mid- | fixed · inv I1 · class fix: hub keeps the active turn whole; JOIN/resync replay it from turn_start; resync names activeTurnId; router rebuilds (reset) instead of compl… |
| state-machine | MED | Duplicate bubble after a POST turn is rebuilt: `reset` with origin 'post' only drops the 'sub' handlers. The rebuilt turn then goes through beginSubscriptionTurn, which removes only a bubble whose id is turnClientId or whose metadata.client | OPEN |
| state-machine | MED | A resync ends a turn that is still running, and the rest of it is then thrown away. The backend sends resync when the cursor is older than the 4000-envelope buffer, and a long turn with many deltas can easily outgrow it. resync marks a sub- | fixed · inv I1 · class fix: hub keeps the active turn whole; JOIN/resync replay it from turn_start; resync names activeTurnId; router rebuilds (reset) instead of compl… |
| api-contract | LOW | Once a turn passes ACTIVE_TURN_LIMIT envelopes, `_active_replay` sends the first 20000 and then the buffer tail after a gap, with eventSeq still increasing and no marker. The client's router cannot see the gap, so a rejoining tab shows a bu | OPEN |
| api-contract | LOW | For an idle session, a JOIN replays nothing, and the client ignores events_hello.seqs, so a joined idle session never gets a cursor. Every join() made while connected aborts and reopens the stream, and each reconnect sends -1 again. A short | fixed · inv I1 · class fix: watch(ids) from open tabs; cursors kept and sent only for watched sessions; hello seqs baseline |
| concurrency | LOW | Duplicate delivery on the resync branch. `high[sid] = head` is set before the code yields the resync, and the generator stays suspended until the SSE writer pulls again. A publish for the active turn during that pause goes into log.active a | OPEN |
| concurrency | LOW | Injected turns publish turn_end (the _published_turn finally) before run_injected_turn's own finally saves the pending tool-result blocks (_persist_assistant_blocks 'injected-results'). This contradicts the comment at :3115 ('a tab that rel | OPEN |
| concurrency | LOW | JOIN replay assumes active_start is still in the 4000-envelope buffer. A long turn (many deltas) evicts its turn_start, so JOIN replays only the tail without a turn_start. routeSubEvent:195 then advances lastSeq and the cursor while renderi | fixed · inv I1 · class fix: hub keeps the active turn whole; JOIN/resync replay it from turn_start; resync names activeTurnId; router rebuilds (reset) instead of compl… |
| correctness | LOW | Stale comment (RP48). It says drain and channel turns are keyless (client_id=None) and leave the _turn_client_id stash at None. run_conversation now gives them a server client_id (`{source}-{uuid}`), so they do write the stash. The behaviou | fixed |
| correctness | LOW | ownership() now keys on the router record's `st.sessionId` instead of the tab's live `tab.sessionId`, which the removed lifecycle filter used. syncTurnStateSession does not move `st.sessionId` while a bound turn is still open. So if a tab m | OPEN |
| integration | LOW | The new comment says that when an unforced release of a still-active unit is skipped (`skipped_active`), the orphan reaper frees it once idle. Per recorded project knowledge, the orphan reaper only kills unowned OS processes, not SessionUni | fixed |
| meta-review | LOW | Canvas file_changed events are published only from the POST chat_stream path, so non-POST turns lack them | OPEN |
| red-team | LOW | A new tab (no session yet) whose POST is lost before it binds a turnId now always gets verdict 'fallback'. onPostLost sets postPending=false before drainBuffer, so the tab's own buffered turn_start becomes 'foreign' and is dropped; the comm | OPEN |
| red-team | LOW | Candidate events are pushed into st.buffer with no cap. A new tab whose first send waits up to 120s in _await_streaming_slot while 3 other sessions stream holds every text_delta of all of them. The cost is bounded by time, not by count, whi | OPEN |
| security | LOW | The replay buffer is limited by event count (4000 per session), not by size. Envelopes hold whole events, including large tool_result / assistant content. A buffer is only cleared by drop_session on release, TTL or purge, and the _logs entr | OPEN |
| security | LOW | parse_cursors does not handle out-of-range numbers. Python's json.loads accepts the non-standard literals Infinity and NaN, and also turns large literals like 1e400 into float inf. Then int(val) raises OverflowError (for inf) or ValueError | fixed · inv I5 · class fix: subscribe treats a missing log as empty; publish buffers only encodable envelopes; parse_cursors drops non-finite |

### Fixed by class

- **api-contract · HIGH** — GET /events calls json.dumps(env) with no guard. The POST path (sse_with_heartbeat, chat.py:253-257) catches TypeError/ValueError, sends SERIALIZATION_ERROR and carries on, which shows unserialisable events are a known case. If one such env
  - invariant: I5
  - root cause: subscribe/stream assumed well-formed state
  - class fix: subscribe treats a missing log as empty; publish buffers only encodable envelopes; parse_cursors drops non-finite
  - break test: backend/tests/test_session_events.py::test_an_unencodable_event_is_buffered_as_an_error_envelope
- **concurrency · HIGH** — Cross-session bleed. While a new tab's first send has no session id, the wiring hands it every session's subscription events (useChatStreamingLifecycle.ts 'st.postPending && !tab.sessionId') and routeSubEvent buffers them. The session filte
  - invariant: I7
  - root cause: a sessionless tab's candidate events were drained on non-bind paths and counted as its own
  - class fix: ownership() in sessionTurnRouter applied first in routeSubEvent and on every drain; the listener hands every event to every tab
  - break test: desktop/src/hooks/__tests__/sessionTurnRouter.test.ts::a_pre-turn_reply_never_begins_or_counts_another_session_s_buffered_turn
- **correctness · HIGH** — subscribe() raises AttributeError when a cursor is >= 0 for a session the hub has no log for, in the same epoch. With log None, head=0 and oldest=1, so cursor 0 passes the resync check (0>0 and 0<0 are both false) and `for env in list(log.b
  - invariant: I5
  - root cause: subscribe/stream assumed well-formed state
  - class fix: subscribe treats a missing log as empty; publish buffers only encodable envelopes; parse_cursors drops non-finite
  - break test: backend/tests/test_session_events.py::test_cursor_for_an_unknown_session_does_not_break_the_stream
- **integration · HIGH** — The client's `cursors` map is never pruned. `dispatch` (line 105) adds a cursor for every session the server broadcasts, and `/events` sends every session's events to every subscriber, channel sessions included. `buildUrl` (line 86) puts th
  - invariant: I1
  - root cause: cursors for every session ever seen; server keeps 64
  - class fix: watch(ids) from open tabs; cursors kept and sent only for watched sessions; hello seqs baseline
  - break test: desktop/src/services/__tests__/sessionEvents.test.ts::reconnect_sends_cursors_only_for_watched_sessions_with_a_hello_baseline
- **red-team · HIGH** — A new tab can show another session's turn. While a new tab's first send has no session yet, the subscription effect (useChatStreamingLifecycle.ts:4771-4773) sends it events from EVERY session, and they are held in st.buffer. The sessionId f
  - invariant: I7
  - root cause: a sessionless tab's candidate events were drained on non-bind paths and counted as its own
  - class fix: ownership() in sessionTurnRouter applied first in routeSubEvent and on every drain; the listener hands every event to every tab
  - break test: desktop/src/hooks/__tests__/sessionTurnRouter.test.ts::a_pre-turn_reply_never_begins_or_counts_another_session_s_buffered_turn
- **state-machine · HIGH** — A turn whose POST was lost gets marked complete while the backend is still running it. onPostLost (line 227) sets bound.postDone=true for a POST that dropped, not only for one that ended with [DONE]. If the subscription then drops even brie
  - invariant: I1
  - root cause: postDone meant both [DONE] and lost
  - class fix: BoundTurn.postLost; only [DONE] sets postDone; completion without turn_end requires postDone
  - break test: desktop/src/hooks/__tests__/sessionTurnRouter.test.ts::a_lost_POST_and_a_subscription_blip_do_not_complete_a_running_turn_K2
- **api-contract · MED** — A JOIN cursor replays the buffered events with eventSeq >= active_start, but nothing checks that the turn_start is still in the 4000-entry deque. Every text_delta counts as an event, so long turns go past 4000. The rejoining tab then gets m
  - invariant: I1
  - root cause: the 4000-envelope ring is shorter than a long turn; resync ended a running turn
  - class fix: hub keeps the active turn whole; JOIN/resync replay it from turn_start; resync names activeTurnId; router rebuilds (reset) instead of completing it
  - break test: backend/tests/test_session_events.py::test_join_after_the_turn_start_left_the_buffer_still_starts_the_turn
- **api-contract · MED** — The client and server disagree on the cursor limit. The hub sends every session's envelopes to every subscriber, and dispatch adds a cursor for each sessionId it sees (channel, task, drain and old-tab sessions included). That map is never p
  - invariant: I1
  - root cause: cursors for every session ever seen; server keeps 64
  - class fix: watch(ids) from open tabs; cursors kept and sent only for watched sessions; hello seqs baseline
  - break test: desktop/src/services/__tests__/sessionEvents.test.ts::reconnect_sends_cursors_only_for_watched_sessions_with_a_hello_baseline
- **concurrency · MED** — The cursor map is never pruned: the window gets every session's events and buildUrl sends a cursor for each one, oldest first. parse_cursors (session_events.py:418) keeps only the first 64 entries, so once a window has seen more than 64 ses
  - invariant: I1
  - root cause: cursors for every session ever seen; server keeps 64
  - class fix: watch(ids) from open tabs; cursors kept and sent only for watched sessions; hello seqs baseline
  - break test: desktop/src/services/__tests__/sessionEvents.test.ts::reconnect_sends_cursors_only_for_watched_sessions_with_a_hello_baseline
- **correctness · MED** — buildUrl sends a cursor for every session that has ever published in this window. Cursors are never pruned, and dispatch stores one for all sessions, including channel, task and skill-creator sessions with no tab. parse_cursors (session_eve
  - invariant: I1
  - root cause: cursors for every session ever seen; server keeps 64
  - class fix: watch(ids) from open tabs; cursors kept and sent only for watched sessions; hello seqs baseline
  - break test: desktop/src/services/__tests__/sessionEvents.test.ts::reconnect_sends_cursors_only_for_watched_sessions_with_a_hello_baseline
- **integration · MED** — `persisted_frames()` in `run_injected_turn` ignores the return value of `_persist_stream_event`. That function's contract is that it returns events to deliver before this one: the `turn_deliverables` and `turn_changes` blocks built on `resu
  - invariant: I1
  - root cause: persisted_frames dropped _persist_stream_event's extras
  - class fix: persisted_frames yields the extras before the event like the other callers
  - break test: backend/tests/test_session_events_publish.py::test_injected_turn_publishes_its_deliverables
- **red-team · MED** — The same unfiltered drain breaks the tab's next send. Draining another session's events sets st.lastSeq to that session's eventSeq (line 195) while st.sessionId is still undefined. When the next send creates a session, syncTurnStateSession
  - invariant: I7
  - root cause: a sessionless tab's candidate events were drained on non-bind paths and counted as its own
  - class fix: ownership() in sessionTurnRouter applied first in routeSubEvent and on every drain; the listener hands every event to every tab
  - break test: desktop/src/hooks/__tests__/sessionTurnRouter.test.ts::a_pre-turn_reply_never_begins_or_counts_another_session_s_buffered_turn
- **red-team · MED** — A resync stops live rendering of a turn that is still running. On resync, a subscription-started turn (or a POST turn whose POST has dropped) is marked completed. All later live events of the same turnId then return [] (line 187), even thou
  - invariant: I1
  - root cause: the 4000-envelope ring is shorter than a long turn; resync ended a running turn
  - class fix: hub keeps the active turn whole; JOIN/resync replay it from turn_start; resync names activeTurnId; router rebuilds (reset) instead of completing it
  - break test: desktop/src/hooks/__tests__/sessionTurnRouter.test.ts::resync_mid-turn_then_the_replay_the_running_turn_is_rebuilt_one_completion_one_copy_K3
- **state-machine · MED** — The client ignores the per-session seqs in events_hello. A session only gets a cursor once one of its events has been dispatched. A session with no cursor yet (a restored tab whose session has been idle since the window loaded) reconnects w
  - invariant: I1
  - root cause: cursors for every session ever seen; server keeps 64
  - class fix: watch(ids) from open tabs; cursors kept and sent only for watched sessions; hello seqs baseline
  - break test: desktop/src/services/__tests__/sessionEvents.test.ts::reconnects_after_the_stream_ends_with_cursors_joins_and_the_epoch
- **state-machine · MED** — A JOIN can miss the turn's turn_start. The JOIN branch replays log.buf from active_start but never checks that the turn_start envelope is still in the deque (maxlen 4000). If a turn has produced more than 4000 events, the replay starts mid-
  - invariant: I1
  - root cause: the 4000-envelope ring is shorter than a long turn; resync ended a running turn
  - class fix: hub keeps the active turn whole; JOIN/resync replay it from turn_start; resync names activeTurnId; router rebuilds (reset) instead of completing it
  - break test: backend/tests/test_session_events.py::test_join_after_the_turn_start_left_the_buffer_still_starts_the_turn
- **state-machine · MED** — A resync ends a turn that is still running, and the rest of it is then thrown away. The backend sends resync when the cursor is older than the 4000-envelope buffer, and a long turn with many deltas can easily outgrow it. resync marks a sub-
  - invariant: I1
  - root cause: the 4000-envelope ring is shorter than a long turn; resync ended a running turn
  - class fix: hub keeps the active turn whole; JOIN/resync replay it from turn_start; resync names activeTurnId; router rebuilds (reset) instead of completing it
  - break test: desktop/src/hooks/__tests__/sessionTurnRouter.test.ts::resync_mid-turn_then_the_replay_the_running_turn_is_rebuilt_one_completion_one_copy_K3
- **api-contract · LOW** — For an idle session, a JOIN replays nothing, and the client ignores events_hello.seqs, so a joined idle session never gets a cursor. Every join() made while connected aborts and reopens the stream, and each reconnect sends -1 again. A short
  - invariant: I1
  - root cause: cursors for every session ever seen; server keeps 64
  - class fix: watch(ids) from open tabs; cursors kept and sent only for watched sessions; hello seqs baseline
  - break test: desktop/src/services/__tests__/sessionEvents.test.ts::reconnects_after_the_stream_ends_with_cursors_joins_and_the_epoch
- **concurrency · LOW** — JOIN replay assumes active_start is still in the 4000-envelope buffer. A long turn (many deltas) evicts its turn_start, so JOIN replays only the tail without a turn_start. routeSubEvent:195 then advances lastSeq and the cursor while renderi
  - invariant: I1
  - root cause: the 4000-envelope ring is shorter than a long turn; resync ended a running turn
  - class fix: hub keeps the active turn whole; JOIN/resync replay it from turn_start; resync names activeTurnId; router rebuilds (reset) instead of completing it
  - break test: backend/tests/test_session_events.py::test_join_after_the_turn_start_left_the_buffer_still_starts_the_turn
- **security · LOW** — parse_cursors does not handle out-of-range numbers. Python's json.loads accepts the non-standard literals Infinity and NaN, and also turns large literals like 1e400 into float inf. Then int(val) raises OverflowError (for inf) or ValueError
  - invariant: I5
  - root cause: subscribe/stream assumed well-formed state
  - class fix: subscribe treats a missing log as empty; publish buffers only encodable envelopes; parse_cursors drops non-finite
  - break test: backend/tests/test_session_events.py::test_parse_cursors_bounds_untrusted_input

## REVIEW findings

| Expert | Severity | Finding | Status |
|---|---|---|---|
| spec-compliance+code-quality+ux-test | CRITICAL | desktop/src/hooks/useChatStreamingLifecycle.ts subscription listener + sessionTurnRouter.routePostEvent: the first send of a new tab rendered nothing while the subscription was connected (POST copies suppressed, subscription events routed only by tab.sessionId, which that turn's own session_start sets). Fixed: the listener also routes by the bound turnId and, while the first send has no session, … | fixed · inv I3 · class fix: subscription events reach a tab by session OR bound turn OR pending first send (one predicate in the listener); routePostEvent filters buff… |
| code-quality | HIGH | Per-tab route state (lastSeq/bound/buffer) was not re-baselined when a tab moved to another session (eventSeq is per session). Fixed: syncTurnStateSession resets it on session change once no turn is open; turnStateOf applies it and drops subscription handlers. | fixed · inv I3 · class fix: TabTurnState.sessionId + syncTurnStateSession on every access (turnStateOf) |
| spec-compliance+code-quality | HIGH | Subscription loss: (a) a subscription that stalled before dropping left POST-suppressed events unrendered (seq gate skipped the gap); (b) a POST turn whose [DONE] waited for turn_end stayed open after a drop/resync. Fixed: suppressed POST copies kept as a shadow and rendered by onSubDisconnected (wired to onConnectionChange); resync completes a POST turn whose POST is done. | fixed · inv I6 · class fix: shadow buffer per bound POST turn + onSubDisconnected flush + resync completion rule |
| code-quality | HIGH | backend/core/session_events.py: replay buffers of released sessions were never freed (drop_session had no caller). Fixed: LifecycleManager._release_session_state frees it; drop_session keeps the seq counter so a tab's cursor never sees a restarted sequence as duplicates. | fixed · inv I1 · class fix: the single per-session release path (_release_session_state) frees the hub buffer with the other per-session state |
| spec-compliance | HIGH | AC6: busy-poll was kept as a fallback and no rescue checked the routed turn. Fixed: busy-poll loop and its tab fields deleted; the B1 drain surface skips while a routed turn is open and clears waiting-for-busy when nothing is pending; a drained subscription turn clears it at its end. | fixed · inv I6 · class fix: the one remaining DB-delivery rescue (B1 drain surface) consults _turnRoute.bound; the poll that duplicated it is gone |
| code-quality | MEDIUM | RP48 stale comments describing the deleted disconnect recovery (chat.py streaming-state docstring + inline, session_router.py, session_unit.py, the busy branch). Fixed. | fixed · inv I2 · class fix: every comment naming _recover_streaming_on_disconnect / post-disconnect flushing / busy poll was grepped and rewritten (chat.py, session_ro… |
| ux-test | MEDIUM | Missing frontend tests: AC4 (question via the subscription, kept after turn_end), resync → DB reload, POST stall error → detach. Added in sessionTurnWiring.test.ts. | fixed · inv I6 · class fix: sessionTurnWiring.test.ts now covers each subscription-path event class: new-session binding (both orders), questions, resync, transport er… |
| code-quality | MEDIUM | SessionUnit disconnect cluster (recover_from_disconnect, schedule_pipe_flush, flush_subprocess_pipe, is_post_disconnect_flushing and its readers incl. /streaming-state post_disconnect_flushing and frontend postDisconnectUncertain) is dead now. Marked DEAD in its docstrings; removal (about 200 references in 26 files) is a pure-deletion follow-up. | OPEN |
| code-quality | MEDIUM | A detached turn whose CLI hangs is no longer unstuck by the client disconnect; it stays STREAMING until the user presses Stop (the tab still shows it via the subscription). No timeout added (STEERING #2: a hang-guard needs a real hang, and the disconnect was never a hang detector). Recorded as a known issue. | OPEN |
| ux-test | LOW | Watcher file_changed events (surface_injection) are only produced for POST turns; drained/injected/channel turns still do not surface sub-agent file writes live in Canvas. Known issue. | OPEN |
| spec-compliance | LOW | State outside a turn generator (post-turn compaction, eviction/cold transitions) still reaches the UI by polling only; the hub carries turn events. Known issue for a follow-up. | OPEN |
| code-quality | LOW | turn_start.source was an untyped string across the boundary. Fixed: TurnSource union (frontend) mirrored by session_events.TURN_SOURCES. | fixed |
| code-quality | LOW | useChatStreamingLifecycle.ts grows by about 260 lines; the routing glue could move to its own hook. Left as is (heuristic; would split the hot file mid-change). | OPEN |

## Meta-review risks

| Severity | Risk | Status |
|---|---|---|
| MED | POST streams stay open while subscribed, plus the window's events stream: several streaming/queued tabs can reach WebKit's 6-per-host HTTP/1.1 cap and starve /chat/stop and polling | OPEN · abort the POST reader once the send is bound to its turnId while the subscription is connected |
| MED | With detach and auto-continue removed, an orphaned hung turn (tab closed) holds a streaming slot until Stop | OPEN · needs a decision against STEERING #2 (timeout only for a true hang); candidate: interrupt a detached turn no subscriber watches after a grace period |
| MED | Replay buffers are bounded by count (4000 + 20000 active), not bytes; large tool_result payloads stay until session release; _logs keys never pruned | OPEN · byte cap, truncated tool_result copies, prune empty logs |
| LOW | Canvas file_changed events are published only on the POST chat_stream path | OPEN · drain the injection queue inside _published_turn |

## Open items

- [runner] Not tested in this run (regression): 478 test file(s) reach a changed module only through other modules and were not run: backend/tests/test_active_run_commit_gate.py, backend/tests/test_admission_backfill.py, backend/tests/test_admission_band.py, backend/tests/test_admission_calibration.py, backend/tests/test_admission_is_noise.py, backend/tests/test_admission_rootfix.py, backend/tests/test_admission_routing.py, backend/tests/test_admission_trust_field.py, backend/tests/test_admission_trust_stamp.py, backend/tests/test_advance_continuation.py, backend/tests/test_adversarial_commit_g…
- [degraded] deliver completed with 18 open gate error(s): Fix by class: adversarial_review.findings[1] (concurrency, HIGH) names no invariant of the design (got 'I7'). Name the I<n> it violated, or add the missed invar; Fix by class: adversarial_review.findings[5] (red-team, HIGH) names no invariant of the design (got 'I7'). Name the I<n> it violated, or add the missed invarian; Fix by class: adversarial_review.findings[19] (red-team, MED) names no invariant of the design (got 'I7'). Name the I<n> it violated, or add the missed invarian (+15)
- Re-check round raised 4 findings, fixed after the last round and not re-reviewed: HIGH a newly watched session seen earlier skipped its JOIN (now always joined); MED a session watched while a connect was in flight missed its join (sentJoins + rejoin after hello); MED a rebuild could stay stuck when its turn ended before the replay (resync completes it; rebuild honoured only while connected); MED an answer/permission continuation removed the question turn's bubble (continuations get their own bubble id)
- Known issue: the SessionUnit disconnect cluster is dead code (follow-up removal)
- Known issue: a detached hung turn is ended only by Stop; a closed tab has no Stop (meta-review MED)
- Known issue: POST readers stay open while subscribed, adding connection-cap pressure (meta-review MED)
- Known issue: replay buffers bounded by count, not bytes; candidate buffer uncapped; turns over ACTIVE_TURN_LIMIT replay with a gap
- Known issue: surface_injection / Canvas file_changed only on POST turns; non-turn state (compaction, eviction) still polled
- Known issue: Slack/channel sessions do not forward CLI-injected turns (out of scope)
- X8 pending: live E2E smoke after XG rebuilds and restarts
- Fixed in scope (made this run's landing red): backend/tests/conftest.py re-rooted every module Path constant under ~/.swarm-ai, including source files of a pipeline worktree (~/.swarm-ai/worktrees/...), so test_deploy_hardening's install() lost its plist TEMPLATE; the guard now skips the source tree. Not reviewed by Gate-2 (found by the regression run)
