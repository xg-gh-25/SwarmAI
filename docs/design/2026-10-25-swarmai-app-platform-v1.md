---
title: SwarmAI App Platform V1 — reliable product boundary and portable host adapters
created: 2026-10-25
updated: 2026-10-25
project: SwarmAI
status: final — product scope fixed for Pollinate, Community, ToDo and Hive
tags: [apps, manifest, ownership, portability, kirocrew, hive, todo, community, ddd, distribution]
---

# SwarmAI App Platform V1

## 0. Decision

SwarmAI will introduce **App** as the installable product boundary above its existing
Agent OS capabilities. The first release follows three rules:

1. **Manifest-first** — an App exists because one validated manifest declares its
   identity and contributions, not because code happens to be registered in several
   backend and frontend tables.
2. **Ownership-first** — every contributed Skill, Agent preset, MCP, Job, Service and
   UI surface has exactly one owning App. Enable, disable, update and uninstall act on
   that ownership graph as one operation.
3. **Built-in-first** — V1 proves the model with code already shipped and trusted in
   SwarmAI. It does not open a third-party Store or add a new execution boundary.

The first built-in pilot is **Pollinate**. The product scope also fixes the later
boundaries for **Community**, **ToDo** and **Hive** so the pilot cannot optimize for a
surface-only App model that fails on Jobs, storage, events or external infrastructure:

- Pollinate is a complete singleton Built-in App and the first ownership pilot.
- Community is a complete Built-in App that owns its signal-domain Jobs and bounded
  provider contributions; Scheduler, Library, Canvas, notification and DDD authority
  remain Core.
- ToDo is a complete Built-in App after its current Chat, Task, lifecycle and SQLite
  couplings are replaced by generic Core storage, relation and session-outcome contracts.
  A locked UI shell over ToDo-specific Core logic is explicitly rejected.
- Hive is split three ways: **Hive Fleet** is a desktop-only Built-in App; the
  `SWARMAI_MODE=hive` runtime is a SwarmAI Host Profile; a future Hive Executor is Core
  distributed-execution infrastructure. They never share one enable/disable lifecycle.

Cross-host work begins only with an adapter contract and portability audit. A KiroCrew
execution canary is blocked until the App explicitly permits KiroCrew and any included
DDD policy can authorize the **final KiroCrew destination**, not merely an intermediate
`open-plugin` rendering. Internal App correctness and cross-host portability are related
goals, but neither is allowed to fake proof for the other.

The target shape is:

```text
Agent OS Core       → supplies chat, sessions, permissions, scheduler, Canvas and storage
DDD Brain           → supplies domain understanding, judgment, gates and learned knowledge
App                 → composes a user-facing product from Brain + capabilities + surfaces
Host Adapter        → renders the portable App package for SwarmAI, KiroCrew or another host
Host Runtime Profile→ states which capabilities one host deployment actually provides
```

**App is not a synonym for Skill, DDD, plugin, Host Profile or executor.** A Skill remains
an atomic ability. A DDD remains a domain brain. A plugin is a legacy package format. A
Host Profile describes the runtime substrate, such as SwarmAI Desktop or SwarmAI Hive. An
executor performs assigned work. An App is the accountable product unit a user installs,
enables, operates and removes.

## 1. Goal and non-goals

### 1.1 Goal

Create a reliable App boundary that:

- makes existing first-party products discoverable and manageable as Apps;
- removes hand-maintained registration drift across UI, backend and capabilities;
- makes resource ownership and true disablement structural;
- adds no filesystem scan, process launch or model call to chat send or streaming paths;
- preserves DDD as the cognitive source rather than copying its knowledge into App state;
- distinguishes portable host adapters from SwarmAI Desktop/Hive runtime profiles;
- fixes the target ownership and migration seams for Pollinate, Community, ToDo and
  Hive Fleet before implementation expands beyond the pilot;
- can later emit a safe, explicit subset for other hosts, beginning with KiroCrew.

### 1.2 Non-goals for V1

V1 does **not**:

- build a public App Store;
- execute third-party backend code, jobs, lifecycle scripts or JavaScript;
- dynamically import App routers into the FastAPI process;
- let an App register or alter PreToolUse, security, permission or pipeline gates;
- make every Skill an App;
- move Pollinate files merely to make the directory tree look modular;
- complete the Community, ToDo or Hive Fleet ownership cutovers in the Pollinate pilot;
- turn Hive into a generic export format or deliver the future Host-to-Hive Run Exchange;
- clean or migrate historical Plugin, ToDo, Community or Hive rows;
- promise feature parity between SwarmAI Desktop, SwarmAI Hive and KiroCrew;
- change the existing DDD distribution policy or publish anything externally.

### 1.3 Product invariants

- Core authority and Host Runtime Profiles cannot be App-owned or App-disabled.
- A managed domain object such as a remote Hive node is not an App Instance.
- Disabling an App blocks new owned operations but never implies deletion, shutdown or
  mutation of external resources; such effects require separate explicit commands.
- A resource has one owner; shared resources are dependencies, not co-owned copies.
- Disable means no new activation through any surface, not a cosmetic status flag.
- Installed package bytes are immutable; user configuration and data live separately.
- Requested permissions never become granted permissions by implication.
- Every contribution is required by default; target-scoped optionality must be explicit.
- A host adapter may omit only an explicitly optional target contribution and must report it.
- Unknown or unmappable required capability makes export fail closed.
- `distribution.targets: []` means deny all export; there is no inheritance or default target.
- Existing sessions may retain descriptive text, but every new App-owned dispatch checks
  current enabled state, grant and activation generation.
- External distribution is opt-in and declaration-bounded: **emit is not publish**.
- A broken App degrades itself; it never blocks backend health or chat readiness.
- The runtime consumes a cached registry. It does not rediscover Apps on hot paths.

## 2. Current state and the missing boundary

SwarmAI already has the component mechanisms:

- Plugin and Marketplace records: `backend/core/plugin_manager.py`,
  `backend/routers/plugins.py` and `backend/schemas/marketplace.py`.
- Skill package descriptors and projection: `backend/core/manifest_loader.py`,
  `backend/core/skill_manager.py` and `backend/core/projection_layer.py`.
- Agent composition: `backend/schemas/agent.py` and `backend/core/agent_defaults.py`.
- MCP catalog/dev layers: `backend/core/mcp_config_loader.py`.
- Scheduled execution: `backend/jobs/`.
- Managed subprocess lifecycle: `backend/core/service_manager.py`.
- DDD identity, capabilities and distribution: `aim.json`, `backend/core/ddd_packager.py`
  and `backend/core/ddd_distribution_policy.py`.
- Product surfaces: `desktop/src/components/layout/overlayIds.ts`, `navTable.ts`,
  `overlayChunks.ts`, `overlaySurfaces.tsx` and `overlayQueries.ts`.

A new first-party surface still requires edits across several closed registries. Plugin
status does not govern filesystem discovery, and an Agent that references one Plugin
can currently expand every plugin-tier Skill. The current mechanisms therefore describe
components, but not a trustworthy aggregate lifecycle.

### 2.1 Assessed built-in product scope

| Product | Current boundary evidence | Final target | Readiness |
|---|---|---|---|
| Pollinate | Dedicated Skill, Overlay, API, templates, brand/channel resources and `Knowledge/Pollinate/**` outputs | Complete singleton Built-in App | First pilot |
| Community | Overlay and Feed API plus Feed types, adapters, signal Jobs, self-tune, digest outputs and Core consumers | Complete singleton Built-in App with owned Jobs and bounded provider contracts | After App-owned Job support |
| ToDo | Entity/API/UI plus attachments, Chat/Task links, session hook, maintenance sweep and direct-DB Skill | Complete Built-in App over generic Core AppStorage, relation and SessionOutcome contracts | After Core seam extraction |
| Hive | Desktop Fleet UI/API/provisioner plus a separate remote SwarmAI runtime and future executor role | Desktop-only Hive Fleet App + SwarmAI Hive Host Profile + Core Hive Executor | Profile now; Fleet cutover last |

The four products are not forced through one migration template. Pollinate validates
surface and Skill ownership. Community validates App-owned automation and data-provider
contributions. ToDo validates App storage and generic host events without product-specific
columns or hooks in Core. Hive Fleet validates high-risk external effects and durable
reconciliation. The sequence deliberately increases authority and failure cost.

