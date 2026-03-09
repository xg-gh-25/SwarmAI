---
name: Scheduler
description: >
  Create, manage, and list scheduled tasks using crontab and launchd (macOS). No external dependencies.
  TRIGGER: "schedule", "cron", "crontab", "run every", "recurring task", "automate daily", "launchd", "scheduled job", "run at".
  DO NOT USE: for one-time reminders (use apple-reminders) or calendar events (use outlook-assistant/google-workspace).
  SIBLINGS: apple-reminders = one-time/date reminders synced to Apple | scheduler = recurring system-level jobs.
version: "1.0.0"
---

# Scheduler

Create, manage, and inspect scheduled/recurring tasks on macOS using crontab and launchd. Zero external dependencies -- uses only built-in macOS tools.

## When to Use What

| Need | Tool | Why |
|------|------|-----|
| Simple recurring command | **crontab** | Easy syntax, universally understood |
| Persistent service / daemon | **launchd** | macOS-native, survives reboots, retry on failure |
| Run once at a specific time | **`at`** command | One-shot scheduled execution |
| Reminder with notification | apple-reminders skill | Syncs to iPhone, not a system job |

## Workflow

### Step 1: Understand the Request

Determine:
- **What** to run: command, script, or pipeline
- **When**: schedule (every hour, daily at 9am, every Monday, etc.)
- **How**: crontab (simple) or launchd (robust)
- **Logging**: where to capture output
- **Notifications**: should it alert on success/failure?

If the user says "schedule X every Y", default to crontab. If they need reliability (retry, keep-alive), use launchd.

### Step 2a: Crontab

#### Cron Syntax Reference

```
* * * * * command
| | | | |
| | | | +-- Day of week (0-7, 0=Sun, 7=Sun)
| | | +---- Month (1-12)
| | +------ Day of month (1-31)
| +-------- Hour (0-23)
+---------- Minute (0-59)
```

#### Common Patterns

| Schedule | Cron Expression |
|----------|----------------|
| Every minute | `* * * * *` |
| Every 5 minutes | `*/5 * * * *` |
| Every hour | `0 * * * *` |
| Daily at 9am | `0 9 * * *` |
| Daily at midnight | `0 0 * * *` |
| Every Monday at 8am | `0 8 * * 1` |
| Weekdays at 6pm | `0 18 * * 1-5` |
| First of month at noon | `0 12 1 * *` |
| Every 15 min, business hours | `*/15 9-17 * * 1-5` |

#### Managing Crontab

```bash
# List current crontab
crontab -l

# Edit crontab (opens in editor)
crontab -e

# Replace entire crontab from file
crontab /path/to/crontab-file

# Remove all crontab entries (DANGEROUS - always confirm with user)
crontab -r
```

#### Safe Crontab Modification

NEVER use `crontab -r`. To add/modify entries safely:

```bash
# 1. Export current crontab
crontab -l > "$TMPDIR/crontab_backup_$(date +%Y%m%d_%H%M%S)"
crontab -l > "$TMPDIR/crontab_current"

# 2. Append new entry
echo '0 9 * * * /path/to/script.sh >> /tmp/script.log 2>&1' >> "$TMPDIR/crontab_current"

# 3. Install updated crontab
crontab "$TMPDIR/crontab_current"

# 4. Verify
crontab -l
```

To remove a specific entry:
```bash
crontab -l > "$TMPDIR/crontab_current"
grep -v 'pattern-to-remove' "$TMPDIR/crontab_current" > "$TMPDIR/crontab_new"
crontab "$TMPDIR/crontab_new"
```

#### Crontab Best Practices

1. **Always use full paths** -- cron has minimal PATH
   ```bash
   # Bad
   0 9 * * * python3 backup.py
   # Good
   0 9 * * * /opt/homebrew/bin/python3 /Users/gawan/scripts/backup.py
   ```

2. **Redirect output** -- otherwise cron mails it (which nobody reads)
   ```bash
   0 9 * * * /path/to/script.sh >> /tmp/script.log 2>&1
   ```

3. **Add a comment** for identification
   ```bash
   # SwarmAI: Daily backup at 9am (created 2026-03-09)
   0 9 * * * /path/to/backup.sh >> /tmp/backup.log 2>&1
   ```

4. **Set environment** if needed
   ```bash
   PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
   SHELL=/bin/bash
   0 9 * * * /path/to/script.sh
   ```

### Step 2b: launchd (macOS Native)

For more robust scheduling, use launchd with a plist file.

#### Create a Launch Agent

```bash
# Location for user-level agents
~/Library/LaunchAgents/

# Naming convention
com.swarm-ai.{task-name}.plist
```

