---
name: Workspace Git
description: >
  Git operations scoped to SwarmWS: status, diff, commit, log, search, stash, and branch management.
  Provides structured output for the agent to reason about workspace changes.
  TRIGGER: "git status", "commit changes", "what changed", "git log", "git diff", "stash", "undo last commit",
  "show me the diff", "commit today's work", "git history", "revert".
  DO NOT USE: for GitHub PR/issue operations (use github-research or gh CLI directly), or non-SwarmWS repos.
  SIBLINGS: workspace-finder = file search | workspace-organizer = batch file ops | code-review = PR review.
tier: always
---
# Workspace Git

Git operations for SwarmWS. All commands run in the SwarmWS workspace root (`~/.swarm-ai/SwarmWS/`).

---

## Quick Reference

| User says | Command |
|-----------|---------|
| "what changed" | `git status --short` |
| "show me the diff" | `git diff` |
| "commit today's work" | Review → stage → commit with summary |
| "what did we do this week" | `git log --since="1 week ago" --oneline` |
| "undo last commit" | `git reset --soft HEAD~1` |
| "stash my changes" | `git stash push -m "description"` |
| "search commits for X" | `git log --all --grep="X" --oneline` |

---

## Operations

### Status & Diff

```bash
# Quick status
git status --short

# Full diff (unstaged)
git diff

# Staged changes
git diff --cached

# Diff for specific file
git diff -- path/to/file

# Stat summary (files changed, insertions, deletions)
git diff --stat

# Word-level diff (useful for markdown)
git diff --word-diff
```

### Commit Workflow

When user asks to commit:

1. **Review** — `git status --short` + `git diff --stat`
2. **Summarize** — Describe what changed in plain language
3. **Stage** — `git add <specific files>` (never `git add -A` blindly)
4. **Commit** — Write a clear commit message

```bash
# Stage specific files
git add Knowledge/Notes/2026-03-15-plan.md .context/MEMORY.md

# Commit with descriptive message
git commit -m "$(cat <<'EOF'
Add explorer improvement plan and update memory

- Created comprehensive explorer improvement plan (Phase 1-4)
- Updated MEMORY.md with session decisions

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

**Rules:**
- Never use `git add -A` or `git add .` without reviewing first
- Never commit `.env`, credentials, or large binaries
- Always include `Co-Authored-By` when agent writes the commit
- Prefer specific file staging over wildcard
- Ask user before committing if changes are large or unclear

### Log & History

```bash
# Recent commits
git log --oneline -10

# Commits with file changes
git log --oneline --name-only -5

# Commits by date range
git log --since="2026-03-14" --until="2026-03-15" --oneline

# Search commit messages
git log --all --grep="keyword" --oneline

# Search code changes (pickaxe)
git log -S "function_name" --oneline

# File history
git log --oneline -- path/to/file

# Graph view
git log --oneline --graph --all -20
```

### Undo & Recovery

```bash
# Undo last commit (keep changes staged)
git reset --soft HEAD~1

# Unstage a file
git restore --staged path/to/file

# Discard changes to a file (DESTRUCTIVE — ask first)
git restore path/to/file

# View a file at a previous commit
git show HEAD~1:path/to/file

# Stash current changes
git stash push -m "WIP: description"

# List stashes
git stash list

# Apply most recent stash
git stash pop

# Apply specific stash
git stash apply stash@{1}
```

**Safety rules for destructive operations:**
- `git reset --hard` — ALWAYS ask user first
- `git checkout -- .` / `git restore .` — ALWAYS ask user first
- `git clean -f` — ALWAYS ask user first
- `git push --force` — NEVER do without explicit request
- Prefer `git stash` over discard when user wants to "undo"

### Branch Operations

SwarmWS typically runs on a single branch, but project sub-repos may have branches:

```bash
# List branches
git branch -a

# Create and switch
git checkout -b feature/name

# Switch branch
git checkout main

# Merge
git merge feature/name

# Delete merged branch
git branch -d feature/name
```

---

## Structured Output

When presenting git information, use tables:

```
### Status Summary
| Status | File | Action |
|--------|------|--------|
| M | .context/MEMORY.md | Modified (unstaged) |
| A | Knowledge/Notes/2026-03-15-plan.md | New file (staged) |
| ?? | Attachments/screenshot.png | Untracked |

### Recent History (last 5 commits)
| Hash | Date | Message |
|------|------|---------|
| abc1234 | 2h ago | Add explorer improvement plan |
| def5678 | 5h ago | Fix file open regression |
```

---

## SwarmWS-Specific Context

- SwarmWS auto-commits via `WorkspaceAutoCommitHook` after each session
- DailyActivity files are created by `DailyActivityExtractionHook`
- `.context/` files are managed by the context system — changes here affect agent behavior
- The workspace is always a git repo (initialized on first run)
- Large files in `Attachments/` should be gitignored if >10MB

## Verification

Before marking this task complete, show evidence for each:

- [ ] **Git command output shown** — the raw git output (status, diff, log, etc.) is displayed so the user sees the actual result
- [ ] **Operation confirmed** — for write operations (commit, stash, reset), the resulting state is verified with a follow-up `git status` or `git log`
- [ ] **Working tree clean** — after commits, `git status` confirms no unexpected unstaged or untracked files remain
- [ ] **Commit message descriptive** — commit messages describe what changed and why, with `Co-Authored-By` included when agent-written
- [ ] **No destructive surprises** — destructive operations (reset --hard, restore, clean) were confirmed with the user before execution
