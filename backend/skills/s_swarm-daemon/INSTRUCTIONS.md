# Swarm Daemon — Instructions

Manage the SwarmAI backend launchd daemon (`com.swarmai.backend`). This skill
provides structured commands for all daemon lifecycle operations.

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
DAEMON_BINARY="${HOME}/.swarm-ai/daemon/python-backend"
PLIST_PATH="${HOME}/Library/LaunchAgents/com.swarmai.backend.plist"
DAEMON_LOG="${HOME}/.swarm-ai/logs/daemon.log"
VERSION_FILE="${HOME}/.swarm-ai/daemon/.version"
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
curl -s http://localhost:18321/health 2>/dev/null || echo "Unreachable"

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

Gracefully stop the daemon.

```bash
# Bootout (graceful unload)
launchctl bootout gui/$(id -u)/com.swarmai.backend 2>/dev/null

# Wait for port to free (max 10s)
for i in $(seq 1 10); do
  if ! lsof -ti :18321 >/dev/null 2>&1; then
    echo "Port 18321 free"
    break
  fi
  sleep 1
done

# Verify
if lsof -ti :18321 >/dev/null 2>&1; then
  echo "WARNING: Port still in use after 10s — force killing"
  kill -9 $(lsof -ti :18321) 2>/dev/null
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
for i in $(seq 1 15); do
  HEALTH=$(curl -s http://localhost:18321/health 2>/dev/null)
  if echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='healthy'" 2>/dev/null; then
    echo "Daemon healthy"
    echo "$HEALTH" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))"
    break
  fi
  sleep 2
done
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

Stop + start with health verification.

```bash
# 1. Stop
launchctl bootout gui/$(id -u)/com.swarmai.backend 2>/dev/null
sleep 2

# 2. Start
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.swarmai.backend.plist

# 3. Wait healthy (30s)
for i in $(seq 1 15); do
  HEALTH=$(curl -s http://localhost:18321/health 2>/dev/null)
  if echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='healthy'" 2>/dev/null; then
    echo "$HEALTH"
    break
  fi
  sleep 2
done
```

**Alternative (faster, if supported):**
```bash
launchctl kickstart -k gui/$(id -u)/com.swarmai.backend
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
DAEMON_BIN="${HOME}/.swarm-ai/daemon/python-backend"

# Check source exists
if [ ! -f "$SIDECAR" ]; then
  echo "ERROR: No backend binary. Run s_swarm-build first."
  exit 1
fi

# Atomic deploy
cp "$SIDECAR" "${DAEMON_BIN}.new"
mv "${DAEMON_BIN}.new" "$DAEMON_BIN"
chmod +x "$DAEMON_BIN"

# Version marker
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai
echo "$(git rev-parse --short HEAD) $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "${HOME}/.swarm-ai/daemon/.version"

# Restart to pick up new binary
launchctl kickstart -k gui/$(id -u)/com.swarmai.backend

# Verify health
sleep 3
curl -s http://localhost:18321/health
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
HEALTH=$(curl -s http://localhost:18321/health)
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
| Port 18321 in use but daemon not loaded | Orphan process | `kill -9 $(lsof -ti :18321)` then `start` |
| "service already loaded" on bootstrap | Double-load | `bootout` first, then `bootstrap` |
| Health returns HTML not JSON | Caddy proxy issue (Hive) | N/A for desktop daemon |
| Version mismatch | Old binary loaded | `deploy` (copies + restarts) |
| Repeated crashes | Check logs | `logs` → fix code → `s_swarm-build` |
| "Operation not permitted" | launchd I/O error | Wait 5s, retry. If persistent: `launchctl remove` + `bootstrap` |

---

## Relationship to Other Skills

```
s_swarm-build  → builds binary + calls deploy + restart + health (Stages 4-6)
s_swarm-daemon → manages daemon WITHOUT building (faster, for ops only)

Use s_swarm-build when: code changed, need fresh binary
Use s_swarm-daemon when: daemon is misbehaving, need restart/logs/status
```
