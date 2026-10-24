---
title: "Pipeline Run Conductor V1: inline, Local background and Slack"
created: 2026-10-23
updated: 2026-10-24
project: SwarmAI
status: draft
tags: [pipeline, conductor, slack, scheduling, execution-plane]
related:
  - docs/design/project-agent-delivery-os-hld.md
  - docs/design/pipeline-run-worktree.md
  - docs/design/pipeline-human-loop-v2.md
  - docs/design/2026-10-24-swarmai-usable-by-anyone.md
  - docs/design/pipeline-conductor-ui-mock.html
---

# Pipeline Run Conductor V1

**Revision 2026-10-24: local scope.** The earlier draft also ran Runs on EC2 and Cloud
Desktop Hives through a Central Service in AWS, and kept an organization copy of every
Run there for Trace and Eval. Both are taken out of V1 (§13); that draft is in git
history at `dfc1108cf`. This revision also changes who owns a Run: the Conductor owns it,
and a session only drives it (§4.1). That removes the stand-in owner session a background
Worker needed.

**Revision 2026-10-24b: built on the general Pipeline.** Aligned with
`2026-10-24-swarmai-usable-by-anyone.md` (final r13, "usable" below). The Conductor is
built after usable S6, on its append-only run log: there is no signing, no hook token and
no daemon-executed pipeline command (usable §3.3.5); the Pipeline runs no tests, the agent
tests natively and submits validation reports (usable §3.3.3); a Run exists only when a
person chose the Pipeline (usable P1, P4); Windows has no Pipeline and so no Conductor
Runs (usable §3.2); nothing in it recognizes the SwarmAI repo or project (usable P7); and
it adds no protection against a same-user agent (usable P5).

## 1. Summary

### 1.1 The problem

Today a Pipeline Run runs only inline, in the Chat tab that started it, and is owned by
that tab's session. V1 solves two problems:

1. **Start a Run from a Chat tab, the Pipelines overlay or Slack (now or on a schedule),
   and run it inline in the tab or on one Local background Worker, queued when the Worker
   is busy.**
2. **Handle every Run's questions and approvals from one place**, whichever surface it
   started from (`pipeline-human-loop-v2.md`).

One deterministic **Pipeline Run Conductor** in the Host daemon accepts a request, places
it, dispatches it and tracks it. It is not an LLM and never calls one.

### 1.2 Entry points and placements

| Entry point | Can place on | Exists today |
|---|---|---|
| Chat tab, inline | `inline_local` (that tab) | Yes. V1 registers it with the Conductor |
| Chat tab, asking for the background (§6.3) | `background_local` | No |
| Pipelines overlay | `background_local` | No |
| Slack, immediate or scheduled (§7) | `background_local` | No |

| Placement | Parallel | Queue |
|---|---|---|
| `inline_local` | One per Chat tab, as today | None |
| `background_local` | One Local Worker | Host queue in submit order; one stream always kept for chat and Slack |

The Pipeline itself does not change: one requirement, one continuous context and one code
tree per Run. The Conductor decides **where and when** a Run executes; the Pipeline
decides **how**, and whether it passed.

## 2. Premises

- **P1. Local only.** Every Run executes on the Host. No cloud service, no remote node.
- **P2. Concurrency.** Inline Runs behave as today. At most one background Worker runs at
  a time; one stream is always kept free for chat and Slack (§6.2).
- **P3. The Conductor owns every Run; a session drives it** (§4.1).
- **P4. DDD.** An inline Run reads the live Host DDD, as today. A background Run reads an
  immutable snapshot taken at submit (§5.2).
- **P5. Trust as in usable P5.** Run log records carry a producer field and a checksum
  that detects truncation, not forgery (usable §3.3.5). The human-only routes on loopback
  are guarded by `run_owner_gate` against an agent's Bash call; a same-user agent that
  runs arbitrary code is not excluded, and that stays known and not fixed
  (`pipeline-v1-e7-review.md` item 7). Single-use drafts, idempotency keys and Slack
  nonces stay: they stop double clicks, replays and other people in a channel, not the
  agent. V1 adds no gate and no hardening.
- **P6. Only Runs a person chose.** Every Conductor request comes from a person choosing
  the Pipeline: in a session (usable P4), by asking for it outright, by a Slack submit or
  by a schedule they created. The chat agent never suggests the background as a way to
  push work into the Pipeline; native stays the default.
- **P7. Any repo, any project.** The Conductor reads repos and projects through the same
  generic paths as every user (usable P7): no SwarmAI path, project name or repo check.
  Commands are `"$SWARM_BACKEND_BIN" pipeline-v1 …` and `"$SWARM_BACKEND_BIN" conductor …`
  in both packaged and dev builds (usable §3.2).
- **P8. macOS and Linux.** On Windows the Pipeline is not enabled (usable §3.2), so the
  Conductor accepts no request there and its surfaces say so.