The existing Hive implementation also supplies a Host Runtime Profile: `SWARMAI_MODE=hive`
runs the full SwarmAI backend behind systemd, Caddy and application-layer authentication,
with daemon Scheduler, Channels and watchers. That runtime is a deployment of the SwarmAI
host, not the Hive Fleet App and not a separate authored App format.

## 3. Domain model

| Entity | Owns | Must not own |
|---|---|---|
| Agent OS Core | Chat, sessions, recovery, permission authority, security gates, scheduler, Canvas, Library, registry runtime, generic AppStorage, relations and host events | App-specific domain behavior |
| Host Runtime Profile | The concrete capability set and constraints of one host deployment, such as SwarmAI Desktop or SwarmAI Hive | App identity, product lifecycle or user grants |
| Host Capability | A versioned contract an App can require, such as `web-ui`, `session-outcome`, `daemon-scheduler` or `aws-instance-role` | A claim that the capability is available or enforced on every profile |
| DDD Brain | Domain definitions: identity, understanding, gates, authored capabilities, delivery contract, refresher | Host UI/lifecycle state |
| App Release | Immutable package closure, canonical manifest, adapter provenance and package release digest | User data, concrete Brain renderings or granted permissions |
| App Installation | Installed package-release set, verified source/trust provenance, active and staged package-release pointers | Instance activation state |
| App Instance | Concrete Brain rendering, activation digest, data namespace, configuration, grants, desired state and activation generation | Global Core settings or external infrastructure lifecycle by implication |
| Managed Resource | An App-domain object governed by explicit desired/observed state, such as a remote Hive node | App installation identity or authority to mutate itself on disable |
| Reconciliation Residue | Durable record of an owned effect not yet cleaned, including exact generation and retry state | A reason to forget or hide partially cleaned external resources |
| Capability Definition | One versioned Skill, Agent preset, MCP, Job, Service, SOP, knowledge bundle or UI contract | Activation lifecycle |
| Resource Dependency | Reference to another owner's immutable capability release | Co-ownership or copied authority |
| Host Adapter | Mapping from canonical resource graph to one host's exact contract | New domain knowledge or wider permissions |

Definition provenance and lifecycle ownership are distinct. A DDD may author a domain
Skill; an App Instance activates a specific immutable rendering of it. Shared capability
releases remain independently owned and are referenced by digest. Removal is blocked
while enabled dependents exist unless they first switch or disable.

A domain App may bind a Brain interface. The package manifest never fixes a user's live
project name. Each App Instance binds a concrete Project and an immutable Brain rendering.
A pure-knowledge Brain may have no App, and a utility App may omit a Brain. A singleton
built-in App such as Pollinate receives one default Instance; project-scoped Apps may
have several independent Instances.

Activation also resolves one Host Runtime Profile. Every required Host Capability must
be present and enforced at the declared level before the contribution becomes active.
SwarmAI Desktop and SwarmAI Hive are profiles under the same SwarmAI host adapter; KiroCrew
is a separate host adapter because its package, lifecycle and permission contracts differ.

An App Instance is never confused with an object the App manages. The singleton Hive Fleet
App Instance may manage zero or many Hive nodes. Disabling that App blocks new Fleet
operations but does not stop or delete those nodes. Their desired state, observed AWS
resources and cleanup residues remain durable App data.

## 4. Canonical App manifest

The source package has one root `app.json`. It is the portable product declaration,
not a SwarmAI runtime dump and not a KiroCrew manifest copied verbatim. Trust is absent:
verified provenance, not package-authored text, assigns `built-in`, `reviewed`, `local`
or `external` trust.

```json
{
  "schemaVersion": 1,
  "id": "pollinate",
  "version": "1.0.0",
  "displayName": "Pollinate",
  "description": "Turn an idea into publishable media",
  "instanceMode": "singleton",
  "brainInterface": null,
  "resources": [
    {
      "id": "skill:s_pollinate",
      "type": "skill",
      "source": "backend/skills/s_pollinate",
      "required": true,
      "permissions": [],
      "targets": {
        "swarmai": {
          "binding": "builtin:skill:s_pollinate",
          "profiles": {
            "desktop-daemon": {"supported": true},
            "desktop-subprocess": {"supported": true},
            "hive": {"supported": true, "requires": ["linux-arm64"]}
          }
        }
      },
      "fixtures": []
    },
    {
      "id": "surface:gallery",
      "type": "surface",
      "source": "desktop/src/components/layout/PollinateOverlay.tsx",
      "required": true,
      "permissions": [],
      "targets": {
        "swarmai": {
          "binding": "builtin:surface:pollinate",
          "profiles": {
            "desktop-daemon": {"supported": true},
            "desktop-subprocess": {"supported": true},
            "hive": {"supported": true, "requires": ["web-ui", "same-origin-api"]}
          }
        },
        "kirocrew": {"mode": "unsupported"}
      }
    }
  ],
  "dependencies": [
    {"resource": "core:surface:canvas", "version": "1", "required": true}
  ],
  "permissionTemplates": {
    "default": {
      "files": {"read": ["Knowledge/Pollinate/**"], "write": ["Knowledge/Pollinate/**"]},
      "network": [], "secrets": [], "externalActions": []
    }
  },
  "distribution": {"targets": [], "visibility": "internal"}
}
```

Each resource is required unless `required:false` is declared for a specific target.
`unsupported` is not optional: it blocks export while the resource is required. A missing
target entry has the same fail-closed effect as `mode:unsupported` for a required resource;
explicit `mode:unsupported` is preferred because it documents the decision. A host adapter
may not infer optionality from what it happens to implement.

The canonical graph can represent Skills, Agents, SOPs, knowledge bundles, commands,
MCPs, Jobs, Services, surfaces, backend contracts, project templates and acceptance
fixtures. Host-specific files and bindings are resources too. Any file under
`hosts/<target>/` that a build or runtime path references must be declared; an undeclared
reference fails closure validation. Unreferenced undeclared files are excluded and never
enter the release.

Component bodies remain authoritative in their own schemas. `app.json` owns identity,
composition, requiredness, dependencies, target bindings, runtime-profile compatibility
and requested permission templates. It references component definitions; it does not
duplicate their bodies.

A target binding first selects a host adapter. Its `profiles` map then constrains where
that exact generated SwarmAI contribution may activate. Profile requirements are checked
against a versioned host-capability registry; `supported:true` is necessary but never
sufficient when a required capability is absent. Profile-specific files are declared
release resources, not a second App manifest. An App omitted from a profile is unsupported,
not implicitly compatible.

## 5. Release, registry, ownership and state

### 5.1 Sources of truth

| Fact | Authority |
|---|---|
| Product/resource declaration | source `app.json` |
| Immutable package behavior being installed | App Release closure and package release digest |
| Installed, active and staged package releases | installation record pointers |
| Concrete Brain/config/data binding | App Instance activation |
| Effective behavior of one Instance | activation digest over package release + Brain rendering |
| Requested versus approved grants and revocation dominance | per-Instance grants keyed by authority-scope digest and monotonic grant generation |
| Definition provenance | capability release metadata |
| Runtime lifecycle ownership | enabled App Instance ownership index |
| Runtime health/residue | reconciler records |

A package release closure contains normalized `app.json`, every declared resource file,
every declared host binding and dependency lock data. It excludes user-specific Project,
configuration, data and Brain renderings. It rejects undeclared transitive files,
duplicate paths, path traversal and escaping symlinks.

Canonicalization uses RFC 8785 JSON Canonicalization for JSON, UTF-8/NFC paths with `/`,
schema-declared set sorting, preserved order for semantic arrays, and SHA-256 over the
canonical manifest plus sorted `(path, content_digest)` entries. Cross-platform golden
vectors must produce the same **package release digest**.

Each Instance separately computes:

```text
activation_digest
  = SHA-256(package_release_digest + brain_rendering_digest-or-none)

authority_scope_digest
  = SHA-256(activation_digest + canonical-resolved-authority-config)
```

The schema marks every configuration field that can change authority, including file/data
scope, Project binding, network destinations, AWS account/region and secret references.
Unknown configuration fields are authority-affecting by default. Cosmetic preferences may
be explicitly excluded. Resolution occurs before approval, so aliases or indirection cannot
change the concrete scope afterward without changing `authority_scope_digest`.

