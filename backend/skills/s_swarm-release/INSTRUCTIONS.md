# Swarm Release — Instructions

Full SwarmAI release pipeline: from pre-flight to GitHub Release.

## Co-Pilot Model

This skill uses **human-in-the-loop** for the one long *local* step (backend
build+deploy, which verifies the new binary boots before we ship). Everything
else is Agent-driven. **The shipped multi-platform artifacts are built by CI, not
locally** — pushing the `v*` tag triggers `.github/workflows/release.yml`, which
builds macOS/Windows/Hive on GitHub runners and creates a **draft** GitHub Release.
The only manual publish step is flipping that draft to published.

```
Agent: PREFLIGHT → BUMP → USER: prod.sh build (local backend deploy+verify) →
Agent: SMOKE → PUSH commit+tag  ─┬─▶ [tag push TRIGGERS release.yml:
                                 │    CI builds all platforms + creates DRAFT release]
Agent: [CI GREEN GATE] → verify DRAFT assets → FLIP draft→published
```

> **Local build ≠ release artifact.** `prod.sh build` (Stage 3) and any local
> `npm run tauri build` (Stage 5, now optional) only prove the app works on THIS
> machine — they do NOT produce what ships. The DMG/exe/msi/tar.gz on the GitHub
> Release are all CI-built by `release.yml`. `prod.sh build`/`release` never push a
> tag and never create a GitHub Release.

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

## Stage 5: LOCAL DESKTOP SMOKE (User, OPTIONAL, 3-5 min)

> **⚠️ This does NOT build the release artifact.** The shipped DMG/exe/msi are
> built by CI (`release.yml`) at Stage 7a's tag push — NOT here. This stage is an
> OPTIONAL local check that the desktop shell bundles + launches on this machine
> before you push the tag. **Skip it freely** if Stage 4 (backend smoke) passed and
> you trust the desktop shell — CI will build and verify the real artifacts anyway.

If you want the local desktop smoke, hand off to user:

```
⏸️ YOUR TURN (OPTIONAL) — 本地验证桌面壳,可跳过:
┌─────────────────────────────────────────────────────
│ cd ~/Desktop/SwarmAI-Workspace/swarmai/desktop && npm run tauri build
└─────────────────────────────────────────────────────
完成后说 "好了",或说 "跳过" 直接进 Stage 7。
```

The local DMG this produces is throwaway — it is NOT uploaded to the Release
(Stage 7 flips the CI-built draft, which already contains all-platform assets).

---

## Stage 6: (folded into Stage 7b) — CI builds + verifies the real artifacts

There is no separate local-DMG verification stage anymore. The artifacts that
ship are the CI-built ones attached to the draft Release; they are verified in
**Stage 7b** by inspecting `gh release view v${VERSION} --json assets` (all
platforms present, fresh, from the current HEAD) — NOT by a local `find *.dmg`.
A local `find` would only see the throwaway Stage-5 build and misses Windows/Hive
entirely. See Stage 7b.

---

## Stage 7: PUBLISH (Agent, ~5-10min incl. CI gate)

**R6 ORDER (non-negotiable):** push → **wait for CI green** → THEN publish (flip the
draft). The push is FORMAL confirmation of an already-qualified HEAD; CI is the barrier
BEFORE the Release goes public, never after. Splitting 7 into 7a→7b→7c is the structural
fix for the v1.24.0 miss (published on HEAD `2d4a2ff2`, CI then went red on 3 stale
artifacts — IMPROVEMENT.md 2026-07-04).

**How release actually happens (CI-driven — verified against `.github/workflows/release.yml`):**
pushing the `v*` tag (7a) TRIGGERS `release.yml`, which builds macOS/Windows/Hive on
GitHub runners and creates a **`draft: true`** GitHub Release with all-platform assets +
auto-generated notes. So by the time you reach 7c the Release object ALREADY EXISTS as a
draft — 7c is a **flip to published**, NOT a `gh release create`. The draft is
star/download-invisible until flipped, so it is safe for the draft to exist pre-CI-green;
what 7b gates is the **flip**.

### Stage 7a: PUSH commit + tag (Agent, 30s) — the tag push triggers CI's release build

