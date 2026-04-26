# Release Workflow

Bump version, update CHANGELOG, tag, and publish GitHub Release. Zero files missed.

## Version Files (ALL 4 MUST BE UPDATED)

| # | File | Field | Format |
|---|------|-------|--------|
| 1 | `backend/pyproject.toml` | `version = "X.Y.Z"` | TOML |
| 2 | `desktop/package.json` | `"version": "X.Y.Z"` | JSON |
| 3 | `desktop/src-tauri/Cargo.toml` | `version = "X.Y.Z"` | TOML |
| 4 | `desktop/src-tauri/tauri.conf.json` | `"version": "X.Y.Z"` | JSON |

## README Files (MUST STAY IN SYNC)

| # | File | What to check |
|---|------|---------------|
| 1 | `README.md` | "What's New" section, "Recent Releases" table, "By the numbers" stats, skills count, Story section |
| 2 | `README.zh-CN.md` | Same sections — Chinese mirror of README.md |

Both READMEs must reflect the new release. Check and update:
- **"What's New"** — feature table matches this release's highlights
- **"Recent Releases"** — new version at top, shift previous rows down (keep last 4)
- **"By the numbers"** — commits count, skills count, LOC, modules, components
- **Skills count** — grep for `55+` / `65+` etc. across all sections (Why SwarmAI cards, Architecture flywheel, vs Alternatives tables, vs OpenClaw table). Get actual count: `ls -d backend/skills/s_*/ | wc -l`
- **Story section** — age in days (`python3 -c "from datetime import date; print((date.today() - date(2026,3,14)).days)"`), memory stats (check MEMORY.md index header)

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

### Step 4: Update READMEs (EN + CN)

Read both `README.md` and `README.zh-CN.md`. Update in parallel:

1. **"What's New" section** — replace the feature table with this release's highlights
2. **"Recent Releases" table** — add new version row at top, drop oldest if > 4 rows
3. **"By the numbers" line** — update commits, LOC, skills, modules, components:
   ```bash
   echo "commits: $(git rev-list --count HEAD)"
   echo "skills: $(ls -d backend/skills/s_*/ | wc -l)"
   echo "backend LOC: $(find backend -name '*.py' -not -path '*/.venv/*' -not -path '*/__pycache__/*' -not -path '*/.*' | xargs wc -l | tail -1)"
   echo "backend modules: $(find backend -name '*.py' -not -path '*/.venv/*' -not -path '*/__pycache__/*' -not -path '*/tests/*' -not -path '*/.*' | wc -l)"
   echo "React components: $(find desktop/src -name '*.tsx' | wc -l)"
   ```
4. **Skills count** — search both files for the old count (e.g. `65+`) in ALL locations: feature cards, flywheel table, vs Alternatives tables, vs OpenClaw table. Update all occurrences.
5. **Story section** — update age in days and memory stats if stale

Commit READMEs separately before version bump:
```bash
git add README.md README.zh-CN.md
git commit -m "docs: refresh README (EN + CN) for vX.Y.Z release"
```

### Step 5: Bump Version in All 4 Files

Edit each of the 4 files listed above. Use the Edit tool — do NOT do search-and-replace on the old version string globally (it might match dependency versions).

**Verification**: After editing, run:
```bash
grep -n "version" backend/pyproject.toml | head -1
grep -n "version" desktop/package.json | head -1
grep -n "version" desktop/src-tauri/Cargo.toml | head -1
grep -n "version" desktop/src-tauri/tauri.conf.json | head -1
```
All 4 must show the new version.

### Step 6: Commit

```bash
git add CHANGELOG.md backend/pyproject.toml desktop/package.json \
  desktop/src-tauri/Cargo.toml desktop/src-tauri/tauri.conf.json
git commit -m "chore: bump version to X.Y.Z, update CHANGELOG"
```

### Step 7: Push

```bash
git push
```

### Step 8: Tag and Push Tag

```bash
git tag vX.Y.Z -m "vX.Y.Z: <one-line summary of highlights>"
git push origin vX.Y.Z
```

### Step 9: Create GitHub Release

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

### Step 10: Report

Output the release URL and a summary:
- Version: X.Y.Z
- Files bumped: 4/4
- READMEs: updated (EN + CN)
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
