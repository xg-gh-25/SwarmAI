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

Host and Hives never connect to each other. Both connect outbound to one **Central
Service** (§4.3) in the AWS account SwarmAI uses for Bedrock. It registers DDDs,
Project Agents and execution nodes, coordinates remote Runs and holds their leases, and
mirrors Run events for the Control Center. It is never on the chat path, and Host-local
Runs do not depend on it.

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

**P5. A Central Service is the only bridge between Host and Hives.**
- It runs in the AWS account SwarmAI uses for Bedrock, built from API Gateway, Lambda,
  DynamoDB and S3.
- Every node (Host, EC2 Hive, Cloud Desktop Hive) only makes outbound HTTPS calls to it.
  No node accepts an inbound connection for Runs.
- It never stores readable internal content (§4.3.4).

## 3. Non-goals

- Project Agents, Intent triage, Run Graph / DAG of Runs, Asset Registry, Offline Eval,
  ADR, Evolution. They depend on this plane; this plane does not depend on them.
- Automatic Hive selection and automatic cross-Hive failover. The user or the Slack
  request names the Hive.
- Replicating schedules to a Hive so they fire while the Host is offline.
- Launching Cloud Desktops. V1 registers one the user already has.
- Pushing from a Hive to a protected branch. External results land on the Host; internal
  results leave as a draft CR on the repo's own internal remote (§7.3).
- More than one Central Service, more than one region, or multiple owners. V1 has one
  owner, who administers the Central Service from the Host.
- Running Project Agents. V1 registers Project Agent definitions in the Central Service;
  executing them is later work.
- Backward compatibility for runs created before the Phase 4 protocol cutover.
- Changing the Chat inline experience. Inline registration must be invisible in chat.

## 4. Architecture

```text
 Chat tab ──(pipeline_v1.py create)──┐
 Dashboard "Run in background" ──────┤
 Slack session (owner only) ─────────┼──► Conductor.submit() ──► Conductor Store (Host)
 Host Job Scheduler (pipeline_submit)┘          │
                                                ├─ inline_local      → chat session owns the Run
                                                ├─ background_local  → Local Worker (1 slot)
                                                └─ remote_hive ──outbound──┐
                                                                           ▼
                        ┌─────────────────────── Central Service ───────────────────────┐
                        │ API Gateway → Lambda · DynamoDB (registry, requests, leases,   │
                        │ events, decisions) · S3 (content-addressed blobs) · sweeper   │
                        └───────────────▲───────────────────────────────▲────────────────┘
                              outbound  │                               │ outbound
                            Host outbox · reads mirror          EC2 Hive / Cloud Desktop Hive
                                                                (claim · heartbeat · events)
   Code and DDD content stay in their own git remotes (GitHub, internal git).
```

The Conductor is a module in the Host daemon (`backend/core/conductor/`). It is not an
LLM and never calls one. It runs off the event loop on its own executor and is never on
the chat send path or the stream path.

### 4.1 Who owns each fact

Each fact has exactly one owner. Every other view is a projection.

| Fact | Owner | Readers |
|---|---|---|
| Stage, verdict, findings, signed events of a Run | The Pipeline v1 run store on the node that executes the Run | That node; others see the mirror |
| Request, origin, placement, queue, Attempts of a Host-local Run | **Conductor Store** on the Host (§4.2) | UI, Slack replies, Scheduler |
| Request, queue, Attempts and **lease / epoch** of a remote Run | **Central Service** `requests` table (§4.3) | Host Conductor, the target Hive |
| Schedule definition and fire times | Host Job Scheduler (`user-jobs.yaml`, new job kind `pipeline_submit`) | Conductor |
| Project class | `bindings.yaml` via `classify_project()` | Conductor placement check, recorded on the Central DDD record at publish |
| DDD content | Its git remote (`repo` origin) or the Host copy (SwarmAI, `host` origin) | Executors, through a pinned revision |
| Published DDD revisions and Project Agent definitions | **Central Service** registry | Every node |
| Node identity, type, allowed class, capacity, heartbeat | **Central Service** `nodes` table | Host Conductor dispatch |
| Org-wide view of Runs | **Central Service** event mirror, a projection of each node's outbox | Control Center |
| Landing on a Host repo | `run_protocol/landing.py` on the Host | Conductor |

