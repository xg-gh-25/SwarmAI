# Google Workspace

**Why?** Manage your entire Google Workspace from the terminal -- read/send Gmail, check Google Calendar, search Drive, edit Sheets, export Docs, and manage Tasks. All through the `gog` CLI with multi-account support and JSON output.

**CLI:** `gogcli` (command: `gog`), installed via `brew install steipete/tap/gogcli`.

---

## Quick Start

```
"Check my Gmail" -> gog gmail search 'newer_than:1d'
"What's on my Google Calendar today?" -> gog calendar list --from today --to today
"Search Google Drive for the Q1 report" -> gog drive search 'Q1 report'
"Send an email to alice@example.com" -> gog gmail send --to alice@example.com --subject "Hi" --body "..."
```

---

## Setup

### Step 1: OAuth Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or use existing)
3. Enable APIs: Gmail, Calendar, Drive, Sheets, Docs, Tasks, Contacts
4. Go to **Credentials** > **Create Credentials** > **OAuth client ID**
5. Application type: **Desktop app**
6. Download the JSON credentials file

### Step 2: Store Credentials

```bash
gog auth credentials set ~/Downloads/client_secret_*.json
```

### Step 3: Authorize Account

```bash
gog auth add you@gmail.com
```

This opens a browser for OAuth consent. Tokens are stored securely in the system keychain.

### Step 4: Set Default Account

```bash
export GOG_ACCOUNT=you@gmail.com
```

Add to shell profile (`~/.zshrc`) for persistence.

### Step 5: Verify

```bash
gog auth status
gog whoami
```

---

## Global Flags

| Flag | Description |
|---|---|
| `-a, --account` | Account email to use |
| `-j, --json` | JSON output (for scripting) |
| `-p, --plain` | TSV output (parseable) |
| `-n, --dry-run` | Preview without making changes |
| `-y, --force` | Skip confirmations |
| `--results-only` | JSON mode: emit only primary result |

---

## Gmail

### Read & Search

```bash
# Search recent emails
gog gmail search 'newer_than:1d'
gog gmail search 'newer_than:7d is:unread'
gog gmail search 'from:alice@example.com'
gog gmail search 'subject:invoice has:attachment'

# Search with limit
gog gmail search 'newer_than:7d' --max 20

# Get specific thread
gog gmail threads get <threadId>

# Get specific message
gog gmail messages get <messageId>

# Get message body
gog gmail messages get <messageId> --body

# List labels
gog gmail labels list
```

**Gmail search operators:**
| Operator | Example |
|---|---|
| `from:` | `from:alice@example.com` |
| `to:` | `to:bob@example.com` |
| `subject:` | `subject:meeting` |
| `newer_than:` | `newer_than:1d`, `newer_than:7d` |
| `older_than:` | `older_than:30d` |
| `is:unread` | Unread only |
| `is:starred` | Starred only |
| `has:attachment` | With attachments |
| `label:` | `label:work` |
| `in:` | `in:inbox`, `in:sent`, `in:trash` |

### Send

```bash
# Simple email
gog gmail send --to alice@example.com --subject "Meeting tomorrow" --body "Let's meet at 3pm."

# With CC/BCC
gog gmail send --to alice@example.com --cc bob@example.com --subject "Update" --body "FYI..."

# HTML body
gog gmail send --to alice@example.com --subject "Report" --body-html "<h1>Q1 Report</h1><p>See attached.</p>"

# Body from file (for long/formatted content)
gog gmail send --to alice@example.com --subject "Notes" --body-file ./notes.txt

# With attachments
gog gmail send --to alice@example.com --subject "Files" --body "Attached." --attach report.pdf --attach data.xlsx

# Reply to a thread
gog gmail send --to alice@example.com --subject "Re: Meeting" --body "Confirmed." --thread <threadId>
```

### Drafts

```bash
# List drafts
gog gmail drafts list

# Create a draft
gog gmail drafts create --to alice@example.com --subject "Draft" --body "TBD"

# Send a draft
gog gmail drafts send <draftId>

# Delete a draft
gog gmail drafts delete <draftId>
```

### Manage

```bash
# Mark as read (remove UNREAD label)
gog gmail messages modify <messageId> --remove-labels UNREAD

# Star a message
gog gmail messages modify <messageId> --add-labels STARRED

# Move to trash
gog gmail messages trash <messageId>

# Batch modify
gog gmail batch modify <id1> <id2> <id3> --remove-labels UNREAD
```

---

## Google Calendar

### View Events

```bash
# Today's events
gog calendar list --from today --to today

# This week
gog calendar list --from today --to "+7d"

# Specific date range
gog calendar list --from 2026-03-08 --to 2026-03-15

# With more details
gog calendar list --from today --to today --json
```

### Create Events

```bash
# Simple event
gog calendar create --title "Team standup" --start "2026-03-09 09:00" --end "2026-03-09 09:30"

# With location
gog calendar create --title "Lunch" --start "2026-03-09 12:00" --end "2026-03-09 13:00" --location "Cafe"

# With attendees
gog calendar create --title "Review" --start "2026-03-09 14:00" --end "2026-03-09 15:00" --attendee alice@example.com --attendee bob@example.com

# All-day event
gog calendar create --title "Holiday" --start "2026-03-10" --all-day

# With color (IDs 1-11)
gog calendar create --title "Urgent" --start "2026-03-09 09:00" --end "2026-03-09 10:00" --color 11
```

### Update & Delete

