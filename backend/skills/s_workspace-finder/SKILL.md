---
name: Workspace Finder
description: >
  Natural language file and content search within SwarmWS workspace. Find files by name, content, modification time,
  size, type, or git status. Returns structured results the agent can reference or act on.
  TRIGGER: "find files", "search workspace", "where is", "find all", "what changed", "show me files", "find TODOs",
  "files modified today", "large files", "find duplicates".
  DO NOT USE: for web search (use tavily-search), GitHub repo research (use github-research), or reading a known file (use Read tool).
  SIBLINGS: deep-research = web research | tavily-search = web search | workspace-git = git-specific operations.
tier: always
---
# Workspace Finder

Find anything in SwarmWS using natural language. Translates user intent into efficient CLI commands.

---

## Quick Reference

| User says | Tool chain |
|-----------|------------|
| "find files about authentication" | `grep -rl "auth" --include="*.md" --include="*.py" --include="*.ts"` |
| "what changed in the last 3 days" | `git log --since="3 days ago" --name-only --pretty=format:""` |
| "find all TODO comments" | `grep -rn "TODO\|FIXME\|HACK\|XXX" --include="*.py" --include="*.ts" --include="*.tsx"` |
| "show me large files over 1MB" | `find . -type f -size +1M -not -path './.git/*'` |
| "Python files modified this week" | `find . -name "*.py" -mtime -7 -not -path './.git/*'` |
| "what files did the agent create today" | `git log --since="today" --diff-filter=A --name-only --pretty=format:""` |
| "find empty directories" | `find . -type d -empty -not -path './.git/*'` |
| "files with merge conflicts" | `git diff --name-only --diff-filter=U` |

---

## Search Strategies

### 1. By Name / Pattern
```bash
# Exact name
find . -name "MEMORY.md" -not -path './.git/*'

# Pattern (glob)
find . -name "*.test.ts" -not -path './.git/*' -not -path '*/node_modules/*'

# Case-insensitive
find . -iname "*readme*" -not -path './.git/*'
```

### 2. By Content
```bash
# Simple text search (recursive, with line numbers)
grep -rn "pattern" --include="*.py" --include="*.ts"

# Files containing pattern (names only)
grep -rl "pattern" --include="*.md"

# Regex search
grep -rn -E "def (test_|_test)" --include="*.py"

# Context around matches
grep -rn -B2 -A2 "pattern" --include="*.py"

# Exclude directories
grep -rn "pattern" --exclude-dir={.git,node_modules,__pycache__,.venv}
```

### 3. By Time
```bash
# Modified in last N days
find . -type f -mtime -3 -not -path './.git/*'

# Modified today
find . -type f -mtime 0 -not -path './.git/*'

# Git: changes since date
git log --since="2026-03-14" --name-only --pretty=format:"" | sort -u

# Git: files changed in last N commits
git diff --name-only HEAD~5
```

### 4. By Git Status
```bash
# All modified/untracked files
git status --short

# Only modified (not untracked)
git diff --name-only

# Staged files
git diff --cached --name-only

# Files changed between branches
git diff main..HEAD --name-only

# Files with specific status
git diff --diff-filter=M --name-only   # Modified
git diff --diff-filter=A --name-only   # Added
git diff --diff-filter=D --name-only   # Deleted
```

### 5. By Size
```bash
# Large files (>1MB)
find . -type f -size +1M -not -path './.git/*' -exec ls -lh {} \;

# Small files (<1KB)
find . -type f -size -1k -not -path './.git/*'

# Disk usage by directory
du -sh */ | sort -rh | head -20
```

### 6. Combined / Advanced
```bash
# Python files modified this week containing "async"
find . -name "*.py" -mtime -7 -not -path './.git/*' -exec grep -l "async" {} \;

# Markdown files in Knowledge/ sorted by modification time
find Knowledge/ -name "*.md" -printf "%T@ %p\n" 2>/dev/null | sort -rn | head -20
# macOS alternative:
find Knowledge/ -name "*.md" -exec stat -f "%m %N" {} \; | sort -rn | head -20

# Count files by extension
find . -type f -not -path './.git/*' | sed 's/.*\.//' | sort | uniq -c | sort -rn | head -20
```

---

## Output Format

Always present results as a structured summary:

```
Found N files matching "query":

| # | File | Size | Modified | Context |
|---|------|------|----------|---------|
| 1 | Knowledge/Notes/2026-03-15-plan.md | 4.2K | 2h ago | Line 42: "authentication flow" |
| 2 | ... | ... | ... | ... |
```

For large result sets (>20 files), summarize by directory first, then offer to drill down.

---

## Workspace-Specific Paths

| Directory | Contains |
|-----------|----------|
| `Knowledge/Notes/` | Date-prefixed notes and specs |
| `Knowledge/DailyActivity/` | Session logs (YYYY-MM-DD.md) |
| `Knowledge/Reports/` | Analysis and research outputs |
| `Projects/` | Symlinked project workspaces |
| `.context/` | 11 context files (P0-P10) |
| `Attachments/` | User uploads and exports |

Always exclude `.git/`, `node_modules/`, `__pycache__/`, `.venv/` from searches unless explicitly asked.

## Verification

Before marking this task complete, show evidence for each:

- [ ] **Search criteria stated** — the translated search command (find/grep/git) is shown so the user knows exactly what was searched
- [ ] **Matching files listed** — results presented in a structured table with file path, size, modification time, and context (if content search)
- [ ] **Results sorted** — output is ordered by relevance (content match) or modification time (file search), not random
- [ ] **Noise excluded** — `.git/`, `node_modules/`, `__pycache__/`, `.venv/` directories are excluded unless explicitly requested
- [ ] **Large results summarized** — if more than 20 matches, results are grouped by directory first with an offer to drill down
