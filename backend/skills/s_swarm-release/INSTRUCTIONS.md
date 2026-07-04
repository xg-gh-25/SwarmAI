# Swarm Release — Instructions

Full SwarmAI release pipeline: from pre-flight to GitHub Release.

## Co-Pilot Model

This skill uses **human-in-the-loop** for long builds. Agent handles all fast
steps (preflight, version bump, smoke, publish); user runs builds in their own
terminal where they can't be killed by session management.

```
Agent: PREFLIGHT → BUMP → USER: prod.sh build → Agent: SMOKE →
USER: TAURI BUILD → Agent: VERIFY DMG → PUSH → [CI GREEN GATE] → PUBLISH
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

- ❌ Run `build-backend.sh` or `prod.sh build` directly in session (exit 137)
- ❌ Run `npm run tauri build` directly in session (exit 137)
- ❌ Run upgrade/deploy endpoint from session (session dies)
- ❌ Use `nohup`, `run_in_background`, or TaskRunner for builds
- ❌ Retry failed builds without user involvement
- ❌ Propose optional work mid-release ("要不要加 hook?")
- ❌ Report stage pass and wait for "继续"
- ❌ Hand-assemble rsync/launchctl deploy commands (prod.sh handles it completely)

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
cd $SWARMAI_ROOT

# 1. Tree must be clean
git status --porcelain

# 2. On main branch
git branch --show-current

# 3. Commit count — INFORMATIONAL ONLY (for release-notes scope), NOT a gate.
#    Release readiness = R6 quality gate (Build + Tests green + verified in the
#    running system), NOT commit count. AGENT.md R11: "There is no commit-count
#    threshold: a batch is shippable when it's qualified, however many commits it
#    took." R6 lists "commit-count/volume → 'time to push'" as an anti-pattern.
#    Do NOT block/warn/ask on this number — it only shapes the release notes.
LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null)
COMMIT_COUNT=$(git rev-list --count ${LAST_TAG}..HEAD)

# 4. Current version
cat VERSION

# 5. CI status
gh run list --branch main --limit 3 --json status,conclusion,name

# 6. Eval gate (git-bound) — RELEASE-only. Enforces "本地必须跑完 Eval 才放行"
#    at the ship boundary (NOT on build, which is high-frequency).
(cd backend && python scripts/ci_eval_gate.py ; echo "gate_rc=$?")
```

**Pass criteria:**
- Clean tree
- **On main branch (BLOCK if not — releases MUST ship from main)**
- **R6 quality gate satisfied — the ONLY scope gate: local Build + affected Tests green, changes verified in the running system. Commit count is NOT a gate (R11).**
- CI green (formal post-push confirmation of an already-qualified HEAD, not the verification venue — R6)
- **Eval gate (`gate_rc`): rc=0 fresh+green → PASS; rc=1 stale/red → BLOCK (re-run `python backend/scripts/eval_runner.py run`); rc=2 no report → WARN (interactive: ask; non-TTY/CI: fail-closed → re-run eval or set `SWARMAI_SKIP_EVAL_GATE=1`). The shared `_eval_gate` in `prod.sh` enforces this on `release`/`release-all`/`release-hive` (1e); the raw `gate_rc` you print here is advisory — if it's `1`, BLOCK now rather than handing off.**

**Report:**
```
Stage 1 PREFLIGHT: PASS
  Branch: main
  Current version: X.Y.Z
  Commits since vX.Y.Z: N (informational — release-notes scope only, NOT a gate)
  R6 gate: Build ✓ / Tests ✓ / verified-in-running-system ✓
  CI: 3/3 green
  Eval gate: PASS (rc=0) | WARN no-report (rc=2) | BLOCK stale/red (rc=1)
```

If not on main: **BLOCK** — switch to main first.
If dirty tree: commit housekeeping changes and continue.
If the R6 gate is NOT satisfied (Build/Tests not run-green, or HEAD unverified in the
  running system): **BLOCK** — qualify it first. This is the real gate. Commit count
  never blocks (R11) — a qualified 84-commit batch ships; an unqualified 3-commit one does not.
