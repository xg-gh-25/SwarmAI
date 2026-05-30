# Swarm Release — Instructions

Full SwarmAI release pipeline: from pre-flight to GitHub Release.

## Co-Pilot Model

This skill uses **human-in-the-loop** for long builds. Agent handles all fast
steps (preflight, version bump, verify, smoke, publish); user runs builds in
their own terminal where they can't be killed by session management.

```
Agent: PREFLIGHT → BUMP → USER: BACKEND BUILD → Agent: VERIFY →
USER: DEPLOY → Agent: SMOKE → USER: TAURI BUILD →
Agent: VERIFY DMG → PUBLISH
```

---

## Execution Rules

### Handoff Format

When handing off to user, ALWAYS use this exact format:

```
⏸️ YOUR TURN — 请在终端跑:
┌─────────────────────────────────────────────────────
│ <command>
└─────────────────────────────────────────────────────
完成后说 "好了" 或贴最后几行 output。
```

### Momentum

- Agent stages: execute immediately, no pause, no "ready to continue?"
- User stages: hand off with clear command, then WAIT for user response.
- When user says "好了" / "done" / "跑完了": proceed to next stage immediately.
- Mid-flow fixes (dirty tree, etc.): fix and CONTINUE. No asking.
- The user said "release" once. That's the only approval needed.

### What NOT to Do

- ❌ Run `build-backend.sh` directly in session (exit 137)
- ❌ Run `npm run tauri build` directly in session (exit 137)
- ❌ Run upgrade/deploy endpoint from session (session dies)
- ❌ Use `nohup`, `run_in_background`, or TaskRunner for builds
- ❌ Retry failed builds without user involvement
- ❌ Propose optional work mid-release ("要不要加 hook?")
- ❌ Report stage pass and wait for "继续"

---

## Stage 0: PROJECT GUARD (blocking)

```
Check:
  - Active project == SwarmAI? If not → ABORT.
  - Any active pipeline runs? If yes → WARN.
```

---

## Stage 1: PREFLIGHT (Agent, 30s)

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai

# 1. Tree must be clean
git status --porcelain

# 2. On main branch
git branch --show-current

# 3. Scope gate
LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null)
COMMIT_COUNT=$(git rev-list --count ${LAST_TAG}..HEAD)

# 4. Current version
cat VERSION

# 5. CI status
gh run list --branch main --limit 3 --json status,conclusion,name
```

**Pass criteria:**
- Clean tree, on main, ≤20 commits (or user approved >20, >40 = BLOCK)
- CI green

**Report:**
```
Stage 1 PREFLIGHT: PASS
  Branch: main
  Current version: X.Y.Z
  Commits since vX.Y.Z: N
  CI: 3/3 green
```

If dirty tree: commit housekeeping changes and continue.
If >20 ≤40 commits: ask for sign-off.
If >40: BLOCK — must split.

---

## Stage 1.5: CONVERGENCE (Agent, 5s, optional)

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai
python backend/scripts/update_convergence.py
```

If data changed → commit. If script fails → skip (non-blocking).

---

## Stage 2: VERSION BUMP (Agent, 30s)

Determine bump type (minor=features, patch=fixes only, major=breaking).

Update all 5 files:
1. `VERSION`
2. `backend/pyproject.toml`
3. `desktop/package.json`
4. `desktop/src-tauri/Cargo.toml`
5. `desktop/src-tauri/tauri.conf.json`

Commit: `release: vX.Y.Z`

**Report:**
```
Stage 2 VERSION BUMP: PASS
  Old: X.Y.Z → New: X.Y.Z (minor|patch)
  Files updated: 5/5 synced
```

---

## Stage 3: BACKEND BUILD (User, 2-5 min)

Hand off to user:

```
⏸️ YOUR TURN — 请在终端跑:
┌─────────────────────────────────────────────────────
│ cd ~/Desktop/SwarmAI-Workspace/swarmai && bash desktop/scripts/build-backend.sh
└─────────────────────────────────────────────────────
完成后说 "好了" 或贴最后几行 output。
```

Wait for user confirmation.

---