Approved grants and dispatch authorization bind to `authority_scope_digest`; desired-state
authorization also requires the current activation generation. A change to package bytes or
the concrete Brain rendering creates a new activation. A change to authority-affecting
configuration creates a new authority scope. Neither can silently retain old grants.

Each Instance also has a monotonic `grant_generation`. Grant approval or revocation
advances it, and effective authority requires all of:

```text
enabled
AND request.activation_digest == instance.activation_digest
AND request.generation == instance.generation
AND request.authority_scope_digest == instance.authority_scope_digest
AND grant.grant_generation == instance.grant_generation
AND permission is currently approved and host-enforced
```

Any activation or authority-scope transition, including rollback to a previously used
digest, advances grant generation and starts without effective grants unless the user
explicitly approves them for that transition. A stored grant from an older grant generation
is historical evidence, never authority that can be revived by digest reuse.

### 5.2 Installation and Instance records

Immutable package release records are keyed by package release digest. An installation
stores `{app_id, source, verified_trust, active_package_release_digest,
staged_package_release_digest}` so one package update can wait for validation while the
prior package remains the installation default.

Each Instance stores `{instance_id, package_release_digest, project_id,
brain_rendering_digest, activation_digest, data_namespace, config_ref,
config_generation, authority_scope_digest, desired_state, generation, grant_generation,
grants_ref}`. Instances may intentionally remain on different installed
package releases and Brain renderings. Updating or rolling back the installation default
does not silently move existing Instances; each Instance transition is explicit and
gets a new activation digest/generation.

The package manifest declares a Brain interface, configuration schema and permission
templates. The Instance binds the user's concrete Project, immutable Brain rendering,
paths, configuration and grants. A Brain refresh changes only that Instance's activation
digest. An authority-affecting configuration change advances `config_generation`,
recomputes `authority_scope_digest` and advances `grant_generation`; preference-only
changes explicitly excluded by schema do not. None creates or mutates the shared package
release, and other Instances remain on their own activations and authority scopes.

### 5.3 Resource identity and dependencies

Every definition has a stable handle `(owner, resource_type, resource_id, version)`.
Every activation additionally carries `(instance_id, generation)`. Shared definitions
remain independently versioned dependencies. Uninstall refuses while an enabled
dependent references the release, unless the dependent is disabled or switched first.

### 5.4 Desired-state reconciliation

External effects cannot be physically atomic with an in-memory snapshot. The durable
atomic fact is **desired state plus monotonically increasing generation**:

1. validate and stage an inert release;
2. persist desired state and next generation;
3. atomically publish the ownership snapshot for that generation;
4. reconcile Jobs, Services and projections idempotently;
5. require every Skill dispatch, built-in/backend route entry, proxy request, Job dispatch,
   Service start and external action to revalidate enabled state, activation digest,
   generation, authority-scope digest and effective grant;
6. retain residue records until cleanup succeeds.

A durable reconciler may perform an external create only through this write-ahead protocol:

1. persist an operation intent containing Instance, Managed Resource, desired generation,
   provider/action, deterministic operation ID, idempotency token and discovery tags;
2. commit the intent before calling the provider;
3. use the provider's idempotency token where available and always tag/natural-key the
   resource so recovery can discover it;
4. persist the provider response and observed resource identifiers;
5. after a crash or timeout, query by token, tag or natural key and adopt the result before
   issuing another create;
6. retain ambiguous outcomes as residue requiring reconciliation or human disposition.

Disable blocks new mutations but never erases an operation intent. Observation, response
recording and orphan adoption may continue while disabled because they make effects visible;
they cannot issue a new create/update/delete. An external call already accepted by a provider
follows its declared drain policy and is recorded against the generation that authorized it.

Existing sessions may retain descriptive text, but retained text is not authority. A
new activation from an old session fails immediately after disable or grant revocation.
Already-running work follows the resource's declared drain/cancel policy.

### 5.5 Build-time bindings

Built-in implementation bindings are generated from manifest target bindings. The build
generates typed Python and TypeScript import maps and fails on an unresolved binding,
unowned implementation or duplicate owner. Generated maps are derived artifacts checked
for freshness, never independently authored registries.

## 6. Runtime architecture

### 6.1 Control plane

The in-process control plane contains only trusted platform code:

- manifest validation and normalization;
- installation, grant and ownership records;
- contribution reconciliation;
- App health summaries and audit events;
- routing to Core capabilities or isolated App processes.

Third-party modules are never imported into the FastAPI process and never register
middleware, routers, lifecycle hooks, security hooks or database models.

### 6.2 Data plane

Executable third-party resources are unavailable until a host-specific launcher proves
all minimum controls:

- package and App-data filesystem roots, with Core data and sibling Apps denied;
- default-deny network egress plus explicit host/port grants, including loopback peers;
- clean environment with named, approved secret injection only;
- child-process permission and process-count limit;
- CPU, memory and wall-time ceilings;
- loopback-only authenticated IPC with replay-resistant request signatures;
- termination and quarantine that cannot take down Core.

MCP servers, scheduled executables, long-running Services, dependency preparation and
custom backend handlers all use this launcher. A platform missing any required control
refuses executable third-party Apps; a separate process without these controls is not
called isolated.

Trust does not relax SSRF controls. Every App-owned outbound request derived from user,
configuration or external data, including trusted built-in handlers, uses a host-owned
egress client. It permits declared schemes/hosts, resolves DNS, rejects loopback, private,
link-local and cloud-metadata destinations for IPv4 and IPv6, and revalidates every redirect.
A Host Profile that exposes an instance role, including `hive`, must additionally enforce
a metadata/link-local egress-denial capability for all App execution paths. It does not
advertise App network capability until that control and negative probes pass. Direct raw
network clients in an App-owned handler are an unresolved required capability, not an
audited exception.

### 6.3 Stable App API

App backends are reached through a host-owned route:

```text
/api/apps/{app_id}/proxy/{path}
```

The proxy verifies installation, enabled state, instance identity and the approved API
scope, then signs the forwarded request with a per-instance credential. Direct App
ports are loopback-only and are not exposed to the main UI.

### 6.4 UI boundary

The existing Capabilities surface becomes **Apps & Capabilities**:

```text
Apps | Skills | Experts | Connections
```

V1 built-in surfaces resolve generated `builtin:<handler>` bindings to components
compiled into SwarmAI; a missing or unowned binding fails the build. A later third-party
surface uses host-rendered declarative cards, forms and tables. Arbitrary third-party
JavaScript is not loaded into the privileged main Tauri window. A future custom UI must
use an isolated Window/WebView with a separate least-privilege Tauri capability set and
an App-scoped message bridge.

### 6.5 Hot-path budget

The following paths perform no App filesystem walk, manifest parse, dependency probe or
process launch:

- chat send and admission;
- streamed token handling;
- each tool call;
- each file write;
- Canvas rendering.

They may read the current immutable in-memory registry snapshot. Registry rebuilding
runs on startup or an explicit lifecycle change, off the event loop and outside chat's
worker pools. A broken App never delays backend readiness.

### 6.6 Bounded host contracts for built-in Apps

These are implementation targets for Phases D–E, not existing code. Core will expose
versioned contracts rather than product-specific imports, columns or hooks:

```text
AppStorage             namespaced records/blobs; no direct SQLite access
HostRelation           App resource ↔ Session/Task/Artifact relation
SessionOutcome         immutable, bounded result event after Core persistence
JobContribution        declarative schedule + generated trusted handler binding
BriefingContribution   bounded sanitized facts for Proactive Intelligence
CultivationCandidate   proposal input; Core retains DDD write and policy authority
NotificationRequest    request only; Core transport and approval remain authoritative
```

These stay thin host primitives. AppStorage stores schema-versioned App records and blobs
and exposes compare-and-swap version tokens; lifecycle statuses remain App data, not Core
enums. HostRelation is typed many-to-many, indexed in both directions and conditionally
updated, so one Session may relate to Task, ToDo and other App resources without new host
columns. SessionOutcome is an immutable, deduplicated fact with outcome ID, Session ID,
relation snapshot and bounded persisted evidence references. The App performs any
multi-stage domain transition through AppStorage CAS; SessionOutcome does not encode ToDo
states or provide a second workflow engine.

