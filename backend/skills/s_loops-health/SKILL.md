---
name: loops-health
disable-model-invocation: true
description: "Self-maintenance scan of 7 dimensions: auto-fix mechanical issues, report Found/Fixed/Pending, escalate only judgment calls. TRIGGER: \"loops health\", \"self-maintenance scan\", \"system self-check\", \"自检\"."
trigger:
  - loops health
  - self-loops health
  - are loops healthy
  - check self-evolution
  - memory health
  - knowledge health
  - context health
  - brain health
do_not_use:
  - post-build verification (use health-check)
  - runtime system health CPU/RAM (use system-health)
  - SwarmAI backend process health (use health-check)
tier: lazy
platform: all
---
# loops-health

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.
