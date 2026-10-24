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
  - docs/design/pipeline-human-loop-v2.md
  - Runtime DDD: ${SWARM_WORKSPACE}/Projects/<project>/bindings.yaml
---

# Pipeline Run Conductor V1

## 1. Summary

### 1.1 The problem

Today a Pipeline Run can only run inline in the Chat tab that started it, one at a time
per tab, on the Host. This design solves two problems, and nothing more:

1. **Start Pipeline Runs from a Host Chat tab or from Slack (now or on a schedule), and
   run them where they fit: inline in the tab, on a Local background Worker, on an EC2
   Hive or on a Cloud Desktop Hive, in parallel where capacity allows and queued where it
   does not.**
2. **Copy what happens during each Run (stages, human-in-the-loop questions, answers,
   approvals, stops, takeovers) and its results afterwards into one remote org store, for
   Trace, statistics, Eval and later uses.** The node that ran the Run keeps its original
   data and its local flow is unchanged.

It delivers the execution plane of the Organizational Autonomy OS HLD
(`project-agent-delivery-os-hld.md` §6.4–§6.6, §7, §17) and nothing above it. One
deterministic **Pipeline Run Conductor** in the Host daemon accepts a Pipeline request,
places it, dispatches it, tracks it and lands its result.

### 1.2 Entry points and placements

| Entry point | Can place on | Exists today |
|---|---|---|
| Host Chat tab, inline | `inline_local` (that tab) | Yes. V1 only registers it with the Conductor |
| Host Chat tab, choosing where (§6.4) | `background_local`, an EC2 Hive, a Cloud Desktop Hive | No |
| Dashboard Pipelines page | `background_local`, a Hive | No |
| Slack, immediate or scheduled (§8) | `background_local`, a Hive | No |

| Placement | Parallel | Queue |
|---|---|---|
| `inline_local` | One per Chat tab, as today | None |
| `background_local` | At most one Local Worker | Host queue, in submit order; one stream always kept for chat and Slack |
| `remote_hive`, EC2 (external projects only) | Up to the slots that Hive reports | Behind that Hive in Central; never moved to another Hive |
| `remote_hive`, Cloud Desktop (internal projects only) | Up to the slots that Hive reports | Same |

Host and Hives never connect to each other. Both connect outbound to one **Central
Service** (§4.3) in the AWS account SwarmAI uses for Bedrock. It registers DDDs,
Project Agents and execution nodes, coordinates remote Runs and holds their leases, and
keeps the organization's copy of every Run's record (§4.4) for Trace, Eval, statistics and
later consumers (§4.5). Whichever node runs a Run keeps its original data and does
everything it does today; the upload is a copy and never changes local behaviour. It is never on the chat path, and Host-local
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
- It keeps the organization's copy of each Run's record (§4.4). The node that ran the Run
  keeps the original and its local flow is unchanged and never depends on the upload.
- In V1 it writes nothing back to any node: no DDD, MEMORY, EVOLUTION or knowledge store.
  Turning stored records into org-level knowledge is a later consumer (§4.5.4).
- What it stores is extensible: new kinds of records can be added later (§4.4.3).
- V1 runs in internal accounts only, so content is stored as is (encrypted at rest, access
  by role, every access audited). Field-level sealing is deferred (§4.3.4).

## 3. Non-goals

- Project Agents, Intent triage, Run Graph / DAG of Runs, Asset Registry, Evolution. They
  depend on this plane; this plane does not depend on them.
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
- Field-level sealing of internal content in Central. V1 runs in internal accounts.
- Any consumer that writes derived knowledge back into a DDD or knowledge base (§4.5.4).
  V1 stores records and defines where such consumers plug in.
- Backward compatibility for runs created before the Phase 6 protocol cutover.
- Changing the Chat inline experience. Inline registration must be invisible in chat.
  One exception: an inline Run's Pipeline questions and approvals no longer block in an
  `AskUserQuestion` card; they are item cards in the tab, answered through the same write
  path as every other surface (`pipeline-human-loop-v2.md` §5.1).

## 4. Architecture

```text
 Chat tab, inline ─(pipeline_v1.py create)──┐
 Chat tab, choosing where ─(conductor draft)┤
 Dashboard "Run in background" / Hive ──────┤
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
| Original Run data: stage, verdict, findings, signed events, documents, transcripts | The Pipeline v1 run store on the node that executes the Run | That node, as today. Central holds an uploaded copy |
| Request, origin, placement, queue, Attempts of a Host-local Run | **Conductor Store** on the Host (§4.2) | UI, Slack replies, Scheduler |
| Request, queue, Attempts and **lease / epoch** of a remote Run | **Central Service** `requests` table (§4.3) | Host Conductor, the target Hive |
| Schedule definition and fire times | Host Job Scheduler (`user-jobs.yaml`, new job kind `pipeline_submit`) | Conductor |
| Project class | `bindings.yaml` via `classify_project()` | Conductor placement check, recorded on the Central DDD record at publish |
| DDD content | Its git remote (`repo` origin) or the Host copy (SwarmAI, `host` origin) | Executors, through a pinned revision |
| Published DDD revisions and Project Agent definitions | **Central Service** registry | Every node |
| Node identity, type, allowed class, capacity, heartbeat | **Central Service** `nodes` table | Host Conductor dispatch |
| Org-wide view of Runs | **Central Service** event mirror, a projection of each node's outbox | Control Center |
| The organization's copy of a Run (Run record) | **Central Service** org Run store (§4.4) | Host landing of remote results, Trace, Eval, later consumers |
| Run Trace statistics | **Central Service** `run_index`, derived from Run records (§4.5.1) | Control Center |
| Intervention classification and five-dimension results | **Central Service** `eval_reports` (§4.5.3) | Control Center, ADR roll-up |
| Whether a local Run uploads | `config.json["pipeline"]["central_upload"]` and the request's `upload` flag, fixed in the Run contract at create (§4.4.1) | Conductor, Host outbox |
| DDD content | The DDD's own store, written only by the existing local paths, as today. Central does not write it in V1 | Executors |
| Audit of Central access | **Central Service** `audit` (§4.4.4) | Owner |
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

From Phase 3 the Host also writes a local outbox (`outbox.jsonl`) of the events of its
own Runs that have upload on (§4.4.1), which a background task posts to the Central Service mirror every 30 s. The
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
- **S3**, block public access, SSE-KMS, for large immutable content (§4.3.2 blobs).
- **Sweeper**: an EventBridge rule runs a Lambda every minute to expire leases (§4.3.3)
  and decisions, and to probe the public API (§4.3.3 lease barrier).

#### 4.3.2 Records

| Table | Key | Holds |
|---|---|---|
| `nodes` | `node_id` | type (`host`, `ec2`, `cloud_desktop`), allowed class, Ed25519 public key, reported `slots`, `active_leases` (held Attempts, changed only inside the claim / release transactions of §6.3), last heartbeat, status (`enrolling`, `active`, `revoked`) |
| `ddds` | `ddd_id`, `rev` | origin (`host` / `repo`), access class, clone URL, pinned commit or snapshot digest, published at |
| `project_agents` | `agent_id`, `rev` | definition (or its blob digest), pinned `ddd_id` + `rev`, published at |
| `requests` | `request_id`; GSI `queue`: partition `<target_node>#<state>`, sort `<created_at>#<request_id>` (FIFO per Hive, §6.3); GSI `(state, updated_at)` | the immutable request, dispatch state (§5.1), attempt, `lease_epoch`, `lease_renewed_at` (Central clock), `worker_incarnation`, `slot_held`, `cancel_deadline` |
| `events` | `request_id`, sort key `<attempt>#<lease_epoch>#<seq>` (zero-padded) | mirrored Run events with their signed `payload_digest` (§7.1) |
| `decisions` | `decision_id`; TTL | transport only, not the authority (§8.3): the question as reported by the Hive, and the Host-signed answer command with its state (`posted`, `accepted`, `acked`, `rejected_stale`, `expired`) |
| `trace_cursors` | `request_id`, `attempt` | `contiguous_seq` (last gap-free `seq`), `version`, its Pipeline event id and chain position, `attempt_closed` seq, `sealed_rev`, `dirty` (§4.5.1) |
| `blob_objects` | `digest` | size, media type, first registered at. One row per distinct content |
| `blob_refs` | `owner_id` (request or DDD revision), sort `<role>#<ref_id>` (`ref_id` is unique per use, so several blobs of one role fit); GSI `digest` | the digest, producing node, attempt, epoch, producer signature. One row per use, so the same content used by many Runs keeps every provenance |
| `record_kinds` | `kind`, `schema_version` | the registered kinds of uploaded records and who may produce them (§4.4.3) |
| `audit` | `day`, `ts#id` | §4.4.4 |
| `enrolments` | `code_hash`; TTL | target node, attempts left, consumed flag (§4.3.6) |
| `run_index` | `request_id`; GSIs `(project, day)`, `(day)` | Trace projection (§4.5.1), rebuildable |
| `eval_jobs` | `job_id`; GSI `state` | §4.5.3 |
| `eval_reports` | `report_id`; GSI `request_id`, `(project, day)` | five-dimension results, intervention classifications, Eval version, input digests |