A built-in App may consume these through a generated trusted binding. It cannot register
an arbitrary lifecycle callback, import Core database internals, add a product-specific
column to Chat or Task, or write a DDD directly. Every delivered event and handler call
carries `instance_id`, activation digest and generation; stale consumers are rejected.

This contract is the migration seam for Community and ToDo. Community emits bounded
briefing and cultivation contributions instead of making Core read
`Services/signals/signal_digest.json`. ToDo consumes generic SessionOutcome events and
HostRelation records instead of requiring `chat_threads.todo_id`,
`tasks.source_todo_id` or a ToDo-specific Core session hook.

## 7. Permission and authority model

A permission template is a request, not protection. A host adapter produces requested
permissions; the Instance stores approved grants against its exact authority-scope digest
and grant generation; the operation boundary records which grants the host demonstrably
enforces. Every operation also matches the current activation digest and activation
generation as specified in §5.1.

Effective access for an external App is always an operation-time intersection:

```text
requested ∩ approved ∩ enforced-by-host ∩ project policy ∩ session/channel policy
```

The Pollinate V1 pilot remains trusted built-in code and does not claim a new sandbox or
new file-scope enforcement. App Center labels its permissions as **requested / governed
by existing Core policy**, never as App-isolated. Phase A exposes no lifecycle controls;
Phase B proves ownership and generation-checked disablement, not least privilege.

Built-in provenance never implies external authority. Hive Fleet must separately request
and receive grants for its AWS account reference, network destinations, secret references
and exact infrastructure action classes. Install, enable, grant and one concrete deploy,
stop, update or delete command are distinct facts. Disabling Hive Fleet performs none of
those effects. Credentials are referenced through Core secret authority rather than copied
into a manifest, activation record, log or target package.

No third-party capability is enabled until every permission it requires is enforced.
This includes Skills and Agent presets: they are active control input that can direct a
model to use tools, so they require a reduced-authority session, explicit tool/file
bounds and operation-time checks. They are not called non-executable.

App Jobs remain unavailable to third parties until file, network, send and tool fields
are enforced by the executor; the current `JobSafety.allow_write`, `allow_send` and
`allow_network` fields are declarations without production readers. An initial disabled
state is not compensation for missing enforcement.

An App can never request these authorities:

- registration or modification of PreToolUse/security/pipeline gates;
- modification of Tool Risk or permission policy;
- direct access to SwarmAI's SQLite database;
- the complete backend environment;
- arbitrary host filesystem roots;
- code execution in the main Tauri window;
- silent external publishing, deployment or infrastructure mutation.

Core security continues to own dangerous-command approval, external-side-effect
approval, AskUserQuestion, session protection and pipeline integrity. App-local events
may trigger owned work, but they cannot redefine who has authority to approve it.

## 8. Cross-host portability

### 8.1 Decision: one source graph, generated release artifacts

SwarmAI will not pretend that one runtime manifest has identical meaning on every host.
UI, scheduler, backend, storage, lifecycle and permission semantics differ. The source
`app.json` declares the complete resource graph; deterministic adapters emit host-exact
release archives:

```text
App source package
├── app.json                         canonical product/resource graph
├── declared portable resources
└── hosts/<target>/                  declared target bindings

CI/release output, never source authority
└── <app>-<version>-<target>.tar.gz
    ├── app.json                     exact target-host contract
    ├── resources
    └── ADAPTER_PROVENANCE.json      source digest, adapter version, host contract
```

Generated `dist/` is not committed and is never catalog authority. CI generates it from
an immutable source commit, verifies reproducibility, records source release digest and
adapter version, and publishes an archive by digest. Local builds use a scratch output.
KiroCrew can install the local archive during development; a future catalog entry should
use its release/archive source form rather than a committed generated subdirectory.

This keeps one authored graph while still producing an exact KiroCrew `app.json`. Every
target-binding file is declared in the source graph and included in the source release
digest, so the adapter has no second hidden descriptor.

### 8.2 Compatibility and portability levels are computed

Authors do not set a self-attested `portable: true` flag. The adapter computes a result
from the resource graph, target bindings and runtime proofs.

| Level | Meaning |
|---|---|
| C0 Package-compatible | Target host validates and installs the package; no useful behavior is claimed |
| P1 Capability-conformant | Declared Agent, Skill, SOP and knowledge behavior passes target fixtures |
| P2 Automation-conformant | Required MCP and scheduled/background behavior pass with enforced grants |
| P3 Product-conformant | Host-native UI, lifecycle, state and behavior meet declared parity tests |

Every source resource appears exactly once in the target report as `mapped`,
`omitted-optional` or `blocked`. Required is the default. An unsupported required Job,
Surface or backend blocks P1 even when its Skill works. Optional omission requires an
explicit target-scoped `required:false` declaration and names the exact resource handle.
P1 proves functional conformance only, not sandboxing, permission enforcement,
automation, UI parity or full App parity.

### 8.3 SwarmAI host profiles

The SwarmAI adapter emits one SwarmAI release form and evaluates it against explicit
runtime profiles:

| Profile | Required substrate | Absent or constrained capability |
|---|---|---|
| `desktop-daemon` | Tauri UI, local API, daemon lifecycle, Scheduler and Channels | none implied beyond declared Tauri capabilities |
| `desktop-subprocess` | Tauri UI and local API | no durable background Jobs or Channels |
| `hive` | browser UI, same-origin API, systemd daemon, Scheduler, Channels, remote filesystem and AWS instance role | no Tauri, Finder/system-open or local-desktop credential management |

Profile compatibility is evaluated per resource. A surface that depends on Tauri is not
Hive-compatible because another surface in the same App works in a browser. A Job that
requires durable scheduling is not subprocess-compatible because the handler imports.
Every activation report names the selected profile and accounts for each required Host
Capability.

Profile exclusion is enforced at the server boundary, not only by omitting UI. On the
`hive` profile, the host must not mount Hive Fleet management routes or must place the
entire route group behind a fail-closed profile middleware that returns 403 before route
logic, including read/detail/credential endpoints. This control is independent of Fleet
App enabled state, browser visibility and authentication success. An authenticated remote
Hive user is still not authorized to access the desktop Fleet control plane.

Hive packaging remains host release engineering. `hive/release.sh`, systemd, Caddy,
`HiveAuthMiddleware`, the daemon startup mode and a future Hive Executor are not App
resources. The package may contain built-in App releases, but each App still requires an
Installation/Instance activation on that Hive and remains independently disableable.

### 8.4 KiroCrew adapter

Current KiroCrew App contracts support Agents, Skills, SOPs, MCP servers, crons,
backend processes, ESM UI, host contributions and App-scoped API/storage. The adapter
maps only semantics it can prove:

| SwarmAI App contribution | KiroCrew output | Rule |
|---|---|---|
| identity/version | required `app.json` fields | direct |
| portable Skill | `skills[]` | include only after isolated-host validation |
| Agent preset | `agents[]` | transform and schema-validate |
| DDD operating rule | `sops[]` or Skill reference | never inject raw knowledge as system authority |
| DDD recall corpus | packaged knowledge consumed by a Skill | target must have an explicit retrieval path |
| MCP | `mcpServers` | only portable command/URL forms with no host path or internal credential |
| simple prompt schedule | `crons[]` | only when timing and ownership semantics match |
| SwarmAI Job handler | none by default | requires a KiroCrew-specific implementation |
| built-in Overlay | none by default | requires a KiroCrew UI adapter |
| standalone backend | `backend` | only if it implements KiroCrew proxy authentication |
| App command | `contributes.commands` | host validates and runs no App code in Command Bar |

The KiroCrew adapter pins a reviewed KiroCrew manifest-schema version and supported
host-version range. It validates generated packages against that schema and an actual
supported host. Unknown versions or changed permission semantics fail closed until the
adapter is reviewed. The target report records adapter version, host contract version,
source release digest and output digest.

The exporter never emits KiroCrew in-gateway Python hooks, arbitrary setup shell
commands, self-managed lifecycle or main-dashboard ESM from SwarmAI implementation
code. Those features exist in KiroCrew, but automatic generation would expand trust
without proving isolation.

KiroCrew currently documents `permissions.api` as enforced while several other fields
remain advisory. The adapter proves allowed and denied API paths at runtime and labels
all unproven permission fields advisory. Every capability the adapter emits, including a
mapped optional capability, must have all of its high-risk permissions enforced; otherwise
that capability is omitted only when target-scoped optionality explicitly permits omission,
or the export fails. A required capability can never be omitted. Initial disabled state is
never a substitute for enforcement.

