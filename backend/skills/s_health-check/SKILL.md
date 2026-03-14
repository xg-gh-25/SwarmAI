---
name: health-check
description: Post-build verification of SwarmAI critical assumptions — streaming, context files, MCP, DailyActivity pipeline.
trigger:
  - health check
  - verify build
  - smoke test
  - post-build check
  - is everything working
do_not_use:
  - ping endpoints (use curl directly)
  - monitoring dashboards
  - load testing
---

# Health Check — Post-Build Verification

Verify that SwarmAI critical subsystems are working after a build. Run all checks and report pass/fail for each.

## When to Run

- After `./dev.sh build` or `./dev.sh quick`
- After deploying a new build
- When user asks "is everything working" or "health check"
- Proactively when multiple P0/P1 bugs are being tracked

## Checks to Run

Run checks in parallel where possible (1-3 have no dependencies). Use Bash tool for each.

### 1. Backend Health
```bash
# Try both dev port (8000) and prod port (23578)
for PORT in 8000 23578; do
  RESP=$(curl -s --max-time 2 "http://localhost:$PORT/api/health" 2>/dev/null)
  if echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('status')=='ok' else 1)" 2>/dev/null; then
    echo "OK Backend UP on port $PORT"
    exit 0
  fi
done
echo "FAIL Backend not reachable on 8000 or 23578"
```

### 2. Context Files
```bash
python3 << 'PYEOF'
from pathlib import Path
ctx = Path.home() / ".swarm-ai/SwarmWS/.context"
expected = ["SWARMAI.md","IDENTITY.md","SOUL.md","AGENT.md","USER.md",
            "STEERING.md","TOOLS.md","MEMORY.md","EVOLUTION.md","KNOWLEDGE.md","PROJECTS.md"]
missing = [f for f in expected if not (ctx / f).exists()]
if missing:
    print(f"FAIL Missing: {', '.join(missing)}")
else:
    print(f"OK All 11 context files present")
    # Check permissions on system files (P0-P3)
    import os
    system = ["SWARMAI.md","IDENTITY.md","SOUL.md","AGENT.md"]
    bad = [f for f in system if oct(os.stat(ctx/f).st_mode)[-3:] != "444"]
    if bad:
        print(f"  WARN System files not readonly: {bad}")
    else:
        print(f"  OK P0-P3 readonly (444)")
PYEOF
```

### 3. MCP Servers Configured
```bash
python3 << 'PYEOF'
import json
from pathlib import Path
mcp_file = Path.home() / ".swarm-ai/user-mcp-servers.json"
if not mcp_file.exists():
    print("WARN No user-mcp-servers.json")
else:
    data = json.loads(mcp_file.read_text())
    # Handle both list format and dict format
    if isinstance(data, list):
        servers = data
        print(f"OK MCP config: {len(servers)} servers")
        for s in servers:
            print(f"  - {s.get('name', s.get('id', '?'))}")
    elif isinstance(data, dict):
        servers = data.get("mcpServers", {})
        print(f"OK MCP config: {len(servers)} servers")
        for name in servers:
            print(f"  - {name}")
PYEOF
```

### 4. MCP Servers Connected (requires running backend)
```bash
# Try both ports
for PORT in 8000 23578; do
  RESP=$(curl -s --max-time 2 "http://localhost:$PORT/api/mcp/status" 2>/dev/null)
  if [ -n "$RESP" ]; then
    echo "$RESP" | python3 -c "
import sys,json
try:
  data = json.load(sys.stdin)
  servers = data if isinstance(data, list) else data.get('servers', [])
  for s in servers:
    name = s.get('name','?')
    status = s.get('status','?')
    icon = 'OK' if status == 'connected' else 'FAIL'
    print(f'  {icon} {name}: {status}')
  if not servers: print('  WARN No MCP servers reported')
except: print('  WARN Cannot parse MCP status')
"
    exit 0
  fi
done
echo "SKIP Backend not running — cannot check MCP connections"
```

### 5. DailyActivity Pipeline
```bash
python3 << 'PYEOF'
from pathlib import Path
da_dir = Path.home() / ".swarm-ai/SwarmWS/Knowledge/DailyActivity"
from datetime import date
today = date.today().isoformat()
files = sorted(da_dir.glob("*.md"))
undistilled = [f for f in files if "distilled: true" not in f.read_text()[:300]]
if (da_dir / f"{today}.md").exists():
    print(f"OK Today's DailyActivity exists")
else:
    print(f"WARN No DailyActivity for today (created on session close)")
print(f"  Files: {len(files)} total, {len(undistilled)} undistilled (threshold: 2)")
PYEOF
```

