# Health Check — Post-Build Verification

Verify that SwarmAI critical subsystems are working after a build. Run all checks and report pass/fail for each.

## 🚨 MOMENTUM RULE — DO NOT STOP BETWEEN CHECKS

**"health check" = run ALL checks and report results. NO pause between checks.**
- Check passes → immediately run next check. Only report the full summary at the end.
- Only STOP for: user explicitly interrupts. Individual check failures are noted and reported in the final summary, not escalated mid-flow.

## When to Run

- After `./dev.sh build` or `./dev.sh quick`
- After deploying a new build
- When user asks "is everything working" or "health check"
- Proactively when multiple P0/P1 bugs are being tracked

## Checks to Run

Run checks in parallel where possible (1-3 have no dependencies). Use Bash tool for each.

### 0. Probe Self-Validation [run FIRST]

Several checks below grep live code / context files by path or section name. When
those refactor (file moves, MEMORY.md schema change), the probe silently mis-reports
— false-FAIL (section gone) or false-PASS (no match treated as fine). This block
validates the probe TARGETS before the checks trust them. Any `DRIFTED` line means
realign the corresponding check before believing its result (mirrors the Q0 gate in
s_chat-brain-check, and the loops-health P0 check).

```bash
python3 << 'PYEOF'
from pathlib import Path
root = Path.home() / "Desktop/SwarmAI-Workspace/swarmai"
ctx = Path.home() / ".swarm-ai/SwarmWS/.context"
drift = []
# Path-anchored probes
checks = [
    ("Check 6 streaming", root / "backend/core/prompt_builder.py", "include_partial_messages"),
    ("Check 10 binary dir", root / "desktop/src-tauri/binaries/python-backend-aarch64-apple-darwin", None),
]
for name, path, needle in checks:
    if not path.exists():
        drift.append(f"{name}: path missing {path}")
    elif needle and needle not in path.read_text(errors="ignore"):
        drift.append(f"{name}: '{needle}' not in {path.name}")
# Schema-anchored probes (Check 9): MEMORY.md / EVOLUTION.md live sections
mem = (ctx / "MEMORY.md").read_text(errors="ignore")
for sec in ["## Memory Index", "## Decisions", "## Open Threads"]:
    if sec not in mem:
        drift.append(f"Check 9 MEMORY: '{sec}' missing (schema drifted)")
evo = (ctx / "EVOLUTION.md").read_text(errors="ignore")
if "### CLASS A" not in evo and "### E001" not in evo:
    drift.append("Check 9 EVOLUTION: no CLASS/E### anchors (format drifted)")
if drift:
    print("FAIL Probe self-validation — DRIFTED targets:")
    for d in drift:
        print(f"  - {d}")
else:
    print("OK Probe self-validation — all probe targets present")
PYEOF
```

### 1. Backend Health (Dynamic Port Discovery)
```bash
# Port is RANDOM in production (Tauri portpicker). Dev mode uses 8000.
# Discover dynamically via psutil socket inspection.
source $SWARMAI_ROOT/backend/.venv/bin/activate && python3 << 'PYEOF'
import psutil, urllib.request, json

def find_backend_port():
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            info = p.info
            name = info["name"] or ""
            cmd = " ".join(info["cmdline"] or [])
            is_daemon = name.startswith("python-backend")
            is_dev = ("main.py" in cmd and "--port" in cmd and "backend" in cmd)
            if is_daemon or is_dev:
                for c in p.net_connections(kind="tcp"):
                    if c.status == "LISTEN":
                        return info["pid"], c.laddr.port, "production" if is_daemon else "dev"
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
            "STEERING.md","TOOLS.md","MEMORY.md","EVOLUTION.md","KNOWLEDGE.md"]
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

### 4. MCP Servers Enabled (requires running backend)
```bash
# Uses dynamic port discovery from Check 1.
# NOTE: the backend has no runtime "connected" endpoint — MCP connections are
# managed by the Claude SDK subprocess, not surfaced via HTTP. GET /api/mcp
# returns the MERGED config, which already pre-filters out disabled entries
# (merge_layers keeps only enabled != False), so it reports ACTIVE servers only
# — it cannot surface a disabled one. We report the active count as the signal.
source $SWARMAI_ROOT/backend/.venv/bin/activate && python3 << 'PYEOF'
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
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/mcp", timeout=2)
        data = json.loads(resp.read())
        servers = data if isinstance(data, list) else data.get("servers", [])
        for s in servers:
            print(f"  OK {s.get('name', '?')}: active")
        if not servers:
            print("  WARN No active MCP servers")
        else:
            print(f"  -> {len(servers)} active MCP server(s)")
    except Exception as e:
        print(f"  WARN Cannot reach /api/mcp: {e}")
