# Release Workflow

Bump version, update CHANGELOG, tag, and publish GitHub Release. Zero files missed.

## Release CI Gate

All work stays on `main`. Push triggers CI automatically. **Wait for CI green before tagging.**

```
commit on main → push → CI runs (backend + backend-windows + frontend + version-check) → all green → tag → release
```

This prevents v1.9.0-class failures (3 P0 bugs that CI would have caught).
No branches, no PRs, no human review. CI is the only gate.

## Version Files (ALL 5 MUST BE UPDATED)

| # | File | Field | Format |
|---|------|-------|--------|
| 0 | **`VERSION`** (root) | `X.Y.Z` (plain text) | **Source of truth** — `sync-version.sh` reads this and overwrites all others |
| 1 | `backend/config.py` | `_read_version("X.Y.Z")` | Python |
| 2 | `backend/pyproject.toml` | `version = "X.Y.Z"` | TOML |
| 3 | `desktop/package.json` | `"version": "X.Y.Z"` | JSON |
| 4 | `desktop/src-tauri/Cargo.toml` | `version = "X.Y.Z"` | TOML |
| 5 | `desktop/src-tauri/tauri.conf.json` | `"version": "X.Y.Z"` | JSON |

> ⚠️ **CRITICAL:** The `VERSION` file MUST be updated first. `dev.sh` and `prod.sh` both call `sync-version.sh` on startup, which reads `VERSION` and overwrites all 4 package files. If `VERSION` is stale, every dev/build run silently downgrades all versions.

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

### Step 0: Pre-Flight Checks

Run ALL of these before proceeding. Any failure = stop and fix first.

```bash
# 1. Working tree must be clean (no uncommitted changes)
git status --short
# If non-empty → commit or stash first

# 2. All 5 version files must be in sync (VERSION is source of truth)
echo "VERSION:   $(cat VERSION)"
echo "pyproject: $(grep '^version' backend/pyproject.toml | head -1)"
echo "package:   $(grep '"version"' desktop/package.json | head -1)"
echo "cargo:     $(grep '^version' desktop/src-tauri/Cargo.toml | head -1)"
echo "tauri:     $(grep '"version"' desktop/src-tauri/tauri.conf.json | head -1)"
# If they differ → run ./scripts/sync-version.sh to fix

# 3. Target tag must not exist
git tag -l "vX.Y.Z"
# If tag exists → user probably wants a different version
```

### Step 1: Determine Version

Read the current version from the pre-flight output (they must be in sync).
If user specified a version, use it. Otherwise, determine from scope:
- Features added since last release? → bump minor
- Only fixes? → bump patch

### Step 2: Gather Changes

```bash
# Primary: find last version tag (most reliable)
git describe --tags --abbrev=0

# Fallback: find last version bump commit
git log --oneline --grep="bump version" -1

# List all commits since last release
git log <last-tag-or-hash>..HEAD --oneline
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

### Step 5: Bump Version in All 5 Files + Lockfiles

**First, update the source of truth:**
```bash
echo "X.Y.Z" > VERSION
```

Then edit each of the 4 package files. Use the Edit tool — do NOT do search-and-replace on the old version string globally (it might match dependency versions).

**After editing, regenerate lockfiles:**
```bash
# Update Cargo.lock (Cargo.toml changed)
cd desktop/src-tauri && cargo generate-lockfile 2>/dev/null || cargo check 2>/dev/null; cd ../..

# Update package-lock.json (package.json changed)
cd desktop && npm install --package-lock-only 2>/dev/null; cd ..
```

**Verification** — all 4 must show the new version:
```bash
grep -n "version" backend/pyproject.toml | head -1
grep -n "version" desktop/package.json | head -1
grep -n "version" desktop/src-tauri/Cargo.toml | head -1
grep -n "version" desktop/src-tauri/tauri.conf.json | head -1
```

### Step 6: Commit + Push

All work stays on `main`. No feature branch.

```bash
git add VERSION CHANGELOG.md backend/config.py backend/pyproject.toml \
  desktop/package.json desktop/package-lock.json \
  desktop/src-tauri/Cargo.toml desktop/src-tauri/Cargo.lock \
  desktop/src-tauri/tauri.conf.json
