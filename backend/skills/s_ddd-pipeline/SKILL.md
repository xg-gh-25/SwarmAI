---
name: ddd-pipeline
description: "DDD-native autonomous dev loop — a portable, decoupled rewrite of s_autonomous-pipeline that runs judge→execute→reflect on a SINGLE DDD using file-based .artifacts/ state (no data.db, no artifact_cli). Retains the moat: Gate-2 adversarial-before-commit + the 养成 ladder. This is section ④ of a DDD, the executable half of the self-養成 loop.\n  TRIGGER: \"ddd pipeline\", \"run the ddd loop\", \"native pipeline for this DDD\".\n  NOT FOR: SwarmAI's own multi-project pipeline (use s_autonomous-pipeline), single stages."
tier: lazy
---
# DDD-Native Pipeline (s_ddd-pipeline)

The **judge→execute→reflect** dev loop a DDD carries so it gets smarter with use —
**on any runtime, without SwarmAI's backend**. This is the DDD-native counterpart
to `s_autonomous-pipeline`: same quality moat, zero SwarmAI-infra coupling.

> **Why a separate skill, not a copy.** `s_autonomous-pipeline` is bound to SwarmAI:
> it stores run state in `data.db` and drives every stage through `artifact_cli.py`
> (a multi-project CLI over that DB). A DDD that ships to Kiro / Claude Code / an AIM
> package has **neither**. `s_ddd-pipeline` keeps the discipline and drops the
> machine room — run state is **plain files** under the DDD's own `.artifacts/`.

## The decoupling (what changes vs s_autonomous-pipeline)

| Concern | s_autonomous-pipeline (SwarmAI-bound) | s_ddd-pipeline (portable) |
|---------|----------------------------------------|---------------------------|
| Run state | `data.db` + `artifact_cli.py` CLI | **plain JSON files** in `<ddd>/.artifacts/runs/<run_id>/run.json` |
| Multi-project index | SQLite `data.db` | none — **one DDD, one `.artifacts/`** |
| Stage records | `artifact_cli run-update` | write `run.json` directly (a DDD needs no cross-project query) |
| DDD docs read | `Projects/<P>/*.md` | **this DDD's own** `PRODUCT/TECH/IMPROVEMENT/PROJECT.md` (co-located) |
| The moat | Gate-2 adversarial + 养成 ladder | **identical — retained, non-negotiable** |

Everything SwarmAI-specific is replaced by files the DDD already owns. Nothing about
the *quality gates* changes.

## Stages (same shape, file-backed)

`EVALUATE → THINK → PLAN → BUILD → REVIEW → TEST → ADVERSARIAL → DELIVER → REFLECT`

State lives in `<ddd>/.artifacts/runs/<run_id>/run.json` — a plain object:

```json
{
  "run_id": "run_<slug>",
  "requirement": "...",
  "profile": "full | bugfix | trivial | goal | docs | research",
  "stages": [
    {"stage": "evaluate", "status": "completed", "artifact": "evaluate.json"},
    {"stage": "build", "status": "completed", "artifact": "changeset.json"}
  ],
  "adversarial_review": {"spawned": true, "findings": [...], "findings_remaining": 0}
}
```

Each stage writes its artifact as a sibling JSON file (`evaluate.json`, `research.json`,
`design_doc.json`, `changeset.json`, `review.json`, `test_report.json`, `delivery.json`).
No CLI, no DB — read/write the files directly. A fresh agent on any runtime can resume
by reading `run.json`.

## THE MOAT (non-negotiable — this is what beats an empty workflow)

Dropping either of these degrades s_ddd-pipeline into "just another empty dev-flow
template" (the exact thing a DDD's judgment-loop exists to beat). They are **required**,
not optional-when-light:

### 1. Gate-2 Adversarial-before-commit
Before ANY commit, spawn a **fresh-context adversarial reviewer** (the runtime's
sub-agent mechanism — Kiro sub-agent / Claude Code Task / a second model call) with
ZERO of the builder's context. It must try to REFUTE the changeset: find the bug, the
untested path, the broken contract. Record findings in `run.json.adversarial_review`.
**No commit while `findings_remaining > 0` at HIGH/CRITICAL.** On a runtime with no
sub-agent primitive (Quick / ChatGPT), degrade to an explicit **"review this diff
adversarially before you commit"** step surfaced to the human — never silently skip.

### 2. The 养成 ladder (judgment → gate)
A pitfall discovered in REFLECT is not just logged — it climbs the ladder:
`prose in ② IMPROVEMENT.md` → (recurs) → `a rule` → (recurs 3×) → `an executable ③
gate` (`gates/<name>.py|sh`, exit-2 = BLOCK, with a knockout test). Each run's REFLECT
sediments judgment back into THIS DDD's ② docs, so the next run is smarter. This is the
self-養成 that compounds — the loop's whole point.

## The loop (self-contained, closes inside ONE DDD)

```
② KNOWLEDGE (judge) ──► s_ddd-pipeline (execute) ──► changes the ⑤-bound repo
       ▲                                                      │
       └──────── REFLECT: new pitfalls/judgment ◄────────────┘
                 written back to THIS DDD's ② IMPROVEMENT.md (养成 ladder)
```

Judgment, execution, reflection all anchored on one DDD → self-contained (no SwarmAI
global infra) and self-improving (each run sediments judgment). See INSTRUCTIONS.md
for the full stage-by-stage file-state protocol.

## Status

🆕 **Portability seed (2026-07-12).** Ships the decoupled contract + moat definition.
The full stage-execution engine iterates per spec §7 ("prove ONE loop first, then
scale") — this skill establishes the file-state shape and the non-negotiable moat so
a DDD is structurally self-養成 on any runtime.
