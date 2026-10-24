---
name: golden-case
description: "Add, validate and promote SwarmAI eval golden cases through a 4-gate check (schema, duplicate, non-vacuous, privacy). TRIGGER: \"add golden case\", \"new eval case\", \"promote case to public\". NOT FOR: running eval."
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
