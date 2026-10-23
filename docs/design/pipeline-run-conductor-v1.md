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
Service** (§4.3), which also stores every Run's result and runs the learning pipeline
that turns results into DDD changes (§4.4) in the AWS account SwarmAI uses for Bedrock. It registers DDDs,
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
- It stores the full result of every Run, from every placement, and is the one place
  results are reviewed, distilled and reflected back into DDDs (§4.4).
- It never writes a DDD store itself. Approved changes are applied through a per-DDD
  storage adapter, so new DDD storage types plug in without changing Central (§4.4.3).
- V1 runs in internal accounts only, so content is stored as is (encrypted at rest, access
  by role, every access audited). Field-level sealing is deferred (§4.3.4). The SwarmAI
  DDD is the first end-to-end case; every other DDD follows the same path.

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
- Field-level sealing of internal content in Central. V1 runs in internal accounts.
- DDD storage types other than Host files and git repos. The adapter interface (§4.4.3)
  is where they are added later.
- Backward compatibility for runs created before the Phase 6 protocol cutover.
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
| A Run's full result (Run record) | **Central Service** result store (§4.4.1) | Learning worker, Host landing, Offline Eval |
| Proposed, approved and applied DDD changes, audit | **Central Service** learning pipeline (§4.4) | Control Center |
| DDD content after an approved change | The DDD's own store, written only by its adapter (§4.4.3) | Executors |
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

From Phase 3 the Host also writes a local outbox (`outbox.jsonl`) of its own Runs'
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
- **S3**, block public access, SSE-KMS, for large immutable content (§4.3.2 blobs).
- **Sweeper**: an EventBridge rule runs a Lambda every minute to expire leases (§4.3.3)
  and decisions, and to probe the public API (§4.3.3 lease barrier).

#### 4.3.2 Records

| Table | Key | Holds |
|---|---|---|
| `nodes` | `node_id` | type (`host`, `ec2`, `cloud_desktop`), allowed class, Ed25519 public key, reported `slots`, last heartbeat, status (`enrolling`, `active`, `revoked`) |
| `ddds` | `ddd_id`, `rev` | origin (`host` / `repo`), storage adapter (§4.4.3), access class, clone URL, pinned commit or snapshot digest, published at |
| `project_agents` | `agent_id`, `rev` | definition (or its blob digest), pinned `ddd_id` + `rev`, published at |
| `requests` | `request_id`; GSI `(target_node, state)`; GSI `(state, updated_at)` | the immutable request, dispatch state (§7.4), attempt, `lease_epoch`, `lease_renewed_at` (Central clock), `worker_incarnation`, `cancel_deadline` |
| `events` | `request_id`, sort key `<attempt>#<lease_epoch>#<seq>` (zero-padded) | mirrored Run events with their signed `payload_digest` (§7.1) |
| `decisions` | `decision_id`; TTL | question, options, answer, answer nonce state, expiry |
| `blob_objects` | `digest` | size, media type, first registered at. One row per distinct content |
| `blob_refs` | `owner_id` (request or DDD revision), `role`; GSI `digest` | the digest, producing node, attempt, epoch, producer signature. One row per use, so the same content used by many Runs keeps every provenance |
| `learning_jobs` | `job_id`; GSI `(destination, state)` | §4.4.2 |
| `change_sets` | `change_set_id`; GSI `(destination, state)` | §4.4.3 |
| `audit` | `day`, `ts#id` | §4.4.5 |
| `enrolments` | `code_hash`; TTL | target node, attempts left, consumed flag (§4.3.6) |

| S3 prefix | Holds |
|---|---|
| `quarantine/<upload_id>` | Raw uploads awaiting intake. Readable only by the intake Lambda; deleted when intake ends, and by a 1-day lifecycle rule as a backstop |
| `blobs/sha256/<digest>` | Verified DDD snapshots, Run records and result bundles (§4.4.1). Kept until the owner deletes them; lifecycle rules are set in Phase 3 from measured volume |

How a blob gets in. Content addressing alone does not stop an authorized writer from
putting wrong bytes under a digest first, so:

1. A writer asks Central for an upload; Central returns a presigned PUT to
   `quarantine/<upload_id>`, scoped to that one key, valid 15 minutes, bound to the
   writer's request, attempt and epoch, and requiring `x-amz-checksum-sha256` equal to the
   claimed digest. S3 rejects a body whose checksum differs.
2. The writer calls `complete` with its signature over the digest, size, media type and
   role. Central checks that the stored object's checksum is that digest.
3. **Intake runs on the quarantined object before anything is registered** (§4.4.2). Text
   content is redacted and becomes a new object with its own digest; the raw object is
   then deleted. A result bundle (code) is never rewritten: a secret finding in it rejects
   the upload and the candidate cannot land.
4. Central writes the `blob_objects` row (if new) and a `blob_refs` row for this use, and
   copies the redacted object to `blobs/sha256/<digest>` with `If-None-Match: *`. Content
   that already exists is not copied again; the new `blob_refs` row still records this use.
5. Every reader verifies the sha256 after download and refuses a mismatch.

No node can write to `blobs/` directly.

A DDD or Project Agent revision is **published** from the Host Control Center. Only a
published revision can be pinned by a remote request. Publishing a `repo`-origin DDD
records the commit; the content stays in its repo.

#### 4.3.3 Identity, authorization and leases

- **Host (admin).** Calls the admin API with SigV4, through a dedicated least-privilege
  IAM role in the account. It does not reuse the Bedrock role.
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
  - *claim* is one conditional update: `state = queued AND target_node = me` →
    `running`, attempt + 1, `lease_epoch` + 1, `worker_incarnation = <the caller's>`,
    `lease_renewed_at = now`;
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
  `awaiting_human`. The question expires after 4 hours (the existing answer timeout);
  the Run then ends `abandoned(decision_timeout)` and its slot is released. The same rule
  applies to the Local Worker.

#### 4.3.4 Data handling

V1 runs only in internal accounts, so Central stores Run content as it is:

- DynamoDB and S3 are encrypted with a customer-managed KMS key; only the Central
  Lambdas and the Host admin role can use it.
- Every read and write of a Run record or blob is recorded in the audit table (§4.4.5).
- Content is redacted at intake (§4.4.2), before anything is distilled from it.
- `classify_project()` still decides **where** a Run may execute (§5.3). It no longer
  decides what Central may store.
- Field-level sealing is deferred. If a later DDD needs it, it is added at intake, where
  every result already passes.

#### 4.3.5 What Central never does

- It is never on the chat send or stream path, and Host-local Runs never wait for it.
- It holds no Pipeline signing key and no credential value. It is not a second
  authoritative copy of any code or DDD.
- It does not execute anything; it stores, authorizes and expires.
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

### 4.4 Result store and learning pipeline

Every Run, wherever it ran, hands its result to Central. Review, distillation, reflection
into DDDs, quality, redaction and audit all happen in one place, decoupled from where Runs
execute and from how each DDD is stored.

#### 4.4.1 What a Run hands in

When the Run is terminal, after REFLECT has completed (`reflect_complete` or
`reflect_exhausted`) and `REPORT.md` and usage have been finalized, the executor uploads
one **Run record**. This holds for every placement, including an internal Run, whose
order is `external_handoff` → REFLECT → `completed` → upload. It holds exactly three kinds
of content, plus the metadata that ties them together:

| Part | Contents | Source today |
|---|---|---|
| Pipeline documents | `REQUIREMENTS.md` and `DESIGN_SPEC.md` (profiles that have them), `REVIEW.md`, `FIXING_SPEC.md` (if review asked for fixes), `REPORT.md` | Rendered by the Pipeline into the run directory (`run_protocol/documents.py`, `engine.py`) |
| Reflect summary | Each lesson the agent wrote, with its destination, relation and the judge's verdict | `artifacts/reflect.json` |
| Delivery record | Commits: per repo the base, landed commit and message. Or CR: package, CR id, revision, commit, state at handoff (draft) | The landing result (`run_protocol/landing.py`), or the CR handoff (§7.3) |
| Metadata | Below | Conductor and Pipeline records |

