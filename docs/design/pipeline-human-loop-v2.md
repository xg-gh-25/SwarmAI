---
title: "Pipeline human loop v2: one message stream per Run, many readers"
created: 2026-10-24
updated: 2026-10-24
project: SwarmAI
status: draft
tags: [pipeline, human-loop, conductor, slack, message-stream]
related:
  - docs/design/pipeline-v1-human-loop.md
  - docs/design/pipeline-run-conductor-v1.md
  - docs/design/project-agent-delivery-os-hld.md
  - docs/design/pipeline-conductor-ui-mock.html
---

# Pipeline human loop v2

Revision 2 (2026-10-24) after a fresh-context review: Worker Runs no longer block in
`AskUserQuestion` (parking left a dangling tool call), delivery and acknowledgement are
split into a daemon fact and an agent claim, request changes and reject get their own
lifecycle, stream ids are immutable, every input route needs a server-side capability,
typed Slack replies are guidance only, expiry is an event, and a Run can hold several
items.

Revision 3 (2026-10-24) after review 8: no Run blocks in `AskUserQuestion`, inline ones
included (one path, one deadline); a lease renewal (`adopt` of a parked Run) keeps its
Attempt and items, only a real restart is a new Attempt; request changes, reject and
approve are each one atomic batch checked by landing under the same lock; the projection
commits order and high-water mark in one transaction; scope growth compares content, not
path sets; the person's text has one serializer; the semantic digest has a canonical
lineage; and the trust section no longer claims a per-surface capability, matching the
B-d decision (same-user agent isolation is a recorded TODO, §4.4).

## 1. Problem

With the Conductor (`pipeline-run-conductor-v1.md`), Runs execute in the background and
on Hives, several at once. A person must be able to work with all of them from one place,
without opening each Run's session. Today (`pipeline-v1-human-loop.md`,
`run_protocol/decision.py`):

1. A Run can only ask, and a person can only pick one of its options. Free text records
   nothing: the Run stays `awaiting_human` and re-asks.
2. There is no follow-up on the same question; every answer spends one of the Run's two
   asks (`decision_cap: 2`).
3. Landing approval has one outcome, approve.
4. A person cannot tell a Run something while it is not asking.
5. Answering means opening the owner session and sending "continue".
6. A Run waiting for a person holds its executor slot and process for up to 4 hours.
7. The same question shown in the dashboard, a chat tab and Slack has no shared state.

## 2. Principles

- **A person's input is a signed fact in the Run before it is delivered.** One daemon
  write path, from any surface. Delivery to the agent is a separate, retriable step.
- **One message stream per Run; every surface is a reader of it.** None keeps its own copy
  of whether something is answered.
- **First write wins.** An open item is resolved by exactly one input; a later input from
  another surface is refused with who answered, where and when.
- **The agent can ask and acknowledge, never answer.** Only the daemon writes a person's
  input. What the agent says it did with an input is a claim; what can be checked is
  checked by the daemon.
- **A waiting Run holds nothing.** No Run, Worker or inline, blocks inside a tool call
  waiting for a person; it ends its turn and, if it holds one, gives its slot back.
- **The person's words are the owner's instruction, delivered as data.** They reach the
  model through one serializer (§5.2), never spliced into a prompt.
- **"Signed by the daemon" means the normal execution path.** After B-a..B-c only the
  daemon signs, and its key is in the Keychain / keyring where available. A same-user
  process that runs arbitrary code is not excluded until agent process isolation exists
  (`pipeline-v1-b-class-authority.md`, B-d not done); this design does not claim more.

## 3. Messages and items

### 3.1 Kinds

