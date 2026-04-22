# Health Check — Post-Build Verification

Verify that SwarmAI critical subsystems are working after a build. Run all checks and report pass/fail for each.

## When to Run

- After `./dev.sh build` or `./dev.sh quick`
- After deploying a new build
- When user asks "is everything working" or "health check"
- Proactively when multiple P0/P1 bugs are being tracked

## Checks to Run

Run checks in parallel where possible (1-3 have no dependencies). Use Bash tool for each.

### 1. Backend Health (Dynamic Port Discovery)
```bash
# Port is RANDOM in production (Tauri portpicker). Dev mode uses 8000.
# Discover dynamically via psutil socket inspection.
source /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/activate && python3 << 'PYEOF'
import psutil, urllib.request, json

def find_backend_port():
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
                        return info["pid"], c.laddr.port, "production" if is_sidecar else "dev"
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None, None, None

pid, port, mode = find_backend_port()
if port:
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
        data = json.loads(resp.read())
        if data.get("status") == "healthy":
            print(f"OK Backend UP on port {port} (PID {pid}, {mode})")
        else:
            print(f"FAIL Backend on port {port} returned: {data}")
    except Exception as e:
        print(f"FAIL Backend process found (PID {pid}, port {port}) but /health unreachable: {e}")
else:
    print("FAIL Backend process not found (no python-backend or main.py --port)")
PYEOF
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
# Uses dynamic port discovery from Check 1
source /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/activate && python3 << 'PYEOF'
import psutil, urllib.request, json

def find_backend_port():
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            info = p.info
            name = info["name"] or ""
            cmd = " ".join(info["cmdline"] or [])
            if name.startswith("python-backend") or ("main.py" in cmd and "--port" in cmd and "backend" in cmd):
                for c in p.net_connections(kind="tcp"):
                    if c.status == "LISTEN":
                        return c.laddr.port
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None

port = find_backend_port()
if port:
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/mcp/status", timeout=2)
        data = json.loads(resp.read())
        servers = data if isinstance(data, list) else data.get("servers", [])
        for s in servers:
            name = s.get("name", "?")
            status = s.get("status", "?")
            icon = "OK" if status == "connected" else "FAIL"
            print(f"  {icon} {name}: {status}")
        if not servers:
            print("  WARN No MCP servers reported")
    except Exception as e:
        print(f"  WARN Cannot reach MCP status: {e}")
else:
    print("SKIP Backend not running — cannot check MCP connections")
PYEOF
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
# agent_manager.py was replaced by session_unit.py in v7 re-architecture (March 2026)
SESSION_UNIT="$HOME/Desktop/SwarmAI-Workspace/swarmai/backend/core/session_unit.py"
if grep -q "include_partial_messages.*True\|output_format.*streaming" "$SESSION_UNIT" 2>/dev/null; then
  echo "OK Streaming config found in session_unit.py"
else
  echo "WARN Cannot verify streaming config in session_unit.py — check manually"
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

## Verification

Before marking this task complete, show evidence for each:

- [ ] **All 10 check categories ran** — every check (Backend Health, Context Files, MCP Config, MCP Connected, DailyActivity, Streaming Config, Sandbox & Config, Skills, MEMORY+EVOLUTION, Dev Tools) was executed with command output captured
- [ ] **Pass/fail stated per check** — each check has an explicit OK, FAIL, WARN, or SKIP status in the summary table
- [ ] **Failing checks have diagnosis** — any FAIL or WARN result includes the specific error output and an actionable fix suggestion (e.g., "run `./dev.sh build`", "fix permissions with `chmod 444`")
- [ ] **Summary score reported** — final line shows the score (e.g., "9/10") and count of issues needing attention