```bash
cd $SWARMAI_ROOT
VERSION=$(cat VERSION)

# Push commits (MUST succeed — no silent swallowing)
git push origin main

# Push the tag — THIS is the release trigger. Pushing v${VERSION} fires
# release.yml, which builds all platforms + creates the DRAFT release.
# (Safe pre-CI-green: the release lands as a draft, no star/download side effect.
#  If 7b goes red, fix forward → HEAD advances → re-point the tag on the green HEAD;
#  the re-push re-triggers release.yml and refreshes the draft assets.)
git tag -a "v${VERSION}" -m "Release v${VERSION}" 2>/dev/null || true
git push origin "v${VERSION}"
```

If `git push` fails (auth, network): **STOP and report** — do NOT proceed.

> **Re-pointing an existing tag** (batch grew after the tag was cut): delete the remote
> tag first, then re-tag the new HEAD and push — `git push origin :refs/tags/v${VERSION}`
> then `git tag -f -a … && git push origin v${VERSION}`. Deleting a tag whose Release is
> still a **draft / 0-download** is safe (no star/download loss); a tag on a PUBLISHED
> release is star-sensitive (C041) — never delete/re-point that without XG sign-off.

### Stage 7b: CI GREEN GATE + verify draft assets (Agent, blocking, ~3-8min wall-clock)

Two things gate the flip to published: (1) CI green on the current HEAD, (2) the CI-built
draft carries all-platform assets from THIS HEAD.

**CI-green marker** (run_9fec1fb1): `release-gate --poll` is the only thing that writes
the CI-green marker; 7b is how you EARN it. Enforcement is the **explicit `release-gate
--verify` step 7c runs immediately before the publish** (run_d613bb27) — it authorizes
the flip IFF the marker attests the commit being released, fail-CLOSED (exit≠0 → do NOT
publish). This REPLACES the old `release_publish_guard` PreToolUse hook: a per-command
product-wide hook was the wrong layer for SwarmAI's OWN release discipline, so the check
moved into this release flow as a one-time gate. (`--verify` covers the same publish the
flip performs — `gh release edit --draft=false`; run 7c's `--verify` before it.)

> **✅ Tag-aware gate (run_81ad1cfe):** when you poll with `--ref v${VERSION}` (below),
> the gate verifies CI on the **commit the tag points at**, not the moving `main` HEAD,
> and records the tag in the marker. The `--verify` step then confirms the tag you're
> flipping derefs to that same CI-verified commit — LOCALLY, fail-CLOSED. This is what
> makes a **re-pointed tag** (tag commit ≠ branch tip) releasable: without `--ref` the
> gate would WAIT forever on unrelated parallel commits that landed on main after the
> tag was cut (observed live, v1.26.0). Always pass `--ref v${VERSION}` for a tag release.

**Verify the draft's assets (replaces the old local `find *.dmg`):**
```bash
cd $SWARMAI_ROOT && VERSION=$(cat VERSION)
gh release view "v${VERSION}" --json isDraft,targetCommitish,assets \
  --jq '{isDraft, target:.targetCommitish, assets:[.assets[].name]}'
```
**Pass (required):** `isDraft=true`, and assets include a `.dmg` + `hive-*.tar.gz` +
`checksums.txt`, freshly built for this HEAD. These are the load-bearing platforms —
`release.yml`'s publish job requires `build-macos OR build-hive` to succeed (release.yml
`if:` L219), so their absence means the CI build genuinely hasn't finished/failed.
**Warn (not fail):** missing `-setup.exe` / `.msi` (Windows). Windows is **best-effort** —
the publish job ships macOS+Hive even when `build-windows` fails (that's by design in
release.yml). So absent Windows assets → WARN + note it in the release, do NOT block the
flip. If you want Windows, re-run the failed `build-windows` job, don't hold the release.
**Fail:** missing `.dmg` AND `hive-*.tar.gz`, or all assets older than the current
release.yml run → the CI build hasn't finished (or fully failed); re-check the workflow
before flipping.

