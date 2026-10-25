---
title: External App canaries — three portable Skills and PPTX Maker
created: 2026-10-25
updated: 2026-10-25
project: SwarmAI
status: accepted — user selected all four candidates
tags: [apps, skills, portability, provenance, pptx, kirocrew]
---

# External App canaries

## 0. Decision

SwarmAI will integrate `domain-modeling`, `research`, `frontend-design`, and
`pptx-maker` as four separately identified Apps. The first three become usable,
shipped Skill Apps. PPTX Maker enters the immutable catalog with its complete
pinned source graph blocked on every SwarmAI profile until its host adapter is
implemented and clean-room tested.

This is a bounded canary, not a public App Store. No package is enabled from a
live `~/.kiro`, `~/.swarm-ai`, global Skill directory, mutable branch, or
unverified download.

## 1. Product boundary

The three instruction-only Skills may run in the existing trusted Skill loader
because their tracked package closures contain no executable code, MCP server,
network client, hook, job, or lifecycle script. Their App evidence remains
`catalog-only` until packaged proof exists; that label does not prevent their
already-shipped Skill contribution from being resolved.

PPTX Maker is different. Its backend, generated-code MCP tools, dependency
provisioner, network fetches and custom UI require a real SwarmAI Host Adapter.
Phase 1 catalogs its complete immutable source and reports required resources as
blocked. It must not import KiroCrew Python modules or expose lifecycle controls.

## 2. Immutable upstream inputs

| App | Source pin | License | Audited closure |
|---|---|---|---|
| domain-modeling | `mattpocock/skills@5beb35b040e3de97e54fa9e386d7e42d15c295c2` | MIT | Skill, glossary format, ADR format, optional agent metadata |
| research | `mattpocock/skills@5beb35b040e3de97e54fa9e386d7e42d15c295c2` | MIT | Skill and optional agent metadata |
| frontend-design | `anthropics/skills@dbd4588f9e1033efb41dad4bef2f7947c8993d44` | Apache-2.0 | Skill and skill-local license |
| pptx-maker adapter | `kirodotdev/KiroCrew@27cb0849eddd693663b3c42c865fb76d7ba5c5d7` | Apache-2.0 plus retained attribution | App backend, four Agents, Skill, prompts, tests, React source and assets |
| PPTX engine | `aws-samples/sample-spec-driven-presentation-maker@b7c7fcf0972b33a480f1b717a3de5bc288d53742` | MIT-0 | Runtime tarball, SHA-256 `ffe8e10b973cada1f6e99629ac780bde25461b432ad07943b4f15ca11c07dfe0` |

Install counts are discovery evidence, not trust: on 2026-10-25 skills.sh
reported about 880.8K for domain-modeling, 713.6K for research and 1.0M for
frontend-design. Every source still passes local schema, license, closure and
security checks.

## 3. Tracked package layout

`domain-modeling` and `research` each own one package directory:

```text
backend/apps/<app>/
  app.json
  package/
    SKILL.md              # SwarmAI metadata wrapper
    INSTRUCTIONS.md       # adapted instructions, modifications marked
    LICENSE.upstream
    PROVENANCE.json
    upstream/             # verbatim pinned source files
```

SwarmAI already ships `backend/skills/s_frontend-design`. Registering a second
Skill with the same name would be shadowed and would not prove installation.
The `frontend-design` App therefore owns that existing Skill, applies a marked
attributed guidance update to its instructions, and keeps the verbatim Anthropic
source, license, provenance and adaptation record under
`backend/apps/frontend-design/package/`.

`PROVENANCE.json` is a strict host-verified sidecar containing source URL,
commit, license identifier, upstream path-to-SHA-256 map and modification notes.
It cannot declare trust, grants, evidence state or lifecycle state. The App
package digest covers it because it lives inside the declared Skill resource.

## 4. Writers, readers and timing

A maintainer-only sync command downloads the declared immutable URLs, verifies
every expected digest, writes only into a selected App package and never enables
it. Normal builds perform no network access. They validate tracked files against
`PROVENANCE.json` and fail when a file, license or generated output is stale.

`generate_app_registry.py` remains the build-time writer of the App index and
binding maps. It derives `source_tier=external` only after strict provenance
validation; `app.json` cannot claim that tier. It emits the Skill package paths
into `generated/app_bindings.py`.

