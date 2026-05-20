# Swarm Daemon — Instructions

Manage the SwarmAI backend launchd daemon (`com.swarmai.backend`). This skill
provides structured commands for all daemon lifecycle operations.

## 🚨 MOMENTUM RULE — DO NOT STOP BETWEEN STEPS

**Multi-step commands (deploy, restart) are ONE atomic operation.**
- Step passes → immediately proceed to the next step. NO pause, NO "ready?", NO options.
- Only STOP for: (1) a step FAILS, (2) user explicitly interrupts.
- The user said "deploy" / "restart" once. That's the only approval needed until health check passes.
- Single-step commands (status, logs, stop) are already atomic — this rule is redundant for them.

## Stage 0: PROJECT GUARD (blocking)

Before any command, verify this is a SwarmAI context:

```
Check:
  - Is the active project SwarmAI? (file paths, user context, pipeline run)
  - If project != SwarmAI → ABORT:
    "s_swarm-daemon is SwarmAI-only. Project '{project}' has its own service management."
```

---

## Constants

```bash
DAEMON_LABEL="com.swarmai.backend"
DAEMON_PORT=18321
DAEMON_DIR="${HOME}/.swarm-ai/daemon"
GUI_TARGET="gui/$(id -u)/com.swarmai.backend"
PLIST_DST="${HOME}/Library/LaunchAgents/com.swarmai.backend.plist"
DAEMON_LOG="${HOME}/.swarm-ai/logs/daemon.log"
```

---

## Commands

Parse the user's intent and execute the matching command below.

---

### `status`

Show daemon running state, version, and health in one view.

```bash
# 1. Is it running?
launchctl print gui/$(id -u)/com.swarmai.backend 2>/dev/null | grep -E "state|pid" || echo "NOT LOADED"

# 2. What version?
cat ~/.swarm-ai/daemon/.version 2>/dev/null || echo "No version file"

# 3. Is it healthy?
curl -s http://127.0.0.1:18321/health 2>/dev/null || echo "Unreachable"

# 4. Binary age
ls -la ~/.swarm-ai/daemon/python-backend | awk '{print $6, $7, $8}'

# 5. Compare against source HEAD
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai && git rev-parse --short HEAD
```

**Report format:**
```
DAEMON STATUS
  State: running (PID 12345)
  Version: abc1234 (2026-05-05T09:30:00Z)
  Health: healthy
  Source HEAD: def5678
  Staleness: 3 commits behind (abc1234..def5678)
```

---

### `stop`

Permanently stop the daemon (deregisters from launchd — KeepAlive will NOT restart).

```bash
# Bootout = deregister service permanently (use only for intentional stop)
launchctl bootout gui/$(id -u)/com.swarmai.backend 2>/dev/null

# Wait for port to free (max 10s)
for i in $(seq 1 10); do
  nc -z 127.0.0.1 18321 2>/dev/null || { echo "Port 18321 free"; break; }
  sleep 1
done

# Verify — force kill only if port still bound
if nc -z 127.0.0.1 18321 2>/dev/null; then
  echo "WARNING: Port still in use after 10s — force killing"
  launchctl kill SIGKILL gui/$(id -u)/com.swarmai.backend 2>/dev/null || true
fi
```

**Report:**
```
DAEMON STOPPED
  Port 18321: free
```

---

### `start`

Start the daemon (assumes binary is already deployed).

```bash
# Check binary exists
if [ ! -f ~/.swarm-ai/daemon/python-backend ]; then
  echo "ERROR: No binary at ~/.swarm-ai/daemon/python-backend"
  echo "Run s_swarm-build first."
  exit 1
fi

# Check plist exists
if [ ! -f ~/Library/LaunchAgents/com.swarmai.backend.plist ]; then
  echo "ERROR: No plist at ~/Library/LaunchAgents/com.swarmai.backend.plist"
  exit 1
fi

# Bootstrap
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.swarmai.backend.plist

# Wait for healthy (max 30s)
START_HEALTHY=0
for i in $(seq 1 15); do
  HEALTH=$(curl -sf http://127.0.0.1:18321/health 2>/dev/null)
  if echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='healthy'" 2>/dev/null; then
    START_HEALTHY=1
    echo "Daemon healthy"
    echo "$HEALTH" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))"
    break
  fi
  sleep 2
done

if [ "$START_HEALTHY" -eq 0 ]; then
  echo "FAIL: Daemon not healthy after 30s."
  tail -20 ~/.swarm-ai/logs/daemon.log 2>/dev/null
  exit 1
fi
```

**Report:**
```
DAEMON STARTED
  Health: healthy
  Version: abc1234
  Port: 18321
```

