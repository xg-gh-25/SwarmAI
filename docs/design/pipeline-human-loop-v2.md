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
---

# Pipeline human loop v2

## 1. Problem

With the Conductor (`pipeline-run-conductor-v1.md`), Runs execute in the background and
on Hives, several at once. A person must be able to work with all of them from one place,
without opening each Run's session. Today (`pipeline-v1-human-loop.md`,
`run_protocol/decision.py`):

1. A Run can only ask, and a person can only pick one of its options. Free text
   "records nothing": the Run stays `awaiting_human` and re-asks.
2. There is no follow-up on the same question; every question spends one of the Run's
   two asks (`decision_cap: 2`).
3. Landing approval has one outcome, approve. A person cannot reject with a reason or
   ask for changes.
4. A person cannot tell a Run something while it is not asking.
5. Answering means opening the owner session and sending "continue".
6. A Run waiting for a person holds its executor: the Local Worker slot (one per Host)
   and its process, for up to 4 hours.
7. The same question shown in the dashboard, a chat tab and Slack has no shared state, so
   answering in one place leaves the others stale.

## 2. Principles

- **A person's input is a signed fact in the Run before it is delivered.** Every input,
  from any surface, goes through one daemon write path into the Run's event chain. Delivery
  to the agent is a separate step that can be retried, and is never the only copy.
- **One message stream per Run; every surface is a reader of it.** The dashboard, a chat
  tab card, a Slack thread and Central's mirror each keep a cursor into the same stream.
  None keeps its own copy of whether something is answered.
- **First write wins.** An open item is resolved by exactly one input. A later input from
  another surface is refused with who answered, where and when, and that surface shows it.
- **The agent can ask and acknowledge, never answer.** As in v1, only the daemon writes a
  person's input (`pipeline-v1-human-loop.md` "Only a human writes").
- **A person's words cannot silently widen the change.** The agent must say whether an
  input changes scope; landing recomputes the change size from the actual diff anyway
  (`_small_change`).
- **Waiting costs nothing.** A Run waiting for a person releases its executor.

## 3. Messages

### 3.1 Kinds

| Direction | Kind | Event | Blocks the Run |
|---|---|---|---|
| Run → person | Question, with rounds | `decision` (new: `round`, `parent`) | yes |
| Run → person | Approval request | `approval` (landing, or another guarded action) | yes |
| Run → person | Notice: how an input was applied, or a choice the Run made itself | `notice`, `input_ack` | no |
| Run → person | Key progress: stage change, parked / resumed, restarted, landed | existing stage and dispatch events | no |
| Person → Run | Answer: an option, an option plus a note, or "none of these" plus text | `human_input{kind: answer}` | unblocks |
| Person → Run | Approval result: approve, request changes (text), reject (text) | `human_input{kind: approval}` | unblocks |
| Person → Run | Guidance: free text at any time | `human_input{kind: guidance}` | no |
| Person → Run | Control: stop, resume, cancel, take over | existing `user_stop`, `resume`, cancel, `owner_transfer` | as today |

`human_input` replaces v1's option-only `human_answer`. It carries: `input_id`, `kind`,
`target` (the item it resolves, or none for guidance), `option_id` (optional), `text`
(optional, stored as a blob, the person's words verbatim), `channel` (`dashboard`,
`chat`, `slack`), `actor`, `idempotency_key`, and for remote Runs the answer-command id.

### 3.2 Items

A blocking message opens an **item**: one question (all its rounds) or one approval
request. An item is in exactly one state, computed from the event chain:

| State | Meaning |
|---|---|
| `open` | waiting for a person |
| `resolved` | resolved by one `human_input` (its id is recorded) |
| `expired` | its 24 h deadline passed (§5.4) |
| `superseded` | the Run ended, was cancelled, or moved to an Attempt that no longer asks it |

Rounds: a follow-up is a new `decision` with `parent` = the item's first question and
`round` + 1. It reopens the same item. At most 3 rounds; after the third answer the Run
must proceed or end with a reason. Follow-ups do not count against `decision_cap`, which
still limits how many new questions a Run may start. Guidance is not an item and has no
limit.

### 3.3 Acknowledgement

Every `human_input` that the agent receives must be acknowledged with `input_ack`
(`input_id`, `applied`: what the agent did with it, `scope_changed`: true / false). If
`scope_changed` is true, the agent must open a question to confirm before acting on the
wider scope. `land` refuses while any delivered `human_input` is unacknowledged. The ack is
shown in the stream as a notice.

Request changes becomes a human review finding (existing findings path), then FIX →
REVIEW → a new approval request. Reject ends the Run `abandoned(rejected_by_human)` with
the text in `REPORT.md`.