### 8.5 Brain distribution remains a separate final-destination ceiling

An App that includes DDD content cannot use an intermediate format as destination
authority. `open-plugin` permission alone does not authorize repackaging for KiroCrew.
Effective export requires both:

```text
App policy explicitly permits target=kirocrew and final visibility/audience
AND
included Brain policy explicitly permits destination=kirocrew, rendering and visibility
```

The more restrictive result wins, and the exported provenance records both decisions.
Changing only the App manifest cannot expand Brain reach.

The current DDD vocabulary cannot express the KiroCrew final destination. Therefore any
Brain-bearing KiroCrew export is **blocked** under this design until a separate,
human-approved DDD policy change adds that destination and its tests. V1 does not make
that policy change. An App with no Brain content may still be evaluated under its own
explicit target policy.

### 8.6 Distribution and catalog trust

A future SwarmAI catalog should copy the mechanism, not the implementation details, of
the strongest current KiroCrew catalog properties:

- authored source references are resolved to immutable commits;
- release archives are verified by digest;
- display metadata is baked from the App's manifest rather than re-authored in catalog;
- published catalog documents are schema-versioned and signed separately;
- removal is positive tombstone data, never inferred from omission;
- a remote uninstall recommendation prompts the user and never silently removes software.

SwarmAI V1 has no remote catalog. Local export to KiroCrew is useful before catalog
publication: the generated package can be installed from a local path and tested. The
design does not depend on the current KiroCrew remote catalog being reachable.

### 8.7 Conditional cross-host canary

Pollinate proves the internal App boundary, but it is not automatically the first
portability proof. Its scripts, templates and host integrations require a separate
audit. A DDD-bearing KiroCrew canary cannot run until §8.5's final-destination policy
exists.

After that explicit policy change, the first canary should use an already-isolated-host
capability such as `s_repo-to-ddd` and prove P1 only:

1. generate a local KiroCrew release archive with no backend, cron, MCP, ESM, setup
   script or gateway hook unless separately declared and authorized;
2. install it in a clean supported KiroCrew version with no SwarmAI binary, database,
   environment variables, paths, credentials or network;
3. record that the generated Agent, Skill, SOP and retrieval corpus were loaded;
4. run positive and negative fixtures;
5. ablate each packaged component and require the fixture to detect its absence;
6. record source/output digests, adapter/host contract versions and every omission.

Only after this passes should Pollinate receive a KiroCrew adapter. Pollinate reaches P3
only when its KiroCrew-native UI, outputs and lifecycle are separately tested. A working
Skill alone is P1 and carries no security or parity claim.

## 9. Legacy Plugin compatibility

The old Plugin database and files are not migrated or cleaned. A read-only adapter
projects each installed record as `legacy-skill-app` in App Center.

- Existing installations continue through their current path.
- New App installs never write components into legacy shared directories.
- Legacy disable is not labelled as reliable until runtime projection honors it.
- App ownership cannot be inferred from current plugin-tier directory membership.
- Conversion reads recognized declarations, rejects path escape and symlink escape,
  copies nothing executable into an enabled state, and reports unmapped fields.

Once new App activation is proven, users may explicitly convert a legacy package. There
is no automatic bulk migration and no historical data rewrite.

## 10. Built-in product boundaries

### 10.1 Pollinate — complete App and first pilot

Pollinate is registered without moving its implementation. Its manifest references:

- `backend/skills/s_pollinate/`;
- the existing Pollinate backend handler;
- the existing Pollinate Overlay implementation;
- its templates, brand resources and preference schema;
- its declared `Knowledge/Pollinate/**` output scope.

The pilot changes registration ownership, not behavior. The App surface continues to
launch work through Chat and opens products through Canvas. It does not create a second
agent runtime, file viewer, output registry or scheduler.

### Pilot acceptance

- Phase A labels Pollinate as a non-operational catalog projection and exposes no
  enable/disable control or lifecycle ownership claim.
- After the Phase B cutover, App Center, Work-nav and Skill resolution derive from one
  Pollinate ownership record; superseded hand registration is deleted in that change.
- Disabling increments the Instance generation and blocks the next `s_pollinate`
  activation from both new and already-open sessions.
- Retained session text is allowed, but it cannot authorize dispatch.
- Re-enabling restores the same release and preserves preferences/products without
  restoring a revoked grant implicitly.
- App Center labels built-in Pollinate as governed by existing Core policy, not as
  separately sandboxed or least-privileged.
- Pollinate failure does not change `/health` or block a Chat session.
- The disabled App contributes zero Skill-listing text and starts no App-owned process.
- No App discovery I/O appears on send, streaming, tool-call or file-write paths.
- Existing Pollinate behavior and output tests remain green without relocation shims.

### 10.2 Community — complete App after Job/provider seams

Community owns the signal-intelligence product closure:

```text
Community Overlay + Feed API
FeedType / MEMBER_KEY + fetch adapters
signal-fetch + signal-digest + self-tune + weekly-rollup
optional signal notification declaration
Feed configuration + raw/dedup state
Knowledge/Signals/** and Services/signals materialization
```

Core retains Scheduler/executor substrate, Library, Canvas, Need You, notification
transport, permission authority and DDD mutation authority. `backend/jobs/system_jobs.py`
may not remain the authored source of Community Jobs, and `backend/jobs/executor.py` may
not retain product-specific `if job.type == "signal_fetch"` ownership after cutover.
The manifest generates trusted built-in Job bindings; third-party handlers remain barred.

The current Core consumers of `Services/signals/signal_digest.json` are boundary leaks.
Proactive Intelligence consumes `BriefingContribution`; DDD cultivation consumes
`CultivationCandidate`; notification uses `NotificationRequest`. Community may submit
those bounded records but cannot write Core DDD state, redefine Need You authority or
own a transport.

Community disable increments its generation, unschedules fetch/digest/self-tune/rollup
and notification, removes its UI and provider contributions, and blocks old-generation
handlers. Feed configuration, dedup state and historical Signals/Reports remain. Library
and Canvas may still browse those historical products. Re-enable never retroactively
executes every missed schedule.

Community targets SwarmAI Desktop daemon and Hive profiles after profile fixtures prove
network, Scheduler, storage, instance-role/secret and notification behavior. Hive activation
also requires every Feed adapter to use the host egress client and negative DNS, redirect,
private, loopback, link-local and metadata probes to pass. Subprocess profiles without
durable scheduling are blocked unless automation resources are explicitly optional.
KiroCrew P2/P3 remains blocked while required network, cron and secret authority is advisory
or otherwise unenforced.

### 10.3 ToDo — complete App after Core seam extraction

ToDo owns its entity service, Workbench surface, WorkPacket schema, attachments, history,
review lifecycle, overdue/closure Job, Task-conversion adapter and `s_radar-todo` capability.
It is optional product behavior: Chat, Task, Canvas and Need You must remain usable while
ToDo is disabled.

Core supplies AppStorage, generic HostRelation, SessionOutcome, Chat launcher, Task service
and file-attachment primitives. The cutover removes ToDo knowledge from Core rather than
renaming it:

- `chat_threads.todo_id` becomes a generic App-resource relation projection;
- `tasks.source_todo_id` becomes a generic provenance relation;
- `TodoLifecycleHook` becomes a generation-bound SessionOutcome consumer;
- `LifecycleManager` stops running ToDo-specific sweeps;
- `s_radar-todo/scripts/todo_db.py` stops bypassing activation through direct SQLite access.

Existing `todos` and `todo_attachments` rows remain in place behind the first AppStorage
adapter. No bulk migration or cleanup is required. At the relation cutover, every runtime
reader switches atomically to HostRelation; there is no period where an active reader treats
`chat_threads.todo_id` or `tasks.source_todo_id` as authority while new writes land only in
HostRelation. The compatibility adapter may synthesize historical relation reads from old
columns without modifying them, but no dual-write path is introduced. Physical column
removal requires a separately reviewed data-migration plan.

ToDo disable removes its UI and Skill, blocks mutation/binding/conversion, unschedules its
maintenance consumer and rejects old-generation SessionOutcome delivery. Existing records,
attachments and historical Chat/Task references remain readable as inert history. Re-enable
does not replay disabled-period events unless an explicit bounded replay policy is later
approved.

