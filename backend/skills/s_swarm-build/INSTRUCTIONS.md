# Swarm Build — Instructions

Build the SwarmAI backend binary via PyInstaller, verify it, deploy to daemon path,
restart the daemon, and confirm health. Each stage runs independently with clear
pass/fail output between steps.

**🚨 SELF-KILL PARADOX:** You (the agent) are a subprocess of the daemon you're
restarting. Killing the daemon = killing your parent = severing your communication
channel. Stage 5 (RESTART) WILL disconnect your session. Stage 6 (HEALTH) uses a
detached verifier that survives the kill — you read its result on cold resume.

## Stage 0: PROJECT GUARD (blocking)

Before anything else, verify this is a SwarmAI task:

```
Check:
  - Is the active project SwarmAI? (file paths, user context, pipeline run)
  - If project != SwarmAI → ABORT:
    "s_swarm-build is SwarmAI-only. Project '{project}' has its own deploy workflow."
```

If invoked during a pipeline run, check `run.json["project"]`.
If invoked standalone, check which project files are being edited or ask.

---

## Stage 1: PREFLIGHT (5s)

Quick sanity checks before burning 3+ minutes on PyInstaller.

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai

# 1. Check tree is clean (warn, don't block)
git status --porcelain | head -5

# 2. VERSION file exists
cat VERSION

# 3. Python + uv available
python3 --version
uv --version
```

**Pass criteria:**
- VERSION file exists and is not empty
- python3 and uv are available
- If tree is dirty: WARN but continue (build includes uncommitted changes)

**Report format:**
```
Stage 1 PREFLIGHT: PASS
  Version: 1.10.0
  Tree: clean (or: 3 uncommitted files — building anyway)
  Python: 3.11.x, uv: 0.x.x
```

---

## Stage 2: PYINSTALLER (2-5 min)

The longest stage. PyInstaller requires ~500MB RAM + full filesystem access.

**🚨 MEMORY REQUIREMENT:** PyInstaller needs ~2GB free RAM. Exit 137 = macOS
jetsam OOM kill. Before running, check available memory:

```bash
# Pre-check: need at least 4GB free (2GB for PyInstaller + 2GB headroom)
FREE_GB=$(vm_stat | awk '/Pages free|Pages inactive/ {sum += $NF} END {printf "%.0f", sum*4096/1024/1024/1024}')
echo "Free memory: ${FREE_GB}GB"
if [ "$FREE_GB" -lt 4 ]; then
  echo "WARN: Only ${FREE_GB}GB free. Build may OOM. Close other apps first."
fi
```

If memory is sufficient, run the build directly:

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/desktop/scripts
bash build-backend.sh 2>&1 | tail -30
```

Use `timeout: 600000` (10 min max) to avoid blocking the conversation.
Do NOT retry on exit 137 — it means OOM, not a code bug.

**On exit 137 (OOM kill):**
1. Do NOT retry immediately (same result)
2. Report: "Build killed by macOS memory pressure (exit 137). ~XGB free, need 4GB+."
3. Suggest: "Close other apps or restart daemon to free memory, then retry."

**Pass criteria:**
- build-backend.sh exits 0
- Binary directory exists at expected output path
- verify_build.py passes (build-backend.sh runs it automatically)

**Report format:**
```
Stage 2 PYINSTALLER: PASS
  Binary: python-backend-aarch64-apple-darwin/ (onedir bundle)
  Location: src-tauri/binaries/
  Verify: XX/XX checks passed
```

**On failure:** Report the last 20 lines of output and STOP.
Common failures:
- Missing hidden imports → fix in build-backend.sh, re-run
- OOM → close other apps, re-run
- Module not found → uv sync first

---

## Stage 3: VERIFY (10s)

Run the verification suite against the built binary (if not already run by build-backend.sh).

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai
python3 desktop/scripts/verify_build.py
```

**Pass criteria:**
- All checks pass (exit code 0)
- Output shows "X/X checks passed"

**Report format:**
```
Stage 3 VERIFY: PASS (46/46 checks)
  Core imports: OK
  MCP catalog: OK
  Skills bundled: OK
  Native extensions: OK
```

**On failure:** Report which checks failed. Common fixes:
- Missing hiddenimport → add to build-backend.sh HIDDEN_IMPORTS
- Missing data file → add to datas list in spec

---

## Stage 4+5: DEPLOY & RESTART

Deploy the verified binary and restart daemon via the `/api/system/upgrade` endpoint.

**🚨 NO SELF-KILL:** The endpoint spawns a detached upgrader process (in a new
session) that deploys THEN kills the daemon. Your session stays alive through
the deploy phase. The daemon dies only after rsync is complete — KeepAlive
restarts with the new binary.

**Preferred method — API endpoint (no self-kill):**

```bash
# Call the upgrade endpoint — daemon handles deploy+restart internally
RESPONSE=$(curl -s -X POST http://127.0.0.1:18321/api/system/upgrade)
echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d, indent=2))"
```

The endpoint returns 202 immediately. The daemon spawns a detached upgrader that:
1. Deploys new binary (rsync from sidecar to daemon dir)
2. Kills daemon via SIGTERM
3. KeepAlive restarts with new binary

Your session stays alive during deploy. It MAY disconnect briefly when daemon
restarts (~5-15s), but reconnects automatically to the new daemon.

**Fallback — manual bash (if endpoint not available on old daemon):**

Use the manual bash approach only if the running daemon doesn't have the
`/api/system/upgrade` endpoint yet (pre-upgrade). See git history for
the old bash approach (commit 676fafc).

```bash
# Check if endpoint exists
curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:18321/api/system/upgrade
# If 404 → use fallback; if 202/403/409 → endpoint exists
```

**Pass criteria:**
- Health verifier spawned (file path echoed)
- Daemon killed (port released)
- rsync completed (version file written)
- "Deploy + restart complete" message shown

**Report format:**
```
Stage 4+5 DEPLOY+RESTART: PASS
  Version: 1.12.2 793f3c4 2026-05-13 11:51:10
  Bundle: 210 MB
  Health verifier: /tmp/swarm-build-health-793f3c4-XXXXX.txt
  Session may disconnect — verify on cold resume (Stage 6)
