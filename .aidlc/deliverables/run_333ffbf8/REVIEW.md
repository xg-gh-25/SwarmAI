# Review — gates and experts

**Run** `run_333ffbf8` · **profile** ? · **project** SwarmAI

> 为什么关上 我们要解决真实的问题

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | SUPPORTED | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | — | plan before BUILD |
| REVIEW — spec + conformance | approved · litmus PASS | 14 finding(s) |
| Gate 2 — adversarial experts | full · 24 finding(s), 11 open | Red Team: yes |
| Meta-review | RISKS_IDENTIFIED | 5 risk(s) |
| Push-ready | no | findings: 3 unresolved HIGH / MEDIUM(conf>=7); test evidence predates later edits (['backend/core/retry_manager.py']); meta_review carries a HIGH; exit criteria unmet: ['X1', 'X2', 'X3']; tests: late… |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| code-quality | 0 | 0 | 0 | 0 | 0 |
| concurrency | 5 | 1 | 4 | 0 | 1 |
| correctness | 3 | 1 | 2 | 0 | 1 |
| fixspec-skeptic | 0 | 0 | 0 | 0 | 0 |
| gate0-skeptic | 0 | 0 | 0 | 0 | 0 |
| gate1-skeptic | 0 | 0 | 0 | 0 | 0 |
| integration | 2 | 0 | 0 | 0 | 2 |
| meta-review | 5 | 3 | 0 | 0 | 5 |
| red-team | 3 | 1 | 2 | 0 | 1 |
| root-cause | 0 | 0 | 0 | 0 | 0 |
| security | 2 | 0 | 2 | 0 | 0 |
| spec-compliance | 0 | 0 | 0 | 0 | 0 |
| state-machine | 4 | 1 | 3 | 0 | 1 |

_Dispatch:_ Agent tool: 6 specialists (correctness, security, integration, red-team, concurrency, state-machine) in one parallel batch; fixing spec through 3 skeptic rounds and one root-cause review (SOUND)

## Gate 2 findings