```bash
# Update event
gog calendar update <eventId> --title "Updated title" --start "2026-03-09 10:00"

# Delete event
gog calendar delete <eventId>

# View color palette
gog calendar colors
```

---

## Google Drive

### Browse & Search

```bash
# List files in root
gog drive ls

# List files in a folder
gog drive ls --parent <folderId>

# Search files
gog drive search "Q1 report"
gog drive search "type:spreadsheet budget"
gog drive search "modifiedTime > 2026-03-01"

# Get file metadata
gog drive get <fileId>
```

### Upload & Download

```bash
# Upload file
gog drive upload ./report.pdf

# Upload to specific folder
gog drive upload ./report.pdf --parent <folderId>

# Download file
gog drive download <fileId>

# Download to specific path
gog drive download <fileId> --out ./downloads/
```

### Organize

```bash
# Create folder
gog drive mkdir "Project Alpha"

# Move file
gog drive move <fileId> --parent <targetFolderId>

# Rename file
gog drive rename <fileId> "New Name"

# Share file
gog drive share <fileId> --email alice@example.com --role writer

# Delete (trash)
gog drive delete <fileId>
```

---

## Google Sheets

### Read Data

```bash
# Get sheet metadata
gog sheets get <spreadsheetId>

# Read a range
gog sheets range <spreadsheetId> "Sheet1!A1:D10"

# Read entire sheet
gog sheets range <spreadsheetId> "Sheet1"

# JSON output for parsing
gog sheets range <spreadsheetId> "Sheet1!A1:D10" --json
```

### Write Data

```bash
# Update cells
gog sheets update <spreadsheetId> "Sheet1!A1" --values '["Name","Email","Score"]'

# Append rows
gog sheets append <spreadsheetId> "Sheet1" --values '["Alice","alice@example.com","95"]'

# Clear a range
gog sheets clear <spreadsheetId> "Sheet1!A2:D100"
```

---

## Google Docs

### Read & Export

```bash
# View doc content in terminal
gog docs show <docId>

# Export as PDF
gog docs export <docId> --format pdf --out ./document.pdf

# Export as plain text
gog docs export <docId> --format txt --out ./document.txt

# Export as Markdown
gog docs export <docId> --format md --out ./document.md
```

---

## Google Tasks

```bash
# List task lists
gog tasks lists

# List tasks in a list
gog tasks list --list <listId>

# Create a task
gog tasks create --list <listId> --title "Review PR" --due "2026-03-10"

# Complete a task
gog tasks complete --list <listId> <taskId>

# Delete a task
gog tasks delete --list <listId> <taskId>
```

---

## Contacts

```bash
# List contacts
gog contacts list

# Search contacts
gog contacts list --query "alice"
```

---

## Workflow

### Step 1: Detect Auth Status

```bash
gog auth status
```

If not authenticated, guide user through setup (see Setup section).

### Step 2: Map User Request

| User Says | Service | Command |
|---|---|---|
| "Check my email" | Gmail | `gog gmail search 'newer_than:1d is:unread'` |
| "Send email to X" | Gmail | `gog gmail send --to X ...` |
| "What meetings do I have today?" | Calendar | `gog calendar list --from today --to today` |
| "Schedule a meeting" | Calendar | `gog calendar create ...` |
| "Find the report on Drive" | Drive | `gog drive search "report"` |
| "Download that file" | Drive | `gog drive download <id>` |
| "Read the spreadsheet" | Sheets | `gog sheets range <id> "Sheet1"` |
| "Add a row to the sheet" | Sheets | `gog sheets append <id> ...` |
| "Export the doc as PDF" | Docs | `gog docs export <id> --format pdf` |
| "What tasks are due?" | Tasks | `gog tasks list` |

### Step 3: Execute and Present

- For email: show sender, subject, date, snippet
- For calendar: show time, title, location, attendees
- For drive: show name, type, modified date, owner
- For sheets: show as formatted table
- Always use `--json` when parsing output programmatically

### Step 4: Confirm Before State Changes

Always confirm before:
- Sending emails
- Creating/deleting calendar events
- Deleting Drive files
- Modifying Sheets data

---

## Multi-Account

```bash
# List accounts
gog auth list

# Use specific account
gog gmail search 'newer_than:1d' --account work@company.com

# Set alias
gog auth alias set work work@company.com
gog gmail search 'newer_than:1d' --account work

# Set default
export GOG_ACCOUNT=work@company.com
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "gog: command not found" | `brew install steipete/tap/gogcli` |
| "no account configured" | `gog auth add you@gmail.com` |
| "token expired" | `gog auth add you@gmail.com` (re-authorize) |
| "insufficient permissions" | Re-authorize with needed scopes: `gog auth add you@gmail.com --services gmail,calendar,drive` |
| "credential file not found" | `gog auth credentials set ~/path/to/client_secret.json` |
| "API not enabled" | Enable API in Google Cloud Console for your project |
| JSON parsing issues | Use `--json --results-only` for clean output |
| Wrong account | Check `GOG_ACCOUNT` env var or use `--account` flag |

---

## Quality Rules

- Always check auth status before making API calls
- Use `--json` output when parsing results programmatically
- Prefer plain text for email body; use `--body-html` only when formatting needed
- Confirm before sending emails, creating events, or deleting files
- Show human-readable summaries, not raw JSON, to the user
- For Gmail search, use Gmail search operators for precision
- When listing many results, summarize and offer to show more
- For multi-account users, always confirm which account to use
- Never display or log OAuth tokens
- Use `--dry-run` when testing destructive operations