A locked `todo-workbench` surface over a Core-owned ToDo manager, tables, hook and sweep is
not accepted as App migration. It would prove registration only while preserving every
lifecycle bypass. KiroCrew remains C0 until target-native storage, relation, outcome, review
and Task adapters preserve the declared semantics; mapping only to a host task list is not
P3 parity.

### 10.4 Hive — Fleet App, Host Profile and future Executor

The desktop-only Hive Fleet App owns its Overlay and Account settings surface, Hive
management API, AWS account references, managed-node inventory, account verification,
provision/start/stop/update/retry/password-rotation commands, health projection and durable
AWS reconciliation. One singleton Fleet App Instance manages `0..N` Hive node records;
those nodes are Managed Resources, not App Instances.

Hive Fleet requests explicit grants for AWS account/region scope, network destinations,
secret references and each external action class across STS, EC2, IAM, S3, CloudFront and
SSM. Verified built-in provenance does not grant them. Existing `hive_accounts.auth_config`
and `hive_instances.auth_password` values are a documented legacy plaintext exposure in
local SQLite; this design does not claim that V1 repairs them. They are never copied into
a manifest, activation, Managed Resource state, residue, audit event, log or target package.
Phase F cannot activate its reconciler for an account until a separate, explicit
user-approved import has stored the credential in Core secret authority and replaced the
active account/Instance reference with a secret reference. The reconciler cannot read the
legacy plaintext columns. Rows that have not been imported remain visible but unmanaged.
Physical secure erasure and column removal are deferred because they mutate historical
data; logical migration away from plaintext as active authority is not deferred.

Provision/update/cleanup moves from partial-progress status persistence plus transient
`asyncio.create_task` execution and manual retry to durable Managed Resource desired state
with generation, observed resource identifiers and an idempotent reconciler. Before every
AWS create, it persists the deterministic operation ID, provider idempotency token where
supported, and unique ownership tags. Recovery lists/describes by token, tag or natural key
and adopts a resource created before its identifier was recorded; it never issues a second
create merely because the response was lost. A partial or ambiguous cleanup retains the
node, operation intent and exact residue until every resource is accounted for. It is
forbidden to delete the local record after AWS cleanup errors, because doing so makes
billable or reachable infrastructure invisible.

Disabling Hive Fleet blocks every new management command and pauses mutating
reconciliation, but does not stop, update or delete existing remote Hives. Non-mutating
observation, response recording and orphan adoption continue so already-started effects
cannot become invisible. Uninstall is refused while managed nodes, in-flight operation
intents or unresolved residues exist unless a separate explicit transfer/forget workflow
is later designed. Re-enable resumes from the same records and grants; revoked grants stay
revoked.

The SwarmAI Hive Host Profile owns `SWARMAI_MODE=hive`, systemd, Caddy, Hive auth middleware,
host packaging, daemon Scheduler/Channels/watchers and Hive workspace behavior. A future
Hive Executor belongs to Core distributed execution and its Host-to-Hive Run Exchange.
Neither is enabled, disabled, installed or removed with Hive Fleet. Hive Fleet is
`unsupported` on the Hive profile itself and on KiroCrew.

### 10.5 Product interaction and migration order

Apps depend on contracts, not one another's tables. Community and Pollinate may declare an
optional dependency on ToDo's `create-work-item` capability; disabling ToDo removes only
that follow-up path. Hive is a runtime placement for compatible Apps, not their owner.

The implementation order is Pollinate, Community, ToDo, then Hive Fleet. SwarmAI Hive
profile accounting starts in Phase A, however, so Pollinate and later Apps cannot assume
Tauri/Desktop semantics and retrofit Hive after their manifests ship.

## 11. Delivery phases

Phases A–C are the committed App Platform V1. Phases D–H are the fixed migration order
and entry criteria for later delivery; they do not expand the V1 definition of done.

### Phase A — contract, profiles and non-operational registry

- Add canonical resource-graph schema, closure/digest algorithm and invariant tests.
- Define versioned SwarmAI `desktop-daemon`, `desktop-subprocess` and `hive` capability
  profiles; every built-in resource receives explicit profile accounting.
- Generate typed built-in binding maps and deterministic built-in index at build time.
- Add read-only App Registry and `/api/apps` projection.
- Add Apps as the default view inside Apps & Capabilities.
- Show Pollinate as `catalog-only`; expose no lifecycle control or ownership claim.

### Phase B — Pollinate ownership and activation pilot

- Create the singleton Pollinate Instance and desired-state generation record.
- Route Pollinate UI and Skill activation through current-generation ownership checks.
- Add resource-origin, selected-profile and honest enforcement status.
- Add the Legacy Plugin read-only adapter.
- Delete superseded Pollinate hand registration in the same cutover.
- Run Desktop and Hive profile fixtures; a missing required Hive capability blocks Hive
  activation without weakening Desktop behavior.

### Phase C — host-adapter contract

- Add target accounting, adapter provenance and reproducible release-archive tests.
- Implement KiroCrew manifest generation against a pinned schema using synthetic,
  Brain-free fixtures only.
- Preserve the existing DDD packager as the Brain export authority.
- Do not run a Brain-bearing canary or publish to a catalog.

### Phase D — Community automation boundary

- Add JobContribution, BriefingContribution, CultivationCandidate and
  NotificationRequest contracts with activation-generation checks.
- Move Community Job declarations, handler ownership, Feed configuration and provider
  outputs under one App graph without rewriting historical data.
- Community handlers remain generated trusted built-in bindings and inherit the current
  Core executor authority; Phase D makes no third-party Job isolation claim. Third-party
  Jobs remain blocked until Phase G/H enforcement.
- Require all Feed HTTP paths to use the host egress client; Hive activation fails until
  private/loopback/link-local/metadata and redirect probes pass.
- Remove fixed Community file reads and Job dispatch branches from Core in the cutover.
- Prove Desktop daemon and Hive behavior; fail closed on subprocess automation gaps.

### Phase E — ToDo storage and event boundary

- Add AppStorage, HostRelation and SessionOutcome contracts.
- Put ToDo entity/API/UI/attachments/Skill/Jobs under one App owner while preserving
  existing rows through a compatibility adapter.
- Remove ToDo-specific Chat/Task write authority, lifecycle hook and Core sweep in the
  same cutover; do not ship a locked UI-only shell.
- Route every `s_radar-todo` operation through generation-checked AppStorage/API contracts
  or remove `todo_db.py`; no direct SQLite mutation path survives the cutover.

### Phase F — Hive Fleet external-resource boundary

- Model the desktop-only Fleet App and its AWS/secret/external-action grants.
- Import each actively managed legacy credential into Core secret authority through an
  explicit user-approved flow and replace its active reference before reconciliation;
  unmanaged rows remain read-only, and the reconciler cannot read plaintext columns.
- Replace transient deployment tasks with durable managed-resource reconciliation.
- Persist deterministic operation IDs, provider idempotency tokens and ownership tags
  before AWS creates; recover and adopt by token/tag/natural key after ambiguous outcomes.
- Retain partial-cleanup residue and refuse uninstall while nodes, operation intents or
  residue remain.
- Enforce the complete Fleet route group as unavailable at the HTTP server boundary on
  the Hive profile, independent of UI omission, App state or successful Hive login.
- Verify that Fleet disable never changes running remote Hives and that Hive profile
  activation cannot expose Fleet management.

### Phase G — reduced-authority third-party inputs

- First build App-scoped sessions with enforced tool/file/data boundaries.
- Then allow staged external Skills, Agent presets and templates as untrusted active
  control input; never call them non-executable.
- MCP declarations remain inactive until their required permissions are enforced.
- No backend, Job, lifecycle script or custom JavaScript.

### Phase H — isolated execution and real portability

- Land the separately approved DDD final-destination policy before a Brain export.
- Run the KiroCrew P1 ablation canary.
- Add the hardened App launcher and proxy authentication.
- Enforce file/network/secret/tool/send grants and generation checks.
- Use Slack and then executable external Apps as progressively higher-risk isolation
  proofs. Community and Hive Fleet are built-in boundary proofs, not substitutes for
  third-party sandbox evidence.

## 12. Failure modes and required behavior