| S3 prefix | Holds |
|---|---|
| `quarantine/<upload_id>` | Raw uploads awaiting intake. Readable only by the intake Lambda and the audit role. One rule: deleted as soon as intake promotes it; an upload that fails intake stays here with its reason for 7 days (lifecycle rule), then is deleted |
| `blobs/sha256/<digest>` | Canonical, byte-exact DDD snapshots, Run records, event chains and result bundles (§4.4.1). Never rewritten. Readable only by the integrity roles (Host landing, Trace sealing, Eval) |
| `views/sha256/<digest>` | Redacted projections of text blobs (§4.4.1 intake), each with a signed transformation manifest naming its canonical digest. What the Control Center and later consumers read |

Lifecycle rules for `blobs/` and `views/` are set in Phase 3 from measured volume (S7).

How a blob gets in. Content addressing alone does not stop an authorized writer from
putting wrong bytes under a digest first, so:

1. A writer asks Central for an upload; Central returns a presigned PUT to
   `quarantine/<upload_id>`, scoped to that one key, valid 15 minutes, bound to the
   writer's request, attempt and epoch, and requiring `x-amz-checksum-sha256` equal to the
   claimed digest. S3 rejects a body whose checksum differs.
2. The writer calls `complete` with its signature over the digest, size, media type and
   role. Central checks that the stored object's checksum is that digest.
3. **Intake runs on the quarantined object before anything is registered** (§4.4.1).
   Canonical evidence is never rewritten: signatures, `prev_hash` links and `blob_sha`
   references cover the exact bytes (`run_protocol/events.py`), so a rewritten event or
   blob would no longer verify. Intake instead scans it and, for text, writes a separate
   redacted projection plus a signed manifest `(canonical digest, projection digest,
   findings)`. A result bundle (code) gets no projection: a secret finding in it rejects
   the upload and the candidate cannot land.
4. Central writes the `blob_objects` row (if new) and a `blob_refs` row for this use,
   copies the canonical object to `blobs/sha256/<digest>` and the projection to
   `views/sha256/<digest>`, both with `If-None-Match: *`, and deletes the quarantined
   object. Content that already exists is not copied again; the new `blob_refs` row still
   records this use.
5. Every reader verifies the sha256 after download and refuses a mismatch.

No node can write to `blobs/` directly.

A DDD or Project Agent revision is **published** from the Host Control Center. Only a
published revision can be pinned by a remote request. Publishing a `repo`-origin DDD
records the commit; the content stays in its repo.

#### 4.3.3 Identity, authorization and leases

- **Host (admin).** Calls the admin API with SigV4, through a dedicated least-privilege
  IAM role in the account. It does not reuse the Bedrock role. Separately, the Host holds
  an Ed25519 **command key** that signs the payload of commands a Hive must verify itself
  (answer commands, §8.3). Central cannot forge them, because it never holds that key.

  | Endpoint group | Transport auth | Payload signature |
  |---|---|---|
  | Admin (create request, publish, cancel, enrol, revoke, register Host key) | SigV4 | none |
  | Answer command | SigV4 | Host command key |
  | Node (claim, events, heartbeat, upload, `cancel_ack`, `adopt`) | Ed25519 node key | the same node key |
- **Hives (nodes).** One path for both Hive types, since a Cloud Desktop is not in the
  account. The Host enrols a node with a one-time code; the node presents it with its
  Ed25519 public key. After that, every node request is signed with its
  Ed25519 key. The exact rules for codes, signing, nonces and keys are in §4.3.6.
- **Authorization.** A node may claim and read only requests targeted at it, write events
  and heartbeats only for Attempts it holds at the current epoch and incarnation, and read
  only the DDD and Project Agent revisions its requests pin. It cannot list other nodes or
  read their requests. Only the Host may create requests, publish revisions, answer
  decisions, cancel and enrol or revoke nodes.
- **Clocks.** Leases use only Central's clock (`lease_renewed_at` is written by Central).
  Node clocks matter only for the signed-request timestamp, which must be within 5 minutes
  of Central's; a rejected request returns Central's time, and nodes must run NTP.
- **Leases.** The Central Service is the lease authority for remote Runs:
  - *claim* is the slot transaction of §6.3: `state = queued AND target_node = me` →
    `dispatching`, attempt + 1, `lease_epoch` + 1, `worker_incarnation = <the caller's>`,
    `lease_renewed_at = now`, `slot_held = true`; the Hive's `started` event then moves it
    to `running`. Every state change and its slot effect is the one table in §5.1;
  - *renew* happens on every heartbeat (60 s), conditional on epoch and incarnation;
  - any event, heartbeat or result with a non-current epoch or incarnation is rejected,
    and the node stops that Worker;
  - V1 never re-queues a Run to another node, so an expired lease can never lead to the
    same Run being claimed twice. Resubmitting is a new request.
- **Lease barrier.** A Central outage must not expire leases, and an outage must not keep
  dead Hives' leases alive forever either. Central keeps one `health` record:
  - each sweeper run probes the public API through API Gateway (the path nodes use);
  - `healthy_since` is reset to *now* whenever the previous sweep is more than 2 minutes
    old or the probe fails;
  - a lease expires only when `now − max(lease_renewed_at, healthy_since) > 10 min`,
    checked in the same conditional update that marks it `failed(lease_lost)`.

  So after any outage every Hive gets a full 10 minutes of working service to renew, and a
  Hive that died before the outage expires 10 minutes after recovery. Leases are never
  extended retroactively.
- **Waiting for a person.** The Hive keeps heartbeating while its Run is
  `awaiting_human`. Its Worker ends its turn and the Run is parked at once (§6.2), its slot
  released; the lease is renewed by the Hive daemon's heartbeat with no Worker running,
  and a Hive restart renews it without a new Attempt (`parked → parked`, items kept).
  The open item expires 24 hours after it
  opened (`pipeline-human-loop-v2.md` §5.4): the daemon writes `item_expired` and the Run
  ends `abandoned(decision_timeout)`. Expiry and an accepted answer are one
  compare-and-swap on the same record, so both cannot win. The same rule applies to the
  Local Worker.

#### 4.3.4 Data handling

V1 runs only in internal accounts, so Central stores Run content as it is:

- DynamoDB and S3 are encrypted with a customer-managed KMS key; only the Central
  Lambdas and the Host admin role can use it.
- Every read and write of a Run record or blob is recorded in the audit table (§4.4.4).
- Consumers read only redacted projections (`views/`, §4.4.1). Canonical bytes
  (`blobs/`) are readable only by the roles that must verify or apply them, each with its
  own KMS grant: the intake and Trace-seal Lambdas (verify signatures and chains), the
  Host landing role (result bundles only, which never contain a detected secret) and the
  audit role. Eval, the Control Center and later consumers have no grant on `blobs/`.
- A canonical text blob with a secret finding is kept 30 days (long enough to re-verify
  and audit the seal), then deleted; its digest, manifest and seal result remain, so the
  Trace still proves what it covered.
- `classify_project()` still decides **where** a Run may execute (§5.3). It no longer
  decides what Central may store.
- Field-level sealing is deferred. If a later DDD needs it, it is added at intake, where
  every result already passes.

#### 4.3.5 What Central never does

- It is never on the chat send or stream path, and Host-local Runs never wait for it.
- It holds no Pipeline signing key and no credential value. It is not a second
  authoritative copy of any code or DDD.
- It does not execute anything; it stores, authorizes and expires.
- In V1 it writes nothing to any node's DDD, MEMORY, EVOLUTION or knowledge store.
- If it is down: Host-local Runs are unaffected; running Hive Runs keep executing and
  buffer their events locally; new remote submissions fail visibly at submit rather than
  being queued silently.

#### 4.3.6 Wire and key specification

Written and reviewed as its own document, with test vectors, before any Phase 3 code.
It must fix at least:

- **Enrolment codes:** 128 bits of randomness, stored as an HMAC with a Central-held
  pepper, valid 15 minutes, consumed by one conditional update, locked after 5 failed
  attempts. Enrolling again with new keys replaces the old keys only with the Host's
  approval.
