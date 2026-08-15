"""Tests for core.data_safety — the unified pre-action destruction guard.

Covers the design ACs for run_a456640f (irreplaceable-data destruction guard):
  AC1  a code-path destroy of an IRREPLACEABLE store is intercepted; on non-approval
       the ORIGINAL still exists (isolated, not deleted).
  AC2  isolate-before-any-wait; never unlink/replace-over. Mutation-proven:
       reverting isolate->unlink turns test_ac1_* RED.
  AC3  a REPLACEABLE target (index/cache/*.tmp) is NOT gated — returns immediately,
       permission engine NOT invoked (anti-vacuity).
  AC4  cold-start branch does NOT await approval — isolate + DestructionBlocked,
       enqueue NOT called (does not wedge boot).
  AC7  with a live session_id, approval routes to THAT session's queue; with NO
       session, degraded branch (isolate + block), enqueue NOT called, no hang.
  AC2-sidecar  isolate moves target + -wal/-shm together (no orphan WAL).

Methodology: guard_destructive is async; permission_manager is mocked at the
boundary (its wait_for_permission_decision / enqueue) — never the guard's own logic.
"""
import asyncio
from pathlib import Path

import pytest

from core.data_safety import (
    StoreClass,
    classify_store,
    isolate_store,
    guard_destructive,
    DestructionBlocked,
    IsolationError,
    is_corruption_error,
    write_recovery_marker,
    read_recovery_marker,
    clear_recovery_marker,
)
import sqlite3


# ---------------------------------------------------------------------------
# classify_store — the selective gate (AC3 anti-vacuity)
# ---------------------------------------------------------------------------

def test_classify_replaceable_index_cache_tmp(tmp_path):
    # ANCHORED (suffix/exact) — a rebuildable index/cache OUTSIDE a governed dir.
    assert classify_store(tmp_path / "knowledge_fts.db") == StoreClass.REPLACEABLE
    assert classify_store(tmp_path / "L1_SYSTEM_PROMPTS.md") == StoreClass.REPLACEABLE
    assert classify_store(tmp_path / "something.tmp") == StoreClass.REPLACEABLE
    assert classify_store(tmp_path / "code_intel.db") == StoreClass.REPLACEABLE
    assert classify_store(tmp_path / "build.cache") == StoreClass.REPLACEABLE


def test_classify_irreplaceable_db_and_stores(tmp_path):
    assert classify_store(tmp_path / "data.db") == StoreClass.IRREPLACEABLE
    assert classify_store(tmp_path / "Projects" / "SwarmAI") == StoreClass.IRREPLACEABLE
    assert classify_store(tmp_path / ".context" / "MEMORY.md") == StoreClass.IRREPLACEABLE
    assert classify_store(tmp_path / "Knowledge" / "Library" / "x.md") == StoreClass.IRREPLACEABLE


def test_classify_governed_dir_dominates_replaceable_basename(tmp_path):
    """Adversarial HIGH (run_a456640f): a file INSIDE a governed store dir is
    IRREPLACEABLE regardless of a REPLACEABLE-looking basename. Without this, a
    loose '_fts'/'.cache' substring downgraded it to REPLACEABLE → ungated +
    un-isolated + destroyed (fail-OPEN in the shared authority)."""
    assert classify_store(tmp_path / ".context" / "knowledge_fts_notes.md") == StoreClass.IRREPLACEABLE
    assert classify_store(tmp_path / "Projects" / "CMHK" / "research_fts.md") == StoreClass.IRREPLACEABLE
    assert classify_store(tmp_path / "Projects" / "CMHK" / "data.cache") == StoreClass.IRREPLACEABLE
    assert classify_store(tmp_path / ".context" / "USER_fts.md") == StoreClass.IRREPLACEABLE
    # archive INSIDE a governed dir stays IRREPLACEABLE (dir wins over -archive)
    assert classify_store(tmp_path / ".context" / "MEMORY-archive.db") == StoreClass.IRREPLACEABLE


