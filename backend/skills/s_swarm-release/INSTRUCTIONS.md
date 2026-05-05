# Swarm Release — Instructions

Full SwarmAI release pipeline: from pre-flight to GitHub Release. Orchestrates
version bumps, binary build, desktop packaging, smoke testing, and publishing.

## Stage 0: PROJECT GUARD (blocking)

```
Check:
  - Active project == SwarmAI? If not → ABORT.
  - Any active pipeline runs? If yes → WARN: "Pipeline run_xxx in progress.
    Releasing now may include unreviewed code."
```

---

## Stage 1: PREFLIGHT (30s)

Comprehensive readiness check before committing to a release.

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai

# 1. Tree must be clean
if [ -n "$(git status --porcelain)" ]; then
  echo "FAIL: Uncommitted changes. Commit or stash first."
  exit 1
fi

# 2. On main branch
BRANCH=$(git branch --show-current)
if [ "$BRANCH" != "main" ]; then
  echo "FAIL: Must be on main branch (currently: $BRANCH)"
  exit 1
fi

# 3. Scope gate (STEERING rule)
LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
if [ -n "$LAST_TAG" ]; then
  COMMIT_COUNT=$(git rev-list --count ${LAST_TAG}..HEAD)
  echo "Commits since $LAST_TAG: $COMMIT_COUNT"
  if [ "$COMMIT_COUNT" -gt 40 ]; then
    echo "BLOCK: >40 commits. MUST split into multiple releases."
    exit 1
  elif [ "$COMMIT_COUNT" -gt 20 ]; then
    echo "WARN: >20 commits. Requires explicit sign-off to proceed."
    echo "→ Ask user: 'Release with $COMMIT_COUNT commits? (scope acknowledged)'"
    exit 1
  fi
else
  echo "No previous tag found — first release"
fi

# 4. Current version
cat VERSION

# 5. CI status on HEAD
gh run list --branch main --limit 3 --json status,conclusion,name \
  | python3 -c "import sys,json; runs=json.load(sys.stdin); [print(f'  {r[\"name\"]}: {r[\"conclusion\"] or r[\"status\"]}') for r in runs]"
```

**Pass criteria:**
- Clean tree, on main, ≤20 commits (or user approved >20)
- CI most recent run is green (or user acknowledges)

**Report format:**
```
Stage 1 PREFLIGHT: PASS
  Branch: main
  Current version: 1.10.0
  Commits since v1.10.0: 12
  CI: 3/3 green
```

---

## Stage 2: VERSION BUMP (30s)

Determine new version and update all 5 version files.

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai

# Determine bump type from user input or commit analysis
# Minor: new features | Patch: bug fixes only | Major: breaking changes
# Default: minor (features present)

NEW_VERSION="<determined>"  # e.g., "1.11.0"

# Update all 5 files atomically
echo "$NEW_VERSION" > VERSION

# pyproject.toml
sed -i '' "s/^version = .*/version = \"$NEW_VERSION\"/" backend/pyproject.toml

# package.json
cd desktop && npm version $NEW_VERSION --no-git-tag-version && cd ..

# Cargo.toml
sed -i '' "s/^version = .*/version = \"$NEW_VERSION\"/" desktop/src-tauri/Cargo.toml

# tauri.conf.json
python3 -c "
import json, pathlib
p = pathlib.Path('desktop/src-tauri/tauri.conf.json')
conf = json.loads(p.read_text())
conf['version'] = '$NEW_VERSION'
p.write_text(json.dumps(conf, indent=2))
"

# Verify all match
echo "Verifying version sync..."
grep -h "version" VERSION backend/pyproject.toml desktop/package.json \
  desktop/src-tauri/Cargo.toml desktop/src-tauri/tauri.conf.json \
  | grep "$NEW_VERSION" | wc -l
# Should be 5
```

**Also update:**
- `CHANGELOG.md` — add new section with commit summary (Added/Fixed/Changed)
- Regenerate lockfiles if deps changed: `cd backend && uv lock`, `cd desktop && npm install`

**Report format:**
```
Stage 2 VERSION BUMP: PASS
  Old: 1.10.0 → New: 1.11.0 (minor)
  Files updated: 5/5 synced
  CHANGELOG: updated
```

---

## Stage 3: BUILD (3-5 min)

Invoke s_swarm-build for the binary. This is the longest stage.

```
→ Execute s_swarm-build stages 1-6 (preflight through health)
```

Or equivalently, run the build script directly:

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/desktop/scripts
bash build-backend.sh 2>&1 | tail -20
```

With `timeout: 600000` (10 min).

Then verify:
```bash
python3 desktop/scripts/verify_build.py
```

**Pass criteria:**
- Binary built successfully
- verify_build.py passes all checks

**Report format:**
```
Stage 3 BUILD: PASS (195s)
  Binary: python-backend-aarch64-apple-darwin (48.2 MB)
  Verify: 38/38 checks passed
```

---

## Stage 4: PACKAGE (3-5 min)

Build the desktop application (Tauri → DMG on macOS).

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/desktop

# Install frontend deps
npm install

# Build Tauri app
npm run tauri build 2>&1 | tail -20
```

