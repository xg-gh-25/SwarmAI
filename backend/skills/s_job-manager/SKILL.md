---
name: Job Manager
description: >
  Create, list, edit, pause, resume, and delete scheduled jobs in the Swarm Job System.
  Jobs run in the background via launchd — independently of chat sessions.
  Supports agent tasks (headless Claude CLI with MCP tools), signal pipeline jobs,
  and script execution. User jobs live in user-jobs.yaml; system jobs are read-only.
  TRIGGER: "schedule", "every day", "every week", "recurring", "scheduled jobs",
  "my jobs", "pause job", "cancel job", "delete job", "run job now", "list jobs",
  "check my inbox every morning", "weekly summary", "monitor".
  DO NOT USE: for one-time reminders (use apple-reminders), calendar events (use
  outlook-assistant), or Apple Reminders. This is for recurring background automation.
  SIBLINGS: apple-reminders = one-time reminders synced to Apple |
  outlook-assistant = Outlook calendar + email | radar-todo = work packet tracking.
input_type: text
output_type: text
tier: lazy
---
# Job Manager

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

TRIGGER: "schedule", "every day", "every week", "recurring", "scheduled jobs",
DO NOT USE: for one-time reminders (use apple-reminders), calendar events (use
