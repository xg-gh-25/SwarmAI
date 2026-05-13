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

## Stage 4+5: DEPLOY & RESTART (single atomic operation)

Deploy the verified onedir bundle and restart daemon in ONE bash call.

**🚨 WHY SINGLE CALL:** You are a subprocess of the daemon. Killing the daemon
severs your communication channel. Also, KeepAlive restarts the daemon within
~1s of kill — if deploy and kill are separate calls, KeepAlive can restart the
daemon with OLD files mid-rsync (corruption). Single call = kill → rsync → done,
no gap for KeepAlive to race.

**🚨 ONEDIR FORMAT:** The binary is a DIRECTORY (not a single file):
```
python-backend-aarch64-apple-darwin/
  ├── python-backend          (executable)
  └── _internal/              (libraries, ~200MB)
```

**CRITICAL:** Use `launchctl kill SIGTERM`, NOT `bootout`. Bootout deregisters
the service permanently — dangerous when running inside daemon's own subprocess.

Run this as a SINGLE Bash tool call:

```bash
PROJECT_ROOT="/Users/gawan/Desktop/SwarmAI-Workspace/swarmai"
BACKEND_BUNDLE_DIR="${PROJECT_ROOT}/desktop/src-tauri/binaries/python-backend-aarch64-apple-darwin"
DAEMON_DIR="${HOME}/.swarm-ai/daemon"
GIT_HASH=$(cd "$PROJECT_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
APP_VERSION=$(cd "$PROJECT_ROOT" && grep -m1 '"version"' desktop/src-tauri/tauri.conf.json 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || cat "$PROJECT_ROOT/VERSION" 2>/dev/null || echo "0.0.0")
HEALTH_FILE="/tmp/swarm-build-health-${GIT_HASH}-$(date +%s).txt"

# ── Step 1: Spawn detached health verifier BEFORE any killing ──
# Uses setsid (if available) or nohup to survive parent death.
# Verifier checks that the NEW daemon (matching GIT_HASH) is healthy.
nohup bash -c "
  EXPECTED_HASH='${GIT_HASH}'
  sleep 12  # Wait for KeepAlive to restart daemon
  for i in \$(seq 1 24); do
    HEALTH=\$(curl -s http://127.0.0.1:18321/health 2>/dev/null)
    if echo \"\$HEALTH\" | python3 -c \"import sys,json; d=json.load(sys.stdin); assert d['status']=='healthy'\" 2>/dev/null; then
      VERSION=\$(echo \"\$HEALTH\" | python3 -c \"import sys,json; print(json.load(sys.stdin).get('version',''))\" 2>/dev/null)
      echo \"HEALTHY\"
      echo \"version=\$VERSION\"
      echo \"expected_hash=\$EXPECTED_HASH\"
      echo \"verified_at=\$(date '+%Y-%m-%d %H:%M:%S')\"
      exit 0
    fi
    sleep 5
  done
  echo 'TIMEOUT: daemon did not become healthy within 120s'
  echo \"expected_hash=\$EXPECTED_HASH\"
  echo \"last_check=\$(date '+%Y-%m-%d %H:%M:%S')\"
  tail -5 ~/.swarm-ai/logs/backend-stderr.log 2>/dev/null
  exit 1
" > "$HEALTH_FILE" 2>&1 &
disown
echo "Health verifier spawned → $HEALTH_FILE (expects hash: $GIT_HASH)"

# ── Step 2: Kill daemon (SIGTERM for graceful shutdown) ──
# After kill, KeepAlive will try to restart — but rsync overwrites files first.
launchctl kill SIGTERM gui/$(id -u)/com.swarmai.backend 2>/dev/null || true

# ── Step 3: Wait for process to die (port release confirms death) ──
# Short wait — just enough for graceful shutdown, NOT long enough for KeepAlive restart.
for i in $(seq 1 8); do
  nc -z 127.0.0.1 18321 2>/dev/null || break
  sleep 0.5
done

# Force-kill if still alive after 4s
if nc -z 127.0.0.1 18321 2>/dev/null; then
  echo "WARN: Graceful shutdown failed — force-killing..."
  launchctl kill SIGKILL gui/$(id -u)/com.swarmai.backend 2>/dev/null || true
  sleep 1
fi

# ── Step 4: Deploy (rsync while daemon is dead, before KeepAlive restarts) ──
rsync -a --delete "$BACKEND_BUNDLE_DIR/" "$DAEMON_DIR/"
chmod +x "$DAEMON_DIR/python-backend"

# Write version file — canonical format: "{semver} {git_hash} {timestamp}"
echo "${APP_VERSION} ${GIT_HASH} $(date '+%Y-%m-%d %H:%M:%S')" > "$DAEMON_DIR/.version"

echo ""
echo "Deploy complete:"
cat "$DAEMON_DIR/.version"
echo "Bundle: $(du -sh "$DAEMON_DIR" | cut -f1)"

# ── Step 5: KeepAlive will now restart daemon with NEW binary ──
# Session may die at this point (we're an orphan of the killed daemon).
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Deploy + restart complete."
echo "  KeepAlive will start new daemon with $GIT_HASH in ~10s."
echo ""
echo "  Session MAY disconnect (self-kill paradox)."
echo "  Health verifier: $HEALTH_FILE"
echo "  On cold resume, Stage 6 reads this file."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
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

## Stage 6: HEALTH (verify on resume)

**Context:** This runs AFTER your session reconnects (cold resume) or immediately
if the session survived Stage 4+5. You're now on the NEW daemon.

**Always do a live health check first** — it's the ground truth. The health
verifier file is supplementary evidence (confirms the daemon came up promptly
after deploy, not just "is healthy now").

```bash
EXPECTED_HASH=$(awk '{print $2}' ~/.swarm-ai/daemon/.version 2>/dev/null)
echo "Expected git hash: $EXPECTED_HASH"

# ── Live health check (primary) ──
HEALTH=$(curl -s http://127.0.0.1:18321/health 2>/dev/null)
if echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='healthy'" 2>/dev/null; then
  echo "=== Live Health: HEALTHY ==="
  echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Version: {d.get(\"version\",\"?\")}')"
else
  echo "=== Live Health: NOT HEALTHY ==="
  echo "Raw: $HEALTH"
  echo "Check logs: tail -30 ~/.swarm-ai/logs/backend-stderr.log"
fi

# ── Health verifier file (supplementary — confirms timely startup) ──
# File named with git hash to prevent stale reads from prior builds
VERIFIER_FILE=$(ls -t /tmp/swarm-build-health-${EXPECTED_HASH}-*.txt 2>/dev/null | head -1)

if [ -n "$VERIFIER_FILE" ]; then
  # Check the verifier has finished (first line is HEALTHY or TIMEOUT)
  FIRST_LINE=$(head -1 "$VERIFIER_FILE" 2>/dev/null)
  if [ "$FIRST_LINE" = "HEALTHY" ] || echo "$FIRST_LINE" | grep -q "TIMEOUT"; then
    echo ""
    echo "=== Verifier Result (from background check) ==="
    cat "$VERIFIER_FILE"
    rm -f "$VERIFIER_FILE"
  else
    echo ""
    echo "=== Verifier still running (file incomplete) — relying on live check ==="
  fi
else
  echo ""
  echo "(No verifier file for hash $EXPECTED_HASH — relying on live check only)"
fi
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
