---
name: system-health
description: >
  Full system health report: desktop overview, worst offenders, SwarmAI resource details,
  and actionable suggestions. Outputs a structured report in the chat window.
  TRIGGER: "system health", "mac health", "linux health", "battery check", "ram usage",
  "what's eating memory", "mac running slow", "system running slow", "check my system",
  "why is my laptop slow", "health report", "resource check".
  DO NOT USE: for AWS resource monitoring or CloudWatch logs (use cloudwatch-log-analysis),
  SwarmAI app health (use health-check skill), or security scanning (use bsc-security-scanner).
---

# System Health Report

**Why?** A single command gives you the full picture: desktop health, worst offenders, SwarmAI resource consumption, and what to do about it.

**Supported platforms:** macOS (primary), Linux

## Quick Start

```
"check my system health" → full report with 4 sections + suggestions
```

---

## Workflow

### Step 0: Detect OS & Collect Everything in Parallel

Run ALL data collection in parallel (single bash call where possible) to minimize latency.

#### CRITICAL: Sandbox Constraints

The Claude SDK sandbox blocks `ps`, `pgrep`, `top`, and all process-listing OS commands ("operation not permitted"). **ALL process inspection must use psutil** via the SwarmAI backend venv. This is a hard constraint — no fallback to OS tools for process data.

Additionally: if you are executing this skill, the SwarmAI app IS running. You are inside it. Don't report "app not running" when you're the one generating the report.

#### macOS/Linux — Unified Collection via psutil (Single Script)

**IMPORTANT**: Always activate the SwarmAI venv first. System Python does NOT have psutil.