#### Plist Template

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.swarm-ai.task-name</string>

    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/python3</string>
        <string>/Users/gawan/scripts/task.py</string>
    </array>

    <!-- Run daily at 9:00 AM -->
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <!-- OR run every 3600 seconds (1 hour) -->
    <!-- <key>StartInterval</key>
    <integer>3600</integer> -->

    <key>StandardOutPath</key>
    <string>/tmp/task-name.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/task-name.stderr.log</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

#### Managing Launch Agents

```bash
# Load (enable) an agent
launchctl load ~/Library/LaunchAgents/com.swarm-ai.task-name.plist

# Unload (disable) an agent
launchctl unload ~/Library/LaunchAgents/com.swarm-ai.task-name.plist

# Start immediately (for testing)
launchctl start com.swarm-ai.task-name

# Stop
launchctl stop com.swarm-ai.task-name

# List all loaded agents
launchctl list | grep swarm

# Check status of specific agent
launchctl list com.swarm-ai.task-name
```

#### launchd Schedule Options

| Key | Example | Meaning |
|-----|---------|---------|
| `StartInterval` | `3600` | Every N seconds |
| `StartCalendarInterval.Hour` | `9` | At hour 9 |
| `StartCalendarInterval.Minute` | `30` | At minute 30 |
| `StartCalendarInterval.Weekday` | `1` | On Monday (0=Sun) |
| `StartCalendarInterval.Day` | `1` | On 1st of month |
| Multiple intervals | Array of dicts | Multiple schedules |

### Step 2c: One-Shot with `at`

For "run this once at 3pm":

```bash
# Schedule a one-time job
echo "/path/to/script.sh" | at 3:00 PM

# Schedule for a specific date
echo "/path/to/script.sh" | at 3:00 PM March 15

# List pending at jobs
atq

# Remove an at job
atrm {job-number}
```

Note: `atrun` must be enabled on macOS:
```bash
sudo launchctl load -w /System/Library/LaunchDaemons/com.apple.atrun.plist
```

### Step 3: Add Notifications (Optional)

For macOS notifications on job completion:

```bash
# Add to the end of any scheduled script
osascript -e 'display notification "Backup completed successfully" with title "SwarmAI Scheduler"'

# For failures
command || osascript -e 'display notification "Backup FAILED - check logs" with title "SwarmAI Scheduler" sound name "Basso"'
```

### Step 4: Verify & Report

After creating a scheduled task, always:

1. **Show the user** what was created (full cron line or plist)
2. **Test it** by running the command once manually
3. **Confirm scheduling** with `crontab -l` or `launchctl list`
4. **Note the log location** so user knows where to check output

Present as:

```markdown
## Scheduled Task Created

| Property | Value |
|----------|-------|
| Task | Daily backup |
| Schedule | Every day at 9:00 AM CST |
| Method | crontab |
| Command | `/opt/homebrew/bin/python3 /Users/gawan/scripts/backup.py` |
| Log | `/tmp/backup.log` |
| Created | 2026-03-09 |

Next run: Tomorrow at 9:00 AM CST

To check: `crontab -l | grep backup`
To remove: `crontab -l | grep -v backup | crontab -`
```

---

## List All Scheduled Tasks

When user asks "what's scheduled" or "show my cron jobs":

```bash
# Crontab entries
echo "=== Crontab ==="
crontab -l 2>/dev/null || echo "No crontab entries"

# LaunchAgents (user level)
echo "=== Launch Agents ==="
ls ~/Library/LaunchAgents/ 2>/dev/null

# SwarmAI-specific agents
echo "=== SwarmAI Agents ==="
ls ~/Library/LaunchAgents/com.swarm-ai.* 2>/dev/null

# At jobs
echo "=== At Jobs ==="
atq 2>/dev/null
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Cron job not running | Check: full paths? Output redirected? `crontab -l` shows it? |
| launchd job not starting | `launchctl list {label}` -- check exit status; verify plist syntax with `plutil` |
| Permission denied | Ensure script is executable: `chmod +x /path/to/script.sh` |
| Wrong timezone | Cron uses system timezone; verify with `date +%Z` |
| Job runs but no output | Add `>> /tmp/job.log 2>&1` to capture stdout+stderr |
| macOS full disk access | Some paths need FDA -- add Terminal/script to System Settings > Privacy |
| plist syntax error | Validate: `plutil -lint ~/Library/LaunchAgents/com.swarm-ai.task.plist` |

## Safety Rules

- NEVER use `crontab -r` without explicit user confirmation
- Always backup current crontab before modifying
- Always use full paths in scheduled commands
- Always redirect output to a log file
- Always tag entries with `# SwarmAI:` comment for identification
- Test the command manually before scheduling
- Zero dependencies: uses only crontab, launchctl, at, osascript (all built into macOS)
