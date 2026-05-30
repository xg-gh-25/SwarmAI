---
name: system-health
description: "Full system health report: desktop overview, worst offenders, SwarmAI resource details, and actionable suggestions. Outputs a structured report in the chat window.\n  TRIGGER: \"system health\"\
  , \"mac health\", \"linux health\", \"battery check\".\n  NOT FOR: cloudwatch-log-analysis, health-check, bsc-security-scanner use cases."
tier: lazy
platform: macos
---
# system-health

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

TRIGGER: "system health", "mac health", "linux health", "battery check", "ram usage",
DO NOT USE: for AWS resource monitoring or CloudWatch logs (use cloudwatch-log-analysis),