If `gate_rc=1`: **BLOCK** — re-run eval before releasing.

---

## Stage 1.5: CONVERGENCE (Agent, 5s, optional)

```bash
cd $SWARMAI_ROOT
python backend/scripts/update_convergence.py
```

If data changed → commit. If script fails → skip (non-blocking).

---

## Stage 2: VERSION BUMP (Agent, 30s)

Determine bump type (minor=features, patch=fixes only, major=breaking).

Update VERSION file, then run sync-version.sh to propagate to all targets + lockfiles:

```bash
# 1. Edit VERSION file with new version
# 2. Sync to all 4 targets + regenerate lockfiles:
cd $SWARMAI_ROOT && bash scripts/sync-version.sh
```

This updates:
1. `VERSION` (manual edit)
2. `backend/pyproject.toml` (sync-version.sh)
3. `desktop/package.json` (sync-version.sh)
4. `desktop/src-tauri/Cargo.toml` (sync-version.sh)
5. `desktop/src-tauri/tauri.conf.json` (sync-version.sh)
6. `Cargo.lock` (cargo check)
7. `package-lock.json` (npm install)

Commit ALL changed files: `release: vX.Y.Z`

**Report:**
```
Stage 2 VERSION BUMP: PASS
  Old: X.Y.Z → New: X.Y.Z (minor|patch)
  Files updated: 7/7 synced (5 version + 2 lockfiles)
```

---

## Stage 3: BACKEND BUILD + DEPLOY (User, 2-5 min)

**WHY build+deploy precedes Tauri build:**
1. If the new binary can't start → building DMG is wasted effort (3-5 min saved)
2. DMG is the final distribution artifact — only produced after backend is confirmed working
3. Tauri build bundles the binary already proven on this machine

`./prod.sh build` does everything in one shot:
- PyInstaller build
- verify_build.py (46 checks) — fails fast if broken
- rsync to `~/.swarm-ai/daemon/` + write `.version` + copy resources + chmod
- SIGKILL old daemon → KeepAlive restarts with new binary

Hand off to user:

```
⏸️ YOUR TURN — 请在终端跑:
┌─────────────────────────────────────────────────────
│ cd ~/Desktop/SwarmAI-Workspace/swarmai && ./prod.sh build
└─────────────────────────────────────────────────────
完成后说 "好了" 或贴最后几行 output。
```

Wait for user confirmation.

---

## Stage 4: SMOKE (Agent, 15s)

Verify daemon is running the new version:

```bash
# Wait for daemon stabilization
sleep 5

# Port check
nc -z 127.0.0.1 18321 || { echo "FAIL: daemon port not open"; exit 1; }

# Health + version verification
curl -sf http://127.0.0.1:18321/health | python3 -c "
import sys, json
d = json.load(sys.stdin)
v = d.get('version', '?')
print(f'Status: {d[\"status\"]}')
print(f'Version: {v}')
print(f'SDK: {d.get(\"sdk_version\", \"?\")}')
print(f'DB: {d.get(\"db_healthy\", \"?\")}')
assert d['status'] == 'healthy', 'NOT HEALTHY'
"
```

**Pass:** healthy + version matches new release version + JSON (not HTML).
**Fail:** Daemon not starting or version mismatch → STOP. Do NOT proceed to Tauri build.

**Report:**
```
Stage 4 SMOKE: PASS
  Status: healthy
  Version: X.Y.Z (matches VERSION file ✓)
```

---

## Stage 5: TAURI BUILD (User, 3-5 min)

**NON-SKIPPABLE** unless user explicitly says "skip DMG" or "不用打包桌面".

Only reached after Stage 4 confirms the new backend binary runs correctly.

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

## Stage 6: VERIFY DMG (Agent, 5s)