```

**What happens next:**
- KeepAlive detects daemon is gone → spawns new process with NEW binary
- Health verifier polls every 5s for 120s → writes result with version check
- Your session may die (parent was killed) → frontend shows reconnecting
- On next message, cold resume → you read the health file in Stage 6

**On failure:** If `launchctl kill` returns error AND port is not occupied → daemon
was already dead. rsync still runs (safe). KeepAlive will start it.

If rsync fails (disk full, permissions): STOP. Do NOT proceed — daemon is dead
and has no valid binary. Fix the issue and re-run this stage.

---

## Stage 6: HEALTH (verify after upgrade)

**Context:** After calling `/api/system/upgrade`, wait ~15-30s for the daemon to
restart, then verify.

```bash
# Wait for daemon to come back (upgrade kills it, KeepAlive restarts in ~10s)
echo "Waiting for daemon restart..."
for i in $(seq 1 20); do
  HEALTH=$(curl -s http://127.0.0.1:18321/health 2>/dev/null)
  if echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='healthy'" 2>/dev/null; then
    echo "=== Daemon HEALTHY ==="
    echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Version: {d.get(\"version\",\"?\")}')"
    break
  fi
  sleep 2
done

# Verify version matches what we built
EXPECTED_HASH=$(awk '{print $2}' ~/.swarm-ai/daemon/.version 2>/dev/null)
echo "Expected git hash: $EXPECTED_HASH"

# Check upgrade result (if endpoint available on new daemon)
curl -s http://127.0.0.1:18321/api/system/upgrade/status 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(f'Upgrade status: {d.get(\"status\", \"unknown\")}')
    if 'result' in d:
        r = d['result']
        print(f'  Deploy: {r.get(\"status\", \"?\")}')
        print(f'  Completed: {r.get(\"completed_at\", \"?\")}')
except: pass
" 2>/dev/null || true
```

**Pass criteria:**
- Health endpoint returns `{"status": "healthy"}`
- Version in health response matches deployed git hash (semantic correctness)

**Report format:**
```
Stage 6 HEALTH: PASS
  Status: healthy
  Version: 1.12.2 (matches deployed)
  Verifier: confirmed healthy at 2026-05-13 11:52:30
```

**On failure:**
- Health verifier says TIMEOUT → check daemon logs: `tail -30 ~/.swarm-ai/logs/backend-stderr.log`
- Version mismatch → old binary still in daemon dir (rsync failed)
- No health file + no live health → daemon never started, check plist:
  `launchctl print gui/$(id -u)/com.swarmai.backend 2>&1 | head -20`

---

## Final Summary

After all 6 stages pass, report:

```
BUILD COMPLETE ✅
  Version: 1.12.2 abc1234
  Bundle: 210 MB (onedir)
  Verify: 46/46 checks
  Daemon: healthy, correct version
  Duration: ~3m 45s total
```

If any stage fails, stop at that stage and report clearly what went wrong.
Do NOT retry the entire build — fix the specific stage that failed.

---

## Quick Reference

| Stage | Duration | Can Fail? | Session Impact |
|-------|----------|-----------|----------------|
| 0. Guard | instant | Yes (abort) | None |
| 1. Preflight | 5s | Warn only | None |
| 2. PyInstaller | 2-5 min | Yes | None |
| 3. Verify | 10s | Yes | None |
| 4+5. Deploy+Restart | 5-10s | Rare | **SESSION MAY DIE** (self-kill) |
| 6. Health | 5s | Yes | Runs on cold resume |

---

## Troubleshooting

### Session hangs after Stage 5
**Expected behavior.** The daemon (your parent process) was killed. Your session
SSE stream is severed. The frontend will show "reconnecting" and eventually
establish a new session on the restarted daemon. Wait ~30s, then check the
health verifier file.

### Port 18321 stuck after restart
Orphan Claude CLI processes may hold resources. Force cleanup:
```bash
pkill -f "claude.*--session" 2>/dev/null || true
pkill -f "python-backend" 2>/dev/null || true
sleep 2
# KeepAlive will spawn a fresh daemon
```

### Daemon starts but health returns "initializing"
Normal during startup. The daemon runs migrations, loads skills, starts
channels. Full startup takes 5-15s. The health verifier polls for 120s —
it will catch the transition to "healthy."

### build-backend.sh succeeds but verify_build.py fails
The build script runs verify internally. If you see this, the binary was
likely corrupted during deploy (rsync mid-write). Re-run Stage 4 (deploy).
