---
name: ddd-manager
description: "Provision and manage DDD projects that conform to the canonical six-section structure — the self-propagation seed. Full lifecycle: CREATE (with context-extraction from a codebase, NOT blank templates), BIND a repo (⑤ delivery contract), LIST / EDIT / RENAME. Pure filesystem, no SwarmAI backend — a DDD carries this so it can create MORE spec-compliant DDDs on Kiro / Claude Code.\n  TRIGGER: \"create ddd\", \"new ddd project\", \"add a domain\", \"bind repo to ddd\", \"provision ddd\".\n  NOT FOR: SwarmAI's own project CRUD (native s_project-manager)."
tier: lazy
---
# DDD Manager (s_ddd-manager) — the self-propagation seed

Provision a NEW spec-compliant DDD (the canonical six-section structure), and manage the
lifecycle of existing ones. Because every DDD carries `s_ddd-manager`, a DDD can create
more DDDs — the ①–⑥ spec spreads to Kiro / Claude Code / any runtime **without SwarmAI's
backend**.

> **DDD-native decouple of SwarmAI's `s_project-manager`.** Same CREATE-with-extraction +
> BIND lifecycle discipline, re-homed to pure filesystem: no `data.db`, no `artifact_cli`,
> no SwarmAI services. Ships INSIDE every DDD (copied from the official template at CREATE).

## The canonical six-section structure this provisions

```
<ddd>/
├── .project.json / aim.json / AGENTS.md / .crux_template.md   # ① Identity & manifest
├── PRODUCT/TECH/IMPROVEMENT/PROJECT.md + Knowledge/            # ② Knowledge
├── gates/ (+ context/includes/)                               # ③ Gates (accrete)
├── skills/  ← the 3 DDD-native skills live here                # ④ Capabilities
├── bindings.yaml                                              # ⑤ Delivery contract (on BIND)
└── REFRESHER.md                                              # ⑥ Code-intel refresher
```
The 3 ④ skills: `s_ddd-manager` `s_ddd-persist` `s_repo-to-ddd` — copied in at CREATE.
Do NOT scaffold `agents/`/`agent-sops/` (AIM-export form, generated at export, not native).

## Lifecycle: CREATE → BIND → PULL → DEVELOP → SYNC-BACK

CREATE is below. BIND+ apply only to a repo-bound DDD (a no-repo DDD stops at CREATE — its
4 docs ARE the deliverable).

## CREATE a new DDD

### Step 1 — name + optional codebase path
PascalCase preferred; if not PascalCase, note the convention but accept the user's choice.
Path (optional) is an existing codebase to extract context FROM (recorded in TECH.md — never
a symlink).

### Step 2 — validate
`test -e "Projects/<Name>"` → refuse duplicates; `<Name>` == a reserved default is refused.

### Step 3 — GATHER CONTEXT before writing (CRITICAL: never write blank templates)

Populate the docs from real sources, in priority order:

**3a. This session's conversation (highest priority):** architecture/stack/audience decisions
→ the docs; non-goals → PRODUCT; past failures → IMPROVEMENT; work items → PROJECT.

**3b. The codebase (if a path was given)** — read the first ~50-80 lines of each, skip if absent:
```bash
cat {path}/package.json {path}/pyproject.toml {path}/Cargo.toml {path}/go.mod 2>/dev/null   # → Stack
cat {path}/README.md 2>/dev/null                                                            # → Vision, description
ls {path}/docs/*.md 2>/dev/null; cat {path}/docs/{design,architecture}.md 2>/dev/null       # → Architecture
cat {path}/CLAUDE.md {path}/AGENTS.md {path}/.cursorrules 2>/dev/null                        # → Conventions (merge)
cat {path}/Makefile {path}/justfile 2>/dev/null; ls {path}/.github/workflows/*.yml 2>/dev/null  # → Dev Commands
cat {path}/LICENSE 2>/dev/null | head -3                                                     # → license note
test -d {path}/.git && git -C {path} log --oneline -10                                       # → Current Focus
```

**3c. Parse → route to the right doc:**

