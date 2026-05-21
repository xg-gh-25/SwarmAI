# Swarm Build — Instructions

Build, verify, deploy, and confirm health of the SwarmAI backend binary.

## Co-Pilot Model

This skill uses **human-in-the-loop** for steps that are slow (>60s) or kill
the session. Agent handles fast checks; user runs long builds in their terminal.

```
Agent: PREFLIGHT → USER: BUILD → Agent: VERIFY → USER: DEPLOY → Agent: HEALTH
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

- Agent stages: execute immediately, no pause between them.
- User stages: hand off with clear command, then WAIT for user response.
- When user says "好了" / "done" / "跑完了": proceed to next agent stage immediately.
- Do NOT ask "ready?" or offer options. Just hand off or execute.

### What NOT to Do

- ❌ Run `build-backend.sh` directly (exit 137 from session death)
- ❌ Run `npm run tauri build` directly (same problem)
- ❌ Run upgrade/deploy endpoint (session dies)
- ❌ Use `nohup`, `run_in_background`, or TaskRunner for builds
- ❌ Retry a failed build without user intervention

---

## Stage 0: GUARD

```
CHECK: Active project == SwarmAI
FAIL:  ABORT with "s_swarm-build is SwarmAI-only."
```

---

## Stage 1: PREFLIGHT (Agent, 5s)

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai && \
  echo "Version: $(cat VERSION)" && \
  echo "Tree: $(git status --porcelain | wc -l | tr -d ' ') uncommitted" && \
  nc -z 127.0.0.1 18321 2>/dev/null && echo "Daemon: UP" || echo "Daemon: DOWN"
```

**Report:**
```
Stage 1 PREFLIGHT: PASS
  Version: 1.16.2
  Tree: clean | N uncommitted
  Daemon: UP | DOWN
```

Then immediately hand off to user for build.

---

## Stage 2: BUILD (User, 2-5 min)

Hand off to user:

```
⏸️ YOUR TURN — 请在终端跑:
┌─────────────────────────────────────────────────────
│ cd ~/Desktop/SwarmAI-Workspace/swarmai && bash desktop/scripts/build-backend.sh
└─────────────────────────────────────────────────────
完成后说 "好了" 或贴最后几行 output。
```

Wait for user confirmation before proceeding.

---

## Stage 3: VERIFY (Agent, 10s)

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai && python3 desktop/scripts/verify_build.py
```

**Pass criteria:** All checks pass (currently 46/46).

**Report:**
```
Stage 3 VERIFY: PASS (46/46)
```

If verify fails: report which checks failed, STOP. Do not proceed to deploy.

---

## Stage 4: DEPLOY (User)

Hand off to user with appropriate command based on daemon status:

**If daemon is UP (most common):**
```
⏸️ YOUR TURN — 请在终端跑:
┌─────────────────────────────────────────────────────
│ curl -X POST http://127.0.0.1:18321/api/system/upgrade
└─────────────────────────────────────────────────────
等 10-15 秒 daemon 重启完成后说 "好了"。
```

**If daemon is DOWN:**
```
⏸️ YOUR TURN — 请在终端跑:
┌─────────────────────────────────────────────────────
│ DAEMON_DIR="$HOME/.swarm-ai/daemon" && \
│ SIDECAR="$HOME/Desktop/SwarmAI-Workspace/swarmai/desktop/src-tauri/binaries/python-backend-aarch64-apple-darwin" && \
│ rsync -a --delete "$SIDECAR/" "$DAEMON_DIR/" && \
│ chmod +x "$DAEMON_DIR/python-backend" && \
│ launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.swarmai.backend.plist
└─────────────────────────────────────────────────────
等 daemon 启动后说 "好了"。
```

Wait for user confirmation.

---

## Stage 5: HEALTH (Agent, 5-40s)

```bash
HEALTH=$(curl -sf http://127.0.0.1:18321/health) && \
  echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Status: {d[\"status\"]}\nVersion: {d.get(\"version\",\"?\")}')" || \
  echo "FAIL: daemon not responding"
```

**Pass criteria:**
- Returns JSON (not HTML)
- `status: healthy`
- Version matches what we just built

**Report:**
```
Stage 5 HEALTH: PASS
  Status: healthy
  Version: 1.16.2

BUILD COMPLETE ✅
```

**On failure:**
- Not responding: "Daemon didn't start. Check `tail -20 ~/.swarm-ai/logs/backend-stderr.log`"
- Version mismatch: "Stale binary. Re-run deploy step."

---

## Quick Reference

| Stage | Who | Duration | Can fail? |
|-------|-----|----------|-----------|
| 0 Guard | Agent | instant | Abort if wrong project |
| 1 Preflight | Agent | 5s | Warn only |
| 2 Build | **User** | 2-5 min | Yes — user fixes |
| 3 Verify | Agent | 10s | Yes — blocks deploy |
| 4 Deploy | **User** | 10-15s | Yes — user fixes |
| 5 Health | Agent | 5-40s | Yes — retry deploy |

---

## Error Recovery

| Error | Who Fixes | How |
|-------|-----------|-----|
| Build exit 137 | User | Close memory-heavy apps, retry |
| Verify fails | Agent reports | Identify which checks failed |
| Deploy 403 | User | Not in daemon mode — use manual path |
| Deploy 409 | User | Wait 60s or `launchctl kill SIGKILL gui/$(id -u)/com.swarmai.backend` |
| Health timeout | User | Check `~/.swarm-ai/logs/backend-stderr.log` |
| Version mismatch | User | Re-run deploy |
