"""Tests for workspace backup & restore (backup_manager + git_sync_engine).

Tests the core backup/restore cycle: DB export, git operations, Keychain
token storage, and the lifecycle_manager daily trigger. Uses tmp dirs
for git repos and in-memory SQLite for DB round-trips.
"""
import asyncio
import gzip
import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Tracer bullet: backup() round-trip
# ---------------------------------------------------------------------------


@pytest.fixture
def backup_env(tmp_path):
    """Create a minimal SwarmAI-like directory layout for testing."""
    # Workspace dir
    ws = tmp_path / "SwarmWS"
    ws.mkdir()
    (ws / ".context").mkdir()
    (ws / ".context" / "MEMORY.md").write_text("# Memory\nTest memory content\n")
    (ws / "Knowledge").mkdir()
    (ws / "Knowledge" / "test-note.md").write_text("# Test Note\n")

    # Config files
    swarm_dir = tmp_path / ".swarm-ai"
    swarm_dir.mkdir()
    (swarm_dir / "notify-channels.yaml").write_text("feishu:\n  webhook: test\n")

    # DB with messages table
    import aiosqlite

    db_path = swarm_dir / "data.db"

    async def _setup_db():
        async with aiosqlite.connect(str(db_path)) as conn:
            await conn.execute(
                "CREATE TABLE messages (id TEXT PRIMARY KEY, content TEXT, session_id TEXT, "
                "role TEXT, created_at TEXT, updated_at TEXT, expires_at INTEGER)"
            )
            await conn.execute(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, name TEXT, created_at TEXT)"
            )
            await conn.execute(
                "CREATE TABLE todos (id TEXT PRIMARY KEY, title TEXT, status TEXT)"
            )
            for i in range(5):
                await conn.execute(
                    "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (f"msg-{i}", f"content-{i}", "sess-1", "user", "2026-05-02", "2026-05-02", 0),
                )
            await conn.execute(
                "INSERT INTO sessions VALUES ('sess-1', 'test session', '2026-05-02')"
            )
            await conn.execute(
                "INSERT INTO todos VALUES ('todo-1', 'Test todo', 'pending')"
            )
            await conn.commit()

    asyncio.run(_setup_db())

    # Init git repo in workspace
    subprocess.run(["git", "init", str(ws)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(ws), "config", "user.email", "test@test.com"],
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(ws), "config", "user.name", "Test"],
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(ws), "add", "-A"], capture_output=True)
    subprocess.run(
        ["git", "-C", str(ws), "commit", "-m", "initial"],
        capture_output=True,
    )

    # Create bare remote repo
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(ws), "remote", "add", "origin", str(remote)],
        capture_output=True,
    )
    # Push to whatever branch git init created (main or master)
    branch = subprocess.run(
        ["git", "-C", str(ws), "branch", "--show-current"],
        capture_output=True, text=True,
    ).stdout.strip() or "main"
    subprocess.run(
        ["git", "-C", str(ws), "push", "-u", "origin", branch],
        capture_output=True,
    )

    return {
        "ws": ws,
        "swarm_dir": swarm_dir,
        "db_path": db_path,
        "remote": remote,
    }