| Failure | Required behavior |
|---|---|
| malformed built-in manifest or unresolved binding | build fails; broken product is not shipped |
| malformed installed manifest | App disabled and surfaced as invalid; Core continues |
| duplicate lifecycle owner | activation fails before desired-state commit |
| selected runtime profile lacks a required capability | that profile activation fails with the exact missing capability; other profiles are unchanged |
| authority-affecting config changes under an existing activation | authority-scope and grant generations advance; prior grants become ineffective |
| an App request on Hive resolves or redirects to private/link-local/metadata space | host egress client rejects it before connection; direct App network path blocks activation |
| stale SessionOutcome, provider or Job generation arrives | contribution is rejected and recorded; it is never replayed into the current activation |
| rollback selects a digest that had historical grants | grant generation advances; historical grants remain ineffective until explicitly re-approved |
| reconciliation partly fails | generation remains authoritative; residue is visible and retryable |
| provider create returns ambiguously or process crashes before response persistence | write-ahead operation remains; recovery discovers/adopts by token/tag/natural key before any retry |
| disable cleanup fails | every new operation remains blocked by generation; residue is retained |
| Hive Fleet is disabled with running nodes | management operations stop; no node is stopped, updated or deleted |
| Hive-profile request reaches a Fleet route | server rejects before route logic even for an authenticated user; UI omission is not the control |
| Hive AWS cleanup partly fails | managed-node record and exact resource residue remain until reconciliation or explicit operator disposition |
| Hive Fleet uninstall sees nodes or residue | uninstall is refused; no implicit infrastructure cleanup or forgetting occurs |
| update requests more permission | staged package remains inactive as default; existing Instance activations are unchanged |
| Instance package/Brain update changes activation digest | old grants do not apply; Instance waits for grants while prior activation remains explicit rollback target |
| target adapter cannot map required resource | export fails with exact resource handle |
| optional target resource cannot map | export succeeds only with explicit omission accounting |
| included Brain lacks final-destination permission | whole export fails; intermediate format cannot substitute |
| host lacks one mandatory isolation control | executable third-party App is refused |
| App process crashes repeatedly | circuit opens for that App; backend health stays independent |

## 13. Proof strategy

Tests are written before each implementation phase.

### Structural properties

- Every active definition has one provenance owner; every activation has one enabled
  App Instance lifecycle owner.
- Every source resource is target-accounted exactly once; required is the default.
- No disabled or old-generation handle can start new work, including from an old session.
- Changing any resolved authority-affecting configuration changes authority-scope digest,
  advances grant generation and invalidates prior grants; cosmetic-only exclusions are
  schema-declared and tested.
- AppStorage CAS detects concurrent writes, HostRelation is many-to-many and bidirectional,
  and duplicate SessionOutcome delivery produces one App transition.
- Reordering set-like manifest fields does not change the normalized registry or digest.
- Author-controlled bytes cannot claim built-in/locked trust.
- Package closure includes all declared/transitively loaded package bytes and rejects
  undeclared, duplicate, traversal and escaping-symlink paths; it excludes Instance data
  and Brain renderings.
- Installation package defaults and each Instance's package-release selection can coexist
  without silently moving another Instance.
- Two Instances using one package release and different Brain renderings have different
  activation digests, independent grants and independent rollback/generation state.
- Rolling back to an activation digest with historical grants advances grant generation
  and does not restore any grant without an explicit new approval.
- Legacy Hive credential values cannot appear in activation, Managed Resource, residue,
  audit, log or generated-package schemas and have no new direct readers.
- Active and staged package releases coexist under separate immutable digest pointers.
- Generated built-in binding maps have no missing or unowned implementation.
- SwarmAI Desktop and Hive profile reports account for every required Host Capability;
  one profile's support never implies another's.
- AppStorage, HostRelation and SessionOutcome carry App identity and activation generation;
  Core schemas and lifecycle code contain no new product-specific App columns or hooks.
- A Managed Resource is never counted as an App Instance, and App disable never creates
  an implicit external-resource desired-state transition.
- A target adapter never outputs a permission, capability or final destination absent
  from source intent.
- Every emitted KiroCrew capability, required or optional, has enforced high-risk
  permissions; mutation to emit an optional capability with advisory network/secret
  authority fails or converts it to an explicitly permitted omission.
- Cross-platform canonicalization golden vectors produce the same SHA-256 release digest.

### Integration proofs

- Pollinate works before and after the ownership cutover with the same public behavior.
- Killing or corrupting Pollinate reconciliation leaves Chat usable.
- Disable during an old open session blocks its next App-owned dispatch; grant revocation
  and policy tightening also take effect at the next operation.
- Re-enable does not restore revoked grants implicitly.
- Pollinate's Hive activation passes browser/same-origin/file fixtures and blocks every
  required Tauri-only or missing media capability rather than silently omitting it.
- Community disable leaves historical Signals browsable but causes zero new fetch,
  digest, self-tune, rollup, briefing, cultivation or notification contribution.
- On Hive, Community URL probes covering DNS rebinding-safe resolution, redirects,
  IPv4/IPv6 private, loopback, link-local and metadata destinations are rejected before
  connection; a raw-client mutation makes the capability test fail.
- ToDo disable leaves Chat, Task, Canvas and Need You operational; old SessionOutcome
  events and direct-DB Skill paths cannot mutate ToDo state.
- A cutover fixture exercises every former ToDo relation reader and proves no runtime
  reader can observe a link that differs from the authoritative HostRelation record.
- Hive Fleet disable with running nodes produces zero AWS mutation; re-enable recovers
  the same inventory, and revoked grants remain ineffective.
- A killed Hive deployment/reconciliation process resumes idempotently from durable
  desired state; fault injection before provider call, after provider acceptance and before
  response persistence proves the same operation is adopted once with no invisible or
  duplicate resource. Partial cleanup keeps every unresolved AWS identifier visible.
- Hive mode cannot activate the desktop-only Hive Fleet management surface, and
  authenticated HTTP probes against every Fleet read/write/detail/credential route fail
  before route logic or AWS/client initialization.
- Phase C validates a generated Brain-free KiroCrew package against pinned schema and
  supported host versions, including an allowed and denied API-path probe.
- A Brain permitting only `open-plugin` fails a KiroCrew target test.
- After the separate DDD destination change, the P1 canary records component loading,
  passes positive/negative fixtures and fails the required ablations.
- Hostile-process tests prove filesystem, network, loopback, environment, child-process
  and resource isolation before executable external Apps are enabled.
- Mutation checks remove ownership filtering, generation checks, requiredness accounting,
  package closure and final-destination checks; their corresponding tests turn red.

### Measured hot-path proof

Instrument startup/reconciliation separately from send and stream. Compare before and
after with Apps enabled and disabled. Acceptance is no new App filesystem operation,
manifest parse, process launch or network request on send, token, tool-call or file-write
paths. Report measured timings, but do not freeze machine-specific numbers into policy.

## 14. Decisions fixed by this design

1. App Center extends Capabilities; no new Nav card or competing full-screen host.
2. Pollinate is a complete singleton Built-in App and the first ownership pilot.
3. Community is a complete Built-in App that owns signal-domain Jobs and data; its
   Scheduler, briefing, cultivation, Library, Canvas and notification authorities are
   bounded Core dependencies.
4. ToDo is a complete Built-in App after generic AppStorage, HostRelation and
   SessionOutcome seams exist; a locked UI shell over ToDo-specific Core logic is rejected.
5. Hive Fleet is a desktop-only Built-in App; SwarmAI Hive is a Host Runtime Profile;
   a future Hive Executor is Core infrastructure. They have independent lifecycles.
6. SwarmAI Desktop and Hive are profiles of one SwarmAI host adapter, not separately
   authored App manifests. KiroCrew remains a separate host adapter.
7. App disable never implies external infrastructure mutation. Hive nodes continue running
   until an explicit authorized command changes their desired state.
8. Built-in trust never implies AWS, network, secret or external-action grants.
9. Cross-host portability uses one canonical resource graph and reproducible release
   adapters, never two authored manifests or committed generated `dist/` state.
10. Phase C proves KiroCrew package compatibility with Brain-free fixtures against a
    pinned contract; P1 execution waits for explicit DDD final-destination authority.
11. Full Pollinate parity on KiroCrew is a later, separately proven P3 target.
12. Existing Plugin and candidate-product data remain in place behind compatibility
    adapters; no automatic migration or cleanup occurs.
