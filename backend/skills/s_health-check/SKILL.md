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

- After `npm run build:all`
- After deploying a new build
- When user asks "is everything working" or "health check"
- Proactively when multiple P0/P1 bugs are being tracked

## Checks to Run

### 1. Backend Health
```bash
# Check backend is responding
curl -s http://localhost:23578/api/health | python3 -c "import sys,json; d=json.load(sys.stdin); print('✅ Backend UP' if d.get('status')=='ok' else '❌ Backend DOWN')" 2>/dev/null || echo "❌ Backend not reachable"
```

### 2. Context Files Loaded
```bash
# Verify all 11 context files exist in .context/
CTX_DIR="$HOME/.swarm-ai/SwarmWS/.context"
EXPECTED="SWARMAI.md IDENTITY.md SOUL.md AGENT.md USER.md STEERING.md TOOLS.md MEMORY.md EVOLUTION.md KNOWLEDGE.md PROJECTS.md"
MISSING=""
for f in $EXPECTED; do
  [ -f "$CTX_DIR/$f" ] || MISSING="$MISSING $f"
done
[ -z "$MISSING" ] && echo "✅ All 11 context files present" || echo "❌ Missing:$MISSING"
```

### 3. MCP Servers Configured
```bash
# Check user-mcp-servers.json exists and has entries
MCP_FILE="$HOME/.swarm-ai/user-mcp-servers.json"
if [ -f "$MCP_FILE" ]; then
  COUNT=$(python3 -c "import json; print(len(json.load(open('$MCP_FILE')).get('mcpServers',{})))" 2>/dev/null)
  echo "✅ MCP config: $COUNT servers configured"
else
  echo "⚠️ No user-mcp-servers.json found"
fi
```

### 4. MCP Servers Connected (requires running app)
```bash
# Check MCP status via API
curl -s http://localhost:23578/api/mcp/status 2>/dev/null | python3 -c "
import sys,json
try:
  data = json.load(sys.stdin)
  servers = data if isinstance(data, list) else data.get('servers', [])
  for s in servers:
    name = s.get('name','?')
    status = s.get('status','?')
    icon = '✅' if status == 'connected' else '❌'
    print(f'  {icon} {name}: {status}')
  if not servers: print('  ⚠️ No MCP servers reported')
except: print('  ⚠️ MCP status endpoint not available')
" || echo "  ⚠️ Backend not reachable for MCP check"
```

### 5. DailyActivity Pipeline
```bash
# Check recent DailyActivity files exist
DA_DIR="$HOME/.swarm-ai/SwarmWS/Knowledge/DailyActivity"
TODAY=$(date +%Y-%m-%d)
if [ -f "$DA_DIR/$TODAY.md" ]; then
  echo "✅ Today's DailyActivity exists"
else
  echo "⚠️ No DailyActivity for today (will be created on session close)"
fi
# Count undistilled files
UNDISTILLED=$(grep -rL "distilled: true" "$DA_DIR"/*.md 2>/dev/null | wc -l | tr -d ' ')
echo "  📊 Undistilled files: $UNDISTILLED (threshold: 3)"
```

### 6. Streaming Verification
```bash
# Check include_partial_messages is set in agent_manager.py
AGENT_MGR="$HOME/Desktop/SwarmAI-Workspace/swarmai/backend/core/agent_manager.py"
if grep -q "include_partial_messages.*True" "$AGENT_MGR" 2>/dev/null; then
  echo "✅ include_partial_messages=True in source"
else
  echo "❌ include_partial_messages not set to True — streaming will feel non-streaming"
fi
```

### 7. Sandbox Network Config
```bash
# Check sandbox_allowed_hosts in config
CONFIG="$HOME/.swarm-ai/config.json"
if [ -f "$CONFIG" ]; then
  HAS_HOSTS=$(python3 -c "import json; c=json.load(open('$CONFIG')); print('yes' if c.get('sandbox_allowed_hosts') else 'no')" 2>/dev/null)
  [ "$HAS_HOSTS" = "yes" ] && echo "✅ sandbox_allowed_hosts configured" || echo "⚠️ sandbox_allowed_hosts not in config (default '*' used)"
else
  echo "⚠️ No config.json — defaults will be used"
fi
```

### 8. Skills Symlinked
```bash
# Check skills are symlinked
SKILLS_DIR="$HOME/.swarm-ai/SwarmWS/.claude/skills"
if [ -d "$SKILLS_DIR" ]; then
  COUNT=$(ls -1 "$SKILLS_DIR" 2>/dev/null | wc -l | tr -d ' ')
  BROKEN=$(find "$SKILLS_DIR" -type l ! -exec test -e {} \; -print 2>/dev/null | wc -l | tr -d ' ')
  echo "✅ $COUNT skills symlinked ($BROKEN broken links)"
else
  echo "❌ Skills directory missing"
fi
```

## How to Run

Execute each check block sequentially. Collect results and present a summary table:

```
SwarmAI Health Check — YYYY-MM-DD HH:MM
────────────────────────────────────────
✅ Backend health
✅ Context files (11/11)
✅ MCP config (5 servers)
❌ MCP connected (2/5 — taskei-p-mcp, workplace-chat-mcp failed)
✅ DailyActivity pipeline
✅ Streaming config
✅ Sandbox network
✅ Skills (38 symlinked, 0 broken)
────────────────────────────────────────
Score: 7/8 — 1 issue needs attention
```

Report ❌ items with actionable fix suggestions. Don't just report status — tell the user what to do.
