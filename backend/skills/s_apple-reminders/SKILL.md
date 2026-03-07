---
name: Apple Reminders
description: >
  Create, manage, and query Apple Reminders from the terminal. Syncs to iPhone/iPad/Mac.
  TRIGGER: "remind me", "add reminder", "create reminder", "my reminders", "to-do", "todo list", "what's due", "reminders".
  DO NOT USE: for calendar events (use Outlook Assistant), project management, or non-Apple reminder systems.
---

# Apple Reminders

**Why?** Create personal reminders that sync instantly to iPhone, iPad, and Mac -- all from the terminal. Perfect for quick "remind me to..." requests during work sessions.

**Platform:** macOS only. Requires the `remindctl` CLI tool.

---

## Quick Start

```
"Remind me to buy groceries tomorrow at 5pm"
-> remindctl add --title "Buy groceries" --due "2026-03-09 17:00" --list "Personal"

"What's due today?"
-> remindctl show today
```

---

## Setup

### Install remindctl

```bash
brew install steipete/tap/remindctl
```

On first run, macOS will prompt to grant Reminders access. The user must approve in:
**System Settings > Privacy & Security > Reminders**

### Verify Installation

```bash
remindctl show today
```

If this returns results or an empty list, setup is complete.

---

## Core Commands

### Viewing Reminders

```bash
# Today's reminders
remindctl show today

# Tomorrow
remindctl show tomorrow

# This week
remindctl show week

# Overdue items
remindctl show overdue

# Specific date
remindctl show --date "2026-03-15"

# All reminders in a list
remindctl show --list "Work"

# All lists
remindctl lists
```

### Creating Reminders

```bash
# Basic reminder (no due date)
remindctl add --title "Review PR #42"

# With due date
remindctl add --title "Submit expense report" --due "2026-03-10"

# With due date and time
remindctl add --title "Call dentist" --due "2026-03-09 09:00"

# Assign to specific list
remindctl add --title "Buy milk" --due "tomorrow" --list "Shopping"

# With notes
remindctl add --title "Quarterly review prep" --due "2026-03-15 14:00" --list "Work" --notes "Prepare slides and metrics"
```

**Date format flexibility:**
- ISO 8601: `2026-03-09`, `2026-03-09 17:00`
- Relative: `tomorrow`, `next monday`, `in 3 days`
- Natural: `march 15`, `next week`

### Completing Reminders

```bash
# Complete by ID
remindctl complete <id>
```

### Deleting Reminders

```bash
# Delete by ID
remindctl delete <id>
```

### Managing Lists

```bash
# Show all lists
remindctl lists

# Create a new list
remindctl lists create "Project Alpha"

# Delete a list
remindctl lists delete "Old List"
```

---

## Workflow

### Step 1: Parse User Request

Extract from natural language:
- **Title**: The reminder text
- **Due date/time**: When it's due (if mentioned)
- **List**: Which list (default to a sensible one)
- **Notes**: Any additional context

| User Says | Title | Due | List |
|-----------|-------|-----|------|
| "Remind me to call Mom tomorrow" | Call Mom | tomorrow | Personal |
| "Add a work reminder: review PRs by Friday 3pm" | Review PRs | friday 15:00 | Work |
| "Don't forget to water the plants every Monday" | Water the plants | next monday | Personal |
| "Todo: update documentation" | Update documentation | (none) | Work |

### Step 2: Determine List

If the user doesn't specify a list:
- Work-related keywords (PR, meeting, deploy, review, report) -> "Work" or relevant work list
- Personal keywords (buy, call, groceries, dentist, gym) -> "Personal"
- If unsure, ask or use the default list

First, check available lists:
```bash
remindctl lists
```

### Step 3: Create and Confirm

```bash
remindctl add --title "..." --due "..." --list "..."
```

Confirm to user:
```
Added reminder: "Call Mom"
Due: Tomorrow (March 9) at 5:00 PM
List: Personal
```

### Step 4: Offer Follow-ups

After creating:
- "Want me to add anything else?"
- "Should I check what else is due today?"

After viewing:
- "Want me to mark any of these done?"
- "Should I reschedule any overdue items?"

---

## Common Patterns

### Morning Check-in

When user asks "what's on my plate?" or "what's due?":

```bash
remindctl show today
remindctl show overdue
```

Present combined:
```
Today's Reminders (March 8):
- [ ] Submit expense report (Work) - due 3:00 PM
- [ ] Call dentist (Personal) - due 9:00 AM

Overdue:
- [ ] Review PR #42 (Work) - was due March 6
```

### Batch Creation

When user gives a list:
```
"Add these reminders:
- Buy groceries tomorrow
- Call bank Monday
- Review slides by Friday"
```

Create all three with appropriate dates, confirm as a batch.

### Quick Capture

For rapid "remind me" requests during conversation:
1. Create immediately with parsed details
2. Confirm in one line
3. Continue the original conversation

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "remindctl: command not found" | `brew install steipete/tap/remindctl` |
| "Permission denied" or no results | Grant Reminders access: System Settings > Privacy & Security > Reminders |
| List not found | Check available lists with `remindctl lists`, create if needed |
| Date parsing fails | Use ISO format: `2026-03-09 17:00` |
| Reminders not syncing to iPhone | Check iCloud sync in Settings > Apple ID > iCloud > Reminders |

---

## Quality Rules

- Always confirm what was created (title, due date, list)
- Show relative dates in confirmation ("Tomorrow" not just "2026-03-09")
- For overdue items, highlight how overdue they are
- Never delete reminders without explicit confirmation
- When showing lists, sort by due date (soonest first)
- Group by list when showing multiple reminders
- Use `--list` to keep things organized -- don't dump everything in the default list

---

## Testing

| Scenario | Expected Behavior |
|----------|-------------------|
| "Remind me to X tomorrow" | Creates with correct date, confirms |
| "What's due today?" | Shows today's + overdue items |
| "Mark reminder X as done" | Completes by ID, confirms |
| remindctl not installed | Provides brew install command |
| Permission denied | Guides to System Settings |
| No due date given | Creates without due date, notes it |
| Ambiguous list | Asks user or picks sensible default |
