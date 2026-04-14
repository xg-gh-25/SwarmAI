---
name: Outlook Assistant
description: >
  Manage Outlook inbox: triage, cleanup, send, and organization via aws-outlook-mcp server.
  TRIGGER: "check email", "email triage", "clean inbox", "email summary", "outlook", "calendar",
  "send email", "reply email", "forward email".
  DO NOT USE: for non-Outlook email (use google-workspace), Apple Reminders, or general scheduling.
  SIBLINGS: google-workspace = Gmail + Google Calendar | outlook-assistant = Outlook email + calendar.
tier: always
---
# Outlook Assistant

Outlook inbox management via the `aws-outlook-mcp` MCP server. Triage, send, cleanup, and organize emails.

## MCP Server Binding

This skill uses the **aws-outlook-mcp** MCP server. Tools are available as `mcp__aws-outlook-mcp__<tool_name>`.

**Before calling any tool**, use `ToolSearch` to discover the exact tool names:
```
ToolSearch("aws-outlook-mcp email")    → find email tools
ToolSearch("aws-outlook-mcp calendar") → find calendar tools
```

The tool names below are the **short names** — always prefix with `mcp__aws-outlook-mcp__` or discover via ToolSearch.

---

## Tool Reference

### Email Operations

| Operation | Tool (short name) | Notes |
|-----------|-------------------|-------|
| Search/List emails | `unified_email_search` | Supports folders, date_filter, sender, is_unread, has_attachment, is_flagged, category |
| Read email content | `get_email_content` | Pass message_id, set content_raw=true for HTML |
| Send email | `send_email_as_html` | Body must be HTML formatted |
| Reply to email | `reply_to_email_as_html` | Pass message_id and reply_text (HTML) |
| Forward email | `forward_email_as_html` | Pass message_id, to, and optional additional_text |
| Create draft | `create_draft_as_html` | Creates in Drafts folder |
| Delete email | `delete_email` | Moves to Deleted Items. Accepts single ID or array |
| Move email | `move_email` | Move to folder by name. Accepts single ID or array |
| Mark as read | `mark_as_read` | Accepts single ID or array |
| Mark as unread | `mark_as_unread` | Accepts single ID or array |
| Save attachments | `save_attachments` | Provide message_id and save_path |

### Category & Analytics

| Operation | Tool (short name) | Notes |
|-----------|-------------------|-------|
| Assign category | `assign_category` | Creates category if doesn't exist |
| Clear category | `clear_category` | Remove specific or all categories |
| Mailbox overview | `mailbox_overview` | Total counts, folder stats |
| Folder analytics | `folder_analytics` | Per-folder statistics |
| Sender analytics | `sender_analytics` | Top senders with per-folder breakdown |
| Volume analytics | `email_volume_analytics` | Volume by day/week/month |
| Custom SQL query | `outlook_database_query` | Advanced queries |

### Calendar Operations

| Operation | Tool (short name) | Notes |
|-----------|-------------------|-------|
| View events / availability | `calendar_availability` | **Use this instead of calendar_view** — returns events with subject, busyType, times. Pass user emails + date range. |
| Search events | `calendar_search` | Supports "today", dates, or text query |
| Create event | `calendar_meeting` | Required: subject, start_time, end_time |
| Book room | `calendar_room_booking` | Find and book meeting rooms |
| Shared calendars | `calendar_shared_list` | View shared/delegated calendars |

> ⚠️ **Do NOT use `calendar_view`** — it has a known bug (returns empty results due to OWA API request format issue). Always use `calendar_availability` for viewing events.

---

## Core Principles

1. **Proactive** — After every action, suggest the next step. But never execute state-changing ops without explicit approval.
2. **Impact first** — Surface ACTION-needed emails before FYI. PRs to review, replies needed, deadlines > receipts.
3. **Batch, don't itemize** — Group similar items. Don't make user review 50 emails individually.
4. **Respect time** — Old emails (>30 days) get summaries, not individual review.

---

## Operating Modes

| User Says | Mode | Behavior |
|-----------|------|----------|
| "Check my emails" | Quick | Summary + top recommendations |
| "Clean up my inbox" | Quick | Find deletables → confirm → delete |
| "What's in my inbox?" | Deep | Full classification + grouped review |
| "What's important?" | Deep | Action Required items only |

---

## Confirmation Rules

**Always confirm before:**
- `delete_email`, `move_email`
- `send_email_as_html`, `reply_to_email_as_html`, `forward_email_as_html`
- `create_calendar_event`, `delete_calendar_event`
- `mark_as_read` (if batch 3+)

**No confirmation needed:**
- `unified_email_search`, `get_email_content`
- `mailbox_overview`, `folder_analytics`, `sender_analytics`
- `get_calendars`, `get_calendar_events`, `search_calendar_events`

---

## Batch Deletion Protocol

| Batch Size | Format |
|------------|--------|
| 1-5 | List each (sender + subject), inline confirmation |
| 6-20 | Categorized summary + examples |
| 21-50 | Category counts + sample |
| 51+ | Split into batches of 50 max |

For 6+ emails, present:
```
## Deletion Proposal ([N] emails)
- Category A: N emails (sample: sender, subject)
- Category B: N emails (sample: sender, subject)
Risk: N unread items. Confidence: High/Medium.
Confirm? (yes / list all)
```

---

## Deletion Tracking

Before deleting, log to `data/deletion-log.json` (relative to skill dir) using:

```bash
.claude/skills/s_outlook-assistant/scripts/deletion-log.sh add --id <id> --subject "<subject>" --sender "<sender>" --folder "<folder>"
.claude/skills/s_outlook-assistant/scripts/deletion-log.sh view
.claude/skills/s_outlook-assistant/scripts/deletion-log.sh search "<keyword>"
```

To restore: search Deleted Items via `unified_email_search`, then `move_email` back.

---

## User Preferences

Stored at `data/user-preferences.md` (relative to skill dir). Manage via:

```bash
.claude/skills/s_outlook-assistant/scripts/preferences.sh view
.claude/skills/s_outlook-assistant/scripts/preferences.sh set --section sender --entry "no-reply@example.com - Always suggest cleanup"
.claude/skills/s_outlook-assistant/scripts/preferences.sh init
```

If no preferences file exists on first use, offer brief onboarding:
1. High-volume senders to auto-suggest cleanup
2. Cleanup aggressiveness (conservative / moderate / aggressive)

---

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Showing Deleted Items emails without noting folder | Always show folder field |
| Including Deleted Items in triage | Filter `folders` param |
| Listing 50 emails individually | Summarize by category |
| Deleting without proposal | Use batch deletion protocol |
| Auto-marking as read | User uses unread as to-do marker — confirm |
| Not logging deletions | Log before every delete |