```bash
source /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/activate && python3 << 'PYEOF'
import psutil, json, collections

# === SYSTEM ===
vm = psutil.virtual_memory()
cpu_pct = psutil.cpu_percent(interval=1)
load1, load5, load15 = psutil.getloadavg()
cores = psutil.cpu_count()
disk = psutil.disk_usage("/")
batt = psutil.sensors_battery()

system = {
    "ram_total_gb": round(vm.total/1024**3, 1),
    "ram_used_gb": round(vm.used/1024**3, 1),
    "ram_avail_gb": round(vm.available/1024**3, 1),
    "ram_pct": vm.percent,
    "cpu_pct": cpu_pct,
    "load": [round(load1,1), round(load5,1), round(load15,1)],
    "cores": cores,
    "disk_total_gb": round(disk.total/1024**3, 0),
    "disk_used_gb": round(disk.used/1024**3, 0),
    "disk_pct": disk.percent,
    "battery_pct": batt.percent if batt else None,
    "plugged": batt.power_plugged if batt else None,
}
print("=== SYSTEM ===")
print(json.dumps(system))

# === BATTERY ===
print("=== BATTERY ===")
if batt:
    status = "AC" if batt.power_plugged else "discharging"
    print(f"{batt.percent}% ({status})")
else:
    print("NO_BATTERY")

# === TOP PROCESSES (by RSS) ===
# Classify into app groups
def classify(name, cmd):
    cl = cmd.lower()
    nl = name.lower()
    if "swarm-jobs" in cl or "scheduler.py" in cl or "self_tune.py" in cl: return "Swarm Jobs (Scheduler)"
    if "job_manager" in cl: return "Swarm Jobs (Manager)"
    if "kiro" in cl or "kiro" in nl: return "Kiro IDE"
    if "SwarmAI" in cmd or "swarmai" in cl: return "SwarmAI (Tauri)"
    if "google chrome" in nl or ("chrome" in nl and "helper" in nl): return "Chrome"
    if "microsoft teams" in nl or "teams" in nl: return "Microsoft Teams"
    if "slack" in nl and "mcp" not in cl: return "Slack"
    if "claude" in nl and "mcp" not in cl: return "Claude CLI"
    if "builder-mcp" in cl: return "MCP: builder"
    if "sentral-mcp" in cl or "sentral" in cl: return "MCP: sentral"
    if "slack-mcp" in cl: return "MCP: slack"
    if "outlook-mcp" in cl: return "MCP: outlook"
    if "taskei" in cl: return "MCP: taskei"
    if "node" in nl and "mcp" in cl: return "MCP: shim/node"
    if "main.py" in cmd and ("backend" in cmd or "uvicorn" in cl): return "SwarmAI Backend"
    if "docker" in nl: return "Docker"
    if "code" in nl and "visual" in cmd: return "VS Code"
    if "firefox" in nl: return "Firefox"
    return name  # ungrouped

apps = collections.defaultdict(lambda: {"rss_mb": 0, "count": 0, "cpu": 0.0})
for p in psutil.process_iter(["pid", "name", "cmdline", "memory_info", "cpu_percent"]):
    try:
        info = p.info
        name = info["name"] or "unknown"
        cmd = " ".join(info["cmdline"] or [])
        rss = (info["memory_info"].rss if info["memory_info"] else 0) / 1024 / 1024
        cpu = info["cpu_percent"] or 0.0
        label = classify(name, cmd)
        apps[label]["rss_mb"] += rss
        apps[label]["count"] += 1
        apps[label]["cpu"] += cpu
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

print("=== TOP_MEM ===")
for name, info in sorted(apps.items(), key=lambda x: -x[1]["rss_mb"])[:10]:
    print(f"{name}|{info['count']}|{info['rss_mb']:.0f}|{info['cpu']:.1f}")

print("=== TOP_CPU ===")
for name, info in sorted(apps.items(), key=lambda x: -x[1]["cpu"])[:5]:
    if info["cpu"] > 0.1:
        print(f"{name}|{info['count']}|{info['cpu']:.1f}")

# === SWARM BREAKDOWN ===
print("=== SWARM_BREAKDOWN ===")
swarm_labels = ["SwarmAI (Tauri)", "SwarmAI Backend", "Claude CLI",
                "MCP: builder", "MCP: sentral", "MCP: slack", "MCP: outlook", "MCP: taskei", "MCP: shim/node",
                "Swarm Jobs (Scheduler)", "Swarm Jobs (Manager)"]
swarm_total = 0
for label in swarm_labels:
    if label in apps and apps[label]["rss_mb"] > 1:
        info = apps[label]
        swarm_total += info["rss_mb"]
        print(f"{label}|{info['count']}|{info['rss_mb']:.0f}|{info['cpu']:.1f}")
print(f"TOTAL|0|{swarm_total:.0f}|0")

# === ORPHAN CHECK ===
print("=== ORPHAN_CHECK ===")
orphan_count = 0
for p in psutil.process_iter(["pid", "ppid", "name", "cmdline", "memory_info", "status"]):
    try:
        info = p.info
        if info["ppid"] == 1:
            cmd = " ".join(info["cmdline"] or [])
            rss = (info["memory_info"].rss if info["memory_info"] else 0) / 1024 / 1024
            if any(kw in cmd.lower() for kw in ["python", "node", "claude", "mcp", "npm", "deno"]):
                print(f"{info['pid']}|{info['name']}|{rss:.0f}|{info['status']}|{cmd[:80]}")
                orphan_count += 1
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
if orphan_count == 0:
    print("NONE")
PYEOF
```

> **NOTE**: psutil's `boot_time()` and `swap_memory()` may throw `PermissionError`/`OSError` in sandbox. Skip them — they're not essential. Focus on `virtual_memory()`, `cpu_percent()`, `getloadavg()`, `disk_usage()`, `sensors_battery()`, and `process_iter()`.

#### SwarmAI Backend API — Dynamic Port Discovery

The backend port is **random on every launch** (Tauri uses `portpicker::pick_unused_port()`). Dev mode uses port 8000. **Never hardcode production ports.** Always discover dynamically via psutil socket inspection.

Add the following to the psutil collection script above, **after the ORPHAN_CHECK section**:

```python
# === BACKEND API (dynamic port discovery) ===
def find_swarmai_backend_port():
    """Find SwarmAI backend port via process name + listening socket.
    1. 'python-backend*' process = Tauri production sidecar
    2. 'main.py --port' in backend dir = dev.sh mode
    3. Return first TCP LISTEN port on the matched process
    """
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            info = p.info
            name = info["name"] or ""
            cmd = " ".join(info["cmdline"] or [])
            is_sidecar = name.startswith("python-backend")
            is_dev = ("main.py" in cmd and "--port" in cmd and "backend" in cmd)
            if is_sidecar or is_dev:
                for c in p.net_connections(kind="tcp"):
                    if c.status == "LISTEN":
                        return {"pid": info["pid"], "port": c.laddr.port,
                                "mode": "production" if is_sidecar else "dev"}
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None

backend = find_swarmai_backend_port()
print("=== BACKEND_API ===")
if backend:
    import urllib.request
    try:
        resp = urllib.request.urlopen(
            f"http://127.0.0.1:{backend['port']}/health", timeout=2)
        health = resp.read().decode()
        print(f"PORT={backend['port']}|PID={backend['pid']}|MODE={backend['mode']}|HEALTH={health}")
        try:
            resp2 = urllib.request.urlopen(
                f"http://127.0.0.1:{backend['port']}/api/system/resources", timeout=2)
            print(f"RESOURCES={resp2.read().decode()}")
        except:
            print("RESOURCES=UNAVAILABLE")
    except Exception as e:
        print(f"PORT={backend['port']}|PID={backend['pid']}|MODE={backend['mode']}|HEALTH=UNREACHABLE|ERR={e}")
else:
    print("BACKEND_NOT_FOUND")
```

> **IMPORTANT**: If you are running this skill, the app IS running — you are executing inside it. Never report "SwarmAI not running" when you are the one generating the report. The backend health check is to verify the API layer specifically, not the app itself.

#### Swarm Job System Health

The job scheduler runs independently via launchd (hourly). Read its state file to report job health without needing the scheduler process to be running:

```python
# === SWARM JOBS ===
import json
from pathlib import Path

jobs_state = Path.home() / ".swarm-ai/SwarmWS/Services/swarm-jobs/state.json"
jobs_yaml = Path.home() / ".swarm-ai/SwarmWS/Services/swarm-jobs/jobs.yaml"
print("=== SWARM_JOBS ===")
if jobs_state.exists():
    try:
        state = json.loads(jobs_state.read_text())
        jobs = state.get("jobs", {})
        for jid, js in jobs.items():
            last = js.get("last_run", "never")
            status = js.get("last_status", "never")
            fails = js.get("consecutive_failures", 0)
            runs = js.get("total_runs", 0)
            print(f"{jid}|{status}|{fails}|{runs}|{last}")
        spend = state.get("monthly_spend_usd", 0)
        tokens = state.get("monthly_tokens_used", 0)
        print(f"SPEND|{spend:.2f}|{tokens}")
    except Exception as e:
        print(f"ERROR|{e}")
else:
    print("NOT_INSTALLED")
```