With `timeout: 600000` (10 min).

**Output locations:**
- macOS DMG: `src-tauri/target/release/bundle/dmg/SwarmAI_<version>_aarch64.dmg`
- macOS app: `src-tauri/target/release/bundle/macos/SwarmAI.app`

**Pass criteria:**
- DMG file exists at expected path
- File size > 50MB (sanity check)

**Report format:**
```
Stage 4 PACKAGE: PASS (240s)
  DMG: SwarmAI_1.11.0_aarch64.dmg (156 MB)
  App: SwarmAI.app
```

**On failure:** Check Rust/Cargo errors, frontend build errors, or code signing issues.

---

## Stage 5: SMOKE TEST (30s)

Verify the daemon is running the NEW version correctly.

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai

# 1. Deploy new binary to daemon
SIDECAR="desktop/src-tauri/binaries/python-backend-aarch64-apple-darwin"
cp "$SIDECAR" ~/.swarm-ai/daemon/python-backend
chmod +x ~/.swarm-ai/daemon/python-backend
echo "$(git rev-parse --short HEAD) $(date -u +%Y-%m-%dT%H:%M:%SZ)" > ~/.swarm-ai/daemon/.version

# 2. Restart daemon
launchctl kickstart -k gui/$(id -u)/com.swarmai.backend
sleep 5

# 3. Health check — must return JSON, not HTML (prod.sh lesson)
HEALTH=$(curl -s http://localhost:18321/health)
echo "$HEALTH" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    assert d['status'] == 'healthy', f'Status: {d[\"status\"]}'
    print(f'Health: OK ({d[\"status\"]})')
    print(f'Version: {d.get(\"version\", \"unknown\")}')
except json.JSONDecodeError:
    print('FAIL: Health returned non-JSON (HTML?) — Caddy/proxy issue')
    sys.exit(1)
except AssertionError as e:
    print(f'FAIL: {e}')
    sys.exit(1)
"

# 4. Version semantic check
DEPLOYED=$(cat ~/.swarm-ai/daemon/.version | awk '{print $1}')
echo "Deployed commit: $DEPLOYED"
```

**Pass criteria:**
- Health returns JSON with `status: healthy`
- Version matches deployed commit
- NOT returning HTML (this caught v1.9.0 regression)

**Report format:**
```
Stage 5 SMOKE TEST: PASS
  Health: JSON, status=healthy
  Version: abc1234 (matches deployed)
```

---

## Stage 6: PUBLISH (60s)

Commit version bump, create tag, push, and create GitHub Release.

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai

# 1. Commit version bump + changelog
git add -A
git commit -m "release: v${NEW_VERSION}

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"

# 2. Push to main
git push origin main

# 3. Wait for CI (quick check)
echo "Waiting 30s for CI to start..."
sleep 30
gh run list --branch main --limit 1 --json status,conclusion

# 4. Create tag
git tag -a "v${NEW_VERSION}" -m "Release v${NEW_VERSION}"
git push origin "v${NEW_VERSION}"

# 5. Create GitHub Release
gh release create "v${NEW_VERSION}" \
  --title "v${NEW_VERSION}" \
  --notes "$(cat <<NOTES
## What's New

$(git log --oneline $(git describe --tags --abbrev=0 HEAD~1 2>/dev/null || echo "HEAD~20")..HEAD~1 | head -20)

## Install

Download the DMG from the assets below, or update via the in-app updater.
NOTES
)" \
  desktop/src-tauri/target/release/bundle/dmg/*.dmg 2>/dev/null || \
  echo "Note: DMG upload skipped (file may not exist or gh auth issue)"
```

**Pass criteria:**
- Commit pushed to main
- Tag created and pushed
- GitHub Release created (DMG upload is best-effort)

**Report format:**
```
Stage 6 PUBLISH: PASS
  Commit: abc1234 (release: v1.11.0)
  Tag: v1.11.0
  GitHub Release: https://github.com/xg-gh-25/SwarmAI/releases/tag/v1.11.0
  DMG uploaded: yes/no
```

---

## Final Summary

```
RELEASE COMPLETE ✅ v1.11.0
  Commits included: 12 (since v1.10.0)
  Binary: 48.2 MB, 38/38 verified
  DMG: 156 MB
  Smoke: healthy, correct version
  Published: GitHub Release + tag
  Duration: ~12 min total
```

---

## Relationship to Existing Skills

```
s_release        → version bump + changelog + tag only (NO build, NO package)
s_swarm-release  → FULL cycle (includes version bump + build + package + smoke + publish)
s_swarm-build    → binary build only (Stages 3 subset, no version/tag/publish)
```

**Migration:** Agent should use `s_swarm-release` for all release operations.
`s_release` remains available for version-bump-only scenarios (rare).

---

## Abort Conditions

At any stage, if failure occurs:
1. Report which stage failed with error details
2. Do NOT proceed to later stages
3. Do NOT retry automatically (fix the issue first)
4. If in Stage 6 (publish) and push succeeded but release failed:
   → The commit is already on main. Create the release manually or re-run Stage 6 only.
