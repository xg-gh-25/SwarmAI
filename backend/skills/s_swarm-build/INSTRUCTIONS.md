# Swarm Build — Instructions

Build, verify, deploy, and confirm health of the SwarmAI backend binary.

## Core Loop

```
PREFLIGHT → BUILD → VERIFY → DEPLOY → [session dies] → HEALTH (on resume)
```

Each stage: emit SIGNAL/CHECK/FAIL preamble → execute → report pass/fail → advance.

---

## Execution Rules

### Momentum

Once user says "build", execute ALL stages without pausing. Only stop on:
- Stage FAILS (exit non-zero, check fails)
- User explicitly interrupts

Mid-flow observations (dirty tree, disk space, etc.): note inline and CONTINUE.

### Session Death at Deploy

**Deploy kills the daemon = kills your session.** This is structural, not a bug.
The agent SDK subprocess is a child of the daemon process. SIGKILL on daemon →
all children get SIGHUP → your SSE stream dies → session ends.

Therefore:
- Deploy is the **last action** in this session. Period.
- After `curl /api/system/upgrade` returns 202, emit the deploy report and STOP.
- Do NOT issue any further Bash commands, tool calls, or text after the curl.
- Health verification happens on cold resume (breadcrumb-driven).

### Detached Build (Session-Death Resilient)

Build takes 2-5 minutes. Claude sessions can crash/evict during this time, killing
all child processes (exit 137 = SIGKILL from parent death, NOT OOM).

**Solution:** Launch build detached from session process tree via `nohup`, poll log
file for completion. If session dies mid-build, the build continues. On resume,
check the log file.

**Never** pipe through `| tail` or `| head` — causes stdout buffering.
**Never** use `run_in_background` — its notifications are unreliable for long tasks.

---

## Stage 0: GUARD

```
SIGNAL: Active project is SwarmAI
CHECK:  File paths, user context, or explicit mention
FAIL:   Project is not SwarmAI → ABORT
```

If project != SwarmAI:
```
ABORT: s_swarm-build is SwarmAI-only. Project '{project}' has its own deploy workflow.
```

---

## Stage 1: PREFLIGHT

```
SIGNAL: VERSION exists, python3 + uv available, daemon reachable
CHECK:  cat VERSION, python3 --version, uv --version, nc -z 127.0.0.1 18321
FAIL:   VERSION missing or python3/uv not found (daemon down = WARN only)
```

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai && cat VERSION && python3 --version && uv --version && nc -z 127.0.0.1 18321 2>/dev/null && echo "Daemon: UP" || echo "Daemon: DOWN (will deploy anyway)"
git status --porcelain | head -5
```

**Report:**
```
Stage 1 PREFLIGHT: PASS
  Version: 1.12.2
  Tree: clean | N uncommitted (building anyway)
  Daemon: UP | DOWN
```

---

## Stage 2: BUILD (2-5 min)

```
SIGNAL: build-backend.sh exits 0, verify checks pass
CHECK:  Exit code + "checks passed" in stdout
FAIL:   Non-zero exit. Exit 137 = process killed (session death or OOM).
```

### Step 1: Launch detached build

```bash
BUILD_LOG="/tmp/swarm-build-$(date +%s).log"
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/desktop/scripts && \
  nohup bash build-backend.sh > "$BUILD_LOG" 2>&1 &
BUILD_PID=$!
echo "Build PID: $BUILD_PID, Log: $BUILD_LOG"
sleep 3
kill -0 $BUILD_PID 2>/dev/null && echo "Build running..." || { echo "Build died immediately"; tail -20 "$BUILD_LOG"; exit 1; }
```

### Step 2: Poll for completion (every 30s)

```bash
# Repeat this block until build finishes:
if kill -0 $BUILD_PID 2>/dev/null; then
  echo "Still running... $(wc -l < "$BUILD_LOG") lines"
  tail -3 "$BUILD_LOG"
else
  wait $BUILD_PID 2>/dev/null
  EXIT_CODE=$?
  echo "Build finished with exit $EXIT_CODE"
  tail -20 "$BUILD_LOG"