## 4. The message stream

### 4.1 What it is

The stream of a Run is a projection, in one order, of:

- the human-related events of the Run's signed event chain (§3.1), and
- the key dispatch events of the Conductor (queued, started, parked, resumed, restarted,
  landed, failed).

It is not a second store of facts. It is kept on the Host as an append-only log per Run,
`stream_seq` 1, 2, 3 …, written by one Host function each time either source appends a
relevant event. It can be rebuilt from the two sources; on rebuild, order is by source
time, then source, then source seq. Execution detail (each Read, Edit, Bash) is not in the
stream; it stays in the Worker transcript.

The aggregate feed is the merge of all Runs' streams by time. The "Needs you" view is the
aggregate feed filtered to open items. No list of open items is stored anywhere else.

### 4.2 One write path

Every surface calls the same daemon function:

```text
submit_human_input(run_id, target_item | none, kind, option_id?, text?,
                   channel, actor, idempotency_key) -> accepted | refused(reason)
```

It runs on the Conductor executor, never on the chat send or stream path, and in one
conditional append:

1. checks the actor is the owner and the channel's own proof (§4.4);
2. returns the earlier result if the `idempotency_key` was already used;
3. for an item: appends only if the item is `open` and the option belongs to its current
   round; otherwise refuses with `already_resolved(by input, channel, at)`, `expired` or
   `superseded`;
4. appends `human_input` to the Run's event chain (local Run), or records
   `answer_received` and posts the answer command (remote Run, conductor §8.3), committing
   only on Central's `accepted`;
5. appends the stream message and notifies readers (§4.3).

Delivery to the agent follows (§5).

### 4.3 Readers and cursors

Each reader keeps a cursor `(reader_id, run_id, last_stream_seq)` and is at-least-once:
it may see a message twice and must be idempotent by `stream_seq`.

| Reader | Cursor kept | What it does |
|---|---|---|
| Pipelines overlay (each open client) | none stored; SSE `Last-Event-ID` | Live feed and per-Run thread. Connects only while open and visible, on the `nav` pool. Reconnect resumes from the last id |
| Chat tab of the Run's origin | per (session, run) | One status card per Run in that tab, written once at submit as a single message row that only references `run_id`. Its content (stage, open item, its state) is rendered from the stream on read, so the tab's transcript gets no message per event. An open item is answerable on the card |
| Slack thread of the Run's origin | per (thread, run), with an outbox | Posts each new message to the thread; records the Slack `ts` per `stream_seq`, so a retry edits instead of posting twice. When an item changes state it edits that item's message: buttons removed, "Answered in the dashboard at 01:38: A" |
| Central mirror | per run (conductor §4.2 outbox) | Copies the events to the org store. For a remote Run it is also the transport (§5.3) |
| Left-nav badge | none | Count of open items, read from the projection |

A reader that is down catches up from its cursor; nothing is lost because the stream is
the source, not the reader. Ordering is per Run only.

### 4.4 Channel proof

| Channel | Proof that the owner sent it |
|---|---|
| Dashboard / overlay | Authenticated click with the item's confirm secret, sent only to the frontend (same rule as the conductor's submit draft, §6.4) |
| Chat tab card | The same; for an inline Run's own AskUserQuestion card, the existing person-answered path of `ask_question_gate` |
| Slack button | One-time nonce bound to owner, thread, Run, item, round and option (conductor §8.3) |
| Slack typed reply | The bound owner's message in the Run's thread. It becomes the note of the one open item; with no open item it is guidance; with several open items the bot asks which one, with buttons |

### 4.5 Answered elsewhere

Because state is read from the stream, every reader shows the same thing after a write.
A surface whose own write was refused shows the reason it got: for example the Slack card
is edited to "Answered in the dashboard at 01:38: A", and the overlay shows "You answered
this in Slack". No surface keeps a pending answer of its own.

## 5. Delivery to the agent

### 5.1 Two paths

- **Fast path.** If the agent is blocked in `AskUserQuestion` for that item
  (`ask_question_gate` waiter), the daemon hands it the input as today (`set_answer`),
  including the text.
- **Normal path.** Every `pipeline_v1.py` command's output includes `inbox`: every
  `human_input` not yet acknowledged. This covers guidance, inputs that arrived while the
  Run was parked, and inputs after a restart or crash. The agent must `input_ack` each.

### 5.2 Parking