13. Third-party execution and Store distribution are outside V1.
14. App code cannot modify Agent OS authority or run inside the privileged main window.
15. DDD distribution remains an independent, more-restrictive final-destination ceiling;
    a separately approved policy extension is required before Brain-bearing KiroCrew export.
16. Catalog publication always remains a human-approved external action.

## 15. Deferred decisions

These are deliberately deferred until the earlier proof exists:

- the public SwarmAI catalog protocol and signing infrastructure;
- the exact isolated UI technology for third-party custom pages;
- portable scheduler semantics beyond simple prompt crons;
- App dependency installation and shared dependency garbage collection;
- whether Pollinate warrants a KiroCrew P2/P3 adapter after the portability audit;
- physical removal of legacy ToDo columns after generic relation projections are proven;
- physical secure erasure of user-migrated legacy Hive credential values and removal of
  their obsolete SQLite columns;
- a Hive Fleet transfer/forget workflow for externally retained nodes;
- the separate Host-to-Hive Run Exchange and remote Executor protocol;
- self-managed native Apps;
- cross-host state migration.

These details do not reopen the four product-boundary decisions and are not needed to
prove Manifest-first, Ownership-first and Built-in-first.

## 16. V1 definition of done

V1 is complete only when:

- Pollinate has one manifest and one App ownership record;
- every Pollinate resource is explicitly accounted for on SwarmAI Desktop and Hive
  profiles, with missing required Hive capabilities blocking only Hive activation;
- App Center, Work-nav visibility and Skill resolution consume that record;
- enable/disable changes real resolution rather than metadata alone;
- Core remains usable when the App is invalid or disabled;
- no App work is added to chat hot paths;
- the old Pollinate registration paths made redundant by ownership are deleted;
- Legacy Plugins are visible without rewriting their historical data;
- the KiroCrew adapter reproducibly emits a Brain-free package-compatible archive against
  a pinned schema and supported host version;
- a test proves `open-plugin` authority alone cannot authorize KiroCrew;
- the target report accounts for every capability as mapped, explicitly optional or blocked;
- Community, ToDo and Hive Fleet are not shown as operational Apps before their own
  ownership cutovers, while their finalized boundaries and phase entry criteria remain
  part of this design;
- no Store, external publish or third-party execution is implied by the V1 UI.

## 17. Sources

SwarmAI:

- `docs/2026-07-20-ddd-dual-target-distribution-design.md`
- `docs/design/2026-10-24-swarmai-usable-by-anyone.md`
- `backend/core/ddd_distribution_policy.py`
- `backend/core/ddd_packager.py`
- `backend/core/plugin_manager.py`
- `backend/core/skill_manager.py`
- `backend/core/mcp_config_loader.py`
- `backend/core/service_manager.py`
- `backend/jobs/models.py`
- `backend/jobs/system_jobs.py`
- `backend/jobs/handlers/signal_fetch.py`
- `backend/jobs/handlers/signal_digest.py`
- `backend/core/community_data.py`
- `backend/core/proactive_intelligence.py`
- `backend/hooks/signal_ddd_bridge.py`
- `backend/routers/community_api.py`
- `backend/core/todo_manager.py`
- `backend/hooks/todo_lifecycle_hook.py`
- `backend/routers/todos.py`
- `backend/core/chat_thread_manager.py`
- `backend/core/lifecycle_manager.py`
- `backend/hive/provisioner.py`
- `backend/hive/user_data.py`
- `backend/routers/hive.py`
- `backend/middleware/hive_auth.py`
- `hive/release.sh`
- `hive/swarmai-hive.sh`
- `hive/Caddyfile`
- `docs/design/project-agent-delivery-os-hld.md`
- `desktop/src/components/layout/CapabilitiesOverlay.tsx`
- `desktop/src/components/layout/overlayRegistry.tsx`
- `desktop/src-tauri/capabilities/default.json`

KiroCrew, read 2026-10-25:

- [Apps overview](https://kiro.dev/docs/crew/apps/)
- [Manifest reference](https://kiro.dev/docs/crew/apps/manifest/)
- [SDK/API reference](https://kiro.dev/docs/crew/apps/sdk/)
- [Publishing guidelines](https://kiro.dev/docs/crew/apps/publishing/)
- [KiroCrewApps catalog](https://github.com/kirodotdev/KiroCrewApps)

The KiroCrew references are interoperability inputs, not authority over SwarmAI's
security model. Notably, KiroCrew documents that only App API path scope is currently
enforced while several other permission fields remain advisory, and its public catalog
endpoint is not yet live. This design therefore targets local generated-package proof
first and does not treat successful installation as proof of sandboxing or parity.

## 18. Independent review disposition

Fresh-context architecture and security reviews rejected the first draft's central
claims until these structural changes were made:

- package grants now bind an Instance activation digest over the immutable package
  release closure plus that Instance's Brain rendering;
- final KiroCrew destination authority is required from the DDD policy;
- the manifest is a complete resource graph with required-by-default target accounting;
- desired-state generation replaces claims of physical lifecycle atomicity;
- old sessions retain text but lose executable authority immediately on disable;
- definition provenance is separated from App Instance lifecycle ownership;
- built-in handler maps are generated and completeness-gated;
- active and staged releases have separate immutable pointers;
- generated target packages are release artifacts, never committed source manifests;
- concrete Project/Brain/data/grant bindings live on Instances, not package metadata;
- third-party Skills and Agent presets are treated as active control input;
- missing platform isolation or advisory high-risk permissions fail closed;
- P1 uses component-load evidence, negative fixtures and ablation, and remains a
  functional conformance claim only;
- a final review separated reusable package releases from per-Instance Brain state:
  Installations hold package-release defaults, while each Instance owns an
  `activation_digest(package_release, brain_rendering)` with independent grants,
  generation and rollback.

The finalized Pollinate, Community, ToDo and Hive scope then received two additional
fresh-context reviews. The architecture review reported no BLOCKER/HIGH findings and
its documentation clarifications were applied: future host contracts are labelled as
Phase D–E targets, missing required target entries fail closed like explicit
`unsupported`, and Hive lifecycle wording distinguishes persisted partial progress from
durable desired state. The security review's HIGH and MEDIUM findings were resolved:

- the Hive profile now requires server-side denial of the complete Hive Fleet route
  group, independent of UI omission, App state and successful Hive authentication;
- monotonic grant generation prevents rollback to an old activation digest from reviving
  previously granted or revoked authority;
- legacy plaintext Hive credential exposure is stated explicitly, prohibited from every
  new App/reconciliation/audit schema, and placed behind Core secret authority before
  Phase F reconciliation becomes authoritative;
- ToDo relation readers cut over atomically to HostRelation with no stale dual-read or
  dual-write window;
- referenced undeclared host files fail closure validation while unrelated files remain
  excluded.

An additional independently delivered review batch found further authority and crash
consistency gaps. They were resolved as follows:

- grants bind a separately canonicalized `authority_scope_digest`, so changing AWS
  account/region, network, secret, Project or other authority-bearing configuration
  invalidates approval without corrupting package/Brain activation identity;
- the operation-time predicate now requires enabled state, activation digest, activation
  generation, authority scope, grant generation and current host enforcement together;
- all App-owned network paths, including trusted built-ins on an instance-role Hive, use
  host egress enforcement that rejects private, loopback, link-local and metadata targets
  after DNS resolution and on every redirect;
- external creates use a write-ahead deterministic operation ID, provider idempotency token
  and ownership tags, allowing ambiguous/crashed operations to be discovered and adopted;
- Phase F is blocked until actively managed legacy Hive credentials are explicitly imported
  into Core secret authority; only later physical plaintext erasure remains deferred;
- optional KiroCrew resources receive the same high-risk permission enforcement as required
  resources whenever emitted;
- the Phase D Community Jobs remain trusted built-in bindings, and the thin AppStorage,
  HostRelation and SessionOutcome contracts now state CAS, many-to-many/bidirectional and
  deduplicated-event semantics without absorbing ToDo lifecycle states into Core.

Final fresh-context architecture and security delta reviews returned CLEAN with no
BLOCKER, HIGH or MEDIUM findings.

No reviewer finding was dismissed as an accepted deviation. The remaining deliberate
boundary is that V1 proves a trusted built-in product boundary, not third-party
least-privilege execution or Brain-bearing KiroCrew portability.
