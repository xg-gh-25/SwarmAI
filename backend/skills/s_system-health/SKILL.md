---
name: system-health
description: >
  Quick system health check (macOS/Linux) with battery, RAM, CPU, disk status and actionable recommendations.
  Diagnoses slow machines, high memory usage, stuck processes, and low disk space in seconds.
  TRIGGER: "system health", "mac health", "linux health", "battery check", "ram usage",
  "what's eating memory", "mac running slow", "system running slow", "check my system",
  "why is my laptop slow".
  DO NOT USE: for AWS resource monitoring or CloudWatch logs (use cloudwatch-log-analysis),
  SwarmAI app health (use health-check skill), or security scanning (use bsc-security-scanner).
---

# System Health Check

**Why?** Quickly diagnose if your system is struggling and get actionable fixes — no Activity Monitor or htop required.

**Supported platforms:** macOS, Linux

## Quick Start

```
"check my system health" → battery, RAM, CPU, disk summary + recommendations
```

---

## Workflow

### Step 0: Detect OS

```bash
uname -s
```

- `Darwin` = macOS
- `Linux` = Linux

Use the result to select the correct commands in all subsequent steps. If detection fails, ask the user.

### Step 1: Gather System Metrics

Run the appropriate commands **in parallel** based on OS.

#### macOS

```bash
# Battery
pmset -g batt

# RAM — detect page size first (16384 on Apple Silicon, 4096 on Intel)
# vm_stat reports in pages; convert: pages × pagesize / 1048576 = MB
pagesize=$(sysctl -n vm.pagesize)
system_profiler SPHardwareDataType | grep "Memory"
vm_stat

# CPU core count (for load assessment)
sysctl -n hw.ncpu

# Top processes by memory (top 5)
ps aux -m | head -6

# Top processes by CPU (top 5)
ps aux -r | head -6

# Disk usage
df -h /
```

> **Network speed** (`networkQuality`) takes 10-15 seconds. Run it LAST and
> only if the user asked about network or you've already presented the fast
> metrics above. Present battery/RAM/CPU/disk results first, then append
> the network line when the test finishes.

```bash
# Network speed (run AFTER presenting other metrics)
networkQuality
```

#### Linux

```bash
# Battery (laptops only; skip if path missing)
cat /sys/class/power_supply/BAT0/capacity 2>/dev/null
cat /sys/class/power_supply/BAT0/status 2>/dev/null

# RAM (use "available" column, NOT "free")
free -h

# CPU core count (for comparing against load average)
nproc

# Top processes by memory (top 5)
ps aux --sort=-%mem | head -6

# Top processes by CPU (top 5)
ps aux --sort=-%cpu | head -6

# Disk usage
df -h /

# CPU load average
cat /proc/loadavg

# Network speed (if speedtest-cli available, otherwise skip)
which speedtest-cli > /dev/null 2>&1 && speedtest-cli --simple || echo "speedtest-cli not installed, skipping network test"
```

> **Validation checkpoint**: Verify all commands succeeded before proceeding.
> - macOS: If `pmset` returns nothing → desktop Mac, skip battery. If `vm_stat` fails → fall back to `top -l 1`.
> - Linux: If `/sys/class/power_supply/BAT0/` doesn't exist → desktop or VM, skip battery. If `free` is missing → fall back to `cat /proc/meminfo`.

### Step 2: Format Concise Output

Present as a brief summary, NOT tables. Example format:

```
🔋 Battery: 73% (discharging)
💾 RAM: 17.1GB / 24GB used (71%)
⚡ CPU: Light load
💿 Disk: 142GB / 500GB used (28%)
🌐 Network: ⬇️ 262 Mbps ⬆️ 26 Mbps (36ms latency)

Top memory: chrome (478MB), code (320MB), java (310MB)
Top CPU: chrome (3.2%), code (2.1%), java (0.8%)
```

For Linux, if `speedtest-cli` is not installed, omit the Network line and note: "(install `speedtest-cli` for network speed test)"

### Step 3: Analyze & Recommend

> Keep output concise — users want a quick health check, not a system report.

Apply these thresholds and provide recommendations:

**Battery:**
- < 20%: "⚠️ Consider plugging in soon"
- < 10%: "🔴 Critical, plug in now"

**RAM:**
- \> 85% used: "⚠️ Memory pressure high"
- \> 95% used: "🔴 System may slow down, consider closing apps"
- Any single process > 2GB: Flag it for potential action