`routers/hive.py` keeps only EC2 provisioning (create, start, stop). Hive identity for
Runs moves to the Central Service.

The Conductor never copies Pipeline stage state into its own store. The status a user
sees is a join of the Conductor record and the Pipeline run record, computed on read.

### 4.2 Conductor Store

A filesystem store, like the run protocol store, under
`~/.swarm-ai/state/conductor/`:

- `requests/<request_id>.json`: the immutable submitted request.
- `runs/<run_id>.json`: the mutable dispatch record, written with `utils.file_lock`.
- `events.jsonl`: an append-only log of dispatch transitions, for audit and recovery.

It does not use `data.db`, so chat history storage is untouched.

From Phase 4a the Host also writes a local outbox (`outbox.jsonl`) of its own Runs'
events, which a background task posts to the Central Service mirror every 30 s. The
outbox is a copy for the org view; the Conductor Store stays the owner of Host-local
Runs, and a Central outage never blocks or slows a local Run.

### 4.3 Central Service

The design follows AgentSpaces: its management service (ASMS) registers each machine,
issues its credentials and reports presence, and "is never on the request path"; every
gateway only connects outbound; work state stays on the machine running it, and
gateways report to the centre through a local outbox with idempotent ids
(`AgentSpacesDesktop` `relay/registration/registrar.ts`, `asms-chat-outbox.ts`;
`AgentSpacesRelay` `docs/ASMS-CONTRACT.md`).

#### 4.3.1 Components

All in the AWS account SwarmAI uses for Bedrock, in the same region, defined as code in
the repo (`backend/central/infra/`, AWS CDK in Python) and deployed only by an explicit
owner command, never automatically.

- **API Gateway (HTTP API)** in front of Python **Lambda** handlers, with per-node
  throttling.
- **DynamoDB**, on-demand, for small mutable coordination records. It is used because
  coordination needs atomic conditional updates (claim, lease renewal, epoch checks),
  queries by node and state (GSIs), field-level updates and TTL expiry. S3 conditional
  writes could do the compare-and-swap, but not the queries.
- **S3**, block public access, SSE-KMS, for large immutable content addressed by sha256,
  written with `If-None-Match: *` so a blob is never overwritten.
- **Sweeper**: an EventBridge rule runs a Lambda every minute to expire leases (§4.3.3)
  and decisions.

#### 4.3.2 Records

| Table | Key | Holds |
|---|---|---|
| `nodes` | `node_id` | type (`host`, `ec2`, `cloud_desktop`), allowed class, Ed25519 and X25519 public keys, reported `slots`, last heartbeat, status (`enrolling`, `active`, `revoked`) |
| `ddds` | `ddd_id`, `rev` | origin (`host` / `repo`), clone URL, access class, pinned commit or snapshot digest, published at |
| `project_agents` | `agent_id`, `rev` | definition (or its blob digest), pinned `ddd_id` + `rev`, published at |
| `requests` | `request_id`; GSI `(target_node, state)`; GSI `(state, updated_at)` | the immutable request, dispatch state, attempt, `lease_epoch`, `lease_expires_at`, `cancel_requested` |
| `events` | `request_id`, `seq` | mirrored Run events, written with `attribute_not_exists` so a replay is a no-op |
| `decisions` | `decision_id`; TTL | question, options, answer, answer nonce state, expiry |

| S3 prefix | Holds |
|---|---|
| `blobs/sha256/<digest>` | SwarmAI DDD snapshots and **external** result bundles only. Bundles of terminal Runs expire after 30 days |

