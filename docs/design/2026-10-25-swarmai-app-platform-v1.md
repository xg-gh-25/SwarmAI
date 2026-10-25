---
title: SwarmAI App Platform V1 — reliable product boundary and portable host adapters
created: 2026-10-25
updated: 2026-10-25
project: SwarmAI
status: draft r2 — independent architecture/security review integrated; awaiting product review
tags: [apps, manifest, ownership, portability, kirocrew, ddd, distribution]
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

The first built-in pilot is **Pollinate**. Cross-host work begins only with an
adapter contract and portability audit. A KiroCrew execution canary is blocked until
the App explicitly permits KiroCrew and any included DDD policy can authorize the
**final KiroCrew destination**, not merely an intermediate `open-plugin` rendering.
Internal App correctness and cross-host portability are related goals, but neither is
allowed to fake proof for the other.

The target shape is:

```text
Agent OS Core  → supplies chat, sessions, permissions, scheduler, Canvas and storage
DDD Brain      → supplies domain understanding, judgment, gates and learned knowledge
App            → composes a user-facing product from Brain + capabilities + surfaces
Host Adapter   → renders the portable App package for SwarmAI, KiroCrew or another host
```

**App is not a synonym for Skill, DDD or plugin.** A Skill remains an atomic ability.
A DDD remains a domain brain. A plugin is a legacy package format. An App is the
accountable product unit a user installs, enables, operates and removes.

## 1. Goal and non-goals

### 1.1 Goal

Create a reliable App boundary that:

- makes existing first-party products discoverable and manageable as Apps;
- removes hand-maintained registration drift across UI, backend and capabilities;
- makes resource ownership and true disablement structural;
- adds no filesystem scan, process launch or model call to chat send or streaming paths;
- preserves DDD as the cognitive source rather than copying its knowledge into App state;
- can later emit a safe, explicit subset for other hosts, beginning with KiroCrew.

### 1.2 Non-goals for V1

V1 does **not**:

- build a public App Store;
- execute third-party backend code, jobs, lifecycle scripts or JavaScript;
- dynamically import App routers into the FastAPI process;
- let an App register or alter PreToolUse, security, permission or pipeline gates;
- make every Skill an App;
- move Pollinate files merely to make the directory tree look modular;
- clean or migrate historical Plugin rows;
- promise feature parity between SwarmAI and KiroCrew;
- change the existing DDD distribution policy or publish anything externally.

### 1.3 Product invariants

- Core authority cannot be App-owned or App-disabled.
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

## 3. Domain model

| Entity | Owns | Must not own |
|---|---|---|
| Agent OS Core | Chat, sessions, recovery, permission authority, security gates, scheduler, Canvas, Library, registry runtime | App-specific domain behavior |
| DDD Brain | Domain definitions: identity, understanding, gates, authored capabilities, delivery contract, refresher | Host UI/lifecycle state |
| App Release | Immutable package closure, canonical manifest, adapter provenance and package release digest | User data, concrete Brain renderings or granted permissions |
| App Installation | Installed package-release set, verified source/trust provenance, active and staged package-release pointers | Instance activation state |
| App Instance | Concrete Brain rendering, activation digest, data namespace, configuration, grants, desired state and activation generation | Global Core settings |
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
        "swarmai": {"binding": "builtin:skill:s_pollinate"}
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
        "swarmai": {"binding": "builtin:surface:pollinate"},
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
`unsupported` is not optional: it blocks export while the resource is required. A host
adapter may not infer optionality from what it happens to implement.

The canonical graph can represent Skills, Agents, SOPs, knowledge bundles, commands,
MCPs, Jobs, Services, surfaces, backend contracts, project templates and acceptance
fixtures. Host-specific files and bindings are resources too; everything under
`hosts/<target>/` must be declared or is excluded from the release closure.

Component bodies remain authoritative in their own schemas. `app.json` owns identity,
composition, requiredness, dependencies, target bindings and requested permission
templates. It references component definitions; it does not duplicate their bodies.

## 5. Release, registry, ownership and state