else:
    print("SKIP Backend not running — cannot check MCP config")
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
# include_partial_messages=True is set where ClaudeAgentOptions is built:
# prompt_builder.py (NOT session_unit.py — that file only consumes the stream).
PROMPT_BUILDER="$HOME/Desktop/SwarmAI-Workspace/swarmai/backend/core/prompt_builder.py"
if grep -Eq "include_partial_messages\s*=\s*True" "$PROMPT_BUILDER" 2>/dev/null; then
  echo "OK Streaming config (include_partial_messages=True) found in prompt_builder.py"
else
  echo "WARN Cannot verify streaming config in prompt_builder.py — check manually"
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

# MEMORY.md — live 7-type governance schema (PRI01, 2026-06-17). The legacy
# "Recent Context / Key Decisions / Lessons Learned" sections were REMOVED;
# checking for them here made mem_ok permanently FALSE (stale probe). Assert
# the sections that actually exist today.
mem = (ctx / "MEMORY.md").read_text()
sections = ["## Memory Index", "## Decisions", "## Guidelines", "## Pitfalls", "## Open Threads"]
mem_ok = all(s in mem for s in sections)
p0 = mem.count("\N{LARGE RED CIRCLE}")
p1 = mem.count("\N{LARGE YELLOW CIRCLE}")
p2 = mem.count("\N{LARGE BLUE CIRCLE}")
print(f"{'OK' if mem_ok else 'FAIL'} MEMORY.md — all sections present: {mem_ok}")
print(f"  Open Threads: {p0} P0, {p1} P1, {p2} P2")

# EVOLUTION.md — live format: capability registry "### E0NN", correction ids
# "C0NN" inline + "### CLASS A/B/C" taxonomy. Legacy "### C\d+ / ### K\d+"
# headers no longer exist.
evo = (ctx / "EVOLUTION.md").read_text()
counts = {
    "E (capabilities)": len(re.findall(r"### E\d+", evo)),
    "O (optimizations)": len(re.findall(r"### O\d+", evo)),
    "C0NN (corrections)": len(set(re.findall(r"\bC0\d{2}\b", evo))),
    "CLASS": len(re.findall(r"^### CLASS [ABC]", evo, re.MULTILINE)),
}
evo_ok = counts["E (capabilities)"] >= 1 and counts["CLASS"] >= 1
print(f"{'OK' if evo_ok else 'FAIL'} EVOLUTION.md — " + " ".join(f"{k}:{v}" for k, v in counts.items()))

# STEERING.md weight
steer = (ctx / "STEERING.md").read_text()
tokens = len(steer) // 4
print(f"OK STEERING.md — {len(steer.splitlines())} lines, ~{tokens} tokens")
PYEOF
```

### 10. Dev Tools
```bash
echo "OK dev.sh exists" && test -x "$HOME/Desktop/SwarmAI-Workspace/swarmai/dev.sh" && echo "  executable: yes" || echo "  WARN not executable"
# Check backend binary age. PyInstaller builds in ONEDIR mode: the artifact is a
# DIRECTORY (python-backend-aarch64-apple-darwin/) containing the python-backend
# executable + _internal/. Use test -d on the dir; age from the inner executable.
BINDIR="$HOME/Desktop/SwarmAI-Workspace/swarmai/desktop/src-tauri/binaries/python-backend-aarch64-apple-darwin"
BINEXE="$BINDIR/python-backend"
if [ -d "$BINDIR" ] && [ -x "$BINEXE" ]; then
  AGE=$(( ($(date +%s) - $(stat -f %m "$BINEXE")) / 3600 ))
  SIZE=$(du -sh "$BINDIR" | cut -f1)
  echo "OK Backend binary (onedir): $SIZE, ${AGE}h old"
else
  echo "WARN No backend binary dir — run ./dev.sh build"
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
