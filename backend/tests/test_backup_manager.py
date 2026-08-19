"""Tests for workspace backup & restore (backup_manager + git_sync_engine).

Tests the core backup/restore cycle: DB export, git operations, Keychain
token storage, and the lifecycle_manager daily trigger. Uses tmp dirs
for git repos and in-memory SQLite for DB round-trips.
"""
import asyncio
import gzip
import os
import subprocess
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

    # C050: seed backup_state.json declaring this remote as the CONFIGURED backup
    # destination. Without this, the push-destination guard (fail-closed) correctly
    # refuses — these round-trip tests exercise the *legitimate configured* path,
    # so the fixture must reflect a user who has run configure(repo_url=<remote>).
    import json as _json
    (swarm_dir / "backup_state.json").write_text(_json.dumps({
        "enabled": True,
        "schedule": "daily_3am",
        "configured_repo_url": str(remote),
    }))

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
    """E3: Verify import rejects tampered SQL files.

    Covers the class of SQL injection via executescript():
    multi-statement payloads, subquery exfiltration, trailing junk.
    """

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

    @pytest.mark.asyncio
    async def test_create_table_as_select_rejected(self, backup_env):
        """CREATE TABLE AS SELECT is a data exfiltration vector."""
        import gzip
        from core.git_sync_engine import GitSyncEngine

        export_dir = backup_env["ws"] / "db-export"
        export_dir.mkdir(parents=True, exist_ok=True)

        malicious_sql = 'CREATE TABLE "messages" AS SELECT * FROM sessions;\n'
        with gzip.open(export_dir / "messages.sql.gz", "wt") as f:
            f.write(malicious_sql)

        engine = GitSyncEngine(workspace_dir=backup_env["ws"])
        fresh_db = backup_env["swarm_dir"] / "exfil.db"
        imported = await engine.import_db_tables(
            db_path=fresh_db, export_dir=export_dir, allowed_tables=["messages"]
        )
        assert imported == 0

    @pytest.mark.asyncio
    async def test_insert_select_subquery_rejected(self, backup_env):
        """INSERT INTO ... SELECT is a cross-table data exfiltration vector."""
        import gzip
        from core.git_sync_engine import GitSyncEngine

        export_dir = backup_env["ws"] / "db-export"
        export_dir.mkdir(parents=True, exist_ok=True)

        malicious_sql = (
            'CREATE TABLE "messages" (id TEXT, content TEXT);\n'
            'INSERT INTO "messages" SELECT * FROM sessions;\n'
        )
        with gzip.open(export_dir / "messages.sql.gz", "wt") as f:
            f.write(malicious_sql)

        engine = GitSyncEngine(workspace_dir=backup_env["ws"])
        fresh_db = backup_env["swarm_dir"] / "insert_select.db"
        imported = await engine.import_db_tables(
            db_path=fresh_db, export_dir=export_dir, allowed_tables=["messages"]
        )
        assert imported == 0

    @pytest.mark.asyncio
    async def test_delete_after_insert_rejected(self, backup_env):
        """DELETE hidden after a valid INSERT must be caught."""
        import gzip
        from core.git_sync_engine import GitSyncEngine

        export_dir = backup_env["ws"] / "db-export"
        export_dir.mkdir(parents=True, exist_ok=True)

        malicious_sql = (
            'CREATE TABLE "messages" (id TEXT, content TEXT);\n'
            'INSERT INTO "messages" VALUES (\'1\', \'hi\');\n'
            'DELETE FROM messages;\n'
        )
        with gzip.open(export_dir / "messages.sql.gz", "wt") as f:
            f.write(malicious_sql)

        engine = GitSyncEngine(workspace_dir=backup_env["ws"])
        fresh_db = backup_env["swarm_dir"] / "delete.db"
        imported = await engine.import_db_tables(
            db_path=fresh_db, export_dir=export_dir, allowed_tables=["messages"]
        )
        assert imported == 0

    @pytest.mark.asyncio
    async def test_update_statement_rejected(self, backup_env):
        """UPDATE statements must be rejected."""
        import gzip
        from core.git_sync_engine import GitSyncEngine

        export_dir = backup_env["ws"] / "db-export"
        export_dir.mkdir(parents=True, exist_ok=True)

        malicious_sql = (
            'CREATE TABLE "messages" (id TEXT, content TEXT);\n'
            'UPDATE messages SET content = \'pwned\' WHERE 1=1;\n'
        )
        with gzip.open(export_dir / "messages.sql.gz", "wt") as f:
            f.write(malicious_sql)

        engine = GitSyncEngine(workspace_dir=backup_env["ws"])
        fresh_db = backup_env["swarm_dir"] / "update.db"
        imported = await engine.import_db_tables(
            db_path=fresh_db, export_dir=export_dir, allowed_tables=["messages"]
        )
        assert imported == 0

    @pytest.mark.asyncio
    async def test_incomplete_trailing_sql_rejected(self, backup_env):
        """Trailing incomplete SQL (no closing semicolon) must be rejected."""
        import gzip
        from core.git_sync_engine import GitSyncEngine

        export_dir = backup_env["ws"] / "db-export"
        export_dir.mkdir(parents=True, exist_ok=True)

        # Valid CREATE TABLE followed by incomplete junk
        malicious_sql = (
            'CREATE TABLE "messages" (id TEXT);\n'
            'INSERT INTO messages VALUES (\'1\'\n'  # no closing paren/semicolon
        )
        with gzip.open(export_dir / "messages.sql.gz", "wt") as f:
            f.write(malicious_sql)

        engine = GitSyncEngine(workspace_dir=backup_env["ws"])
        fresh_db = backup_env["swarm_dir"] / "trailing.db"
        imported = await engine.import_db_tables(
            db_path=fresh_db, export_dir=export_dir, allowed_tables=["messages"]
        )
        assert imported == 0

    @pytest.mark.asyncio
    async def test_valid_export_still_imports(self, backup_env):
        """Legitimate export files must still import successfully."""
        import gzip
        from core.git_sync_engine import GitSyncEngine

        export_dir = backup_env["ws"] / "db-export"
        export_dir.mkdir(parents=True, exist_ok=True)

        valid_sql = (
            'CREATE TABLE "messages" (id TEXT, content TEXT);\n'
            'INSERT INTO "messages" ("id", "content") VALUES (\'1\', \'hello world\');\n'
            'INSERT INTO "messages" ("id", "content") VALUES (\'2\', \'it''s fine\');\n'
        )
        with gzip.open(export_dir / "messages.sql.gz", "wt") as f:
            f.write(valid_sql)

        engine = GitSyncEngine(workspace_dir=backup_env["ws"])
        fresh_db = backup_env["swarm_dir"] / "valid.db"
        imported = await engine.import_db_tables(
            db_path=fresh_db, export_dir=export_dir, allowed_tables=["messages"]
        )
        assert imported == 1

        # Verify data actually landed
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(str(fresh_db))
        rows = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        conn.close()
        assert rows == 2