- **P9. After usable S6.** The Conductor changes the files S6 rewrites (`engine.py`,
  `cli_commands.py`, `run_owner_gate.py`, `landing.py`, `lifecycle.py`), so it starts once
  S6 has landed, and the decision to start reads usable §6's measures (per-run cost,
  how many Runs a person starts inline).

## 3. Non-goals

- Running Runs on another machine, and any cloud service (§13.1).
- An organization Run store, cross-machine Trace or an Offline Eval service (§13.2). Each
  Run keeps today's local five-dimension audit.
- Project Agents, intent triage, graphs of Runs, Evolution.
- More than one background Worker.
- Background native work: an unattended task is a Pipeline Run (usable §3.3.1); the
  Conductor does not run native tasks.
- New gates or hardening against a same-user agent (usable P3, P5).
- Windows (P8).
- Compatibility with Runs created before this cutover: the owner → driver change (§4.1)
  and `human_answer` → `human_input` (`pipeline-human-loop-v2.md` §3.1) apply to new Runs
  only; older Runs are not replayed by the new code and their data waits for the later
  unified cleanup.
- Changing the Chat inline experience, with one exception: an inline Run's Pipeline
  questions and approvals are item cards in the tab instead of a blocking
  `AskUserQuestion` (`pipeline-human-loop-v2.md` §5.1).

## 4. Architecture

```text
 Chat tab, inline ─(pipeline-v1 create)─────┐
 Chat tab, "in the background" ─(draft)─────┤
 Pipelines overlay ─────────────────────────┼──► Conductor.submit() ──► Conductor Store
 Slack session (owner only) ─(draft)────────┤          │
 Host Job Scheduler (pipeline_submit) ──────┘          ├─ inline_local     → driven by the tab session
                                                       └─ background_local → driven by the Local Worker (1 slot)

 Every surface (overlay, chat tab, Slack thread) reads each Run's message stream and
 writes a person's input through one daemon path (pipeline-human-loop-v2.md §4).
```

The Conductor is a module in the Host daemon (`backend/core/conductor/`). It runs off the
event loop on its own executor and is never on the chat send or stream path.

### 4.1 Who owns a Run, and who drives it

After usable S6, `RunEngine.owner_session()` (`run_protocol/engine.py`) is the creator
session, then each `owner_transfer` record in the run log. That one value decides four
things:

1. which session may run mutating `pipeline-v1` commands: the CLI checks, under the run
   operation lock, that `SWARM_APP_SESSION_ID` is that session (`cli_commands._not_owner`);
   `run_owner_gate` only supplies the session id to an adopted prewarm subprocess
   (usable §3.3.5) and no longer issues tokens;
2. which session a landing approval binds to: `auth` requests carry a `session_id` and
   only that session may consume them (`landing.py` `make_request`,
   `consume_authorization`);
3. whose Stop or lost session pauses the Run (`pipeline_human.owned_runs`,
   `on_user_stop`, `on_session_lost`);
4. when another tab may take the Run over (takeover after `owner_live` is false, then
   `owner_transfer`).

It fuses "who is responsible for the Run" with "which process executes it". A background
Run has no tab, so the earlier draft created a session `pipeline:<run_id>` to stand as its
owner. V1 splits the two:

| Fact | Holder | Changed by |
|---|---|---|
| **Owner** | The Conductor, acting for the app's one owner (the person). Recorded in the contract as `owner: conductor` | Never |
| **Driver** | The one session allowed to run mutating `pipeline-v1` commands now: the tab session for an inline Run, the Worker session for a background Run | A `driver_bind` record in the run log, written only by the Conductor, in the daemon, under the run operation lock |

What follows from it:

- **Mutating commands.** The CLI accepts a mutating command only from the current driver,
  read from the run log under the operation lock. This is a correctness rule (one session
  acts for a Run at a time), not a defence against the agent (P5). `run_owner_gate` no
  longer decides takeovers.
- **Human input** binds to the Run and its item, never to a session
  (`submit_human_input`, `pipeline-human-loop-v2.md` §4.3). Any surface can answer; the
  driver's tab does not have to be open.
- **Landing approval.** An `auth` binds to its `land_request` and is consumed by the
  current driver. A driver change supersedes any open `land_request`
  (`approval_superseded`), so an approval given while one session drove is never consumed
  by another.
- **Takeover.** A person asks in a tab to take a Run over. The Conductor checks that the
  current driver is not live (for a Worker it knows, because it runs it; for a tab it uses
  `owner_live`, as today), stops the Worker if it is one, and writes `driver_bind` to the
  tab's session. Handing a Run back to the background is a `driver_bind` to a new Worker.
- **Stop and lost session** pause the Runs that session drives. A Worker's process is
  managed by the Conductor (§8).

The Worker is still a real headless `SessionUnit` with the full hook chain (§6.2); what
changes is that it drives the Run instead of owning it.