A DDD or Project Agent revision is **published** from the Host Control Center. Only a
published revision can be pinned by a remote request. Publishing a `repo`-origin DDD
records the commit; the content stays in its repo.

#### 4.3.3 Identity, authorization and leases

- **Host (admin).** Calls the admin API with SigV4, through a dedicated least-privilege
  IAM role in the account. It does not reuse the Bedrock role.
- **Hives (nodes).** One path for both Hive types, since a Cloud Desktop is not in the
  account. The Host enrols a node by creating its `nodes` record with a one-time
  enrolment code, valid for 15 minutes and stored only as a hash. The node presents the
  code with its Ed25519 and X25519 public keys. After that, every node request is signed
  with its Ed25519 key over method, path, body digest, timestamp and nonce. A Lambda
  authorizer verifies the signature, rejects a timestamp more than 5 minutes off, and
  rejects a reused nonce (kept 10 minutes, TTL). Revoking a node is one write; its next
  request fails.
- **Authorization.** A node may claim and read only requests targeted at it, write events
  and heartbeats only for Attempts it holds at the current epoch, and read only the DDD
  and Project Agent revisions its requests pin. It cannot list other nodes or read their
  requests. Only the Host may create requests, publish revisions, answer decisions, cancel
  and enrol or revoke nodes.
- **Leases.** The Central Service is the lease authority for remote Runs:
  - *claim* is one conditional update: `state = queued AND target_node = me` →
    `running`, attempt + 1, `lease_epoch` + 1, `lease_expires_at = now + 10 min`;
  - *renew* happens on every heartbeat (60 s), conditional on the epoch matching;
  - any event or result with a non-current epoch is rejected;
  - the sweeper marks an unrenewed lease `failed(lease_lost)`. If the sweeper finds its
    own previous run more than 2 minutes ago (the service itself was down), it first
    extends every live lease by that gap, so a Central outage never expires a lease.

  Waiting for a person is not a lease problem: the Hive keeps heartbeating while its Run
  is `awaiting_human`.

#### 4.3.4 Internal data boundary

The Central Service never holds readable content of an `internal` project:

- Structural fields stay in the clear: ids, commit SHAs, digests, states, stage names,
  enum verdicts, counts, timestamps.
- Every free-text field of an internal request or event (requirement text, human answers,
  summaries, error text) is sealed (libsodium sealed box). Content for a Hive is sealed to
  that node's X25519 key; content for the Host is sealed to the Host's key. Central and
  anyone reading DynamoDB see only ciphertext.
- Internal DDD content and internal code never go to S3. The Cloud Desktop fetches them
  from internal git, and its result leaves as a draft CR (§7.3).
- Whether sealed internal text may be stored in this account is open item **O1** (§12.1).
  Internal remote Runs stay disabled until it is confirmed.

External projects use the same records without sealing.

#### 4.3.5 What Central never does

- It is never on the chat send or stream path, and Host-local Runs never wait for it.
- It holds no Pipeline signing key and no credential value. It is not a second
  authoritative copy of any code or DDD.
- It does not execute anything; it stores, authorizes and expires.
- If it is down: Host-local Runs are unaffected; running Hive Runs keep executing and
  buffer their events locally; new remote submissions fail visibly at submit rather than
  being queued silently.

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

The request names one Hive. The Conductor checks the placement rules (§5.3) and that the
Hive is `active` in the Central `nodes` table, of the right type and within its reported
slots. Otherwise the request stays `queued` behind that Hive; it is never moved to another
Hive.

**Hive types.**

- **EC2** (external). Existing provisioning in `routers/hive.py`, running
  `SWARMAI_MODE=hive`.
- **Cloud Desktop** (internal). The user installs the hive build on their own Cloud
  Desktop and enrols it with the Central Service using a one-time code issued from the
  Host (§4.3.3). V1 does not launch, stop or delete the machine.

Both types enrol the same way and then only make outbound calls. An EC2 Hive's Caddy
basic auth stays for its own dashboard and is not used by Runs.

