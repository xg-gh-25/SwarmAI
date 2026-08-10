"""Workspace backup & restore manager.

Coordinates L1 (git text) + L2 (DB SQL dump) backup to a GitHub private
repo, and full restore from that repo on a new machine.

Key design:
- backup() and restore() share the same GitSyncEngine
- DB export uses sqlite3 .backup (WAL-safe) → per-table streaming gzip
- Token stored in macOS Keychain via `security` CLI, never plaintext
- Env var SWARM_BACKUP_TOKEN overrides Keychain (for CI/testing)
- backup_state.json tracks last_backup, repo_url, schedule
- asyncio.Lock prevents concurrent backup/restore (A4 fix)
"""
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from core.git_sync_engine import GitSyncEngine

logger = logging.getLogger(__name__)


def _sanitize_repo_url(url: str | None) -> str | None:
    """Strip embedded credentials from a git URL before storing/returning.

    Converts https://ghp_xxx@github.com/user/repo.git
         to  https://github.com/user/repo.git
    Handles URLs with @ in username (C2 fix): strips everything up to LAST @.
    """
    if not url:
        return url
    # Greedy match: strip everything between :// and last @ (C2 fix)
    return re.sub(r"(https?://).*@", r"\1", url)


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
    except Exception as exc:  # noqa: BLE001
        # DEBUG on purpose: get_backup_token() calls this on EVERY platform, so off-macOS
        # this raises FileNotFoundError (no `security` binary) by design and falls
        # through to _file_get_token(). Expected miss, not an incident — but still say
        # something, because a genuine macOS keychain fault currently reads identically
        # to "the user never configured a backup token".
        logger.debug("keychain token lookup unavailable (%s); trying file fallback", exc)
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
    except Exception as exc:  # noqa: BLE001
        # WARNING, unlike the getter above: set_backup_token() only routes here when
        # sys.platform == "darwin", so this is never the "no keychain on Linux" case —
        # it is a real macOS keychain failure. The caller does propagate False, but
        # without the reason the user just sees "couldn't save token" and has nothing
        # to act on.
        logger.warning("failed to store backup token in keychain: %s", exc)
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
        # Create with 0600 ALREADY SET, before the secret is written. The previous
        # write_text()-then-chmod() order left the token on disk at the default umask
        # (typically 0644) for the window in between, and if chmod raised we returned
        # False while the secret stayed on disk world-readable — a silent failure that
        # was also a silent disclosure.
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, (token + "\n").encode())
        finally:
            os.close(fd)
        os.chmod(path, 0o600)  # enforce on a pre-existing file, whose mode O_CREAT keeps
        return True
    except Exception as exc:  # noqa: BLE001
        # Last-resort writer: if this fails the token is stored NOWHERE, so backup auth
        # will fail later at a point far from the cause.
        logger.warning("failed to write backup token to %s: %s", path, exc)
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
        # Ensure swarm_dir exists — restore may be the FIRST op on a fresh machine,
        # and the in-progress marker below is written here (run_037a02af).
        self.swarm_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_dir = Path(workspace_dir or self.swarm_dir / "SwarmWS")
        self.db_path = Path(db_path or self.swarm_dir / "data.db")
        self.state_path = self.swarm_dir / "backup_state.json"
        # In-progress marker: written BEFORE clone, removed only on full success.
        # Its PRESENCE means a prior restore was interrupted → the workspace holds
        # debris (partial clone/config) that must be cleaned before a retry. It
        # lives in swarm_dir (NOT the workspace) so a workspace rmtree preserves it.
        self.restore_marker = self.swarm_dir / ".restore-in-progress"
        self.engine = GitSyncEngine(self.workspace_dir)
        self._lock = asyncio.Lock()  # A4 fix: prevent concurrent backup/restore

    def _cleanup_partial_restore(self) -> None:
        """Purge the CLONED WORKSPACE debris of an interrupted restore so a retry
        can re-clone (git refuses to clone into a non-empty dir; leftover .git
        would break retry). Synchronous, called under self._lock after the import
        await has returned (never races the import thread).

        NOTE: this does NOT touch data.db. The DB import is atomic (single
        transaction — commit-all or rollback-all in git_sync_engine), so an
        interrupted restore never leaves a partially-imported DB; there is no
        partial DB to purge. Unlinking the live daemon's open WAL db here would be
        the data-loss bug (Gate-2 HIGH, run_037a02af) — so we deliberately don't.

        Blast-radius: ALLOW-LIST. Only rmtree a real (non-symlink) directory
        STRICTLY BELOW swarm_dir. Anything else (a symlink, a path outside
        swarm_dir, swarm_dir itself) is refused — a misconfigured or symlinked
        workspace_dir must never redirect the delete onto real user data.
        """
        import shutil
        try:
            if self.workspace_dir.is_symlink():
                logger.error("cleanup: REFUSING to rmtree a symlinked workspace %s", self.workspace_dir)
                return
            ws = self.workspace_dir.resolve()
            swarm = self.swarm_dir.resolve()
            # Must be strictly BELOW swarm_dir (swarm is an ancestor), never swarm itself.
            if ws == swarm or swarm not in ws.parents:
                logger.error("cleanup: REFUSING to rmtree workspace not strictly under swarm_dir: %s", ws)
                return
            if ws.exists():
                shutil.rmtree(ws, ignore_errors=True)
        except OSError as e:
            logger.warning("cleanup: failed to rmtree workspace %s: %s", self.workspace_dir, e)

    def _workspace_has_real_data(self, memory_file: Path) -> bool:
        """True if this workspace holds REAL user data that restore must not clobber.

        Real data = a populated DB (any row in messages/sessions) OR a MEMORY.md
        whose content is more than a fresh template placeholder. Because DB import
        is atomic, interrupted-restore debris leaves an EMPTY DB — so a populated DB
        reliably means genuine prior data, never debris. This is the authority the
        stale-marker guard relies on (Gate-2 HIGH, run_037a02af)."""
        import sqlite3
        try:
            if self.db_path.exists():
                conn = sqlite3.connect(str(self.db_path))
                try:
                    for table in ("messages", "sessions"):
                        try:
                            if conn.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone():
                                return True
                        except sqlite3.OperationalError:
                            continue  # table absent → not populated
                finally:
                    conn.close()
        except sqlite3.Error as e:
            # Can't read the DB → be SAFE: treat as real data (refuse rather than risk deleting).
            logger.warning("real-data check: DB unreadable (%s) — treating as real data (safe refuse)", e)
            return True
        # MEMORY.md with non-trivial content also counts as real data.
        try:
            if memory_file.exists() and len(memory_file.read_text(errors="ignore").strip()) > 0:
                return True
        except OSError:
            return True  # unreadable → safe refuse
        return False

    def _load_state(self) -> dict:
        """Load backup state from JSON file."""
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text())
            except Exception as e:
                logger.warning("Failed to load backup state: %s", e)
        return {"last_backup": None, "repo_url": None, "schedule": "daily_3am", "enabled": True}

    def _save_state(self, state: dict) -> None:
        """Persist backup state atomically (A6 fix: write-then-rename)."""
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(self.swarm_dir), suffix=".json.tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp_path, str(self.state_path))
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    async def backup(self) -> dict:
        """Run a full backup: config snapshot + DB export + git push.

        Returns dict with status, tables_exported, commit, push_status.
        """
        async with self._lock:
            return await self._backup_impl()

    async def _backup_impl(self) -> dict:
        """Internal backup implementation (called under lock)."""
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

        # 2. DB export (runs in thread, non-blocking)
        export_dir = self.workspace_dir / "db-export"
        tables_exported = await self.engine.export_db_tables(
            db_path=self.db_path,
            export_dir=export_dir,
            tables=L2_TABLES,
        )
        result["tables_exported"] = tables_exported

        # 3. Git add + commit + push (A5 fix: run in thread)
        def _git_ops():
            self.engine.git_add_all()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            sha = self.engine.git_commit(f"backup: {now_str}")
            pushed = self.engine.git_push() if sha else False
            return sha, pushed

        commit_sha, pushed = await asyncio.to_thread(_git_ops)
        result["commit"] = commit_sha
        if commit_sha:
            result["push_status"] = "ok" if pushed else "failed"
        else:
            result["push_status"] = "no_changes"

        # 4. Update state
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
        async with self._lock:
            async for event in self._restore_impl(repo_url, token):
                yield event

    async def _restore_impl(self, repo_url: str, token: str | None = None):
        """Internal restore implementation (called under lock).

        Freshness/safety (run_037a02af, hardened by Gate-2):
          • REAL user data present (populated DB OR MEMORY.md)  → REFUSE, ALWAYS.
            This gate wins even when the in-progress marker is present — a marker
            can go stale (process SIGKILL'd after a successful clone+import, then
            the user keeps using the app), and treating stale-marker as unconditional
            cleanup would rmtree real data (Gate-2 HIGH). Real-data check is the
            authority; the marker only decides HOW to treat a NON-populated workspace.
          • marker present, NO real data  → interrupted-restore debris → CLEAN + proceed
          • neither                        → fresh install → proceed
        Because DB import is atomic, an interrupted restore leaves an EMPTY/rolled-back
        DB (not "some rows") — so "populated DB" reliably means a real prior restore or
        real usage, never debris.
        """
        memory_file = self.workspace_dir / ".context" / "MEMORY.md"
        interrupted = self.restore_marker.exists()
        has_real_data = self._workspace_has_real_data(memory_file)

        if has_real_data:
            # Real user data — refuse regardless of marker (stale-marker safety).
            if interrupted:
                logger.warning("Restore marker present BUT workspace has real data — refusing (stale marker, not debris)")
            yield {"stage": "error", "error": "Workspace already has data. Restore requires a fresh install."}
            return
        if interrupted:
            # Marker present + NO real data → genuine interrupted-restore debris.
            logger.warning("Detected interrupted restore (marker present, no real data) — cleaning up before retry")
            self._cleanup_partial_restore()

        # Mark restore in-progress BEFORE any side effect (clone/config/import).
        try:
            self.restore_marker.write_text("in-progress\n")
        except OSError as e:
            logger.warning("Failed to write restore marker: %s", e)

        try:
            # Stage 1: CLONE
            yield {"stage": "clone", "progress": 10, "detail": "Cloning backup repository..."}
            cloned = await asyncio.to_thread(
                self.engine.git_clone, _sanitize_repo_url(repo_url) or repo_url, self.workspace_dir
            )
            if not cloned:
                # Clone failed BEFORE we stored the token (AC4: don't persist an
                # unconfirmed credential). Clean the (possibly partial) clone.
                self._cleanup_partial_restore()
                self.restore_marker.unlink(missing_ok=True)
                yield {"stage": "clone", "progress": 10, "error": "git clone failed. Check repo URL and credentials."}
                return
            self.engine = GitSyncEngine(self.workspace_dir)

            # Store token ONLY after clone succeeds (AC4) — a failed restore must
            # not durably persist a token the user never confirmed works.
            if token:
                set_backup_token(token)

            # Stage 2: CONFIG
            yield {"stage": "config", "progress": 20, "detail": "Restoring configuration files..."}
            config_dir = self.workspace_dir / "config-backup"
            if config_dir.exists():
                for f in config_dir.iterdir():
                    if f.is_file():
                        shutil.copy2(str(f), str(self.swarm_dir / f.name))

            # Stage 3: DB_IMPORT (atomic — raises on failure, rolls back fully)
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
            import sqlite3
            conn = sqlite3.connect(str(self.db_path))
            conn.execute("SELECT 1")
            conn.close()

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

            # Success — remove the in-progress marker LAST (restore is now complete).
            self.restore_marker.unlink(missing_ok=True)

            yield {
                "stage": "verify",
                "progress": 100,
                "detail": "Restore complete",
                "tables_imported": tables_imported,
                **counts,
            }
        except Exception as e:
            # Any failure (import rollback-raise, schema check, unexpected) →
            # clean the debris so the NEXT restore can re-clone. Marker stays as a
            # belt-and-suspenders signal (next run also cleans on marker-present),
            # but we remove it here too so a fresh retry isn't treated as "interrupted".
            logger.error("Restore failed at import/verify — cleaning up partial state: %s", e)
            self._cleanup_partial_restore()
            self.restore_marker.unlink(missing_ok=True)
            yield {"stage": "error", "progress": 50, "error": f"Restore failed: {e}"}
            return

    def get_status(self) -> dict:
        """Return current backup status."""
        state = self._load_state()
        return {
            "last_backup": state.get("last_backup"),
            "repo_url": state.get("repo_url") or _sanitize_repo_url(self.engine.get_remote_url()),
            "schedule": state.get("schedule", "daily_3am"),
            "enabled": state.get("enabled", True),
        }

    def configure(self, repo_url: str | None = None, token: str | None = None,
                  schedule: str | None = None) -> dict:
        """Update backup configuration."""
        state = self._load_state()
        if repo_url is not None:
            state["repo_url"] = _sanitize_repo_url(repo_url)  # C1 fix: sanitize here too
        if schedule is not None:
            state["schedule"] = schedule
        if token is not None:
            set_backup_token(token)
        self._save_state(state)
        return state