def test_classify_replaceable_substring_not_downgraded_outside_governed_dir(tmp_path):
    """A basename merely CONTAINING a marker is NOT auto-REPLACEABLE (anchoring):
    'knowledge_fts_notes.md' (contains _fts but doesn't end with it) is not an index."""
    # ends with _fts → index (replaceable); merely contains → not
    assert classify_store(tmp_path / "my_fts_notes.md") == StoreClass.IRREPLACEABLE
    assert classify_store(tmp_path / "notes_fts") == StoreClass.REPLACEABLE  # endswith _fts


def test_classify_recoverable_archive(tmp_path):
    # -archive OUTSIDE a governed dir → RECOVERABLE (softer); inside → IRREPLACEABLE
    assert classify_store(tmp_path / "MEMORY-archive-2026-08.md") == StoreClass.RECOVERABLE


def test_classify_unknown_is_failclosed_irreplaceable(tmp_path):
    # fail-closed: an unrecognized path is treated as IRREPLACEABLE (ask, don't destroy)
    assert classify_store(tmp_path / "mystery_store.dat") == StoreClass.IRREPLACEABLE


# ---------------------------------------------------------------------------
# isolate_store — preserve-not-destroy (AC2 + sidecars)
# ---------------------------------------------------------------------------

def test_isolate_renames_not_deletes(tmp_path):
    target = tmp_path / "data.db"
    target.write_text("real user data")
    moved = isolate_store(target)
    assert not target.exists(), "original path should be gone (renamed away)"
    assert moved.exists(), "isolated copy must exist"
    assert moved.read_text() == "real user data", "data preserved, not destroyed"
    assert ".corrupt-" in moved.name or ".bak-" in moved.name


def test_isolate_moves_wal_shm_sidecars_together(tmp_path):
    target = tmp_path / "data.db"
    target.write_text("db")
    wal = tmp_path / "data.db-wal"
    shm = tmp_path / "data.db-shm"
    wal.write_text("wal")
    shm.write_text("shm")
    moved = isolate_store(target)
    # no orphan sidecars left beside the (now-absent) original
    assert not wal.exists() and not shm.exists(), "sidecars must not be orphaned"
    assert (moved.parent / (moved.name + "-wal")).exists()
    assert (moved.parent / (moved.name + "-shm")).exists()


# ---------------------------------------------------------------------------
# isolate_store — rename-failure handling (AC4): rename() can raise OSError on a
# read-only fs, cross-device (EXDEV), or a Windows open handle. A bare rename that
# propagates would crash boot (main.py:_purge_corrupt_db → lifespan → crash-loop).
# EXDEV is recoverable (shutil.move copies across devices, still PRESERVES); an
# un-preservable OSError must raise IsolationError so the caller NEVER reseeds over
# a store that was not successfully isolated (that would be a 2nd data-wipe).
# ---------------------------------------------------------------------------

def test_isolate_exdev_falls_back_to_move_preserving_data(tmp_path, monkeypatch):
    """AC4: cross-device rename (EXDEV) → shutil.move fallback, data preserved."""
    import errno as _errno
    target = tmp_path / "data.db"
    target.write_text("real user data")
    orig_rename = Path.rename
    calls = {"rename": 0}

    def fake_rename(self, dst):
        # Only the primary target rename raises EXDEV; sidecars (none here) unaffected.
        if self == target:
            calls["rename"] += 1
            raise OSError(_errno.EXDEV, "Invalid cross-device link")
        return orig_rename(self, dst)

    monkeypatch.setattr(Path, "rename", fake_rename)
    moved = isolate_store(target)
    assert calls["rename"] == 1, "rename must have been attempted (and raised EXDEV)"
    assert not target.exists(), "original gone (moved away)"
    assert moved.exists() and moved.read_text() == "real user data", "data preserved via move"


