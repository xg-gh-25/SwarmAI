"""A2 (startup hazard): a malformed-but-nonzero data.db must be detected and
re-seeded at boot, NOT used → crash-loop.

The 0-byte guard in _ensure_database_initialized only catches size==0. A DB that
is non-empty but MALFORMED (torn WAL write / truncated page from a crash
mid-write — the classic `database disk image is malformed`) passes the size
check → the fast path returns True → the first real query (migrations) raises
sqlite3.DatabaseError → lifespan aborts → launchd KeepAlive restarts into the
same corrupt DB → infinite crash-loop, user stuck at "initializing" forever.

Fix: after the size check, run an integrity probe (PRAGMA quick_check /
open+read) and re-seed on failure — mirroring the existing 0-byte recovery.
"""
import sqlite3

import pytest


@pytest.fixture
def temp_app_data_dir(tmp_path):
    app_data = tmp_path / ".swarm-ai"
    app_data.mkdir(parents=True, exist_ok=True)
    return app_data


@pytest.fixture
def seed_db_path(tmp_path):
    """A minimal VALID seed.db (initialization_complete=1)."""
    seed_path = tmp_path / "seed.db"
    conn = sqlite3.connect(str(seed_path))
    conn.execute(
        "CREATE TABLE app_settings (id TEXT PRIMARY KEY, initialization_complete INTEGER)"
    )
    conn.execute("INSERT INTO app_settings VALUES ('default', 1)")
    conn.commit()
    conn.close()
    return seed_path


def _write_malformed_db(path):
    """Write a non-zero file that is NOT a valid SQLite database."""
    # Valid SQLite files start with "SQLite format 3\000". Garbage bytes of
    # non-zero length pass the size check but fail any real query.
    path.write_bytes(b"this is not a sqlite database, it is garbage\n" * 4)


def test_malformed_nonzero_db_is_reseeded_not_used(
    temp_app_data_dir, seed_db_path, monkeypatch
):
    """A malformed non-zero data.db → detected, removed, re-seeded from seed.db.

    UNFIXED behavior: size>0 → returns True → the caller trusts a corrupt DB →
    first query crashes → KeepAlive crash-loop.
    FIXED behavior: integrity probe fails → unlink + re-seed → returns True with
    a VALID db that answers a query.
    """
    monkeypatch.setattr("main.get_app_data_dir", lambda: temp_app_data_dir)
    monkeypatch.setattr("main._get_seed_database_path", lambda: seed_db_path)

    corrupt = temp_app_data_dir / "data.db"
    _write_malformed_db(corrupt)
    assert corrupt.stat().st_size > 0  # passes the 0-byte guard

    from main import _ensure_database_initialized

    result = _ensure_database_initialized()

    # DB must be ready...
    assert result is True
    # ...and the file on disk must now be a VALID, queryable SQLite DB
    # (re-seeded), not the garbage we wrote.
    conn = sqlite3.connect(str(corrupt))
    try:
        rows = conn.execute(
            "SELECT initialization_complete FROM app_settings WHERE id='default'"
        ).fetchone()
    finally:
        conn.close()
    assert rows is not None and rows[0] == 1, "data.db should be the re-seeded valid DB"


def test_valid_existing_db_is_preserved(temp_app_data_dir, seed_db_path, monkeypatch):
    """Negative-control: a VALID existing data.db is NOT re-seeded (data preserved).

    Guards against an over-eager integrity check that nukes good user data.
    """
    monkeypatch.setattr("main.get_app_data_dir", lambda: temp_app_data_dir)
    monkeypatch.setattr("main._get_seed_database_path", lambda: seed_db_path)

    # A valid user DB with a DISTINCT marker so we can prove it wasn't replaced.
    user_db = temp_app_data_dir / "data.db"
    conn = sqlite3.connect(str(user_db))
    conn.execute("CREATE TABLE app_settings (id TEXT PRIMARY KEY, initialization_complete INTEGER)")
    conn.execute("INSERT INTO app_settings VALUES ('default', 1)")
    conn.execute("CREATE TABLE user_marker (note TEXT)")
    conn.execute("INSERT INTO user_marker VALUES ('DO_NOT_LOSE_ME')")
    conn.commit()
    conn.close()

    from main import _ensure_database_initialized

    assert _ensure_database_initialized() is True

    conn = sqlite3.connect(str(user_db))
    try:
        marker = conn.execute("SELECT note FROM user_marker").fetchone()
    finally:
        conn.close()
    assert marker is not None and marker[0] == "DO_NOT_LOSE_ME", "valid user DB must be preserved, not re-seeded"


def test_locked_valid_db_is_not_destroyed(temp_app_data_dir, seed_db_path, monkeypatch):
    """HIGH data-loss regression: a valid-but-LOCKED data.db must be preserved.

    sqlite3.OperationalError ("database is locked") IS A SUBCLASS of
    sqlite3.DatabaseError. If _db_is_intact's except clauses are ordered
    DatabaseError-first, a momentarily-locked-but-valid DB (concurrent boot
    access / stale -wal replay / NFS lock) is caught as "corrupt" → False →
    _ensure_database_initialized unlink()s + re-seeds → USER DATA DESTROYED.
    The OperationalError clause MUST precede DatabaseError. This test holds an
    EXCLUSIVE lock and asserts the user's distinct data survives.
    """
    monkeypatch.setattr("main.get_app_data_dir", lambda: temp_app_data_dir)
    monkeypatch.setattr("main._get_seed_database_path", lambda: seed_db_path)

    user_db = temp_app_data_dir / "data.db"
    setup = sqlite3.connect(str(user_db))
    setup.execute("CREATE TABLE app_settings (id TEXT PRIMARY KEY, initialization_complete INTEGER)")
    setup.execute("INSERT INTO app_settings VALUES ('default', 1)")
    setup.execute("CREATE TABLE user_marker (note TEXT)")
    setup.execute("INSERT INTO user_marker VALUES ('PRECIOUS_USER_DATA')")
    setup.commit()
    setup.close()

    # Hold an EXCLUSIVE lock so _db_is_intact's PRAGMA quick_check hits
    # "database is locked" (OperationalError) — the exact false-positive path.
    locker = sqlite3.connect(str(user_db), timeout=0.1)
    locker.execute("BEGIN EXCLUSIVE")
    try:
        from main import _ensure_database_initialized
        _ensure_database_initialized()
    finally:
        locker.rollback()
        locker.close()

    # The user's data must NOT have been destroyed by a false-corrupt re-seed.
    conn = sqlite3.connect(str(user_db))
    try:
        marker = conn.execute("SELECT note FROM user_marker").fetchone()
    finally:
        conn.close()
    assert marker is not None and marker[0] == "PRECIOUS_USER_DATA", (
        "a valid-but-locked DB was destroyed — OperationalError must be treated as intact"
    )
