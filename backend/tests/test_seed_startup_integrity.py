"""A2 (startup hazard): a malformed-but-nonzero data.db must be detected and
re-seeded at boot, NOT used → crash-loop.

**Design B (run_4326397d) — integrity by migration, not by pre-scan.** The old
approach ran ``PRAGMA quick_check`` on every boot (``_db_is_intact``) to detect a
malformed file. But quick_check reads every b-tree page → O(db-size) → ~47s on a
1.29GB db, a real cold-start regression. It was ALSO redundant: the migration run
inside ``initialize_database`` IS the integrity gate. A malformed/torn db makes the
first migration query raise ``sqlite3.DatabaseError`` — that IS the crash-loop
trigger A2 defends against. So ``_init_db_bounded`` now catches that DatabaseError
on the fast path → purge (incl. -wal/-shm) → re-seed → retry the migration ONCE on
the fresh db → if that also raises, re-raise (bounded KeepAlive restart, never an
infinite re-seed loop). This detects the crash-loop class for free and keeps
cold-start <1s.

**Contract narrowing (deliberate, documented):** a torn CONTENT page that does NOT
surface during migration (e.g. a damaged row page in a large table) is NO LONGER
detected at boot. That is intentional and safe: it does not crash-loop (migrations
touch schema, not arbitrary row content — verified: ``SELECT count(*)`` over a
torn-content-page db does not raise). It would surface later as a specific query
error, which is out of scope for a boot-time crash-loop guard. The prior
``test_openable_but_page_corrupt_db`` asserted detection of THIS shape via
quick_check; it is retired here because the shape it targeted is not the crash-loop
class and quick_check (the only thing that caught it) cost 47s.

**Load-bearing invariant:** ``sqlite3.OperationalError`` IS A SUBCLASS of
``sqlite3.DatabaseError``. A momentarily-locked-but-VALID db raises
OperationalError ("database is locked") — it must NOT be treated as corruption
(that would unlink()+re-seed a valid db → USER DATA DESTROYED, the run_2d3417d9
HIGH). The OperationalError re-raise clause MUST precede the DatabaseError recovery
clause in ``_init_db_bounded``.
"""
import asyncio
import sqlite3

import pytest


@pytest.fixture
def temp_app_data_dir(tmp_path):
    app_data = tmp_path / ".swarm-ai"
    app_data.mkdir(parents=True, exist_ok=True)
    return app_data


@pytest.fixture
def seed_db_path(tmp_path):
    """A copy of the REAL production seed.db.

    O009 (mock ≠ reality): a hand-crafted minimal seed omits tables that
    migrations assume already exist (e.g. ``tasks``) — not everything is a
    ``CREATE TABLE IF NOT EXISTS``. Re-seed + retry-migration must run against
    the same schema shape production ships, so we copy the real seed.db. If it
    is unavailable (unexpected in CI), skip rather than assert on a fake shape.
    """
    import shutil
    import main

    real_seed = main._get_seed_database_path()
    if not real_seed or not real_seed.exists():
        pytest.skip("real seed.db not available in this environment")
    seed_path = tmp_path / "seed.db"
    shutil.copy2(real_seed, seed_path)
    return seed_path


def _point_singleton_at(monkeypatch, temp_app_data_dir, seed_db_path):
    """Wire main + database + settings so both the purge path
    (``get_app_data_dir()/data.db``) and the migration path
    (``settings.sqlite_db_path``) resolve to the SAME temp data.db, and the
    DB singleton is fresh for this test.
    """
    import database
    from config import settings

    user_db = temp_app_data_dir / "data.db"
    monkeypatch.setattr("main.get_app_data_dir", lambda: temp_app_data_dir)
    monkeypatch.setattr("main._get_seed_database_path", lambda: seed_db_path)
    monkeypatch.setattr(settings, "sqlite_db_path", str(user_db))
    # Fresh singleton so initialize() actually runs against our temp db.
    database._db_instance = None
    return user_db


def _write_garbage_db(path):
    """A non-zero file that is NOT a valid SQLite database (fails on first query)."""
    path.write_bytes(b"this is not a sqlite database, it is garbage\n" * 4)


# --------------------------------------------------------------------------- #
# _ensure_database_initialized — the synchronous size/seed guard.
# --------------------------------------------------------------------------- #


