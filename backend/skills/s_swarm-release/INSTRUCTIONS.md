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

**🚨 MEMORY REQUIREMENT:** PyInstaller needs ~2GB free RAM. Exit 137 = macOS
jetsam OOM kill. Check free memory before running. If < 4GB free, warn user
to close apps first. Run with `run_in_background: true` to avoid blocking.

**Execution method:**
1. Check free memory (vm_stat). If < 4GB → warn, suggest freeing memory.
2. Run build in background:
   ```bash
   cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/desktop/scripts && bash build-backend.sh
   ```
   With `timeout: 600000` and `run_in_background: true`.
   **🚨 NEVER pipe through `| tail`** — causes buffering hang.
3. When complete, verify:
   ```bash
   ls -la desktop/src-tauri/binaries/python-backend-aarch64-apple-darwin
   python3 desktop/scripts/verify_build.py
   ```

**On exit 137:** Do NOT retry. Report OOM + suggest freeing memory.

**Pass criteria:**
- User confirms build completed
- Binary exists at expected output path
- verify_build.py passes all checks

**Report format:**
```
Stage 3 BUILD: PASS (user-built)
  Binary: python-backend-aarch64-apple-darwin (48.2 MB)
  Verify: 38/38 checks passed
```

---

## Stage 4: PACKAGE (3-5 min)

Build the desktop application (Tauri → DMG on macOS).

**🚨 MEMORY REQUIREMENT:** Tauri/Rust compilation needs ~2-3GB free RAM.
Same OOM guard as Stage 3. Run in background with 600s timeout.

**Execution method:**
1. Check free memory. If < 5GB → warn user.
2. Run in background:
   ```bash
   cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/desktop && npm install && npm run tauri build
   ```
   With `timeout: 600000` and `run_in_background: true`.
   **🚨 NEVER pipe through `| tail`** — causes buffering hang.
3. Verify output:
   ```bash
   ls -la src-tauri/target/release/bundle/dmg/SwarmAI_*.dmg
   ```

**On exit 137:** Do NOT retry. Report OOM + suggest freeing memory.

**Output locations:**
- macOS DMG: `src-tauri/target/release/bundle/dmg/SwarmAI_<version>_aarch64.dmg`
- macOS app: `src-tauri/target/release/bundle/macos/SwarmAI.app`

**Pass criteria:**
- DMG file exists at expected path
- File size > 50MB (sanity check)

**Report format:**
```
Stage 4 PACKAGE: PASS (user-built)
  DMG: SwarmAI_1.11.0_aarch64.dmg (156 MB)
  App: SwarmAI.app
```

**On failure:** Check Rust/Cargo errors, frontend build errors, or code signing issues.

---

## Stage 5: SMOKE TEST (90s max)

Deploy new binary to daemon and verify health with correct version.

**CRITICAL: SIGKILL + bootout + rsync + bootstrap.** Never use SIGTERM (SSE streams
block indefinitely). Never rely on KeepAlive (it restarts the OLD binary before rsync
finishes → race condition → zlib corruption or I/O hang). See commit cfa1564 for
full rationale.

**Preferred method — API endpoint (if daemon is healthy):**

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai

# Try the upgrade endpoint first — it handles the full sequence internally
HTTP_CODE=$(curl -s -o /tmp/upgrade_response.json -w "%{http_code}" -X POST http://127.0.0.1:18321/api/system/upgrade 2>/dev/null)
echo "Upgrade endpoint: $HTTP_CODE"
if [ "$HTTP_CODE" = "202" ]; then
  cat /tmp/upgrade_response.json | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))"
  echo "Upgrade spawned. Waiting for restart (max 40s)..."
  sleep 5
  for i in $(seq 1 18); do
    HEALTH=$(curl -sf http://127.0.0.1:18321/health 2>/dev/null) && {
      echo "=== Daemon HEALTHY after $((i*2+5))s ==="
      echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Version: {d.get(\"version\",\"?\")}')"
      break
    }
    sleep 2
  done
fi
# If 404/000/5xx → use manual fallback below
```

**Manual fallback (if endpoint unavailable or daemon unhealthy):**

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai
GUI_TARGET="gui/$(id -u)/com.swarmai.backend"
DAEMON_DIR="${HOME}/.swarm-ai/daemon"
BUNDLE_DIR="desktop/src-tauri/binaries/python-backend-aarch64-apple-darwin"
PLIST_SRC="desktop/src-tauri/resources/com.swarmai.backend.plist"
PLIST_DST="${HOME}/Library/LaunchAgents/com.swarmai.backend.plist"

# Step 1: SIGKILL (instant death — SSE cannot block SIGKILL)
launchctl kill SIGKILL "$GUI_TARGET" 2>/dev/null || true
sleep 1

# Step 2: bootout (deregister — process already dead so returns instantly)
# This disables KeepAlive so nothing restarts during rsync
launchctl bootout "$GUI_TARGET" 2>/dev/null || true
sleep 1

# Step 3: Confirm port is free (should be — process is dead)
if nc -z 127.0.0.1 18321 2>/dev/null; then
  echo "WARN: Port still held after SIGKILL+bootout. Waiting 5s..."
  sleep 5
  if nc -z 127.0.0.1 18321 2>/dev/null; then
    echo "FAIL: Port 18321 still held. Orphan process. Debug manually."
    exit 1
  fi
fi

# Step 4: Deploy (safe — no live process, no KeepAlive)
rsync -a --delete "${BUNDLE_DIR}/" "${DAEMON_DIR}/"
chmod +x "${DAEMON_DIR}/python-backend"

# Step 5: Version marker
APP_VER=$(cat VERSION)
echo "${APP_VER} $(git rev-parse --short HEAD) $(date '+%Y-%m-%d %H:%M:%S')" > "${DAEMON_DIR}/.version"
echo "Deployed: $(cat ${DAEMON_DIR}/.version)"

# Step 6: Deploy fresh plist (so ExitTimeOut=15 is active for future restarts)
if [ -f "$PLIST_SRC" ]; then
  cp "$PLIST_SRC" "$PLIST_DST"
fi

# Step 7: bootstrap with retry (re-register + start new binary)
BOOTSTRAP_OK=0
for attempt in 1 2 3; do
  launchctl bootstrap "gui/$(id -u)" "$PLIST_DST" 2>/dev/null && { BOOTSTRAP_OK=1; break; }
  echo "Bootstrap attempt $attempt failed, retrying in 2s..."
  sleep 2
done

if [ "$BOOTSTRAP_OK" -eq 0 ]; then
  echo "FAIL: bootstrap failed 3x. Service not registered."
  echo "Manual fix: launchctl bootstrap gui/$(id -u) $PLIST_DST"
  exit 1
fi

# Step 8: Health check (hard 40s timeout)
echo "Waiting for daemon startup (max 40s)..."
for i in $(seq 1 20); do
  HEALTH=$(curl -sf http://127.0.0.1:18321/health 2>/dev/null) && {
    echo "=== Daemon HEALTHY after $((i*2))s ==="
    echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Status: {d[\"status\"]}\n  Version: {d.get(\"version\",\"?\")}')"
    break
  }
  sleep 2
done

# Step 9: Semantic version check
DEPLOYED=$(awk '{print $1}' "${DAEMON_DIR}/.version")
echo "Deployed version: $DEPLOYED"
```

**🚨 NEVER:**
- Use SIGTERM on a daemon with SSE streams (blocks indefinitely)
- Rely on KeepAlive to restart after deploy (restarts OLD binary, races rsync)
- rsync while daemon is still running (zlib corruption from open file handles)
- Skip bootout before rsync (KeepAlive fires within 1-3s of SIGKILL)

**Pass criteria:**
- Health returns JSON with `status: healthy`
- Version matches deployed binary
- NOT returning HTML (this caught v1.9.0 regression)

**Report format:**
```
Stage 5 SMOKE TEST: PASS
  Health: JSON, status=healthy
  Version: 1.12.2 abc1234 (matches deployed)
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