> **Validation**: If psutil unavailable (shouldn't happen with venv), fall back to `vm_stat` with `active + wired` formula (NOT free + inactive). See Lessons Learned below.

### Step 1: Format the Full Report

Output a **single structured report** with 4 sections. Use this exact format:

---

```markdown
# System Health Report

## 1. Desktop Overview

| Metric | Value | Status |
|--------|-------|--------|
| RAM | 18.2GB / 36GB (50.6%) | ✅ Healthy |
| CPU | Light load (avg 2.1) | ✅ Healthy |
| Disk | 245GB / 500GB (49%) | ✅ Healthy |
| Battery | 73% (discharging) | ✅ OK |

## 2. Worst Offenders

### Memory (Top 5)

| # | Process | PID | RSS | % RAM |
|---|---------|-----|-----|-------|
| 1 | Kiro | 1234 | 2.8GB | 7.8% |
| 2 | Chrome | 5678 | 1.9GB | 5.3% |
| 3 | claude | 9012 | 512MB | 1.4% |
| 4 | Docker | 3456 | 480MB | 1.3% |
| 5 | Slack | 7890 | 420MB | 1.2% |

### CPU (Top 5)

| # | Process | PID | CPU% |
|---|---------|-----|------|
| 1 | node | 2345 | 45.2% |
| 2 | chrome | 5678 | 12.1% |
| ... | | | |

## 3. SwarmAI Resource Details

| Component | PID | RSS | CPU% | State | Uptime |
|-----------|-----|-----|------|-------|--------|
| Backend (FastAPI) | 1001 | 180MB | 1.2% | running | 2h 15m |
| Session: tab-1 (claude) | 1002 | 520MB | 3.1% | STREAMING | 5m |
| Session: tab-2 (claude) | 1003 | 490MB | 0.2% | IDLE | 45m |
| MCP: slack-mcp | 1004 | 85MB | 0.1% | running | 2h 15m |
| MCP: builder-mcp | 1005 | 92MB | 0.1% | running | 2h 15m |
| MCP: aws-outlook-mcp | 1006 | 78MB | 0.0% | running | 2h 15m |
| **Total SwarmAI** | | **1,445MB** | **4.7%** | | |

**Session Slots:** 2/4 active (STREAMING: 1, IDLE: 1)
**Memory Pressure:** ok (50.6% used, threshold 85%)
**Spawn Budget:** ✅ Can spawn (headroom: 12,400MB, cost: ~500MB)
**Orphaned Processes:** None detected

### Swarm Job System

| Job | Status | Runs | Failures | Last Run |
|-----|--------|------|----------|----------|
| signal-fetch | ✅ success | 13 | 0 | 2026-03-23 20:07 |
| signal-digest | ✅ success | 7 | 0 | 2026-03-23 21:07 |
| self-tune | ✅ success | 2 | 0 | 2026-03-24 04:05 |
| weekly-maintenance | ✅ success | 1 | 0 | 2026-03-22 06:17 |
| morning-inbox | 🔴 disabled | 0 | 0 | never |

**Monthly spend:** $0.52 | **Scheduler:** launchd (hourly)

## 4. Suggestions

### Issues Found
- ⚠️ Kiro using 2.8GB — normal for IDE, but consider closing unused projects
- ⚠️ Session tab-2 IDLE for 45m — will auto-evict at 12hr TTL, or close tab to free 490MB now

### Quick Wins
- Close Chrome tabs to free ~1.9GB
- No orphaned MCP processes — cleanup is working correctly

### System Verdict
✅ **System is healthy** — 50% RAM used, light CPU load, plenty of headroom for more tabs.
```

---

### Step 2: Analysis Rules

Apply these thresholds to determine status and generate suggestions:

#### Desktop Overview Thresholds

**RAM:**
- < 75% → ✅ Healthy
- 75-85% → ⚠️ Elevated
- >= 85% → 🔴 Critical

> **IMPORTANT**: Use psutil `percent` for RAM. If psutil unavailable, use `active + wired` as "used" — NOT `free + inactive`. This matches Activity Monitor. See COE from 2026-03-22: vm_stat `free + speculative + 50% inactive` formula was 37x wrong.

**CPU (macOS load average / core count):**
- < 0.5 → ✅ Light
- 0.5-1.0 → ⚠️ Moderate
- > 1.0 → 🔴 High

**Disk:**
- < 85% → ✅ Healthy
- 85-95% → ⚠️ Low
- >= 95% → 🔴 Critical

**Battery:**
- > 20% → ✅ OK
- 10-20% → ⚠️ Low
- < 10% → 🔴 Critical

#### Worst Offenders Rules

- Show top 5 by RSS, top 5 by CPU%
- Flag any single process > 2GB RAM
- Flag any process > 100% CPU sustained
- Flag any process > 150% CPU as potentially stuck
- Aggregate related processes (e.g., all Chrome helpers → "Chrome (12 processes)")
- Convert RSS from KB to human-readable (MB/GB)

#### SwarmAI Details Rules

**Data sources (in priority order):**
1. SwarmAI `/api/system/resources` endpoint (most accurate — has session IDs, states, spawn budget)
2. psutil `process_iter()` via SwarmAI venv (always works in sandbox — `ps`/`pgrep` do NOT work)
3. Never use `ps`, `pgrep`, `top` — they are blocked by Claude SDK sandbox

**What to show:**
- Backend sidecar process (python main.py)
- Each Claude CLI subprocess with session state (COLD/STREAMING/IDLE/WAITING_INPUT/DEAD)
- Each MCP server process with name
- Total SwarmAI RSS footprint
- Session slot usage (active/max from max-tabs endpoint)
- Memory pressure level and spawn budget status
- Orphaned processes (PPID=1 matching our patterns)

**SwarmAI-specific thresholds:**
- Total SwarmAI RSS > 4GB on 16GB machine → ⚠️ "SwarmAI using >25% of total RAM"
- Total SwarmAI RSS > 6GB on 36GB machine → ⚠️ "SwarmAI using >16% of total RAM"
- Any IDLE session > 1hr → suggest closing tab to free memory
- Any STREAMING session > 30min → note (long tasks are normal, but flag if stuck)
- Orphaned MCP processes found → 🔴 "Leaked MCP servers — kill them to free memory"
- Spawn budget can_spawn=false → ⚠️ "Cannot open new tabs — close idle tabs or other apps"
- Backend unavailable → 🔴 "SwarmAI backend not responding — app may need restart"

#### Swarm Job System Rules

**Data source:** `~/.swarm-ai/SwarmWS/Services/swarm-jobs/state.json` (always readable, no API needed)

- If state.json doesn't exist → "Swarm Job System: Not installed" (info, not error)
- Any job with `consecutive_failures >= 3` → 🔴 "Job 'X' circuit-breaker tripped (N failures)"
- Any job with `consecutive_failures >= 1` → ⚠️ "Job 'X' failed last run"
- Agent task jobs (morning-inbox, etc.) spawn a Claude CLI + MCP servers per run (~500MB for ~90s). This is transient — no long-running processes. Flag only if a swarm-jobs process appears stuck (running > 10min).
- Monthly spend > $50 → ⚠️ "Job system monthly spend at $X"
- All jobs healthy → "Job system: ✅ all jobs healthy"

#### Suggestions Rules

Generate 3 categories:

**Issues Found** — Problems that need attention:
- 🔴 for critical (orphans, backend down, disk >95%, stuck processes)
- ⚠️ for warnings (high memory, IDLE sessions, spawn budget tight)

**Quick Wins** — Easy actions that free resources:
- "Close X to free ~YMB" (for identifiable memory hogs)
- "Kill orphaned MCP process PID XXXX" (if found)
- "Close idle tab-N to free ~500MB"
- "Run `brew cleanup` to free disk" (if disk >85%)

**System Verdict** — One-line summary:
- ✅ **Healthy** if no 🔴 issues
- ⚠️ **Under pressure** if any ⚠️ issues
- 🔴 **Critical** if any 🔴 issues

### Step 3: Offer Actions (only if issues found)

If orphaned processes found:
> "Want me to kill the orphaned MCP processes?"

If stuck process (>150% CPU):
> "Want me to kill PID XXXX ([process name])?"

If disk critical:
> "Want me to find large files eating disk space?"

If backend down:
> "The SwarmAI backend isn't responding. Try restarting the app."

If healthy — no action prompts, just the verdict.

---

## Examples

### Example 1: Healthy 36GB Machine

```markdown
# System Health Report

## 1. Desktop Overview

| Metric | Value | Status |
|--------|-------|--------|
| RAM | 18.2GB / 36GB (50.6%) | ✅ Healthy |
| CPU | Light load (1.2 avg / 12 cores) | ✅ Healthy |
| Disk | 245GB / 500GB (49%) | ✅ Healthy |
| Battery | 73% (discharging) | ✅ OK |

## 2. Worst Offenders

### Memory (Top 5)
| # | Process | PID | RSS | % RAM |
|---|---------|-----|-----|-------|
| 1 | Kiro | 1234 | 2.1GB | 5.8% |
| 2 | Chrome (8 procs) | — | 1.4GB | 3.9% |
| 3 | claude | 5678 | 520MB | 1.4% |
| 4 | claude | 5679 | 490MB | 1.4% |
| 5 | Slack | 7890 | 380MB | 1.1% |

### CPU (Top 5)
| # | Process | PID | CPU% |
|---|---------|-----|------|
| 1 | WindowServer | 234 | 3.2% |
| 2 | claude | 5678 | 2.1% |
| 3 | Kiro | 1234 | 1.8% |
| 4 | Chrome | 5670 | 0.9% |
| 5 | Finder | 456 | 0.3% |

## 3. SwarmAI Resource Details

| Component | PID | RSS | CPU% | State | Uptime |
|-----------|-----|-----|------|-------|--------|
| Backend (FastAPI) | 1001 | 165MB | 0.8% | running | 3h 10m |
| Session: tab-1 | 5678 | 520MB | 2.1% | STREAMING | 8m |
| Session: tab-2 | 5679 | 490MB | 0.1% | IDLE | 1h 20m |
| MCP: slack-mcp | 6001 | 82MB | 0.0% | running | 3h 10m |
| MCP: builder-mcp | 6002 | 88MB | 0.1% | running | 3h 10m |
| MCP: aws-outlook-mcp | 6003 | 75MB | 0.0% | running | 3h 10m |
| MCP: aws-sentral-mcp | 6004 | 91MB | 0.0% | running | 3h 10m |
| MCP: taskei-p-mcp | 6005 | 68MB | 0.0% | running | 3h 10m |
| **Total SwarmAI** | | **1,579MB** | **3.1%** | | |

**Session Slots:** 2/4 active (STREAMING: 1, IDLE: 1)
**Memory Pressure:** ok (50.6%, threshold 85%)
**Spawn Budget:** ✅ Can spawn (headroom: 12,400MB)
**Orphaned Processes:** None

## 4. Suggestions

No issues found.

✅ **System is healthy** — plenty of headroom. SwarmAI using 1.6GB (4.3% of RAM).
```

### Example 2: Under Pressure with Orphans

```markdown
# System Health Report

## 1. Desktop Overview

| Metric | Value | Status |
|--------|-------|--------|
| RAM | 28.1GB / 36GB (78.1%) | ⚠️ Elevated |
| CPU | Moderate load (6.2 avg / 12 cores) | ⚠️ Moderate |
| Disk | 420GB / 500GB (84%) | ✅ Healthy |
| Battery | AC Power | ✅ OK |

## 2. Worst Offenders

### Memory (Top 5)
| # | Process | PID | RSS | % RAM |
|---|---------|-----|-----|-------|
| 1 | Docker | 3456 | 6.2GB | 17.2% |
| 2 | Kiro | 1234 | 3.1GB | 8.6% |
| 3 | Chrome (15 procs) | — | 2.8GB | 7.8% |
| 4 | claude | 5678 | 540MB | 1.5% |
| 5 | node (orphan) | 9999 | 520MB | 1.4% |

### CPU (Top 5)
| # | Process | PID | CPU% |
|---|---------|-----|------|
| 1 | node | 9999 | 165% |
| 2 | Docker | 3456 | 45% |
| 3 | claude | 5678 | 12% |
| 4 | Kiro | 1234 | 8% |
| 5 | Chrome | 5670 | 5% |

## 3. SwarmAI Resource Details

| Component | PID | RSS | CPU% | State | Uptime |
|-----------|-----|-----|------|-------|--------|
| Backend (FastAPI) | 1001 | 172MB | 0.9% | running | 5h |
| Session: tab-1 | 5678 | 540MB | 12% | STREAMING | 15m |
| MCP: slack-mcp | 6001 | 85MB | 0.0% | running | 5h |
| MCP: builder-mcp | 6002 | 90MB | 0.1% | running | 5h |
| **Orphan: node (MCP?)** | **9999** | **520MB** | **165%** | **PPID=1** | **2h** |
| **Total SwarmAI** | | **1,407MB** | **13.0%** | | |

**Session Slots:** 1/3 active (STREAMING: 1)
**Memory Pressure:** warning (78.1%, threshold 85%)
**Spawn Budget:** ✅ Can spawn (headroom: 2,484MB)
**Orphaned Processes:** 1 found (node PID 9999 — likely leaked MCP server)

## 4. Suggestions

### Issues Found
- 🔴 **Orphaned node process (PID 9999)** — 520MB RSS, 165% CPU, PPID=1. Likely a leaked MCP server from a crashed session. Kill it.
- ⚠️ Docker using 6.2GB — if not actively needed, stop containers to free memory
- ⚠️ Memory at 78% — approaching spawn threshold (85%)

### Quick Wins
- Kill orphan PID 9999 → free ~520MB + stop CPU drain
- Close Chrome tabs → free ~2.8GB
- `docker system prune` → free disk + memory

### System Verdict
⚠️ **Under pressure** — 78% RAM used with an orphaned process burning CPU. Kill the orphan and close Chrome to get back to healthy.

Want me to kill the orphaned process (PID 9999)?
```

---

## Lessons Learned (from real bugs)

These lessons come from actual production bugs in SwarmAI (March 2026). They are encoded into the rules above:

1. **Never use vm_stat `inactive` pages as "available"** — On a 36GB Mac, `inactive` was 12.6GB. Including it (or even 50% of it) reported 45% used when psutil correctly showed 63%. Use psutil, or `active + wired` as a fallback.

2. **psutil is the source of truth for memory** — It's cross-platform, battle-tested, and matches Activity Monitor. Don't parse OS-specific tools unless psutil is unavailable.

3. **SwarmAI spawns ~500MB per Claude CLI session** — Each tab costs ~500MB (CLI + MCP children). On a 16GB machine, 2 tabs + backend + MCPs = ~2GB. On 36GB, 4 tabs = ~3GB. **Job system agent_task jobs also spawn a Claude CLI + MCPs (~500MB) but are transient (90-180s max). They won't show in steady-state unless stuck.**

4. **MCP orphans are the silent killer** — When Claude CLI crashes with shared PGID, MCP children (5+ per session) survive as orphans. They accumulate memory and CPU. Always check for PPID=1 processes matching our patterns.

5. **The backend `/api/system/resources` endpoint is the best data source** — It has session IDs, states, spawn budget, and per-process metrics. Always try it first; fall back to `ps` only if backend is down.

6. **Memory pressure threshold is 85%, not 80%** — Changed 2026-03-22 after psutil installation. Warning at 75%, critical at 85%.

7. **Claude SDK sandbox blocks `ps`, `pgrep`, `top`** — All process-listing OS commands return "operation not permitted". The ONLY way to inspect processes from inside SwarmAI is `psutil.process_iter()` via the backend venv. This is a hard constraint, not a preference.

8. **If you're executing, the app is running** — The agent runs inside SwarmAI. Checking "is the app running?" is tautological. The backend health endpoint (`/health`) verifies the API layer; the Tauri shell is guaranteed alive if you can execute anything at all.

9. **Backend port is random in production, 8000 in dev** — Tauri uses `portpicker::pick_unused_port()` on every launch. `./dev.sh` defaults to 8000. Never hardcode ports — discover dynamically via psutil `process_iter()` + `net_connections()`. The correct health endpoint is `/health` (not `/api/system/health`).

---

## Troubleshooting

### macOS

| Issue | Cause | Fix |
|-------|-------|-----|
| psutil not found | System Python doesn't have it | Activate SwarmAI venv first: `source .../backend/.venv/bin/activate` |
| `ps`/`pgrep` "operation not permitted" | Claude SDK sandbox blocks process listing | Use psutil `process_iter()` instead — always works |
| Backend API returns connection refused | Backend sidecar crashed or port changed | Use `find_swarmai_backend_port()` (psutil socket discovery). Port is random each launch. You ARE the app — it's running |
| psutil `boot_time()` PermissionError | Sandbox blocks `sysctl()` | Skip boot_time — not essential for health report |
| psutil `swap_memory()` OSError | Sandbox blocks swap inspection | Skip swap — not essential |
| MCP process names vary | Depends on config | Match `mcp` keyword in cmdline via psutil |

### Linux

| Issue | Cause | Fix |
|-------|-------|-----|
| `free` shows low "available" but high "buff/cache" | Linux caches aggressively; normal | Use psutil — it handles this correctly |
| No SwarmAI processes found | Processes exist but classify() missed them | Check cmdline patterns in psutil output |
| Load average seems high | Includes I/O wait | Compare to core count from `psutil.cpu_count()` |

---

## Quality Rules

### Output Validation Checklist

Before presenting the report, verify:
- [ ] All 4 sections present (Desktop Overview, Worst Offenders, SwarmAI Details + Job System, Suggestions)
- [ ] RAM percentage matches psutil (or active+wired fallback) — NOT vm_stat inactive
- [ ] SwarmAI section tried API first, fell back to ps only if unavailable
- [ ] Orphan check included (PPID=1 matching claude/mcp/node/python)
- [ ] Suggestions are specific and actionable (include PIDs, MB amounts, command to run)
- [ ] System verdict is one line with emoji status
- [ ] All RSS values converted to human-readable (MB/GB)
- [ ] Top processes aggregated where appropriate (Chrome helpers → single line)

### Anti-Patterns to Avoid

| Don't | Do Instead |
|-------|------------|
| Show raw vm_stat or /proc/meminfo | Use psutil via SwarmAI venv |
| Use `inactive` pages as "available" | Use `active + wired` as "used" |
| Use `ps`, `pgrep`, `top` for process data | Use psutil `process_iter()` — sandbox blocks OS tools |
| Report "SwarmAI not running" | You ARE the app. Check backend API, not app existence |
| Skip SwarmAI section if backend API is down | Use psutil process data as fallback |
| Ignore orphaned processes | Always check PPID=1 |
| Just say "memory is high" | Say "28.1GB / 36GB (78%) — close Chrome (2.8GB) to drop to 70%" |
| Suggest killing system processes | Only suggest for user apps and orphans |
| Skip spawn budget status | Always report if backend is reachable |
| Run network speed test by default | Only if user specifically asks about network |