| Expert | Severity | Finding | Status |
|---|---|---|---|
| concurrency | HIGH | A user turn that ends in TurnAbandoned goes to send()'s generic `except Exception`. That handler crashes the unit with `_crash_to_cold_async(clear_identity=True)`, which deletes the --resume id. I checked `_is_retriable_error` against all f | fixed · inv I4 · class fix: SessionUnit._end_abandoned_turn, called from send() and _retry_with_resume: teardown ends quietly, MessageNotConsumed -> SessionBusyError (… |
| correctness | HIGH | If the reader dies while the unit is IDLE, the session is stuck: no user message gets delivered and nothing recovers it. Any of these sets `self.error` permanently: the CLI exits between turns (stream ends, line 219), a stream or transport | fixed · inv I6 · class fix: CliStreamReader.dead is quiescent with no background work; send() at IDLE with a dead reader respawns via _crash_to_cold_async(clear_identi… |
| meta-review | HIGH | Dev/test SDK 0.2.105 (mise) vs pinned 0.2.168; replay-user-messages / session_state events are forced on every spawn with no capability check | OPEN |
| red-team | HIGH | A dead stream reader looks like a busy CLI, not a broken one, and nothing recovers from it. `quiescent` returns False whenever `self.error` is set, and the error stays set for the life of the client. If the reader dies between turns (CLI ex | fixed · inv I6 · class fix: CliStreamReader.dead is quiescent with no background work; send() at IDLE with a dead reader respawns via _crash_to_cold_async(clear_identi… |
| state-machine | HIGH | An IDLE unit whose reader has failed gets stuck, and nothing recovers it. If the CLI exits or crashes while the unit is IDLE, CliStreamReader._run sets reader.error, and `quiescent` then returns False for good (`self.error is None` is its f | fixed · inv I6 · class fix: CliStreamReader.dead is quiescent with no background work; send() at IDLE with a dead reader respawns via _crash_to_cold_async(clear_identi… |
| concurrency | MED | `_notif_count` is only brought back level with `_notif_at_idle` by a later idle report. Suppose a terminal `task_updated` or a `task_notification` arrives while the CLI is idle and the CLI never starts a turn for it, e.g. a background task | OPEN |
| concurrency | MED | Notification race where the CLI does not fold the message. A user claim misses `init` because its mark differs from the count, so it is still waiting while the CLI's own turn runs and sets `_running_seen`. The CLI emits idle between queued | fixed |
| concurrency | MED | The quiescence check runs at session_unit.py:2659, but the claim's `_notif_mark` is only taken later, in `write_user_message`. Between the two, send() suspends at `yield turn_phase_event(TURN_PHASE_WAITING)` (session_unit.py:2764) until the | fixed |
| correctness | MED | The `_bg` set can keep a task id forever. Ids are added on any `task_started`, including foreground in-turn subagents (fixture agent_in_turn.jsonl). They are removed only on `task_notification` or a terminal `task_updated`. If a task's end | fixed |
| integration | MED | A CLI-initiated turn can only reach the UI through the old reconcile loop, and that loop polls /sessions/streaming-state every 15s when no tab is streaming (nextReconcileDelay). It turns the spinner on (shouldArmSpinnerFromBackend) only if | OPEN |
| meta-review | MED | UNCHECKED: whether SessionBusyError raised in the retry_manager abandoned path reaches the router's pending store; budget exhausted | OPEN |
| meta-review | MED | A never-ending or unterminated background task pins has_background_work, exempting the unit from TTL and RSS proactive restart indefinitely | OPEN |
| red-team | MED | `has_background_work` has no time limit. `_bg` gains an id on every `task_started` and loses it only on `task_notification` or a terminal `task_updated`. A background command that never ends (a dev server or a watch started with run_in_back | fixed |
| security | MED | Nothing bounds the exemption from routine cleanup. has_background_work becomes true on any task_started. It becomes false only on task_notification or a terminal task_updated (cli_stream_reader.py:316-326). interrupt() and _reset_turn_state | fixed |
| security | MED | If the reader dies while the CLI process is still alive, the session is stuck for good. receive_messages() runs parse_message, which raises MessageParseError on a malformed frame. That error ends _run() permanently and sets self.error. Noth | fixed · inv I6 · class fix: CliStreamReader.dead is quiescent with no background work; send() at IDLE with a dead reader respawns via _crash_to_cold_async(clear_identi… |
| state-machine | MED | A TurnAbandoned in a user turn takes the non-retriable path, which kills the CLI and clears the resume identity. The reader ends a user turn with TurnAbandoned in three cases: an idle arrives with the turn still open, an idle arrives after | fixed · inv I4 · class fix: SessionUnit._end_abandoned_turn, called from send() and _retry_with_resume: teardown ends quietly, MessageNotConsumed -> SessionBusyError (… |
| state-machine | MED | There is no timeout on the non-quiescent state. When a task_notification or terminal task_updated arrives, `_notif_count` goes up, and only a later `session_state_changed: idle` brings quiescence back. If the CLI does not start a turn for t | OPEN |
| concurrency | LOW | Task ids in `_bg` are only removed by a terminal frame for the same task_id. If a turn is interrupted, or the CLI drops a task without a terminal frame, the id stays for the whole life of the reader. `has_background_work` then stays True, a | fixed |
| correctness | LOW | `run_injected_turn` moves the unit IDLE to STREAMING (`begin_injected_turn`) without calling `_await_streaming_slot()`. A CLI-initiated turn therefore skips the MAX_CONCURRENT_STREAMS=3 peak-OOM cap, which every new user turn waits on. Seve | OPEN |
| integration | LOW | run_injected_turn only writes to the DB through _persist_stream_event. Nothing passes the turn's events to a channel adapter. On a channel session (is_channel_session, Slack), the CLI's reply to a finished background task is stored but neve | OPEN |
| meta-review | LOW | _injected_tasks entries are never removed on unit teardown | OPEN |
| meta-review | LOW | Frontend has no handling for label 'injected' or 'injected-*' client_ids | OPEN |
| red-team | LOW | The claim check only catches task notifications that arrive after the claim. `_open_turn` gives the turn to the claim when `claim._notif_mark == self._notif_count`, and the mark is taken when the claim is made. A leak continuation is writte | OPEN |
| state-machine | LOW | An injected turn read from its buffer is measured from the wrong start time. begin_injected_turn sets the STREAMING entry time when the sink starts, not when the CLI turn started. If the turn was queued while the unit was busy, all its fram | fixed |

### Fixed by class

- **concurrency · HIGH** — A user turn that ends in TurnAbandoned goes to send()'s generic `except Exception`. That handler crashes the unit with `_crash_to_cold_async(clear_identity=True)`, which deletes the --resume id. I checked `_is_retriable_error` against all f
  - invariant: I4
  - root cause: TurnAbandoned reached send()'s non-retriable crash (clear_identity=True)
  - class fix: SessionUnit._end_abandoned_turn, called from send() and _retry_with_resume: teardown ends quietly, MessageNotConsumed -> SessionBusyError (pending), a cut turn -> IDLE + TURN_ABANDONED, identity kept
  - break test: backend/tests/test_session_injected_turn.py::test_an_abandoned_user_turn_keeps_the_session_identity
- **correctness · HIGH** — If the reader dies while the unit is IDLE, the session is stuck: no user message gets delivered and nothing recovers it. Any of these sets `self.error` permanently: the CLI exits between turns (stream ends, line 219), a stream or transport
  - invariant: I6
  - root cause: quiescence folded 'the reader failed' into 'not quiescent' and nothing consumed reader.error
  - class fix: CliStreamReader.dead is quiescent with no background work; send() at IDLE with a dead reader respawns via _crash_to_cold_async(clear_identity=False); on_error re-drives the sink and the drain; compact refuses
  - break test: backend/tests/test_session_injected_turn.py::test_a_dead_reader_at_idle_is_respawned_not_reported_busy
- **red-team · HIGH** — A dead stream reader looks like a busy CLI, not a broken one, and nothing recovers from it. `quiescent` returns False whenever `self.error` is set, and the error stays set for the life of the client. If the reader dies between turns (CLI ex
  - invariant: I6
  - root cause: quiescence folded 'the reader failed' into 'not quiescent' and nothing consumed reader.error
  - class fix: CliStreamReader.dead is quiescent with no background work; send() at IDLE with a dead reader respawns via _crash_to_cold_async(clear_identity=False); on_error re-drives the sink and the drain; compact refuses
  - break test: backend/tests/test_session_injected_turn.py::test_a_dead_reader_at_idle_is_respawned_not_reported_busy
- **state-machine · HIGH** — An IDLE unit whose reader has failed gets stuck, and nothing recovers it. If the CLI exits or crashes while the unit is IDLE, CliStreamReader._run sets reader.error, and `quiescent` then returns False for good (`self.error is None` is its f
  - invariant: I6
  - root cause: quiescence folded 'the reader failed' into 'not quiescent' and nothing consumed reader.error
  - class fix: CliStreamReader.dead is quiescent with no background work; send() at IDLE with a dead reader respawns via _crash_to_cold_async(clear_identity=False); on_error re-drives the sink and the drain; compact refuses
  - break test: backend/tests/test_session_injected_turn.py::test_a_dead_reader_at_idle_is_respawned_not_reported_busy
- **security · MED** — If the reader dies while the CLI process is still alive, the session is stuck for good. receive_messages() runs parse_message, which raises MessageParseError on a malformed frame. That error ends _run() permanently and sets self.error. Noth
  - invariant: I6
  - root cause: quiescence folded 'the reader failed' into 'not quiescent' and nothing consumed reader.error
  - class fix: CliStreamReader.dead is quiescent with no background work; send() at IDLE with a dead reader respawns via _crash_to_cold_async(clear_identity=False); on_error re-drives the sink and the drain; compact refuses
  - break test: backend/tests/test_session_injected_turn.py::test_a_dead_reader_at_idle_is_respawned_not_reported_busy
- **state-machine · MED** — A TurnAbandoned in a user turn takes the non-retriable path, which kills the CLI and clears the resume identity. The reader ends a user turn with TurnAbandoned in three cases: an idle arrives with the turn still open, an idle arrives after
  - invariant: I4
  - root cause: TurnAbandoned reached send()'s non-retriable crash (clear_identity=True)
  - class fix: SessionUnit._end_abandoned_turn, called from send() and _retry_with_resume: teardown ends quietly, MessageNotConsumed -> SessionBusyError (pending), a cut turn -> IDLE + TURN_ABANDONED, identity kept
  - break test: backend/tests/test_session_injected_turn.py::test_an_abandoned_user_turn_keeps_the_session_identity

## REVIEW findings

| Expert | Severity | Finding | Status |
|---|---|---|---|
| spec-compliance+code-quality | HIGH | cli_stream_reader._route: on session_state idle, waiting claims whose message the CLI never took stayed open, so a send() hung. Fixed: _abandon_unconsumed_claims ends them with TurnAbandoned. | fixed · inv I4 · class fix: every waiting claim records whether the CLI went running after it was registered; an idle abandons exactly those (CliStreamReader._abandon_… |
| code-quality | HIGH | cli_stream_reader._route: a ResultMessage with no open turn opened one, so a stray result (interrupt on an idle CLI) became a claimant's first frame and ended the user turn at once. Fixed: a stray result is logged and dropped. | fixed · inv I2 · class fix: a ResultMessage never opens a turn (CliStreamReader._route) |
| code-quality | HIGH | session_unit.send(): the sink can switch the unit to STREAMING during send()'s waits; the post-wait re-check then yielded a 'send again' error and the message was never stored as pending. Fixed: STREAMING/WAITING_INPUT at the re-check raises SessionBusyError (router stores it pending). | fixed · inv I3 · class fix: the re-check separates busy (raise SessionBusyError) from refreshed/killed (yield + abort) for any source of STREAMING |
| spec-compliance | MEDIUM | session_unit.cli_quiescent / has_background_work: a stream transferred to a user claim stayed in _injected_turns and held the CLI busy until the next idle report, so a pending message could wait. Fixed: _queued_injected drops transferred streams. | fixed · inv I3 · class fix: both predicates read _queued_injected, which only counts streams still owed to the sink |
| code-quality | LOW | session_unit._start_stream_reader overwrote an existing reader without closing it (RP11). Fixed: the old reader is closed first. | fixed |
| code-quality | LOW | StreamingOrchestrator.write_user_message was a pass-through and the set-_turn_stream step was duplicated at two call sites. Fixed: one module function that also sets unit._turn_stream. | fixed |
| code-quality | LOW | RP48: moved _reset_turn_state comments and the _cleanup_internal retry-counter comment named send() as the only admission point. Fixed. | fixed |
| spec-compliance | LOW | test_stream_is_consumed_with_no_turn_in_progress failed once under load (_settle spun a fixed 200 ticks). Fixed: _settle waits on wall time until the fed queue is empty. | fixed |
| spec-compliance | LOW | The leak continuation writes before the CLI's idle, which read as a breach of I3. Resolved by scope: I3 applies to turn-starting writes (send, compact); write_user_message documents it. | fixed |
| code-quality | LOW | RP41: has_background_work relies on a terminal task frame per task_id; a missing one keeps a unit off routine reclaim until memory pressure. Left: no observed case, and a timeout would be a recovery mechanism, not a fix; recorded as a known risk. | OPEN |
| code-quality | LOW | RP89: in a rare race an injected turn's rows can be persisted after a user turn the CLI ran later. Left: low frequency, recorded. | OPEN |
| code-quality | LOW | SessionRouter.run_injected_turn reaches into unit internals (Feature Envy) and enqueue_injected checks for a real deque. Left: moving the sink into SessionUnit belongs with the follow-up live-SSE run. | OPEN |
| spec-compliance | LOW | AC6 exception: Tier-1 memory-pressure survival may still kill a unit with background work (by design, recorded in design_updates). | OPEN |
| code-quality | LOW | A follow-up sent in the milliseconds between a turn's result and the CLI's idle frame now goes through the pending queue (delivered on idle) instead of straight to the CLI. Intended: writing then would fold the message into the finished turn's tail. | OPEN |

## Meta-review risks

| Severity | Risk | Status |
|---|---|---|
| HIGH | SDK version mismatch (0.2.105 vs pinned 0.2.168) | OPEN |
| MED | a never-ending background task exempts an owned unit from TTL and RSS restart | OPEN |
| MED | SessionBusyError from the retry path's abandoned-turn branch reaching the pending store | OPEN |
| LOW | _injected_tasks entries are not pruned | OPEN |
| LOW | frontend has no handling for label injected | OPEN |

## Open items

- Gate-2 fixes landed after the review loop ended (tests and mutations cover them, no second review)
- needs rebuild + restart, then: start a background command, let it finish without sending, check the tab streams and shows the follow-up
- known: frontend sees a short CLI-initiated turn only on reconcile/focus (live SSE is the follow-up run)
- known: channel sessions do not relay CLI-initiated turns
- [gate2] Stopped: the fixing spec says the class did not close (spec_not_agreed: 2 skeptic BLOCKs in round 1 after the root-cause review answered SOUND); root-cause review: SOUND — ROOT-CAUSE VERDICT: SOUND The approach (one reader per CLI subprocess, turns paired by replayed uuid, a quiescence gate, a router sink) is not what the open findings attack. What is left is two lifecycle edges the design never specified. More fixes on top of it should converge. - **Only one round has run.** No finding has come back after a fix. All four open blockers are from round 1. The two skep. Still open (4): c…
- [runner] Not tested in this run (regression): 473 test file(s) reach a changed module only through other modules and were not run: backend/tests/test_active_run_commit_gate.py, backend/tests/test_admission_backfill.py, backend/tests/test_admission_band.py, backend/tests/test_admission_calibration.py, backend/tests/test_admission_is_noise.py, backend/tests/test_admission_rootfix.py, backend/tests/test_admission_routing.py, backend/tests/test_admission_trust_field.py, backend/tests/test_admission_trust_stamp.py, backend/tests/test_advance_continuation.py, backend/tests/test_adversarial_commit_g…
- [degraded] deliver completed with 8 open gate error(s): Fix by class: adversarial_review.findings[1] (correctness, HIGH) names no invariant of the design (got 'I6'). Name the I<n> it violated, or add the missed invar; Fix by class: adversarial_review.findings[3] (red-team, HIGH) names no invariant of the design (got 'I6'). Name the I<n> it violated, or add the missed invarian; Fix by class: adversarial_review.findings[4] (state-machine, HIGH) names no invariant of the design (got 'I6'). Name the I<n> it violated, or add the missed inv (+5)