### 5.1 Sources of truth

| Fact | Authority |
|---|---|
| Product/resource declaration | source `app.json` |
| Immutable package behavior being installed | App Release closure and package release digest |
| Installed, active and staged package releases | installation record pointers |
| Concrete Brain/config/data binding | App Instance activation |
| Effective behavior of one Instance | activation digest over package release + Brain rendering |
| Requested versus approved grants | per-Instance grants keyed by activation digest |
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
activation_digest = SHA-256(package_release_digest + brain_rendering_digest-or-none)
```

Approved grants, desired-state generation and dispatch authorization bind to the
activation digest. A change to either package bytes or the concrete Brain rendering
creates a new activation and cannot silently retain old grants.

### 5.2 Installation and Instance records

Immutable package release records are keyed by package release digest. An installation
stores `{app_id, source, verified_trust, active_package_release_digest,
staged_package_release_digest}` so one package update can wait for validation while the
prior package remains the installation default.

Each Instance stores `{instance_id, package_release_digest, project_id,
brain_rendering_digest, activation_digest, data_namespace, config_ref, desired_state,
generation, grants_ref}`. Instances may intentionally remain on different installed
package releases and Brain renderings. Updating or rolling back the installation default
does not silently move existing Instances; each Instance transition is explicit and
gets a new activation digest/generation.

The package manifest declares a Brain interface and permission templates. The Instance
binds the user's concrete Project, immutable Brain rendering, paths and grants. A Brain
refresh changes only that Instance's activation digest. It does not create or mutate the
shared package release, and other Instances remain on their own activations.

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
5. require every Skill dispatch, proxy request, Job dispatch, Service start and external
   action to revalidate current enabled state, grant and generation;
6. retain residue records until cleanup succeeds.

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

## 7. Permission and authority model

A permission template is a request, not protection. A host adapter produces requested
permissions; the Instance stores approved grants against its exact activation digest;
the operation boundary records which grants the host demonstrably enforces.

Effective access for an external App is always an operation-time intersection:

```text
requested ∩ approved ∩ enforced-by-host ∩ project policy ∩ session/channel policy
```

The Pollinate V1 pilot remains trusted built-in code and does not claim a new sandbox or
new file-scope enforcement. App Center labels its permissions as **requested / governed
by existing Core policy**, never as App-isolated. Phase A exposes no lifecycle controls;
Phase B proves ownership and generation-checked disablement, not least privilege.

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

### 8.3 KiroCrew adapter

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
all unproven permission fields advisory. If a required capability depends on an
unenforced high-risk permission, export fails. It is never emitted merely disabled as
a substitute for enforcement.

### 8.4 Brain distribution remains a separate final-destination ceiling

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

### 8.5 Distribution and catalog trust

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

### 8.6 Conditional cross-host canary

Pollinate proves the internal App boundary, but it is not automatically the first
portability proof. Its scripts, templates and host integrations require a separate
audit. A DDD-bearing KiroCrew canary cannot run until §8.4's final-destination policy
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

## 10. Pollinate built-in pilot

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

## 11. Delivery phases

### Phase A — contract and non-operational registry

- Add canonical resource-graph schema, closure/digest algorithm and invariant tests.
- Generate typed built-in binding maps and deterministic built-in index at build time.
- Add read-only App Registry and `/api/apps` projection.
- Add Apps as the default view inside Apps & Capabilities.
- Show Pollinate as `catalog-only`; expose no lifecycle control or ownership claim.

### Phase B — ownership and activation pilot

- Create the singleton Pollinate Instance and desired-state generation record.
- Route Pollinate UI and Skill activation through current-generation ownership checks.
- Add resource-origin display and honest enforcement status.
- Add the Legacy Plugin read-only adapter.
- Delete superseded Pollinate hand registration in the same cutover.

### Phase C — host-adapter contract

- Add target accounting, adapter provenance and reproducible release-archive tests.
- Implement KiroCrew manifest generation against a pinned schema using synthetic,
  Brain-free fixtures only.
- Preserve the existing DDD packager as the Brain export authority.
- Do not run a Brain-bearing canary or publish to a catalog.

### Phase D — reduced-authority third-party inputs

- First build App-scoped sessions with enforced tool/file/data boundaries.
- Then allow staged external Skills, Agent presets and templates as untrusted active
  control input; never call them non-executable.
- MCP declarations remain inactive until their required permissions are enforced.
- No backend, Job, lifecycle script or custom JavaScript.

### Phase E — isolated execution and real portability

- Land the separately approved DDD final-destination policy before a Brain export.
- Run the KiroCrew P1 ablation canary.
- Add the hardened App launcher and proxy authentication.
- Enforce file/network/secret/tool/send grants and generation checks.
- Use Slack, then Community, then Hive as progressively higher-risk proofs.

## 12. Failure modes and required behavior

| Failure | Required behavior |
|---|---|
| malformed built-in manifest or unresolved binding | build fails; broken product is not shipped |
| malformed installed manifest | App disabled and surfaced as invalid; Core continues |
| duplicate lifecycle owner | activation fails before desired-state commit |
| reconciliation partly fails | generation remains authoritative; residue is visible and retryable |
| disable cleanup fails | every new operation remains blocked by generation; residue is retained |
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
- Reordering set-like manifest fields does not change the normalized registry or digest.
- Author-controlled bytes cannot claim built-in/locked trust.
- Package closure includes all declared/transitively loaded package bytes and rejects
  undeclared, duplicate, traversal and escaping-symlink paths; it excludes Instance data
  and Brain renderings.
- Installation package defaults and each Instance's package-release selection can coexist
  without silently moving another Instance.
- Two Instances using one package release and different Brain renderings have different
  activation digests, independent grants and independent rollback/generation state.
- Active and staged package releases coexist under separate immutable digest pointers.
- Generated built-in binding maps have no missing or unowned implementation.
- A target adapter never outputs a permission, capability or final destination absent
  from source intent.
- Cross-platform canonicalization golden vectors produce the same SHA-256 release digest.

### Integration proofs

- Pollinate works before and after the ownership cutover with the same public behavior.
- Killing or corrupting Pollinate reconciliation leaves Chat usable.
- Disable during an old open session blocks its next App-owned dispatch; grant revocation
  and policy tightening also take effect at the next operation.
- Re-enable does not restore revoked grants implicitly.
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
2. Pollinate is the internal App-boundary pilot.
3. Cross-host portability uses one canonical resource graph and reproducible release
   adapters, never two authored manifests or committed generated `dist/` state.
4. Phase C proves KiroCrew package compatibility with Brain-free fixtures against a
   pinned contract; P1 execution waits for explicit DDD final-destination authority.
5. Full Pollinate parity on KiroCrew is a later, separately proven P3 target.
6. Existing Plugin data is adapted read-only; no automatic migration or cleanup.
7. Third-party execution and Store distribution are outside V1.
8. App code cannot modify Agent OS authority or run inside the privileged main window.
9. DDD distribution remains an independent, more-restrictive final-destination ceiling;
   a separately approved policy extension is required before Brain-bearing KiroCrew export.
10. Catalog publication always remains a human-approved external action.

## 15. Deferred decisions

These are deliberately deferred until the earlier proof exists:

- the public SwarmAI catalog protocol and signing infrastructure;
- the exact isolated UI technology for third-party custom pages;
- portable scheduler semantics beyond simple prompt crons;
- App dependency installation and shared dependency garbage collection;
- whether Pollinate warrants a KiroCrew P2/P3 adapter after the portability audit;
- self-managed native Apps;
- cross-host state migration.

None is needed to prove Manifest-first, Ownership-first and Built-in-first.

## 16. V1 definition of done

V1 is complete only when:

- Pollinate has one manifest and one App ownership record;
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

No reviewer finding was dismissed as an accepted deviation. The remaining deliberate
boundary is that V1 proves a trusted built-in product boundary, not third-party
least-privilege execution or Brain-bearing KiroCrew portability.
