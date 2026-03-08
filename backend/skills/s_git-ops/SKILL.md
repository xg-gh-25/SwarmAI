---
name: Git Operations
description: >
  Advanced git workflows: PR review, changelog generation, branch strategy,
  conflict resolution, commit analysis, and repository management.
  TRIGGER: "PR review", "pull request", "changelog", "git log", "branch strategy",
  "merge conflict", "git blame", "commit history", "release notes", "git diff analysis",
  "rebase", "cherry-pick", "git bisect", "squash".
  DO NOT USE: for basic git add/commit/push (just use Bash), or GitHub API
  operations beyond what git CLI provides (use github-research).
---

# Git Operations — Advanced Git Workflows

Structured workflows for git operations beyond basic add/commit/push.
PR reviews, changelog generation, conflict resolution, commit analysis,
and release management.

## Quick Start

```
"Review the changes in this PR branch"
"Generate a changelog from the last release"
"Analyze the commit history of this file"
"Help me resolve this merge conflict"
"What changed between v1.0 and v2.0?"
```

---

## PR / Branch Review

### Full Branch Diff Analysis

```bash
# What branch am I on?
git branch --show-current

# Diff against main (summary)
git diff main...HEAD --stat

# Full diff
git diff main...HEAD

# Commits on this branch
git log main..HEAD --oneline --no-merges

# Files changed
git diff main...HEAD --name-only

# Diff for specific file type
git diff main...HEAD -- '*.py' '*.ts'
```

### PR Review Checklist

When reviewing a branch, analyze in this order:

1. **Scope** — `git diff main...HEAD --stat` — how big is the change?
2. **Commits** — `git log main..HEAD --oneline` — are commits logical?
3. **Files** — `git diff main...HEAD --name-only` — what areas touched?
4. **Code** — `git diff main...HEAD` — read the actual diff
5. **Tests** — check if test files are included in the diff
6. **Breaking** — look for API changes, schema migrations, config changes

Report format:
```
## PR Review: [branch-name]
**Scope**: N files changed, +X/-Y lines
**Summary**: [one-line description]
**Commits**: N commits — [logical/messy]

### Changes
- [file]: [what changed and why]

### Concerns
- [any issues found]

### Verdict
- [ ] Ready to merge
- [ ] Needs changes: [list]
```

---

## Changelog & Release Notes

### Generate Changelog from Tags

```bash
# Commits since last tag
git log $(git describe --tags --abbrev=0)..HEAD --oneline --no-merges

# Between two tags
git log v1.0.0..v2.0.0 --oneline --no-merges

# Grouped by author
git shortlog v1.0.0..v2.0.0 --no-merges

# With dates
git log v1.0.0..v2.0.0 --format="%h %ad %s" --date=short --no-merges
```

### Changelog Format

Generate structured changelogs by categorizing commits:

```markdown
## [v2.0.0] - 2026-03-08

### Added
- feat: new browser automation skill (#123)

### Changed
- refactor: improved DOM compression engine (#124)

### Fixed
- fix: CDP connection persistence (#125)

### Removed
- removed: deprecated legacy browser driver
```

**Commit prefix mapping:**
- `feat:` / `add:` → **Added**
- `fix:` / `bugfix:` → **Fixed**
- `refactor:` / `update:` / `improve:` → **Changed**
- `remove:` / `deprecate:` → **Removed**
- `docs:` → **Documentation**
- `test:` → **Testing**
- `chore:` / `ci:` → skip (internal)

---

## Commit Analysis

### File History

```bash
# Full history of a file
git log --oneline --follow -- path/to/file

# With diffs
git log -p --follow -- path/to/file

# Who changed what (blame)
git blame path/to/file

# Blame specific lines
git blame -L 10,20 path/to/file

# Ignore whitespace changes in blame
git blame -w path/to/file
```

### Find When Something Changed

```bash
# Search commit messages
git log --oneline --grep="search term"

# Search code changes (pickaxe)
git log -p -S "function_name" -- '*.py'

# Search with regex
git log -p -G "pattern" -- '*.ts'

# Bisect to find bug introduction
git bisect start
git bisect bad HEAD
git bisect good v1.0.0
# Then test at each point...
git bisect reset
```

### Statistics

```bash
# Contributor stats
git shortlog -sn --no-merges

# Activity in last 30 days
git log --since="30 days ago" --oneline --no-merges | wc -l

# Most changed files
git log --since="30 days ago" --name-only --no-merges --format="" | sort | uniq -c | sort -rn | head -20

# Churn analysis (files that change together)
git log --name-only --no-merges --format="" | sort | uniq -c | sort -rn | head -10
```

---

## Branch Management

### Branch Strategy

```bash
# List branches with last commit date
git branch -a --sort=-committerdate --format="%(refname:short) %(committerdate:relative)"

# Merged branches (safe to delete)
git branch --merged main

# Unmerged branches
git branch --no-merged main

# Delete merged branches
git branch --merged main | grep -v "main\|master\|\*" | xargs git branch -d
```

### Rebase Workflow

```bash
# Interactive rebase to clean up commits before merge
git rebase -i main
# In editor: squash/fixup/reorder commits

# Rebase on latest main
git fetch origin
git rebase origin/main
```

### Cherry-Pick

```bash
# Pick specific commit onto current branch
git cherry-pick <commit-hash>

# Pick range
git cherry-pick <start>..<end>

# Pick without committing (stage only)
git cherry-pick --no-commit <commit-hash>
```

---

## Conflict Resolution

### Identify Conflicts

```bash
# After merge/rebase that has conflicts
git status  # shows conflicted files
git diff --name-only --diff-filter=U  # only unmerged files
```

### Resolve Strategy

1. **Read both sides**: `git diff --ours -- file` and `git diff --theirs -- file`
2. **Understand intent**: `git log --oneline main..HEAD -- file` (our changes)
   and `git log --oneline HEAD..main -- file` (their changes)
3. **Edit the file**: Remove conflict markers, keep correct code
4. **Mark resolved**: `git add file`
5. **Continue**: `git rebase --continue` or `git merge --continue`

### Abort if Needed

```bash
git merge --abort   # undo merge attempt
git rebase --abort  # undo rebase attempt
```

---

## Stash Operations

```bash
# Save work in progress
git stash push -m "description of WIP"

# List stashes
git stash list

# Apply latest
git stash pop

# Apply specific
git stash apply stash@{2}

# Stash including untracked files
git stash push -u -m "including new files"
```

---

## Diff Analysis Patterns

### Meaningful Diff Summary

When presenting diffs to the user, summarize as:

```
**Changed files** (N files, +X/-Y lines):
- `path/to/file.py` (+15/-3) — added error handling to parse_config()
- `tests/test_parse.py` (+25/-0) — new tests for edge cases
- `README.md` (+2/-1) — updated usage example
```

### Compare Anything

```bash
# Two branches
git diff branch-a..branch-b --stat

# Two commits
git diff abc123..def456

# Working tree vs last commit
git diff HEAD

# Staged changes
git diff --cached

# Specific file between commits
git diff abc123..def456 -- path/to/file
```

---

## Rules

1. **Never force-push to main/master** — warn user if they request it
2. **Never run destructive commands silently** — always explain what
   `reset --hard`, `push --force`, `clean -f` will do before running
3. **Read before acting** — always `git status` and `git log` before
   mutations
4. **Prefer new commits over amend** — unless user explicitly asks to amend
5. **Don't skip hooks** — no `--no-verify` unless user insists
6. **Summarize diffs** — don't dump 500 lines of diff; summarize then
   offer to show specific files
7. **Credentials** — never log or echo tokens/passwords from git remotes
