---
name: workspace-backup
disable-model-invocation: true
description: Backup and restore SwarmAI workspace (memory, knowledge, projects, config, conversations) to/from a private GitHub repository.
tier: lazy
platform: all
---

# Workspace Backup & Restore

Manage workspace backups to a private GitHub repository. Your memory,
knowledge, projects, config, and conversation history are backed up daily
and can be restored on a new machine.

TRIGGER: "backup workspace", "backup now", "restore workspace", "backup status",
"configure backup", "when was last backup", "备份", "恢复workspace".
DO NOT USE: for git operations on the swarmai codebase (use workspace-git),
for file search (use workspace-finder), or for project CRUD (use project-manager).

## Commands

### Check Status
```bash
curl -s "http://localhost:$(cat ~/.swarm-ai/backend.json | python3 -c 'import sys,json;print(json.load(sys.stdin)["port"])')/api/system/backup/status" | python3 -m json.tool
```
Shows: last_backup timestamp, repo_url, schedule, enabled.

### Run Backup Now
```bash
curl -s -X POST "http://localhost:$(cat ~/.swarm-ai/backend.json | python3 -c 'import sys,json;print(json.load(sys.stdin)["port"])')/api/system/backup" | python3 -m json.tool
```
Exports DB tables, copies config, commits and pushes to GitHub.

### Configure Backup
```bash
curl -s -X PUT "http://localhost:$(cat ~/.swarm-ai/backend.json | python3 -c 'import sys,json;print(json.load(sys.stdin)["port"])')/api/system/backup/config" \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/USER/REPO.git", "token": "ghp_..."}' | python3 -m json.tool
```

### Alternative: Direct Python
When the API isn't available (e.g., daemon not running), use Python directly:
```python
import asyncio
from core.backup_manager import BackupManager

# Backup
result = asyncio.run(BackupManager().backup())
print(result)

# Status
print(BackupManager().get_status())

# Restore (on a fresh machine)
async def restore():
    mgr = BackupManager()
    async for event in mgr.restore(repo_url="https://github.com/USER/REPO.git"):
        print(event)
asyncio.run(restore())
```

## What Gets Backed Up

| Layer | Content | Size |
|-------|---------|------|
| L1 (git) | .context/, Knowledge/, Projects/, config | ~240MB |
| L2 (DB dump) | messages, sessions, todos, token_usage, etc. | ~8MB gz |
| L3 (skip) | embeddings, FTS, knowledge_chunks | Rebuilt on restore |

## Schedule

- **Daemon**: checks every hour, runs if >24h since last backup
- **launchd fallback**: `com.swarmai.backup` at 3:30 AM daily
- Both are idempotent — safe to have both active

## Token Storage

- **macOS**: Keychain (`com.swarmai.backup`)
- **Linux/Hive**: `~/.swarm-ai/.backup-token` (chmod 600)
- **CI/testing**: `SWARM_BACKUP_TOKEN` env var
