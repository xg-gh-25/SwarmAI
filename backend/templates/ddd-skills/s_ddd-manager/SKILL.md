---
name: ddd-manager
description: "Provision and manage DDD (Domain-Driven Design) projects that conform to the canonical six-section structure. The self-propagation seed: a DDD carries this so it can create MORE spec-compliant DDDs on any runtime, without SwarmAI. DDD-native rewrite of SwarmAI's s_project-manager (file-based, no backend).\n  TRIGGER: \"create ddd\", \"new ddd project\", \"add a domain\", \"provision ddd\".\n  NOT FOR: SwarmAI's own project CRUD (that's the native s_project-manager)."
tier: lazy
---
# DDD Manager (s_ddd-manager) — the self-propagation seed

Provision a NEW spec-compliant DDD (the canonical six-section structure), and manage
existing ones. This is the **self-propagation seed**: because every DDD carries
`s_ddd-manager`, a DDD can create more DDDs — the ①–⑥ spec spreads to Kiro / Claude
Code / any runtime **without needing SwarmAI's backend**.

> **DDD-native rewrite of SwarmAI's `s_project-manager`.** Learned from the original,
> re-designed to be portable: pure filesystem, no `data.db`, no `artifact_cli`, no
> SwarmAI services. Ships INSIDE every DDD (copied from the official template at
> provision time) so it travels with the package.

## The canonical six-section structure this provisions

```
<ddd>/
├── .project.json / aim.json / AGENTS.md / .crux_template.md   # ① Identity & manifest
├── PRODUCT/TECH/IMPROVEMENT/PROJECT.md + Knowledge/            # ② Knowledge
├── gates/ (+ context/includes/)                               # ③ Gates (accrete)
├── skills/  ← the 5 DDD-native skills live here                # ④ Capabilities
├── bindings.yaml                                              # ⑤ Delivery contract (on BIND)
└── REFRESHER.md                                              # ⑥ Code-intel refresher
```

## Create a new DDD

1. Get the name (PascalCase preferred) + optional bound-repo path.
2. Materialize the skeleton (only-if-absent, idempotent):
   - `.project.json` with `ddd_spec_version` stamped
   - ① manifests: `aim.json` (declares the 5 native skills), `AGENTS.md` (the ONE
     unified README documenting all six sections), `.crux_template.md`
   - ② the 4 DDD docs + `Knowledge/`
   - ③ `gates/` + `gates/context/includes/` (empty, `.gitkeep`)
   - ④ `skills/` with the 5 DDD-native skills copied in (`s_ddd-manager`,
     `s_ddd-persist`, `s_ddd-pipeline`, `s_ddd-pollinate`, `s_ai-ready-repo`)
   - ⑥ `REFRESHER.md` marker
3. Do NOT scaffold `agents/` or `agent-sops/` — those are AIM-export-form, generated
   at export, not part of the native skeleton.
4. `bindings.yaml` (⑤) is added later by BIND, when a repo is attached.

## Manage / backfill

- **List** DDDs, **rename**, **edit** the 4 docs (or via `s_ddd-persist`).
- **Backfill** a pre-spec project idempotently: write `.project.json` (stable id, never
  a fresh uuid), prune legacy over-build (content-gated — never delete human content),
  fill the skeleton only-if-absent.

## Spec versioning (anti-drift)

`s_ddd-manager` declares which `DDD_SPEC_VERSION` it implements. Because a DDD creates
more DDDs, the structure is a VERSIONED spec — declaring the version is what keeps
propagated DDDs from drifting. Bump it when the six-section structure changes.

## Portability

No SwarmAI backend. State is the filesystem. That is what lets a DDD, once packaged via
`aim`, create and manage sibling DDDs directly inside Kiro / Claude Code.
