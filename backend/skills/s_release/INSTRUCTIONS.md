# Release Workflow

Bump version, update CHANGELOG, tag, and publish GitHub Release. Zero files missed.

## Version Files (ALL 4 MUST BE UPDATED)

| # | File | Field | Format |
|---|------|-------|--------|
| 1 | `backend/pyproject.toml` | `version = "X.Y.Z"` | TOML |
| 2 | `desktop/package.json` | `"version": "X.Y.Z"` | JSON |
| 3 | `desktop/src-tauri/Cargo.toml` | `version = "X.Y.Z"` | TOML |
| 4 | `desktop/src-tauri/tauri.conf.json` | `"version": "X.Y.Z"` | JSON |

## Versioning Convention

- **Major** (X): Breaking changes, architecture redesign
- **Minor** (Y): New features, new skills, new UI sections
- **Patch** (Z): Bug fixes, PE review fixes, config changes

## Execution Steps

### Step 1: Determine Version

Read the current version from any of the 4 files (they must be in sync).
If user specified a version, use it. Otherwise, determine from scope:
- Features added since last release? → bump minor
- Only fixes? → bump patch

### Step 2: Gather Changes

```bash
# Find the last version bump commit
git log --oneline --grep="bump version" -1

# List all commits since then
git log <last-bump-hash>..HEAD --oneline
```

Categorize each commit into Added / Fixed / Changed for CHANGELOG.

### Step 3: Update CHANGELOG.md

Prepend a new section above the previous version entry:

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added
- **Feature Name**: One-line description

### Fixed
- **Bug Name**: One-line description

### Changed
- **Change Name**: One-line description
```

Rules:
- Date is today (user's local timezone, UTC+8)
- Bold the feature/bug name
- One line per item — details belong in commit messages
- Order: most impactful first within each section
- Keep format consistent with existing CHANGELOG entries

### Step 4: Bump Version in All 4 Files

Edit each of the 4 files listed above. Use the Edit tool — do NOT do search-and-replace on the old version string globally (it might match dependency versions).

**Verification**: After editing, run:
```bash
grep -n "version" backend/pyproject.toml | head -1
grep -n "version" desktop/package.json | head -1
grep -n "version" desktop/src-tauri/Cargo.toml | head -1
grep -n "version" desktop/src-tauri/tauri.conf.json | head -1
```
All 4 must show the new version.

### Step 5: Commit

```bash
git add CHANGELOG.md backend/pyproject.toml desktop/package.json \
  desktop/src-tauri/Cargo.toml desktop/src-tauri/tauri.conf.json
git commit -m "chore: bump version to X.Y.Z, update CHANGELOG"
```

### Step 6: Push

```bash
git push
```

### Step 7: Tag and Push Tag

```bash
git tag vX.Y.Z -m "vX.Y.Z: <one-line summary of highlights>"
git push origin vX.Y.Z
```

### Step 8: Create GitHub Release

```bash
gh release create vX.Y.Z \
  --title "vX.Y.Z — <short highlight summary>" \
  --notes "$(cat <<'EOF'
## Highlights

- **Feature 1** — one sentence
- **Feature 2** — one sentence

## Added

- item 1
- item 2

## Fixed

- item 1
- item 2

## Changed

- item 1

---

**Full changelog:** [CHANGELOG.md](https://github.com/xg-gh-25/SwarmAI/blob/main/CHANGELOG.md)
EOF
)"
```

The release notes should be a condensed version of the CHANGELOG — highlights at top, then Added/Fixed/Changed sections.

### Step 9: Report

Output the release URL and a summary:
- Version: X.Y.Z
- Files bumped: 4/4
- CHANGELOG: updated
- Tag: pushed
- Release: URL

## Patch Release Shortcut

For patch releases (only fixes, no features):

1. Steps 1-2: Determine version, gather fixes
2. Steps 3-6: CHANGELOG + bump + commit + push
3. Steps 7-8: Tag + release
4. Release notes can be shorter — just list the fixes

## Pre-Release Checklist (informational — not enforced by this skill)

These are recommended before cutting a release, but this skill does NOT block on them:
- [ ] All tests pass (`cd backend && python -m pytest --timeout=60`)
- [ ] Build succeeds (`./prod.sh build` — runs PyInstaller + 41 checks)
- [ ] No uncommitted changes in working tree

If user wants to skip — their call. This skill only handles versioning.
