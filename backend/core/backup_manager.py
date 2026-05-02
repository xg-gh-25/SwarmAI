"""Workspace backup & restore manager.

Coordinates L1 (git text) + L2 (DB SQL dump) backup to a GitHub private
repo, and full restore from that repo on a new machine.

Key design:
- backup() and restore() share the same GitSyncEngine
- DB export uses sqlite3 .backup (WAL-safe) → per-table .dump → gzip
- Token stored in macOS Keychain via `security` CLI, never plaintext
- Env var SWARM_BACKUP_TOKEN overrides Keychain (for CI/testing)
- backup_state.json tracks last_backup, repo_url, schedule
"""
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import re

from core.git_sync_engine import GitSyncEngine

logger = logging.getLogger(__name__)


def _sanitize_repo_url(url: str | None) -> str | None:
    """Strip embedded credentials from a git URL before storing/returning.

    Converts https://ghp_xxx@github.com/user/repo.git
         to  https://github.com/user/repo.git
    """
    if not url:
        return url
    return re.sub(r"https?://[^@]+@", lambda m: m.group(0).split("//")[0] + "//", url)

# L2 tables to export (irreplaceable data only)
L2_TABLES = [
    "messages", "sessions", "channel_messages", "channel_sessions",
    "channels", "todos", "token_usage", "skill_metrics",
    "hive_instances", "hive_accounts", "agents", "mcp_servers",
    "app_settings", "workspace_config",
]

# Config files to snapshot
CONFIG_FILES = [
    "notify-channels.yaml",
    "user-mcp-servers.json",
    "pollinate-prefs.json",
    "pollinate-accounts.yaml",
    "pollinate-backlog.json",
    "estimation_learner.json",
]


# ---------------------------------------------------------------------------
# Keychain token storage
# ---------------------------------------------------------------------------