def test_isolate_unpreservable_oserror_raises_isolation_error(tmp_path, monkeypatch):
    """AC4: a non-EXDEV OSError (read-only fs / open handle) → IsolationError (NOT a bare
    OSError, NOT a silent swallow) so the boot caller can skip reseed + re-raise."""
    import errno as _errno
    target = tmp_path / "data.db"
    target.write_text("real user data")

    def fake_rename(self, dst):
        raise OSError(_errno.EROFS, "Read-only file system")

    monkeypatch.setattr(Path, "rename", fake_rename)
    with pytest.raises(IsolationError):
        isolate_store(target)
    # data must still be on disk (never destroyed on a failed isolate)
    assert target.exists() and target.read_text() == "real user data"


def test_isolate_exdev_copy_failure_raises_isolation_error(tmp_path, monkeypatch):
    """AC4: if rename→EXDEV falls back to shutil.move and the CROSS-DEVICE COPY itself
    fails (ENOSPC on a full dest, EROFS mid-copy), it MUST raise IsolationError — never a
    bare OSError. The boot caller relies on IsolationError to skip reseed (a bare OSError
    would still skip reseed by propagating, but guard_destructive relies on IsolationError
    to raise DestructionBlocked, and the marker/no-reseed contract is keyed on it)."""
    import errno as _errno
    target = tmp_path / "data.db"
    target.write_text("real user data")

    def fake_rename(self, dst):
        raise OSError(_errno.EXDEV, "Invalid cross-device link")

    def fake_move(src, dst):
        raise OSError(_errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Path, "rename", fake_rename)
    monkeypatch.setattr("core.data_safety.shutil.move", fake_move)
    with pytest.raises(IsolationError):
        isolate_store(target)
    # store must still be on disk (a failed cross-device copy must not destroy the original)
    assert target.exists() and target.read_text() == "real user data"


def test_isolate_deletes_orphan_sidecar_when_it_cannot_relocate(tmp_path, monkeypatch):
    """A sidecar that can't move must be DELETED, not left behind: the primary is
    already gone from `target`, so a leftover -wal beside where reseed writes a fresh
    data.db → foreign-WAL-replay re-corruption (COE run_2d3417d9). Orphan is
    reconstructable → delete it."""
    import errno as _errno
    target = tmp_path / "data.db"
    target.write_text("real user data")
    wal = tmp_path / "data.db-wal"
    wal.write_text("stale wal")
    orig_rename = Path.rename

    def fake_rename(self, dst):
        # primary moves fine; the -wal sidecar hits an un-preservable OSError
        if self == wal:
            raise OSError(_errno.EROFS, "Read-only file system")
        return orig_rename(self, dst)

    monkeypatch.setattr(Path, "rename", fake_rename)
    moved = isolate_store(target)  # must NOT raise (sidecar failure is non-fatal)
    assert moved.exists() and moved.read_text() == "real user data", "primary preserved"
    assert not wal.exists(), "orphan sidecar must be deleted, never left to replay into a fresh db"


# ---------------------------------------------------------------------------
# guard_destructive — the chokepoint
# ---------------------------------------------------------------------------

class _SpyPM:
    """Boundary spy for the permission engine (never mocks guard's own logic)."""
    def __init__(self, decision="approve"):
        self.decision = decision
        self.enqueue_calls = []
        self.stored = []
        self.waited = []

    def store_pending_request(self, req):
        self.stored.append(req)

    async def enqueue_permission_request(self, session_id, req):
        self.enqueue_calls.append((session_id, req))

    async def wait_for_permission_decision(self, request_id, timeout=None):
        self.waited.append(request_id)
        return self.decision

    def remove_pending_request(self, request_id):
        pass


def test_ac3_replaceable_not_gated(tmp_path, monkeypatch):
    """AC3: REPLACEABLE returns immediately; permission engine NOT touched."""
    spy = _SpyPM()
    monkeypatch.setattr("core.data_safety.permission_manager", spy)
    target = tmp_path / "knowledge_fts.db"
    target.write_text("index")
    asyncio.run(guard_destructive(target, "drop", "reindex", session_id="s1"))
    assert target.exists(), "REPLACEABLE target must not be isolated"
    assert spy.enqueue_calls == [], "REPLACEABLE must not enqueue an approval"
    assert spy.waited == [], "REPLACEABLE must not wait for approval"


