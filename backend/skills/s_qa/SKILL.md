---
name: qa
description: "Diff-aware structured QA: scope from git changes, run unit tests, visual test UI changes, fix bugs with atomic commits, halt when fixes get risky.\n  TRIGGER: \"run QA\", \"test my changes\"\
  , \"QA this\", \"check for regressions\".\n  NOT FOR: code-review use cases."
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
