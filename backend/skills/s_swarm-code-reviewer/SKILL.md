---
name: swarm-code-reviewer
description: "Review an Amazon CRUX code review (CR) as a Principal SDE — read the CR, load package standards, follow SIM/design refs, review across dimensions, verify-and-exceed AutoSDE, then report a structured verdict and (behind human gates) post comments or approve.\n  TRIGGER: \"review CR\", \"review this CR\", \"review CR-1234567\", \"code review CR-\", \"审这个 CR\", \"以 Principal SDE 视角审\", a code.amazon.com/reviews/CR-xxx link.\n  NOT FOR: local git/PR review (use code-review), UI review (use web-design-review)."
version: 1.0.0
tier: lazy
---
# Swarm Code Reviewer

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

Principal-SDE review of an Amazon CRUX code review (CR). Reads the CR via
builder-mcp (CriticService / ReadInternalWebsites), loads the package's own
standards, follows references (SIM / design docs), reviews the changed code
across dimensions, **verifies and goes beyond AutoSDE** (never restates it),
and outputs a structured review ending APPROVED / BLOCKED.

Posting comments and approving are **external actions behind human gates** —
comments are drafted then published only on your confirmation; approve is never
automatic and requires an explicit per-CR command.

TRIGGER (subset of the frontmatter description above — that is the SDK-extracted source of truth): "review CR", "review this CR", "review CR-1234567", a `code.amazon.com/reviews/CR-xxx` link, "审这个 CR", "以 Principal SDE 视角审"
DO NOT USE: for local git working-tree / GitHub PR review (use `code-review`), for UI/design review (use `web-design-review`), or for generating a CR (use `cr` CLI)

Scope: Phase 1 = manual, single-CR, triggered by a CR link. Batch ("review all
CRs assigned to me today") and scheduled daily review are future phases — see the
"Future (not built)" section in INSTRUCTIONS.md.
