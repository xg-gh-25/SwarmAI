---
title: "Pipeline Run Conductor V1: Inline, Local Background, Hive and Slack"
created: 2026-10-23
updated: 2026-10-23
project: SwarmAI
status: draft
tags: [pipeline, conductor, hive, slack, scheduling, execution-plane]
related:
  - docs/design/project-agent-delivery-os-hld.md
  - docs/design/pipeline-run-worktree.md
  - docs/design/pipeline-v1-human-loop.md
  - Runtime DDD: ${SWARM_WORKSPACE}/Projects/<project>/bindings.yaml
---

# Pipeline Run Conductor V1

## 1. Summary

This design delivers the execution plane of the Organizational Autonomy OS HLD
(`project-agent-delivery-os-hld.md` §6.4–§6.6, §7, §17) and nothing above it. One
deterministic **Pipeline Run Conductor** in the Host daemon accepts a Pipeline request,
places it, dispatches it, tracks it and lands its result. V1 supports four entry
paths:

| # | Entry | Placement | Exists today |
|---|---|---|---|
| E1 | Chat tab, inline | `inline_local` | Yes. V1 only registers it with the Conductor |
| E2 | Background run on the Host | `background_local` | No |
| E3 | Run on a named Hive | `remote_hive` | No |
| E4 | Slack, immediate or scheduled | `background_local` or `remote_hive` | No |

The Pipeline itself does not change: one requirement, one owner, one continuous context
and one code tree per Run. The Conductor decides **where and when** a Run executes. The
Pipeline decides **how** it executes and whether it passed.

## 2. V1 premises (agreed 2026-10-23)

These premises bound the design. Anything outside them is rejected with a reason.

**P1. DDD sources.** A Run's DDD comes from exactly one of two origins:

| Origin | Which DDDs | How a Run gets it |
|---|---|---|
| `host` | Only the built-in SwarmAI DDD (`SwarmWS/Projects/SwarmAI/`) | The Host takes a digest-pinned snapshot and ships it with the Run |
| `repo` | Every other DDD. Each lives in a remote repo (GitHub, or an internal git such as IVTHubSpec) and the Host syncs its copy from there | The Host pins `{repo, path, commit}` at submit; the executor fetches that commit |

A non-SwarmAI DDD with no declared remote repo cannot run off-Host.

**P2. Classification is declared in the DDD.** Each binding in `bindings.yaml` already
declares `kind: internal | external`, and `ddd_bindings.classify_project()` is the single
source for a project's class (any internal binding makes the project internal). V1 keeps
it as the only source. It adds no project-level classification scalar, because
`classify_project()` documents that a stored project-level value drifts from the bindings.
The only new declaration is `ddd_origin.access` for the repo that holds the DDD itself
(§5.2), which `classify_project()` also reads.

**P3. Hive types.** Exactly two Hive types are supported:

| Project class | Allowed Hive | Never |
|---|---|---|
| `internal` | An Amazon internal **Cloud Desktop** registered as a Hive | EC2 |
| `external` | An **EC2** Hive (existing `SWARMAI_MODE=hive`) | Cloud Desktop |
| `none` (no bindings) | No Hive; `background_local` or `inline_local` only | Any Hive |

Containers, other clouds and other machine types are out of scope.

**P4. Concurrency.**
- Local: inline runs keep their current behaviour. In addition, **at most one**
  background Local Worker runs at a time. A second background request waits in the
  Host queue.
- Remote: each Hive runs as many Runs as its **reported** capacity allows. Capacity is
  computed on the Hive from its own measured resources, so it varies by machine type.
  A Hive that has not reported capacity is treated as one slot.

## 3. Non-goals

- Project Agents, Intent triage, Run Graph / DAG of Runs, Asset Registry, Offline Eval,
  ADR, Evolution. They depend on this plane; this plane does not depend on them.
- Automatic Hive selection and automatic cross-Hive failover. The user or the Slack
  request names the Hive.
- Replicating schedules to a Hive so they fire while the Host is offline.
- Launching Cloud Desktops. V1 registers one the user already has.
- Pushing from a Hive. Every landing happens on the Host.
- Backward compatibility for runs created before the Phase 4 protocol cutover.
- Changing the Chat inline experience. Inline registration must be invisible in chat.

## 4. Architecture

```text
 Chat tab ──(pipeline_v1.py create)──┐
 Dashboard "Run in background" ──────┤
 Slack session (owner only) ─────────┼──► Conductor.submit() ──► Conductor Store
 Host Job Scheduler (pipeline_submit)┘          │
                                                ├─ inline_local      → existing chat session owns the Run
                                                ├─ background_local  → Local Worker (1 slot)
                                                └─ remote_hive       → Run Exchange → named Hive Worker
                                                                         │
                         Host acceptance + landing (CAS) ◄── result_candidate
```