- **Request signing:** a canonical string of method, path, sorted and percent-encoded
  query, sha256 of the exact body bytes, timestamp, nonce and node id; Ed25519 over it.
  The authorizer does not cache results. A nonce is stored per node with a conditional
  put and a 10-minute TTL, so a reuse fails atomically.
- **Host admin role:** the Host assumes `swarmai-central-admin` from the owner's AWS
  profile with `sts:AssumeRole` (1-hour sessions); it never stores long-lived keys for it.
  Revocation is an edit of the role's trust policy.
- **Key lifecycle:** node private keys in the OS keystore, or a `0600` file owned by the
  service user when there is none; rotation, loss and recovery.
- **Host command key:** generated on the Host and kept in its Keychain; its public key is
  registered by a SigV4 admin call (`PUT /v1/admin/host-keys/{key_id}`), stored in `nodes`
  as type `host`. Every command carries its `key_id`. Rotation registers a new key and
  marks the old one verify-only until every command it signed has expired and its 1-hour
  delivery window has passed (at most 25 h),
  then deletes it; revocation deletes it at once and pending commands are re-signed or
  rejected.

### 4.4 Org Run store

Central keeps the organization's copy of each Run. It is a store, not a step in the Run:
the node that ran the Run keeps its own original data and does everything it does today,
and what Central does with the copy (§4.5) never changes or waits on that.

#### 4.4.1 What a Run hands in

When the Run is terminal, after REFLECT has completed (`reflect_complete` or
`reflect_exhausted`) and `REPORT.md` and usage have been finalized, the executor uploads
one **Run record**. This holds for every placement, including an internal Run, whose
order is `external_handoff` → REFLECT → `completed` → upload. It holds four kinds of
content, plus the metadata that ties them together:

| Part | Contents | Source today |
|---|---|---|
| Pipeline documents | `REQUIREMENTS.md` and `DESIGN_SPEC.md` (profiles that have them), `REVIEW.md`, `FIXING_SPEC.md` (if review asked for fixes), `REPORT.md` | Rendered by the Pipeline into the run directory (`run_protocol/documents.py`, `engine.py`) |
| Reflect summary | Each lesson the agent wrote, with its destination, relation, the judge's verdict and what local REFLECT did with it | `artifacts/reflect.json` |
| Delivery record | Commits: per repo the base, landed commit and message. Or CR: package, CR id, revision, commit, state at handoff (draft) | The landing result (`run_protocol/landing.py`), or the CR handoff (§7.3) |
| Trace and human-loop record | The full signed Pipeline event chain and the blobs it references: stage attempts and completions with times, checks and test calls with results, review handouts, answers and findings, every `decision`, `human_input` (answers, approval outcomes, guidance), `input_delivered`, `input_ack`, `item_expired`, `human_finding`, `human_reject`, `auth` (human approvals), `user_stop`, `resume`, `owner_transfer` (takeover), `session_lost`, `restore`, `abandon`, landing events and usage metrics per stage and model | The run's signed event store (`run_protocol/events.py`), usage from `run_protocol/usage.py` |
| Dispatch record | Queue wait, Attempts with their start, end and reason, cancel and lease events | Conductor Store (local) or Central `requests` (remote) |
| Metadata | Below | Conductor and Pipeline records |

The record is a versioned manifest (`run_record_version: 1`). Metadata fields:

| Field | Required for |
|---|---|
| `request_id`, `run_id`, project, profile, origin, placement, executing node | all |
| `ddd_ref` (origin, revision, digest), Project Agent Definition revision if any | all |
| `code_ref` per repo (base commit) and owned paths | all |
| Pipeline protocol revision and evidence contract id | all |
| Attempts (attempt, epoch, node, start, end, reason) | all |
| The four completion facts and the landing outcome | all |
| Candidate tree digest per repo and the result bundle digest | Runs that changed code |
| Signed event chain: head digest and event count, so the uploaded chain can be verified complete | all |
| Token and time usage | all. Per stage and model once 3-0 instruments it (today `run_protocol/usage.py` records only run-level input/output/call totals with no model); until then Token Cost is recorded at run level and its per-stage split is `UNKNOWN` |
| Digests of transcripts and logs | all |

The **result bundle** is a separate blob, referenced by digest. Per repo it holds the base
commit, the ordered patch series (`git format-patch`) and the candidate tree digest. The
Host lands from it (§7.2): it applies the series on the base, checks the tree digest, then
runs the landing. Internal Runs have no result bundle; their delivery record names the CR.

Agent transcripts and raw process logs stay on the node that ran the Run; the Run record
carries only their digests, so they can be checked if fetched later. Everything Trace and
Eval need (§4.5) is in the event chain and the dispatch record, so neither has to reach
back to a node.

**Upload is the Pipeline standard, with an opt-out for local Runs.**

| Placement | Upload |
|---|---|
| `remote_hive` | Always. Central is how its result reaches the Host, so it cannot be turned off |
| `inline_local`, `background_local` | On by default; the Host uploads from its outbox (§4.2). Can be turned off |

The switch is `config.json["pipeline"]["central_upload"]`: `"on"` (default) or `"off"`,
next to `worktree_mode` in the same section and changed through `PUT /api/settings`. A
request can also set `upload: false` at submit (`pipeline_v1.py create --no-upload`, or the
Conductor API), which only narrows the setting, never widens it.

The value is decided once, when the Run is created, and recorded in the Conductor Store
and the Run contract. Changing the setting later does not affect Runs already created.
A local Run with upload off:

- sends nothing to Central: no Run record, no event mirror, no lessons;
- runs, lands and reflects exactly the same locally;
- is shown in the Control Center only on this Host, marked `local only`;
- is not part of org Trace statistics or Eval (§4.5). Central cannot see it, so org
  metrics are stated as **over the Runs Central received**, never as over all Runs. The
  Host's own Control Center shows how many of its Runs are `local only`, counted locally
  and never sent.

A Run that should upload but cannot (Central down) keeps its record locally and retries;
nothing is lost.

**Intake.** Every uploaded record passes intake before it is registered: schema and
digest checks, signature and chain verification on the canonical bytes, and a redaction
scan of all text. Findings never alter the canonical record; they produce the redacted
projection (§4.3.2 step 3) and an audit entry. A record that fails schema, digest or
signature checks stays in quarantine with the reason (§4.3.2) and is not registered; the
node keeps its own copy and nothing about the Run changes.

#### 4.4.2 The local flow does not change

- On every node, for every placement, the Pipeline runs as today, including REFLECT:
  `reflect_job.route` applies kept lessons on the node that ran the Run, exactly as now.
- Chat sessions, MEMORY, EVOLUTION, DailyActivity, the post-session hooks, Save to Memory
  and DDD cultivation are not part of the upload and are not touched.
- Uploading happens after the Run is terminal, on the Conductor executor. A failed or
  slow upload never fails, delays or changes a Run; the record waits in the outbox.
- One consequence to know: lessons from a remote Run are applied to that Hive's own
  workspace, as any SwarmAI daemon does. They reach the Host's DDD only through a later
  consumer (§4.5.4) or a person, not automatically in V1.

#### 4.4.3 Record kinds

Every uploaded object is a record of a registered kind with a schema version
(`record_kinds`). V1 has one kind, `run_record` v1 (§4.4.1). Later kinds are added by
registering them, with no change to the store, intake or audit, for example:

- `judge_verdicts`: the distilled judge results of a Host's ingestion gate;
- `corrections`: captured corrections;
- `eval_report`: written by Eval (§4.5.3).

A kind declares who may produce it (a role) and whether its upload is on by default; a
kind that carries a node's private content (such as corrections) is off unless the owner
turns it on, as `central_upload` does for Runs. Records are immutable; a new version is a
new record.

#### 4.4.4 Audit

Central keeps an append-only `audit` table: every Run record received, every redaction
finding, every quarantined upload, every Eval report written, and every read of Run
content. Every record and every derived output can be traced to who produced and read it.


### 4.5 Consumers of the org store

Consumers read records; they never modify them and never run in the delivery path. Trace
and Offline Eval are the first two (Phase 4). Both read only what Runs hand in (§4.4.1) and
what Central records itself.

#### 4.5.1 Trace

- **Live:** while a Run executes, its events reach Central through the event mirror
  (§4.2 outbox for local Runs, §7.1 for remote Runs), so the Control Center shows stage,
  waits and human questions as they happen.