**Capacity.** On every heartbeat to the Central Service the Hive reports `slots`, computed on the Hive from its
own `ResourceMonitor.spawn_budget()` and a per-Run memory figure measured in Phase 4 (§12).
The Conductor dispatches at most `slots` concurrent Runs to that Hive. There is no
instance-type table on the Host. A Hive with no report counts as one slot.

**Worker on the Hive.** The Hive runs the same Worker as §6.2 (headless `SessionUnit`,
full hooks, `pipeline:<run_id>`), against:

- code: each bound repo cloned or fetched on the Hive at the pinned `base_commit`;
- DDD: the shipped snapshot (`host`) or the pinned commit of `ddd_origin` (`repo`);
- credentials: the Hive's own. An internal Run needs Midway and internal git access on
  the Cloud Desktop; the Host never sends credentials.

**Results.** A Hive never pushes to a protected branch and never lands. Where its result
goes follows the repo's `delivery_contract.remote_kind`:

- `self-hosted-main` or `github-pr` (external): the result bundle goes to S3 and the Host
  accepts and lands it (§7.2);
- `code-amazon-cr` (internal): the Cloud Desktop hands the result off as a draft CR
  (§7.3).

**Signing authority on a Hive.** A Pipeline v1 run signs its events with one per-run
HMAC key (`authority.key`); whoever holds it can sign as any producer, including `hook`.
That key therefore never leaves the machine that created it:

- The Hive is a full SwarmAI daemon. It creates the Run in its own run store, with its own
  `authority.key`, and its own hook chain signs as `hook` exactly as on the Host. Nothing
  signed on the Host is sent to a Hive, and the Host never sends its keys.
- At enrolment the Hive generates an Ed25519 key pair; its public key is stored in the
  Central `nodes` table. Every `result_candidate` and every outbox event is signed with that
  key over `(hive_id, request_id, attempt, lease_epoch, seq, payload digest)`.
- The Host verifies that signature, the epoch and the pinned `base_commit` / `ddd_ref`. It
  then re-runs, on the Host, the test selection recorded in the candidate against the
  candidate applied on the pinned base. Only after that can the candidate be accepted.

The trust model is explicit: a registered Hive is the owner's own machine. The Host trusts
its review verdict, and re-checks what can be checked deterministically (identity,
freshness, that the patch applies, tests). A Hive key is revoked by deregistering the Hive;
candidates signed with a revoked key are rejected.

## 7. Remote Run flow and completion protocol

### 7.1 Remote Run flow through the Central Service

Host and Hive never call each other. Every step is an outbound call to the Central
Service:

1. **Submit.** The Host Conductor pins the request (§5), uploads the SwarmAI DDD snapshot
   to S3 if the DDD origin is `host` (external only), and creates the `requests` record
   with `state: queued`, `target_node` and the sealed free text where required.
2. **Claim.** The Hive long-polls `POST /v1/nodes/{id}/claim` (20 s wait, then again).
   The conditional claim returns the request, its attempt and `lease_epoch`.
3. **Run.** The Hive fetches code at the pinned `base_commit`, the DDD at its pinned
   revision, and runs the Worker (§6.2).
4. **Report.** The Hive writes ordered events to `PUT /v1/requests/{id}/events/{seq}`
   from its local outbox: started, stage changed, `awaiting_human`, `result_candidate`,
   failed. Each carries `attempt` and `lease_epoch`. Its heartbeat (60 s) renews leases
   and reports `slots`.
5. **Observe.** The Host Conductor reads new events for its live remote requests every 10 s
   while it has any, and not at all otherwise. A Host restart resumes from the last `seq`
   it applied.
6. **Answer and cancel.** The Host writes the human answer to `decisions`, or sets
   `cancel_requested` on the request. The Hive sees both on its next claim poll or
   heartbeat.

Every write is idempotent: events by `(request_id, seq)`, requests by `request_id`,
blobs by digest.