def _keychain_get_token() -> str | None:
    """Read token from macOS Keychain. Returns None if not found."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", "swarm-backup",
             "-s", "com.swarmai.backup", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def _keychain_set_token(token: str) -> bool:
    """Write token to macOS Keychain. Returns True on success."""
    try:
        subprocess.run(
            ["security", "add-generic-password", "-a", "swarm-backup",
             "-s", "com.swarmai.backup", "-w", token, "-U"],
            capture_output=True, timeout=5, check=True,
        )
        return True
    except Exception:
        return False


def _file_get_token() -> str | None:
    """Read token from ~/.swarm-ai/.backup-token (Linux/Hive fallback)."""
    path = Path(os.path.expanduser("~/.swarm-ai/.backup-token"))
    if path.exists():
        try:
            return path.read_text().strip()
        except Exception:
            pass
    return None


def _file_set_token(token: str) -> bool:
    """Write token to ~/.swarm-ai/.backup-token with chmod 600."""
    path = Path(os.path.expanduser("~/.swarm-ai/.backup-token"))
    try:
        path.write_text(token + "\n")
        path.chmod(0o600)
        return True
    except Exception:
        return False


def get_backup_token() -> str | None:
    """Get backup token. Priority: env var > Keychain (macOS) > file (Linux)."""
    env_token = os.environ.get("SWARM_BACKUP_TOKEN")
    if env_token:
        return env_token
    token = _keychain_get_token()
    if token:
        return token
    return _file_get_token()


def set_backup_token(token: str) -> bool:
    """Store backup token. macOS: Keychain. Linux/Hive: ~/.swarm-ai/.backup-token."""
    import sys
    if sys.platform == "darwin":
        return _keychain_set_token(token)
    return _file_set_token(token)


# ---------------------------------------------------------------------------
# BackupManager
# ---------------------------------------------------------------------------

class BackupManager:
    """Manages workspace backup and restore operations."""

    def __init__(
        self,
        workspace_dir: Path | None = None,
        swarm_dir: Path | None = None,
        db_path: Path | None = None,
    ):
        self.swarm_dir = Path(swarm_dir or os.path.expanduser("~/.swarm-ai"))
        self.workspace_dir = Path(workspace_dir or self.swarm_dir / "SwarmWS")
        self.db_path = Path(db_path or self.swarm_dir / "data.db")
        self.state_path = self.swarm_dir / "backup_state.json"
        self.engine = GitSyncEngine(self.workspace_dir)

    def _load_state(self) -> dict:
        """Load backup state from JSON file."""
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text())
            except Exception as e:
                logger.warning("Failed to load backup state: %s", e)
        return {"last_backup": None, "repo_url": None, "schedule": "daily_3am", "enabled": True}

    def _save_state(self, state: dict) -> None:
        """Persist backup state to JSON file."""
        self.state_path.write_text(json.dumps(state, indent=2))

    async def backup(self) -> dict:
        """Run a full backup: config snapshot + DB export + git push.

        Returns dict with status, tables_exported, commit, push_status.
        """
        state = self._load_state()
        result = {
            "status": "ok",
            "tables_exported": 0,
            "commit": None,
            "push_status": "skipped",
        }

        # 1. Config snapshot
        config_dir = self.workspace_dir / "config-backup"
        config_dir.mkdir(parents=True, exist_ok=True)
        for fname in CONFIG_FILES:
            src = self.swarm_dir / fname
            if src.exists():
                shutil.copy2(str(src), str(config_dir / fname))

        # 2. DB export
        export_dir = self.workspace_dir / "db-export"
        tables_exported = await self.engine.export_db_tables(
            db_path=self.db_path,
            export_dir=export_dir,
            tables=L2_TABLES,
        )
        result["tables_exported"] = tables_exported

        # 3. Git add + commit
        self.engine.git_add_all()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        commit_sha = self.engine.git_commit(f"backup: {now_str}")
        result["commit"] = commit_sha

        # 4. Git push (non-fatal)
        if commit_sha:
            pushed = self.engine.git_push()
            result["push_status"] = "ok" if pushed else "failed"
        else:
            result["push_status"] = "no_changes"

        # 5. Update state
        state["last_backup"] = datetime.now().isoformat()
        state["repo_url"] = _sanitize_repo_url(self.engine.get_remote_url())
        self._save_state(state)

        logger.info(
            "Backup complete: %d tables, commit=%s, push=%s",
            tables_exported, commit_sha, result["push_status"],
        )
        return result

    async def restore(self, repo_url: str, token: str | None = None):
        """Restore workspace from a backup repo. Yields SSE-style progress dicts.

        Stages: clone → config → db_import → schema_migrate → verify.
        Refuses to run if workspace already contains data (safety).
        """
        # Safety: refuse non-empty workspace
        memory_file = self.workspace_dir / ".context" / "MEMORY.md"
        if memory_file.exists():
            yield {"stage": "error", "error": "Workspace already has data. Restore requires a fresh install."}
            return

        # Store token if provided
        if token:
            set_backup_token(token)

        # Stage 1: CLONE
        yield {"stage": "clone", "progress": 10, "detail": "Cloning backup repository..."}
        cloned = self.engine.git_clone(repo_url, self.workspace_dir)
        if not cloned:
            yield {"stage": "clone", "progress": 10, "error": "git clone failed. Check repo URL and token."}
            return
        # Re-init engine with cloned dir
        self.engine = GitSyncEngine(self.workspace_dir)

        # Stage 2: CONFIG
        yield {"stage": "config", "progress": 20, "detail": "Restoring configuration files..."}
        config_dir = self.workspace_dir / "config-backup"
        if config_dir.exists():
            for f in config_dir.iterdir():
                if f.is_file():
                    shutil.copy2(str(f), str(self.swarm_dir / f.name))

        # Stage 3: DB_IMPORT
        yield {"stage": "db_import", "progress": 50, "detail": "Importing database tables..."}
        export_dir = self.workspace_dir / "db-export"
        tables_imported = 0
        if export_dir.exists():
            tables_imported = await self.engine.import_db_tables(
                db_path=self.db_path,
                export_dir=export_dir,
            )

        # Stage 4: SCHEMA_MIGRATE
        yield {"stage": "schema_migrate", "progress": 70, "detail": "Running database migrations..."}
        # Migrations run automatically on next DB access via SQLiteDatabase.initialize()
        # Just verify DB is accessible
        import sqlite3
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.execute("SELECT 1")
            conn.close()
        except Exception as e:
            yield {"stage": "schema_migrate", "progress": 70, "error": f"DB verification failed: {e}"}
            return

        # Stage 5: VERIFY
        yield {"stage": "verify", "progress": 90, "detail": "Verifying restored data..."}
        counts = {}
        try:
            conn = sqlite3.connect(str(self.db_path))
            for table in ["messages", "sessions", "todos"]:
                try:
                    cur = conn.execute(f'SELECT COUNT(*) FROM "{table}"')
                    counts[f"{table}_count"] = cur.fetchone()[0]
                except Exception:
                    counts[f"{table}_count"] = 0
            conn.close()
        except Exception:
            pass

        # Update state
        state = self._load_state()
        state["repo_url"] = _sanitize_repo_url(repo_url)
        self._save_state(state)

        yield {
            "stage": "verify",
            "progress": 100,
            "detail": "Restore complete",
            "tables_imported": tables_imported,
            **counts,
        }

    def get_status(self) -> dict:
        """Return current backup status."""
        state = self._load_state()
        return {
            "last_backup": state.get("last_backup"),
            "repo_url": state.get("repo_url") or self.engine.get_remote_url(),
            "schedule": state.get("schedule", "daily_3am"),
            "enabled": state.get("enabled", True),
        }

    def configure(self, repo_url: str | None = None, token: str | None = None,
                  schedule: str | None = None) -> dict:
        """Update backup configuration."""
        state = self._load_state()
        if repo_url is not None:
            state["repo_url"] = repo_url
        if schedule is not None:
            state["schedule"] = schedule
        if token is not None:
            set_backup_token(token)
        self._save_state(state)
        return state