**Cutover** (one change, new Runs only, after usable S6): `owner_transfer` is replaced by
`driver_bind`; `owner_session()` becomes `driver_session()`; every reader above moves in
the same change (`engine.py`, `cli_commands.py`, `run_owner_gate.py`,
`pipeline_human.py`, `run_worktree.py`, `landing.py`), with its tests. Building it on S6's
log rather than today's signed events means it is written once.

### 4.2 Who owns each fact

| Fact | Owner | Readers |
|---|---|---|
| Run data: stage, verdict, findings, validation reports, the run log (including `driver_bind`), documents, transcripts | The Pipeline v1 run store, as after usable S6 | Pipeline, CLI, surfaces |
| Request, origin, placement, queue, Attempts | **Conductor Store** (§4.3) | Overlay, chat cards, Slack replies, Scheduler |
| A Run's message stream and item sets | Projection of the two sources above (`pipeline-human-loop-v2.md` §4) | Every surface |
| Schedule definition and fire times | Host Job Scheduler (`user-jobs.yaml`, job kind `pipeline_submit`) | Conductor |
| Schedule occurrences | Conductor occurrence ledger (§7.2) | Slack replies, Schedules tab |
| DDD content | The Host DDD, written only by today's local paths | Inline Runs live; background Runs through a snapshot (§5.2) |
| Landing on a Host repo | `run_protocol/landing.py` | Conductor |

The Conductor never copies Pipeline stage state into its store. What a person sees is a
join of the Conductor record and the Pipeline record, computed on read.

### 4.3 Conductor Store

Under `~/.swarm-ai/state/conductor/`, never in `data.db`, so chat history storage is
untouched:

- `requests/<request_id>.json`: the immutable submitted request;
- `runs/<run_id>.json`: the mutable dispatch record, written with `utils.file_lock`;
- `events.jsonl`: an append-only log of dispatch transitions;
- `stream.db`: the message-stream projection (SQLite, `pipeline-human-loop-v2.md` §4.1);
- `ddd/<digest>/`: read-only DDD snapshots for background Runs (§5.2);
- `occurrences.jsonl`: the schedule occurrence ledger (§7.2).

## 5. Data model

### 5.1 Request and dispatch record

```yaml
request:                       # immutable once accepted
  request_id: req_<ulid>
  project: <project name>
  requirement: "<the person's words, stored verbatim as data>"
  title: "<= 60 grapheme clusters (§5.4)"
  profile: direct | bugfix | full
  placement: inline_local | background_local
  origin:
    kind: chat | dashboard | slack | schedule
    ref: <session id | slack channel + thread ts | job id>
  ddd_ref:
    kind: live | snapshot | none   # live = inline only; none = the project has no DDD (§5.2)
    digest: sha256:... | null
  code_ref:                    # per bound repo, pinned at submit
    - repo: <repo name>
      base_commit: <sha>
  created_at: <iso>

dispatch:                      # mutable, owned by the Conductor
  run_id: <pipeline v1 run id, set when the Run is created>
  state: queued | dispatching | running | parked | resume_pending
       | cancelling | done | failed | cancelled
  attempts:
    - attempt: 1
      executor: host:inline | host:worker
      started_at: <iso>
      ended_at: null
      end_reason: null | completed | crashed | cancelled
  slot_held: false             # true exactly while this request holds the Worker slot
```

`state` describes dispatch only. Whether the Run passed, and whether it is waiting for a
person, is Pipeline state (`run_protocol/lifecycle.py`), read from the Pipeline store.

**Legal transitions** (normative; any other write is rejected). **Slot** is the effect on
the one Worker slot; a release is conditional on `slot_held = true`, so it happens once.

| From | To | Trigger | Slot |
|---|---|---|---|
| `queued` | `dispatching` | the Worker slot is free (oldest `resume_pending` first, then oldest `queued`) | acquire |
| `queued` | `cancelled` | owner cancel | — |
| `queued` | `failed(queue_timeout \| <rule>)` | 24 h in the queue, or a placement re-check failed | — |
| `dispatching` | `running` | the Worker reported `started` | keep |
| `dispatching` | `queued` | the Worker refused before start; keeps its `created_at` | release |
| `dispatching`, `running` | `cancelling` | owner cancel | keep |
| `running` | `running` (new Attempt) | crash recovery on daemon start (§8) | keep |
| `running` | `parked` | the Worker's turn ended with a blocking item open, or the Conductor interrupted it on a progress limit and opened an item (§6.2); the Worker is recycled to COLD keeping its SDK session id | release |
| `parked` | `resume_pending` | the last blocking item was resolved | — |
| `resume_pending` | `dispatching` | the Worker slot is free; goes ahead of `queued` | acquire |
| `parked`, `resume_pending` | `cancelled` | owner cancel (no Worker runs, no ack needed) | — |
| `parked` | `failed(decision_timeout)` | `item_expired` (`pipeline-human-loop-v2.md` §5.4) | — |
| `running` | `done` | Pipeline terminal `completed`, with its landing outcome | release |
| `running` | `failed(<pipeline reason>)` | Pipeline terminal `abandoned` or `failed` | release |
| `cancelling` | `cancelled` | the Worker acknowledged | release |
| `cancelling` | `cancelled(unacknowledged)` | cancel deadline | release |

