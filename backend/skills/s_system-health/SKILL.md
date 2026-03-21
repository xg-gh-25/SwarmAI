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

#### macOS — Single Collection Script

```bash
echo "=== SYSTEM ==="
sysctl -n hw.memsize  # total RAM in bytes
sysctl -n hw.ncpu     # CPU core count
uname -m              # architecture

echo "=== BATTERY ==="
pmset -g batt 2>/dev/null || echo "NO_BATTERY"

echo "=== MEMORY_PSUTIL ==="
python3 -c "
import psutil, json
vm = psutil.virtual_memory()
print(json.dumps({
    'total_gb': round(vm.total/1024**3, 1),
    'used_gb': round(vm.used/1024**3, 1),
    'available_gb': round(vm.available/1024**3, 1),
    'percent': vm.percent,
    'active_gb': round(vm.active/1024**3, 1) if hasattr(vm, 'active') else None,
    'wired_gb': round(vm.wired/1024**3, 1) if hasattr(vm, 'wired') else None,
}))
" 2>/dev/null || echo "PSUTIL_UNAVAILABLE"

echo "=== DISK ==="
df -h / | tail -1

echo "=== TOP_MEM ==="
ps axo pid,rss,%mem,comm -m | head -16

echo "=== TOP_CPU ==="
ps axo pid,%cpu,comm -r | head -11

echo "=== SWARM_PROCESSES ==="
# SwarmAI ecosystem: Tauri app, backend sidecar, Claude CLI subprocesses, MCP servers
ps axo pid,ppid,rss,%cpu,%mem,etime,comm | grep -E "swarm|claude|tauri|mcp|python.*main\.py" | grep -v grep

echo "=== MCP_CHILDREN ==="
# Find MCP server processes spawned by Claude CLI
pgrep -f "claude|mcp" | xargs -I{} ps -o pid,ppid,rss,%cpu,comm -p {} 2>/dev/null | grep -v "PID"

echo "=== ORPHAN_CHECK ==="
# Orphaned processes (PPID=1) that look like our ecosystem
ps axo pid,ppid,rss,%cpu,comm | awk '$2 == 1' | grep -iE "claude|mcp|python|node" | head -10

echo "=== LOAD ==="
sysctl -n vm.loadavg 2>/dev/null || uptime
```

#### macOS — SwarmAI Backend API (if running)

```bash
# Hit the SwarmAI resource endpoint for accurate session-level data
curl -s http://localhost:23816/api/system/resources 2>/dev/null || echo "BACKEND_UNAVAILABLE"
curl -s http://localhost:23816/api/system/max-tabs 2>/dev/null || echo "MAX_TABS_UNAVAILABLE"
curl -s http://localhost:23816/api/system/status 2>/dev/null || echo "STATUS_UNAVAILABLE"
```

#### Linux — Single Collection Script

```bash
echo "=== SYSTEM ==="
nproc
uname -m
cat /proc/meminfo | head -5

echo "=== BATTERY ==="
cat /sys/class/power_supply/BAT0/capacity 2>/dev/null || echo "NO_BATTERY"
cat /sys/class/power_supply/BAT0/status 2>/dev/null

echo "=== MEMORY ==="
free -h

echo "=== DISK ==="
df -h / | tail -1

echo "=== TOP_MEM ==="
ps axo pid,rss,%mem,comm --sort=-%mem | head -16

echo "=== TOP_CPU ==="
ps axo pid,%cpu,comm --sort=-%cpu | head -11

echo "=== SWARM_PROCESSES ==="
ps axo pid,ppid,rss,%cpu,%mem,etime,comm | grep -E "swarm|claude|tauri|mcp|python.*main\.py" | grep -v grep

echo "=== ORPHAN_CHECK ==="
ps axo pid,ppid,rss,%cpu,comm | awk '$2 == 1' | grep -iE "claude|mcp|python|node" | head -10

echo "=== LOAD ==="
cat /proc/loadavg
```

> **Validation**: If psutil unavailable on macOS, fall back to `vm_stat` with `active + wired` formula (NOT free + inactive). See Lessons Learned below.

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
2. SwarmAI `/api/system/status` endpoint (component health)
3. `ps` output filtered for swarm/claude/mcp/tauri/python (fallback if backend unavailable)

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

3. **SwarmAI spawns ~500MB per Claude CLI session** — Each tab costs ~500MB (CLI + MCP children). On a 16GB machine, 2 tabs + backend + MCPs = ~2GB. On 36GB, 4 tabs = ~3GB.

4. **MCP orphans are the silent killer** — When Claude CLI crashes with shared PGID, MCP children (5+ per session) survive as orphans. They accumulate memory and CPU. Always check for PPID=1 processes matching our patterns.

5. **The backend `/api/system/resources` endpoint is the best data source** — It has session IDs, states, spawn budget, and per-process metrics. Always try it first; fall back to `ps` only if backend is down.

6. **Memory pressure threshold is 85%, not 80%** — Changed 2026-03-22 after psutil installation. Warning at 75%, critical at 85%.

---

## Troubleshooting

### macOS

| Issue | Cause | Fix |
|-------|-------|-----|
| psutil not found | Not installed in system Python | Use `vm_stat` fallback with `active + wired` formula |
| Backend API returns connection refused | SwarmAI not running or crashed | Fall back to `ps` output; note backend is down |
| RSS values from `ps` don't match API | `ps` shows instantaneous; API caches 5s | Use API values when available |
| Process names truncated in `ps` | Default column width | Use `ps axo pid,rss,comm` for full names |
| MCP process names vary | Depends on config | Read from `mcp-dev.json` or match `mcp` in command |

### Linux

| Issue | Cause | Fix |
|-------|-------|-----|
| `free` shows low "available" but high "buff/cache" | Linux caches aggressively; normal | Use "available" column, NOT "free" |
| No SwarmAI processes found | App not running | Report "SwarmAI not running" in section 3 |
| Load average seems high | Includes I/O wait | Compare to core count from `nproc` |

---

## Quality Rules

### Output Validation Checklist

Before presenting the report, verify:
- [ ] All 4 sections present (Desktop Overview, Worst Offenders, SwarmAI Details, Suggestions)
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
| Show raw vm_stat or /proc/meminfo | Use psutil or convert to GB |
| Use `inactive` pages as "available" | Use `active + wired` as "used" |
| Skip SwarmAI section if backend is down | Fall back to `ps` output |
| Ignore orphaned processes | Always check PPID=1 |
| Just say "memory is high" | Say "28.1GB / 36GB (78%) — close Chrome (2.8GB) to drop to 70%" |
| Suggest killing system processes | Only suggest for user apps and orphans |
| Skip spawn budget status | Always report if backend is reachable |
| Run network speed test by default | Only if user specifically asks about network |
