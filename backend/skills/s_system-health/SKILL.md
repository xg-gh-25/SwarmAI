---
name: system-health
description: >
  Full system health report: desktop overview, worst offenders, SwarmAI resource details,
  and actionable suggestions. Outputs a structured report in the chat window.
  TRIGGER: "system health", "mac health", "linux health", "battery check", "ram usage",
  "what's eating memory", "mac running slow", "system running slow", "check my system",
  "why is my laptop slow", "health report", "resource check".
  DO NOT USE: for AWS resource monitoring or CloudWatch logs (use cloudwatch-log-analysis),
  SwarmAI app health (use health-check skill), or security scanning (use bsc-security-scanner).
tier: lazy
platform: macos
---
# system-health

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

TRIGGER: "system health", "mac health", "linux health", "battery check", "ram usage",
DO NOT USE: for AWS resource monitoring or CloudWatch logs (use cloudwatch-log-analysis),
