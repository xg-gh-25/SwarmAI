---
name: golden-case
description: "Add, validate, and promote SwarmAI eval golden cases through a 4-gate quality check (schema/duplicate/non-vacuous/privacy). The ONLY sanctioned path to modify the golden set — keeps cases from being 太随便. ADD lands private by default; PROMOTE runs the privacy gate before a case ships public.\n  TRIGGER: \"add golden case\", \"new eval case\", \"promote case to public\", \"validate golden set\".\n  NOT FOR: running eval (use eval_runner), s_self-evolution governance rules."
tier: lazy
platform: desktop
project_scope: SwarmAI
---

# Golden Case

The standard intake for SwarmAI eval golden cases. Read INSTRUCTIONS.md before proceeding.

ABORT if active project != SwarmAI: "s_golden-case manages the SwarmAI eval golden set only."

Cases are split public (tracked, shippable) / private (gitignored, instance-specific).
Every case passes a 4-gate validator before entering the corpus. Direct edits to
golden_set.yaml are discouraged — they bypass the gates that keep the set trustworthy.
