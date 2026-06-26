# Swarm Build — Instructions

Build, verify, deploy, and confirm health of the SwarmAI backend binary.

## Co-Pilot Model

This skill uses **human-in-the-loop** for the build step (slow, kills session).
Agent handles pre/post checks; user runs `./prod.sh build` which does everything:
PyInstaller build → verify (46 checks) → deploy to daemon → restart.

```
Agent: PREFLIGHT → USER: prod.sh build → Agent: HEALTH
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

- ❌ Run `build-backend.sh` directly in session (exit 137)
- ❌ Run `prod.sh build` directly in session (exit 137 — same problem)
- ❌ Run upgrade/deploy API endpoint (session dies)
- ❌ Use `nohup`, `run_in_background`, or TaskRunner for builds
- ❌ Retry a failed build without user intervention
- ❌ Hand-assemble rsync/launchctl deploy commands (use prod.sh — it handles .version, resources, permissions, restart)

---

## Stage 0: GUARD

```
CHECK: Active project == SwarmAI
FAIL:  ABORT with "s_swarm-build is SwarmAI-only."
```

---

## Stage 1: PREFLIGHT (Agent, 5s)

```bash
cd $SWARMAI_ROOT && \
  echo "Version: $(cat VERSION)" && \
  echo "Tree: $(git status --porcelain | wc -l | tr -d ' ') uncommitted" && \
  nc -z 127.0.0.1 18321 2>/dev/null && echo "Daemon: UP" || echo "Daemon: DOWN" ; \
  echo "--- Eval gate ---" ; \
  (cd backend && python scripts/ci_eval_gate.py ; echo "gate_rc=$?")
```

**Report:**
```
Stage 1 PREFLIGHT: PASS
  Version: 1.17.2
  Tree: clean | N uncommitted
  Daemon: UP | DOWN
  Eval gate: PASS (rc=0) | WARN no-report (rc=2) | BLOCK stale/red (rc=1)
```

**Eval gate semantics** (mirrors `prod.sh` Step -1, which enforces it for real):
- `rc=0` fresh + green → build will proceed
- `rc=2` no gate-readable report (bootstrap / fresh clone) → `prod.sh` SOFT-warns and proceeds; tell the user to run `python backend/scripts/eval_runner.py run` to enable the gate
- `rc=1` stale (code/golden_set changed since last eval) OR red (BVT failing) → `prod.sh` will **HARD-BLOCK** the build. The user must re-run eval (`python backend/scripts/eval_runner.py run`) before building, or set `SWARMAI_SKIP_EVAL_GATE=1` to override (CI/emergency).

If PREFLIGHT shows `gate_rc=1`, warn the user that `./prod.sh build` will block until eval is re-run — surface this BEFORE the handoff so they fix it in one trip.

Then immediately hand off to user for build.

---

## Stage 2: BUILD + DEPLOY (User, 2-5 min)

`./prod.sh build` is a single command that does:
0. **Eval gate** (git-bound): block if the committed eval report is stale/red (rc=1); soft-warn if no report yet (rc=2); pass if fresh+green (rc=0). Override: `SWARMAI_SKIP_EVAL_GATE=1`.
1. Sync versions from VERSION file
2. PyInstaller build → binary at `desktop/src-tauri/binaries/python-backend-aarch64-apple-darwin/`
3. Run `verify_build.py` (46 capability checks) — fails fast if broken
4. `rsync -a --delete` binary bundle to `~/.swarm-ai/daemon/`
5. Write `.version` file (semver + git hash + timestamp)
6. Copy `desktop/resources/` to daemon
7. `chmod +x` the daemon binary
8. SIGKILL old daemon → KeepAlive auto-restarts with new binary

Hand off to user:

```
⏸️ YOUR TURN — 请在终端跑:
┌─────────────────────────────────────────────────────
│ cd ~/Desktop/SwarmAI-Workspace/swarmai && ./prod.sh build
└─────────────────────────────────────────────────────
完成后说 "好了" 或贴最后几行 output。
```

Wait for user confirmation before proceeding.

**If user reports verify failed:** Read their output, identify which checks failed, diagnose.
**If user reports "Daemon not running":** prod.sh warns but doesn't fail. Proceed to health — it will confirm.

---

## Stage 3: HEALTH (Agent, 5-40s)

Wait 5s for daemon to stabilize, then check:

```bash
sleep 5 && \
nc -z 127.0.0.1 18321 2>/dev/null && \
curl -sf http://127.0.0.1:18321/health | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Status: {d[\"status\"]}')
print(f'Version: {d.get(\"version\", \"?\")}')
print(f'SDK: {d.get(\"sdk_version\", \"?\")}')
print(f'DB: {d.get(\"db_healthy\", \"?\")}')
assert d['status'] == 'healthy', 'NOT HEALTHY'
" || echo "FAIL: daemon not responding on port 18321"
```

**Pass criteria:**
- Port 18321 open
- Returns JSON (not HTML)
- `status: healthy`
- Version matches VERSION file

**Report:**
```
Stage 3 HEALTH: PASS
  Status: healthy
  Version: 1.17.2

BUILD COMPLETE ✅
```

**On failure:**
- Port closed: "Daemon didn't start. Check: `tail -20 ~/.swarm-ai/logs/backend-stderr.log`"
- Version mismatch: "Deploy may have failed. Re-run `./prod.sh build`"
- Not JSON / HTML: "Caddy proxy issue, not a build problem"

---

## Quick Reference

| Stage | Who | Duration | Can fail? |
|-------|-----|----------|-----------|
| 0 Guard | Agent | instant | Abort if wrong project |
| 1 Preflight | Agent | 5s | Warn only |
| 2 Build+Deploy | **User** | 2-5 min | Yes — user retries |
| 3 Health | Agent | 5-40s | Yes — check logs |

---

## Error Recovery

| Error | Who Fixes | How |
|-------|-----------|-----|
| Build exit 137 | User | Close memory-heavy apps, retry `./prod.sh build` |
| Verify fails (inside prod.sh) | User reads output | Identify missing PyInstaller hiddenimports |
| Daemon won't start | User | `tail ~/.swarm-ai/logs/backend-stderr.log` |
| Version mismatch after build | User | Re-run `./prod.sh build` (deploy may have been partial) |
| Port 18321 in use by old process | User | `launchctl kill SIGKILL gui/$(id -u)/com.swarmai.backend` then wait 10s |

---

## Why prod.sh (not manual rsync)

`prod.sh build` calls `_deploy_daemon_binary()` from `scripts/daemon-lib.sh` which:
- Validates binary exists before deploy
- Uses `rsync -a --delete` (atomic, incremental)
- Writes `.version` file in canonical format: `{semver} {git_hash} {timestamp}`
- Copies `desktop/resources/` to daemon resources dir
- Sets correct permissions

Hand-assembling these steps in skill docs = guaranteed drift when daemon-lib.sh
is updated. Single source of truth: `prod.sh build`.

---

## Relationship to Other Skills

```
s_swarm-build    → Binary build + deploy + health (THIS SKILL)
s_swarm-release  → Full release cycle: bump + build + tauri + publish (calls prod.sh build internally)
s_swarm-daemon   → Daemon operations only (status/stop/start/logs) — no build
```
