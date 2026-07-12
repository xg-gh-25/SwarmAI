---
name: ddd-pipeline
description: "DDD-native autonomous dev loop — judge→execute→reflect on a SINGLE DDD via a bundled, decoupled engine (engine/artifact_cli.py, file-based .artifacts/ state, NO data.db, NO SwarmAI backend). Retains the moat: Gate-2 adversarial-before-commit + the 养成 ladder. DDD-native decouple of SwarmAI's s_autonomous-pipeline engine.\n  TRIGGER: \"ddd pipeline\", \"run the ddd loop\", \"build in this ddd\".\n  NOT FOR: SwarmAI's own multi-project pipeline (that's the native s_autonomous-pipeline)."
tier: lazy
---
# DDD-Native Pipeline (s_ddd-pipeline)

The **judge→execute→reflect** dev loop a DDD carries so it gets smarter with use —
on any runtime, without SwarmAI's backend. Same quality moat; no SwarmAI package is
required to import or run the engine (the few off-path SwarmAI imports are try/except
fail-soft and only touch advisory DDD-maintenance subcommands).

> **DDD-native decouple of SwarmAI's `s_autonomous-pipeline` engine.** The original's
> `artifact_cli.py` binds to `data.db` (a Radar-todo side-effect) and `core.*` modules.
> A DDD shipped to Kiro / Claude Code / an AIM package has neither. This bundles the REAL
> engine — `engine/artifact_cli.py` + validator + registry + the cultivation trio, all
> copied and decoupled — so run state is **plain files** under the DDD's own `.artifacts/`
> and the engine imports with SwarmAI's `core/` completely absent (proven: 12/12 modules).

## The decoupling (real engine, not a hand-written shell)

`engine/` holds the copied-and-decoupled machine (~10.8K lines): `artifact_cli.py`,
`pipeline_validator.py`, `artifact_registry.py`, `pipeline_profiles.py`, `file_lock.py`,
the 4 pipeline scripts, and the **cultivation trio** (`ddd_cultivation` + `persist_routing`
+ `ddd_auto_approval`) that powers a real REFLECT→DDD write (not a naive append).

| Concern | s_autonomous-pipeline (SwarmAI) | s_ddd-pipeline (portable) |
|---------|----------------------------------|---------------------------|
| Engine | `backend/scripts/artifact_cli.py` + `core.*` | **`engine/artifact_cli.py`** (co-located, decoupled) |
| Run state | `data.db` Radar row + `run.json` | **`run.json`** only — sqlite side-effect dropped |
| Workspace | `~/.swarm-ai/SwarmWS` (hardcoded) | `$SWARM_WORKSPACE` or cwd — portable |
| DDD docs read | `Projects/<P>/*.md` | THIS DDD's own 4 docs (co-located) |
| The moat | Gate-2 adversarial + 养成 ladder | **identical — retained, non-negotiable** |

## Stages (same shape, engine-driven)

`EVALUATE → THINK → PLAN → BUILD → REVIEW → TEST → ADVERSARIAL → DELIVER → REFLECT`

Drive via `engine/artifact_cli.py` (`run-create`, `run-update`, `run-cultivate`, …) with
`SWARM_WORKSPACE=<ddd-workspace>`. The engine enforces the same mechanical gates
(stage_doc_consumed, push-ready) standalone. A fresh agent resumes by reading `run.json`.
See INSTRUCTIONS.md.

## THE MOAT (non-negotiable — what beats an empty workflow)

**1. Gate-2 Adversarial-before-commit.** Before ANY commit, spawn a fresh-context
adversarial reviewer (the runtime's sub-agent mechanism) with ZERO builder context to
REFUTE the changeset. Record findings in `run.json.adversarial_review`. **No commit while
HIGH/CRITICAL `findings_remaining > 0`.** On a runtime with no sub-agent (Quick/ChatGPT),
degrade to an explicit "review this diff adversarially before commit" step — never
silently skip.

**2. The 养成 ladder.** Every REFLECT sediments new pitfalls into THIS DDD's ②
`IMPROVEMENT.md` (via `s_ddd-persist`). A recurring pitfall climbs: prose → rule → (3×)
an executable ③ gate with a knockout test. This is the self-養成 that compounds.

## The loop (self-contained, closes inside ONE DDD)

```
② KNOWLEDGE (judge) ──► s_ddd-pipeline (execute) ──► changes the ⑤-bound repo
       ▲                                                      │
       └──────── REFLECT: new pitfalls/judgment ◄────────────┘
                 written back to THIS DDD's ② IMPROVEMENT.md (养成 ladder)
```

Dropping either moat half degrades this into just-another-empty-workflow — the exact
thing a judgment-bearing DDD exists to beat.