- **Mirror writes.** Each mirrored event is written in one `TransactWriteItems` with its
  Attempt's `trace_cursors` row: put the event (condition: key absent), and, if its `seq`
  is `contiguous_seq + 1`, advance `contiguous_seq` and `version` (condition: `version`
  unchanged). An event that arrives after a gap is stored without advancing; the write
  that fills the gap advances the cursor over the stored run in further version-checked
  updates. If the cursor row is already sealed, the same transaction sets `dirty = true`.
- **Sealed:** the Run record seals the Trace when it arrives after REFLECT, in one
  conditional transaction on every `trace_cursors` row of the request, conditioned on each
  row's `version` as read. So a mirror write that lands meanwhile fails the seal, which
  re-reads and retries. Each mirrored event row carries its Pipeline event id and chain
  position, so the live mirror maps onto the chain. The seal checks, per Attempt, that the
  cursor is contiguous up to that Attempt's `attempt_closed` seq (or its last event), that
  every mirrored event appears in the uploaded chain at its position with the same digest,
  and that the chain verifies to its head digest and count. The seal is one immutable
  revision, `complete` or `incomplete`; an `incomplete` revision is Eval-eligible, and every
  dimension that needs the missing range is `UNKNOWN` (§4.5.3). Data that arrives after the
  seal never edits it: its write sets `dirty`, and the sweeper creates a new seal revision
  that supersedes the previous one. An Eval report stays bound to the revision it read.
- **Index:** a `run_index` table is built from Run records and dispatch facts (project,
  DDD, placement, node, profile, origin, outcome, start and end, per-stage durations,
  tokens, human events) with GSIs by project and by day, so Control Center statistics are
  queries, not scans. It is a projection: it can be rebuilt from the Run records at any
  time and holds no fact of its own.

#### 4.5.2 Human intervention record

Every point where a person entered a Run is a fact in its event chain, with time,
stage and who:

| Event | Meaning |
|---|---|
| `decision` / `land_request` → `human_input` | The Run asked or requested approval; a person answered (wait time = answer time − question time) |
| `human_input{kind: guidance}` → `input_delivered` → `input_ack` | A person steered the Run unasked; when it reached the agent; what the agent said it did |
| `human_finding`, `human_reject` | A person requested changes or rejected at approval |
| `auth` | A person approved a landing or another guarded action |
| `user_stop`, `resume` | A person stopped or resumed the Run |
| `owner_transfer` | Ownership moved, including a human takeover |
| `abandon` with a human reason, cancel | A person ended the Run |
| A Slack or dashboard answer | Same `human_input`, with its server-set channel (§8.3) |

The Pipeline records what happened, not whether it was avoidable. Offline Eval classifies
each intervention into the HLD's canonical set (`required_judgment` as neutral, or
`preventable_question`, `human_correction`, `manual_routing`, `manual_retry`,
`operational_rescue`, `unnecessary_approval`, `manual_code_fix`, `takeover`), and stores
the classification and its reasoning in its report, never back into the Run.

#### 4.5.3 Offline Eval

Eval runs as `eval_jobs` claimed by a node with the `eval` role (the Host in V1), the same
way Hives claim Runs (§4.3.3), and writes `eval_reports`. It never blocks or
delays a Run or a landing. A report is computed only from Central
data and records the Eval version and the Run record digests it read, so it can be
reproduced.

| Dimension | Computed from |
|---|---|
| Run Isolation | Worktree mode and paths, owned paths against the contract's repos, landing CAS outcomes, `partial_land`, placement-rule results |
| Quality | Pipeline verdict, review findings by severity and their status, test calls and results, review rounds, fixing cycles |
| Autonomy | The classified human interventions (§4.5.2); a Run is autonomous only with zero preventable interventions |
| Speed | Queue wait, wall time, per-stage durations from `stage_attempt` / `stage_complete`, with time waiting for a person reported separately |
| Token Cost | Usage metrics per stage and model, retries included |

Missing or unverifiable evidence makes that dimension `UNKNOWN` for the Run, never PASS.
Org roll-ups (by project, DDD, placement, period) are computed by Eval from reports;
Autonomous Delivery Rate follows the HLD formula (A / E) and is always shown with its
evidence coverage over the Runs Central received, which counts `incomplete` Traces as
not covered. Inline Runs read the live DDD (§5.2), so their context is unpinned: Eval
reports their DDD-context parity as `UNKNOWN` and Local-versus-remote comparisons use
managed Runs (`background_local`, `remote_hive`) only.


#### 4.5.4 Later consumers

Statistics and reports, and secondary processing of records into organization-level
knowledge stored in a particular DDD or a knowledge base, are later work. The store is
built so they plug in without changing it:

- a consumer reads records by kind and time, and writes its own output as a new record
  kind with provenance (the record digests it read);
- it writes into a DDD or knowledge base only through that destination's own write path
  (for a SwarmAI DDD, the existing `apply_to_ddd` under its lock), with the destination
  owner's approval, and is designed when it is built;
- it never edits Run records or any node's local data.

## 5. Data model

### 5.1 Request and dispatch record

```yaml
request:                       # immutable once accepted
  request_id: req_<ulid>
  project: SwarmAI
  requirement: "<user's words, stored verbatim as data>"
  title: "<= 60 chars, how the Run is named on every surface (§5.4)"
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
  state: submitting | queued | dispatching | running | parked | resume_pending
       | candidate_ready | landing | cancelling | done | failed | cancelled
  attempts:
    - attempt: 1
      executor: host:inline | host:worker | hive_<id>
      lease_epoch: 1           # remote Runs: advanced only by Central (claim, adopt, cancel)
      started_at: <iso>
      ended_at: null
      end_reason: null | completed | lease_lost | crashed | cancelled | adopted
  slot_held: false             # true exactly while this request holds an executor slot
```

`state` describes dispatch only. Whether the Run passed, and whether it is waiting for a
person, is Pipeline state (`core/run_protocol/lifecycle.py` `STATUSES`), read from the
Pipeline store. Schedule outcomes such as `missed` and `skipped_overlap` belong to
schedule occurrences (§8.2), not to a request, because no request exists for them.

Legal dispatch transitions. This table is normative: §4.3.3, §6.3, §7.4 and §9 refer to it
and do not restate it. Any other write is rejected. **Slot** is the effect on the executor's
slot: for a remote request, `nodes.active_leases` on the target Hive, guarded by the
request's own `slot_held` flag (set in the claim transaction, cleared in the releasing
transaction, and a release is conditional on `slot_held = true`, so it happens exactly
once); for a Local request, the one Local Worker slot. A request holds a slot exactly
while its executor is running a Worker for it.

| From | To | Trigger, writer | Slot |
|---|---|---|---|
| `submitting` | `queued` | Central acknowledged the create (Host Conductor, remote only, §7.1) | — |
| `submitting` | `failed(central_unavailable)` | create keeps failing (Host Conductor) | — |
| `submitting` | `cancelled` | owner cancel; the Host also cancels the Central record if the create landed | — |
| `queued` | `dispatching` | claim (Central, §6.3) / Local Worker start (Host) | acquire |
| `queued` | `cancelled` | owner cancel | — |
| `queued` | `failed(queue_timeout \| node_revoked \| <rule>)` | sweeper / revoke / re-check | — |
| `dispatching` | `running` | executor reported `started` | keep |
| `dispatching` | `queued` | executor refused before start (keeps its `created_at`, so its queue position) | release |
| `dispatching` | `cancelling` | owner cancel (§7.4) | keep |
| `dispatching`, `running` | `failed(lease_lost)` | lease expiry (sweeper, §4.3.3) | release |
| `running` | `running` (new Attempt) | `adopt` after a Hive restart, or Local crash recovery | keep (net zero) |
| `running` | `parked` | the Worker's turn ended with a blocking item open; the Worker is recycled to COLD keeping its session id | release |
| `parked` | `resume_pending` | the last blocking item was resolved | — |
| `parked`, `resume_pending` | same state (new epoch, **same Attempt**) | lease renewal after a Hive restart with no Worker running: epoch and incarnation renewed, open items rebound to the new epoch in the same update (`pipeline-human-loop-v2.md` §3.5) | — |
| `resume_pending` | `dispatching` | a slot is free on the executor; `resume_pending` requests go ahead of `queued` ones | acquire |
| `parked`, `resume_pending` | `cancelled` | owner cancel (no Worker is running, so no ack is needed) | — |
| `parked` | `failed(<pipeline reason>)` | `item_expired` (`decision_timeout`) | — |
| `parked`, `resume_pending` | `failed(lease_lost \| node_revoked)` | lease expiry with no renewal (sweeper) or the Hive revoked, in the same conditional update as the epoch check | — |
| `running` | `cancelling` | owner cancel | keep |
| `running` | `candidate_ready` | executor offered a candidate (Phase 6+); the Worker has ended | release |
| `running` | `done` | Pipeline terminal `completed`, with its landing outcome (before Phase 6) | release |
| `running` | `failed(<pipeline reason>)` | Pipeline terminal `abandoned` (incl. `decision_timeout`) or `failed` | release |
| `cancelling` | `cancelled` | `cancel_ack` | release |
| `cancelling` | `cancelled(unacknowledged)` | cancel deadline (sweeper) | release |
| `candidate_ready` | `landing` | Host accepted the candidate | — |
| `candidate_ready` | `queued` (rebase Attempt) | first-repo CAS failed; keeps its `created_at` | — |
| `candidate_ready` | `cancelled` | owner cancel (no Worker is running, so no ack is needed) | — |
| `landing` | `done` | every recorded landing outcome, including `not_landed(partial_land)` | — |
| `landing` | `failed(landing_conflict)` | rebase conflicted | — |