**CPU:**
- Any process > 80% sustained: "⚠️ [Process] using significant CPU"
- Any process > 150%: "🔴 [Process] may be stuck, consider force-quitting"
- Linux load average > number of cores: "⚠️ System overloaded"

**Disk:**
- \> 85% used: "⚠️ Disk space getting low"
- \> 95% used: "🔴 Disk nearly full, free space urgently"

**Network:**
- Download < 10 Mbps: "⚠️ Slow download speed"
- Upload < 5 Mbps: "⚠️ Slow upload speed"
- Latency > 100ms: "⚠️ High latency, may affect video calls"

**Common macOS issues:**

| Condition | Recommendation |
|-----------|----------------|
| mds_stores high CPU/RAM | "Spotlight indexing, will settle down" |
| kernel_task high CPU | "Thermal throttling, check ventilation" |
| WindowServer high | "GPU load from UI, normal if using many windows" |
| Inactive app using >1GB | "Consider quitting [app] to free memory" |

**Common Linux issues:**

| Condition | Recommendation |
|-----------|----------------|
| kswapd0 high CPU | "Kernel swapping heavily, RAM is under pressure" |
| journald high CPU/RAM | "Systemd journal bloated, run `journalctl --vacuum-size=500M`" |
| tracker-miner high CPU | "GNOME file indexer, will settle down (or disable with `tracker3 daemon -k`)" |
| snapd high CPU/RAM | "Snap daemon doing background updates, will settle" |
| Xorg/Xwayland high CPU | "Display server under load, normal with many windows" |
| OOM killer active in dmesg | "System ran out of memory recently, check `dmesg \| grep -i oom`" |
| High swap usage | "System swapping to disk, close apps or add RAM" |

### Step 4: Offer Actions

> Only offer to kill a process if it's clearly stuck (>150% CPU) or the user explicitly asks.

If issues found, offer:
- "Want me to kill [process]?" (if clearly stuck)
- "Should I check what's causing [issue]?"
- Linux disk full: "Want me to find large files? (`find / -xdev -type f -size +500M`)"
- Linux high swap: "Want me to check swap usage per process?"

If healthy:
- End with "✅ System looks healthy, no action needed"

## Examples

### Example 1: Healthy macOS

```
🔋 Battery: 73% (discharging, ~20h remaining)
💾 RAM: 17.1GB / 24GB (71%) — healthy
⚡ CPU: Light load (< 10% average)
💿 Disk: 142GB / 500GB (28%) — healthy
🌐 Network: ⬇️ 262 Mbps ⬆️ 26 Mbps (36ms latency)

Top memory: Amp (478MB), Passwords (320MB), Spotlight (310MB)
Top CPU: WindowServer (1.0%), Amp (0.7%), Finder (0.3%)

✅ System looks healthy — no action needed
```

### Example 2: macOS Under Pressure

```
🔋 Battery: 8% (discharging, ~45min remaining)
   🔴 Critical — plug in now!
💾 RAM: 22.8GB / 24GB (95%)
   🔴 Memory pressure critical — consider closing apps
⚡ CPU: High load
💿 Disk: 380GB / 500GB (76%) — healthy

Top memory: Chrome (4.2GB), Slack (1.8GB), Docker (1.5GB)
Top CPU: node (187%), Chrome Helper (12%), Docker (8%)

🔴 node at 187% CPU — likely stuck or in infinite loop
⚠️ Chrome using 4.2GB — consider closing unused tabs

Want me to kill the stuck node process (PID 12847)?
```

### Example 3: Linux Under Pressure

```
💾 RAM: 14.8GB / 16GB (92%)
   ⚠️ Memory pressure high
   Swap: 3.2GB / 4GB used — system is swapping heavily
⚡ CPU: High load (load avg: 8.72, 7.15, 5.90 on 4 cores)
   ⚠️ Load average well above core count — system overloaded
💿 Disk: 186GB / 200GB (93%)
   ⚠️ Disk space getting low

Top memory: java (6.1GB), chrome (3.2GB), docker (2.8GB)
Top CPU: java (145%), chrome (38%), kswapd0 (22%)

🔴 java at 6.1GB RAM and 145% CPU — may be stuck or leaking memory
⚠️ kswapd0 at 22% — kernel swapping heavily due to RAM pressure

Want me to:
- Kill the java process (PID 4521)?
- Find large files eating disk space?
```

## Troubleshooting

### macOS

