---
name: swarmai-monthly-report
description: >
  Generate SwarmAI Monthly Report — MBR-style health & progress covering all 12
  Core Engine subsystems (Memory, Context, Pipeline, DDD, Evolution, Health, Jobs,
  Code Intel, Skills, Pollinate, Sessions, Git). Outputs to Knowledge/Reports/.
  TRIGGER: "monthly report", "月报", "SwarmAI health", "system report", "generate monthly",
  "how did we do this month", "monthly recap", "monthly summary".
  DO NOT USE: for DDD weekly report (use ddd-weekly-report job), for CMHK reports
  (use cmhk-monthly-report), for daily briefings (automatic via proactive intelligence).
  SIBLINGS: s_cmhk-monthly-report = CMHK revenue report | ddd-weekly-report = DDD cultivation weekly.
tier: lazy
platform: all
project_scope: SwarmAI
---

# SwarmAI Monthly Report

Generate a comprehensive monthly health & progress report for the SwarmAI system.

Read INSTRUCTIONS.md before proceeding.