def test_ac1_irreplaceable_denied_preserves_original(tmp_path, monkeypatch):
    """AC1+AC2: on deny, original is isolated (preserved), not deleted; blocks."""
    spy = _SpyPM(decision="deny")
    monkeypatch.setattr("core.data_safety.permission_manager", spy)
    target = tmp_path / "data.db"
    target.write_text("months of user data")
    with pytest.raises(DestructionBlocked):
        asyncio.run(guard_destructive(target, "purge", "suspected corrupt", session_id="s1"))
    assert not target.exists(), "target isolated (renamed away) before wait"
    isolated = list(tmp_path.glob("data.db.*"))
    assert isolated, "an isolated copy must exist — data preserved"
    assert isolated[0].read_text() == "months of user data"


def test_ac1_irreplaceable_approved_proceeds(tmp_path, monkeypatch):
    """AC1: on approve, guard returns normally (caller then does its destroy)."""
    spy = _SpyPM(decision="approve")
    monkeypatch.setattr("core.data_safety.permission_manager", spy)
    target = tmp_path / "data.db"
    target.write_text("data")
    # should NOT raise
    asyncio.run(guard_destructive(target, "purge", "reason", session_id="s1"))
    assert spy.waited, "approval was awaited"
    assert spy.enqueue_calls and spy.enqueue_calls[0][0] == "s1", "routed to session s1"


def test_ac4_cold_start_no_await(tmp_path, monkeypatch):
    """AC4: cold_start=True never awaits approval — isolate + block, enqueue NOT called."""
    spy = _SpyPM(decision="approve")  # even if approve were reachable, it must NOT be reached
    monkeypatch.setattr("core.data_safety.permission_manager", spy)
    target = tmp_path / "data.db"
    target.write_text("boot-time db")
    with pytest.raises(DestructionBlocked):
        asyncio.run(guard_destructive(target, "purge", "boot corrupt", cold_start=True))
    assert spy.enqueue_calls == [], "cold-start must NOT enqueue (no session to answer)"
    assert spy.waited == [], "cold-start must NOT await approval (would wedge boot)"
    assert not target.exists() and list(tmp_path.glob("data.db.*")), "isolated, not destroyed"


def test_ac7_no_session_degraded_branch(tmp_path, monkeypatch):
    """AC7: session_id=None → degraded branch: isolate + block, never enqueue to None."""
    spy = _SpyPM(decision="approve")
    monkeypatch.setattr("core.data_safety.permission_manager", spy)
    target = tmp_path / "Projects" / "SwarmAI"
    target.mkdir(parents=True)
    (target / "PRODUCT.md").write_text("ddd")
    with pytest.raises(DestructionBlocked):
        asyncio.run(guard_destructive(target, "delete", "http delete", session_id=None))
    assert spy.enqueue_calls == [], "must NOT enqueue to a None/unmonitored queue"
    assert spy.waited == [], "must NOT hang waiting"
    assert not target.exists(), "isolated"


def test_ac7_session_routes_to_that_session(tmp_path, monkeypatch):
    """AC7: a live session_id routes the approval to that session's queue."""
    spy = _SpyPM(decision="approve")
    monkeypatch.setattr("core.data_safety.permission_manager", spy)
    target = tmp_path / ".context" / "MEMORY.md"
    target.parent.mkdir(parents=True)
    target.write_text("memory")
    asyncio.run(guard_destructive(target, "overwrite", "reason", session_id="sessX"))
    assert spy.enqueue_calls[0][0] == "sessX", "approval routed to the initiating session"


# ---------------------------------------------------------------------------
# is_corruption_error — AC5: tighten the corruption verdict (not every
# DatabaseError == whole-store corruption)
# ---------------------------------------------------------------------------

def test_ac5_operational_error_is_not_corruption():
    """A locked/busy db (OperationalError) must NOT be judged corruption —
    it is the exact false-positive that destroyed a valid db in the COE."""
    assert is_corruption_error(sqlite3.OperationalError("database is locked")) is False