A Pipeline's own `completed` status always maps to dispatch `done`, whatever the landing
outcome; `failed` dispatch means the Run did not complete.

Before Phase 6, an inline or Local Run goes `running` → `done` directly: today's v1 `land`
both judges and lands, so there is no candidate step. Every transition is an event in
`events.jsonl` with a key `(request_id, attempt, lease_epoch, seq)`; replaying a key that
was already applied is a no-op. A local request is created directly as `queued`;
`submitting` exists only while a remote create awaits Central.

**What a new Attempt may reuse** (HLD §6.5). A new Attempt (crash recovery, adopt, rebase)
resumes from the Pipeline checkpoint and may reuse its verified source changes and
completed documents as starting material. It may not reuse the closed Attempt's session
state, test results, review closure, authorization or completion verdict as proof: every
check that landing reads is recomputed by the new Attempt on its current code tree,
which v1 already requires (`facts.bind_final`). The closed Attempt's evidence stays in the
chain as history.

What a user sees is computed on read:

| Dispatch | Pipeline status | Shown as |
|---|---|---|
| `queued` / `dispatching` | (no Run yet) | Queued (position N) / Starting |
| `running` | `active`, `reflecting` | Running · stage |
| `running` | `awaiting_human` | Waiting for you |
| `parked` | `awaiting_human` | Waiting for you (parked, slot free) |
| `resume_pending` | `active` | Answered, resuming (next slot) |
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
| `inline_local` | The live Host copy, as today. `ddd_ref` records `kind: live` and the digest at create. It is a record, not a pin: an inline session may cultivate its own DDD mid-run, and V1 does not change that. So the HLD's "every Run pins its Context revision" invariant applies to managed Runs only; inline Runs are marked unpinned and Eval treats them as such (§4.5.3) |
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

### 5.4 Run title

Every surface names a Run by its **title**, with the requirement as its subtitle and the
`run_id` only as a small secondary label. An id alone tells a person nothing.

- **What it is:** at most 60 characters, plain text, part of the immutable request.
- **Where it comes from:**
  - Chat tab: the chat agent proposes it in the draft, from the person's words; the
    person sees it on the confirmation card and can edit it (§6.4).
  - Slack immediate: the Slack session agent proposes it the same way (§8.2).
  - Chat inline: the agent passes `pipeline_v1.py create --title`, recorded in the contract
    and copied by `register_inline`. The flag does not exist today, and the engine's
    current fallback cuts the requirement at 80 raw characters; Phase 1 adds the flag and
    replaces that cut with the rule below.
  - Schedule: the schedule's name plus the fire date in the schedule's timezone, for
    example "Nightly dependency bump · Oct 24", so each fire is told apart.
  - Missing or empty (a draft from a caller that does not propose one): the first line of
    the requirement, cut at 60 user-perceived characters (grapheme clusters) on a word
    boundary where there is one; normalized to plain single-line text in one function.
    Never generated later by a
    model behind the person's back.
- **Never changes** after submit. A Run that drifts from its title is visible as such in
  its thread; the requirement stays the record of intent.
- **Shown:** stream messages and cards use "title · project · profile"; the drawer heads
  with the title and shows the full requirement under it; Slack thread replies quote the
  title; the chat-tab card heads with it.

## 6. Execution modes

### 6.1 `inline_local` (exists)

Unchanged for the user. `pipeline_v1.py create` calls `Conductor.register_inline()` after
the run is created: one file write off the event loop, recording origin `chat`, the
session id and `executor: host:inline`. The chat session stays the Run's signed owner.
A registration failure is logged and never fails the create; the Run is then simply
absent from the Conductor view. Inline runs do not count against the background slot.

### 6.2 `background_local`

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

**Human questions.** A Worker Run that needs a person reaches Pipeline status `awaiting_human`. Its
open item appears in the Run's message stream and every reader of it (§8.3,
`pipeline-human-loop-v2.md` §4). The Worker never blocks in `AskUserQuestion`: the
decision or landing request returns at once, the Worker ends its turn with no open tool
call, and the Conductor **parks** the Run at once: its process is recycled to COLD keeping
`_sdk_session_id`, and its slot is released (§5.1). When the item is resolved the Run is
`resume_pending`; on its next slot it resumes with a new turn that carries the input
(`pipeline-human-loop-v2.md` §5).

### 6.3 `remote_hive`

The request names one Hive. The Conductor checks the placement rules (§5.3) and that the
Hive is `active` in the Central `nodes` table and of the right type. The request is then
queued behind that Hive; it is never moved to another Hive.

**Queue and capacity rules** (enforced by Central, not by the Host):

- **Order.** Each Hive's queue is the `queue` GSI (§4.3.2): FIFO by `created_at` then
  `request_id`. V1 has one priority, so there is no aging; the queue position shown is
  exact.
- **Slots.** A claim is one DynamoDB `TransactWriteItems`: move the oldest queued request
  to `dispatching` with `slot_held = true` (condition: still `queued`) and increment the
  node's `active_leases` (condition: `active_leases < slots` and status `active`). Either
  both happen or neither, so a Hive never holds more Attempts than its slots. Every
  releasing transition in §5.1 decrements `active_leases` in the same transaction that
  clears `slot_held`, conditional on it being `true`. A lowered `slots` report takes
  effect for new claims only.
- **Offline target.** Submitting to a Hive with no heartbeat in the last 10 minutes is
  allowed; the confirmation card says the Hive is offline, and the request waits.
- **Revoked target.** Revoking a Hive fails every request still queued for it with
  `failed(node_revoked)`.
- **Queue timeout.** A request still `queued` 24 hours after `created_at` fails with
  `failed(queue_timeout)` (sweeper). The person resubmits if wanted.

**Hive types.**

- **EC2** (external). Existing provisioning in `routers/hive.py`, running
  `SWARMAI_MODE=hive`.
- **Cloud Desktop** (internal). The user installs the hive build on their own Cloud
  Desktop and enrols it with the Central Service using a one-time code issued from the
  Host (§4.3.3). V1 does not launch, stop or delete the machine.

Both types enrol the same way and then only make outbound calls. An EC2 Hive's Caddy
basic auth stays for its own dashboard and is not used by Runs.

**Capacity.** On every heartbeat to the Central Service the Hive reports `slots`, computed on the Hive from its
own `ResourceMonitor.spawn_budget()` and a per-Run memory figure measured in Phase 6 (§12).
The Conductor dispatches at most `slots` concurrent Runs to that Hive, enforced by the
claim transaction above. There is no instance-type table on the Host. A Hive with no
report counts as one slot.

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


### 6.4 Choosing a placement from a Chat tab

A person can ask in a Chat tab for a Run anywhere, not only inline: "run this as a
bugfix pipeline in the background", or "on hive-ec2-1", or "on my Cloud Desktop".