## Stage 4: VERIFY BACKEND (Agent, 10s)

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai && python3 desktop/scripts/verify_build.py
```

**Pass:** All checks pass (46/46).
**Fail:** Report which checks failed, STOP.

---

## Stage 5: DEPLOY + SMOKE (User deploy, Agent verify)

**WHY this precedes Tauri build:** The daemon must be running the new binary
before we build the DMG. Rationale:
1. If the new binary can't start or fails health checks, building the DMG is
   wasted effort (3-5 min saved on failure).
2. The DMG is the final distribution artifact — it should only be produced after
   confirming the backend actually runs correctly.
3. Tauri build bundles the binary that's already proven working on this machine.

Hand off deploy to user:

```
⏸️ YOUR TURN — 请在终端跑:
┌─────────────────────────────────────────────────────
│ cp ~/Desktop/SwarmAI-Workspace/swarmai/desktop/src-tauri/binaries/swarm-backend-aarch64-apple-darwin ~/.swarm-ai/daemon/swarm-backend && launchctl kickstart -k gui/$(id -u)/com.swarmai.daemon
└─────────────────────────────────────────────────────
等 10-15 秒 daemon 重启后说 "好了"。
```

When user confirms, agent runs smoke:

```bash
# Verify daemon is listening
nc -z 127.0.0.1 18321 || { echo "FAIL: daemon port not open"; exit 1; }

# Verify health + version
curl -sf http://127.0.0.1:18321/health | python3 -c "
import sys, json
d = json.load(sys.stdin)
v = d.get('version', '?')
print(f'Status: {d[\"status\"]}')
print(f'Version: {v}')
print(f'DB: {d.get(\"db_healthy\", \"?\")}')
assert d['status'] == 'healthy', 'NOT HEALTHY'
"
```

**Pass:** healthy + version matches new release + JSON (not HTML).
**Fail:** Daemon not starting or version mismatch → STOP. Do NOT proceed to Tauri build.

---

## Stage 6: TAURI BUILD (User, 3-5 min)

**NON-SKIPPABLE** unless user explicitly says "skip DMG" or "不用打包桌面".

Only reached after Stage 5 confirms the new backend binary runs correctly.

Hand off to user:

```
⏸️ YOUR TURN — 请在终端跑:
┌─────────────────────────────────────────────────────
│ cd ~/Desktop/SwarmAI-Workspace/swarmai/desktop && npm run tauri build
└─────────────────────────────────────────────────────
完成后说 "好了"。
```

Wait for user confirmation.

---

## Stage 7: VERIFY DMG (Agent, 5s)

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/desktop
# DMG might be in bundle/dmg/ or bundle/macos/ depending on Tauri version
find src-tauri/target/release/bundle -name "*.dmg" -newer src-tauri/Cargo.toml | head -3
```

**Pass:** DMG exists, >30MB.
**Fail:** No DMG found → ask user to check build output.

---

## Stage 8: PUBLISH (Agent, 30s)

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai
VERSION=$(cat VERSION)

# Push if needed
git push origin main 2>/dev/null || true

# Tag (skip if already exists)
git tag -a "v${VERSION}" -m "Release v${VERSION}" 2>/dev/null || true
git push origin "v${VERSION}" 2>/dev/null || true

# Find the DMG
DMG=$(find desktop/src-tauri/target/release/bundle -name "*.dmg" -newer desktop/src-tauri/Cargo.toml | sort -t. -k1,1 | tail -1)

# Create GitHub Release with DMG
gh release create "v${VERSION}" "$DMG" \
  --title "v${VERSION}" \
  --notes "<release notes>"
```

Release notes: summarize commits since last tag, grouped by type (feat/fix/improve/content).

**Report:**
```
Stage 8 PUBLISH: PASS
  Tag: vX.Y.Z
  Release: https://github.com/xg-gh-25/SwarmAI/releases/tag/vX.Y.Z
  DMG: uploaded
```

---

## Final Summary

```
RELEASE COMPLETE ✅ vX.Y.Z
  Commits: N (since vPREV)
  Backend: 46/46 verified
  DMG: XX MB
  Smoke: healthy, correct version
  Published: GitHub Release
```

---

## Quick Reference

| Stage | Who | Duration | Can fail? |
|-------|-----|----------|-----------|
| 0 Guard | Agent | instant | Abort |
| 1 Preflight | Agent | 30s | Block if >40 commits |
| 1.5 Convergence | Agent | 5s | Non-blocking |
| 2 Version bump | Agent | 30s | No |
| 3 Backend build | **User** | 2-5 min | Yes |
| 4 Verify backend | Agent | 10s | Yes — blocks release |
| 5 Tauri build | **User** | 3-5 min | Yes |
| 6 Verify DMG | Agent | 5s | Yes — blocks release |
| 7 Deploy + Smoke | **User** + Agent | 15-40s | Yes |
| 8 Publish | Agent | 30s | Rare |

---

## Abort Conditions

At any stage, if failure occurs:
1. Report which stage failed with error details
2. Do NOT proceed to later stages
3. Do NOT retry automatically
4. If Stage 8 push succeeded but release failed: re-run Stage 8 only.

---

## Relationship to Existing Skills

```
s_release        → version bump + tag only (NO build, NO package)
s_swarm-release  → FULL cycle (co-pilot: agent + user)
s_swarm-build    → binary build + deploy only (no version/tag/publish)
```
