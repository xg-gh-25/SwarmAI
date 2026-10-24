---
title: Chat stream structure — what to build now, what only on evidence
date: 2026-10-24
created: 2026-10-24
updated: 2026-10-24
status: accepted (v3, after two fresh-context reviews; v1 and v2 were not build-ready). Scope agreed 2026-10-24: A0, A1, A2, A4 now; A3 and Track B conditional
---

# Chat stream structure

Scope: how a turn's CONTENT reaches the desktop chat and how it is laid out. The turn's
STATE is one fact since chat-turn-liveness P0-P4 and is not changed. Display rules:
`Knowledge/Designs/2026-10-19-chat-turn-display-spec.md`,
`docs/design/2026-10-24-turn-column-grid.md`. Not in scope: SkillsPage, channels,
History rendering (they keep their own direct stream / snapshot).

## 0. Why v3 is split

v1 and v2 tried to remove the two delivery paths (R1) and the length rule (R3) in one
design. Both need a durable record of which rows belong to which turn, and two reviews
showed what that record really takes: turn membership for queued and coalesced sends (a
queued receipt cannot name its turn; one drain turn carries several client ids), segment
boundaries in the history wire format and across pagination merges, a POSITIVE
completeness certificate across writers that commit on their own (TurnWriter, Stop
receipt, background-agent result, question/permission write-back, pending rows, resume
marker), an owner/epoch fence before marking turns interrupted, a schema migration on a
1.5 GB data.db, and a durable "turns after N" feed for a watcher that missed a turn.

That is a large change for two costs that are, today, UNPROVEN:
- R1: the only live-only divergence seen (the duplicated RESULT tail) is closed by the
  block key (`8c49d7d1d`); which path delivered the repeated start is unknown.
- R3: the length rule keeps a longer live copy; with the block key no known mechanism
  produces such a copy any more.

So v3 builds now what is certain and cheap, and gates the turn record on evidence.

## 1. The structures (unchanged facts, corrected by the reviews)

| # | Structure | Track |
|---|---|---|
| R1 | Turn content reaches the tab by two paths (POST + subscription); `sessionTurnRouter` decides who renders | B |
| R2 | The bubble holds content that exists only in the frontend | A |
| R3 | A reconcile picks local vs DB by text length | B |
| R4 | Live and settled layouts are different trees (the last text changes parent at turn end) | A |
| R5 | ChatPage (3575 lines) has no behaviour harness; its protections are source-text tests | A |
| R6 | The live==reload property test covers both paths, disconnects and restarts, but not block keys, retries into one bubble, repeated starts; two flakes on main | A |

## 2. Track A — build now (each phase: its acceptance tests first and red, implementation, green, mutation-checked, reviewed, one commit; the suite is never left red)

### A0. Evidence and flakes
- `[stream-restart] {block, source: post|sub, eventSeq}` logged once per block where the
  block key absorbs a repeated start (`applyTextDelta` / `applyThinkingDelta`;
  `createStreamHandler` knows its source).
