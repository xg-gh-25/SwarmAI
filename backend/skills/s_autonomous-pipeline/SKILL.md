---
name: Autonomous Pipeline
description: >
  Orchestrate the full AIDLC Autonomous Pipeline from a one-sentence requirement
  to a PR-ready delivery with TDD methodology. DDD drives judgment (should we?),
  SDD produces specs (what exactly?), TDD verifies delivery (did we?).
  Drives stages sequentially: Evaluate, Think, Plan, Build (TDD red-green),
  Review, Test, Deliver (with report), Reflect. Classifies every decision as
  mechanical (auto-approve), taste (batch at delivery gate), or judgment
  (block for human). Checkpoints on L2 BLOCK, retry exhaustion, or context
  budget limits. Resumes from checkpoint in a fresh session.
  TRIGGER: "run pipeline", "autonomous pipeline", "pipeline for", "full pipeline",
  "build end-to-end", "execute pipeline", "resume pipeline", "continue pipeline",
  "pipeline status".
  DO NOT USE: for a single stage (use the specific skill: evaluate, deep-research,
  code-review, qa, deliver). Not for tasks without a clear requirement.
  SIBLINGS: evaluate = the GO/DEFER gate alone | qa = testing alone |
  deliver = packaging alone | pipeline = the full orchestrated sequence.
consumes_artifacts: [evaluation, research, alternatives, design_doc, changeset, review, test_report]
produces_artifact: [evaluation, research, design_doc, changeset, review, test_report, delivery, checkpoint]
tier: lazy
---
# Autonomous Pipeline

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

TRIGGER: "run pipeline", "autonomous pipeline", "pipeline for", "full pipeline",
DO NOT USE: for a single stage (use the specific skill: evaluate, deep-research,