The Conductor is a module in the Host daemon (`backend/core/conductor/`). It is not an
LLM and never calls one. It runs off the event loop on its own executor and is never on
the chat send path or the stream path.

### 4.1 Who owns each fact

Each fact has exactly one owner. Every other view is a projection.

| Fact | Owner | Readers |
|---|---|---|
| Stage, verdict, findings, signed events of a Run | Pipeline v1 run store (`core/run_protocol/`) | Conductor, UI |
| Request, origin, placement, queue position, Attempts, leases | **Conductor Store** (new) | UI, Slack replies, Scheduler |
| Schedule definition and fire times | Host Job Scheduler (`user-jobs.yaml`, new job kind `pipeline_submit`) | Conductor |
| Project class | `bindings.yaml` via `classify_project()` | Conductor placement check |
| DDD origin | `bindings.yaml` `ddd_origin` (new, §5.2) | Conductor DDD pin |
| Hive identity, type, capacity, heartbeat | Hive registry (extends `routers/hive.py` store) | Conductor dispatch |
| Landing on the Host repo | `run_protocol/landing.py` | Conductor |

The Conductor never copies Pipeline stage state into its own store. The status a user
sees is a join of the Conductor record and the Pipeline run record, computed on read.

### 4.2 Conductor Store

A filesystem store, like the run protocol store, under
`~/.swarm-ai/state/conductor/`:

- `requests/<request_id>.json`: the immutable submitted request.
- `runs/<run_id>.json`: the mutable dispatch record, written with `utils.file_lock`.
- `events.jsonl`: an append-only log of dispatch transitions, for audit and recovery.

It does not use `data.db`, so chat history storage is untouched.

## 5. Data model

### 5.1 Request and dispatch record

```yaml
request:                       # immutable once accepted
  request_id: req_<ulid>
  project: SwarmAI
  requirement: "<user's words, stored verbatim as data>"
  profile: direct | bugfix | full
  placement:
    mode: inline_local | background_local | remote_hive
    hive_id: null | hive_<id>  # required when mode = remote_hive
  origin:
    kind: chat | dashboard | slack | schedule
    ref: <session id | slack channel+thread ts | job id>
    submitted_by: <owner identity>
  ddd_ref:                     # pinned at submit, §5.2
    origin: live | host | repo # live = inline_local record only (§5.2)
    digest: sha256:...         # digest of the DDD tree the Run reads
    clone: <git URL>           # repo origin only
    path: <dir in repo>
    commit: <sha>
  code_ref:                    # per bound repo, pinned at submit
    - repo: swarmai
      base_commit: <sha>
  created_at: <iso>

dispatch:                      # mutable, owned by the Conductor
  run_id: <pipeline v1 run id, set when the Run is created>
  state: queued | dispatching | running | candidate_ready | landing
       | done | failed | cancelled
  attempts:
    - attempt: 1
      executor: host:inline | host:worker | hive_<id>
      lease_epoch: 1           # revoked only by the Host (§9)
      started_at: <iso>
      ended_at: null
      end_reason: null | completed | lease_lost | crashed | cancelled
```

`state` describes dispatch only. Whether the Run passed, and whether it is waiting for a
person, is Pipeline state (`core/run_protocol/lifecycle.py` `STATUSES`), read from the
Pipeline store. Schedule outcomes such as `missed` and `skipped_overlap` belong to
schedule occurrences (§8.2), not to a request, because no request exists for them.

Legal dispatch transitions (any other write is rejected):

| From | To | Written by |
|---|---|---|
| `queued` | `dispatching`, `cancelled`, `failed` | Conductor (slot free / cancel / re-check failed) |
| `dispatching` | `running`, `queued`, `failed` | Conductor (executor accepted / executor refused before start / rule failed) |
| `running` | `candidate_ready`, `running` (new Attempt), `failed`, `cancelled` | Conductor on executor events |
| `candidate_ready` | `landing`, `running` (new Attempt for rebase), `failed` | Conductor (Phase 4+) |
| `landing` | `done`, `failed` | Landing result |

Before Phase 4, an inline or Local Run goes `running` → `done` directly: today's v1 `land`
both judges and lands, so there is no candidate step. Every transition is an event in
`events.jsonl` with a key `(request_id, attempt, lease_epoch, seq)`; replaying a key that
was already applied is a no-op.

What a user sees is computed on read:

| Dispatch | Pipeline status | Shown as |
|---|---|---|
| `queued` / `dispatching` | (no Run yet) | Queued (position N) / Starting |
| `running` | `active`, `reflecting` | Running · stage |
| `running` | `awaiting_human` | Waiting for you |
| `running` | `paused` | Paused |
| `candidate_ready` / `landing` | any | Landing |
| `done` | `completed` | Delivered, with the Pipeline landing (`landed`, `not_landed(<reason>)`, `human_override`, `no_code`) |
| `failed` / `cancelled` | any or none | Failed (reason) / Cancelled |

A Pipeline terminal status (`completed`, `abandoned`, `failed`) with dispatch still
`running` means the Worker has not reported yet; the Conductor closes the request from
the Pipeline record on its next pass.

### 5.2 DDD origin declaration

`bindings.yaml` gains one optional top-level field. It states where the DDD itself lives;
`bindings[]` keeps stating which code repos the DDD governs.

```yaml
# Built-in SwarmAI DDD only
ddd_origin:
  kind: host

# Every other DDD
ddd_origin:
  kind: repo
  clone: "<git URL of the repo holding the DDD, e.g. IVTHubSpec>"
  access: internal          # internal | external, declared like bindings[].kind
  path: ddd/IVTHub          # six-section DDD directory inside that repo
  branch: main
```

The DDD repo is often not one of the governed code repos (a spec repo such as
IVTHubSpec), so `ddd_origin` carries its own `clone` and its own explicit `access`.

Validation (in `ddd_bindings.py`). `ddd_origin` is a new field: today `BindingsDoc`
silently drops unknown keys, so the model must declare it, or the field is lost.

- `kind: host` is accepted only for the SwarmAI DDD (`.project.json` id
  `swarmai-default`). Any other DDD declaring `host` is rejected.
- `kind: repo` requires `clone`, `access` and `branch`, and a relative `path` confined to
  the repo (the same `_confine` check `sync_back` uses). A `clone` URL that embeds
  credentials (`user:pass@`, tokens in the query) is rejected.
- A DDD with no `ddd_origin` can run `inline_local` and `background_local` only.

`classify_project()` stays the single source of the project class and gains one rule:
the project is `internal` if any binding is internal **or** `ddd_origin.access` is
internal. An internal spec repo therefore keeps the Run on a Cloud Desktop even when every
code repo is external.

**Pinning and materializing.** A Run executes against a DDD revision it owns, not
against the mutable Host copy:

| Placement | DDD the Run reads |
|---|---|
| `inline_local` | The live Host copy, as today. `ddd_ref` records `kind: live` and the digest at create. It is a record, not a pin: an inline session may cultivate its own DDD mid-run, and V1 does not change that |
| `background_local`, `remote_hive` | An immutable copy materialized at dispatch into the Run's directory (`<run dir>/ddd/`, read-only), with its digest in `ddd_ref`. The Pipeline contract is created pointing at that directory, so the Run cannot read another revision |

The Pipeline gains one input for this: `pipeline_v1.py create --ddd-root <dir>`. Today
the Pipeline resolves the DDD through `ddd_paths`; with `--ddd-root` it resolves from the
given directory instead, and the contract records the digest. That change lands in
Phase 2.

How the copy is made:

- `repo` origin: the Conductor resolves `branch` to a commit with `git ls-remote` at
  submit, and the executor fetches exactly that commit's `path`. Host-local DDD edits that
  are not in that commit are not in the Run, and the submit reply says so when the Host
  copy differs.
- `host` origin (SwarmAI only): the Conductor copies the Host DDD through an explicit
  manifest and records the digest.

**What may leave the Host for EC2.** The `host` snapshot is the only Host-authored
content that goes to an EC2 Hive, and "external" classification does not prove it is safe.
So:

- The manifest is an allowlist: `*.md`, `*.yaml`, `*.json` under the six DDD sections
  and `bindings.yaml`. Derived files (`code-intel.json`), runtime state, binaries and any
  file that does not decode as UTF-8 are excluded, and an unexpected file type fails the
  build of the snapshot instead of being skipped.
- `ddd_packager.content_safety_scan(tree, external=True)` runs over the built snapshot.
  Any finding blocks the remote placement before anything is uploaded, and lists the files.
- A test runs the manifest and the scan against the real SwarmAI DDD. If that DDD
  currently contains internal content, the test is expected to fail, and SwarmAI remote
  Runs stay blocked until the content is moved out. This is a correct outcome, not a bug
  to suppress.

### 5.3 Placement rules

The Conductor checks placement at submit and rejects a violating request with the rule
that failed. It never silently changes placement.