- `[live-diverged] {turn, kinds, lengths}` logged (no content) whenever a reconcile keeps
  a local copy that differs from the record (the length rule's "local wins" branch). This
  is the evidence that gates Track B.
- Root-cause `test_session_injected_turn`'s teardown hang (4/10 on main) and the
  `sessionTurnWiring` resync flake.
- Result (2026-10-24, A0 commit):
  - `test_session_injected_turn`: a fire-and-forget `session_state` push left
    pending at the end of a test reads the DB on the test's own loop. When
    pytest-asyncio closes that loop, the aiosqlite worker answers a closed loop,
    raises twice and dies (the next pool `close()` waits on it forever: teardown
    hang), or a connect cut off mid-flight is in no pool list (its NON-daemon worker
    blocks interpreter exit). Fix: the pool tracks connections from before the
    connect and ends their workers on close / on a failed connect, on the running
    loop (production benefit: a task pending at daemon shutdown no longer blocks
    exit); the test fixture cancels the test's leftover tasks and closes that loop's
    pools while it runs (`close_loop_pools`). 25/25 runs clean (was ~1/2 hung).
  - `sessionTurnWiring` resync: a turn ended in an earlier test leaves its 200 ms
    turn-end reconcile timer; under full-suite load it fires inside the resync
    case's window and counts as a tail read. Fix: `resetTestState` clears each
    tab's timers, as `closeTab` does. Test: `streamingTestIsolation.test.ts`.
  - Seen once each in 3 full frontend runs after these fixes, not yet root-caused
    (not in this commit's scope): `sessionEvents.test.ts` "a silent stream is
    cancelled and reconnected" (a 30 ms silence timer against an 80 ms wait under
    load) and `overlayHostE2E.test.tsx` "back-to-chat closes the open surface".
  - Review deviations: `[stream-restart]` carries `via` (the transport that
    delivered the start, tagged at the POST and subscription entry points) and
    `handler` (whose handler rendered it): a POST `turn_start` begins the turn as
    the subscription would, so the handler label alone does not answer R1.
    `[live-diverged]` carries `where` (load / reconcile-id / reconcile-cid), not the
    store phase: both paths run only at idle. The pool's `start()` is transactional
    and a cancelled lazy read grow returns its slot (found by review; pre-existing).

### A1. ChatPage controllers become testable (R5)
- Extract send / stop / continue into `pages/chat/turnControls.ts` with injected deps;
  ChatPage calls them; behaviour tests replace the source-text guards (turnLiveness.p0
  R4/R5, stopOnce.guard).
- Extract a pure content reducer from the hook (`applyContentEvent`: blocks only); the
  effect layer (request state, activity, prompts, notices, caches, reconcile scheduling)
  stays in the hook under its existing harness.
- Built (2026-10-24): `pages/chat/turnControls.ts` (`sendMessage`, `sendWhileBusy`,
  `answerQuestion`, `decidePermission`, `stopTurn`, `stopBackground`; deps built per
  render in ChatPage's `turnDepsRef`); `applyContentEvent` returns `{msg, restarted}`
  (pure: the handler logs `[stream-restart]`). Behaviour tests:
  `pages/chat/__tests__/turnControls.test.ts`, `__tests__/applyContentEvent.test.ts`;
  the keyed-send scan covers turnControls too. Deviation from strict parity: the
  no-store fallback now uses the same reducer as the store path, so it honours block
  keys (a repeated start continues its block there too). SkillsPage's Stop stays a
  source guard (not part of A1).

### A2. No local-only content in the bubble (R2)
Full inventory (both reviews):

| Local-only content today | Where | Becomes |
|---|---|---|
| `*Stopped*` text | ChatPage `handleStop` (4 writes) | the persisted `turn_stop` receipt (already rendered); while pending the `stopping` activity; a Stop whose response has no receipt → a tab notice, not bubble text |
| transport error text (fetch reject, premature close) | hook ~4126-4176 | a request-level notice on the user's message (the "Not sent · Retry" surface for unaccepted; the reconnecting state for an accepted send) |
| error text of an `error` event the backend did not persist | hook ~3336 | backend-known turn errors are persisted as a `turn_notice` (kind `error`, code + one line) INSIDE `_published_turn` before `turn_end`; errors raised after it (HTTP wrapper, `routers/chat.py` ~557-640, ~1510-1570) are request-level notices |
| `turn_limit_reached` appended text | hook ~3700-3715 | a persisted `turn_notice` (kind `turn_limit`) |
| `Session resumed` / `Context refreshed` lines | ChatPage ~3210, hook ~3676 | rendered from the persisted resume marker / compaction row, else removed |
| `context_compacting` local system record | hook ~3741-3749 | transient presentation state (the activity line), replaced by the persisted compaction row |
| optimistic escalation resolution | ChatPage ~2846-2868 | exempt (ephemeral by design, `core/escalation.py`); listed in the guard |

- Guard: drive every bubble writer (hook, store, A1 controllers); after a turn ends and
  the record is applied, the bubble's content blocks equal the record's, except
  provisional streamed blocks of a running turn and the escalation exemption.
  Presentation fields (`isError`, send state, badges, sub-agent progress) are not content.
- Why this is Track A: it removes a second source without any new schema, and it is the
  precondition for ever letting the record win (Track B).

**Scope narrowed (Gawan, 2026-10-24, after A1).** Track B is conditional, so A2 is no
longer justified as its precondition: each row must stand on what the user loses today.
Built: only the rows where a reload loses something the user needs.

| Row | Decision | Why |
|---|---|---|
| backend turn errors | **build**: a persisted `turn_notice` (reason `error`, a stable `code`, one line) yielded inside the turn before `turn_end`; errors raised outside a turn stay request-level | a reload loses why the turn stopped (the same class as `gave_up`, already persisted) |
| `turn_limit_reached` | **build**: a persisted `turn_notice` (reason `turn_limit`) | a reload loses "send a message to continue"; rare (0 in the current log), the mechanism exists |
| `*Stopped*` text | not built | not rendered as text: `turnView` reads it only as the stopped flag, which the receipt also sets (`turnView.ts` STOP_MARKER_RE); nothing visible to gain |
| transport error text | not built | the "Not sent · Retry" and reconnecting surfaces already carry it; a network error is not conversation content, so losing it on reload is correct |
| `Session resumed` / `Context refreshed` | not built | separators only; no loss on reload |
| `context_compacting` record | not built | transient by nature |
| escalation | exempt (decision 5) | |

The full-equality guard above is therefore not built; the A4 cases that ship with A2 are
the ones guarding the D4 fix (block keys, a repeated start, a retry into the same bubble)
plus the two new notice reasons.

### A3. One render tree (R4), within a bubble — CONDITIONAL
Gate: after A0-A2 ship, a remaining end-of-turn jump or remount is observed. The visible
symptom (the answer moving left) was fixed by `d9c0e0d96`; the move is now vertical
only, and A3's surface (fold-state migration, a render-cost baseline) is large.
- `turnZones` emits a flat ordered row list `{key, kind, ancestors: string[], column,
  label?, visibleWhenFolded?, forceOpen?}`. Keys are namespaced by the bubble's own
  stable ids (`tx-<ordinal>`, tool ids, `th-<ordinal>`); a later turn-segment namespace
  is Track B's job.
- Visibility: every ancestor open; a done stage's result row `visibleWhenFolded`; a stage
  with an open question `forceOpen`s its chain.
- Folded rows stay unmounted (as today). Fold-surviving UI state is inventoried and
  moved: strip / stop-receipt / agent-row / agents-panel / thought / doc expansion → the
  bubble's choice map keyed by row key; an agent's in-flight Stop → its controller
  (A1); fetched stage-document text → a per-session cache; settle / linger clocks keyed
  by row key in the existing module maps.
- Turn end: the last text row keeps its key, component and parent list; it gains a
  RESULT label row and loses its phase ancestor. Test: React node identity equal before
  and after; no markdown re-parse.
- Cost: commit time, DOM node count and effect count of an 80-call turn, measured before
  (baseline captured first) and after; after ≤ before.

### A4. Guards (R6)
- Extend `liveReload.property.test.ts`: streamed blocks with keys, a retry into the same
  bubble (new run key), a repeated block start, a resync mid-block, a Stop with and
  without a receipt, a turn_notice, a turn-limit notice. Properties: each block key
  renders once; after the record is applied the bubble equals the record (A2); a row's
  key and parent list are equal live and settled (A3).
- WebKit geometry: manual macOS verification script (Playwright WebKit + app CSS), run
  before a visual change merges; not a merge gate; nothing about Windows.

Order: A0 → A1 → A2; A4's cases ship with the phase they guard (A3's cases only if A3
runs).

## 3. Track B — only on evidence

Gate: A0's `[live-diverged]` or `[stream-restart]` with `source: post` appears in real
use after Track A ships, or a user-visible divergence is reported. Until then R1 and R3
stay as they are (the length rule is a known heuristic, listed, logged).

If the gate opens, Track B is its own design, starting from these requirements (from the
two reviews, all of them, so the next design does not rediscover them):
1. Turn membership: `turn_members(turn_id, message_id, client_id)` written when a turn
   (incl. a drain's coalesced turn) claims its messages; a queued receipt names no turn;
   duplicate receipts read the mapping.
2. Rows → turn: `messages.turn_id` (one relation, indexed where non-null), no JSON row
   list; updates of existing rows keep their turn and bump their rev.
3. Positive completeness: a turn is `complete` only when its writer saw every required
   write succeed and committed the close; any failure, or a failed close, leaves it
   incomplete. Late writers (Stop receipt, agent result, answers, permissions) either
   update the message and the turn's mutation rev atomically or are excluded from
   completeness explicitly.
4. Owner fence: each turn records its process epoch; only records of a provably dead
   epoch become `interrupted`.
5. History wire format: merged bubbles return `segments: [{turnId, rowIds, start,
   end}]`, merged across pagination; render keys namespaced by `turnId`.
6. Discovery: a durable "turns after revision N" read (or a `turn_committed` feed fact),
   not inference from `activeTurnId`.
7. An interrupted turn's unbound local block stays explicitly "unknown"; no content
   comparison decides replacement.
8. Schema lifecycle: retention aligned with message TTL, session-delete cascade,
   compaction rows session-level, legacy NULL behaviour, record-insert failure.
9. Migration: timed on a synthetic 1.5 GB fixture (never the live data.db), lock
   duration, restart mid-migration.
Then S1 (subscription renders, `accepted` binds non-queued sends, request lifecycle
handler stays) and S3 (reconcile by segment from the record, delete the length rule).

## 4. Decisions (Gawan, 2026-10-24: "only the certain problems")

1. Split: A0, A1, A2, A4 now. A3 only if an end-of-turn jump remains after A2. Track B
   (durable turn record, one delivery path, record-wins reconcile) only if A0's logs or
   a report show live-only divergence.
2. A2 (narrowed after A1, see A2's scope table): backend turn errors and turn-limit
   become persisted `turn_notice`s; `*Stopped*`, transport-error text, the resumed /
   refreshed separators and the compacting record stay as they are (no user loss).
   A4 ships only the block-key / repeated-start / same-bubble-retry cases and the two
   notice reasons; the WebKit script waits for the next visual change.
   First step for Gawan: rebuild, so A0's logs collect the evidence for A3 / Track B.
3. A3: folded rows stay unmounted; their UI state moves to the choice map / controllers.
4. A4: the WebKit geometry check is manual, not a merge gate.
5. Escalation stays local-only (exempt) until it has its own design.

## 5. Review history

- v1: fresh-context review — not build-ready (accepted had no turnId; a short first turn
  lost before JOIN; POST-only events; turn_end.rev cannot certify completeness; client_id
  is not a turn boundary; incomplete local-only inventory; render-tree gaps).
- v2 (durable turn record): second review — not build-ready (queued/coalesced sends have
  no turn at receipt; hello/resync cannot name a missed turn; history merge erases
  segments; completeness not atomic across writers; no owner fence; turn_limit and
  context_compacting missing; P3 left the suite red).
- v3: split into Track A (certain, no schema) and Track B (on evidence); all review
  requirements for B recorded in §3.
