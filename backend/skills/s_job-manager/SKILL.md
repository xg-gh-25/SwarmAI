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
---

# Job Manager Skill

Manage scheduled background jobs in the Swarm Job System. Jobs run hourly via
launchd (`com.swarm.signal-pipeline.plist`) — the scheduler checks which jobs are
due and executes them independently of any chat session.

## Tool

```bash
# All commands output JSON to stdout
python3 ~/.swarm-ai/SwarmWS/Services/swarm-jobs/job_manager.py <command> [options]
```

Where `~/.swarm-ai/SwarmWS` = `~/.swarm-ai/SwarmWS`

## Commands

### List all jobs
```bash
python3 ~/.swarm-ai/SwarmWS/Services/swarm-jobs/job_manager.py list
```
Returns array of all jobs (system + user) with status, last run, failure count.

### Create a user job
```bash
python3 ~/.swarm-ai/SwarmWS/Services/swarm-jobs/job_manager.py create --json '{
  "name": "Morning Inbox Summary",
  "type": "agent_task",
  "schedule": "0 1 * * 1-5",
  "prompt": "Check my Outlook inbox for unread emails from the last 12 hours. Summarize the top 5 by importance.",
  "config": {},
  "safety": {
    "max_budget_usd": 0.20,
    "timeout_seconds": 180,
    "allowed_tools": ["mcp__aws-outlook-mcp__email_inbox", "mcp__aws-outlook-mcp__email_read"]
  }
}'
```

### Edit a user job
```bash
python3 ~/.swarm-ai/SwarmWS/Services/swarm-jobs/job_manager.py edit uj-morning-inbox --json '{
  "schedule": "0 0 * * 1-5",
  "prompt": "Updated prompt here"
}'
```

### Pause / Resume / Delete
```bash
python3 ~/.swarm-ai/SwarmWS/Services/swarm-jobs/job_manager.py pause uj-morning-inbox
python3 ~/.swarm-ai/SwarmWS/Services/swarm-jobs/job_manager.py resume uj-morning-inbox
python3 ~/.swarm-ai/SwarmWS/Services/swarm-jobs/job_manager.py delete uj-morning-inbox
```

### Show full details
```bash
python3 ~/.swarm-ai/SwarmWS/Services/swarm-jobs/job_manager.py show signal-fetch
```

### Validate a cron expression
```bash
python3 ~/.swarm-ai/SwarmWS/Services/swarm-jobs/job_manager.py validate-cron "0 9 * * 1-5"
```

### Force-run a job immediately
```bash
python3 ~/.swarm-ai/SwarmWS/Services/swarm-jobs/scheduler.py --run-now JOB_ID
```

## Job Types

| Type | What It Does | Key Config |
|------|-------------|------------|
| `agent_task` | Headless Claude CLI with MCP tools | `prompt`, `safety.allowed_tools`, `safety.max_budget_usd` |
| `script` | Run a shell command or Python script | `config.command` or `config.script` |
| `signal_fetch` | Fetch from RSS/GitHub/HN feeds | System only (read-only) |
| `signal_digest` | LLM-score fetched signals | System only (read-only) |
| `maintenance` | Prune caches, clean state | System only (read-only) |

## Parsing User Intent

When a user requests a scheduled job, extract these fields:

1. **name** — Short descriptive name (e.g., "Morning Inbox Summary")
2. **schedule** — Convert natural language to cron:
   - "every morning at 9am" → `"0 1 * * *"` (9am ICT = 1:00 UTC)
   - "weekdays at 8am" → `"0 0 * * 1-5"` (8am ICT = 0:00 UTC)
   - "every Monday at 10am" → `"0 2 * * 1"` (10am ICT = 2:00 UTC)
   - "every hour" → `"0 * * * *"`
   - "twice daily" → `"0 2,14 * * *"` (10am, 10pm ICT)
   - "every Friday at 5pm" → `"0 9 * * 5"` (5pm ICT = 9:00 UTC)
3. **type** — Usually `agent_task` for natural-language tasks, `script` for commands
4. **prompt** — The task description (for `agent_task`)
5. **safety.allowed_tools** — Which MCP tools the job needs

### CRITICAL: Timezone Conversion

The user's timezone is **ICT (UTC+8)**. All cron schedules run in **UTC**.
Always convert: `user_hour - 8 = UTC_hour` (wrap around midnight).

Examples:
- User says "9am" → `1` in cron (hour field)
- User says "8am" → `0` in cron
- User says "6pm" → `10` in cron
- User says "midnight" → `16` in cron (previous day in UTC)

### Common Tool Mappings

| User says | allowed_tools |
|-----------|--------------|
| "check my email/inbox" | `["mcp__aws-outlook-mcp__email_inbox", "mcp__aws-outlook-mcp__email_read", "mcp__aws-outlook-mcp__email_search"]` |
| "send me a Slack message" | `["mcp__slack-mcp__post_message", "mcp__slack-mcp__open_dm_channel"]` |
| "check my calendar" | `["mcp__aws-outlook-mcp__calendar_view", "mcp__aws-outlook-mcp__calendar_search"]` |
| "search the web" | `["WebFetch"]` |
| "read/write files" | (no restriction needed — CLI has SwarmWS access by default) |

## Workflow

### Creating a job from user request

1. Parse user intent into name, schedule, type, prompt, tools
2. **Validate the cron**: run `validate-cron` first
3. **Confirm with user** before creating:
   - Show: name, schedule (in user's local time + UTC), prompt summary, tools, budget
   - Ask: "Create this job?" (don't auto-create — this is an external action)
4. On confirmation: run `create --json '{...}'`
5. Show result: job ID, next run time
6. Suggest: "Run it now to test?" → `scheduler.py --run-now JOB_ID`

### Listing jobs

1. Run `list`
2. Format as a readable table with status indicators
3. Group by category (system vs user)

### Editing a job

1. Show current job details first (`show JOB_ID`)
2. Confirm changes with user
3. Run `edit JOB_ID --json '{...}'` with only changed fields

## Safety Rules

- **Always confirm** before create/edit/delete — these are external actions
- System jobs (`signal-fetch`, `signal-digest`, `weekly-maintenance`) are **read-only** from this skill
- Default budget for agent_task: `$0.20` per run (configurable)
- Default timeout: `180s` (configurable)
- Jobs are disabled by default if user says "set up" or "configure" (enable after testing)
- Suggest `--run-now` after creation so user can validate before the next scheduled run

## Error Handling

All commands return JSON. Check for `"error"` key in response:
```json
{"error": "User job 'uj-foo' not found"}
```

Common errors:
- Invalid cron expression → show the validation error, help user fix
- System job modification → explain it's read-only, suggest editing jobs.yaml directly
- Duplicate job ID → auto-resolved with timestamp suffix