An inline Run is registered directly as `running` with `executor: host:inline` and never
holds the slot; it ends `done` or `failed`. While it waits for a person its dispatch stays
`running` and its Pipeline status is `awaiting_human`; it does not park, because its tab
holds no pooled slot.

A daemon restart while a Run is `parked` or `resume_pending` is not a new Attempt: nothing
was executing, so its items stay open or answered as they were.

Every transition is an event in `events.jsonl` keyed `(request_id, attempt, seq)`;
replaying an applied key is a no-op.

**What a new Attempt may reuse** (HLD §6.5): it resumes from the Pipeline checkpoint and
may reuse verified source changes and completed documents as starting material, never the
closed Attempt's validation reports, review closure, authorization or verdict as proof:
the new Attempt submits its own TEST validation report, and landing requires its
`code_tree` to equal the landing tree and that tree to have passed Gate-2 (usable
§3.3.3, LG-1).

**What a person sees**, computed on read:

| Dispatch | Pipeline status | Shown as |
|---|---|---|
| `queued` / `dispatching` | (no Run yet) | Queued (position N) / Starting |
| `running` | `active`, `reflecting` | Running · stage |
| `running` | `awaiting_human` | Waiting for you |
| `parked` | `awaiting_human` | Waiting for you (slot free) |
| `resume_pending` | `active` | Answered, resuming next |
| `running` | `paused` | Paused |
| `done` | `completed` | Delivered, with the landing outcome |
| `failed` / `cancelled` | any or none | Failed (reason) / Cancelled |

A Pipeline terminal status with dispatch still `running` means the Worker has not reported
yet; the Conductor closes the request from the Pipeline record on its next pass.

### 5.2 DDD for background Runs

| Placement | DDD the Run reads |
|---|---|
| `inline_local` | The live Host copy, as today. `ddd_ref` records `kind: live` and the digest at create. It is a record, not a pin: an inline session may cultivate its DDD mid-run |
| `background_local` | An immutable snapshot taken at submit: the Conductor copies the Host DDD through an explicit manifest into `state/conductor/ddd/<digest>/` (read-only, content-addressed, shared by Runs with the same digest) and records the digest. A project with no DDD records `kind: none` and the Run reads none |

The Pipeline gains one input: `pipeline-v1 create --ddd-root <dir>`. With it the
Pipeline resolves the DDD from that directory instead of through `ddd_paths`, and the
contract records the digest, so a background Run cannot read another revision. Editing
the Host DDD while it runs does not change what it reads.

The snapshot is read-only input. REFLECT still writes through the daemon's background
job to the live Host DDD, or, for a project with no DDD, into the Run's own REFLECT
record and REPORT (usable §3.3.2).

### 5.3 Placement rules

Checked at submit and again at dispatch; a violating request is rejected with the rule
that failed, and placement is never changed silently.

- Every bound repo resolves to a Host checkout; the project's DDD is optional.
- The Host is not Windows (P8).
- `background_local` needs a profile the Worker supports (`direct`, `bugfix`, `full`).
- The base commits pinned at submit still exist at dispatch. If a repo's main moved, the
  Run starts from the pinned base and lands through the normal rebase rules of
  `landing.py`, as an inline Run does.

### 5.4 Run title

Every surface names a Run by its **title**, with the requirement as its subtitle and the
`run_id` only as a small secondary label.

- **What it is:** at most 60 user-perceived characters (grapheme clusters), plain single
  line text, part of the immutable request.
- **Where it comes from:**
  - Chat tab or Slack: the agent proposes it in the draft from the person's words; the
    person sees it on the confirmation card and can edit it (§6.3, §7.2).
  - Chat inline: the agent passes `pipeline-v1 create --title`, recorded in the
    contract and copied by `register_inline`. The flag does not exist today, and the
    engine's fallback cuts the requirement at 80 raw characters; Phase 1 adds the flag and
    replaces that cut with the rule below.
  - Schedule: the schedule's name plus the fire date in its timezone, for example
    "Nightly dependency bump · Oct 24".
  - Missing or empty: the first line of the requirement, cut at 60 grapheme clusters on a
    word boundary where there is one, normalized in one function. Never generated later
    by a model.
- **Never changes** after submit; the requirement stays the record of intent.
- **Shown** as "title · project · profile" on cards and Slack replies; the review view
  heads with the title and shows the full requirement under it.

## 6. Execution modes

### 6.1 `inline_local`

Unchanged for the user. `pipeline-v1 create` calls `Conductor.register_inline()` after
the Run is created: one file write off the event loop, recording origin `chat` and
`executor: host:inline`. The creating tab's session is the Run's first driver
(`driver_bind` at create). A registration failure is logged and never fails the create;
the Run is then absent from the Conductor view. Inline Runs never use the Worker slot.