A Run whose open item has waited 10 minutes is **parked**: the Worker's process is
recycled to COLD keeping `_sdk_session_id`, and its slot is released (new dispatch state
`parked`, conductor §5.1: `running → parked` releases, `parked → running` acquires ahead
of queued requests). When an input resolves the item, the Run re-queues first on its
executor, resumes with `--resume`, and reads the input from `inbox` on its first
`pipeline_v1.py` call.

Cost: one cold start, a few seconds. After 10 minutes the 5-minute Bedrock prompt cache
has expired anyway, so the resumed turn reprocesses the context whether or not it was
parked (measured 2026-10-23, session 31b7372b). Waiting less than 10 minutes keeps the
process and the fast path.

A `streaming` slot is not the issue: a session blocked in `AskUserQuestion` is
`WAITING_INPUT`, which `_streaming_count` does not count (`session_unit.py:1588`). The
executor slot is.

### 5.3 Remote Runs

Unchanged from the conductor design §8.3: the Host commits the input only after Central
accepts the signed answer command against the current Attempt; the Hive delivers it on its
own fast or normal path and replies `answer_ack`, then the agent's `input_ack`. Parking on
a Hive releases the Hive slot the same way. A Hive's own dashboard shows the stream
read-only and does not accept inputs: the Host is the only place a person's input enters.

### 5.4 Deadlines

An open item expires after 24 hours (was 4 hours, which existed to bound a held process;
a parked Run holds nothing). The Run then ends `abandoned(decision_timeout)`. A Run
resumed long after it parked may find `main` moved; landing's CAS and rebase handle that
(conductor §7.2).

## 6. Scenarios

| # | Scenario | Path |
|---|---|---|
| 1 | A background Run asks while you are chatting elsewhere | Badge and Slack thread show it; you pick A and add a note in the overlay; Slack card is edited to "answered in the dashboard"; the parked Run re-queues first and reads it from `inbox` |
| 2 | No option fits | "None of these" + your text; the Run acks with a notice "I'll do it like this"; if scope changes it opens round 2 to confirm |
| 3 | Three rounds and still unclear | After round 3 the Run decides from evidence (`agent_after_cap`, counted against autonomy in Eval) or ends with a reason |
| 4 | You dislike the change at approval | Request changes with text → human finding → FIX → REVIEW → new approval request |
| 5 | You want to steer a Run that is not asking | Guidance; delivered on its next `pipeline_v1.py` call; it cannot land until acked |
| 6 | Eight Runs wait at once | Needs-you view: blocking items, longest wait first; keyboard through them; each send moves to the next |
| 7 | You answer in Slack and the dashboard | First write wins; the other surface shows where it was answered |
| 8 | You want to do it yourself | Take over in a chat tab (`owner_transfer`); the Run becomes inline in that tab |
| 9 | You do not answer for a day | Parked, holding nothing; expires at 24 h |
| 10 | Daemon restarts while you type | Your input, once accepted, is in the event chain; the Run reads it after recovery |
| 11 | A remote Run, Hive restarts after you answered | Adopt bumps the epoch; if the new Attempt asks the same question, the Host re-sends the committed answer (conductor §8.3) |

## 7. Changes

Pipeline v1:

- `decision.py`: answers with text and "none of these"; rounds with `parent`; follow-ups
  outside `decision_cap`.
- Events: `human_input` (replaces `human_answer`), `input_ack`, `notice`, `approval`.
- `land` refuses unacknowledged inputs; every command returns `inbox`.
- Approval outcomes request changes (human finding) and reject.
- `pipeline-v1-human-loop.md`: the "free text records nothing" rule and "every dashboard
  action ends in the owning chat" are replaced by this document for Worker-owned Runs;
  inline Runs keep their AskUserQuestion card, which writes through §4.2.

Conductor (`pipeline-run-conductor-v1.md`):

- Dispatch state `parked` and its transitions; item deadline 24 h.
- §8.3 answer commands carry `kind`, `option_id` and `text`.
- §10 surfaces become readers of the stream (§4.3).

Host:

- `submit_human_input` (§4.2), the stream log, reader cursors, the Slack reader's outbox,
  the overlay SSE on the `nav` pool.

## 8. Hot-path cost

- Chat send and stream: none. All writes run on the Conductor executor; the chat-tab card
  is one row written at submit and rendered from the stream on read.
- Daemon idle: no work without open overlays or new events. The overlay SSE exists only
  while an overlay is open.
- Per event: one append to the Run's stream log and one in-process notify.

## 9. Open questions

1. Parking threshold: 10 minutes is chosen against the 5-minute prompt cache. Measure cold
   resume of a Worker before fixing it.
2. Is the 3-round limit per item right, or should it come from the profile?
3. Should a notice ever require a reply? This design says no; a person who disagrees sends
   guidance.