The record is a versioned manifest (`run_record_version: 1`). Metadata fields:

| Field | Required for |
|---|---|
| `request_id`, `run_id`, project, profile, origin, placement, executing node | all |
| `ddd_ref` (adapter, revision, digest), Project Agent Definition revision if any | all |
| `code_ref` per repo (base commit) and owned paths | all |
| Pipeline protocol revision and evidence contract id | all |
| Attempts (attempt, epoch, node, start, end, reason) | all |
| The four completion facts and the landing outcome | all |
| Candidate tree digest per repo and the result bundle digest | Runs that changed code |
| Signed event chain: head digest and event count | all |
| Token and time usage | all |
| Digests of transcripts and logs | all |

The **result bundle** is a separate blob, referenced by digest. Per repo it holds the base
commit, the ordered patch series (`git format-patch`) and the candidate tree digest. The
Host lands from it (§7.2): it applies the series on the base, checks the tree digest, then
runs the landing. Internal Runs have no result bundle; their delivery record names the CR.

Transcripts, full logs and the signed event chain stay on the node that ran the Run; the
Run record carries only their digests, so they can be checked if fetched later.

This applies to every placement, inline and Local Runs included: the Host uploads them
from its outbox (§4.2). A Run that cannot upload keeps its record locally and retries;
nothing is lost because Central was down.

#### 4.4.2 Learning stages

| Stage | What happens | Where it runs |
|---|---|---|
| Intake | Schema and digest check; redaction scan of all text (secrets and credential shapes masked, findings recorded); a record that fails is quarantined, not learned from | Central Lambda |
| Review and distill | Each reflect lesson, and anything distilled from the documents and delivery records across Runs, becomes a proposal and goes through the existing admission logic: `ingestion_gate` (noise floors, quality, the self-adversarial judge) and `reflect_job.judge`; related proposals across Runs are merged; `contradicts` proposals are held for a person | **Learning worker** on a SwarmAI node (the Host in V1), which claims learning jobs from Central like a Hive claims Runs |
| Approve | Verdict `auto` (DDD knowledge passing every gate), `review` (anything touching Gates, rules or governed context files, and every `contradicts`) or `discard`. `review` items wait for the owner in the Control Center | Central records the decision |
| Apply | The approved change set is applied to its destination (a DDD, or another store such as MEMORY or EVOLUTION) through that destination's adapter (§4.4.3), by the one node that holds the adapter | The adapter's node |
| Record | The applied revision is recorded, published for a DDD (§4.3.2), and linked back to the Runs and proposals it came from | Central |

The learning worker reuses SwarmAI's existing code instead of reimplementing it in a
Lambda, and runs as a background job, never on the chat path or in the Local Worker slot.

**Learning jobs.** Intake creates one `learning_jobs` row per accepted Run record:

| State | Next | Written by |
|---|---|---|
| `queued` | `claimed` | learning worker (conditional claim with lease and epoch, as for Runs) |
| `claimed` | `proposed`, `queued` (lease lost), `failed` | worker / sweeper |
| `proposed` | done when every proposal has a change set or a `discard` | Central |
| `failed` | `queued` (retry, at most 3), then `dead_letter` | sweeper |

- The worker is a node enrolled with the role `learning` (the Host in V1). It may claim
  learning jobs, read the Run records they name and write proposals and change sets. It
  may apply only to destinations whose adapter it holds (§4.4.3). It cannot create or
  claim Run requests.
- A `dead_letter` job is shown in the Control Center with its last error.
- If the adapter node is offline, approved change sets stay `approved` and are applied
  when it returns. Nothing is applied by another node.

**What changes in the Pipeline.** Today `reflect_job.route` writes a kept lesson directly
on the executing machine: `ddd_cultivation.apply_to_ddd` or `annotate_entry` for DDD
docs, and the project's local `.artifacts/proposals/` for MEMORY / EVOLUTION. From
Phase 4, `route` no longer writes; its decision and the lesson become proposals in the
Run record. No executor writes a DDD. Before Phase 4 it works as today.

