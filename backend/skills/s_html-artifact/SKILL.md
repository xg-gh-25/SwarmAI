---
name: html-artifact
description: >
  Generate HTML artifact files for human-consumed outputs (reports, reviews, scorecards, comparisons).
  Dual-consumer protocol: agent self-use = markdown always; human consumption = HTML when content
  triggers escalation (>100 lines, multi-dimensional, spatial, interactive, shareable).
  TRIGGER: "generate report", "html report", "comparison table", "scorecard", "review report",
  "visual summary", "dashboard", "format as html", "make this visual".
  DO NOT USE: for chat conversation (stay inline markdown), agent self-use files (.context/,
  DDD docs, DailyActivity), or Slack output (mrkdwn only). Also NOT for full web pages or
  apps (use frontend-design skill).
  SIBLINGS: frontend-design = full web pages/apps | html-artifact = structured data artifacts for review/decision.
version: "1.0.0"
tier: lazy
platform: all
---
# HTML Artifact Generator

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

TRIGGER: "generate report", "html report", "comparison table", "scorecard", "review report", "visual summary", "dashboard", "format as html"

DO NOT USE: for inline chat responses (markdown), agent-consumed files, Slack, or full web apps.
