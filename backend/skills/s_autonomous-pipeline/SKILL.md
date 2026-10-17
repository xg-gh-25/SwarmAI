---
name: autonomous-pipeline
pipeline_version: "6.0"  # 3 gates (Gate 0 EVALUATE family + Gate 1 Skeptic/SSA + Gate 2 Adversarial) · 9 stages · 2 execution modes (full one-shot / goal iterative). See INSTRUCTIONS.md Architecture.
description: "Orchestrate the full AIDLC Autonomous Pipeline from a one-sentence requirement to a PR-ready\
  \ delivery with TDD methodology. DDD drives judgment, SDD produces specs, TDD verifies delivery.\
  \ Stages: Evaluate, Think, Plan, Build, Review, Test, Deliver, Reflect. Checkpoints on BLOCK or context limits.\n\
  \  TRIGGER: \"run pipeline\", \"autonomous pipeline\", \"pipeline for\", \"full pipeline\".\n\
  \  NOT FOR: single stages (use evaluate, deep-research, code-review, qa, deliver directly)."
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

![SwarmAI Autonomous Pipeline — one-page panorama (9 stages · 3 gates · 2 modes · 6 profiles · 16 review/adversarial roles)](https://github.com/xg-gh-25/SwarmAI/raw/main/docs/assets/discussions/2026-10-14-pipeline-panorama.png)

> One requirement in → PR-ready delivery out, on a DDD layer the pipeline reads and writes. Walkthrough: [Discussion #111](https://github.com/xg-gh-25/SwarmAI/discussions/111) · design: [Autonomous-Pipeline-Design.md](../../../docs/Autonomous-Pipeline-Design.md).

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

TRIGGER: "run pipeline", "autonomous pipeline", "pipeline for", "full pipeline",
DO NOT USE: for a single stage (use the specific skill: evaluate, deep-research,

## Verification

Before marking the pipeline complete, show evidence for each:

- [ ] **REPORT.md generated** — saved to `.artifacts/runs/<RUN_ID>/REPORT.md`
- [ ] **All stage artifacts published** — every completed stage has a published artifact
- [ ] **Push-ready verdict recorded** — the binary PUSH-READY / NOT-PUSH-READY gate (deliver.md), not a numeric score
- [ ] **Decision log complete** — every non-trivial decision classified (mechanical/taste/judgment)
- [ ] **TDD cycle verified** — RED (tests fail) → GREEN (tests pass) → VERIFY (no regressions)