| Issue | Cause | Fix |
|-------|-------|-----|
| `pmset` returns nothing | No battery (desktop Mac) | Skip battery section |
| `vm_stat` weird numbers | Values are in pages | Get page size via `sysctl -n vm.pagesize` (16384 on Apple Silicon, 4096 on Intel), then multiply pages × pagesize / 1048576 for MB |
| Process names truncated | `ps` default column width | Use `ps aux -m -o pid,rss,comm` for full names |
| RAM numbers don't add up | macOS uses compressed/cached memory | Focus on "active + wired" as true usage |
| High CPU but system feels fine | Brief spikes are normal | Only flag if sustained >30 seconds |

### Linux

| Issue | Cause | Fix |
|-------|-------|-----|
| No battery info | Desktop, server, or VM | Skip battery section |
| `free` shows low "available" but high "buff/cache" | Linux caches aggressively; this is normal | Use "available" column, not "free" |
| Load average seems high | Load includes I/O wait, not just CPU | Compare to core count; check `iostat` for disk bottleneck |
| `speedtest-cli` not found | Not installed by default | Suggest `sudo apt install speedtest-cli` or `pip install speedtest-cli` |
| Process names show as `[kworker/...]` | Kernel threads | Ignore these; focus on user-space processes |
| `ps` sort flags differ | GNU vs BSD ps | Use `--sort=-%mem` on Linux (not `-m`) |
| Swap usage high but RAM not full | Swappiness setting too aggressive | Check `cat /proc/sys/vm/swappiness`; suggest lowering to 10 |

---

## Quality Rules

### Output Validation Checklist

Before presenting results, verify:
- [ ] OS detected and correct commands used
- [ ] All relevant sections present (Battery if laptop, RAM, CPU, Disk, Network if tool available)
- [ ] Percentages calculated correctly (used/total × 100)
- [ ] Top processes listed for both memory AND CPU
- [ ] Recommendations match thresholds (not arbitrary)
- [ ] Emojis used consistently (🔋💾⚡💿🌐 for sections, ⚠️🔴✅ for status)
- [ ] Linux: "available" memory used (not "free") from `free` output
- [ ] Linux: load average compared against core count

### Anti-Patterns to Avoid

| Don't | Do Instead |
|-------|------------|
| Show raw `vm_stat` or `/proc/meminfo` output | Convert to human-readable MB/GB |
| List 10+ processes | Top 3-5 only |
| Use technical jargon | Plain language ("memory pressure" not "page faults") |
| Recommend killing system processes | Only suggest for user apps |
| Give advice without thresholds | Always cite the threshold being exceeded |
| Treat Linux "free" as available memory | Use the "available" column from `free` |
| Panic about high buff/cache on Linux | Explain this is normal and reclaimable |
| Run `networkQuality` on Linux | Use `speedtest-cli` or skip gracefully |

### Naming Conventions

- Battery states: "charging", "discharging", "fully charged", "not charging"
- RAM levels: "healthy" (<85%), "high" (85-95%), "critical" (>95%)
- CPU levels: "light" (<30%), "moderate" (30-60%), "high" (>60%)
- CPU load (Linux): "light" (< cores/2), "moderate" (cores/2 to cores), "high" (> cores)
- Network levels: "slow" (download <10Mbps), "moderate" (10-50Mbps), "fast" (>50Mbps)
- Disk levels: "healthy" (<85%), "low" (85-95%), "critical" (>95%)

---

## Testing

### Evaluation Scenarios

| Scenario | Input Condition | Expected Behavior |
|----------|-----------------|-------------------|
| Healthy system | RAM <85%, CPU <30%, Battery >20% | Shows "✅ System looks healthy" |
| Low battery | Battery <10% | Shows 🔴 critical warning |
| High RAM | RAM >95% | Shows 🔴 + suggests closing apps |
| Stuck process | Any process >150% CPU | Offers to kill with PID |
| Desktop Mac | No battery (iMac, Mac Mini, Mac Pro) | Skips battery section gracefully |
| Linux VM/server | No battery path | Skips battery section gracefully |
| Memory hog | Single process >2GB | Flags for potential action |
| Slow network | Download <10 Mbps | Shows ⚠️ slow download warning |
| High latency | Latency >100ms | Shows ⚠️ latency warning |
| Disk nearly full | Disk >95% | Shows 🔴 + suggests cleanup |
| Linux high load | Load avg > core count | Shows ⚠️ overloaded warning |
| Linux high swap | Swap >50% used | Shows ⚠️ swapping warning |
| Linux no speedtest | `speedtest-cli` missing | Skips network, suggests install |
| Linux buff/cache high | "free" low but "available" fine | Reports healthy, does NOT panic |