#### 4.4.3 Destination adapters

Central knows each destination of learned content only through its adapter. A DDD
declares its adapter on the `ddds` record; other destinations (MEMORY, EVOLUTION, a
future knowledge store) are registered the same way.

Each approved change becomes a **change set** with a `change_set_id`, its destination and
the `base_rev` it was reviewed against. Change sets of one destination are applied one at
a time, in approval order.

| State | Meaning |
|---|---|
| `approved` | Waiting for the destination's turn |
| `applying` | The adapter node holds it (lease, as for jobs) |
| `handed_off` | `git_repo` only: a PR or CR is open; waiting for it to merge |
| `applied` | The destination now contains it; `applied_rev` recorded |
| `conflict` | The destination moved past `base_rev`; back to review |
| `failed` | The adapter reported an error; retried at most 3 times |
| `reverted` | A later revert change set removed it |

Adapter operations, all idempotent by `change_set_id`:

| Operation | Meaning |
|---|---|
| `read(rev)` | Materialize the destination at a revision (used by §5.2) |
| `head()` | Current revision |
| `apply(change_set)` | If the destination is at `base_rev`, apply and return a receipt with `applied_rev`; if this change set was already applied, return the same receipt; otherwise `conflict` |
| `status(change_set_id)` | `git_repo`: whether its PR / CR merged and at which revision |
| `revert(change_set_id)` | Apply the inverse as a new change set, through the same path |

A change set is all or nothing for its destination. Changes that span destinations are
separate change sets and are never assumed atomic together.

V1 has two adapters:

- `host_files` (SwarmAI DDD and the Host's MEMORY / EVOLUTION): writes through
  `locked_write.py` under the file lock. The revision is the sha256 of the destination's
  files; each applied change set is journalled with its inverse, so `revert` is exact.
- `git_repo`: `apply` opens a PR (GitHub) or a draft CR (internal git) and moves the change
  set to `handed_off`. The destination's revision is published only when `status` sees the
  merge. While one change set is `handed_off`, later ones for the same destination wait.

A new storage type, or a new kind of destination, is a new adapter and a new registered
value; Central, the learning stages and the Pipeline do not change. SwarmAI's own DDD on
`host_files` is the first end-to-end case.

#### 4.4.4 Every writer goes through the adapter

Today several components write these destinations directly:

| Writer | Writes | Source |
|---|---|---|
| REFLECT routing | DDD docs (`apply_to_ddd`, `annotate_entry`), proposal queue | `run_protocol/reflect_job.py` |
| Improvement write-back hook | `IMPROVEMENT.md` via `apply_to_ddd` | `hooks/improvement_writeback_hook.py` |
| DailyActivity hook | `TECH.md` | `hooks/daily_activity_hook.py` |
| Context health hook | MEMORY and DDD entry lifecycle (ref bump, decay), indexes | `hooks/context_health_hook.py` |
| Distillation hook | MEMORY.md | `hooks/distillation_hook.py` |
| Correction capture | MEMORY.md | `core/runtime_hooks.py` |
| Save to Memory | MEMORY.md | `core/memory_extractor.py` |
| Evolution optimizer and maintenance hook | EVOLUTION.md, SKILL.md | `core/evolution_optimizer.py`, `hooks/evolution_maintenance_hook.py` |
| DDD cultivation | DDD docs | `core/ddd_cultivation.py`, `core/cultivation_dispatcher.py` |

V1 rule:

- **Content learned from Pipeline Runs** goes only through Central (§4.4.2), from Phase 4.
- **Content learned from chat sessions** (the hooks above, Save to Memory) keeps being
  decided on the Host in V1, so chat behaviour does not change. But it no longer writes
  files itself: every one of these writers calls the `host_files` adapter, which is the
  only code that writes those files. Its change sets are recorded in Central's audit
  when Central is reachable and queued locally when not.
- Lifecycle edits (ref bump, decay, deprecation) are change sets too, so the revision and
  the journal stay complete.
- A structural test fails the build when any module other than the adapter writes a
  registered destination path.

Moving session learning itself to Central is a later decision; with every write already
behind the adapter, it changes who proposes, not who writes.

#### 4.4.5 Audit

Central keeps an append-only `audit` table: every Run record received, every redaction
finding, every gate verdict, every human approval, every learning job and change set
transition, every applied change with its adapter, base and resulting revision, every
revert, and every read of Run content. Any DDD line can be traced to
the Runs and decisions that produced it, and an applied change set can be reverted through
its adapter.

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
       | cancelling | done | failed | cancelled
  attempts:
    - attempt: 1
      executor: host:inline | host:worker | hive_<id>
      lease_epoch: 1           # remote Runs: advanced only by Central (claim, adopt, cancel)
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
| `running` | `candidate_ready`, `running` (new Attempt), `failed`, `cancelling` | Conductor on executor events |
| `cancelling` | `cancelled` | `cancel_ack`, or the cancel deadline (§7.4) |
| `candidate_ready` | `landing`, `running` (new Attempt for rebase), `failed`, `cancelling` | Conductor (Phase 6+) |
| `landing` | `done`, `failed` | Landing result |

Before Phase 6, an inline or Local Run goes `running` → `done` directly: today's v1 `land`
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
own `ResourceMonitor.spawn_budget()` and a per-Run memory figure measured in Phase 6 (§12).
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

1. **Submit.** The Host Conductor pins the request (§5) and records it locally as
   `submitting` with its `request_id`. It uploads the SwarmAI DDD snapshot if the DDD
   origin is `host` (external only), then creates the Central `requests` record with
   `state: queued`. The create is idempotent by `request_id`, so a retry after a timeout
   is safe. The Host shows the request as queued only after Central acknowledged it; a
   create that keeps failing ends `failed(central_unavailable)` with the reason.
2. **Claim.** The Hive long-polls `POST /v1/nodes/{id}/claim` (20 s wait, then again),
   sending its `worker_incarnation`: a random id the Hive daemon generates at start and
   keeps in its local state. The conditional claim returns the request, its attempt and
   `lease_epoch`.
3. **Run.** The Hive fetches code at the pinned `base_commit`, the DDD at its pinned
   revision, and runs the Worker (§6.2).
4. **Report.** The Hive writes events from its local outbox to
   `PUT /v1/requests/{id}/events/{attempt}/{epoch}/{seq}`: started, stage changed,
   `awaiting_human`, `result_candidate`, failed, `cancel_ack`. `seq` starts at 1 for each
   Attempt and has no gaps. Each event carries the sha256 of its signed payload. Its
   heartbeat (60 s) renews leases and reports `slots`.
5. **Observe.** The Host Conductor reads events for its live remote requests every 10 s
   while it has any, and not at all otherwise. It keeps one cursor per Attempt and
   advances it only over a contiguous run of `seq`; a gap means it waits and asks again
   from the cursor. The Hive keeps resending an event until Central acknowledges it.
6. **Answer and cancel.** The Host writes the human answer to `decisions`, or cancels the
   request (§7.4). The Hive sees both on its next claim poll or heartbeat.

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
whichever lands first wins:

- **Cancel of a queued request:** `queued` → `cancelled`.
- **Cancel of a running request:** `running` or `candidate_ready` → `cancelling`, epoch
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

Cancel is always available to the owner until landing starts (§7.4).

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
| Run Isolation | Unchanged locally: each Run keeps its own worktree. On a Hive each Run gets its own clone at the pinned base | Placement rules keep internal code off EC2; Central stores every result encrypted at rest and audited; Hives never push to a protected branch; external landing is a CAS on the Host, internal results are draft CRs |
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
| 3-0 | Contracts before code: wire and key specification (§4.3.6) with test vectors; DynamoDB, event, Run-record and audit schemas; `ddd_origin` and adapter fields in `ddd_bindings.py`; the four-fact event schema | Reviewed documents and red conformance tests for: crash after claim, out-of-order and duplicate events, duplicate-with-different-payload, cancel racing a result, Hive restart and adopt, lease across a simulated outage, key loss and rotation, a poisoned blob upload, a Run record that fails intake |
| 3 | Central Service in the Bedrock account (CDK: HTTP API, Lambdas, DynamoDB, S3, KMS key, sweeper); Host enrolment and signed requests; DDD and Project Agent registry; Run-record upload from the Host outbox for inline and Local Runs; intake with redaction; audit; Control Center reads Central | Every finished local Run's record reaches Central and passes intake; a planted fake secret is masked and recorded; a Central outage leaves local Runs unaffected and their records upload afterwards; a replayed upload is a no-op; a node with a wrong signature, stale timestamp or reused nonce is refused |
| 4 | Learning pipeline: `learning_jobs` and `change_sets`, learning worker on the Host, review and approve in the Control Center, `host_files` adapter with journal and revert, every writer of §4.4.4 moved behind the adapter with the structural test, `reflect_job.route` switched to proposals | A SwarmAI Run's lessons arrive as proposals and no executor writes the DDD; an `auto` lesson is applied through `host_files` and appears in `TECH.md` etc. with an audit trail back to its Run; a Gates change waits for approval; a change whose base moved returns to review; an applied change set can be reverted |
| 5 | Slack submit (owner only), confirmation card, `pipeline_submit` job, thread updates, Slack answer button | A non-owner cannot submit; a scheduled Run fires and reports in its thread; a missed fire is recorded `missed(host_offline)` and never submitted late; an overlapping fire is `skipped_overlap`; a replayed occurrence key is a no-op; an owner button press consumes its nonce once and records `human_answer`, while a second click, a non-owner click or an expired nonce is rejected |
| 6 | Hive credentials moved to the secret store; four-fact completion protocol with landing journal for every placement; EC2 Hive enrolment and Worker through Central; leases; capacity reporting; DDD snapshot manifest and scan; `git_repo` adapter for GitHub DDDs | An external Run on EC2 hands in its Run record; the Host takes the result from Central, verifies, re-tests and lands it; a claim raced by two nodes has one winner; a lease survives a simulated 15-minute Central outage and a dead Hive's lease expires 10 minutes after recovery; a revoked-epoch event is rejected; an internal project is rejected for EC2; a crash mid-landing reconciles without double-landing |
| 7 | Cloud Desktop enrolment and Worker; `ddd_origin: repo` fetch with internal git; draft CR handoff; `git_repo` adapter for internal git. Gated on S6 | An internal Run with an IVTHubSpec-hosted DDD runs on a Cloud Desktop, hands in its Run record and records all four facts ending in `external_handoff` with a draft CR; its approved lessons are applied to IVTHubSpec as a draft CR; an external project is rejected for a Cloud Desktop |

Slack scheduling to a named Hive is part of phases 6 and 7 once those Hives exist.

### 12.1 Spikes before code

| ID | Question | Blocks |
|---|---|---|
| S1 | Can a headless `SessionUnit` with app session id `pipeline:<run_id>` get hook tokens from `run_owner_gate` and land a `direct` Run, the way channel sessions run without a tab? | Phase 2 |
| S2 | Memory and stream use of one Pipeline Run on the Host, measured; confirms that one extra Worker fits next to chat | Phase 2 |
| S3 | Per-Run memory on a Hive, measured, to turn `spawn_budget()` into `slots` | Phase 6 |
| S4 | Lambda authorizer for Ed25519-signed requests: measured added latency, and that a Cloud Desktop can reach the API Gateway endpoint outbound | Phase 3 |
| S5 | How an owner's Slack button press becomes a `human_answer` without the gateway's unattended path | Phase 5 |
| S6 | Headless Midway, internal git and `cr` on a Cloud Desktop. `ddd_bindings.bind_repo` defers internal bindings today ("needs Brazil+Midway headless auth") | Phase 7 |
| S7 | Size of a real Run record (documents, reflect summary, delivery record), measured over recent SwarmAI Runs, to confirm one upload per Run and set S3 lifecycle | Phase 3 |
| S8 | Confirm the §4.4.4 writer list is complete (a search for every write to a registered destination path) and measure the adapter's added latency on the hook paths | Phase 4 |

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