| Source | Extract → | Target |
|--------|-----------|--------|
| README first paragraph / features | description, capabilities | PRODUCT § Vision / Strategic Priorities |
| package.json deps+scripts / pyproject | frameworks, dev/test/build cmds | TECH § Stack / Dev Commands |
| docs/design.md, docs/architecture.md | system overview | TECH § Architecture |
| CLAUDE.md / AGENTS.md / .cursorrules | conventions (merge, don't overwrite) | TECH § Conventions |
| .github/workflows, Makefile | CI + build commands | TECH § Dev Commands |
| LICENSE | license type | PRODUCT (note) |
| git log -10 | recent activity | PROJECT § Current Focus |
| session conversation | decisions, goals, blockers | all 4 docs |

### Step 4 — write POPULATED docs (extracted content where found, placeholder only where not)

The 4 ② doc skeletons (fill extracted fields, keep the `_italic placeholder_` for unknowns):

- **PRODUCT.md** — `## Vision` · `## Strategic Priorities` · `## Success Criteria` · `## Non-Goals`
- **TECH.md** — `## Architecture` · `## Stack` · `## Codebase Location` · `## Dev Commands` · `## Conventions` · `## Runtime Traps` · `## Key Files`
- **IMPROVEMENT.md** — `## What Worked` · `## What Failed` · `## What to Watch For` · `## Known Issues`
- **PROJECT.md** — `## Current Focus` · `## Open Items` · `## Recent Decisions` · `## Blocked By`

Then the skeleton: ① `.project.json` (stamp `ddd_spec_version`) + `aim.json` (declares the 3
native skills) + `AGENTS.md` (the ONE unified README covering all six sections); ③ `gates/` +
`gates/context/includes/` (`.gitkeep`); ④ `skills/` with the 3 native skills; ⑥ `REFRESHER.md`.

## BIND a repo (⑤ DELIVERY CONTRACT) — turns 4 docs into a DDD that governs a real repo

BIND is NOT a symlink — it's a clone + code-intel index + a **governance-as-DATA delivery
contract** declared in `Projects/<name>/bindings.yaml`. A DDD may bind many repos.

**Three shapes (the lifecycle adapts — NOT internal-only):**

| Shape | bindings.yaml | DEVELOP path | SYNC-BACK |
|---|---|---|---|
| **External** (GitHub) | `kind: external` | branch + PR + `s_code-review` | opt-in |
| **Internal** (enterprise host) | `kind: internal` | the host's `s_internal-*` toolchain (provided by that host, not this package) | opt-in |
| **No-repo** (pure DDD) | none | docs ARE the deliverable — edit directly | n/a |

`bindings.yaml` (⑤, `DDD_SPEC_VERSION 1.0`) — `bindings` is an ARRAY; each entry:
```yaml
bindings:
  - repo: my-repo
    kind: external                 # external | internal
    clone: "https://github.com/org/my-repo.git"   # git URL (or an enterprise-host clone command for internal)
    worktree: null                 # null → clone OUTSIDE the DDD tree (never pollute DDD git)
    delivery_contract:             # governance-as-DATA — NEVER hoist into a global rule file
      remote_kind: github-pr       # github-pr | code-amazon-cr | self-hosted-main
      build_system: none           # none | local-script | (an enterprise build system)  (orthogonal to remote_kind)
      branch: main
      review_path: s_code-review   # s_code-review (github) | the host's internal review skill
      auto_send: manual-push       # manual-push = stop at PUSH-READY (commit=auto, push=user) | on-clean-review; required
      refresh_policy: on-develop   # when the ⑥ refresher regenerates code-intel
```
- **Delivery policy lives in the binding, not a global rule file** — each repo carries its own.
- **After writing bindings.yaml → RECONCILE the project's class (SSOT).** A DDD's class is
  DERIVED from its bindings (no bindings = `none`; any `kind:internal` = `internal`; else
  `external`) — never a stored flag. Binding an **internal** repo means the project now needs
  the internal toolchain (`s_internal-*` skills + `no_git_push` ③ gate). Trigger the reconcile
  so they're provisioned at BIND (idempotent — no-op for external/no-repo or if already present):
  ```python
  from core.swarm_workspace_manager import SwarmWorkspaceManager
  await SwarmWorkspaceManager().sync_internal_provisioning("<project>")
  # → {"classification": "internal", "provisioned": [...]}  (reads classify_project, fires
  #    provision(internal=True) only when internal). On Kiro/no-SwarmAI: skip — an internal
  #    DDD there already carries its skills from a prior SwarmAI provision or the export.
  ```
- **PULL + index:** clone the repo into a worktree OUTSIDE the DDD tree, then run the ⑥
  refresher (`s_repo-to-ddd`) to build `code-intel.json`. Idempotent. `code_intel` is NOT a
  binding member — it's a DERIVED projection (§3.6), regenerated locally, gitignored.
- **DEVELOP:** read the project's class (`classify_project`) + the binding's `remote_kind` +
  `build_system` and route — never assume a specific enterprise toolchain. The agent never
  `git push`es an internal CR remote (the host's auto-merge owns it).

## MANAGE — List / Edit / Rename

- **List** — enumerate `Projects/*/` that have `.project.json`; show name + spec version + bound repos.
- **Edit** — the 4 ② docs are edited via `s_ddd-persist` (routing + locked write), never a blind overwrite.
- **Rename** — `git mv Projects/<old> Projects/<new>`, update `.project.json.name` + `aim.json`,
  grep for the old name in the DDD's own docs. Never rename the reserved default.
- **Backfill** a pre-spec project idempotently: write `.project.json` (stable id, never a fresh
  uuid), fill the skeleton only-if-absent, content-gate any prune (never delete human content).

## Spec versioning (anti-drift)

`s_ddd-manager` declares which `DDD_SPEC_VERSION` it implements. Because a DDD creates more
DDDs, the structure is a VERSIONED spec — declaring the version keeps propagated DDDs from
drifting. Bump it when the six-section structure changes.

## Portability

No SwarmAI backend. State is the filesystem. That is what lets a DDD, packaged via `aim`,
create + manage + bind sibling DDDs directly inside Kiro / Claude Code.
