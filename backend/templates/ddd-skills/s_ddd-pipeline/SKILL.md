---
name: ddd-pipeline
description: "DDD-native autonomous dev loop — judge→execute→reflect on a SINGLE DDD using file-based .artifacts/ state (no data.db, no artifact_cli). Retains the moat: Gate-2 adversarial-before-commit + the 养成 ladder. DDD-native rewrite of SwarmAI's s_autonomous-pipeline.\n  TRIGGER: \"ddd pipeline\", \"run the ddd loop\", \"build in this ddd\".\n  NOT FOR: SwarmAI's own multi-project pipeline (that's the native s_autonomous-pipeline)."
tier: lazy
---
# DDD-Native Pipeline (s_ddd-pipeline)

The **judge→execute→reflect** dev loop a DDD carries so it gets smarter with use —
on any runtime, without SwarmAI's backend. Same quality moat, zero SwarmAI-infra coupling.

> **DDD-native rewrite of SwarmAI's `s_autonomous-pipeline`.** The original stores run
> state in `data.db` and drives every stage through `artifact_cli.py`. A DDD shipped to
> Kiro / Claude Code / an AIM package has neither. This keeps the discipline and drops
> the machine room — run state is **plain files** under the DDD's own `.artifacts/`.

## The decoupling

| Concern | s_autonomous-pipeline (SwarmAI) | s_ddd-pipeline (portable) |
|---------|----------------------------------|---------------------------|
| Run state | `data.db` + `artifact_cli.py` | **plain JSON** in `<ddd>/.artifacts/runs/<run_id>/run.json` |
| Multi-project index | SQLite `data.db` | none — **one DDD, one `.artifacts/`** |
| DDD docs read | `Projects/<P>/*.md` | THIS DDD's own 4 docs (co-located) |
| The moat | Gate-2 adversarial + 养成 ladder | **identical — retained, non-negotiable** |

## Stages (same shape, file-backed)

`EVALUATE → THINK → PLAN → BUILD → REVIEW → TEST → ADVERSARIAL → DELIVER → REFLECT`

Each stage writes a sibling JSON artifact; `run.json` tracks stage status. A fresh agent
on any runtime resumes by reading `run.json` — no CLI, no DB. See INSTRUCTIONS.md.

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