### 6.2 `background_local`

**One Worker slot**, fixed at 1 in V1 and not a setting. A request that finds the slot
busy stays `queued`.

**The Worker is a headless app session.** The product gates that stay after usable S4
(`dangerous_command_gate`, `external_approval_gate`, the run-integrity gates), the
session id the CLI checks and landing all need a hooked app session; the `jobs/executor.py`
agent path (`claude --print`, possibly with `--bare`, which skips hooks) is not one and is
not reused. The Worker is a headless `SessionUnit` with app session id `pipeline:<run_id>`
and the full hook chain from `hook_builder.build_hooks`, the same way Slack channel
sessions run without a tab. Its environment carries `SWARM_BACKEND_BIN` like every agent
session (usable §3.2). The Conductor writes `driver_bind` to it before it starts. The
Worker:

1. creates the Run with the pinned requirement, title, profile, repos and
   `--ddd-root` (§5.2);
2. drives the Pipeline exactly as an inline session would, including testing natively and
   submitting its BUILD and TEST validation reports (usable §3.3.3);
3. when its turn ends with a blocking item open, is parked: recycled to COLD keeping its
   SDK session id, slot released (§5.1, `pipeline-human-loop-v2.md` §5.1);
4. when the Run reaches a terminal Pipeline state, releases its session through
   `LifecycleManager`, so post-session hooks run normally.

**A Worker that stops making progress is noticed.** usable S4 removes the gates that used
to bound test and background commands (`pytest_command_guard`,
`background_command_guard`), and nobody watches a headless Worker. The Conductor, not a
gate, bounds it with two limits read from the Worker's own events:

- **no progress:** no completed tool call for 15 minutes;
- **stage budget:** one stage running longer than 2 hours of wall clock.

