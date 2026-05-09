# Swarm Build — Instructions

Build the SwarmAI backend binary via PyInstaller, verify it, deploy to daemon path,
restart the daemon, and confirm health. Each stage runs independently with clear
pass/fail output between steps.

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
bash build-backend.sh 2>&1 | tail -20
```

Use `timeout: 600000` (10 min max) and `run_in_background: true` to avoid
blocking the conversation. Do NOT retry on exit 137 — it means OOM, not a
code bug. Ask user to close apps and free memory first.

**On exit 137 (OOM kill):**
1. Do NOT retry immediately (same result)
2. Report: "Build killed by macOS memory pressure (exit 137). ~XGB free, need 4GB+."
3. Suggest: "Close other apps or restart daemon to free memory, then retry."

**Pass criteria:**
- User confirms build completed
- Binary exists at expected output path
- Binary is newer than the release commit

**Report format:**
```
Stage 2 PYINSTALLER: PASS (user-built)
  Binary: python-backend-aarch64-apple-darwin (48.2 MB)
  Location: src-tauri/binaries/
```

**On failure:** Report the last 20 lines of user's Terminal output and STOP.
Common failures:
- Missing hidden imports → fix in build-backend.sh, re-run
- OOM → close other apps, re-run
- Module not found → uv sync first

---

## Stage 3: VERIFY (10s)

Run the 38-check verification suite against the built binary.

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai
python3 desktop/scripts/verify_build.py
```

**Pass criteria:**
- All checks pass (exit code 0)
- Output shows "X/X checks passed"

**Report format:**
```
Stage 3 VERIFY: PASS (38/38 checks)
  Core imports: OK
  MCP catalog: OK
  Skills bundled: OK
  Native extensions: OK
```

**On failure:** Report which checks failed. Common fixes:
- Missing hiddenimport → add to build-backend.sh HIDDEN_IMPORTS
- Missing data file → add to datas list in spec

---

## Stage 4: DEPLOY (5s)

Copy the verified binary to the daemon directory.

```bash
# Source and destination
SIDECAR="/Users/gawan/Desktop/SwarmAI-Workspace/swarmai/desktop/src-tauri/binaries/python-backend-aarch64-apple-darwin"
DAEMON_DIR="${HOME}/.swarm-ai/daemon"
DAEMON_BIN="${DAEMON_DIR}/python-backend"

# Atomic deploy: copy to temp, then move (prevents partial binary)
cp "$SIDECAR" "${DAEMON_BIN}.new"
mv "${DAEMON_BIN}.new" "$DAEMON_BIN"
chmod +x "$DAEMON_BIN"

# Write version marker
echo "$(cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai && git rev-parse --short HEAD) $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "${DAEMON_DIR}/.version"

cat "${DAEMON_DIR}/.version"
```

**Pass criteria:**
- Binary exists at daemon path
- .version file written

**Report format:**
```
Stage 4 DEPLOY: PASS
  Binary: ~/.swarm-ai/daemon/python-backend (48.2 MB)
  Version: abc1234 2026-05-05T09:30:00Z
```

---

## Stage 5: RESTART (90s max, typically 15-30s)

Kill daemon process so KeepAlive auto-restarts with new binary.

**CRITICAL:** Use `launchctl kill SIGTERM`, NOT `bootout`. Bootout deregisters the service — if this script dies mid-execution (e.g. running inside daemon's own subprocess), nobody re-registers and daemon is permanently dead.

```bash
# Kill process — service stays registered, KeepAlive will restart
launchctl kill SIGTERM gui/$(id -u)/com.swarmai.backend 2>/dev/null || {
  # If kill fails (daemon wasn't running), bootstrap fresh
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.swarmai.backend.plist 2>/dev/null || true
}

# Wait for port to release
for i in $(seq 1 15); do
  nc -z 127.0.0.1 18321 2>/dev/null || break
  sleep 1
done

# Force-kill if stuck
if nc -z 127.0.0.1 18321 2>/dev/null; then
  launchctl kill SIGKILL gui/$(id -u)/com.swarmai.backend 2>/dev/null || true
  sleep 1
fi

echo "Daemon killed — KeepAlive will restart with new binary"
```

**Pass criteria:**
- Port 18321 released (nc -z fails)
- Daemon will auto-restart via KeepAlive (verified in Stage 6)

**Report format:**
```
Stage 5 RESTART: PASS
  Daemon: com.swarmai.backend killed, KeepAlive will restart
```

**On failure:** If kill fails AND bootstrap fails → plist may be missing. Report: "Check ~/Library/LaunchAgents/com.swarmai.backend.plist exists."

---

## Stage 6: HEALTH (15s, poll)

Verify the daemon is healthy AND running the correct version.

```bash
# Wait for startup (poll up to 30s)
for i in $(seq 1 15); do
  HEALTH=$(curl -s http://localhost:18321/health 2>/dev/null)
  if echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='healthy'" 2>/dev/null; then
    echo "$HEALTH"
    break
  fi
  sleep 2
done

# Verify version matches what we deployed
DEPLOYED_VERSION=$(cat ~/.swarm-ai/daemon/.version | awk '{print $1}')
HEALTH_VERSION=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('version','unknown'))" 2>/dev/null)
echo "Deployed: $DEPLOYED_VERSION | Running: $HEALTH_VERSION"
```

**Pass criteria:**
- Health endpoint returns `{"status": "healthy"}`
- Version in health response matches deployed version (semantic correctness, not just liveness)

**Report format:**
```
Stage 6 HEALTH: PASS
  Status: healthy
  Version: abc1234 (matches deployed)
  Uptime: 5s
```

**On failure:**
- If health never returns: check `log stream --predicate 'subsystem == "com.swarmai.backend"' --last 30s`
- If version mismatch: daemon loaded old binary — check plist WorkingDirectory

---

## Final Summary

After all 6 stages pass, report:

```
BUILD COMPLETE ✅
  Version: abc1234
  Binary: 48.2 MB
  Verify: 38/38 checks
  Daemon: healthy, correct version
  Duration: ~3m 45s total
```

If any stage fails, stop at that stage and report clearly what went wrong.
Do NOT retry the entire build — fix the specific stage that failed.

---

## Quick Reference

| Stage | Duration | Can Fail? | Retry? |
|-------|----------|-----------|--------|
| 0. Guard | instant | Yes (abort) | No |
| 1. Preflight | 5s | Warn only | No |
| 2. PyInstaller | 2-5 min | Yes | No (fix first) |
| 3. Verify | 10s | Yes | After fix |
| 4. Deploy | 5s | Rare | Yes |
| 5. Restart | 15s | Rare | Alt method |
| 6. Health | 15-30s | Yes | Check logs |
