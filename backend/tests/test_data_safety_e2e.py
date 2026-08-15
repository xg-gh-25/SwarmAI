"""Layer-4 Cross-Boundary E2E — the boot-recovery → attention seam (run_a456640f).

EVALUATE set cross_boundary=true (kinds: multi-subsystem shared path). The live
seam this run introduces is NOT the guard_destructive↔PermissionManager path
(deliberately unwired — the destroy sinks are sessionless boot/HTTP paths that
cannot await), but the RECOVERY MARKER CONTRACT between two subsystems:

    BOOT subsystem (main._init_db_bounded, on real DB corruption)
        --writes--> .db-recovery-pending.json marker
    ATTENTION subsystem (attention_authority.collect)
        --reads--> surfaces a BLOCKING "recover-vs-discard" item to the user

Layers 1-3 exercise each side alone. THIS layer drives the REAL wiring end-to-end
across the subsystem boundary, with NO mock of the thing-under-change, plus a
mutation proof (revert the marker write → the item vanishes → RED).
"""
import asyncio
import sqlite3
import sys

import pytest


def _write_garbage_db(path):
    path.write_bytes(b"this is not a sqlite database, it is garbage\n" * 4)


@pytest.fixture
def _seed(tmp_path):
    import shutil
    import main
    real_seed = main._get_seed_database_path()
    if not real_seed or not real_seed.exists():
        pytest.skip("real seed.db not available in this environment")
    seed = tmp_path / "seed.db"
    shutil.copy2(real_seed, seed)
    return seed


def test_boot_corruption_surfaces_in_attention_end_to_end(tmp_path, _seed, monkeypatch):
    """REAL boot corruption → REAL attention surfacing, across the subsystem seam.

    Drives the actual wiring (no mock of main's recovery or of the attention
    collector). Mutation proof at the end: without the marker write, the BLOCKING
    item does NOT appear → confirms the test is non-vacuous.
    """
    import database
    from config import settings
    import core.attention_authority as aa
    from core.data_safety import read_recovery_marker

    app_data = tmp_path / ".swarm-ai"
    app_data.mkdir(parents=True)
    user_db = app_data / "data.db"

    # Point BOTH subsystems at the same temp app-data dir (the shared seam).
    monkeypatch.setattr("main.get_app_data_dir", lambda: app_data)
    monkeypatch.setattr("main._get_seed_database_path", lambda: _seed)
    monkeypatch.setattr(settings, "sqlite_db_path", str(user_db))
    monkeypatch.setattr(aa, "get_app_data_dir", lambda: app_data)
    database._db_instance = None

    # --- BOOT side (REAL): a genuinely corrupt db drives the recovery path ---
    _write_garbage_db(user_db)
    from main import _init_db_bounded
    asyncio.run(_init_db_bounded(skip_schema=True))  # real isolate+reseed+mark

    # marker written + isolated file preserved (pending decision)
    assert read_recovery_marker(app_data) is not None
    isolated = list(app_data.glob("data.db.corrupt-*"))
    assert isolated, "corrupt db preserved (isolated), not destroyed"

    # --- ATTENTION side (REAL): collect() must surface it as BLOCKING ---
    result = aa.collect(tmp_path)  # real aggregator, real _collect_db_recovery leg
    db_items = [it for it in result.items if it.source == "db_recovery"]
    assert len(db_items) == 1, "boot recovery must surface across the seam into attention"
    assert db_items[0].tier == aa.TIER_BLOCKING
    assert "recover" in db_items[0].dispatch["message"].lower()
    assert result.counts[aa.TIER_BLOCKING] >= 1

    # --- MUTATION PROOF (non-vacuity): remove the marker (simulate reverting the
    # main.py write side) → the attention item MUST vanish. If it stayed, the test
    # would be asserting on something other than the real seam. ---
    from core.data_safety import clear_recovery_marker
    clear_recovery_marker(app_data)
    result_after = aa.collect(tmp_path)
    assert [it for it in result_after.items if it.source == "db_recovery"] == [], (
        "without the marker (reverted write side) the item must disappear — proves "
        "the E2E asserts the real boot→attention contract, not a fixture"
    )
