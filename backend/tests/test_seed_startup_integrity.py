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


def _write_openable_but_page_corrupt_db(path):
    """Write a db that OPENS fine (valid header) but fails PRAGMA quick_check.

    This is the *production-realistic* corruption shape A2 exists for: a torn
    WAL write / truncated b-tree page from a crash mid-write. The file header is
    intact, sqlite3.connect() succeeds, and the corruption only surfaces as a
    non-"ok" quick_check result row — it does NOT raise on connect. This is the
    exact path that exercises _db_is_intact's ``row[0] == "ok"`` branch, which
    _write_malformed_db (unopenable garbage → DatabaseError branch) never reaches.
    """
    # Build a valid multi-page db (small page_size forces many b-tree pages fast).
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA page_size=512")
        conn.execute(
            "CREATE TABLE app_settings (id TEXT PRIMARY KEY, initialization_complete INTEGER)"
        )
        conn.execute("INSERT INTO app_settings VALUES ('default', 1)")
        # A marker table the seed.db does NOT have — lets the re-seed assertion
        # prove the file was REPLACED (marker gone), not merely re-read. Without
        # this, the corruption spares app_settings so a no-op would falsely pass.
        conn.execute("CREATE TABLE pre_corrupt_marker (note TEXT)")
        conn.execute("INSERT INTO pre_corrupt_marker VALUES ('ONLY_IN_CORRUPT_DB')")
        conn.execute("CREATE TABLE t (k INTEGER PRIMARY KEY, v TEXT)")
        for i in range(400):  # force several b-tree pages so a mid-file page exists
            conn.execute("INSERT INTO t VALUES (?, ?)", (i, "x" * 80))
        conn.commit()
    finally:
        conn.close()

    # Corrupt the BODY of a single interior page, computed from the REAL file
    # size (not a hardcoded offset — that drifts when the schema changes and can
    # miss the data pages, leaving quick_check "ok"). Target page 3/4 through the
    # file: never page 1 (header + schema), deep enough to hit table t's data.
    # +24 skips the page header so we damage cell content, and we touch ONLY this
    # one page → the file still OPENS (header intact) but quick_check reports
    # tree/page damage as a non-"ok" row (not a connect-time DatabaseError).
    PAGE_SIZE = 512
    size = path.stat().st_size
    npages = size // PAGE_SIZE
    target_page = max(2, (npages * 3) // 4)  # 1-based; never touch page 1
    with open(path, "r+b") as f:
        f.seek((target_page - 1) * PAGE_SIZE + 24)
        f.write(b"\xff" * (PAGE_SIZE - 40))


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


def test_openable_but_page_corrupt_db_is_detected_and_reseeded(
    temp_app_data_dir, seed_db_path, monkeypatch
):
    """The REAL corruption shape: header valid, db OPENS, but quick_check fails.

    A torn WAL / truncated page from a crash mid-write leaves a db that
    sqlite3.connect() opens without error — the corruption surfaces only as a
    non-"ok" ``PRAGMA quick_check`` result ROW, never as a connect-time raise.
    This exercises _db_is_intact's ``row[0] == "ok"`` return-value check (the
    most production-realistic AND data-DELETING branch), which the garbage-bytes
    test (unopenable → DatabaseError) never reaches. Without the return-value
    check, quick_check's non-ok row would be ignored → the corrupt db is trusted
    → KeepAlive crash-loop.

    Asserts BOTH halves: (a) _db_is_intact returns False for this shape, and
    (b) _ensure_database_initialized re-seeds it into a valid, queryable db.
    """
    monkeypatch.setattr("main.get_app_data_dir", lambda: temp_app_data_dir)
    monkeypatch.setattr("main._get_seed_database_path", lambda: seed_db_path)

    corrupt = temp_app_data_dir / "data.db"
    _write_openable_but_page_corrupt_db(corrupt)
    assert corrupt.stat().st_size > 0  # passes the 0-byte guard

    from main import _db_is_intact, _ensure_database_initialized

    # (a) The db OPENS (proving it's not the DatabaseError branch) but the probe
    # must judge it corrupt via the non-ok quick_check row.
    probe = sqlite3.connect(str(corrupt), timeout=2.0)
    try:
        row = probe.execute("PRAGMA quick_check").fetchone()
    finally:
        probe.close()
    assert row is not None and row[0] != "ok", (
        "fixture precondition: this db must OPEN and quick_check must report "
        "corruption (non-ok row) — otherwise the test isn't exercising the "
        "row[0]=='ok' branch"
    )
    assert _db_is_intact(corrupt) is False, (
        "an openable-but-page-corrupt db must be judged NOT intact via the "
        "quick_check return-value check, not just via connect-time exceptions"
    )

    # (b) End-to-end: the caller re-seeds it into a valid, queryable db.
    assert _ensure_database_initialized() is True
    conn = sqlite3.connect(str(corrupt))
    try:
        rows = conn.execute(
            "SELECT initialization_complete FROM app_settings WHERE id='default'"
        ).fetchone()
        # Prove the file was REPLACED, not merely re-read: the pre-corrupt marker
        # (which the seed.db lacks) must be GONE. Without this, a no-op would pass
        # because the corruption spared app_settings and the seed value is identical.
        marker_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pre_corrupt_marker'"
        ).fetchone()
    finally:
        conn.close()
    assert rows is not None and rows[0] == 1, "data.db should be the re-seeded valid DB"
    assert marker_exists is None, (
        "re-seed did not happen: the pre-corrupt marker table survived, so the "
        "corrupt file was re-read rather than replaced by the seed"
    )


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
