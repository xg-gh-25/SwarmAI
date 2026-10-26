---
name: autonomous-pipeline
pipeline_version: "1.0"  # v13 event-sourced protocol; one pipeline, no legacy fallback.
description: "Run one intent through Pipeline v1: documented, expert-reviewed, isolated, landed. Only when the user chose the pipeline. TRIGGER: \"run pipeline\", \"full pipeline\", \"用 pipeline 做\", \"无人值守做完\". NOT FOR: ordinary code changes (do them natively)."
consumes_artifacts:
- evaluation
- research
- alternatives
- design_doc
- changeset
- review
- test_report
produces_artifact:
- evaluation
- research
- design_doc
- changeset
- review
- test_report
- delivery
- checkpoint
tier: lazy
---
# Autonomous Pipeline

> One requirement in → a landed, audited delivery out, through the event-sourced Pipeline v1
> protocol (`"$SWARM_BACKEND_BIN" pipeline-v1`). Deterministic checks own every stage exit,
> and the landing; the model proposes, the checks dispose. Pushing is the user's call.

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

TRIGGER: "run pipeline", "autonomous pipeline", "pipeline for", "full pipeline".
DO NOT USE: for a single stage (use the specific skill directly).

## Flow (stages per profile, from `core/pipeline_profiles.py`)

| profile | stages |
|---|---|
| full, bugfix | evaluate → think → plan → build → review → test → deliver → reflect |
| trivial | evaluate → plan → build → review → test → deliver → reflect |
| direct | build → review → test → deliver → reflect (`create --requirement`, no PLAN) |
| research | evaluate → think → deliver → reflect |

`"$SWARM_BACKEND_BIN" pipeline-v1 create` → follow each response's `next` / `stage-complete` (REVIEW via
`review-start`, BUILD and TEST end with your validation report) → `land` in DELIVER, which also runs
REFLECT and removes the run's worktree before it returns.

## Verification

Before reporting the run, show evidence for each:

- [ ] **Run ended** — `"$SWARM_BACKEND_BIN" pipeline-v1 status --project <P> --run-id <R>` shows `completed`
      with `landing` (landed, or not_landed and its reason)
- [ ] **REPORT rendered by the engine** — `.artifacts/runs/<R>/REPORT.md` (never hand-edited)
- [ ] **Run Audit** — the five dimensions in `run_audit.json`; every BLOCK named with its reason
- [ ] **Tests reported** — REPORT's test section lists your BUILD and TEST reports on the landed tree