> **✅ Updater artifacts ARE now published — but the first post-fix release needs a one-time signature verification (fixed 2026-07-28).**
> `release.yml` now uploads the updater bundles + their `.sig` and generates
> `latest.json` (`.github/scripts/gen-latest-json.sh`, run in the publish job before
> the release is created). Expect these assets on the draft/published release:
> `SwarmAI.app.tar.gz` + `SwarmAI.app.tar.gz.sig` (macOS, load-bearing), the Windows
> `*.nsis.zip` + `.sig` (best-effort), and **`latest.json`**. The generator points each
> platform url at the updater BUNDLE (not the DMG/exe), reads `signature` from the `.sig`
> contents, keys platforms `darwin-aarch64` / `windows-x86_64`, fails loud if macOS built
> but its bundle/sig is missing, and gracefully omits a platform that didn't build (a
> hive-only release still ships).
> **⚠️ ONE-TIME CHECK on the first release after this fix (assumption A — key pairing):**
> auto-update only works if the CI secret `TAURI_SIGNING_PRIVATE_KEY` is the private key
> for the pubkey embedded in `tauri.conf.json` (`7B9CEDB5D3C58A4D`). That pairing is
> **unverifiable ahead of time** (GitHub secrets are write-only). Git history shows a key
> fork: `cf4caeb0` (2026-03-27) set the config pubkey to `E034…`; `64a917e2` (2026-03-28,
> "update pubkey to match new signing keypair") changed it to the current `7B9C…`. So the
> CI secret is *presumed* to be the `7B9C` key. **Verify on the first post-fix release:**
> download the published `latest.json`, take its `darwin-aarch64.signature`, and confirm it
> verifies against the `7B9C` pubkey (e.g. `minisign -V -P <7B9C pubkey> -m SwarmAI.app.tar.gz -x <sig>`,
> or install the new build on a machine running the prior version and confirm the in-app
> update applies). If verification FAILS → the secret is a different key: rotate to a new
> keypair (update the CI secret + `tauri.conf.json` pubkey together). Until this one check
> passes, still treat the DMG/exe/msi as the primary delivery channel.
>
> **🔑 Signing key authority (unified 2026-07-28 — read this before touching signing):**
> There is exactly ONE valid signing key: pubkey `7B9CEDB5D3C58A4D` (embedded in
> `tauri.conf.json` + every shipped app). Its **private key lives ONLY in the GitHub CI
> secret `TAURI_SIGNING_PRIVATE_KEY`** (Updated ~2026-03-28, same source as the `64a917e2`
> pubkey switch — a strong date-coincidence inference, NOT yet cryptographically proven;
> the first-tag verify above is the final proof). **This build machine holds NO signing
> private key, and should not** — releases are signed in CI, never locally. The old
> `E034920AC30D40E6` keypair (set 2026-03-27, abandoned next day) was the source of the
> "which key?" drift; it has been **archived** to `~/.tauri/_archived-E034.key(.pub).bak`
> (recoverable until `7B9C` is confirmed, then delete). If you ever find a `SwarmAI.key`
> back in `~/.tauri`, someone re-introduced the drift — it is not needed for any release.

Poll via the CLI (one bounded call per invocation — **do NOT use `gh run watch` or wrap a
`sleep`-loop in one bash call**; both are multi-minute single foreground calls that get
silently killed by the foreground timeout). The AGENT drives the loop:

**Each poll = this single call (returns in ~2-3s).** Pass `--ref v${VERSION}` so the gate
verifies the **commit the tag points at** (not the moving `main` tip) and records the tag
in the marker for the publish hook to cross-check:
```bash
cd $SWARMAI_ROOT && VERSION=$(cat VERSION) && python backend/scripts/artifact_cli.py release-gate --poll --ref "v${VERSION}" --project SwarmAI
```

**Read the JSON `state` + exit code:**
- `state=PASS` (exit 0) → CI green on the tag's commit, **marker written (with tag)** → proceed to 7c.
- `state=WAIT` (exit 3) → CI not done / not registered yet. Wait ~30-45s (a short
  standalone `sleep 40` bash call is fine — bounded, not a 10-min loop), then re-poll.
  Give up after ~12-15 polls (~8-10min) → HALT + report.
- `state=BLOCK` (exit 1) → CI is RED. Run `gh run view <run_id> --json jobs` to list
  failing jobs, diagnose + fix. A release batch's own commits often leave test/scan
  artifacts stale — md5-intent (`usedforsecurity=False`), camelCase-mapper shape
  (`toEqual`), hand-built arg stubs (missing new attr) — the highest-probability red
  source; sweep those first. Fix → push → **re-poll on the new HEAD** (the old marker,
  if any, no longer matches the new HEAD → still fail-closed). Do NOT create the Release.

**Do not flip before the marker is PASS.** Publishing before CI green is the exact
"skip verification" hole (CLASS A) 7b exists to close. A legit manual re-publish of an
already-CI-green tag can set `SWARM_RELEASE_GATE_FORCE=1` on any gated command —
deliberate + logged, never the default.

### Stage 7c: FLIP DRAFT → PUBLISHED (Agent, 30s) — reached ONLY after 7b is green

The Release object already exists as a **draft** (CI created it at 7a's tag push, with
all-platform assets). 7c does NOT create a release and does NOT upload a local DMG — it
**flips the existing draft to published** and sets it latest.

