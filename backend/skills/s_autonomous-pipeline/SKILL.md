---
name: autonomous-pipeline
pipeline_version: "1.0"  # v13 event-sourced protocol; one pipeline, no legacy fallback.
description: "Optional advanced mode: turn one intent into an auditable, expert-reviewed, isolated Agent Delivery Unit with the Pipeline v1 protocol. Use ONLY when the user chose the pipeline (asked for it, or picked it when asked about a risky change). Normal coding is native and never needs this skill.\n  TRIGGER: \"run pipeline\", \"autonomous pipeline\", \"pipeline for\", \"full pipeline\", \"用 pipeline 做\", \"无人值守做完\".\n  NOT FOR: ordinary code changes (do them natively), single stages (use the specific skill directly)."
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
> protocol (`backend/scripts/pipeline_v1.py`). Deterministic checks own every stage exit,
> the landing and push readiness; the model proposes, the checks dispose.

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

TRIGGER: "run pipeline", "autonomous pipeline", "pipeline for", "full pipeline".
DO NOT USE: for a single stage (use the specific skill directly).

## Flow (stages per profile, from `core/pipeline_profiles.py`)

| profile | stages |
|---|---|
| full, bugfix | evaluate → think → plan → build → review → test → deliver → reflect |
| goal | evaluate → think → plan → goal_cycle → review → test → deliver → reflect |
| trivial | evaluate → plan → build → review → test → deliver → reflect |
| direct | build → review → test → deliver → reflect (`create --requirement`, no PLAN) |
| research | evaluate → think → deliver → reflect |

`"$SWARM_BACKEND_BIN" pipeline-v1 create` → follow each response's `next` / `stage-complete` (REVIEW via
`review-start`, BUILD and TEST end with your validation report) → `land` in DELIVER → REFLECT runs in the
daemon's background and completes itself.

## Verification

Before reporting the run, show evidence for each:

- [ ] **Run ended** — `"$SWARM_BACKEND_BIN" pipeline-v1 status --project <P> --run-id <R>` shows `completed`
      with `landing` (landed, or not_landed and its reason)
- [ ] **REPORT rendered by the engine** — `.artifacts/runs/<R>/REPORT.md` (never hand-edited)
- [ ] **Run Audit** — the five dimensions in `run_audit.json`; every BLOCK named with its reason
- [ ] **Push readiness** — `"$SWARM_BACKEND_BIN" pipeline-v1 push-status`: the fold of push-effect checks, not a model field
- [ ] **Tests reported** — REPORT's test section lists your BUILD and TEST reports on the landed tree
