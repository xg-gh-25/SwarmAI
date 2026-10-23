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
- **A waiting Run holds nothing.** A Worker Run never blocks inside a tool call waiting for
  a person; it ends its turn and gives its slot back.
- **The person's words are the owner's instruction, delivered as data.** They reach the
  model in a delimited, labelled field, never spliced into a prompt.

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

`human_input` carries `input_id`, `kind`, `item_id` (none for guidance), `option_id`
(optional), `text` (optional; a blob holding the person's words verbatim), `channel` and
`actor` (both derived by the server from the authenticated surface, never taken from the
request body), `idempotency_key`, and for remote Runs the answer-command id.

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
| `superseded` | the Run ended or was cancelled, or a new Attempt does not ask it again (§3.5) |

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
  - at delivery the daemon records the Run's changed-path set; at landing it computes the
    paths added after each input from the diff, shows them on the approval card ("changed
    after your note: 2 files"), and the change size is recomputed as today
    (`_small_change`), so added files move the Run into full review whatever the agent
    claimed.

### 3.4 Approval outcomes

Approval happens in DELIVER, after TEST. Its three outcomes:

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

A new Attempt (restart, adopt, rebase) supersedes the old Attempt's open items. If the new
Attempt asks a question whose **semantic digest** equals an item that was already
resolved, the daemon writes `input_rebound` (old input, new item) and the earlier answer is
reused; the person is not asked twice. The semantic digest covers the item kind, question
text, ordered options with their consequences and risk, lineage, and the Run's changed-path
set at asking time. Any difference is a new item.

## 4. The message stream

### 4.1 Identity and order

Every message has an immutable id derived from its source: `<source>:<run_id>:<source_seq>`
(source `run` for the Run's signed chain, `dispatch` for the Conductor Store). Two
sequence numbers are assigned once, at projection time, and never changed:

- `stream_seq`, per Run: the order the Host projected the message;
- `feed_seq`, global: the order across all Runs, used by the aggregate SSE.

The projection log is the record of that order. Each source keeps a high-water mark of
what has been projected; on startup the Host projects any source event above the mark
before serving readers, so an accepted input that was signed but not yet projected when
the daemon died appears on restart. Only if the projection log itself is lost is it
rebuilt from the sources, in order (source time, source, source_seq); readers then dedupe
by message id, which is unchanged, not by sequence number.

Execution detail (each Read, Edit, Bash) is not in the stream; it stays in the Worker
transcript.

### 4.2 Views

The working view is one card per Run, grouped by state (conductor §10); its Needs-you
column is the Runs with an open blocking item. The aggregate feed (all streams by
`feed_seq`) is the Activity tab, for audit. No list of open items is stored outside the
item sets.

### 4.3 One write path

```text
submit_human_input(capability, run_id, item_id | none, kind, option_id?, text?)
    -> accepted(input_id) | refused(reason)
```

`actor`, `channel` and `idempotency_key` come from the authenticated surface (§4.4), not
from the arguments. It runs on the Conductor executor, never on the chat send or stream
path, and in one locked compare-and-swap on the Run:

1. verifies the capability for this owner, Run and (if any) item;
2. same `idempotency_key` with the same payload digest returns the earlier result; with a
   different payload it is refused;
3. for an item: appends only if the item is `open` and the option belongs to its current
   round; otherwise `already_resolved(by input, channel, at)`, `expired` or
   `superseded`;
4. appends `human_input` (local Run), or records `answer_received` and posts the answer
   command, committing only on Central's `accepted` (remote Run, conductor §8.3);
5. projects the message and notifies readers.

### 4.4 Capabilities per surface

| Surface | Capability | Actor and channel |
|---|---|---|
| Overlay review view and board cards | A per-Run capability (covers guidance and control) and a per-item confirm secret, issued by the daemon only into the frontend's payload over the dashboard's own connection | owner, `dashboard` |
| Chat-tab card | The same, scoped to the Runs shown in that tab | owner, `chat` |
| Inline Run's own AskUserQuestion card | The existing person-answered path of `ask_question_gate`, which then calls the write path | owner, `chat` |
| Slack button or modal | One-time nonce bound to Slack team, channel, thread, user, Run, item, round and option (conductor §8.3); a modal (opened by a "Reply with note" button) carries the text | owner, `slack` |
| Slack typed reply in the Run's thread | Bound Slack team, channel, thread, user and message ts, verified server-side; bot-originated events rejected; deduplicated by that identity | owner, `slack`; **guidance only**, never resolves an item |

Capabilities and confirm secrets never appear in CLI output, persisted messages, the
transcript, logs, Central events or replay payloads; a test covers each serializer.
`run_owner_gate` denies a Bash call to every human-only Conductor route as defence in
depth only; the routes enforce the capability themselves. Residual risk as for today's
decision route: the daemon API is unauthenticated on loopback, so a local process that can
read the frontend's memory could replay a capability.

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

### 5.1 Worker Runs never block on a person

For a Worker-owned Run (background or remote), `pipeline_v1.py decision-request` and
`land_request` return at once with `awaiting_human` and the instruction to end the turn;
the Worker never calls `AskUserQuestion`. The turn ends cleanly, with no open tool call.
When the Worker's turn ends with a blocking item open, the Conductor **parks** the Run at
once: the process is recycled to COLD keeping `_sdk_session_id`, and the slot is released
(conductor §5.1). There is no 10-minute warm wait: the prompt cache expires after 5
minutes anyway, so holding the process buys little and costs a slot.

Inline Runs keep today's `AskUserQuestion` card in their own tab: a person is there, and
the tab session is not a pooled slot.

### 5.2 How an input reaches the agent

- **Resume turn.** When an item is resolved, the Run becomes `resume_pending`; when it gets
  a slot, the Conductor resumes the session (`--resume`) and sends one new turn whose body
  is a structured block: the open Run id, every undelivered `human_input` (kind, item,
  option, the person's text in a delimited field marked as the owner's words), and the
  instruction to `input_ack` each. The daemon writes `input_delivered` for each as the
  turn is sent.
- **Guidance during a long turn.** While the Worker is streaming, pending guidance is
  attached at the next tool boundary by a PostToolUse hook as `additionalContext` (the same
  mechanism the design hooks use) and `input_delivered` is written then. The surface shows
  "pending delivery" until it is. A person who needs the Run to stop now uses Stop, not
  guidance.
- **Every `pipeline_v1.py` command** also returns any accepted but undelivered input, as a
  backstop.

### 5.3 Remote Runs

As in conductor §8.3, with three changes: Central accepts an answer command while the
request is `running`, `parked` or `resume_pending`; parking on a Hive releases the Hive
slot; and a Hive restart adopts a parked request (`parked → parked`, epoch and incarnation
renewed, no slot effect). A Hive's own dashboard shows the stream read-only; the Host is
the only place a person's input enters.

### 5.4 Deadlines

An open item expires 24 hours after it opened (was 4 hours, which bounded a held process;
a parked Run holds nothing): the daemon writes `item_expired` and the Run ends
`abandoned(decision_timeout)`. Slack nonces and answer commands carry the same item
deadline. After an answer is accepted, delivery to a remote Hive has its own retry window
of 1 hour; the Host keeps the command-signing key valid for verification at least that
long.

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
| 11 | A remote Hive restarts while the Run is parked | `parked → parked` adopt; your later answer is accepted against the new epoch |
| 12 | A new Attempt asks the same question again | Same semantic digest → `input_rebound`, no second question; any difference → a new item |

## 8. Changes

Pipeline v1 (one cutover, new Runs only):

- `decision.py`: item ids and lineage, rounds, text and "none of these"; split counters.
- Events: `human_input` (replaces `human_answer`), `input_delivered`, `input_ack`,
  `notice`, `item_expired`, `input_rebound`, `human_finding`, `human_reject`,
  `approval_superseded`; producers as in §3.1.
- Lifecycle: item set; DELIVER → BUILD fix cycle on request changes; landing refuses
  superseded `land_request` and unacknowledged or undelivered inputs.
- `decision-request` / `land_request` for Worker Runs return without `AskUserQuestion`.
- `pipeline-v1-human-loop.md` is superseded by this document for Worker-owned Runs.

Conductor (`pipeline-run-conductor-v1.md`): `parked` and `resume_pending` states and their
transitions, remote acceptance in `parked` / `resume_pending`, `parked → parked` adopt,
24 h item deadline, typed Slack replies as guidance only, §10 surfaces.

Host: `submit_human_input`, capabilities, the stream projection with `stream_seq` /
`feed_seq` and startup catch-up, reader cursors and the persisted seen cursor, the Slack
reader's outbox, the overlay SSE on the `nav` pool, the PostToolUse guidance hook.

**Phasing.** Ships in Conductor Phase 2 for local Runs, its Slack reader in Phase 5, its
remote delivery in Phase 6.

**Red tests before code (Phase 2):** ask → park → answer → cold resume delivers exactly one
input with no dangling tool call; first write wins across two surfaces; capability missing
or replayed is refused; input accepted then daemon killed before projection appears after
restart; guidance during a long BUILD is delivered at the next tool boundary; land refuses
an unacked input; request changes supersedes the `land_request` and a stale authorization
cannot land; `item_expired` at the deadline and an answer at the same moment cannot both
win; a re-asked identical question is rebound, a changed one is not.

## 9. Cost on hot paths

- Chat send and stream: none. All writes run on the Conductor executor; the chat-tab card
  is one row written at submit and rendered from the stream on read.
- Worker streaming: the PostToolUse guidance hook reads one in-memory flag per tool call
  and does work only when guidance is pending.
- Daemon idle: no work without open overlays or new events.

## 10. Open questions

1. Is the 3-round limit per item right, or should it come from the profile?
2. Should the Approve button really wait for the evidence preview, or is that friction for
   small `direct` changes?