```bash
cd $SWARMAI_ROOT
VERSION=$(cat VERSION)

# ★ PUBLISH GATE (fail-closed, replaces the old release_publish_guard hook, run_d613bb27):
# authorize the flip IFF the CI-green marker attests the commit this tag ships. exit≠0 →
# CI not green on the published commit → STOP, do NOT flip. (Legit manual re-publish of an
# already-green tag: SWARM_RELEASE_GATE_FORCE is gone with the hook — instead re-poll, or
# only proceed when --verify is PASS.)
python backend/scripts/artifact_cli.py release-gate --verify --ref "v${VERSION}" --project SwarmAI || {
  echo "release-gate --verify BLOCKED the publish — CI is not green on v${VERSION}'s commit. STOP."
  exit 1
}

# Flip the CI-built draft to published + mark latest (assets already attached by CI).
# NOTE: this uses `gh release edit --draft=false`, NOT `gh release create`. There is
# no local DMG to upload — CI built and attached all platforms to the draft.
gh release edit "v${VERSION}" --draft=false --latest \
  --notes "<release notes>"

# Verify it is actually published (not still a draft)
gh release view "v${VERSION}" --json isDraft,isLatest,url \
  --jq '{isDraft, isLatest, url}'
# Expect: isDraft=false, isLatest=true

# Consume the CI-green marker (one publish = one marker; prevents a stale marker
# from authorizing a future accidental flip on the same HEAD)
python backend/scripts/artifact_cli.py release-gate --clear --project SwarmAI
```

Release notes: summarize commits since last tag, grouped by type (feat/fix/improve/content).
(CI also auto-generates notes via `generate_release_notes`; the `--notes` here overrides
them with the curated summary.)

**Report:**
```
Stage 7 PUBLISH: PASS
  Tag: vX.Y.Z
  CI gate: GREEN (HEAD <sha>, run <id>)
  Draft flipped → Published + Latest ✓
  Release: https://github.com/xg-gh-25/SwarmAI/releases/tag/vX.Y.Z
  Assets: macOS DMG · Windows exe+msi · Hive tar.gz · checksums (all CI-built)
```

---

## Final Summary

```
RELEASE COMPLETE ✅ vX.Y.Z
  Commits: N (since vPREV)
  Backend: verified (prod.sh build passed — local deploy)
  Artifacts: CI-built (macOS DMG · Windows exe+msi · Hive tar.gz)
  Smoke: healthy, correct version
  Published: GitHub Release (draft flipped → published + latest)
```

---

## Quick Reference

| Stage | Who | Duration | Can fail? |
|-------|-----|----------|-----------|
| 0 Guard | Agent | instant | Abort |
| 1 Preflight | Agent | 30s | Block if R6 gate unmet or not on main |
| 1.5 Convergence | Agent | 5s | Non-blocking |
| 2 Version bump | Agent | 30s | No |
| 3 Build+Deploy (local) | **User** | 2-5 min | Yes — blocks release |
| 4 Smoke (local backend) | Agent | 15s | Yes — blocks release |
| 5 Local desktop smoke | **User** | 3-5 min | **OPTIONAL** — skippable; NOT the release artifact |
| 6 (folded into 7b) | — | — | CI builds the real artifacts; verified in 7b |
| 7a Push commit+tag | Agent | 30s | Yes — tag push triggers release.yml (CI builds all platforms + drafts) |
| 7b CI green gate + verify draft assets | Agent | 3-8 min (blocking) | Yes — do not flip if CI red or draft assets incomplete |
| 7c Flip draft → published | Agent | 30s | Rare — `gh release edit --draft=false`, NOT `gh release create` |

---

## Abort Conditions

At any stage, if failure occurs:
1. Report which stage failed with error details
2. Do NOT proceed to later stages
3. Do NOT retry automatically
4. If 7a push succeeded but 7c flip (`gh release edit --draft=false`) failed: re-run 7c
   only (do NOT re-push — the draft already exists from CI; just re-flip it).
5. If 7b CI gate is red: HALT, list failing jobs, fix forward → push → re-run 7b on the
   new HEAD. NEVER flip the draft to published on a red HEAD, and NEVER skip 7b to publish faster.

---

## Why prod.sh build (not manual rsync)

> **Local deploy only — NOT the shipped artifact.** This section is about Stage 3's
> local backend deploy+verify. The DMG/exe/msi that ship are CI-built (`release.yml`);
> see the Co-Pilot Model at the top.

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
