---
name: job-manager
description: "Create, list, edit, pause, resume, and delete scheduled jobs in the Swarm Job System. Jobs run in the background via launchd — independently of chat sessions. Supports agent tasks (headless\
  \ Claude CLI with MCP tools), signal pipeline jobs, and script execution. User jobs live in user-jobs.yaml; system jobs are read-only.\n  TRIGGER: \"schedule\", \"every day\", \"every week\", \"recurring\"\
  .\n  NOT FOR: apple-reminders, outlook-assistant use cases."
input_type: text
output_type: text
tier: lazy
---
# Job Manager

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

TRIGGER: "schedule", "every day", "every week", "recurring", "scheduled jobs",
DO NOT USE: for one-time reminders (use apple-reminders), calendar events (use