**On failure:** If "service already loaded" → daemon was already running. Use `restart` instead.

---

### `restart`

Kill process, let KeepAlive auto-restart. **NEVER use bootout for restart** — bootout deregisters the service, and if the script dies mid-execution, nobody re-registers.

```bash
# 1. Send SIGTERM — service stays registered, KeepAlive will restart
launchctl kill SIGTERM gui/$(id -u)/com.swarmai.backend 2>/dev/null || {
  echo "kill failed — daemon may not be running, bootstrapping fresh..."
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.swarmai.backend.plist
}

# 2. Wait 5s for port to release (ExitTimeOut does NOT apply to `kill`)
# SSE streams block graceful shutdown indefinitely → SIGKILL after 5s
for i in $(seq 1 5); do
  nc -z 127.0.0.1 18321 2>/dev/null || break
  sleep 1
done

# 3. Force-kill if still stuck (SSE drain will never complete)
if nc -z 127.0.0.1 18321 2>/dev/null; then
  echo "Port still held after 5s (SSE drain) — SIGKILL..."
  launchctl kill SIGKILL gui/$(id -u)/com.swarmai.backend 2>/dev/null || true
  sleep 1
fi

# 4. Wait for KeepAlive to restart daemon (ThrottleInterval=10s + cold start)
RESTART_HEALTHY=0
for i in $(seq 1 45); do
  HEALTH=$(curl -sf http://127.0.0.1:18321/health 2>/dev/null)
  if echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='healthy'" 2>/dev/null; then
    RESTART_HEALTHY=1
    echo "$HEALTH" | python3 -m json.tool
    break
  fi
  sleep 2
done

if [ "$RESTART_HEALTHY" -eq 0 ]; then
  echo "FAIL: Daemon not healthy after 90s. KeepAlive may have failed."
  tail -20 ~/.swarm-ai/logs/daemon.log 2>/dev/null
  exit 1
fi
```

**Report:**
```
DAEMON RESTARTED
  Health: healthy
  Version: abc1234
  Downtime: ~4s
```

---

### `deploy`

Deploy a pre-built binary to daemon path WITHOUT rebuilding.
Use when binary already exists (e.g., after s_swarm-build Stage 2-3).

```bash
SIDECAR="/Users/gawan/Desktop/SwarmAI-Workspace/swarmai/desktop/src-tauri/binaries/python-backend-aarch64-apple-darwin"
DAEMON_DIR="${HOME}/.swarm-ai/daemon"
GUI_TARGET="gui/$(id -u)/com.swarmai.backend"
PLIST_DST="${HOME}/Library/LaunchAgents/com.swarmai.backend.plist"

# Check source exists (onedir = directory, not single file)
if [ ! -d "$SIDECAR" ]; then
  echo "ERROR: No backend binary directory. Run s_swarm-build first."
  exit 1
fi

# Step 1: SIGKILL (instant death — SSE streams block SIGTERM indefinitely)
launchctl kill SIGKILL "$GUI_TARGET" 2>/dev/null || true
sleep 1

# Step 2: bootout (deregister — disables KeepAlive so nothing restarts during rsync)
launchctl bootout "$GUI_TARGET" 2>/dev/null || true
sleep 1

# Step 3: Confirm port is free
if nc -z 127.0.0.1 18321 2>/dev/null; then
  echo "WARN: Port still held after SIGKILL+bootout. Waiting 5s..."
  sleep 5
  if nc -z 127.0.0.1 18321 2>/dev/null; then
    echo "FAIL: Port 18321 still held. Orphan process. Debug manually."
    exit 1
  fi
fi

# Step 4: Deploy (safe — no live process, no KeepAlive)
rsync -a --delete "$SIDECAR/" "$DAEMON_DIR/"
chmod +x "$DAEMON_DIR/python-backend"

# Step 5: Version marker (use VERSION file — single source of truth)
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai
APP_VER=$(cat VERSION)
echo "${APP_VER} $(git rev-parse --short HEAD) $(date '+%Y-%m-%d %H:%M:%S')" > "$DAEMON_DIR/.version"
echo "Deployed: $(cat $DAEMON_DIR/.version)"

# Step 6: Verify plist exists (deploy assumes plist already installed by s_swarm-build)
if [ ! -f "$PLIST_DST" ]; then
  echo "FAIL: No plist at $PLIST_DST. Run s_swarm-build or s_swarm-release first."
  exit 1
fi

# Step 7: Bootstrap with retry (re-register + start new binary)
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
DEPLOY_HEALTHY=0
for i in $(seq 1 20); do
  HEALTH=$(curl -sf http://127.0.0.1:18321/health 2>/dev/null) && {
    DEPLOY_HEALTHY=1
    echo "=== Daemon HEALTHY after $((i*2))s ==="
    echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Status: {d[\"status\"]}\n  Version: {d.get(\"version\",\"?\")}')"
    break
  }
  sleep 2
done

if [ "$DEPLOY_HEALTHY" -eq 0 ]; then
  echo "FAIL: Daemon not healthy after 40s."
  tail -20 ~/.swarm-ai/logs/daemon.log 2>/dev/null
  exit 1
fi
```

