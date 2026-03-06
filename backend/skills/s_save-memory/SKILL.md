---
name: Save Memory
description: >
  Write specific content to MEMORY.md when the user says "remember this",
  "save to memory", "save the lessons", or similar. Uses locked_write.py
  for concurrent write protection.
---

## Save Memory Skill

Write user-specified content directly to MEMORY.md. This is the **only user-triggered**
memory operation — all other memory management (DailyActivity, distillation, archiving)
is fully automatic.

### When to Use

The user explicitly asks you to remember something:
- "remember this"
- "save to memory"
- "remember the key decisions"
- "save the lessons learned"
- "persist this to memory"

### How to Save

#### Step 1: Determine what to save

Extract the specific content the user wants remembered. Be concise — one line per
decision/lesson/fact. Don't dump raw conversation.

#### Step 2: Determine the target section

Map the content to the appropriate MEMORY.md section:

| Content type | Target section |
|---|---|
| Decisions, choices | `Key Decisions` |
| Lessons, debugging insights | `Lessons Learned` |
| Current work status | `Recent Context` |
| Unfinished tasks, pending items | `Open Threads` |
| General / unclear | `Key Decisions` (default) |

#### Step 3: Write using locked_write.py

Use the locked write script for concurrent-safe MEMORY.md modification:

```bash
python backend/scripts/locked_write.py \
  --file .context/MEMORY.md \
  --section "Key Decisions" \
  --append "- <content to save>"
```

Replace the section name and content as appropriate.

If the target section doesn't exist in MEMORY.md, the script automatically
appends under a `## Distilled` fallback section.

#### Step 4: Confirm silently

After writing, briefly confirm to the user what was saved:
- "Saved to MEMORY.md under Key Decisions."
- "Remembered. Added to Lessons Learned."

Keep confirmation to one line. Don't repeat the content back.

### Rules

- **Always use `locked_write.py`** — never write MEMORY.md directly with file tools
- **Append only** — never remove or replace existing MEMORY.md content
- **Be concise** — one line per entry, no raw conversation dumps
- **Don't duplicate** — check if the content is already in MEMORY.md before adding
- **MEMORY.md location** — always at `.context/MEMORY.md` (relative to workspace root)