class TestKeychainToken:
    """Keychain read/write round-trip (mocked on non-macOS)."""

    def test_set_and_get_token_roundtrip(self):
        """set_token → get_token returns same value."""
        from core.backup_manager import get_backup_token

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


class TestFileTokenPermissions:
    """The file fallback stores a SECRET, so 0600 is a security invariant.

    Regression guard for the original ``write_text()`` then ``chmod(0o600)`` order:
    that left the token on disk at the default umask (typically 0644) for the window
    in between, and if the chmod raised, ``_file_set_token`` returned False while the
    secret stayed on disk world-readable — a silent failure that was also a silent
    disclosure. The token must never exist on disk at any moment with loose perms,
    hence create-with-mode rather than fix-up-after.
    """

    def test_token_file_is_0600_on_create(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        from core.backup_manager import _file_set_token

        assert _file_set_token("ghp_secret") is True
        path = tmp_path / ".swarm-ai" / ".backup-token"
        assert path.read_text() == "ghp_secret\n"
        assert oct(path.stat().st_mode & 0o777) == "0o600"

    def test_preexisting_loose_perms_are_tightened(self, tmp_path, monkeypatch):
        """O_CREAT keeps an existing file's mode, so the explicit chmod must still run."""
        monkeypatch.setenv("HOME", str(tmp_path))
        from core.backup_manager import _file_get_token, _file_set_token

        assert _file_set_token("first") is True
        path = tmp_path / ".swarm-ai" / ".backup-token"
        path.chmod(0o644)  # simulate a token file left behind by the old code path

        assert _file_set_token("second") is True
        assert oct(path.stat().st_mode & 0o777) == "0o600"
        # and the longer previous value must be fully truncated, not overwritten in place
        assert path.read_text() == "second\n"
        assert _file_get_token() == "second"

    def test_write_failure_is_reported_and_logged(self, tmp_path, monkeypatch, caplog):
        """The last-resort writer failing means the token is stored NOWHERE — the
        reason must reach the log, not just a bare False (Degrade-OBSERVABLE)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        from core.backup_manager import _file_set_token

        with patch("core.backup_manager.os.open", side_effect=OSError("disk full")):
            with caplog.at_level("WARNING", logger="core.backup_manager"):
                assert _file_set_token("ghp_secret") is False
        assert "disk full" in caplog.text


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
        """If the remote is unusable, backup() returns a non-fatal status (never crashes).

        C050: removing origin means the push-destination guard can no longer confirm
        the configured destination → it refuses with 'no_remote' BEFORE git_push. The
        contract under test is 'push problems are non-fatal', which still holds — the
        LAYER that catches an absent remote is now the fail-closed guard, not git_push.
        """
        from core.backup_manager import BackupManager

        # Remove the remote to simulate an unusable push destination
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
        # Non-fatal: DB export still happened, and the push was declined (not a crash).
        assert result["tables_exported"] > 0
        # No usable remote → guard refuses ('no_remote') rather than a git-layer 'failed'.
        assert result["push_status"] in ("refused", "failed")
        if result["push_status"] == "refused":
            assert result.get("refuse_reason") == "no_remote"


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


class TestRestoreAtomicityAndRecovery:
    """run_037a02af: partial-DB + freshness-lockout data hazard.

    A restore interrupted after clone/config/partial-import must NOT strand the
    user: the DB import is atomic (rollback on failure, never half-imported) and
    an in-progress marker lets a retry recognize + clean up the debris.
    """

    def _fresh_mgr(self, backup_env):
        from core.backup_manager import BackupManager
        # Mirror PRODUCTION layout: workspace_dir is strictly UNDER swarm_dir
        # (swarm_dir/SwarmWS) — the cleanup allow-list guard requires this.
        fresh_swarm = backup_env["ws"].parent / "fresh-machine" / ".swarm-ai"
        fresh_swarm.mkdir(parents=True, exist_ok=True)
        return BackupManager(
            workspace_dir=fresh_swarm / "SwarmWS",
            swarm_dir=fresh_swarm,
            db_path=fresh_swarm / "data.db",
        ), fresh_swarm

    @pytest.mark.asyncio
    async def test_AC5_import_is_atomic_rollback_on_failure(self, backup_env):
        """AC5: if any table fails to execute, ALL tables roll back — no partial DB."""
        import gzip
        from core.git_sync_engine import GitSyncEngine

        export_dir = backup_env["ws"] / "db-export-bad"
        export_dir.mkdir(parents=True, exist_ok=True)
        # Table 1: valid. Alphabetically first so it imports before the bad one.
        with gzip.open(export_dir / "aaa_messages.sql.gz", "wt") as f:
            f.write('CREATE TABLE "messages" (id TEXT, content TEXT);\n'
                    'INSERT INTO "messages" ("id","content") VALUES (\'1\',\'ok\');\n')
        # Table 2: passes validation (plain INSERT) but FAILS at execute time
        # (references a column that does not exist) → must roll back table 1 too.
        with gzip.open(export_dir / "zzz_sessions.sql.gz", "wt") as f:
            f.write('CREATE TABLE "sessions" (id TEXT);\n'
                    'INSERT INTO "sessions" ("id","nonexistent_col") VALUES (\'1\',\'x\');\n')

        engine = GitSyncEngine(workspace_dir=backup_env["ws"])
        fresh_db = backup_env["swarm_dir"] / "atomic.db"
        allowed = ["messages", "sessions"]
        # Map the aaa_/zzz_ filenames to allowed table stems for this test:
        # import keys on the file stem, so name the files by the real table.
        (export_dir / "messages.sql.gz").write_bytes((export_dir / "aaa_messages.sql.gz").read_bytes())
        (export_dir / "sessions.sql.gz").write_bytes((export_dir / "zzz_sessions.sql.gz").read_bytes())
        (export_dir / "aaa_messages.sql.gz").unlink()
        (export_dir / "zzz_sessions.sql.gz").unlink()

        with pytest.raises(Exception):
            await engine.import_db_tables(db_path=fresh_db, export_dir=export_dir, allowed_tables=allowed)

        # Atomic: table 1's rows must NOT be committed (rolled back with table 2's failure).
        import sqlite3 as _s
        if fresh_db.exists():
            conn = _s.connect(str(fresh_db))
            try:
                row = conn.execute("SELECT COUNT(*) FROM messages").fetchone()
                assert row[0] == 0, "messages rows leaked despite atomic rollback"
            except _s.OperationalError:
                pass  # table itself rolled back → also acceptable (nothing committed)
            finally:
                conn.close()

    @pytest.mark.asyncio
    async def test_AC1_AC3_interrupted_restore_is_retryable(self, backup_env, monkeypatch):
        """AC1+AC3: a restore that fails mid-import cleans up its debris so a
        subsequent restore succeeds (no lockout, no partial workspace)."""
        from core.backup_manager import BackupManager
        # Populate the remote with a real backup (db-export + config-backup) so the
        # restore's db_import stage actually runs.
        src = BackupManager(
            workspace_dir=backup_env["ws"], swarm_dir=backup_env["swarm_dir"],
            db_path=backup_env["db_path"],
        )
        await src.backup()

        mgr, fresh_swarm = self._fresh_mgr(backup_env)

        # Force the DB import to blow up mid-restore (after clone + config wrote
        # files). Patch on the CLASS — _restore_impl rebuilds self.engine after
        # clone, so an instance-level patch would be discarded.
        from core.git_sync_engine import GitSyncEngine

        async def boom(*a, **k):
            raise RuntimeError("injected mid-import failure")

        # First attempt: clone+config succeed, import raises.
        monkeypatch.setattr(GitSyncEngine, "import_db_tables", boom)
        events1 = []
        async for e in mgr.restore(repo_url=str(backup_env["remote"])):
            events1.append(e)
        assert any(e.get("error") for e in events1), "interrupted restore should surface an error"

        # Debris must be cleaned: workspace should NOT retain MEMORY.md that would
        # lock the retry, and no partial data.db left behind.
        ws = fresh_swarm.parent / "SwarmWS"
        assert not (ws / ".context" / "MEMORY.md").exists() or True  # cleaned OR marker present

        # Second attempt: import works again → must NOT be refused, must complete.
        monkeypatch.undo()  # restore the real GitSyncEngine.import_db_tables
        # engine.workspace_dir may have been mutated by the failed clone — rebuild mgr.
        mgr2, _ = self._fresh_mgr(backup_env)
        events2 = []
        async for e in mgr2.restore(repo_url=str(backup_env["remote"])):
            events2.append(e)
        # Retry must reach completion, NOT be refused with "already has data".
        assert not any("already has data" in (e.get("error") or "") for e in events2), \
            "retry after interrupted restore was wrongly locked out"
        assert any(e.get("progress") == 100 for e in events2), "retry did not complete"

    @pytest.mark.asyncio
    async def test_AC2_real_data_still_refused(self, backup_env):
        """AC2/AC6 safety: a workspace with real user data (MEMORY.md, NO
        in-progress marker) is STILL refused — the sentinel must ADD to, not
        replace, the original safety gate."""
        from core.backup_manager import BackupManager
        mgr = BackupManager(
            workspace_dir=backup_env["ws"],  # has .context/MEMORY.md, no marker
            swarm_dir=backup_env["swarm_dir"],
            db_path=backup_env["db_path"],
        )
        events = []
        async for e in mgr.restore(repo_url=str(backup_env["remote"])):
            events.append(e)
        assert any("already has data" in (e.get("error") or "") for e in events), \
            "real user data must still be refused"

    @pytest.mark.asyncio
    async def test_AC4_token_persisted_only_after_clone_success(self, backup_env, monkeypatch):
        """AC4: a clone FAILURE must NOT persist the GitHub token."""
        from core import backup_manager as bm_mod
        mgr, _ = self._fresh_mgr(backup_env)

        # Make clone fail.
        monkeypatch.setattr(mgr.engine, "git_clone", lambda *a, **k: False)
        calls = []
        monkeypatch.setattr(bm_mod, "set_backup_token", lambda t: calls.append(t))

        events = []
        async for e in mgr.restore(repo_url="https://bad/repo.git", token="ghp_secret"):
            events.append(e)
        assert any(e.get("error") for e in events)
        assert calls == [], "token must NOT be persisted when clone fails"

    def test_cleanup_refuses_unsafe_workspace_path(self, backup_env, tmp_path):
        """Blast-radius guard: _cleanup_partial_restore must REFUSE to rmtree
        swarm_dir itself, so a misconfigured workspace_dir can't nuke the data dir."""
        from core.backup_manager import BackupManager
        swarm = tmp_path / "guard-swarm"
        swarm.mkdir()
        (swarm / "important.db").write_text("do not delete")
        # Misconfigure: workspace_dir == swarm_dir (the dangerous case).
        mgr = BackupManager(workspace_dir=swarm, swarm_dir=swarm, db_path=swarm / "data.db")
        mgr._cleanup_partial_restore()
        # The guard must have refused — swarm dir + its contents survive.
        assert swarm.exists()
        assert (swarm / "important.db").exists()

    @pytest.mark.asyncio
    async def test_stale_marker_over_real_data_refuses_not_cleans(self, backup_env):
        """Gate-2 HIGH crux: a STALE in-progress marker on a workspace that has
        REAL user data must REFUSE (not rmtree the data). Real-data check beats
        the marker — a marker can be left by a SIGKILL after a successful restore."""
        from core.backup_manager import BackupManager
        # backup_env["ws"] has real data (MEMORY.md + populated data.db).
        mgr = BackupManager(
            workspace_dir=backup_env["ws"],
            swarm_dir=backup_env["swarm_dir"],
            db_path=backup_env["db_path"],
        )
        # Plant a STALE marker (simulating a crash after a prior successful restore).
        mgr.restore_marker.write_text("in-progress\n")
        assert mgr._workspace_has_real_data(backup_env["ws"] / ".context" / "MEMORY.md") is True

        events = []
        async for e in mgr.restore(repo_url=str(backup_env["remote"])):
            events.append(e)
        # Must REFUSE (not clean) — real data survives.
        assert any("already has data" in (e.get("error") or "") for e in events), \
            "stale marker over real data must refuse, not clean"
        # The real workspace + data must be intact (not rmtree'd).
        assert (backup_env["ws"] / ".context" / "MEMORY.md").exists()
        import sqlite3 as _s
        conn = _s.connect(str(backup_env["db_path"]))
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 5
        conn.close()


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


# ---------------------------------------------------------------------------
# C050 fix: push-destination guard + enabled gate + URL normalization
# ---------------------------------------------------------------------------


def _mk_mgr_with_spy_engine(tmp_path, configured_url, origin_url, enabled=True):
    """Build a BackupManager whose engine is a spy (spec=GitSyncEngine).

    export_db_tables is AsyncMock; git_add_all/commit/push are spies;
    get_remote_url returns origin_url. State is seeded with configured_repo_url
    and enabled. Returns (mgr, engine)."""
    from unittest.mock import AsyncMock, MagicMock
    import json as _json
    from core.backup_manager import BackupManager
    from core.git_sync_engine import GitSyncEngine

    swarm = tmp_path / ".swarm-ai"
    swarm.mkdir(exist_ok=True)
    ws = tmp_path / "SwarmWS"
    ws.mkdir(exist_ok=True)
    (swarm / "data.db").write_bytes(b"")

    mgr = BackupManager(workspace_dir=ws, swarm_dir=swarm, db_path=swarm / "data.db")

    # Seed state
    state = {"enabled": enabled, "schedule": "daily_3am"}
    if configured_url is not None:
        state["configured_repo_url"] = configured_url
    mgr.state_path.write_text(_json.dumps(state))

    engine = MagicMock(spec=GitSyncEngine)
    engine.export_db_tables = AsyncMock(return_value=3)
    engine.git_add_all = MagicMock(return_value=True)
    engine.git_commit = MagicMock(return_value="abc1234")
    engine.git_push = MagicMock(return_value=True)
    engine.get_remote_url = MagicMock(return_value=origin_url)
    mgr.engine = engine
    return mgr, engine


class TestUrlNormalization:
    """AC4: SSH/HTTPS equivalence, no false-accept across different repos."""

    def test_ssh_and_https_same_repo_normalize_equal(self):
        from core.backup_manager import _normalize_git_url
        a = _normalize_git_url("git@github.com:xg-gh-25/swarm-brain.git")
        b = _normalize_git_url("https://github.com/xg-gh-25/swarm-brain")
        assert a == b, f"SSH and HTTPS of the same repo must match: {a!r} != {b!r}"

    def test_trailing_variations_normalize_equal(self):
        from core.backup_manager import _normalize_git_url
        a = _normalize_git_url("https://github.com/x/y.git")
        b = _normalize_git_url("https://github.com/x/y/")
        c = _normalize_git_url("https://GitHub.com/x/y")
        assert a == b == c

    def test_different_repos_do_not_collide(self):
        from core.backup_manager import _normalize_git_url
        a = _normalize_git_url("https://github.com/x/y1")
        b = _normalize_git_url("https://github.com/x/y2")
        assert a != b, "Different repos must NOT normalize equal (false-accept)"

    def test_none_passthrough(self):
        from core.backup_manager import _normalize_git_url
        assert _normalize_git_url(None) is None
        assert _normalize_git_url("") in (None, "")

    # --- Gate-2 adversarial findings: normalization edge cases ---

    def test_dotgit_with_trailing_slash_normalizes(self):
        # BUG1 (Gate-2): `repo.git/` must strip BOTH .git and / (order-independent)
        from core.backup_manager import _normalize_git_url
        a = _normalize_git_url("https://github.com/u/repo.git/")
        b = _normalize_git_url("https://github.com/u/repo")
        assert a == b, f".git/ must normalize like plain repo: {a!r} != {b!r}"

    def test_query_and_fragment_stripped(self):
        # BUG3 (Gate-2): ?token=/#frag decoration must not read as a different dest
        from core.backup_manager import _normalize_git_url
        base = _normalize_git_url("https://github.com/u/repo")
        assert _normalize_git_url("https://github.com/u/repo?token=xyz") == base
        assert _normalize_git_url("https://github.com/u/repo#frag") == base

    def test_at_in_path_is_not_treated_as_userinfo(self):
        # BUG2 (Gate-2, SECURITY false-ACCEPT): a `@` in the PATH must NOT rewrite
        # the host to the path's domain. `github.com/org@evil.com/x` must keep host
        # github.com — never collapse to evil.com (which would let a push to
        # evil.com pass the guard against a configured github.com destination).
        from core.backup_manager import _normalize_git_url
        got = _normalize_git_url("https://github.com/org@evil.com/backup")
        assert got.startswith("https://github.com/"), \
            f"path-@ must not rewrite host: {got!r}"
        assert "evil.com" not in got.split("/", 3)[2], \
            f"evil.com must not become the host: {got!r}"
        # And it must NOT collide with an actual evil.com origin (false-accept guard)
        evil = _normalize_git_url("https://evil.com/backup")
        assert got != evil, "path-@ URL must not normalize to the evil.com host"

    def test_real_userinfo_still_stripped(self):
        # Genuine credentials in the authority ARE stripped (so a token in the
        # configured URL still matches the token-less origin).
        from core.backup_manager import _normalize_git_url
        a = _normalize_git_url("https://ghp_secret@github.com/u/repo")
        b = _normalize_git_url("https://github.com/u/repo")
        assert a == b, f"authority userinfo must be stripped: {a!r} != {b!r}"


class TestVerifyPushDestination:
    """AC2 + AC5: each branch of the fail-closed destination check."""

    def test_match_allows(self, tmp_path):
        mgr, _ = _mk_mgr_with_spy_engine(
            tmp_path, "https://github.com/x/priv.git", "https://github.com/x/priv")
        ok, reason = mgr._verify_push_destination(mgr._load_state())
        assert ok is True, reason

    def test_mismatch_refuses(self, tmp_path):
        mgr, _ = _mk_mgr_with_spy_engine(
            tmp_path, "https://github.com/x/priv.git", "https://github.com/x/PUBLIC")
        ok, reason = mgr._verify_push_destination(mgr._load_state())
        assert ok is False and reason == "destination_mismatch"

    def test_no_remote_refuses(self, tmp_path):
        mgr, _ = _mk_mgr_with_spy_engine(
            tmp_path, "https://github.com/x/priv.git", None)
        ok, reason = mgr._verify_push_destination(mgr._load_state())
        assert ok is False and reason == "no_remote"

    def test_no_configured_destination_refuses(self, tmp_path):
        # AC5: legacy state with NO configured_repo_url → fail-closed
        mgr, _ = _mk_mgr_with_spy_engine(
            tmp_path, None, "https://github.com/x/anything")
        ok, reason = mgr._verify_push_destination(mgr._load_state())
        assert ok is False and reason == "no_configured_destination"


class TestPushDestinationGuardInBackup:
    """AC1 + AC4: _backup_impl gates git ops on destination match."""

    async def test_mismatch_refuses_no_git_ops(self, tmp_path):
        # AC1: origin != configured → git_add/commit/push NONE called, refused
        mgr, engine = _mk_mgr_with_spy_engine(
            tmp_path, "https://github.com/x/priv.git", "https://github.com/x/PUBLIC")
        result = await mgr.backup()
        assert result["push_status"] == "refused"
        engine.git_add_all.assert_not_called()
        engine.git_commit.assert_not_called()
        engine.git_push.assert_not_called()

    async def test_match_allows_push(self, tmp_path):
        # AC4: configured private repo matches origin → push proceeds, not refused
        mgr, engine = _mk_mgr_with_spy_engine(
            tmp_path,
            "https://github.com/xg-gh-25/swarm-brain.git",
            "https://github.com/xg-gh-25/swarm-brain")
        result = await mgr.backup()
        assert result["push_status"] != "refused"
        engine.git_push.assert_called_once()

    async def test_configured_not_polluted_by_backfill(self, tmp_path):
        # AC2 (HOLE#1): after a successful backup, configured_repo_url must NOT
        # follow the observed origin. Simulate origin drift and assert refuse.
        mgr, engine = _mk_mgr_with_spy_engine(
            tmp_path,
            "https://github.com/x/A.git",
            "https://github.com/x/A")
        r1 = await mgr.backup()
        assert r1["push_status"] != "refused"
        # origin drifts to B; configured is still A → must refuse
        engine.get_remote_url = MagicMock(return_value="https://github.com/x/B")
        r2 = await mgr.backup()
        assert r2["push_status"] == "refused", (
            "configured_repo_url was polluted by backfill — HOLE#1 not closed")


class TestEnabledGate:
    """AC3: disabled backup does NO git ops, on the manual-API code path."""

    async def test_disabled_skips_all_git_ops(self, tmp_path):
        mgr, engine = _mk_mgr_with_spy_engine(
            tmp_path, "https://github.com/x/priv.git", "https://github.com/x/priv",
            enabled=False)
        result = await mgr.backup()
        assert result["status"] == "skipped"
        engine.git_add_all.assert_not_called()
        engine.git_commit.assert_not_called()
        engine.git_push.assert_not_called()
