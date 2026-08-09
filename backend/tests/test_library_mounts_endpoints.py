"""Endpoint-level tests for GET/POST /api/library/mounts (run_a1f4c2d8).

WHY THIS FILE EXISTS
--------------------
Both endpoints were restructured to run their blocking work off the event loop: a
synchronous sqlite3 session (connect / ensure_table DDL / query / close) plus, for
register_mount, judge_mount_kind's rglob walk AND index_code_mount's tree-sitter graph
build — all of which previously executed directly in the ``async def`` body, freezing
every other request and every chat tab's SSE stream for the duration.

The existing test_library_mounts.py covers the ``LibraryMounts`` STORE class only; it
never calls the router functions. So the restructure had zero behavioural coverage —
`test_router_async_blocking.py` would prove the blocking call MOVED, but nothing proved
the endpoints still WORK. This file closes that: it invokes the real coroutines.

Load-bearing property beyond "it still returns rows": the two rejection guards
(non-directory, protected system path) must fire in the ASYNC body, BEFORE any work is
dispatched to a thread. That was a deliberate design choice — fail fast on a path we are
not allowed to touch, rather than pay a thread hop first — and it is a SECURITY guard
(the exfiltration check), so a refactor must not be free to slide it into the worker.

NOTE — every argument is passed EXPLICITLY. Calling a FastAPI handler as a plain
coroutine bypasses dependency resolution, so an omitted parameter arrives as the
``Query(default=...)`` OBJECT rather than its default value. sqlite then rejects it
("type 'Query' is not supported") and the endpoint's own degrade-path swallows that into
an empty result — a failure that reads like a product bug but is a test-authoring trap.

Methodology: monkeypatch ``jobs.paths.DB_PATH`` to a tmp file so the endpoints build a
real on-disk registry without touching the developer's DB, and drive the coroutines with
asyncio.run.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from routers import library_api


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the endpoints' DB_PATH at a tmp file (they import it lazily, inside the
    worker, so patching the module attribute is what they will read)."""
    db = tmp_path / "registry.db"
    import jobs.paths
    monkeypatch.setattr(jobs.paths, "DB_PATH", db, raising=True)
    return db


def test_list_mounts_reports_not_ready_when_db_absent(tmp_db: Path) -> None:
    """No DB yet (fresh install) → empty list + registry_ready False, never a 500."""
    assert not tmp_db.exists()
    out = asyncio.run(library_api.list_mounts(scope=None))
    assert out == {"count": 0, "mounts": [], "registry_ready": False}


def test_list_mounts_returns_rows_written_by_the_store(tmp_db: Path) -> None:
    """The off-loop sqlite session still reads real rows (the restructure is behaviour-
    preserving, not just syntactically off the loop)."""
    from core.library_mounts import LibraryMounts

    conn = sqlite3.connect(str(tmp_db))
    conn.row_factory = sqlite3.Row
    store = LibraryMounts(conn)
    store.ensure_table()
    store.add_mount(scope="GLOBAL", path="/tmp/alpha", kind="docs", briefing="alpha dir")
    conn.commit()
    conn.close()

    out = asyncio.run(library_api.list_mounts(scope=None))
    assert out["registry_ready"] is True
    assert out["count"] == 1
    row = out["mounts"][0]
    assert row["path"] == "/tmp/alpha"
    assert row["kind"] == "docs"
    assert row["enabled"] is True          # coerced to a real bool for the overlay
    assert row["briefing"] == "alpha dir"
    assert set(row) == {"id", "path", "kind", "health", "enabled",
                        "last_synced", "briefing"}


def test_list_mounts_scope_filter_is_passed_through(tmp_db: Path) -> None:
    from core.library_mounts import LibraryMounts

    conn = sqlite3.connect(str(tmp_db))
    conn.row_factory = sqlite3.Row
    store = LibraryMounts(conn)
    store.ensure_table()
    store.add_mount(scope="GLOBAL", path="/tmp/g", kind="docs")
    store.add_mount(scope="SwarmAI", path="/tmp/s", kind="docs")
    conn.commit()
    conn.close()

    assert asyncio.run(library_api.list_mounts(scope=None))["count"] == 2          # default: all
    scoped = asyncio.run(library_api.list_mounts(scope="SwarmAI"))
    assert scoped["count"] == 1 and scoped["mounts"][0]["path"] == "/tmp/s"


