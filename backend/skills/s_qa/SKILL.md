---
name: qa
description: "Diff-aware QA: scope from git changes, run tests, check UI changes, fix bugs in atomic commits, stop when fixes get risky. TRIGGER: \"run QA\", \"test my changes\", \"QA this\", \"check for regressions\". NOT FOR: code review."
consumes_artifacts:
- changeset
- design_doc
- review
produces_artifact: test_report
tier: lazy
---
# QA

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

TRIGGER: "run QA", "test my changes", "QA this", "check for regressions",
DO NOT USE: for writing new tests from scratch (just code them),
