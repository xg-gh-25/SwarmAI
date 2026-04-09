---
name: Save Memory
description: >
  Write specific content to MEMORY.md for long-term persistence.
  TRIGGER: "remember this", "save to memory", "save the lessons", "persist this".
  DO NOT USE: for session handoffs (use save-context) or daily logs (use save-activity).
  Uses locked_write.py for concurrent write protection.
  SIBLINGS: save-memory = permanent facts/decisions -> MEMORY.md | save-activity = session log -> DailyActivity/ | save-context = handoff doc for next session.
---

# Save Memory

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

#### Step 3: Write using the Edit tool

Use the Edit tool to modify MEMORY.md directly. The file is at `.context/MEMORY.md`
relative to the workspace root.

**Always prefix entries with today's date** in `YYYY-MM-DD` format.

1. Read `.context/MEMORY.md` to find the target section
2. Use the Edit tool to prepend the new entry at the top of the section (after the `##` heading)
3. Format: `- YYYY-MM-DD: <content to save>`

Use prepend (add at top of section) so newest entries appear first.

If the target section doesn't exist in MEMORY.md, add it as a new `## <Section Name>`
heading at the end of the file.

#### Step 4: Confirm silently

After writing, briefly confirm to the user what was saved:
- "Saved to MEMORY.md under Key Decisions."
- "Remembered. Added to Lessons Learned."

Keep confirmation to one line. Don't repeat the content back.

### Rules

- **Always use the Edit tool** — never use `python3 locked_write.py` via Bash (crashes in PyInstaller bundles)
- **Always date-prefix** — every entry must start with `YYYY-MM-DD:` (today's date)
- **Newest first** — use `--prepend` so the most recent entries are at the top of each section
- **Append only** — never remove or replace existing MEMORY.md content
- **Be concise** — one line per entry, no raw conversation dumps
- **Don't duplicate** — check if the content is already in MEMORY.md before adding (match by content, ignore date)
- **MEMORY.md location** — always at `.context/MEMORY.md` (relative to workspace root)
- **Size management** — if MEMORY.md exceeds ~5KB (~100 entries), move the oldest entries from each section to `Knowledge/Archives/MEMORY-archive-YYYY-MM.md` before adding new ones. Keep MEMORY.md focused on the most recent and relevant items
- cache the store instance.
- overrides, channel context, and prompt builder wiring are al
- assigned on line 693. If before_id is not None, the if befor
- cache the store instance.
- overrides, channel context, and prompt builder wiring are al
- assigned on line 693. If before_id is not None, the if befor
- cache the store instance.
- overrides, channel context, and prompt builder wiring are al
- assigned on line 693. If before_id is not None, the if befor
- cache the store instance.
- overrides, channel context, and prompt builder wiring are al
- assigned on line 693. If before_id is not None, the if befor
- cache the store instance.
- overrides, channel context, and prompt builder wiring are al
- assigned on line 693. If before_id is not None, the if befor