def test_list_mounts_degrades_instead_of_500(tmp_db: Path, monkeypatch) -> None:
    """A DB failure inside the worker still yields the overlay's empty shape. The
    try/except must wrap the AWAIT, not live inside the old inline body."""
    tmp_db.write_text("not a database", encoding="utf-8")
    out = asyncio.run(library_api.list_mounts(scope=None))
    assert out == {"count": 0, "mounts": [], "registry_ready": False}


def test_register_mount_rejects_a_non_directory(tmp_path: Path, tmp_db: Path) -> None:
    f = tmp_path / "single.md"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(library_api.register_mount(path=str(f), scope="GLOBAL"))
    assert ei.value.status_code == 400
    assert "not a directory" in str(ei.value.detail)


def test_register_mount_rejects_a_protected_system_path(tmp_path: Path, tmp_db: Path,
                                                       monkeypatch) -> None:
    """The exfiltration guard still blocks — and with a 400, not a 500."""
    d = tmp_path / "looks_ok"
    d.mkdir()
    import core.library_mounts as lm
    monkeypatch.setattr(lm, "is_protected_system_path", lambda p: True, raising=True)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(library_api.register_mount(path=str(d), scope="GLOBAL"))
    assert ei.value.status_code == 400
    assert "protected system path" in str(ei.value.detail)


def test_rejection_guards_run_BEFORE_any_thread_work(tmp_path: Path, tmp_db: Path,
                                                     monkeypatch) -> None:
    """Both 400 guards must short-circuit in the async body — no thread hop, and above
    all no judge_mount_kind walk / DB write on a path we are not allowed to touch.

    Asserted by making the worker's dependencies EXPLODE if reached: if a guard ever
    slides into _work(), these raise instead of returning a clean 400.
    """
    import core.library_mounts as lm

    def _boom(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("worker ran despite a rejection guard")

    monkeypatch.setattr(lm, "judge_mount_kind", _boom, raising=True)

    # (a) non-directory
    f = tmp_path / "f.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(library_api.register_mount(path=str(f), scope="GLOBAL"))
    assert ei.value.status_code == 400

    # (b) protected path
    d = tmp_path / "d"
    d.mkdir()
    monkeypatch.setattr(lm, "is_protected_system_path", lambda p: True, raising=True)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(library_api.register_mount(path=str(d), scope="GLOBAL"))
    assert ei.value.status_code == 400


def test_register_mount_registers_a_docs_dir_and_hands_off_to_chat(
    tmp_path: Path, tmp_db: Path, monkeypatch,
) -> None:
    """Happy path through the worker: judge → sqlite write → docs handoff payload."""
    import core.library_mounts as lm

    d = tmp_path / "notes"
    d.mkdir()
    (d / "a.md").write_text("hi", encoding="utf-8")
    monkeypatch.setattr(lm, "is_protected_system_path", lambda p: False, raising=True)
    monkeypatch.setattr(lm, "judge_mount_kind", lambda p: "docs", raising=True)

    out = asyncio.run(library_api.register_mount(path=str(d), scope="GLOBAL"))
    assert out["kind"] == "docs"
    assert out["status"] == "registered"
    assert "brief the docs dir" in out["next"]      # semantic work handed to chat
    assert out["id"]

    # …and it is durably in the registry the GET endpoint reads.
    listed = asyncio.run(library_api.list_mounts(scope=None))
    assert listed["count"] == 1
    assert listed["mounts"][0]["path"] == str(d)


def test_register_mount_code_dir_returns_the_index_result(
    tmp_path: Path, tmp_db: Path, monkeypatch,
) -> None:
    """kind=code runs index_code_mount IN THE WORKER (the heaviest blocking call that
    this run moved off the loop) and surfaces its symbol count."""
    import core.library_mounts as lm

    d = tmp_path / "src"
    d.mkdir()
    monkeypatch.setattr(lm, "is_protected_system_path", lambda p: False, raising=True)
    monkeypatch.setattr(lm, "judge_mount_kind", lambda p: "code", raising=True)
    monkeypatch.setattr(lm, "index_code_mount",
                        lambda store, mid: {"status": "indexed", "symbols": 42},
                        raising=True)

    out = asyncio.run(library_api.register_mount(path=str(d), scope="GLOBAL"))
    assert out["kind"] == "code"
    assert out["status"] == "indexed"
    assert out["symbols"] == 42