1. The chat agent builds a draft with `pipeline_conductor.py draft` (a Host CLI over the
   owner-only socket, the same one Slack uses in §8.2), giving project, requirement (the
   person's words, stored verbatim as data), a proposed title (§5.4), profile and
   placement. Draft applies the
   placement rules (§5.3) and the DDD pin (§5.2), and lists the Hives allowed for that
   project with their free slots and queue length. The draft is persisted in the Conductor
   Store and signed by the daemon. It binds: actor (the owner), origin session, project,
   profile, placement, DDD revision, base commits, the title and the sha256 of the
   requirement text.
   It is single-use and expires after 10 minutes.
2. The tab shows a confirmation card: title (editable), the requirement, project,
   profile, placement, queue position or free slot, DDD revision and base commits. Editing
   the title makes a new draft. Only the person's click on that card submits;
   the agent has no submit command. The submit route is a human-only dashboard route,
   protected the same way as the decision and `run-land` routes: `run_owner_gate` is
   extended to deny an agent's Bash call to it, as it does for
   `/api/pipelines/<id>/decision` today (a Phase 2 change; today it recognises only that
   route). There is no per-draft confirm secret: a same-user agent could read it from the
   frontend's payload or the daemon's memory, so it would only raise the bar (the B-d
   decision, XG 2026-10-24). That an agent can still reach the loopback route by other
   means is the open item recorded in `pipeline-v1-e7-review.md` item 7 (agent process
   isolation). The route checks that the draft is bound to this owner and origin. Submit
   consumes the draft
   atomically (unused → used) and re-checks placement rules and the bound revisions; if
   any changed, nothing is submitted and the card shows a new draft for a new click. An
   invalid or expired draft shows the rule that failed.
3. After submit, the Run belongs to its Worker (`pipeline:<run_id>`), not to the chat
   session. The tab stays free for chat, and a status card in the tab shows queue
   position, stage and outcome. It reads `GET /api/conductor/requests/<id>` on the `nav`
   pool when the tab is visible, never on the send or stream path.
4. Questions from that Run come back to the same tab as decision cards (§8.3, origin
   `chat`) and to the Pipelines page; the person's input is recorded as `human_input`.
5. Closing the tab does not affect the Run. Its card reappears in that tab's history and
   on the Pipelines page.

## 7. Remote Run flow and completion protocol

### 7.1 Remote Run flow through the Central Service

Host and Hive never call each other. Every step is an outbound call to the Central
Service:

1. **Submit.** The Host Conductor pins the request (§5) and records it locally as
   `submitting` with its `request_id`. It uploads the SwarmAI DDD snapshot if the DDD
   origin is `host` (external only), then creates the Central `requests` record with
   `state: queued`. The create is idempotent by `request_id`, so a retry after a timeout
   is safe. The Host shows the request as queued only after Central acknowledged it; a
   create that keeps failing ends `failed(central_unavailable)` with the reason.
2. **Claim.** The Hive long-polls `POST /v1/nodes/{id}/claim` (20 s wait, then again),
   sending its `worker_incarnation`: a random id the Hive daemon generates at start and
   keeps in its local state. The claim is the transaction of §6.3 and returns the request,
   its attempt and `lease_epoch`.
3. **Run.** The Hive fetches code at the pinned `base_commit`, the DDD at its pinned
   revision, and runs the Worker (§6.2).
4. **Report.** The Hive writes events from its local outbox to
   `PUT /v1/requests/{id}/events/{attempt}/{epoch}/{seq}`: started, stage changed,
   `awaiting_human`, `result_candidate`, failed, `cancel_ack`. `seq` starts at 1 for each
   Attempt and has no gaps. Each event carries the sha256 of its signed payload and, when
   it mirrors a Pipeline event, that event's id and chain position (used to seal the
   Trace, §4.5.1). Its heartbeat (60 s) renews leases and reports `slots`.
5. **Observe.** The Host Conductor reads events for its live remote requests every 10 s
   while it has any, and not at all otherwise. It keeps one cursor per Attempt and
   advances it only over a contiguous run of `seq`; a gap means it waits and asks again
   from the cursor. The Hive keeps resending an event until Central acknowledges it.
6. **Answer and cancel.** The Host records the person's answer in its own decision
   record (the authority, §8.3) and posts a signed answer command to `decisions`, or
   cancels the request (§7.4). The Hive sees both on its next claim poll or heartbeat.

Idempotency: an event write whose key exists with the same `payload_digest` succeeds as a
no-op; one with a different digest is rejected as `equivocation`, recorded and shown
against that node. Requests are idempotent by `request_id`, blobs by digest.

**Hive restart.** A Hive daemon that restarts gets a new incarnation. Before it starts any
Worker it kills every Worker process recorded in its local state, then calls `adopt` for
each Attempt it held. `adopt` is one conditional update on the current epoch that starts
a **new Attempt** (attempt + 1, epoch + 1, the new incarnation) and closes the old one:
Central records `attempt_closed` with the last `seq` it accepted for it. Late writes to
the closed Attempt are rejected, and the Host treats it as complete at that `seq`. Only
then does the Hive resume the Run from the Pipeline checkpoint, as the new Attempt with
its own `seq` from 1.

Credentials (prerequisite of Phase 6). Today `routers/hive.py` keeps the Caddy basic
auth password and AWS access-key configuration as plaintext SQLite fields, and the
instance detail endpoint returns the password. Phase 6 moves these into the Host's
restricted secret store, keeps only a reference in SQLite, and changes every endpoint to
return credential metadata (present / rotated at), never a value. Credentials are never
logged. Node keys never leave their node; revoking a node in the Central Service revokes
its access for Runs.

### 7.2 Completion protocol (one cutover, Phase 6)

Today the combined v1 `land` event both judges and lands. That works while the executor
and the landing repo are on the same Host. A Hive breaks that assumption, so Phase 6
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

Phases 1 to 5 keep today's v1 `land`. The cutover is deliberately not done first: the
Local Worker runs in the same daemon, with the same hooks, signing and landing as an
inline run, and does not need it.

### 7.3 Internal results: draft CR handoff

An internal repo is delivered through its own review system. It still produces all four
facts:

| Fact | Internal Run |
|---|---|
| `pipeline_verdict` | Written by the Cloud Desktop's Pipeline, as for any Run |
| `result_candidate` | The Cloud Desktop creates a **draft** CR with the `cr` tool from the Run's worktree, and records the CR id and the reviewed commit |
| `result_acceptance` | Recorded by the Host as `pending_external_review` on a PASS verdict; on any other verdict no CR is created |
| `integration_landing` | Recorded as `external_handoff` with the CR id. Merging is the person's, in CRUX |

The Run is terminal after `external_handoff`. V1 does not observe the CR afterwards.

What the Cloud Desktop may do: create the draft CR. It never pushes a branch, never
publishes the CR to reviewers and never merges. A draft CR is shared review state owned by
the user, so creating it needs the same per-run authorization a landing needs today.
Spike S6 checks exactly what `cr` uploads in draft mode and that it moves no branch of the
package.

### 7.4 Cancel and result precedence

Cancel and a result race on the same record, so each is one conditional update and
whichever lands first wins. The transitions and slot effects are those of §5.1:

- **Cancel before a Worker runs** (`submitting`, `queued`, `candidate_ready`): straight
  to `cancelled`.
- **Cancel of a running request** (`dispatching`, `running`): → `cancelling`, epoch
  + 1, `cancel_deadline = now + 10 min`, and a random `cancel_token`. The epoch bump fences
  the old Attempt: no later candidate, event or heartbeat from it is accepted. Its next
  call is refused with `cancelling` and the `cancel_token`; the only write it may still make
  is `cancel_ack`, carrying the old epoch and that token, accepted once.
- **On the executor:** the Worker is stopped and the Pipeline abandons the Run with reason
  `cancelled`; then `cancel_ack` moves the request to `cancelled`. Locally this is
  immediate.
- **No ack by the deadline:** `cancelled(unacknowledged)`, and the node is flagged on the
  Control Center. The Run cannot land, because landing requires the current epoch.
- **Once landing has started** (`landing`), cancel is refused; landing finishes or fails
  on its own (§7.2).

## 8. Slack

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
3. The bot replies with a confirmation card listing title, requirement, project, profile,
   placement, DDD ref, base commits and, for a schedule, the cron expression and timezone.
4. The owner confirms on the card. The draft is the same signed, single-use, bound draft
   as §6.4, and the button consumes it with the same atomic re-check; the button carries
   only the draft id, and the Slack adapter accepts the Slack-signed click only from the
   bound owner in that channel and thread (Slack authenticates the person, so this path
   does not depend on the loopback residual). Only then:
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

