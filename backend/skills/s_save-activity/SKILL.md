---
name: Save Activity
description: >
  Extract key points from the current conversation into today's DailyActivity log.
  TRIGGER: "save activity", "save daily activity", "log today's activity".
  DO NOT USE: for persistent facts (use save-memory) or session handoffs (use save-context).
  On-demand counterpart to the automatic post-session extraction hook.
  SIBLINGS: save-memory = permanent facts/decisions -> MEMORY.md | save-activity = session log -> DailyActivity/ | save-context = handoff doc for next session.
---

## Save Activity Skill

Extract key points from the current conversation and append them to today's
DailyActivity file. This is the **on-demand** counterpart to the automatic
post-session-close extraction hook.

### When to Use

The user explicitly asks to save activity:
- "save activity"
- "save daily activity"
- "log today's activity"
- "write daily activity"

### How to Save

#### Step 1: Gather conversation context

Review the current conversation and identify:
- **Topics**: What subjects were discussed
- **Decisions**: What choices or recommendations were made
- **Files Modified**: What files were created, edited, or read
- **Open Questions**: What remains unresolved

#### Step 2: Write the DailyActivity entry

Write a structured entry to `Knowledge/DailyActivity/YYYY-MM-DD.md` (today's date)
using this format:

```markdown
## Session — HH:MM | session_id | Title

### What Happened
- Topic 1
- Topic 2

### Key Decisions
- Decision 1

### Files Modified
- path/to/file1
- path/to/file2

### Open Questions
- Question 1
```

If the file already exists, **append** the new session entry — do not overwrite.
Update the `sessions_count` in the YAML frontmatter.

If the file does not exist, create it with frontmatter:
```yaml
---
date: "YYYY-MM-DD"
sessions_count: 1
distilled: false
---
```

#### Step 3: Confirm to user

After writing, briefly confirm:
- "Saved activity to Knowledge/DailyActivity/2025-07-15.md"

Keep confirmation to one line.

### Rules

- **Append only** — never overwrite existing DailyActivity content
- **Be concise** — summarize, don't dump raw conversation
- **Use consistent format** — always include all 4 subsections
- **Deduplicate** — don't repeat topics or files already in today's entry
