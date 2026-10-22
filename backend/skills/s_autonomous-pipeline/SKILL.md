---
name: autonomous-pipeline
pipeline_version: "1.0"  # v13 event-sourced protocol; one pipeline, no legacy fallback.
description: "Turn one intent into an auditable, verified Agent Delivery Unit with the event-sourced Pipeline v1 protocol. Stages are profile-specific and deterministic checks own progression, landing and push readiness.\n  TRIGGER: \"run pipeline\", \"autonomous pipeline\", \"pipeline for\", \"full pipeline\".\n  NOT FOR: single stages (use the specific skill directly)."
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

`pipeline_v1.py create` → `stage-start` / `stage-complete` per stage (REVIEW via
`review-start`, tests via `pipeline_test.py`) → `land` in DELIVER → REFLECT runs in the
daemon's background and completes itself.

## Verification

Before reporting the run, show evidence for each:

- [ ] **Run ended** — `pipeline_v1.py status --project <P> --run-id <R>` shows `completed`
      with `landing` (landed, or not_landed and its reason)
- [ ] **REPORT rendered by the engine** — `.artifacts/runs/<R>/REPORT.md` (never hand-edited)
- [ ] **Run Audit** — the five dimensions in `run_audit.json`; every BLOCK named with its reason
- [ ] **Push readiness** — `pipeline_v1.py push-status`: the fold of push-effect checks, not a model field
- [ ] **Tests from the runner** — BUILD/TEST exits passed on `pipeline_test.py` results only