### 6. Streaming Config
```bash
AGENT_MGR="$HOME/Desktop/SwarmAI-Workspace/swarmai/backend/core/agent_manager.py"
if grep -q "include_partial_messages.*True" "$AGENT_MGR" 2>/dev/null; then
  echo "OK include_partial_messages=True"
else
  echo "FAIL include_partial_messages not True — streaming will feel non-streaming"
fi
```

### 7. Sandbox & Config
```bash
python3 << 'PYEOF'
import json
from pathlib import Path
# Check both possible locations
for loc in [Path.home()/".swarm-ai/SwarmWS/config.json", Path.home()/".swarm-ai/config.json"]:
    if loc.exists():
        c = json.loads(loc.read_text())
        hosts = c.get("sandbox_allowed_hosts")
        wpaths = c.get("sandbox_additional_write_paths")
        print(f"OK config.json found at {loc}")
        print(f"  sandbox_allowed_hosts: {hosts or '(not set, default * used)'}")
        print(f"  sandbox_additional_write_paths: {len(wpaths) if wpaths else 0} paths")
        break
else:
    print("WARN No config.json found — defaults used")
PYEOF
```

### 8. Skills
```bash
python3 << 'PYEOF'
from pathlib import Path
skills_dir = Path.home() / ".swarm-ai/SwarmWS/.claude/skills"
if not skills_dir.exists():
    print("FAIL Skills directory missing")
else:
    skills = [d for d in skills_dir.iterdir() if d.name.startswith("s_")]
    broken = [s for s in skills if s.is_symlink() and not s.resolve().exists()]
    print(f"OK {len(skills)} skills ({len(broken)} broken symlinks)")
    if broken:
        for b in broken:
            print(f"  BROKEN: {b.name}")
PYEOF
```

### 9. MEMORY.md & EVOLUTION.md Health
```bash
python3 << 'PYEOF'
from pathlib import Path
import re

ctx = Path.home() / ".swarm-ai/SwarmWS/.context"

# MEMORY.md
mem = (ctx / "MEMORY.md").read_text()
sections = ["Recent Context", "Key Decisions", "Lessons Learned", "COE Registry", "Open Threads"]
mem_ok = all(s in mem for s in sections)
p0 = mem.count("\N{LARGE RED CIRCLE}")
p1 = mem.count("\N{LARGE YELLOW CIRCLE}")
p2 = mem.count("\N{LARGE BLUE CIRCLE}")
print(f"{'OK' if mem_ok else 'FAIL'} MEMORY.md — all sections present: {mem_ok}")
print(f"  Open Threads: {p0} P0, {p1} P1, {p2} P2")

# EVOLUTION.md
evo = (ctx / "EVOLUTION.md").read_text()
counts = {
    "E": len(re.findall(r"### E\d+", evo)),
    "O": len(re.findall(r"### O\d+", evo)),
    "C": len(re.findall(r"### C\d+", evo)),
    "K": len(re.findall(r"### K\d+", evo)),
}
print(f"OK EVOLUTION.md — E:{counts['E']} O:{counts['O']} C:{counts['C']} K:{counts['K']}")

# STEERING.md weight
steer = (ctx / "STEERING.md").read_text()
tokens = len(steer) // 4
print(f"OK STEERING.md — {len(steer.splitlines())} lines, ~{tokens} tokens")
PYEOF
```

### 10. Dev Tools
```bash
echo "OK dev.sh exists" && test -x "$HOME/Desktop/SwarmAI-Workspace/swarmai/dev.sh" && echo "  executable: yes" || echo "  WARN not executable"
# Check sidecar binary age
BINARY="$HOME/Desktop/SwarmAI-Workspace/swarmai/desktop/src-tauri/binaries/python-backend-aarch64-apple-darwin"
if [ -f "$BINARY" ]; then
  AGE=$(( ($(date +%s) - $(stat -f %m "$BINARY")) / 3600 ))
  SIZE=$(du -h "$BINARY" | cut -f1)
  echo "OK Sidecar binary: $SIZE, ${AGE}h old"
else
  echo "WARN No sidecar binary — run ./dev.sh build"
fi
```

## How to Run

Execute each check using Bash tool. Run 1-3 in parallel (no dependencies), then 4-10. Present summary table:

```
SwarmAI Health Check — YYYY-MM-DD HH:MM
────────────────────────────────────────
OK  Backend health (port 8000)
OK  Context files (11/11, P0-P3 readonly)
OK  MCP config (5 servers)
FAIL MCP connected (2/5 failed)
OK  DailyActivity (3 files, 0 undistilled)
OK  Streaming config
OK  Sandbox & config
OK  Skills (43, 0 broken)
OK  MEMORY + EVOLUTION health
OK  Dev tools
────────────────────────────────────────
Score: 9/10 — 1 issue needs attention
```

Replace OK/FAIL/WARN/SKIP with emoji in final output. Report FAIL items with actionable fix suggestions.