git commit -m "chore: bump version to X.Y.Z, update CHANGELOG"
git push
```

### Step 7: Wait for CI

Push to main triggers the CI workflow (backend + frontend + version-check).
**Wait for all 3 checks to pass before tagging.**

```bash
# Watch CI status (auto-refreshes)
gh run watch --exit-status

# Or check manually
gh run list --branch main --limit 1
```

If CI fails → fix on main, push again, wait for green. Do NOT tag with red CI.

### Step 8: Tag and Release

Only after CI is green:

```bash
git tag vX.Y.Z -m "vX.Y.Z: <one-line summary of highlights>"
git push origin vX.Y.Z
```

### Step 9: Create GitHub Release

> **Note:** Tag push triggers the unified Release workflow (`release.yml`) which builds DMG + Windows + Hive tar.gz + checksums automatically. The CI creates a **draft** release with all artifacts. You can either:
> - **Wait for CI** (~15 min) — then edit the draft release to add notes
> - **Create manually** — if you need to ship before CI finishes (artifacts uploaded later)

**Option A: Edit the CI-created draft** (preferred)
```bash
# Wait for CI to finish, then add release notes to the draft
gh release edit vX.Y.Z --draft=false --notes "$(cat <<'EOF'
...release notes...
EOF
)"
```

**Option B: Create manually** (if CI hasn't run yet)
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

**By the numbers:** <N>+ commits · <N>K+ backend LOC · <N>+ skills · <N>+ tests · <N>+ backend modules · <N>+ React components

**Full changelog:** [CHANGELOG.md](https://github.com/xg-gh-25/SwarmAI/blob/main/CHANGELOG.md)
EOF
)"
```

The release notes should be a condensed version of the CHANGELOG — highlights at top, then Added/Fixed/Changed sections. Use the same stat-gathering commands from Step 4 for "By the numbers".

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

1. Steps 0-2: Pre-flight, determine version, gather fixes
2. Step 3: CHANGELOG (can be shorter — just list fixes)
3. Step 4: README — **still required**: add patch row to "Recent Releases" table, update "By the numbers" if stats changed. "What's New" section stays unchanged (patches don't change highlights)
4. Steps 5-7: Bump + commit + push
5. Steps 8-9: Tag + release (release notes = just the fixes)

## Rollback

If something goes wrong mid-release:

```bash
# Tag pushed but gh release failed → just retry Step 9
gh release create vX.Y.Z ...

# Tag is wrong (wrong commit, wrong version) → delete and redo
git tag -d vX.Y.Z                    # delete local
git push origin :refs/tags/vX.Y.Z   # delete remote
# Then redo Steps 8-9

# Release created but content is wrong → edit in place
gh release edit vX.Y.Z --title "..." --notes "..."

# Everything is wrong → delete release + tag, revert commit
gh release delete vX.Y.Z --yes
git tag -d vX.Y.Z
git push origin :refs/tags/vX.Y.Z
git revert HEAD   # revert the version bump commit
git push
```

## Pre-Release Checklist (informational — not enforced by this skill)

These are recommended before cutting a release, but this skill does NOT block on them:
- [ ] All tests pass (`cd backend && python -m pytest --timeout=60`)
- [ ] Desktop build succeeds (`./prod.sh build` — PyInstaller + 38 capability checks)
- [ ] Hive package verified (`./prod.sh release-hive` — tar.gz + 25-point verify)
- [ ] No uncommitted changes in working tree (Step 0 checks this)

If user wants to skip — their call. This skill only handles versioning.

> **Unified release shortcut:** `./prod.sh release-all` runs Desktop build + Hive package + verification + offers to create GitHub Release — all in one command. Use this instead of manual Steps 8-9 when you want both artifacts.