| Project class (`classify_project`) | `inline_local` | `background_local` | EC2 Hive | Cloud Desktop Hive |
|---|---|---|---|---|
| `internal` | yes | yes | **reject** | yes, if `ddd_origin` is set |
| `external` | yes | yes | yes, if `ddd_origin` is set | **reject** |
| `none` | yes | yes | reject | reject |

The project class is computed again at dispatch. If `bindings.yaml` changed class between
submit and dispatch, the request fails with `classification_changed` and is not retried.

## 6. Execution modes

### 6.1 `inline_local` (E1, exists)

Unchanged for the user. `pipeline_v1.py create` calls `Conductor.register_inline()` after
the run is created: one file write off the event loop, recording origin `chat`, the
session id and `executor: host:inline`. The chat session stays the Run's signed owner.
A registration failure is logged and never fails the create; the Run is then simply
absent from the Conductor view. Inline runs do not count against the background slot.

### 6.2 `background_local` (E2)

**One Local Worker slot.** `background_local_slots` is fixed at 1 in V1 and is not a
setting. A request that finds the slot busy stays `queued`, in submit order.

**The Worker is a real app session.** Pipeline v1 binds a Run to a signed owner session
and issues hook tokens through `run_owner_gate`, which keys on `SWARM_APP_SESSION_ID`. The
`jobs/executor.py` agent_task path spawns `claude --print` (adding `--bare`, which skips
hooks, when the CLI supports it). Either way it is not a hooked app session and cannot
hold a Run; it would end in `not_landed(needs_hooked_session)`. So the Worker
is a headless `SessionUnit` with app session id `pipeline:<run_id>` and the full hook
chain from `hook_builder.build_hooks`, the same way channel (Slack) sessions run without
a tab. The Worker:

1. creates the Run with `pipeline_v1.py create --session-id pipeline:<run_id>` and the
   pinned requirement, profile and repos;
2. drives the Pipeline exactly as an inline session would;
3. ends when the Run reaches a terminal Pipeline state, then releases its session through
   `LifecycleManager` so post-session hooks run normally.

The jobs `agent_task` path is not reused for Pipelines and is not patched.

**Interactive capacity is reserved.** The daemon caps concurrent streams at
`SessionUnit.MAX_CONCURRENT_STREAMS` (default 3). Today's admission
(`_await_streaming_slot`) is a soft poll that lets a stream through anyway after 120 s, so
it cannot reserve anything. Phase 2 replaces it with one atomic admission counter that
has two priority classes:

- **interactive** (chat, Slack): admitted while total streams < cap, as today, including
  the 120 s fall-through, so chat behaviour does not change;
- **background** (Local Worker): admitted only while total streams < cap − 1, decided and
  counted in the same locked step. A background stream waits with no timeout, and its
  wait shows as "Waiting for capacity" on the Pipelines page. It never takes the last
  slot and never preempts an interactive stream.

**Human questions.** A Worker Run that needs a person reaches Pipeline status `awaiting_human`. The
decision card is routed to the request's origin (§8.3). The Worker session stays alive
while waiting and holds its epoch, so a long wait is never treated as a crash.

### 6.3 `remote_hive` (E3)

The request names one Hive. The Conductor checks the placement rules (§5.3), that the Hive
is registered, healthy and of the right type, and that it has a free slot. Otherwise the
request stays `queued` behind that Hive; it is never moved to another Hive.

**Hive types.**

- **EC2** (external). Existing provisioning in `routers/hive.py`, running
  `SWARMAI_MODE=hive`.
- **Cloud Desktop** (internal). The user installs the hive build on their own Cloud
  Desktop and registers it with a one-time pairing code issued by the Host. V1 adds a
  `register` path to the Hive registry next to the EC2 `create_instance` path; it does not
  launch, stop or delete the machine.

**Capacity.** On every heartbeat the Hive reports `slots`, computed on the Hive from its
own `ResourceMonitor.spawn_budget()` and a per-Run memory figure measured in Phase 4 (§12).
The Conductor dispatches at most `slots` concurrent Runs to that Hive. There is no
instance-type table on the Host. A Hive with no report counts as one slot.

**Worker on the Hive.** The Hive runs the same Worker as §6.2 (headless `SessionUnit`,
full hooks, `pipeline:<run_id>`), against:

- code: each bound repo cloned or fetched on the Hive at the pinned `base_commit`;
- DDD: the shipped snapshot (`host`) or the pinned commit of `ddd_origin` (`repo`);
- credentials: the Hive's own. An internal Run needs Midway and internal git access on
  the Cloud Desktop; the Host never sends credentials.

**Results.** A Hive never pushes and never lands. It returns a `result_candidate`
through the Run Exchange, and the Host accepts and lands it (§7).