**Report:**
```
DAEMON DEPLOYED + RESTARTED
  Binary: 48.2 MB → ~/.swarm-ai/daemon/python-backend
  Version: abc1234 (2026-05-05T09:30:00Z)
  Health: healthy
```

---

### `logs`

Show recent daemon logs.

```bash
# Last 50 lines of daemon log
tail -50 ~/.swarm-ai/logs/daemon.log 2>/dev/null || echo "No log file"

# Also check system log for crash info
log show --predicate 'subsystem == "com.swarmai.backend"' --last 2m --style compact 2>/dev/null | tail -20
```

**With filter (if user specifies):**
```bash
# Error logs only
grep -i "error\|exception\|traceback" ~/.swarm-ai/logs/daemon.log | tail -20

# Startup logs
grep -i "startup\|listening\|ready" ~/.swarm-ai/logs/daemon.log | tail -10
```

---

### `health`

Deep health check: liveness + semantic correctness (version match).

```bash
# 1. Liveness
HEALTH=$(curl -s http://127.0.0.1:18321/health)
echo "Health response: $HEALTH"

# 2. Version match (deployed vs running)
DEPLOYED=$(cat ~/.swarm-ai/daemon/.version 2>/dev/null | awk '{print $1}')
RUNNING=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('version','unknown'))" 2>/dev/null)
echo "Deployed version: $DEPLOYED"
echo "Running version: $RUNNING"

# 3. Source HEAD comparison
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai
SOURCE_HEAD=$(git rev-parse --short HEAD)
echo "Source HEAD: $SOURCE_HEAD"

# 4. Judgment
if [ "$DEPLOYED" = "$RUNNING" ]; then
  echo "VERSION MATCH: OK"
else
  echo "VERSION MISMATCH: deployed=$DEPLOYED but running=$RUNNING"
  echo "→ Daemon may be using old binary. Restart needed."
fi

if [ "$DEPLOYED" = "$SOURCE_HEAD" ]; then
  echo "FRESHNESS: up-to-date"
else
  BEHIND=$(cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai && git rev-list --count ${DEPLOYED}..HEAD 2>/dev/null || echo "?")
  echo "FRESHNESS: $BEHIND commits behind HEAD"
fi
```

**Report:**
```
DAEMON HEALTH
  Liveness: healthy
  Version match: OK (abc1234)
  Freshness: up-to-date (or: 5 commits behind — consider rebuild)
```

---

## Error Recovery

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| Port 18321 in use but daemon not loaded | Orphan process | `launchctl kill SIGKILL gui/$(id -u)/com.swarmai.backend` then `start` |
| "service already loaded" on bootstrap | Double-load | `launchctl bootout` first, then `bootstrap` |
| Health returns HTML not JSON | Caddy proxy issue (Hive) | N/A for desktop daemon |
| Version mismatch | Old binary loaded | `deploy` (SIGKILL + bootout + rsync + bootstrap) |
| Repeated crashes | Check logs | `logs` → fix code → `s_swarm-build` |
| "Operation not permitted" | launchd I/O error | Wait 5s, retry. If persistent: `launchctl bootout` + `bootstrap` |
| Daemon dead after dev.sh build | bootout killed registration | `start` (re-bootstraps from plist) |

**Critical rules:**
- **Restart:** `SIGTERM` (service stays registered, KeepAlive auto-restarts). Fallback to SIGKILL after 5s if SSE blocks.
- **Deploy:** `SIGKILL` + `bootout` + rsync + `bootstrap` (must disable KeepAlive during rsync to prevent stale binary restart).
- **Stop (permanent):** `bootout` only.
- **Port checks:** Always `nc -z 127.0.0.1 18321`. Never `lsof`.

---

## Relationship to Other Skills

```
s_swarm-build  → builds binary + calls deploy + restart + health (Stages 4-6)
s_swarm-daemon → manages daemon WITHOUT building (faster, for ops only)

Use s_swarm-build when: code changed, need fresh binary
Use s_swarm-daemon when: daemon is misbehaving, need restart/logs/status
```
