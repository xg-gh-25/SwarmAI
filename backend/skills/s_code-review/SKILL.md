---
name: Code Review
description: >
  Structured code review for PRs, files, or diffs with actionable findings.
  TRIGGER: "review code", "code review", "review PR", "review this file", "check code quality", "review my changes", "PR review".
  DO NOT USE: for UI/frontend-specific review (use web-design-review) or for auto-fixing code (use simplify).
  SIBLINGS: web-design-review = UI/accessibility audit | simplify = auto-fix quality issues | code-review = structured findings report.
version: "1.0.0"
consumes_artifacts: [changeset, design_doc]
produces_artifact: review
tier: lazy
---
# Code Review

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

TRIGGER: "review code", "code review", "review PR", "review this file", "check code quality", "review my changes", "PR review"
DO NOT USE: for UI/frontend-specific review (use web-design-review) or for auto-fixing code (use simplify)