**Signing authority on a Hive.** A Pipeline v1 run signs its events with one per-run
HMAC key (`authority.key`); whoever holds it can sign as any producer, including `hook`.
That key therefore never leaves the machine that created it:

- The Hive is a full SwarmAI daemon. It creates the Run in its own run store, with its own
  `authority.key`, and its own hook chain signs as `hook` exactly as on the Host. Nothing
  signed on the Host is sent to a Hive, and the Host never sends its keys.
- At registration the Hive generates an Ed25519 key pair; the Host stores the public key in
  the Hive registry. Every `result_candidate` and every outbox event is signed with that
  key over `(hive_id, request_id, attempt, lease_epoch, seq, payload digest)`.
- The Host verifies that signature, the epoch and the pinned `base_commit` / `ddd_ref`. It
  then re-runs, on the Host, the test selection recorded in the candidate against the
  candidate applied on the pinned base. Only after that can the candidate be accepted.

The trust model is explicit: a registered Hive is the owner's own machine. The Host trusts
its review verdict, and re-checks what can be checked deterministically (identity,
freshness, that the patch applies, tests). A Hive key is revoked by deregistering the Hive;
candidates signed with a revoked key are rejected.

## 7. Run Exchange and completion protocol

### 7.1 Run Exchange (Host ↔ Hive)

All Host–Hive traffic for Runs goes through one Exchange API on the Hive. **The Host
always initiates.** A laptop Host is not reachable from EC2, and the Host may be offline,
so the Hive never calls the Host; it buffers its output in an outbox.

| Call (Host → Hive) | Purpose |
|---|---|
| `POST /api/exchange/runs` | Deliver a request bundle: request, `ddd_ref`, `code_ref`, DDD snapshot if `host`, attempt and lease epoch |
| `GET /api/exchange/outbox?after=<seq>` | Read ordered events: started, stage changed, `awaiting_human`, heartbeat with `slots`, `result_candidate`, failed |
| `POST /api/exchange/runs/<id>/answer` | Deliver a human answer the Host collected |
| `POST /api/exchange/runs/<id>/cancel` | Cancel, or revoke a stale epoch |

- Every outbox event carries `run_id`, `attempt`, `lease_epoch` and a monotonic `seq`.
  The Host stores the last applied `seq` per Hive and drops events from a stale epoch.
- Polling runs on the Conductor executor: every 10 s while that Hive has a live Run,
  every 60 s otherwise. A Host restart resumes from the stored `seq`.
- Transport and auth:
  - EC2: the existing path (CloudFront, then Caddy basic auth), plus a per-Hive Exchange
    token issued at registration.
  - Cloud Desktop: reachable from the Host only over the corporate network. How it is
    reached (direct port or tunnel) is decided in spike S4 (§12) before Phase 4b.
- Credentials (prerequisite of Phase 4a). Today `routers/hive.py` keeps the Caddy basic
  auth password and AWS access-key configuration as plaintext SQLite fields, and the
  instance detail endpoint returns the password. Phase 4a moves every Hive credential
  (basic auth, AWS keys, Exchange token) into the Host's restricted secret store, keeps
  only a reference in SQLite, and changes every endpoint to return credential metadata
  (present / rotated at), never a value. Credentials are never logged. Exchange tokens
  rotate on re-registration and on demand; deregistering a Hive revokes its token and its
  Ed25519 key together.

### 7.2 Completion protocol (one cutover, Phase 4)

Today the combined v1 `land` event both judges and lands. That works while the executor
and the landing repo are on the same Host. A Hive breaks that assumption, so Phase 4
replaces it, once and for every placement, with the four facts from the HLD:

1. `pipeline_verdict`: the Pipeline's quality result, immutable once written.
2. `result_candidate`: the patch series and evidence a Run offers for landing.
3. `result_acceptance`: the Host's decision to accept that candidate (automatic on PASS
   under the delivery contract, or a person's decision).
4. `integration_landing`: the Host landing the candidate into the target repo.

Acceptance and landing reuse the existing v1 landing machinery
(`run_protocol/landing.py`), which already handles multi-repo runs, crash adoption and
partial landings, rather than inventing a second one. Git cannot move several repositories'
refs atomically, so "atomic" means per repository, with a journal across them:

1. **Journal first.** Before touching any repo, the Host writes a landing journal entry
   keyed by the candidate digest: the ordered repo list (sorted by repo name), each repo's
   expected branch head and the prepared commit to land.
2. **Ordered locks.** Repo locks are taken in that order, so two landings cannot deadlock.
3. **Per-repo compare-and-swap.** Each repo fast-forwards only if its branch head still
   equals the expected head; this is the existing `merge --ff-only`.