```bash
cd $SWARMAI_ROOT/desktop
# DMG might be in bundle/dmg/ or bundle/macos/ depending on Tauri version
find src-tauri/target/release/bundle -name "*.dmg" -newer src-tauri/Cargo.toml | head -3
```

**Pass:** DMG exists, >5MB. (Baseline: v1.18.0–v1.21.0 DMGs are all ~10.3MB.
The PyInstaller backend ships separately via daemon auto-install — it is NOT
bundled in the DMG, which is just the Rust Tauri shell. So >5MB is a broken/empty-
build floor, not a "backend is included" check. Do NOT raise this toward 30MB.)
**Fail:** No DMG found, or DMG <5MB → ask user to check build output.

---

## Stage 7: PUBLISH (Agent, ~5-10min incl. CI gate)

**R6 ORDER (non-negotiable):** push → **wait for CI green** → THEN publish. The push is
FORMAL confirmation of an already-qualified HEAD; CI is the barrier BEFORE the Release
object exists, never after. Splitting 7 into 7a→7b→7c is the structural fix for the
v1.24.0 miss (published on HEAD `2d4a2ff2`, CI then went red on 3 stale artifacts —
IMPROVEMENT.md 2026-07-04). The GitHub Release (tag + DMG) is the star/download-
side-effect artifact — it MUST NOT be created until 7b is green.

### Stage 7a: PUSH (Agent, 30s)

```bash
cd $SWARMAI_ROOT
VERSION=$(cat VERSION)

# Push commits (MUST succeed — no silent swallowing)
git push origin main

# Tag (safe to push before CI — a tag has no star/download side effect;
# if 7b goes red, fix forward → HEAD advances → re-tag on the green HEAD)
git tag -a "v${VERSION}" -m "Release v${VERSION}" 2>/dev/null || true
git push origin "v${VERSION}"
```

If `git push` fails (auth, network): **STOP and report** — do NOT proceed.

### Stage 7b: CI GREEN GATE (Agent, blocking, ~3-8min wall-clock) — the ONLY thing that unlocks 7c

**This gate is now CODE-ENFORCED** (run_9fec1fb1): `release-gate --poll` is the only thing
that writes the CI-green marker, and the `release_publish_guard` PreToolUse hook DENIES
`gh release create` unless that marker's `head_sha` == the current HEAD. You cannot skip
to 7c — the hook blocks it. 7b is how you EARN the marker.

Poll via the CLI (one bounded call per invocation — **do NOT use `gh run watch` or wrap a
`sleep`-loop in one bash call**; both are multi-minute single foreground calls that get
silently killed by the foreground timeout). The AGENT drives the loop:

**Each poll = this single call (returns in ~2-3s):**
```bash
cd $SWARMAI_ROOT && python backend/scripts/artifact_cli.py release-gate --poll --project SwarmAI
```

**Read the JSON `state` + exit code:**
- `state=PASS` (exit 0) → CI green on HEAD, **marker written** → proceed to 7c.
- `state=WAIT` (exit 3) → CI not done / not registered yet. Wait ~30-45s (a short
  standalone `sleep 40` bash call is fine — bounded, not a 10-min loop), then re-poll.
  Give up after ~12-15 polls (~8-10min) → HALT + report.
- `state=BLOCK` (exit 1) → CI is RED. Run `gh run view <run_id> --json jobs` to list
  failing jobs, diagnose + fix. A release batch's own commits often leave test/scan
  artifacts stale — md5-intent (`usedforsecurity=False`), camelCase-mapper shape
  (`toEqual`), hand-built arg stubs (missing new attr) — the highest-probability red
  source; sweep those first. Fix → push → **re-poll on the new HEAD** (the old marker,
  if any, no longer matches the new HEAD → still fail-closed). Do NOT create the Release.

**No skip flag — code-enforced, not just runbook.** `release_publish_guard` blocks
`gh release create` whenever the marker is absent / stale / HEAD-mismatched. Publishing
before CI green is the exact "skip verification" hole this closes (CLASS A). A legit
manual re-publish (e.g. re-uploading an asset to an already-CI-green tag) can set
`SWARM_RELEASE_GATE_FORCE=1` — deliberate + logged, never the default.