How a person talks to a Run (answers with text, follow-up rounds, approval outcomes,
guidance, one message stream per Run read by every surface, and the single write path
`submit_human_input`) is specified in `pipeline-human-loop-v2.md`. This section keeps the
Slack nonce rules and the remote answer command, which that design uses. An answer
command carries `kind`, `option_id` and `text` (the person's words) besides the binding
fields below.

A Run in `awaiting_human` posts its decision card to its origin:

| Origin | Where the card goes |
|---|---|
| chat | that chat tab (as today) |
| dashboard | the Pipelines page |
| slack, schedule | the request's Slack thread, plus the Pipelines page |

Pipeline v1 counts only a person's input as a `human_input`, never an unattended pick by
the channel gateway. Today `answered_by="person"` is metadata supplied by the caller, and
the Slack adapter's owner-click check covers only sender-approval cards, not Pipeline
decisions. So a Slack answer needs its own protocol:

1. When the Host posts a decision card to Slack, it creates one nonce per option, bound
   to: Slack team, owner Slack user id, channel and thread ts, `run_id`, `item_id`, round,
   option, `lease_epoch`, and an expiry (the item's 24 h deadline).
2. The nonce is stored on the Host only; the Slack button carries the nonce id, never the
   binding.
3. On a click, the Slack adapter checks that the clicking user is the bound owner and the
   channel/thread match, then consumes the nonce with a compare-and-swap (unused → used).
   A second click, another user, a stale epoch or an expired nonce is rejected and the card
   says why.
4. Only a successful consume calls `submit_human_input` (surface `slack`), the same write
   path the dashboard uses, with the nonce id as evidence.

A typed Slack reply in the Run's thread is never an answer: it becomes guidance
(`pipeline-human-loop-v2.md` §4.4). Resolving an item needs a button, or a modal opened
by a "Reply with note" button for an answer with text. Phase 5's
answer acceptance is gated on this protocol being implemented and tested; spike S5 checks
step 4 end to end.

**Where an answer is authoritative, and how it reaches a remote Run.** Every answer,
whatever its channel (tab, Pipelines page, Slack), is first written to the Host's
authenticated decision record as `answer_received`. That record is the authority (HLD
§13). For a local Run the Host then delivers it as today. For a remote Run:

1. The Host posts an answer command to Central `decisions`, its payload signed with the
   Host command key (§4.3.3) over: `request_id`, `run_id`, attempt, `lease_epoch`, the
   item's semantic digest, the chosen option, the actor, the item deadline (24 h after the
   item opened) and a delivery deadline (`accepted_at` + 1 h).
2. Central **accepts** it in one conditional update, only if the request is `running`,
   `parked` or `resume_pending` (not `cancelling` or terminal), the attempt and epoch are
   current, the item digest matches the open item and the item deadline has not passed.
   Otherwise it is `rejected_stale` or `expired`. Central cannot create or change a
   command. An accepted command's delivery is retried until its delivery deadline; the
   Hive checks only that deadline, never the item deadline, so an answer accepted just
   before the item deadline is still delivered. The Host keeps its command key valid for
   verification at least until the delivery deadline.
3. Only on `accepted` does the Host commit the answer in its decision record; on a
   rejection it records why and the card says so (cancelled, timed out, or the Run moved
   to a new Attempt).
4. The Hive verifies the Host signature, attempt, epoch and item digest, then its daemon
   writes the `human_input` event with the command id as evidence and replies
   `answer_ack`. The parked Run becomes `resume_pending` and receives the input in its
   resume turn (`pipeline-human-loop-v2.md` §5.2). A repeated command is a no-op.

Precedence follows from step 2 being one conditional update on the same record as cancel,
`adopt` and the decision timeout: whichever lands first wins. A cancel or timeout first
makes the answer `rejected_stale` / `expired`; an accepted answer first makes the timeout
not fire (§4.3.3), and a later cancel still cancels the Run. A lease renewal of a parked
Run keeps its Attempt and items, so an answer then is accepted against the new epoch. A
running `adopt` (new Attempt) before the input was delivered supersedes the old Attempt's
items; if the new Attempt asks an item with the same semantic digest, the daemon writes `input_rebound` and reuses
the committed answer, so the person is not asked twice (`pipeline-human-loop-v2.md` §3.5).
Any difference is a new item.

Spike S8 checks step 4 end to end: a parked Hive Run receives an answer command, resumes
with the input in a new turn and acknowledges it, with no dangling tool call.

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

Cancel is always available to the owner until landing starts (§7.4).

## 10. Surfaces

Every surface below reads the Conductor Store and the Runs' message streams
(`pipeline-human-loop-v2.md` §4); none keeps its own copy of a Run's state. Every surface
names a Run by its title (§5.4). The mockup is
`docs/design/pipeline-conductor-ui-mock.html`.

- **Pipelines overlay.** It becomes the live control surface, designed for a person who
  reviews and judges 20 to 30 Runs at a time. Today's retro content moves to its History
  tab unchanged, with two added columns (where the Run executed, org copy state). The
  unit on screen is the **Run card**, never a single message: a mixed message feed of 30
  Runs cannot be read.
  - **Executor strip** at the top: Chat tabs, Local Worker, each Hive, with slots used /
    total and queue length.
  - **Board** (tab Runs): four columns, each a list of Run cards, scrolled on its own.

    | Column | Cards | Order |
    |---|---|---|
    | Needs you | Runs with an open item (question or approval) | longest wait first |
    | Running | `dispatching`, `running` without an open item | stage, then start time |
    | Waiting | `queued` (with position), `resume_pending` (answered, resuming next), `cancelling` | `resume_pending` first, then queue order |
    | Delivered (24 h) | Runs that reached `done` / `failed` / `cancelled` in the last 24 h, with the five-dimension audit line | newest first |

    A card shows: title (§5.4), project · profile · executor, a stage bar, age, an unread
    count of notices, and for Needs you the open item in one line with its quick actions
    (option buttons, Approve). Filters: project, executor, origin, text search on titles.
  - **Review view**: opening a card shows it on the right, the board narrowing to a card
    list on the left. It holds the title and full requirement, dispatch facts, and tabs
    Thread (that Run's message stream with the reply box), Changes (diff summary), Review
    (findings), Report. Previous / Next (`J` / `K`) walk one of two queues: **Needs you**
    (longest wait first) or **Delivered to review** (failed, then unread, then newest), so
    a person can go through every waiting or finished Run without returning to the board.
    What a question and an approval card must show (rationale, option consequences,
    evidence before Approve), unread state and the limits on batch actions are in
    `pipeline-human-loop-v2.md` §6.
  - **Activity** tab: the aggregate message feed of all Runs, for audit, filterable; not
    the main working view.
  - Tabs Schedules, History, Hives as before.

  Live updates come over one SSE connection opened only while the overlay is visible, on
  the `nav` pool, reading the stream logs and the Conductor Store; no polling of git or
  files.
- **Control Center.** The org view of Runs, nodes and registered DDDs / Project Agents
  reads the Central Service. Publishing a DDD or Project Agent revision and enrolling or
  revoking a node happen here.
- **Chat.** No visible change for inline runs. For other placements: the confirmation
  card (§6.4) and one status card per Run in the originating tab, rendered from the Run's
  stream, where its open items can be answered.
- **Slack.** §8; the Run's thread is a reader of its stream.
- **Left nav.** The Pipelines item shows the count of open items.
- **API.** `POST /api/conductor/requests`, `GET /api/conductor/requests[/<id>]`,
  `POST /api/conductor/requests/<id>/cancel`, `GET /api/conductor/capacity`,
  `GET /api/conductor/stream` (SSE), `POST /api/conductor/runs/<id>/input`.
  Endpoints run on the `nav` pool, never on the chat path.

## 11. Pipeline five dimensions

| Dimension | Effect of this design | Guard |
|---|---|---|
| Run Isolation | Unchanged locally: each Run keeps its own worktree. On a Hive each Run gets its own clone at the pinned base | Placement rules keep internal code off EC2; Central stores every result encrypted at rest and audited; Hives never push to a protected branch; external landing is a CAS on the Host, internal results are draft CRs |
| Quality | Unchanged. The same Pipeline, gates and adversarial review run in every placement | `pipeline_verdict` is immutable; the Host never lands without a PASS verdict or a recorded human acceptance |
| Autonomy | Background, remote and scheduled Runs need no open chat tab. Human questions reach the origin | Only the owner can submit; only a person's pick counts as an answer |
| Speed | One extra local Run in parallel, plus Hive slots | One stream stays reserved for chat and Slack; the Conductor is off the send and stream paths |
| Token Cost | One more concurrent local Run at most. Hive Runs use Hive credentials and budgets | Schedule overlap is skipped, not stacked; missed fires are not replayed |

## 12. Phases and acceptance

Measure first, then build. Each phase starts with its acceptance tests written and red.

| Phase | Scope | Acceptance |
|---|---|---|
| 1 | Conductor Store, `submit` / `register_inline`, status join, API, Pipelines page columns | An inline Run appears in the Conductor view with origin `chat`; chat send and stream timings are unchanged (measured before and after); a failing registration never fails `create`; `create --title` is recorded and shown, and the fallback title is tested for an empty title, a multilingual requirement, a duplicate title and a scheduled fire |
| 2 | Local Worker (headless `SessionUnit`, `pipeline:<run_id>`), one slot, atomic two-class stream admission, `pipeline_v1.py create --ddd-root` with a materialized immutable DDD, crash recovery; Chat tab placement choice for `background_local` (§6.4) | A background `direct` Run reaches `land` with no tab open; its contract records the DDD digest and editing the Host DDD mid-run does not change what it reads; a second request queues; with 2 interactive streams active the Worker waits and chat still gets a stream; killing the daemon mid-BUILD resumes on restart as attempt 2; from a Chat tab, asking for a background Run shows a confirmation card, only the click submits, the tab stays usable while the Run works, and the Run's questions come back to that tab; an answer with a note given in the overlay reaches the Worker in its resume turn, which acks it, and the tab's card and the overlay both show it answered; a Worker's question ends its turn and parks the Run at once, freeing the slot for the next queued request, and answering it resumes the Run ahead of the queue with no dangling tool call (S9); the red tests listed in `pipeline-human-loop-v2.md` §8 pass; a Bash call to the submit route is denied, a second click on a used draft, an expired draft, or a draft whose DDD revision or base commit moved submits nothing |
| 3-0 | Contracts before code: wire and key specification (§4.3.6) with test vectors; DynamoDB, event, Run-record, trace-cursor and audit schemas; `ddd_origin` in `ddd_bindings.py`; the four-fact event schema; contract fields the Run record needs (`ddd_ref`, `code_ref`, placement, evidence contract id); per-stage, per-model usage instrumentation in `run_protocol/usage.py`; the answer-command envelope (§8.3) | Reviewed documents and red conformance tests for: crash after claim, out-of-order and duplicate events, duplicate-with-different-payload, cancel racing a result, Hive restart and adopt, lease across a simulated outage, key loss and rotation, a poisoned blob upload, a malformed or badly signed Run record that stays in quarantine, a text secret that is registered only through a redacted projection while the canonical record still verifies, a secret in a result bundle that rejects the upload, a sealed Trace with a mirror gap, a mirror write racing the seal, two claims racing for a Hive's last slot, every row of the §5.1 table releasing a slot exactly once, an answer racing cancel, timeout and adopt |
| 3 | Central Service in the Bedrock account (CDK: HTTP API, Lambdas, DynamoDB, S3, KMS key, sweeper); Host enrolment and signed requests; DDD and Project Agent registry; Run-record upload from the Host outbox for inline and Local Runs; intake with redaction; audit; Control Center reads Central | Every finished local Run with upload on hands its record to Central, which passes intake; with `central_upload: "off"`, or `--no-upload`, a local Run sends nothing to Central and behaves the same locally; a remote request ignores the setting and always uploads; a planted fake secret appears masked in the projection and recorded in audit, while the canonical record still verifies; a Central outage leaves local Runs unaffected and their records upload afterwards; a replayed upload is a no-op; a node with a wrong signature, stale timestamp or reused nonce is refused |
| 4 | First consumers: Trace (live view, sealed Trace, `run_index`) and Offline Eval (`eval_jobs`, `eval_reports`, intervention classification, the five dimensions) | An uploaded local Run shows per-stage times, tokens and every human answer in the Control Center from Central data only; a dropped event marks its Trace `incomplete`; an Eval report gives all five dimensions or `UNKNOWN`, never a guessed PASS; rebuilding `run_index` from Run records gives the same statistics; `local only` Runs are absent from org stats, which are labelled "of Runs received by Central", and are counted only on the Host's own dashboard; with Central down, local Runs, chat, hooks and MEMORY behave exactly as before |
| 5 | Slack submit (owner only), confirmation card, `pipeline_submit` job, thread updates, Slack answer button | A non-owner cannot submit; a scheduled Run fires and reports in its thread; a missed fire is recorded `missed(host_offline)` and never submitted late; an overlapping fire is `skipped_overlap`; a replayed occurrence key is a no-op; an owner button press consumes its nonce once and records `human_input`, while a second click, a non-owner click or an expired nonce is rejected |
| 6 | Hive credentials moved to the secret store; four-fact completion protocol with landing journal for every placement; EC2 Hive enrolment and Worker through Central; leases; capacity reporting; DDD snapshot manifest and scan | from a Chat tab, a Run can be placed on an EC2 Hive and only Hives allowed for the project are offered; An external Run on EC2 hands in its Run record; the Host takes the result from Central, verifies, re-tests and lands it; a claim raced by two nodes has one winner; a Hive never holds more Attempts than its slots; a queued request fails `queue_timeout` after 24 h and `node_revoked` when its Hive is revoked; an answer given in the tab reaches the Hive Worker once, is acknowledged, and a stale-epoch answer is refused; a lease survives a simulated 15-minute Central outage and a dead Hive's lease expires 10 minutes after recovery; a revoked-epoch event is rejected; an internal project is rejected for EC2; a crash mid-landing reconciles without double-landing |
| 7 | Cloud Desktop enrolment and Worker; `ddd_origin: repo` fetch with internal git; draft CR handoff. Gated on S6 | from a Chat tab, an internal project offers only Cloud Desktop Hives; An internal Run with an IVTHubSpec-hosted DDD runs on a Cloud Desktop, hands in its Run record and records all four facts ending in `external_handoff` with a draft CR; an external project is rejected for a Cloud Desktop |

Slack scheduling to a named Hive is part of phases 6 and 7 once those Hives exist.

### 12.1 Spikes before code

| ID | Question | Blocks |
|---|---|---|
| S1 | Can a headless `SessionUnit` with app session id `pipeline:<run_id>` get hook tokens from `run_owner_gate` and land a `direct` Run, the way channel sessions run without a tab? | Phase 2 |
| S2 | Memory and stream use of one Pipeline Run on the Host, measured; confirms that one extra Worker fits next to chat | Phase 2 |
| S3 | Per-Run memory on a Hive, measured, to turn `spawn_budget()` into `slots` | Phase 6 |
| S4 | Lambda authorizer for Ed25519-signed requests: measured added latency, and that a Cloud Desktop can reach the API Gateway endpoint outbound | Phase 3 |
| S5 | How an owner's Slack button press becomes a `human_input` through `submit_human_input`, without the gateway's unattended path | Phase 5 |
| S6 | Headless Midway, internal git and `cr` on a Cloud Desktop. `ddd_bindings.bind_repo` defers internal bindings today ("needs Brazil+Midway headless auth") | Phase 7 |
| S7 | Size of a real Run record (documents, reflect summary, delivery record, event chain with its blobs), measured over recent SwarmAI Runs, to confirm one upload per Run and set S3 lifecycle | Phase 3 |
| S8 | A parked Hive Run: answer command → `human_input` → `resume_pending` → resume turn with the input → `input_ack`, with no dangling tool call | Phase 6 |
| S9 | Locally: Worker decision-request ends the turn → park (COLD) → answer → `--resume` with a new turn carrying the input; the resumed SDK conversation has no unmatched `tool_use`; and guidance delivered by PostToolUse `additionalContext` during a long BUILD | Phase 2 |

If S1 fails, Phase 2 stops and the design is revised; the jobs `--bare` path is not used
as a fallback. If S6 fails, internal remote Runs stay unsupported and Phase 7 is
deferred; external Runs are unaffected.

## 13. Cost on hot paths

- Chat send and stream: no new work. Inline registration is one file write on the
  Conductor executor after `create` returns.
- Daemon idle: no Central polling without a live remote Run (10 s while it has one), plus
  one outbox flush every 30 s when there are new events, on the Conductor executor.
- Central Service: DynamoDB on-demand and Lambda are billed per request, so idle cost is
  storage plus the one-minute sweeper. Phase 3 measures the real monthly cost before
  Hives are enabled.
- Each file write: none added outside the Conductor Store and the Run's own files.

Phase 1 measures chat send-to-first-token and stream timings before and after the change
on the same machine; a regression blocks the phase.

## 14. Changes to the HLD

The HLD (`project-agent-delivery-os-hld.md`) originally put the protocol cutover
(Phase 1b) before local background execution. It is now edited to match this design:
Phase 1b ships with the Hive executor (Phase 6), local background execution runs on the
current v1 landing until then, and execution-plane phasing points here. The move is sound
only under two conditions, both kept here: Phases 1 to 5 create no candidate or acceptance
authority, and Phase 6 moves every placement to the four facts in one change.

The HLD's Host-to-Hive Run Exchange (§7) is realised through the Central Service: its
store split is kept, the transport becomes outbound calls from both sides, and lease
epoch authority for remote Runs moves from the Host to the Central Service.