4. **Crash reconciliation.** On restart the Host reads unfinished journal entries and, per
   repo, checks git: if the branch head already equals the prepared commit, that repo is
   adopted as landed (the existing `adoptable` check); otherwise it is still pending.
5. **Partial landing is terminal.** If some repos landed and a later one fails its CAS, the
   Run ends `not_landed(partial_land)`, as today. It is reported with which repos landed and
   which did not, and is never retried automatically, because the half that landed is no
   longer the reviewed change. A person decides.

If the first repo's CAS fails (nothing landed yet), a new Attempt may rebase the candidate;
on conflict the request ends `failed(landing_conflict)`. Nothing is force-landed.

Inline and Local Worker runs use the same four facts after the cutover; their candidate
is local, so the exchange step is a no-op. Runs created before the cutover are not
migrated.

Phases 1 to 3 keep today's v1 `land`. The cutover is deliberately not done first: the
Local Worker runs in the same daemon, with the same hooks, signing and landing as an
inline run, and does not need it.

## 8. Slack (E4)

### 8.1 Who may submit

Only the configured SwarmAI owner may submit a Pipeline from Slack. Other users in an
allowlisted channel can talk to the bot as today but cannot start a Run. The requirement
text is stored verbatim as data and is never used as an instruction to the Conductor.

### 8.2 Immediate and scheduled runs

1. The owner asks in Slack, for example "run a bugfix pipeline on SwarmAI on hive-ec2-1:
   …" or "every weekday 09:00, run …".
2. The Slack session agent turns the message into a draft request with
   `pipeline_conductor.py draft` (a Host CLI over the owner-only socket). Draft runs the
   placement rules and the DDD pin, so an invalid request is rejected here, with its reason.
3. The bot replies with a confirmation card listing project, profile, placement, DDD ref,
   base commits and, for a schedule, the cron expression and timezone.
4. The owner confirms on the card. Only then:
   - immediate: `Conductor.submit()` queues the request;
   - scheduled: the Conductor writes a `pipeline_submit` job into the Host Job Scheduler.
     The job stores a request template, not a pinned request.
5. Replies go to the same Slack thread: queued, started, waiting for you, delivered,
   failed. One message is edited in place for progress; terminal states post a new reply.

`pipeline_submit` is a new job type. It is added to `JobType` in `jobs/models.py` and
handled by the scheduler without spawning any CLI or LLM. When it fires, the scheduler
calls `Conductor.submit()` with the template; the Conductor pins `ddd_ref` and `code_ref`
at that moment, so every fire is a new request on the current code.

**Occurrences.** The generic cron path (`jobs/cron_utils.py`) catches up one missed
occurrence within seven days and only reports a boolean. That is wrong for Pipelines, so
`pipeline_submit` does not use it. The Conductor keeps an occurrence ledger
(`state/conductor/occurrences.jsonl`), one row per scheduled time:

```yaml
occurrence:
  key: <job_id>@<scheduled_utc>     # idempotency key; a second fire with the same key is a no-op
  job_id: <id>
  scheduled_local: 2026-10-26T09:00 # in the schedule's IANA timezone
  outcome: submitted | missed | skipped_overlap | rejected
  request_id: <id> | null
  reason: <text> | null
```

- Every schedule stores an IANA timezone; there is no default.
- Due times are computed in that timezone. A local time that does not exist (spring
  forward) is recorded `missed(dst_gap)`; a time that happens twice (fall back) fires once,
  on its first occurrence.
- A scheduled time that passed while the Host was off is recorded `missed(host_offline)`
  when the Host is back and reported in Slack. It is never submitted late.
- If the previous occurrence's request is not terminal, the new one is
  `skipped_overlap` and reported. Runs of one schedule never pile up.
- A template that fails the placement rules or the DDD pin at fire time is `rejected`
  with the reason.

V1 does not replicate schedules to a Hive.

### 8.3 Human answers for background and remote Runs

A Run in `awaiting_human` posts its decision card to its origin:

| Origin | Where the card goes |
|---|---|
| chat | that chat tab (as today) |
| dashboard | the Pipelines page |
| slack, schedule | the request's Slack thread, plus the Pipelines page |

Pipeline v1 counts only a person's pick as a `human_answer`, never an unattended pick by
the channel gateway. Today `answered_by="person"` is metadata supplied by the caller, and
the Slack adapter's owner-click check covers only sender-approval cards, not Pipeline
decisions. So a Slack answer needs its own protocol:

1. When the Host posts a decision card to Slack, it creates one nonce per option, bound
   to: owner Slack user id, channel and thread ts, `run_id`, question / tool-use id, option,
   `lease_epoch`, and an expiry (the question's own 4 h answer timeout).
2. The nonce is stored on the Host only; the Slack button carries the nonce id, never the
   binding.
3. On a click, the Slack adapter checks that the clicking user is the bound owner and the
   channel/thread match, then consumes the nonce with a compare-and-swap (unused → used).
   A second click, another user, a stale epoch or an expired nonce is rejected and the card
   says why.
4. Only a successful consume writes the `human_answer`, through the same path the
   dashboard uses, with `answered_by: person` and the nonce id as evidence.

A typed Slack reply is shown to the agent as context but is never an answer. Phase 3's
answer acceptance is gated on this protocol being implemented and tested; spike S5 checks
how step 4 joins `ask_question_gate`'s answer record.

## 9. Failure and recovery

Each Attempt holds a lease with an epoch. Only the holder of the current epoch may write
results for the Run. The Run itself keeps its continuous context inside one Attempt; a new
Attempt resumes from the Pipeline's own checkpoint (its signed chain and run worktree), or
starts the Run fresh when there is no usable checkpoint. It never splits the Run across
executors.

| Failure | Detection | Action |
|---|---|---|
| Host daemon restarts during a Local Worker Run | Conductor recovery on startup: `running` with `executor: host:worker` and no live Worker session | New Attempt (epoch +1) on the same Run, resuming from the Pipeline checkpoint. A second crash on the same stage fails the request with `worker_crash_loop` |
| Inline chat session dies | Existing v1 owner takeover (`run_owner_gate`, signed `owner_transfer`) | Unchanged. The Conductor only records the new owner |
| Hive unreachable | The Host is online and every poll of that Hive has failed for 10 min | Request moves to `failed(lease_lost)` and the epoch is revoked. No automatic move to another Hive. The owner may resubmit to a Hive they choose |
| Hive returns after the epoch was revoked | Outbox events carry the revoked epoch | Dropped. The Hive is told to cancel its stale Attempt |
| Host offline while a Hive Run continues | Nothing to detect; the Host is the only side that can revoke | The Hive keeps running and buffers its outbox. Host downtime never counts toward `lease_lost`; the Host reads the outbox on return |
| Target branch moved before landing | CAS fails on the first repo in `integration_landing` | New Attempt rebases the candidate; on conflict the request ends `failed(landing_conflict)` and is reported |
| Landing crashed or a later repo's CAS failed | Unfinished landing journal entry (§7.2) | Reconcile against git; adopt repos already at the prepared commit; if some repos landed and others cannot, end `not_landed(partial_land)` for a person to decide |
| Forged, replayed or stale Hive result | Ed25519 signature, epoch or `seq` check fails | Rejected; never accepted or landed |
| Placement or DDD pin invalid at dispatch | Re-check at dispatch | `failed(<rule>)`, never re-placed |
| DDD snapshot manifest or content-safety finding | Manifest build or `content_safety_scan(external=True)` at submit | Remote placement rejected before upload, with the files listed |

Cancel is always available to the owner. It sets `cancelled`, ends the Worker or sends
`cancel` to the Hive, and lets the Pipeline abandon the Run with reason `cancelled`.

## 10. Surfaces

- **Pipelines page.** Each Run gains placement, executor, queue position, attempt count
  and origin. The Local Worker slot and each Hive's slots are shown as used / total.
  A "Run in background" action submits `background_local`; a Hive picker submits
  `remote_hive`, listing only Hives the placement rules allow for that project.
- **Chat.** No visible change for inline runs.
- **Slack.** §8.
- **API.** `POST /api/conductor/requests`, `GET /api/conductor/requests[/<id>]`,
  `POST /api/conductor/requests/<id>/cancel`, `GET /api/conductor/capacity`.
  Endpoints run on the `nav` pool, never on the chat path.

## 11. Pipeline five dimensions

| Dimension | Effect of this design | Guard |
|---|---|---|
| Run Isolation | Unchanged locally: each Run keeps its own worktree. On a Hive each Run gets its own clone at the pinned base | Placement rules keep internal code off EC2; Hives never push; landing is a CAS on the Host |
| Quality | Unchanged. The same Pipeline, gates and adversarial review run in every placement | `pipeline_verdict` is immutable; the Host never lands without a PASS verdict or a recorded human acceptance |
| Autonomy | Background, remote and scheduled Runs need no open chat tab. Human questions reach the origin | Only the owner can submit; only a person's pick counts as an answer |
| Speed | One extra local Run in parallel, plus Hive slots | One stream stays reserved for chat and Slack; the Conductor is off the send and stream paths |
| Token Cost | One more concurrent local Run at most. Hive Runs use Hive credentials and budgets | Schedule overlap is skipped, not stacked; missed fires are not replayed |

## 12. Phases and acceptance

Measure first, then build. Each phase starts with its acceptance tests written and red.

| Phase | Scope | Acceptance |
|---|---|---|
| 1 | Conductor Store, `submit` / `register_inline`, status join, API, Pipelines page columns | An inline Run appears in the Conductor view with origin `chat`; chat send and stream timings are unchanged (measured before and after); a failing registration never fails `create` |
| 2 | Local Worker (headless `SessionUnit`, `pipeline:<run_id>`), one slot, atomic two-class stream admission, `pipeline_v1.py create --ddd-root` with a materialized immutable DDD, crash recovery | A background `direct` Run reaches `land` with no tab open; its contract records the DDD digest and editing the Host DDD mid-run does not change what it reads; a second request queues; with 2 interactive streams active the Worker waits and chat still gets a stream; killing the daemon mid-BUILD resumes on restart as attempt 2 |
| 3 | Slack submit (owner only), confirmation card, `pipeline_submit` job, thread updates, Slack answer button | A non-owner cannot submit; a scheduled Run fires and reports in its thread; a missed fire is recorded `missed(host_offline)` and never submitted late; an overlapping fire is `skipped_overlap`; a replayed occurrence key is a no-op; an owner button press consumes its nonce once and records `human_answer`, while a second click, a non-owner click or an expired nonce is rejected |
| 4a | Hive credentials moved to the secret store; four-fact completion protocol with landing journal for every placement; Run Exchange with Ed25519-signed outbox; EC2 Hive Worker; Hive capacity reporting; SwarmAI DDD snapshot manifest and external scan | No Hive endpoint returns a credential value; an external Run on EC2 returns a signed candidate the Host verifies, re-tests and lands; a forged or revoked-epoch event is dropped; an internal project is rejected for EC2; a snapshot scan finding blocks upload; a crash mid-landing reconciles without double-landing; inline and Local Runs land through the new facts |
| 4b | Cloud Desktop registration and Worker; `ddd_origin: repo` fetch with internal git | An internal Run with an IVTHubSpec-hosted DDD runs on a registered Cloud Desktop and lands on the Host; an external project is rejected for a Cloud Desktop |
| 5 | Slack scheduling to a named Hive | A scheduled Run targets the named Hive, reports in its thread and lands on the Host |

### 12.1 Spikes before code

| ID | Question | Blocks |
|---|---|---|
| S1 | Can a headless `SessionUnit` with app session id `pipeline:<run_id>` get hook tokens from `run_owner_gate` and land a `direct` Run, the way channel sessions run without a tab? | Phase 2 |
| S2 | Memory and stream use of one Pipeline Run on the Host, measured; confirms that one extra Worker fits next to chat | Phase 2 |
| S3 | Per-Run memory on a Hive, measured, to turn `spawn_budget()` into `slots` | Phase 4a |
| S4 | How the Host reaches a Cloud Desktop over the corporate network (direct port or tunnel), and how the Exchange is authenticated there | Phase 4b |
| S5 | How an owner's Slack button press becomes a `human_answer` without the gateway's unattended path | Phase 3 |
| S6 | Headless Midway and internal git on a Cloud Desktop. `ddd_bindings.bind_repo` defers internal bindings today ("needs Brazil+Midway headless auth") | Phase 4b |

If S1 fails, Phase 2 stops and the design is revised; the jobs `--bare` path is not used
as a fallback. If S6 fails, internal remote Runs stay unsupported and Phase 4b is
deferred; external Runs are unaffected.

## 13. Cost on hot paths

- Chat send and stream: no new work. Inline registration is one file write on the
  Conductor executor after `create` returns.
- Daemon idle: one poll per registered Hive per 60 s (10 s while it has a live Run), on the
  Conductor executor.
- Each file write: none added outside the Conductor Store and the Run's own files.

Phase 1 measures chat send-to-first-token and stream timings before and after the change
on the same machine; a regression blocks the phase.

## 14. Changes to the HLD

The HLD (`project-agent-delivery-os-hld.md`) originally put the protocol cutover
(Phase 1b) before local background execution. It is now edited to match this design:
Phase 1b ships with the Hive executor (Phase 4), local background execution runs on the
current v1 landing until then, and execution-plane phasing points here. The move is sound
only under two conditions, both kept here: Phases 1 to 3 create no candidate or acceptance
authority, and Phase 4 moves every placement to the four facts in one change.