Either one interrupts the Worker (the generation-safe `interrupt()`), parks the Run and
opens a notice-plus-question item for the person ("stalled in BUILD for 2 h: continue,
take over, or cancel"). Both values are V1 defaults, stored in the request so a Run is
judged by the values it started with; S2 measures real stage durations, and the defaults
are replaced by those measurements before Phase 2a lands. The Conductor never kills a
process tree it did not start, and a Run is never failed by these limits without the
person's answer.

**Interactive capacity is reserved.** The daemon caps concurrent streams at
`SessionUnit.MAX_CONCURRENT_STREAMS` (default 3). Today's admission
(`_await_streaming_slot`) is a soft poll that lets a stream through after 120 s anyway, so
it cannot reserve anything. Phase 2a replaces it with one atomic admission counter with
two classes:

- **interactive** (chat, Slack): admitted while total streams < cap, as today, including
  the 120 s fall-through, so chat does not change;
- **background** (the Worker): admitted only while total streams < cap − 1, decided and
  counted in the same locked step. It waits with no timeout, shown as "Waiting for
  capacity", never takes the last stream and never preempts an interactive one.

### 6.3 Asking for a background Run from a Chat tab

A person can ask in a tab: "run this as a bugfix pipeline in the background". Asking for
the Pipeline is the person's choice (P6); when a risky change makes the agent ask
"native or Pipeline" (usable P4), it may name the background as where a Pipeline Run
would go, never as a reason to pick the Pipeline.

1. The chat agent builds a draft with `"$SWARM_BACKEND_BIN" conductor draft`, a
   `python-backend` subcommand (usable §3.2) that calls the daemon's loopback API:
   project, requirement (verbatim), proposed title, profile. The draft
   applies §5.3 and the DDD snapshot (§5.2), is stored in the Conductor Store, binds the
   origin session, project, profile, DDD digest, base commits, title and the requirement's
   sha256, is single-use and expires after 10 minutes.
2. The tab shows a confirmation card: title (editable; an edit makes a new draft),
   requirement, project, profile, queue position or free slot, DDD digest, base commits.
   Only the person's click submits; the agent has no submit command. The submit route is a
   human-only dashboard route guarded like the decision and `run-land` routes:
   `run_owner_gate` denies an agent's Bash call to it. There is no per-draft secret: a
   same-user agent could read it too, and per usable P5 that gap stays known and not fixed
   (`pipeline-v1-e7-review.md` item 7). Submit consumes the draft atomically and re-checks
   §5.3 and the bound revisions; if any changed, nothing is submitted and the card shows a
   new draft.
3. After submit the Worker drives the Run. The tab stays free for chat and shows one status
   card for the Run, rendered from its stream (`pipeline-human-loop-v2.md` §4.5), on the
   `nav` pool while the tab is visible.
4. The Run's questions and approvals appear on that card, in the overlay and, for Slack
   origins, in the thread.
5. Closing the tab does not affect the Run.

## 7. Slack

### 7.1 Who may submit

Only the app's configured owner may submit a Run from Slack. Others in an allowlisted
channel can talk to the bot as today but cannot start a Run. The requirement text is
stored verbatim as data and is never an instruction to the Conductor.

### 7.2 Immediate and scheduled Runs

1. The owner asks in Slack, for example "run a bugfix pipeline on my-service: …" or "every
   weekday 09:00, run …".
2. The Slack session agent builds a draft with `"$SWARM_BACKEND_BIN" conductor draft`
   (§6.3). An invalid request is rejected here, with its reason.
3. The bot replies with a confirmation card: title, requirement, project, profile, DDD
   digest, base commits and, for a schedule, the cron expression and timezone.
4. The owner confirms on the card. The button carries only the draft id; the Slack
   adapter accepts the Slack-signed click only from the bound owner in that channel and
   thread, and consumes the draft as §6.3 does. Slack authenticates the person here, so
   this path does not depend on the loopback gap. Then:
   - immediate: `Conductor.submit()` queues the request;
   - scheduled: the Conductor writes a `pipeline_submit` job into the Host Job Scheduler,
     storing a request template, not a pinned request.
5. Progress goes to the same thread: one message edited in place (queued, started, stage),
   a new reply for waiting for you, delivered and failed. The thread is a reader of the
   Run's stream (`pipeline-human-loop-v2.md` §4.5).

`pipeline_submit` is a new `JobType` in `jobs/models.py`, handled by the scheduler without
spawning any CLI or LLM. When it fires the scheduler calls `Conductor.submit()` with the
template; the Conductor takes the DDD snapshot and base commits at that moment, so every
fire is a new request on the current code.

**Occurrences.** The generic cron path (`jobs/cron_utils.py`) catches up one missed
occurrence within seven days and reports only a boolean; `pipeline_submit` does not use
it. The Conductor keeps `occurrences.jsonl`, one row per scheduled time:

```yaml
occurrence:
  key: <job_id>@<scheduled_utc>     # a second fire with the same key is a no-op
  job_id: <id>
  scheduled_local: 2026-10-26T09:00 # in the schedule's IANA timezone
  outcome: submitted | missed | skipped_overlap | rejected
  request_id: <id> | null
  reason: <text> | null
```

- Every schedule stores an IANA timezone; there is no default. A local time that does not
  exist (spring forward) is `missed(dst_gap)`; one that happens twice fires once.
- A time that passed while the Host was off is recorded `missed(host_offline)` when the
  Host is back and reported in Slack. It is never submitted late.
- If the previous occurrence's request is not terminal, the new one is `skipped_overlap`.
- A template that fails §5.3 at fire time is `rejected` with the reason.

### 7.3 Answers in Slack

A typed reply in the Run's thread is **guidance**, never an answer
(`pipeline-human-loop-v2.md` §4.4). An item is resolved only by a button, or by a modal
opened by a "Reply with note" button. The nonce below authenticates the person against
other people in the channel and against replays; it is not a defence against the agent
(P5):

1. When the Host posts an item card to Slack it creates one nonce per option, bound to the
   Slack team, owner user id, channel, thread ts, `run_id`, `item_id`, round, option,
   attempt and the item deadline. The nonce lives on the Host only; the button carries its
   id.
2. On a click the adapter checks the user and the thread, then consumes the nonce with a
   compare-and-swap (unused → used). A second click, another user, a stale attempt or a
   passed deadline is rejected and the card says why.
3. Only a successful consume calls `submit_human_input` (surface `slack`), the same write
   path the overlay uses, with the nonce id as evidence. Spike S3 checks this path does not
   go through the gateway's unattended-pick route.

## 8. Failure and recovery

| Failure | Detection | Action |
|---|---|---|
| Daemon restarts while the Worker runs | Conductor recovery on startup: `running`, `executor: host:worker`, no live Worker | New Attempt on the same Run from the Pipeline checkpoint, `driver_bind` to the new Worker. A second crash on the same stage fails the request with `worker_crash_loop` |
| Daemon restarts while a Run is `parked` / `resume_pending` | Startup | Nothing to recover; same Attempt (§5.1). The stream projection catches up first (`pipeline-human-loop-v2.md` §4.1) |
| An inline tab's session dies | `owner_live` false | The Run pauses as today; a person can take it over in another tab (§4.1) or hand it to the background |
| The person presses Stop on an inline Run | `on_user_stop` | Pauses the Runs that session drives |
| Worker cannot get a stream | Admission (§6.2) | Waits, shown as "Waiting for capacity"; no timeout |
| Worker stalls (no completed tool call) or a stage runs past its budget | Conductor, from the Worker's events (§6.2) | Interrupt, park, open an item; the person continues, takes over or cancels |
| Run log truncated or a checksum mismatch | Reading the run log (usable §3.3.5) | The Run stops and needs a person; the Conductor shows it in Needs you and never resumes it on its own |
| Base or target branch moved before landing | `landing.py` CAS | As today: rebase rules, or `not_landed(<reason>)` |
| Placement invalid at dispatch | Re-check | `failed(<rule>)`, never re-placed |

Cancel is available to the owner until landing starts; a cancel and a Pipeline terminal
event are ordered by the Conductor's lock on the dispatch record, first one wins.

## 9. Surfaces

Every surface reads the Conductor Store and the Runs' message streams
(`pipeline-human-loop-v2.md` §4); none keeps its own copy of a Run's state. Every surface
names a Run by its title (§5.4). Mockup: `docs/design/pipeline-conductor-ui-mock.html`.

- **Pipelines overlay.** The live control surface, built for a person who goes through
  many Runs, including a day's delivered ones. Today's retro content moves to its History
  tab unchanged, with one added column (placement).
  - **Executor strip:** Chat tabs (inline Runs), Local Worker (slot used, queue length).
  - **Board** (tab Runs), one card per Run, four columns, each scrolled on its own:

    | Column | Cards | Order |
    |---|---|---|
    | Needs you | Runs with an open item | longest wait first |
    | Running | `dispatching`, `running` without an open item | stage, then start |
    | Waiting | `queued` (position), `resume_pending`, `cancelling` | `resume_pending` first, then queue order |
    | Delivered (24 h) | `done` / `failed` / `cancelled` in the last 24 h, with the five-dimension audit line | newest first |

    A card shows title, project · profile · placement, a stage bar, age, unread notices,
    and for Needs you the open item in one line with its quick actions.
  - **Review view:** opening a card shows it on the right, the board narrowing to a card
    list. It holds title, requirement, dispatch facts and tabs Thread, Changes, Review,
    Report. Previous / Next (`J` / `K`) walk the **Needs you** or the **Delivered to
    review** queue. What item cards must show and the limits on batch actions are in
    `pipeline-human-loop-v2.md` §6.
  - **Activity** tab: the aggregate feed of all streams, for audit.
  - **Schedules** tab: schedules with their last occurrence outcomes.

  Live updates come over one SSE connection opened only while the overlay is visible, on
  the `nav` pool, reading `stream.db` and the Conductor Store; no polling of git or files.
- **Chat.** No visible change for inline Runs except item cards. For background Runs: the
  confirmation card (§6.3) and one status card per Run in the originating tab.
- **Slack.** §7; the thread is a reader of the Run's stream.
- **Left nav.** The Pipelines item shows the count of open items.
- **Windows.** The overlay shows that the Pipeline is not available on Windows; the
  Conductor routes return the same "not supported" as `pipeline-v1` (usable §3.2).
- **API**, all on the `nav` pool: `POST /api/conductor/requests` (human-only),
  `GET /api/conductor/requests[/<id>]`, `POST /api/conductor/requests/<id>/cancel`,
  `GET /api/conductor/capacity`, `GET /api/conductor/stream` (SSE),
  `POST /api/conductor/runs/<id>/input` (human-only).

## 10. Pipeline five dimensions

| Dimension | Effect | Guard |
|---|---|---|
| Run Isolation | Unchanged: each Run keeps its own worktree; a background Run also has its own DDD snapshot | `driver_bind` makes exactly one session able to act for a Run |
| Quality | Unchanged: the same general Pipeline in both placements; tests are the agent's own validation reports, checked by Gate-2 (usable §3.3.3) | Landing never runs on a superseded approval (`pipeline-human-loop-v2.md` §3.4); a new Attempt submits a new TEST report on its landing tree |
| Autonomy | Background and scheduled Runs need no open tab; questions reach the person wherever they are | Only the owner submits; only a person's input resolves an item; a stalled Worker becomes a question, not a silent hang |
| Speed | One extra Run in parallel | One stream always kept for chat and Slack; the Conductor is off the send and stream paths |
| Token Cost | At most one more concurrent Run; a resume turn after a long wait re-reads the context | Overlapping schedule fires are skipped; missed fires are not replayed |

## 11. Phases and acceptance

Measure first, then build. The Conductor starts after usable S6 has landed and usable §6's
measures have been read (P9). Each phase starts with its acceptance tests written and red,
and lands as one or more ff-merged commits. Sizes are estimates of final lines including
tests.

| Phase | Scope | Acceptance | Size |
|---|---|---|---|
| 1 | Conductor Store, `register_inline`, status join, read-only API, overlay board for inline Runs, `create --title` and the title rule, the `conductor` subcommand, Windows "not supported" | An inline Run appears on the board with origin `chat`; chat send-to-first-token and stream timings are unchanged (measured before and after on the same machine); a failing registration never fails `create`; the title rule is tested for an empty title, a multilingual requirement, a duplicate title and a scheduled fire; on Windows every Conductor route and the overlay report "not supported"; a structural test finds no SwarmAI repo or project check in `core/conductor/` | ~2k |
| 2a | Owner / driver cutover on the S6 run log (§4.1); Local Worker; one slot; two-class stream admission; `create --ddd-root` with snapshots and `kind: none`; progress limits (§6.2); crash recovery; background placement from a Chat tab (§6.3) | A background `direct` Run on a non-SwarmAI sample repo reaches `land` with no tab open, its TEST validation report's `code_tree` equal to the landed tree; editing the Host DDD mid-run does not change what it reads; a project with no DDD runs and its lessons land in REPORT; a second request queues; with 2 interactive streams active the Worker waits and chat still gets a stream; killing the daemon mid-BUILD resumes as attempt 2 with a new `driver_bind` and a new TEST report; a mutating command from a session that is not the current driver is refused; a takeover in a tab supersedes an open `land_request`; a Worker whose test hangs is interrupted, parked and asks the person, and is never failed without an answer; only the person's click on the confirmation card submits, and a used, expired or stale draft submits nothing; an agent Bash call to the submit route is denied | ~4k |
| 2b | Human loop v2, all of it (`pipeline-human-loop-v2.md` §8): items, `human_input`, park and resume, delivery envelope and PostToolUse delivery, stream projection and SSE, approval outcomes, content snapshots; overlay review view and chat item cards | The red tests in `pipeline-human-loop-v2.md` §8 pass; a Worker's question parks the Run at once, freeing the slot for the next request, and answering it resumes the Run ahead of the queue with no dangling tool call (S4); an inline Run's question is an item card and its tab stays usable meanwhile; an answer with a note given in the overlay reaches the Run, which acks it, and the tab card shows it answered | ~7k |
| 3 | Slack: owner submit, confirmation card, `pipeline_submit` job and occurrence ledger, thread reader, item buttons and "Reply with note" modal, typed replies as guidance | A non-owner cannot submit; a scheduled Run fires and reports in its thread; a missed fire is `missed(host_offline)` and never submitted late; an overlapping fire is `skipped_overlap`; a replayed occurrence key is a no-op; an owner click consumes its nonce once and records `human_input`, a second click, another user or a passed deadline is rejected; a typed reply never resolves an item | ~3.5k |

Total about 16.5k lines including tests, roughly 3 to 4 weeks at the current review
cadence, counted from the end of usable S6.

### 11.1 Spikes before code

| ID | Question | Blocks |
|---|---|---|
| S1 | Can a headless `SessionUnit` `pipeline:<run_id>`, bound by `driver_bind`, run `"$SWARM_BACKEND_BIN" pipeline-v1` as the current driver (the prewarm session-id supply included) and land a `direct` Run, the way Slack channel sessions run without a tab? | 2a |
| S2 | Memory, stream use and per-stage wall clock of one Pipeline Run on the Host, with native tests; confirms one Worker fits next to chat and replaces the §6.2 progress-limit defaults | 2a |
| S3 | How an owner's Slack button press reaches `submit_human_input` without the gateway's unattended-pick path | 3 |
| S4 | A Worker's decision request ends the turn → park (COLD) → answer → `--resume` with a new turn carrying the input; the resumed SDK conversation has no unmatched `tool_use`; guidance delivered by PostToolUse `additionalContext` during a long BUILD | 2b |

If S1 fails, Phase 2a stops and the design is revised; the jobs `--bare` path is not a
fallback. If S4 fails, Phase 2b stops: a Worker must not wait for a person inside a tool
call.

## 12. Cost on hot paths

- Chat send and stream: no new work. Inline registration is one file write on the
  Conductor executor after `create` returns. Two-class admission is one locked counter
  step, the same place today's poll runs.
- Daemon idle: no work without open overlays, a running Worker or a due schedule.
- Each file write: none added outside the Conductor Store and the Run's own files.

Phase 1 measures chat send-to-first-token and stream timings before and after; a
regression blocks the phase.

## 13. Later work, outside V1

### 13.1 Running Runs on another machine

Started only with evidence that one Worker is not enough: the Conductor Store records
queue wait for every background request, so the decision reads that number. If it is
built, the direction is **federated Hives**, in its own design:

- each Hive is a full SwarmAI daemon (`SWARMAI_MODE=hive` already exists) whose own
  Conductor creates and drives its Runs and keeps their logs, exactly as the Host does;
- the Host sends a Hive a request and reads that Hive's Run streams read-only, merging
  them into the overlay; a person's input for a Hive Run goes to that Hive's
  `submit_human_input`;
- code comes back as a branch the Host fetches and lands with today's `landing.py`.

This avoids a central lease service; the trade-off is that a dead Hive's Runs are
resubmitted by a person, not taken over automatically. Internal projects need a Cloud
Desktop Hive with headless Midway, which is its own spike.

### 13.2 Organization Run store, Trace and Eval

A separate project. It reads finished Runs' records offline and is never in the delivery
path: no Run waits for it, and a Run behaves the same with it down.

### 13.3 The HLD

The HLD (`project-agent-delivery-os-hld.md`) points its execution-plane phasing here.
Its Central Service, Host-to-Hive exchange, org store and protocol cutover (Phase 1b) are
not part of this V1 and wait for §13.1 and §13.2.
