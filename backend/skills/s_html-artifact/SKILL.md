---
name: html-artifact
description: "Generate HTML artifact files for human-consumed outputs (reports, reviews, scorecards, comparisons). Dual-consumer protocol: agent self-use = markdown always; human consumption = HTML when\
  \ content triggers escalation (>100 lines, multi-dimensional, spatial, interactive, shareable).\n  TRIGGER: \"generate report\", \"html report\", \"comparison table\", \"scorecard\".\n  NOT FOR: files,\
  \ frontend-design use cases."
version: 1.0.0
tier: lazy
platform: all
---
# HTML Artifact Generator

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

TRIGGER: "generate report", "html report", "comparison table", "scorecard", "review report", "visual summary", "dashboard", "format as html"

DO NOT USE: for inline chat responses (markdown), agent-consumed files, Slack, or full web apps.