| Direction | Kind | Event (producer) | Blocks the Run |
|---|---|---|---|
| Run → person | Question, with rounds | `decision` (cli) with `item_id`, `round`, `parent` | yes |
| Run → person | Approval request | existing `land_request` (cli), now opening an approval item | yes |
| Run → person | Notice: how an input was applied, or a choice the Run made itself | `notice`, `input_ack` (cli) | no |
| Run → person | Key progress | existing stage and dispatch events | no |
| Person → Run | Answer: option, option + note, or "none of these" + text | `human_input{kind: answer}` (hook) | resolves an item |
| Person → Run | Approval result: approve / request changes (text) / reject (text) | `human_input{kind: approval}` (hook) | resolves an item |
| Person → Run | Guidance: free text at any time | `human_input{kind: guidance}` (hook) | no |
| Person → Run | Control: stop, resume, cancel, take over | existing events | as today |
| Daemon | Delivered to the agent | `input_delivered` (hook) | — |
| Daemon | Item expired | `item_expired` (hook) | — |

Producer names are logical (`hook`, `cli`); since B-b every one of them is physically
signed in the daemon (`run_protocol/daemon_exec.py`).

`human_input` carries `input_id`, `kind`, `item_id` (none for guidance), `option_id`
(optional), `text` (optional; a blob holding the person's words verbatim), `channel` and
`actor` (both set by the server from the route or adapter that received it, never taken
from the request body), `idempotency_key`, and for remote Runs the answer-command id.

`human_input` replaces v1's option-only `human_answer` in one cutover for new Runs. Per the
project rule for Pipeline v1 changes, there is no compatibility path: Runs created before
the cutover are not replayed by the new code and their data is left for the later unified
cleanup. Every consumer of `human_answer` (event validator, lifecycle fold, decision
documents, Gate-0 reset, audit timing and autonomy accounting, dashboard schema) moves to
`human_input` in the same change, and its tests move with it.

### 3.2 Items

A blocking message opens an **item**. A Run holds an item set keyed by immutable
`item_id`; each item has a kind (question / approval), lineage (`parent`), current round,
deadline, and resolution. The Pipeline status is `awaiting_human` while any blocking item
is open, and leaves it when the last one is resolved.

| State | Set by |
|---|---|
| `open` | the `decision` or `land_request` event |
| `resolved` | one `human_input` (its id is recorded) |
| `expired` | `item_expired`, written by the daemon at the deadline under the same lock and compare-and-swap as an answer, so expiry is a recorded fact and replay is time-independent |
| `superseded` | the Run ended or was cancelled, or a new Attempt does not ask it again (§3.5); never by a lease renewal |

Counters are split: `decision_items_started` (new questions the Run opened; `decision_cap`
still limits this) and `human_answers` (inputs that resolved items). A follow-up round is a
new `decision` with the same `item_id` lineage (`parent` = first question, `round` + 1);
it reopens the item and does not count against `decision_cap`. At most 3 rounds; after the
third answer the Run must proceed or end with a reason. Guidance is not an item and has
no limit.

A question item must carry, as today's decision schema does, `why_evidence_insufficient`
and per option its `consequence`; the cards show them (§6).

### 3.3 Delivery and acknowledgement

- `input_delivered` is written by the daemon at the moment the input is handed to the
  agent (§5), with the input's digest and the turn it went into. It is the receipt.
- `input_ack` is the agent's statement (`input_id`, the delivered digest, `applied`: what
  it did, `scope_changed`: its own judgement). It is a claim, shown as a notice and graded
  by Offline Eval, not trusted.
- What the daemon checks itself:
  - `land` refuses while any `input_delivered` has no matching `input_ack`, or any
    accepted input has not been delivered;
  - at delivery the daemon records a content snapshot of the Run's code: path → blob hash
    for every changed path, against the run base. At landing it diffs the final snapshot
    against each delivery snapshot and lists, per input, the paths **added, modified or
    deleted** after it ("changed after your note: 1 added, 2 modified"), so a further edit
    to a file that was already changed is shown too. The change size is recomputed as
    today (`_small_change`), so growth moves the Run into full review whatever the agent
    claimed.

### 3.4 Approval outcomes

Approval happens in DELIVER, after TEST. Each outcome is **one append batch** under the
Run lock: `human_input` resolving the approval item plus every event the outcome implies,
all appended or none. Landing (`land` / `run-land`) takes the same lock and, before
consuming an `auth`, checks that its `land_request` is the newest one, not superseded,
and its approval item resolved by an approve. So no interleaving lets a stale request
land. The three outcomes:

- **Approve**: today's landing authorization (`auth` on the signed `land_request`).
- **Request changes** (text required): the daemon writes `human_finding` (producer hook,
  severity HIGH, the person's text) and supersedes the `land_request`
  (`approval_superseded`). The Run returns from DELIVER to BUILD in a new fix cycle; the
  final, test and review evidence of the superseded candidate is no longer valid, and the
  cycle must pass REVIEW (which must close the human finding) and TEST again before a new
  `land_request` opens a new approval item. Landing execution refuses any superseded
  `land_request`, including through the dangerous-command approval path.
- **Reject** (text required): the daemon writes `human_reject`; the Run ends
  `abandoned(rejected_by_human)` and the text goes into `REPORT.md`. Any open
  `land_request` is superseded.

### 3.5 Items across Attempts

Two different things renew a Run's lease:

- **Lease renewal of a parked Run** (`parked → parked`, e.g. a Hive restart while no Worker
  is running). Nothing executed, so it is the **same Attempt**: the epoch and incarnation
  advance, the open items stay open and are bound to the new epoch in the same
  conditional update. An answer to them is accepted against the new epoch.
- **A new Attempt** (a crash while running, a running adopt, a rebase). It supersedes the
  old Attempt's open items. If it asks a question whose **semantic digest** equals an item
  that was already resolved, the daemon writes `input_rebound` (old input, new item) and
  the earlier answer is reused; the person is not asked twice. Any difference is a new
  item.

The semantic digest is the hash of a canonical JSON of: item kind, round, question text,
ordered options with their consequences and risk, the **semantic lineage** (the ordered
list of ancestor items' semantic digests, computed the same way, plus the answers that
resolved them), and the Run's changed-path content snapshot at asking time (§3.3). It
excludes every physical identity: `item_id`, event ids, epoch, Attempt and times.

## 4. The message stream

### 4.1 Identity and order

Every message has an immutable id derived from its source: `<source>:<run_id>:<source_seq>`
(source `run` for the Run's signed chain, `dispatch` for the Conductor Store). Two
sequence numbers are assigned once, at projection time, and never changed:

- `stream_seq`, per Run: the order the Host projected the message;
- `feed_seq`, global: the order across all Runs, used by the aggregate SSE.

The projection log is the record of that order: a SQLite table with `message_id` unique.
Projecting one message is **one transaction**: skip if the id exists, allocate
`stream_seq` and `feed_seq`, insert, and advance that source's high-water mark. A crash
leaves either all of it or none, so a message never gets a second pair of sequence
numbers. On startup the Host projects any source event above the mark before serving
readers, so an input that was signed but not yet projected when the daemon died appears
on restart. Only if the projection log itself is lost is it rebuilt from the sources, in
order (source time, source, source_seq), with new sequence numbers; readers then dedupe
by message id, which is unchanged, and the SSE tells connected readers to resync.

Execution detail (each Read, Edit, Bash) is not in the stream; it stays in the Worker
transcript.

### 4.2 Views

The working view is one card per Run, grouped by state (conductor §10); its Needs-you
column is the Runs with an open blocking item. The aggregate feed (all streams by
`feed_seq`) is the Activity tab, for audit. No list of open items is stored outside the
item sets.

### 4.3 One write path

```text
submit_human_input(surface, run_id, item_id | none, kind, option_id?, text?, idempotency_key)
    -> accepted(input_id) | refused(reason)
```

`surface` is set by the route or adapter that received the input (§4.4); `actor` and
`channel` follow from it, never from the request body. It runs on the Conductor executor,
never on the chat send or stream path, and in one locked compare-and-swap on the Run:

1. checks the surface's own proof (§4.4) for this owner, Run and (if any) item;
2. same `idempotency_key` with the same payload digest returns the earlier result; with a
   different payload it is refused;
3. for an item: appends only if the item is `open` and the option belongs to its current
   round; otherwise `already_resolved(by input, channel, at)`, `expired` or
   `superseded`;
4. appends the outcome's batch (§3.4) or `human_input` (local Run), or records
   `answer_received` and posts the answer command, committing only on Central's
   `accepted` (remote Run, conductor §8.3);
5. projects the message and notifies readers.

### 4.4 Surfaces and what proves the input is the owner's

| Surface | Proof | Actor and channel |
|---|---|---|
| Overlay review view and board cards | The human-only dashboard route on loopback; same proof as today's decision and `run-land` routes | owner, `dashboard` |
| Chat-tab card (inline and background Runs) | The same route, called from that tab | owner, `chat` |
| Slack button or modal | One-time nonce bound to Slack team, channel, thread, user, Run, item, round and option (conductor §8.3), on a Slack-signed request; a modal (opened by a "Reply with note" button) carries the text | owner, `slack` |
| Slack typed reply in the Run's thread | Slack-signed event; bound team, channel, thread, user and message ts verified server-side; bot-originated events rejected; deduplicated by that identity | owner, `slack`; **guidance only**, never resolves an item |

Slack proofs authenticate the person. The dashboard routes do not: like the existing
decision and `run-land` routes, they trust that a call on loopback came from the
dashboard. `run_owner_gate` denies an agent's Bash call to every human-only Conductor
route by command text. That is the whole protection for now, and it is the same open item
as B3: a same-user agent that runs arbitrary code can reach loopback by other means. Per
the B-d decision (XG 2026-10-24) no per-surface secret is added, since such an agent could
read it too; the fix is agent process isolation, recorded as the TODO in
`pipeline-v1-e7-review.md` item 7. Phase 2 states this residual in E7, not as closed.

### 4.5 Readers and cursors

Each reader is at-least-once and idempotent by message id.

| Reader | Cursor | What it does |
|---|---|---|
| Pipelines overlay | `feed_seq` via SSE `Last-Event-ID`; a persisted per-user seen cursor per Run for unread counts | Run cards, each Run's thread, Activity feed. Connected only while open and visible, on the `nav` pool |
| Chat tab of the Run's origin | per (session, run) | One status card per Run, written once at submit as a row that only references `run_id`; its content is rendered from the stream on read |
| Slack thread of the Run's origin | per (thread, run), with an outbox | Posts each new message once (Slack `ts` recorded per message id); edits an item's message when its state changes ("Answered in the dashboard at 01:38: A") |
| Central mirror | per run (conductor §4.2 outbox) | Copies events to the org store; for a remote Run also the transport |
| Left-nav badge | none | Count of open blocking items |

## 5. Delivery to the agent

### 5.1 No Run blocks on a person

For every Run, `pipeline_v1.py decision-request` and `land_request` return at once with
`awaiting_human` and the instruction to end the turn; no Run calls `AskUserQuestion` for a
Pipeline item. The turn ends cleanly, with no open tool call.

- **Worker Run** (background or remote): when its turn ends with a blocking item open,
  the Conductor **parks** it at once: the process is recycled to COLD keeping
  `_sdk_session_id`, and the slot is released (conductor §5.1). There is no warm wait:
  the prompt cache expires after 5 minutes anyway, so holding the process buys little and
  costs a slot.
- **Inline Run**: its tab shows the item as the Run's status card (§4.5), answered through
  the same write path. The tab stays usable for chat meanwhile; it holds no pooled slot.

This replaces v1's `AskUserQuestion` card and its 4-hour waiter for Pipeline items. Inline
and Worker Runs then have one item lifecycle and one 24-hour deadline (§5.4).
`ask_question_gate` keeps serving ordinary, non-Pipeline questions.

### 5.2 How an input reaches the agent

- **Resume turn.** When an item is resolved:
  - a Worker Run becomes `resume_pending`; when it gets a slot, the Conductor resumes the
    session (`--resume`) and sends one new turn;
  - an inline Run gets that turn in its own tab session, through the tab's pending-message
    queue (never interrupting a turn already streaming there).
  The turn's body is the delivery envelope below with every undelivered `human_input` and
  the instruction to `input_ack` each. The daemon writes `input_delivered` for each as the
  turn is sent.
- **Guidance during a long turn.** While the Run is streaming, pending guidance is
  attached at the next tool boundary by a PostToolUse hook as `additionalContext` (the
  hook chain already merges it, `hook_builder.py`), in the same envelope, and
  `input_delivered` is written then. The surface shows "pending delivery" until it is. A
  person who needs the Run to stop now uses Stop, not guidance.
- **Every `pipeline_v1.py` command** also returns any accepted but undelivered input, in
  the same envelope, as a backstop.

**Delivery envelope**, built by one serializer (`run_protocol/human_envelope.py`) used by
all three paths: a fixed header line `[OWNER INPUT for run_<id>, JSON follows]`, then one
JSON object per input (`input_id`, `kind`, `item_id`, `option_id`, and `text` as a JSON
string, so quotes, newlines and any bracket line are escaped and cannot end the block),
then a fixed footer. The serializer neutralizes the header and footer strings if they
occur inside `text`. A red test feeds text that contains a forged header, footer and
system-style block and checks the envelope still parses to exactly one input.

### 5.3 Remote Runs

As in conductor §8.3, with three changes: Central accepts an answer command while the
request is `running`, `parked` or `resume_pending`; parking on a Hive releases the Hive
slot; and a Hive restart while parked is a lease renewal of the same Attempt
(`parked → parked`, epoch and incarnation renewed, open items rebound to the new epoch,
no slot effect, §3.5). A Hive's own dashboard shows the stream read-only; the Host is the
only place a person's input enters.

### 5.4 Deadlines

Two deadlines, never mixed:

- **Item deadline**: 24 hours after the item opened (was 4 hours, which bounded a held
  process; a waiting Run holds nothing). At it the daemon writes `item_expired` and the
  Run ends `abandoned(decision_timeout)`. It is checked only when an input is **accepted**
  (local CAS, Central's conditional update). Slack nonces carry it.
- **Delivery deadline**: for a remote Run, `accepted_at + 1 hour`, signed into the answer
  command next to the item deadline. Central checks the item deadline at acceptance; the
  Hive checks only the delivery deadline for an accepted command. So an answer accepted at
  23:59 is still delivered. The Host keeps the command-signing key valid for verification
  at least until the delivery deadline.

## 6. What a reviewer sees

Designed for a person handling 20 to 30 Runs (conductor §10, mockup
`pipeline-conductor-ui-mock.html`):

- **Board** of Run cards: Needs you, Running, Waiting, Delivered (24 h).
- **Question card**: the question, `why_evidence_insufficient` in one line, each option
  with its consequence, round n/3, waiting time.
- **Approval card**: target branch, changed files with +/−, test summary, review findings
  open / fixed, files changed after your notes; Approve is enabled only after the evidence
  preview has been opened once (one keystroke).
- **Review view** with Prev / Next (`J` / `K`) over two queues: **Needs you** (longest wait
  first) and **Delivered to review** (failed, then unread, then newest).
- **Unread** counts from the persisted seen cursor.
- **Batch actions** only for non-judgement operations: mark seen, cancel queued Runs. No
  batch approve or batch answer.

## 7. Scenarios

| # | Scenario | Path |
|---|---|---|
| 1 | A background Run asks while you chat elsewhere | Run ends its turn and parks; badge and Slack show it; you pick A with a note in the overlay; Slack card shows "answered in the dashboard"; Run becomes `resume_pending`, gets a slot first, resumes with your input in a new turn and acks it |
| 2 | No option fits | "None of these" + text; the Run acks with how it will proceed; if it opens round 2 to confirm scope, it parks again |
| 3 | Three rounds and still unclear | After round 3 the Run decides from evidence (`agent_after_cap`, counted against autonomy) or ends with a reason |
| 4 | You dislike the change at approval | Request changes with text → `human_finding`, `land_request` superseded → BUILD → REVIEW must close it → TEST → new approval item |
| 5 | You steer a Run that is not asking | Guidance; attached at its next tool boundary; it cannot land until acked; files it adds afterwards are listed on the approval card |
| 6 | Twenty Runs wait or have delivered | Needs-you queue first, then Delivered-to-review, walked with J / K |
| 7 | You answer in Slack and in the dashboard | First write wins; the other surface shows where it was answered |
| 8 | You want to do it yourself | Take over in a chat tab (`owner_transfer`) |
| 9 | You do not answer for a day | Parked, holding nothing; `item_expired` at 24 h |
| 10 | Daemon dies right after accepting your input | The input is signed; on restart the projection catches up from the source high-water mark and the Run reads it when resumed |
| 11 | A remote Hive restarts while the Run is parked | Lease renewal of the same Attempt: the open item stays open, bound to the new epoch; your later answer is accepted against it |
| 13 | An inline Run asks while you are away from its tab for 6 hours | Its card stays open in the tab and in the overlay; you answer on return; the tab gets the resume turn through its pending queue |
| 12 | A new Attempt asks the same question again | Same semantic digest → `input_rebound`, no second question; any difference → a new item |

## 8. Changes

Pipeline v1 (one cutover, new Runs only):

- `decision.py`: item ids and lineage, rounds, text and "none of these"; split counters.
- Events: `human_input` (replaces `human_answer`), `input_delivered`, `input_ack`,
  `notice`, `item_expired`, `input_rebound`, `human_finding`, `human_reject`,
  `approval_superseded`; producers as in §3.1.
- Lifecycle: item set; DELIVER → BUILD fix cycle on request changes; landing refuses
  superseded `land_request` and unacknowledged or undelivered inputs.
- `decision-request` / `land_request` return without `AskUserQuestion` for every Run; the
  Pipeline-decision branch of `ask_question_gate` and its `human_answer` recording go.
- `run_protocol/human_envelope.py`: the one delivery serializer (§5.2).
- `pipeline-v1-human-loop.md` is superseded by this document.

Conductor (`pipeline-run-conductor-v1.md`): `parked` and `resume_pending` states and their
transitions (including lease loss in `resume_pending`), remote acceptance in `parked` /
`resume_pending`, `parked → parked` lease renewal, item and delivery deadlines, typed
Slack replies as guidance only, §10 surfaces.

Host: `submit_human_input`, the stream projection (one transaction per message) with
`stream_seq` / `feed_seq` and startup catch-up, reader cursors and the persisted seen
cursor, the Slack reader's outbox, the overlay SSE on the `nav` pool, the PostToolUse
guidance hook, delivery content snapshots.

**Phasing.** Ships in Conductor Phase 2 for local Runs, its Slack reader in Phase 5, its
remote delivery in Phase 6.

**Red tests before code (Phase 2):**
- ask → park → answer → cold resume delivers exactly one input with no dangling tool call;
  the same for an inline Run through its tab's pending queue;
- first write wins across two surfaces; an agent Bash call to a human-only route is denied;
- input accepted then daemon killed before projection appears after restart; a crash
  between projection insert and high-water update never yields two sequence pairs;
- guidance during a long BUILD is delivered at the next tool boundary; text with a forged
  envelope header, footer or system block parses to exactly one input;
- land refuses an unacked or undelivered input; a further edit to an already changed file
  after a note is listed on the approval card;
- a six-way race on one Run (answer, `item_expired`, cancel, lease renewal, request changes,
  land) under the Run lock: exactly one outcome per item, and no `auth` on a superseded
  `land_request` is ever consumed;
- a parked Run's lease renewal keeps its item answerable; a running adopt supersedes it;
- a re-asked identical question in a new Attempt is rebound, a changed one is not; the
  digest is equal across Attempts with different ids and epochs.

## 9. Cost on hot paths

- Chat send and stream: none. All writes run on the Conductor executor; the chat-tab card
  is one row written at submit and rendered from the stream on read.
- Run streaming: the PostToolUse guidance hook reads one in-memory flag per tool call
  and does work only when guidance is pending.
- Delivery snapshot: one `git diff --name-only` plus blob hashes of the changed paths per
  delivered input, on the Conductor executor.
- Daemon idle: no work without open overlays or new events.

## 10. Open questions

1. Is the 3-round limit per item right, or should it come from the profile?
2. Should the Approve button really wait for the evidence preview, or is that friction for
   small `direct` changes?