### Stage 7c: PUBLISH RELEASE (Agent, 30s) — reached ONLY after 7b is green

```bash
cd $SWARMAI_ROOT
VERSION=$(cat VERSION)

# Find the DMG
DMG=$(find desktop/src-tauri/target/release/bundle -name "*.dmg" -newer desktop/src-tauri/Cargo.toml | sort -t. -k1,1 | tail -1)

# Create GitHub Release with DMG (CI is green — this HEAD is qualified)
gh release create "v${VERSION}" "$DMG" \
  --title "v${VERSION}" \
  --notes "<release notes>"

# Verify release actually exists
gh release view "v${VERSION}" --json tagName,url

# Consume the CI-green marker (one publish = one marker; prevents a stale marker
# from authorizing a future accidental publish on the same HEAD)
python backend/scripts/artifact_cli.py release-gate --clear --project SwarmAI
```

Release notes: summarize commits since last tag, grouped by type (feat/fix/improve/content).

**Report:**
```
Stage 7 PUBLISH: PASS
  Tag: vX.Y.Z
  CI gate: GREEN (HEAD <sha>, run <id>)
  Release: https://github.com/xg-gh-25/SwarmAI/releases/tag/vX.Y.Z
  DMG: uploaded
```

---

## Final Summary

```
RELEASE COMPLETE ✅ vX.Y.Z
  Commits: N (since vPREV)
  Backend: verified (prod.sh build passed)
  DMG: XX MB
  Smoke: healthy, correct version
  Published: GitHub Release
```

---

## Quick Reference

| Stage | Who | Duration | Can fail? |
|-------|-----|----------|-----------|
| 0 Guard | Agent | instant | Abort |
| 1 Preflight | Agent | 30s | Block if R6 gate unmet or not on main |
| 1.5 Convergence | Agent | 5s | Non-blocking |
| 2 Version bump | Agent | 30s | No |
| 3 Build+Deploy | **User** | 2-5 min | Yes — blocks release |
| 4 Smoke | Agent | 15s | Yes — blocks release |
| 5 Tauri build | **User** | 3-5 min | Yes |
| 6 Verify DMG | Agent | 5s | Yes — blocks release |
| 7a Push | Agent | 30s | Yes — stop if push fails |
| 7b CI green gate | Agent | 3-8 min (blocking) | Yes — BLOCK publish if CI red; NO skip flag |
| 7c Publish release | Agent | 30s | Rare — reached only after 7b green |

---

## Abort Conditions

At any stage, if failure occurs:
1. Report which stage failed with error details
2. Do NOT proceed to later stages
3. Do NOT retry automatically
4. If 7a push succeeded but 7c release-create failed: re-run 7c only (do NOT re-push).
5. If 7b CI gate is red: HALT, list failing jobs, fix forward → push → re-run 7b on the
   new HEAD. NEVER create the Release on a red HEAD, and NEVER skip 7b to publish faster.

---

## Why prod.sh build (not manual rsync)

`prod.sh build` calls `_deploy_daemon_binary()` from `scripts/daemon-lib.sh` which:
- Validates binary exists before deploy
- Uses `rsync -a --delete` (atomic, incremental)
- Writes `.version` file in canonical format: `{semver} {git_hash} {timestamp}`
- Copies `desktop/resources/` to daemon resources dir
- Sets correct permissions (`chmod +x`)
- SIGKILL + KeepAlive restart (handles SSE streams blocking graceful shutdown)

Hand-assembling rsync/kickstart in skill docs = guaranteed drift when daemon-lib.sh
evolves. **Single source of truth: `prod.sh build`.**

---

## Relationship to Existing Skills

```
s_release        → version bump + tag only (NO build, NO package)
s_swarm-release  → FULL cycle: bump + build + deploy + tauri + publish (THIS SKILL)
s_swarm-build    → binary build + deploy only (no version/tag/publish)
```