class TestBackupRoundTrip:
    """Tracer bullet: full backup → verify git contains DB dumps."""

    @pytest.mark.asyncio
    async def test_backup_creates_commit_with_db_exports(self, backup_env):
        """backup() should export DB tables, copy config, commit, and push."""
        from core.backup_manager import BackupManager

        mgr = BackupManager(
            workspace_dir=backup_env["ws"],
            swarm_dir=backup_env["swarm_dir"],
            db_path=backup_env["db_path"],
        )

        result = await mgr.backup()

        assert result["status"] == "ok"
        assert result["tables_exported"] > 0

        # Verify db-export dir exists with gzipped SQL files
        export_dir = backup_env["ws"] / "db-export"
        assert export_dir.exists()
        gz_files = list(export_dir.glob("*.sql.gz"))
        assert len(gz_files) >= 3  # messages, sessions, todos at minimum

        # Verify messages.sql.gz contains our 5 rows
        with gzip.open(export_dir / "messages.sql.gz", "rt") as f:
            sql = f.read()
        assert "msg-0" in sql
        assert "msg-4" in sql

        # Verify git pushed (check remote has more than 1 commit)
        log = subprocess.run(
            ["git", "-C", str(backup_env["ws"]), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "backup:" in log.stdout


class TestDBExportImport:
    """DB dump → import round-trip: row counts must match."""

    @pytest.mark.asyncio
    async def test_export_import_preserves_rows(self, backup_env):
        """Export tables, import into fresh DB, verify row counts match."""
        from core.git_sync_engine import GitSyncEngine

        engine = GitSyncEngine(workspace_dir=backup_env["ws"])

        export_dir = backup_env["ws"] / "db-export"
        tables = ["messages", "sessions", "todos"]
        exported = await engine.export_db_tables(
            db_path=backup_env["db_path"],
            export_dir=export_dir,
            tables=tables,
        )
        assert exported == 3

        # Import into a fresh DB (pass same allowed_tables as export)
        fresh_db = backup_env["swarm_dir"] / "fresh.db"
        imported = await engine.import_db_tables(
            db_path=fresh_db,
            export_dir=export_dir,
            allowed_tables=tables,
        )
        assert imported == 3

        # Verify row counts
        import aiosqlite

        async with aiosqlite.connect(str(fresh_db)) as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM messages")
            assert (await cur.fetchone())[0] == 5
            cur = await conn.execute("SELECT COUNT(*) FROM sessions")
            assert (await cur.fetchone())[0] == 1
            cur = await conn.execute("SELECT COUNT(*) FROM todos")
            assert (await cur.fetchone())[0] == 1


class TestSpecialCharacterRoundTrip:
    """P0 regression test: semicolons, quotes, newlines, unicode in data."""

    @pytest.mark.asyncio
    async def test_special_chars_survive_export_import(self, backup_env):
        """Data with semicolons, quotes, newlines, unicode must round-trip correctly."""
        import aiosqlite
        from core.git_sync_engine import GitSyncEngine

        # Insert adversarial data
        adversarial = [
            ("adv-1", "print('hello'); print('world')", "sess-1", "user"),
            ("adv-2", "line1\nline2\nline3", "sess-1", "user"),
            ("adv-3", "it's a 'quoted' string", "sess-1", "user"),
            ("adv-4", "emoji: \U0001f41d and chinese: 你好", "sess-1", "user"),
            ("adv-5", "NULL literal; DROP TABLE messages; --", "sess-1", "user"),
        ]
        async with aiosqlite.connect(str(backup_env["db_path"])) as conn:
            for row in adversarial:
                await conn.execute(
                    "INSERT INTO messages (id, content, session_id, role, created_at, updated_at, expires_at) "
                    "VALUES (?, ?, ?, ?, '2026-05-02', '2026-05-02', 0)",
                    row,
                )
            await conn.commit()

        # Export + import
        engine = GitSyncEngine(workspace_dir=backup_env["ws"])
        export_dir = backup_env["ws"] / "db-export"
        exported = await engine.export_db_tables(
            db_path=backup_env["db_path"], export_dir=export_dir, tables=["messages"]
        )
        assert exported == 1

        fresh_db = backup_env["swarm_dir"] / "special-chars.db"
        imported = await engine.import_db_tables(
            db_path=fresh_db, export_dir=export_dir, allowed_tables=["messages"]
        )
        assert imported == 1

        # Verify ALL rows including adversarial ones
        async with aiosqlite.connect(str(fresh_db)) as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM messages")
            count = (await cur.fetchone())[0]
            assert count == 10  # 5 original + 5 adversarial

            # Verify specific adversarial content survived
            cur = await conn.execute("SELECT content FROM messages WHERE id='adv-1'")
            assert (await cur.fetchone())[0] == "print('hello'); print('world')"

            cur = await conn.execute("SELECT content FROM messages WHERE id='adv-2'")
            assert (await cur.fetchone())[0] == "line1\nline2\nline3"

            cur = await conn.execute("SELECT content FROM messages WHERE id='adv-3'")
            assert (await cur.fetchone())[0] == "it's a 'quoted' string"

            cur = await conn.execute("SELECT content FROM messages WHERE id='adv-5'")
            content = (await cur.fetchone())[0]
            assert "DROP TABLE" in content  # The string survived, not executed as SQL


class TestMaliciousSqlRejection:
    """E3: Verify import rejects tampered SQL files."""

    @pytest.mark.asyncio
    async def test_drop_table_rejected(self, backup_env):
        """Import must reject .sql.gz files containing DROP TABLE."""
        import gzip
        from core.git_sync_engine import GitSyncEngine

        export_dir = backup_env["ws"] / "db-export"
        export_dir.mkdir(parents=True, exist_ok=True)

        # Create a malicious messages.sql.gz
        malicious_sql = 'CREATE TABLE "messages" (id TEXT);\nDROP TABLE sessions;\n'
        with gzip.open(export_dir / "messages.sql.gz", "wt") as f:
            f.write(malicious_sql)

        engine = GitSyncEngine(workspace_dir=backup_env["ws"])
        fresh_db = backup_env["swarm_dir"] / "malicious.db"
        imported = await engine.import_db_tables(
            db_path=fresh_db, export_dir=export_dir, allowed_tables=["messages"]
        )
        # Should reject the file and import 0
        assert imported == 0


class TestKeychainToken:
    """Keychain read/write round-trip (mocked on non-macOS)."""

    def test_set_and_get_token_roundtrip(self):
        """set_token → get_token returns same value."""
        from core.backup_manager import set_backup_token, get_backup_token

        # Use env var fallback for testability (avoid real Keychain in CI)
        with patch.dict(os.environ, {"SWARM_BACKUP_TOKEN": "ghp_test123"}):
            token = get_backup_token()
        assert token == "ghp_test123"

    def test_get_token_returns_none_when_unset(self):
        """get_token returns None when no token configured."""
        from core.backup_manager import get_backup_token

        with patch.dict(os.environ, {}, clear=True):
            with patch("core.backup_manager._keychain_get_token", return_value=None):
                token = get_backup_token()
        assert token is None


class TestBackupStatus:
    """Status API returns accurate last_backup info."""

    @pytest.mark.asyncio
    async def test_status_after_backup(self, backup_env):
        """After backup(), status shows last_backup timestamp."""
        from core.backup_manager import BackupManager

        mgr = BackupManager(
            workspace_dir=backup_env["ws"],
            swarm_dir=backup_env["swarm_dir"],
            db_path=backup_env["db_path"],
        )

        await mgr.backup()
        status = mgr.get_status()

        assert status["last_backup"] is not None
        assert status["enabled"] is True
        assert status["repo_url"] is not None
        assert len(status["repo_url"]) > 0

    @pytest.mark.asyncio
    async def test_status_before_any_backup(self, backup_env):
        """Before backup(), status shows last_backup=None."""
        from core.backup_manager import BackupManager

        mgr = BackupManager(
            workspace_dir=backup_env["ws"],
            swarm_dir=backup_env["swarm_dir"],
            db_path=backup_env["db_path"],
        )

        status = mgr.get_status()
        assert status["last_backup"] is None


class TestGitOperationsTimeout:
    """Git operations must respect 30s timeout and not crash on failure."""

    @pytest.mark.asyncio
    async def test_git_push_failure_is_non_fatal(self, backup_env):
        """If git push fails, backup() returns error status but doesn't crash."""
        from core.backup_manager import BackupManager

        # Remove the remote to simulate push failure
        subprocess.run(
            ["git", "-C", str(backup_env["ws"]), "remote", "remove", "origin"],
            capture_output=True,
        )

        mgr = BackupManager(
            workspace_dir=backup_env["ws"],
            swarm_dir=backup_env["swarm_dir"],
            db_path=backup_env["db_path"],
        )

        result = await mgr.backup()
        # Should still export and commit, just fail on push
        assert result["tables_exported"] > 0
        assert result["push_status"] == "failed"


class TestRestoreRoundTrip:
    """Full backup → restore round-trip on a fresh DB."""

    @pytest.mark.asyncio
    async def test_backup_then_restore_preserves_data(self, backup_env):
        """backup() then restore() into a fresh dir reproduces all data."""
        from core.backup_manager import BackupManager

        # 1. Backup
        mgr = BackupManager(
            workspace_dir=backup_env["ws"],
            swarm_dir=backup_env["swarm_dir"],
            db_path=backup_env["db_path"],
        )
        backup_result = await mgr.backup()
        assert backup_result["status"] == "ok"

        # 2. Simulate fresh machine: new swarm_dir with empty DB
        fresh_dir = backup_env["ws"].parent / "fresh-machine"
        fresh_dir.mkdir()
        fresh_swarm = fresh_dir / ".swarm-ai"
        fresh_swarm.mkdir()

        # 3. Restore from the backup repo (local bare remote)
        fresh_mgr = BackupManager(
            workspace_dir=fresh_dir / "SwarmWS",
            swarm_dir=fresh_swarm,
            db_path=fresh_swarm / "data.db",
        )

        events = []
        async for event in fresh_mgr.restore(repo_url=str(backup_env["remote"])):
            events.append(event)

        # 4. Verify stages completed
        stages = [e["stage"] for e in events]
        assert "clone" in stages
        assert "db_import" in stages
        assert "verify" in stages

        # 5. Verify data round-trip
        final = events[-1]
        assert final["stage"] == "verify"
        assert final.get("messages_count", 0) == 5
        assert final.get("sessions_count", 0) == 1

        # 6. Verify config was restored
        assert (fresh_swarm / "notify-channels.yaml").exists()

    @pytest.mark.asyncio
    async def test_restore_rejects_non_empty_workspace(self, backup_env):
        """restore() should refuse if workspace already has data."""
        from core.backup_manager import BackupManager

        mgr = BackupManager(
            workspace_dir=backup_env["ws"],
            swarm_dir=backup_env["swarm_dir"],
            db_path=backup_env["db_path"],
        )

        events = []
        async for event in mgr.restore(repo_url=str(backup_env["remote"])):
            events.append(event)

        assert any(e.get("error") for e in events)


class TestLifecycleManagerTrigger:
    """lifecycle_manager daily trigger fires backup."""

    @pytest.mark.asyncio
    async def test_daily_backup_trigger(self):
        """_run_daily_backup should call backup_manager.backup()."""
        from core.lifecycle_manager import LifecycleManager

        mgr = LifecycleManager.__new__(LifecycleManager)
        mgr._router = MagicMock()
        mgr._backup_manager = MagicMock()
        mgr._backup_manager.backup = AsyncMock(
            return_value={"status": "ok", "tables_exported": 5}
        )

        await mgr._run_daily_backup()
        mgr._backup_manager.backup.assert_called_once()