fi
```

Use `timeout: 45000` (45s) per poll. Poll until process ends (~5-8 iterations).
Do NOT use `timeout: 600000` on a single foreground command — session death kills it.

### Step 3: Verify exit code

After build completes, check:
```bash
grep -q "checks passed" "$BUILD_LOG" && echo "PASS" || echo "FAIL"
```

**Report:**
```
Stage 2 BUILD: PASS
  Binary: python-backend-aarch64-apple-darwin/ (onedir)
  Verify: XX/XX checks passed
  Duration: ~Xm Xs
```

**On failure:**
- Exit 137 (in log): Session death killed it before detach worked, OR true OOM. Retry.
- Other: report last 20 lines of build log, STOP.

---

## Stage 3: VERIFY

```
SIGNAL: verify_build.py exits 0
CHECK:  "X/X checks passed" in output
FAIL:   Any check fails
```

Skip if build-backend.sh already ran verify (check output for "checks passed").
Otherwise:

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai && python3 desktop/scripts/verify_build.py
```

**Report:**
```
Stage 3 VERIFY: PASS (46/46)
```

---

## Stage 4: DEPLOY

```
SIGNAL: Upgrade endpoint returns 202 + "initiated"
CHECK:  HTTP status code = 202 in response
FAIL:   Non-202 response (403 = wrong mode, 409 = already upgrading, 400 = no binary)
```

**This is the LAST action in this session.**

### Step 1: Write breadcrumb (for resume)

```bash
cat > /tmp/swarm-build-pending.json << 'EOF'
{"stage": "health", "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)", "project_root": "/Users/gawan/Desktop/SwarmAI-Workspace/swarmai"}
EOF
```

### Step 2: Trigger upgrade

```bash
RESPONSE=$(curl -s -X POST http://127.0.0.1:18321/api/system/upgrade)
echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d, indent=2))"
```

### Step 3: Emit final report and STOP

**Report:**
```
Stage 4 DEPLOY: INITIATED
  Upgrade ID: <from response>
  What happens next:
    1. Detached upgrader: SIGKILL daemon → bootout → rsync → bootstrap (~10s)
    2. This session will disconnect (daemon death = session death)
    3. On reconnect: Stage 5 HEALTH runs automatically (breadcrumb at /tmp/swarm-build-pending.json)

BUILD STAGES 1-4 COMPLETE. Awaiting daemon restart...
```

**After emitting this report: DO NOTHING ELSE.** No more tool calls. No "let me
check if it worked." The session will die within seconds. That's correct behavior.

### Fallback: Daemon not reachable

If daemon is DOWN (preflight detected it, or curl fails with connection refused),
use the manual deploy path:

```bash
DAEMON_DIR="${HOME}/.swarm-ai/daemon"
SIDECAR="/Users/gawan/Desktop/SwarmAI-Workspace/swarmai/desktop/src-tauri/binaries/python-backend-aarch64-apple-darwin"
GUI_TARGET="gui/$(id -u)/com.swarmai.backend"
PLIST_SRC="/Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/channels/com.swarmai.backend.plist"
PLIST_DST="${HOME}/Library/LaunchAgents/com.swarmai.backend.plist"

# Kill + deregister (safe even if already dead)
launchctl kill SIGKILL "$GUI_TARGET" 2>/dev/null || true
sleep 1
launchctl bootout "$GUI_TARGET" 2>/dev/null || true
sleep 1

# Deploy
rsync -a --delete "${SIDECAR}/" "${DAEMON_DIR}/"
chmod +x "${DAEMON_DIR}/python-backend"
VERSION_LINE="$(cat /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/VERSION) $(git -C /Users/gawan/Desktop/SwarmAI-Workspace/swarmai rev-parse --short HEAD) $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "$VERSION_LINE" > "${DAEMON_DIR}/.version"

# Deploy plist template
if [ -f "$PLIST_SRC" ]; then
  sed -e "s|__HOME__|$HOME|g" -e "s|__WRAPPER_PATH__|$HOME/.swarm-ai/swarmai_backend.sh|g" -e "s|__LOG_DIR__|$HOME/.swarm-ai/logs|g" "$PLIST_SRC" > "$PLIST_DST"
fi

# Bootstrap with retry
for attempt in 1 2 3; do
  launchctl bootstrap "gui/$(id -u)" "$PLIST_DST" 2>/dev/null && break
  sleep 2
done

# Write breadcrumb
echo "{\"stage\":\"health\",\"deployed_at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > /tmp/swarm-build-pending.json
echo "Manual deploy complete. Session will reconnect when daemon starts."
```