Credentials (prerequisite of Phase 4b). Today `routers/hive.py` keeps the Caddy basic
auth password and AWS access-key configuration as plaintext SQLite fields, and the
instance detail endpoint returns the password. Phase 4b moves these into the Host's
restricted secret store, keeps only a reference in SQLite, and changes every endpoint to
return credential metadata (present / rotated at), never a value. Credentials are never
logged. Node keys never leave their node; revoking a node in the Central Service revokes
its access for Runs.

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

### 7.3 Internal results: draft CR handoff

An internal repo is delivered through its own review system, not landed by the Host:

- The Cloud Desktop creates a **draft** CR with the `cr` tool from the Run's worktree. It
  never pushes a branch, never publishes the CR and never merges it.
- It writes a `result_candidate` event carrying the CR id and the reviewed commit, and the
  Run ends with a new landing outcome `handed_off_cr`, added with the Phase 4 cutover.
- Acceptance and integration are the human's, in CRUX. V1 records the CR id; it does not
  track the CR afterwards.

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
| Hive stops heartbeating | Central sweeper: lease not renewed for 10 min | Request moves to `failed(lease_lost)` and the epoch is revoked. No automatic move to another Hive. The owner may resubmit to a Hive they choose |
| Hive returns after the epoch was revoked | Central rejects its events and heartbeats for that epoch | The Hive cancels its stale Attempt |
| Host offline while a Hive Run continues | Nothing to detect; Central holds the lease, not the Host | The Hive keeps running and reporting to Central. The Host reads the events on return; an external landing waits for the Host |
| Central Service unreachable | Node calls fail | Host-local Runs unaffected. Hive Runs keep executing and buffer events locally; leases are protected by the sweeper gap rule (§4.3.3). A new remote submission fails at submit with the reason |
| Stolen or misbehaving node key | Owner action | Owner revokes the node; its next request fails and its requests can be cancelled |
| Target branch moved before landing | CAS fails on the first repo in `integration_landing` | New Attempt rebases the candidate; on conflict the request ends `failed(landing_conflict)` and is reported |
| Landing crashed or a later repo's CAS failed | Unfinished landing journal entry (§7.2) | Reconcile against git; adopt repos already at the prepared commit; if some repos landed and others cannot, end `not_landed(partial_land)` for a person to decide |
| Forged, replayed or stale Hive result | Ed25519 signature, epoch or `seq` check fails | Rejected; never accepted or landed |
| Placement or DDD pin invalid at dispatch | Re-check at dispatch | `failed(<rule>)`, never re-placed |
| DDD snapshot manifest or content-safety finding | Manifest build or `content_safety_scan(external=True)` at submit | Remote placement rejected before upload, with the files listed |

Cancel is always available to the owner. It sets `cancelled`, ends the Worker or sets
`cancel_requested` for the Hive, and lets the Pipeline abandon the Run with reason `cancelled`.

## 10. Surfaces

- **Pipelines page.** Each Run gains placement, executor, queue position, attempt count
  and origin. The Local Worker slot and each Hive's slots are shown as used / total.
  A "Run in background" action submits `background_local`; a Hive picker submits
  `remote_hive`, listing only Hives the placement rules allow for that project.
- **Control Center.** The org view of Runs, nodes and registered DDDs / Project Agents
  reads the Central Service. Publishing a DDD or Project Agent revision and enrolling or
  revoking a node happen here.
- **Chat.** No visible change for inline runs.
- **Slack.** §8.
- **API.** `POST /api/conductor/requests`, `GET /api/conductor/requests[/<id>]`,
  `POST /api/conductor/requests/<id>/cancel`, `GET /api/conductor/capacity`.
  Endpoints run on the `nav` pool, never on the chat path.

## 11. Pipeline five dimensions

