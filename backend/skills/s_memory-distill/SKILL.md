---
name: Memory Distill
description: >
  Distill unprocessed DailyActivity files into curated MEMORY.md entries.
  TRIGGER: auto-triggers when >3 unprocessed DailyActivity files detected at session start.
  DO NOT USE: manually — this runs silently and automatically.
tier: always
---
# Memory Distill

Distill raw DailyActivity logs into curated MEMORY.md entries. This skill runs
automatically and silently — never announce, never ask permission.

### 1. Detection

Scan `Knowledge/DailyActivity/*.md` for unprocessed files:

1. List all `.md` files in `Knowledge/DailyActivity/`
2. For each file, read the YAML frontmatter (delimited by `---` lines at the top)
3. A file is **unprocessed** if it has no frontmatter OR lacks `distilled: true`
4. Count unprocessed files
5. **If count ≤ 3, exit silently** — not enough material to distill

Do not announce the scan or its results.

### 2. Extraction

For each unprocessed DailyActivity file, extract:

- **Key decisions** — Architecture choices, tool selections, approach changes
- **Lessons learned** — What worked, what didn't, debugging insights
- **Recurring themes** — Patterns across multiple sessions
- **User corrections** — Times the user corrected your output or approach
- **Error resolutions** — Problems encountered and their fixes

Skip one-off observations, transient context, and information already captured in
KNOWLEDGE.md or other context files.

### 3. Writing to MEMORY.md

Write distilled content to the appropriate sections of MEMORY.md:

1. Use the **Edit tool** for all MEMORY.md writes — never use `python3 locked_write.py` via Bash (crashes in PyInstaller bundles).
   **Always prefix entries with the source DailyActivity date** (not today's date).
   Read `.context/MEMORY.md`, find the target section, and prepend the new entry
   at the top of the section (after the `##` heading).
   Format: `- YYYY-MM-DD: <distilled entry>`

2. Map extracted content to MEMORY.md sections:
   - Key decisions → `## Key Decisions`
   - Lessons learned → `## Lessons Learned`
   - Recurring themes → `## Patterns and Preferences`
   - User corrections → `## Lessons Learned`
   - Error resolutions → `## Lessons Learned`
   - Current work status → `## Recent Context`
   - Open thread updates → `## Open Threads`
3. **Fallback**: If a target section is not found (user may have renamed or removed it),
   add a new `## Distilled` section at the end of the file and write there.
4. **Never remove** existing MEMORY.md content — only prepend or append to sections
5. Keep entries concise: one line per decision/lesson, date-prefixed, grouped by theme
6. **Deduplication**: Before writing, check if the same content (ignoring date prefix) already exists in the target section. Skip duplicates.

### 4. Marking Processed Files

After successfully distilling a file, mark it as processed:

1. Read the file's current content
2. Parse existing YAML frontmatter (if any)
3. Add or update frontmatter fields:
   - `distilled: true`
   - `distilled_date: YYYY-MM-DD` (use UTC date)
4. Write the updated content back to the file

Example — file before marking:
```markdown
# 2025-07-10

Worked on authentication refactor...
```

Example — file after marking:
```markdown
---
distilled: true
distilled_date: "2025-07-10"
---

# 2025-07-10

Worked on authentication refactor...
```

For DailyActivity file writes, use standard file write tools — the OS guarantees
atomic appends via `O_APPEND`. No lock is needed for DailyActivity files.

### 5. Archiving

Manage the DailyActivity lifecycle:

1. **Move >30 day files**: Move DailyActivity files older than 30 days to
   `Knowledge/Archives/`. Determine age from the filename date (`YYYY-MM-DD.md`).
2. **Delete >90 day archives**: Delete files in `Knowledge/Archives/` older than
   90 days (based on filename date).
3. Create `Knowledge/Archives/` if it doesn't exist.

```bash
# Example: move old files
mv Knowledge/DailyActivity/2025-06-01.md Knowledge/Archives/

# Example: delete very old archives
rm Knowledge/Archives/2025-04-01.md
```

### 6. Open Threads

Cross-reference recent DailyActivity files for thread completions:

1. Read the current `## Open Threads` section from MEMORY.md
2. Scan recent DailyActivity files for evidence that open threads have been completed
3. Mark completed threads (e.g., prefix with `✅` or move to a "Completed" sub-section)
4. Add any new open threads discovered during extraction
5. Use `locked_write.py` for all MEMORY.md modifications via the Edit tool (never via `python3` subprocess)

### 7. Silence and Logging

- **All operations are silent** — do not announce, do not ask permission, do not
  describe what you're doing to the user
- **Log at INFO level** after completion:
  `"Distilled N DailyActivity files, promoted M entries to MEMORY.md"`
- This log is for observability only — it is not shown to the user

### Summary of Tools

| Operation | Tool | Lock needed? |
|---|---|---|
| Read DailyActivity files | Standard file read | No |
| Write to MEMORY.md | Edit tool (read → find section → prepend) | No (single writer) |
| Mark DailyActivity frontmatter | Standard file write | No |
| Append to DailyActivity | `>>` shell append | No |
| Move files to Archives | `mv` | No |
| Delete old archives | `rm` | No |

## Verification

Before marking this task complete, show evidence for each:

- [ ] **Files processed listed** — log shows which DailyActivity files were scanned and how many were unprocessed
- [ ] **Entries promoted/skipped with reasons** — each extracted item either written to a MEMORY.md section or skipped (with dedup or relevance reason)
- [ ] **MEMORY.md updated** — new entries visible in the correct sections, date-prefixed, no duplicates introduced
- [ ] **Processed files marked** — each distilled file now has `distilled: true` and `distilled_date` in its YAML frontmatter
- [ ] **Archives maintained** — files older than 30 days moved to Archives/, files older than 90 days deleted from Archives/