At backend startup, the Skill registry reads the generated binding map and adds
those exact package roots to its built-in candidates. It does not scan
`backend/apps`, parse App manifests, access the network or inspect user-global
Skills. Existing duplicate-name and Skill validation rules still apply. Chat
send, stream, tool-call and file-write paths do no new App work.

## 5. Host adaptation

The Skill wrappers translate host vocabulary only where needed:

- `domain-modeling` retains the upstream glossary and ADR discipline but follows
  the repository's existing documentation locations when they differ.
- `research` uses SwarmAI's background-agent and primary-source tools, writes to
  the repository's existing notes convention, and never writes to a guessed path.
- `frontend-design` keeps the Anthropic source verbatim under `upstream/` and
  enriches the existing SwarmAI Skill with a marked attributed guidance section;
  SwarmAI design tokens, shared overlay components, type scale, accessibility and
  project instructions override general aesthetic advice.

The wrappers must not grant permissions. Their manifests request only required
Host capabilities. Unknown or missing capabilities block that profile.

## 6. PPTX Maker stages

Phase 1 vendors and verifies the complete pinned KiroCrew source graph and its
license/attribution. Its `app.json` declares Skill, Agent, backend, MCP, surface,
storage and dependency resources. Every SwarmAI target binding is `unsupported`
and required, so all profiles report blocked and no code is imported.

Phase 2 replaces KiroCrew host dependencies with SwarmAI implementations for:
strict subprocess isolation, credential stripping, bounded extraction, atomic
writes, path containment, app storage, `uv` resolution and HTTPS/SHA-256 asset
fetching. The engine commit and tarball digest remain immutable deployment inputs.

Phase 3 adapts FastAPI routes, four Agent templates, the private `@sdpm` MCP
server and React page. It adds explicit network, storage, subprocess and optional
preview-tool permissions. Only then may a supported profile receive an execution
release. Product proof still requires a packaged clean-room run producing a real
`.pptx` through public UI/API.

## 7. Security and licensing invariants

- Upstream content is data during import and cannot alter build instructions.
- A mutable tag, branch or unpinned URL is rejected.
- Every vendored byte has an expected SHA-256 and applicable license copy.
- Apache-modified files carry a modification notice; upstream originals remain
  available verbatim for comparison.
- External content cannot register middleware, security hooks or arbitrary
  FastAPI modules in the privileged process.
- No package can read ambient credentials or copy developer runtime state.
- PPTX archive extraction rejects traversal, links, devices, oversized members
  and unexpected roots before any subprocess starts.
- No App receives lifecycle controls or `product-proven` from its manifest.

## 8. Tests and proof

Source tests cover provenance schema rejection, digest mismatch, license absence,
mutable source rejection, duplicate Skill names, stale generated files and App
profile accounting. Runtime tests prove startup-only generated registration and
no App manifest scan on `/api/apps` or Chat paths.

Each Skill has a positive trigger fixture, a negative non-trigger fixture and an
ablation that removes its packaged instructions. Frontend tests show all four
Apps, their source attribution and honest profile state without lifecycle buttons.

PPTX Phase 1 tests prove every required resource is blocked. Later phases add
malicious archive, credential stripping, path escape, dependency lock, engine
hash, backend/API, MCP and real presentation-generation tests.

No repository test upgrades evidence to `product-proven`. That requires two clean
builders, two clean runtimes, an unavailable source checkout, empty HOME/app-data,
no global Skills or ambient credentials, installer verification and retained
artifacts tied to the exact package and execution digests.

## 9. Delivery order

1. Land provenance schema, build checks and startup-only generated Skill paths.
2. Vendor and register domain-modeling, research and frontend-design.
3. Vendor PPTX Maker's complete source as blocked catalog content.
4. Review, test and merge this catalog/import slice without a production build.
5. Implement PPTX runtime adaptation in later isolated slices.
6. Run installer and clean-room proof only with separate authorization.

The first merged slice therefore gives users three real shipped Skills and one
honest blocked PPTX Maker catalog entry. It does not claim a general Store,
third-party executable sandbox, Enable/Disable or completed PPTX portability.