def test_ac5_true_corruption_signatures_are_corruption():
    """Only genuine malformed/not-a-db signatures count as corruption."""
    assert is_corruption_error(sqlite3.DatabaseError("database disk image is malformed")) is True
    assert is_corruption_error(sqlite3.DatabaseError("file is not a database")) is True
    assert is_corruption_error(sqlite3.DatabaseError("database or disk is full")) is False, (
        "a full disk is not corruption — destroying the db would be catastrophic"
    )


def test_ac5_unknown_databaseerror_is_failclosed_not_corruption():
    """Fail-CLOSED for the DESTRUCTIVE action: an unrecognized DatabaseError is
    NOT auto-classified as corruption (the heaviest action never fires on the
    weakest judge — STEERING #20). It propagates as a bounded restart instead."""
    assert is_corruption_error(sqlite3.DatabaseError("some novel sqlite message")) is False


# ---------------------------------------------------------------------------
# recovery marker — makes B (reseed-fresh) NON-SILENT: the pending decision is
# surfaced to the user at the next session open (STEERING #20 "reach the human")
# ---------------------------------------------------------------------------

def test_recovery_marker_roundtrip(tmp_path):
    isolated = tmp_path / "data.db.corrupt-20260815-000000-abc123"
    isolated.write_text("preserved")  # must exist to be a live pending entry
    write_recovery_marker(tmp_path, isolated_path=isolated, reason="malformed at boot")
    m = read_recovery_marker(tmp_path)
    assert m is not None
    assert m["isolated_path"] == str(isolated)
    assert "malformed" in m["reason"]
    assert m.get("isolated_at")
    assert m.get("pending") and m["pending"][-1]["isolated_path"] == str(isolated)


def test_recovery_marker_accrues_multiple_pending(tmp_path):
    """Adversarial MED (run_a456640f): a 2nd corruption must NOT clobber the 1st
    still-pending isolate — both accrue so neither isolated store is orphaned."""
    iso1 = tmp_path / "data.db.corrupt-A"
    iso2 = tmp_path / "data.db.corrupt-B"
    iso1.write_text("first preserved")
    iso2.write_text("second preserved")
    write_recovery_marker(tmp_path, isolated_path=iso1, reason="first corruption")
    write_recovery_marker(tmp_path, isolated_path=iso2, reason="second corruption")
    m = read_recovery_marker(tmp_path)
    paths = {e["isolated_path"] for e in m["pending"]}
    assert str(iso1) in paths and str(iso2) in paths, "both pending isolates retained"


def test_recovery_marker_prunes_resolved_on_write(tmp_path):
    """A prior isolate whose file is GONE (resolved) is pruned when a new one writes."""
    iso1 = tmp_path / "data.db.corrupt-A"  # never created → resolved
    iso2 = tmp_path / "data.db.corrupt-B"
    iso2.write_text("live")
    write_recovery_marker(tmp_path, isolated_path=iso1, reason="first")
    write_recovery_marker(tmp_path, isolated_path=iso2, reason="second")
    m = read_recovery_marker(tmp_path)
    paths = {e["isolated_path"] for e in m["pending"]}
    assert str(iso1) not in paths, "resolved prior isolate pruned"
    assert str(iso2) in paths


def test_recovery_marker_absent_returns_none(tmp_path):
    assert read_recovery_marker(tmp_path) is None


def test_recovery_marker_clear(tmp_path):
    write_recovery_marker(tmp_path, isolated_path=tmp_path / "x", reason="r")
    assert read_recovery_marker(tmp_path) is not None
    clear_recovery_marker(tmp_path)
    assert read_recovery_marker(tmp_path) is None


def test_recovery_marker_corrupt_json_is_failsoft(tmp_path):
    """A garbage marker file must not crash the reader (fail-soft → None)."""
    (tmp_path / ".db-recovery-pending.json").write_text("{not json")
    assert read_recovery_marker(tmp_path) is None
