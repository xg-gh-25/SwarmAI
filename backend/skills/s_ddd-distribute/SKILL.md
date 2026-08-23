---
name: ddd-distribute
description: "Package a grown DDD into a distributable capability package — the last step of the DDD lifecycle (cultivate → version → DISTRIBUTE). Human-in-the-loop: reads the DDD's OWN declared reach (aim.json distribution block = the ceiling), confirms which declared target(s) to emit NOW, renders BOTH standard shapes on demand — an internal AIM capabilities package (Config+AIMBuild+agent-spec) and/or an Open-Plugins plugin (.plugin/plugin.json) — runs a fail-closed content-safety scan over the emitted tree, and returns the built package path. Never widens reach by inference; emit ≠ publish.\n  TRIGGER: \"distribute this ddd\", \"package ddd\", \"ship ddd to kiro/quick/claude code\", \"publish ddd\", \"make a plugin from this ddd\".\n  NOT FOR: creating/binding a DDD (s_project-manager); running the pipeline (s_autonomous-pipeline); publishing content/media (s_pollinate)."
tier: lazy
---
# DDD Distribute (s_ddd-distribute) — the distribution step of the DDD lifecycle

Render a **grown DDD** into a **distributable capability package** so another agent
host (Kiro / Quick / Claude Code / a public consumer) can install and use it.

This skill is a **thin human-in-the-loop wrapper** — all packaging logic lives in
`core/ddd_packager.py` and the reach policy in `core/ddd_distribution_policy.py`.
The skill's job is: read the declaration → confirm the target subset with the human
→ call the packager → surface warnings and the built path.

## The reach model (READ BEFORE RUNNING)

A DDD declares its own reach in its `aim.json`:

```json
"distribution": {
  "targets": ["aim-capabilities", "open-plugin"],   // the CEILING (0..2)
  "visibility": "internal"                            // internal | external
}
```

- **The declaration is the ceiling.** You may emit a SUBSET of the declared targets;
  you may NEVER add an undeclared target or raise visibility. To widen reach, the
  DDD *owner* edits the declaration first (a separate, deliberate change).
- **Fail-closed.** An absent / malformed declaration → `targets:[]` = not
  distributable. Nothing leaves by inference.
- **Emit ≠ publish.** A `visibility:internal` DDD may EMIT an open-plugin tree for a
  private install, but the public-publish step is refused until visibility is
  explicitly `external` (a human-gated change).

## Workflow (HITL)

1. **Locate the DDD** — resolve `<workspace>/Projects/<name>/` (the DDD dir).
2. **Read the declaration** — call the packager's policy; show the human the
   declared `targets` + `visibility` + any warnings (e.g. an unknown target token).
3. **Confirm the subset** — ask the human WHICH declared target(s) to emit now, and
   whether this is an emit-only pass or an external publish. Never propose a target
   the DDD didn't declare.
4. **Render** — run `scripts/distribute.py` with the confirmed subset. The packager:
   emits each target tree, runs the content-safety scan over the EMITTED tree, and
   aborts (fail-closed) on any secret / host-path (any target) or internal-string
   (external publish).
5. **Return the path(s)** — report the built package dir(s) + any scan warnings +
   which skills were included (class-B domain) vs excluded (class-A enablement /
   unclassified). Point the human at the install command:
   - internal → build + `cr`/PR into the internal package, then `aim plugins install <Pkg>`
   - external → push to the public code host, then `aim plugins install` / `bash install.sh`

## Invocation

```bash
python3 scripts/distribute.py --ddd <path/to/Projects/NAME> --out <out_dir> \
  [--targets aim-capabilities,open-plugin] [--publish]
```

- `--targets` omitted → emit the full DECLARED set (subset-only still enforced).
- `--publish` → run the external-publish gate (refused unless visibility=external).
- The script only ORCHESTRATES — it never contains packaging logic.

## What this skill does NOT do

- It does not run `aim`, `git`, or `gh` — building/committing/pushing the emitted
  package is the human's step (crosses auth + build-duration walls).
- It does not backfill a DDD's `distribution` block — that is the owner's declaration.
- It does not widen reach, ever.
