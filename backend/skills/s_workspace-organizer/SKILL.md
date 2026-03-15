---
name: Workspace Organizer
description: >
  Batch file operations for SwarmWS: organize, archive, clean up, and restructure workspace contents.
  Always previews before executing. Uses mv (not rm) for safety.
  TRIGGER: "organize files", "clean up workspace", "archive old files", "move files", "restructure",
  "deduplicate", "batch rename", "clean empty folders", "organize by date", "tidy up".
  DO NOT USE: for single file rename/move (use explorer context menu), deleting files (use explorer delete),
  or git operations (use workspace-git).
  SIBLINGS: workspace-finder = find files | workspace-git = git operations | project-manager = project CRUD.
---

# Workspace Organizer

Batch file operations for SwarmWS. **Always preview before execute. Use mv, not rm.**

---

## Core Principle

```
1. FIND → show what will be affected
2. PREVIEW → present the plan as a table
3. CONFIRM → wait for user approval
4. EXECUTE → run the operations
5. VERIFY → show the result
```

Never skip the preview step. Never delete without user confirmation.

---

## Quick Reference

| User says | Operation |
|-----------|-----------|
| "organize Notes by month" | Group files into YYYY-MM/ subdirectories |
| "archive files older than 30 days" | Move to Knowledge/Archives/ |
| "clean up empty directories" | Find and remove empty dirs |
| "deduplicate files" | Find by content hash, show duplicates |
| "batch rename with date prefix" | Add YYYY-MM-DD prefix to unprefixed files |
| "tidy up Attachments" | Organize by file type into subdirectories |

---

## Operations

### Organize by Date (Month)

```bash
# Preview: list files and their target directories
for f in Knowledge/Notes/*.md; do
  month=$(stat -f "%Sm" -t "%Y-%m" "$f" 2>/dev/null || date -r "$f" +"%Y-%m")
  echo "$f → Knowledge/Notes/$month/$(basename "$f")"
done

# Execute (after user confirms):
for f in Knowledge/Notes/*.md; do
  month=$(stat -f "%Sm" -t "%Y-%m" "$f" 2>/dev/null || date -r "$f" +"%Y-%m")
  mkdir -p "Knowledge/Notes/$month"
  mv "$f" "Knowledge/Notes/$month/"
done
```

### Archive Old Files

```bash
# Preview: files older than 30 days
find Knowledge/DailyActivity/ -name "*.md" -mtime +30 -not -name "*.distilled" \
  -exec echo "Archive: {} → Knowledge/Archives/" \;

# Execute:
mkdir -p Knowledge/Archives/DailyActivity
find Knowledge/DailyActivity/ -name "*.md" -mtime +30 \
  -exec mv {} Knowledge/Archives/DailyActivity/ \;
```

### Clean Empty Directories

```bash
# Preview: find empty dirs
find . -type d -empty -not -path './.git/*' -not -path './.context/*'

# Execute:
find . -type d -empty -not -path './.git/*' -not -path './.context/*' -delete
```

### Deduplicate Files

```bash
# Find potential duplicates by content hash (md5)
find . -type f -not -path './.git/*' -exec md5 -r {} \; | \
  awk '{print $1}' | sort | uniq -d | while read hash; do
    echo "=== Duplicate group (hash: $hash) ==="
    find . -type f -not -path './.git/*' -exec md5 -r {} \; | grep "^$hash" | awk '{print $2}'
  done
```

Present as:
```
Duplicate groups found:
| Group | Hash | Files |
|-------|------|-------|
| 1 | abc123 | Notes/2026-03-10-plan.md, Notes/old-plan.md |
| 2 | def456 | Attachments/img1.png, Attachments/img1-copy.png |

Which groups should I deduplicate? (Keep newest, move others to trash)
```

### Batch Rename — Add Date Prefix

```bash
# Preview: files without YYYY-MM-DD prefix
find Knowledge/Notes/ -name "*.md" -not -name "[0-9][0-9][0-9][0-9]-*" | while read f; do
  mod_date=$(stat -f "%Sm" -t "%Y-%m-%d" "$f")
  new_name="$(dirname "$f")/${mod_date}-$(basename "$f")"
  echo "Rename: $f → $new_name"
done

# Execute:
find Knowledge/Notes/ -name "*.md" -not -name "[0-9][0-9][0-9][0-9]-*" | while read f; do
  mod_date=$(stat -f "%Sm" -t "%Y-%m-%d" "$f")
  mv "$f" "$(dirname "$f")/${mod_date}-$(basename "$f")"
done
```

### Organize by File Type

```bash
# Preview: categorize files in Attachments/
for f in Attachments/*; do
  [ -f "$f" ] || continue
  ext="${f##*.}"
  case "$ext" in
    png|jpg|jpeg|gif|svg|webp) dir="Attachments/Images" ;;
    pdf) dir="Attachments/PDFs" ;;
    csv|xlsx|xls) dir="Attachments/Spreadsheets" ;;
    *) dir="Attachments/Other" ;;
  esac
  echo "$f → $dir/$(basename "$f")"
done

# Execute (after confirm):
for f in Attachments/*; do
  [ -f "$f" ] || continue
  ext="${f##*.}"
  case "$ext" in
    png|jpg|jpeg|gif|svg|webp) dir="Attachments/Images" ;;
    pdf) dir="Attachments/PDFs" ;;
    csv|xlsx|xls) dir="Attachments/Spreadsheets" ;;
    *) dir="Attachments/Other" ;;
  esac
  mkdir -p "$dir"
  mv "$f" "$dir/"
done
```

---

## Safety Rules

1. **Never use `rm`** — always `mv` to a staging area or `Knowledge/Archives/`
2. **Never modify `.context/`** — system-managed files
3. **Never modify `.git/`** — git internals
4. **Always preview first** — show a table of planned changes
5. **Wait for confirmation** — do not auto-execute batch operations
6. **Preserve git history** — use `git mv` when files are tracked
7. **Update indexes** — after moving files in Knowledge/, update KNOWLEDGE.md

---

## Output Format

Always present the plan as:

```
### Proposed Changes (N files)

| # | Action | Source | Destination |
|---|--------|--------|-------------|
| 1 | Move | Knowledge/Notes/old.md | Knowledge/Archives/old.md |
| 2 | Rename | notes.md | 2026-03-15-notes.md |
| 3 | Create | — | Knowledge/Notes/2026-03/ |

Proceed? (yes/no)
```

After execution:
```
### Done ✓
- Moved 5 files to Archives
- Created 2 directories
- Renamed 3 files with date prefix
```
