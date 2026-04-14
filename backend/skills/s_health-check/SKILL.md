---
name: health-check
description: Post-build verification of SwarmAI critical assumptions — streaming, context files, MCP, DailyActivity pipeline.
trigger:
  - health check
  - verify build
  - smoke test
  - post-build check
  - is everything working
do_not_use:
  - ping endpoints (use curl directly)
  - monitoring dashboards
  - load testing
tier: lazy
---
# health-check

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.