Same rule: after this completes, emit report and STOP.

---

## Stage 5: HEALTH (on cold resume)

```
SIGNAL: /health returns 200 + version matches deployed hash
CHECK:  curl + version comparison
FAIL:   40s timeout or version mismatch
```

**Trigger:** On session resume (cold start), check for breadcrumb:
```bash
cat /tmp/swarm-build-pending.json 2>/dev/null
```

If breadcrumb exists → execute health verification → delete breadcrumb.
If no breadcrumb → skip (not a post-deploy resume).

### Health verification

```bash
echo "Verifying daemon health after deploy..."
HEALTHY=0
for i in $(seq 1 20); do
  HEALTH=$(curl -sf http://127.0.0.1:18321/health 2>/dev/null) && {
    HEALTHY=1
    echo "Daemon HEALTHY after $((i*2))s"
    echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Status: {d[\"status\"]}\n  Version: {d.get(\"version\",\"?\")}')"
    break
  }
  sleep 2
done

if [ "$HEALTHY" -eq 0 ]; then
  echo "FAIL: Daemon not healthy after 40s"
  tail -10 ~/.swarm-ai/logs/backend-stderr.log 2>/dev/null
  exit 1
fi

# Semantic correctness: version match
EXPECTED_HASH=$(awk '{print $2}' ~/.swarm-ai/daemon/.version 2>/dev/null)
ACTUAL_VERSION=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('version','?'))" 2>/dev/null)
echo "Expected: $EXPECTED_HASH"
echo "Actual:   $ACTUAL_VERSION"

# Clean up breadcrumb
rm -f /tmp/swarm-build-pending.json
```

**Report:**
```
Stage 5 HEALTH: PASS
  Status: healthy
  Version: 1.12.2 abc1234 (matches deployed)
  Latency: Xs after deploy

BUILD COMPLETE ✅
```

**On failure:**
- Timeout: `tail -30 ~/.swarm-ai/logs/backend-stderr.log` for import errors
- Version mismatch: rsync didn't complete or stale binary. Re-run Stage 4.

---

## Quick Reference

| Stage | Duration | Blocks? | Session Impact |
|-------|----------|---------|----------------|
| 0 Guard | instant | Yes (abort) | None |
| 1 Preflight | 5s | Warn only | None |
| 2 Build | 2-5 min | Poll (30s intervals) | Detached — survives session death |
| 3 Verify | 10s | Yes | None |
| 4 Deploy | 5s | **Session dies** | Last action in session |
| 5 Health | 5-40s | Yes | First action on resume |

---

## Error Recovery

| Error | Cause | Fix |
|-------|-------|-----|
| Exit 137 (foreground) | Session crash/evict killed child process | Use detached build (nohup). This is the #1 failure mode. |
| Exit 137 (detached) | True macOS OOM kill | Close memory-heavy apps (Chrome, Teams), retry |
| Build PID gone + no log | Session died before nohup launched | Retry — the 3s sleep check catches this |
| 403 on upgrade | Not in daemon mode | Use manual fallback |
| 409 on upgrade | Prior upgrade still in progress | Wait 60s or restart daemon manually |
| Health timeout | Binary crashes on import | Check stderr log, fix hiddenimports |
| Version mismatch | rsync raced or failed | Re-run Stage 4 |
| Breadcrumb stale | Deploy happened but session never resumed | Delete breadcrumb, verify manually |