| Dimension | Effect of this design | Guard |
|---|---|---|
| Run Isolation | Unchanged locally: each Run keeps its own worktree. On a Hive each Run gets its own clone at the pinned base | Placement rules keep internal code off EC2; Central never holds readable internal content; Hives never push to a protected branch; external landing is a CAS on the Host, internal results are draft CRs |
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
| 4a | Central Service in the Bedrock account (CDK: HTTP API, Lambdas, DynamoDB tables, S3 bucket, sweeper); node enrolment and signed requests; DDD and Project Agent registry with publish; Host outbox mirror of local Runs; Control Center reads Central | A node with a wrong signature, a stale timestamp or a reused nonce is refused; a node cannot read another node's requests; a claim races two nodes and exactly one wins; a lease survives a simulated 15-minute Central outage; a replayed event is a no-op; a local Run's events appear in the org view while a Central outage leaves local Runs unaffected |
| 4b | Hive credentials moved to the secret store; four-fact completion protocol with landing journal for every placement; EC2 Hive Worker through Central; Hive capacity reporting; SwarmAI DDD snapshot manifest and external scan | No Hive endpoint returns a credential value; an external Run on EC2 returns a signed candidate the Host verifies, re-tests and lands; a revoked-epoch event is rejected; an internal project is rejected for EC2; a snapshot scan finding blocks upload; a crash mid-landing reconciles without double-landing; inline and Local Runs land through the new facts |
| 4c | Cloud Desktop enrolment and Worker; sealed internal free text; `ddd_origin: repo` fetch with internal git; draft CR handoff. Gated on O1 and S6 | An internal Run with an IVTHubSpec-hosted DDD runs on a Cloud Desktop and ends `handed_off_cr` with a draft CR; no internal free text in Central is readable without the node or Host key; an external project is rejected for a Cloud Desktop |
| 5 | Slack scheduling to a named Hive | A scheduled Run targets the named Hive and reports in its thread; external results land on the Host, internal ones end as a draft CR |

### 12.1 Spikes before code

| ID | Question | Blocks |
|---|---|---|
| S1 | Can a headless `SessionUnit` with app session id `pipeline:<run_id>` get hook tokens from `run_owner_gate` and land a `direct` Run, the way channel sessions run without a tab? | Phase 2 |
| S2 | Memory and stream use of one Pipeline Run on the Host, measured; confirms that one extra Worker fits next to chat | Phase 2 |
| S3 | Per-Run memory on a Hive, measured, to turn `spawn_budget()` into `slots` | Phase 4b |
| S4 | Lambda authorizer for Ed25519-signed requests: measured added latency, and that a Cloud Desktop can reach the API Gateway endpoint outbound | Phase 4a |
| S5 | How an owner's Slack button press becomes a `human_answer` without the gateway's unattended path | Phase 3 |
| O1 | Policy: confirm with security whether sealed internal free text and internal metadata may be stored in the SwarmAI Bedrock account | Phase 4c |
| S6 | Headless Midway, internal git and `cr` on a Cloud Desktop. `ddd_bindings.bind_repo` defers internal bindings today ("needs Brazil+Midway headless auth") | Phase 4c |

If S1 fails, Phase 2 stops and the design is revised; the jobs `--bare` path is not used
as a fallback. If O1 or S6 fails, internal remote Runs stay unsupported and Phase 4c is
deferred; external Runs are unaffected.

## 13. Cost on hot paths

- Chat send and stream: no new work. Inline registration is one file write on the
  Conductor executor after `create` returns.
- Daemon idle: no Central polling without a live remote Run (10 s while it has one), plus
  one outbox flush every 30 s when there are new events, on the Conductor executor.
- Central Service: DynamoDB on-demand and Lambda are billed per request, so idle cost is
  storage plus the one-minute sweeper. Phase 4a measures the real monthly cost before
  Hives are enabled.
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

The HLD's Host-to-Hive Run Exchange (§7) is realised through the Central Service: its
store split is kept, the transport becomes outbound calls from both sides, and lease
epoch authority for remote Runs moves from the Host to the Central Service.