def test_zero_byte_db_is_purged_and_reseeded(temp_app_data_dir, seed_db_path, monkeypatch):
    """A 0-byte data.db (interrupted create) → purged + re-seeded from seed.db."""
    user_db = _point_singleton_at(monkeypatch, temp_app_data_dir, seed_db_path)
    user_db.write_bytes(b"")  # 0-byte
    assert user_db.stat().st_size == 0

    from main import _ensure_database_initialized

    assert _ensure_database_initialized() is True
    # File on disk is now the valid re-seeded db.
    conn = sqlite3.connect(str(user_db))
    try:
        row = conn.execute(
            "SELECT initialization_complete FROM app_settings WHERE id='default'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row[0] == 1


def test_valid_existing_db_is_preserved(temp_app_data_dir, seed_db_path, monkeypatch):
    """Negative-control: a VALID existing data.db is NOT re-seeded (data preserved).

    Guards against an over-eager guard that nukes good user data: no content
    scan runs here, so a valid db with size>0 is trusted immediately.
    """
    user_db = _point_singleton_at(monkeypatch, temp_app_data_dir, seed_db_path)
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
    assert marker is not None and marker[0] == "DO_NOT_LOSE_ME", (
        "valid user DB must be preserved, not re-seeded"
    )


# --------------------------------------------------------------------------- #
# _init_db_bounded — the migration-catch integrity gate (Design B).
# --------------------------------------------------------------------------- #


def test_malformed_db_migration_catch_reseeds(temp_app_data_dir, seed_db_path, monkeypatch):
    """The crash-loop class: a malformed non-zero data.db whose MIGRATION raises
    sqlite3.DatabaseError → _init_db_bounded purges + re-seeds + retries once →
    ends with a VALID, queryable db (no crash-loop).

    This is the behavioral heart of Design B. Mutation check: delete the
    ``except sqlite3.DatabaseError`` recovery clause in _init_db_bounded and this
    test goes RED (the DatabaseError propagates, no re-seed happens).
    """
    user_db = _point_singleton_at(monkeypatch, temp_app_data_dir, seed_db_path)
    _write_garbage_db(user_db)
    assert user_db.stat().st_size > 0  # passes the 0-byte guard

    from main import _init_db_bounded

    # Must NOT raise — the migration DatabaseError is caught + recovered.
    asyncio.run(_init_db_bounded(skip_schema=True))

    conn = sqlite3.connect(str(user_db))
    try:
        row = conn.execute(
            "SELECT initialization_complete FROM app_settings WHERE id='default'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row[0] == 1, (
        "data.db should be the re-seeded valid DB after migration-catch recovery"
    )


def test_isolation_failure_never_reseeds_over_unisolated_store(
    temp_app_data_dir, seed_db_path, monkeypatch
):
    """AC4-boot (run_d47d3e5e): if isolate_store CANNOT preserve (rename fails on a
    read-only fs / held handle → IsolationError), _init_db_bounded MUST NOT reseed —
    reseeding would os.replace() OVER a store that was never moved away = a 2nd wipe
    (STEERING #20). The IsolationError must propagate (bounded restart, data in place).

    Mutation check: if IsolationError were swallowed / _purge returned None-then-reseed,
    _reseed_from_seed WOULD be called → this test's call-count assertion goes RED.
    """
    import main
    user_db = _point_singleton_at(monkeypatch, temp_app_data_dir, seed_db_path)
    _write_garbage_db(user_db)
    assert user_db.stat().st_size > 0

    from core.data_safety import IsolationError

    # Force isolation to fail as if the fs were read-only / the file held open.
    def _isolate_fails(target):
        raise IsolationError(f"read-only fs: cannot isolate {target}")

    monkeypatch.setattr("core.data_safety.isolate_store", _isolate_fails)

    reseed_calls = {"n": 0}
    real_reseed = main._reseed_from_seed

    def _spy_reseed(path):
        reseed_calls["n"] += 1
        return real_reseed(path)

    monkeypatch.setattr(main, "_reseed_from_seed", _spy_reseed)

    from main import _init_db_bounded

    # The IsolationError must propagate AS IsolationError — NOT be swallowed, NOT be
    # silently converted to another exception before reseed. (A broad matcher would let
    # an unrelated bug pass; this enforces the actual propagation contract.)
    with pytest.raises(IsolationError):
        asyncio.run(_init_db_bounded(skip_schema=True))

    assert reseed_calls["n"] == 0, (
        "CRITICAL: _reseed_from_seed was called after isolation FAILED — this would "
        "os.replace() over the un-isolated live store = a 2nd data wipe (STEERING #20)"
    )
    # The original (un-isolated) file must still be on disk.
    assert user_db.exists(), "un-isolated store must remain in place, never destroyed"


def test_valid_db_migration_no_reseed(temp_app_data_dir, seed_db_path, monkeypatch):
    """A valid existing db must survive _init_db_bounded (migrations run, no
    re-seed): its distinct user marker must remain (proves no purge happened)."""
    import shutil

    user_db = _point_singleton_at(monkeypatch, temp_app_data_dir, seed_db_path)
    # Start from the REAL schema (migrations assume full schema) + a distinct
    # marker so we can prove the file was NOT purged/replaced.
    shutil.copy2(seed_db_path, user_db)
    conn = sqlite3.connect(str(user_db))
    conn.execute("CREATE TABLE user_marker (note TEXT)")
    conn.execute("INSERT INTO user_marker VALUES ('PRECIOUS')")
    conn.commit()
    conn.close()

    from main import _init_db_bounded

    asyncio.run(_init_db_bounded(skip_schema=True))

    conn = sqlite3.connect(str(user_db))
    try:
        marker = conn.execute("SELECT note FROM user_marker").fetchone()
    finally:
        conn.close()
    assert marker is not None and marker[0] == "PRECIOUS", (
        "a valid db must NOT be purged by the migration-catch path"
    )


def test_locked_valid_db_is_not_destroyed(temp_app_data_dir, seed_db_path, monkeypatch):
    """HIGH data-loss regression: a valid-but-LOCKED data.db must be preserved.

    An exclusive lock makes the migration raise sqlite3.OperationalError
    ("database is locked"). OperationalError IS A SUBCLASS of DatabaseError; if
    the except clauses were ordered DatabaseError-first, the lock would be
    mis-read as corruption → purge + re-seed → USER DATA DESTROYED. The
    OperationalError clause MUST precede DatabaseError and re-raise WITHOUT
    purging. This test holds an EXCLUSIVE lock and asserts the user's distinct
    data survives (whatever _init_db_bounded does — raise or not — it must NOT
    purge).
    """
    import shutil

    user_db = _point_singleton_at(monkeypatch, temp_app_data_dir, seed_db_path)
    shutil.copy2(seed_db_path, user_db)  # real schema
    setup = sqlite3.connect(str(user_db))
    setup.execute("CREATE TABLE user_marker (note TEXT)")
    setup.execute("INSERT INTO user_marker VALUES ('PRECIOUS_USER_DATA')")
    setup.commit()
    setup.close()

    # Hold an EXCLUSIVE lock so the migration hits "database is locked".
    locker = sqlite3.connect(str(user_db), timeout=0.1)
    locker.execute("BEGIN EXCLUSIVE")
    try:
        from main import _init_db_bounded

        # It may raise (OperationalError/RuntimeError) OR complete — but it must
        # NEVER purge a locked valid db. Swallow any raise; assert data survives.
        try:
            asyncio.run(_init_db_bounded(skip_schema=True))
        except Exception:
            pass
    finally:
        locker.rollback()
        locker.close()

    conn = sqlite3.connect(str(user_db))
    try:
        marker = conn.execute("SELECT note FROM user_marker").fetchone()
    finally:
        conn.close()
    assert marker is not None and marker[0] == "PRECIOUS_USER_DATA", (
        "a valid-but-locked DB was destroyed — OperationalError must be treated "
        "as transient, never as corruption"
    )


def test_reseed_retry_is_bounded_not_infinite(temp_app_data_dir, monkeypatch):
    """If the SEED itself is unusable (re-seed can't produce a valid db), the
    migration-catch must NOT loop forever — it re-raises after one retry.

    Simulate an unavailable seed: _get_seed_database_path → None. A garbage
    data.db then can't be recovered → _init_db_bounded must raise (bounded
    KeepAlive restart), not spin.
    """
    import database
    from config import settings

    user_db = temp_app_data_dir / "data.db"
    monkeypatch.setattr("main.get_app_data_dir", lambda: temp_app_data_dir)
    monkeypatch.setattr("main._get_seed_database_path", lambda: None)  # no seed
    monkeypatch.setattr(settings, "sqlite_db_path", str(user_db))
    database._db_instance = None

    _write_garbage_db(user_db)

    from main import _init_db_bounded

    with pytest.raises(sqlite3.DatabaseError):
        asyncio.run(_init_db_bounded(skip_schema=True))


def test_reseed_succeeds_but_fresh_db_also_bad_propagates(
    temp_app_data_dir, monkeypatch
):
    """The reseed-then-retry-fails branch (Gate-2 Risk-6 coverage): if
    ``_reseed_from_seed`` reports success but the freshly written db STILL fails
    migration, the retry's DatabaseError must PROPAGATE (bounded restart), not
    trigger another purge/re-seed cycle.

    This exercises the retry ``await`` at the tail of the DatabaseError block —
    the exact path the boundedness inspection relies on. Simulate it by making
    ``_reseed_from_seed`` return True while leaving a garbage file in place, so
    the retry migration raises a SECOND DatabaseError.
    """
    import database
    from config import settings

    user_db = temp_app_data_dir / "data.db"
    monkeypatch.setattr("main.get_app_data_dir", lambda: temp_app_data_dir)
    monkeypatch.setattr(settings, "sqlite_db_path", str(user_db))
    database._db_instance = None

    _write_garbage_db(user_db)

    reseed_calls = {"n": 0}

    def _fake_reseed(path):
        # Report success but write garbage again → retry migration fails again.
        reseed_calls["n"] += 1
        _write_garbage_db(path)
        return True

    monkeypatch.setattr("main._reseed_from_seed", _fake_reseed)

    from main import _init_db_bounded

    with pytest.raises(sqlite3.DatabaseError):
        asyncio.run(_init_db_bounded(skip_schema=True))

    # Bounded: reseed attempted exactly ONCE (no infinite purge/reseed loop).
    assert reseed_calls["n"] == 1, (
        f"retry must be bounded to a single re-seed, got {reseed_calls['n']}"
    )


# --------------------------------------------------------------------------- #
# run_a456640f (option B): boot-path corruption recovery must PRESERVE (isolate,
# not unlink), reseed fresh to keep the daemon reachable, and drop a recovery
# marker so the recover-vs-discard decision reaches the user at the next session.
# --------------------------------------------------------------------------- #


def test_boot_corruption_isolates_not_deletes_and_marks(
    temp_app_data_dir, seed_db_path, monkeypatch
):
    """A malformed data.db at boot → the ORIGINAL is preserved (renamed to
    .corrupt-<ts>, never unlinked) AND a recovery marker is written. This is the
    core anti-COE guarantee: no irreplaceable store is destroyed on the boot path.
    """
    from core.data_safety import read_recovery_marker

    user_db = _point_singleton_at(monkeypatch, temp_app_data_dir, seed_db_path)
    _write_garbage_db(user_db)
    original_bytes = user_db.read_bytes()

    from main import _init_db_bounded

    # Corruption is a known signature ("file is not a database") → recover.
    # Reseed from the real seed makes the retry migration succeed → no raise.
    asyncio.run(_init_db_bounded(skip_schema=True))

    # PRESERVED: an isolated copy of the original corrupt db exists on disk.
    isolated = list(temp_app_data_dir.glob("data.db.corrupt-*"))
    assert isolated, "corrupt db must be isolated (renamed), not deleted"
    assert isolated[0].read_bytes() == original_bytes, "isolated copy is the original data"

    # NON-SILENT: a recovery marker names the isolated file + reason.
    marker = read_recovery_marker(temp_app_data_dir)
    assert marker is not None, "a recovery marker must be written for in-band surfacing"
    assert marker["isolated_path"] == str(isolated[0])

    # REACHABLE: a fresh valid db is live so the daemon boots (B, not A).
    conn = sqlite3.connect(str(user_db))
    try:
        assert conn.execute(
            "SELECT initialization_complete FROM app_settings WHERE id='default'"
        ).fetchone() is not None
    finally:
        conn.close()


def test_boot_non_corruption_databaseerror_does_not_destroy(
    temp_app_data_dir, seed_db_path, monkeypatch
):
    """AC5 verdict tightened: a DatabaseError that is NOT a corruption signature
    (e.g. 'database or disk is full') must PROPAGATE as a bounded restart —
    NEVER isolate/reseed a possibly-valid db. This is the exact false-positive
    the COE (a bare DatabaseError → whole-store destroy) is about.
    """
    import database
    from config import settings

    user_db = _point_singleton_at(monkeypatch, temp_app_data_dir, seed_db_path)
    # A real, VALID db must survive — build a minimal valid one.
    conn = sqlite3.connect(str(user_db))
    conn.execute("CREATE TABLE keep (v TEXT)")
    conn.execute("INSERT INTO keep VALUES ('PRECIOUS')")
    conn.commit()
    conn.close()
    valid_bytes = user_db.read_bytes()

    # Force initialize_database to raise a NON-signature DatabaseError.
    async def _raise_disk_full(skip_schema=False):
        raise sqlite3.DatabaseError("database or disk is full")

    monkeypatch.setattr("main.initialize_database", _raise_disk_full)

    reseed_spy = {"n": 0}
    monkeypatch.setattr(
        "main._reseed_from_seed",
        lambda p: (reseed_spy.__setitem__("n", reseed_spy["n"] + 1) or True),
    )

    from main import _init_db_bounded

    with pytest.raises(sqlite3.DatabaseError):
        asyncio.run(_init_db_bounded(skip_schema=True))

    # The valid db is UNTOUCHED — not isolated, not reseeded.
    assert user_db.exists() and user_db.read_bytes() == valid_bytes, "valid db preserved intact"
    assert list(temp_app_data_dir.glob("data.db.corrupt-*")) == [], "must NOT isolate a non-corrupt db"
    assert reseed_spy["n"] == 0, "must NOT reseed on a non-corruption error"
