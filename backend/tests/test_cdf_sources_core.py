"""CDF stage-B source and governed-editor integration tests.

Tests source-only enqueue boundaries with monkeypatched ingestion APIs, the
append-only provenance migration/taint fallback, optimistic editor conflicts,
and a representative legacy mutator's diagnostics-only switch.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytest
from fastapi import HTTPException


def _provenance_schema(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE session_inputs (
            event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, kind TEXT NOT NULL,
            trust TEXT NOT NULL, content_hash TEXT NOT NULL, ref TEXT, root_id TEXT,
            created_at TEXT NOT NULL)""")


@pytest.mark.asyncio
async def test_v17_migration_adds_session_input_root_id_and_measured_trust(tmp_path):
    from database.sqlite import SQLiteDatabase

    db_path = tmp_path / "data.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version = 16")
        conn.execute("""CREATE TABLE session_inputs (
            event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, kind TEXT NOT NULL,
            trust TEXT NOT NULL CHECK (trust IN ('user','external','agent')),
            content_hash TEXT NOT NULL, ref TEXT, created_at TEXT NOT NULL)""")
    database = SQLiteDatabase(str(db_path))
    async with aiosqlite.connect(db_path) as conn:
        await database._run_versioned_migrations(conn, 16)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 17
        columns = {row[1] for row in conn.execute("PRAGMA table_info(session_inputs)")}
        assert columns == {"event_id", "session_id", "kind", "trust", "content_hash",
                           "ref", "root_id", "created_at"}
        conn.execute(
            "INSERT INTO session_inputs VALUES('e','s','measured_field','measured','h',NULL,NULL,'t')")


def test_session_input_registers_roots_and_deprecates_envelope(monkeypatch, tmp_path):
    from core import session_inputs

    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    db_path = tmp_path / "data.db"
    _provenance_schema(db_path)
    monkeypatch.setattr(session_inputs, "_db_path", lambda: db_path)
    user = session_inputs.record_session_input("s1", "message", "user", "my correction")
    tool = session_inputs.record_session_input(
        "s1", "tool_result", "external", '{"exit_code":0,"text":"output"}')

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = list(conn.execute("SELECT * FROM session_inputs ORDER BY created_at,event_id"))
    assert user in {row["event_id"] for row in rows}
    assert tool in {row["event_id"] for row in rows}
    assert all(row["root_id"] for row in rows)
    assert {row["trust"] for row in rows} == {"user", "external", "measured"}
    with pytest.raises(DeprecationWarning):
        session_inputs.derive_envelope("s1", (user,))


@pytest.mark.asyncio
async def test_backflow_enqueues_stable_message_slot_and_never_writes(monkeypatch):
    from core.ingestion import api
    from hooks import knowledge_backflow_hook as module

    text = "## Analysis\n" + ("word " * 510) + "\n## Findings\nroot cause and trade-off"
    monkeypatch.setattr(module.db.messages, "list_by_session_paginated", lambda *a, **k: None)

    async def messages(*args, **kwargs):
        return [{"id": "message-7", "role": "assistant", "content": text}]

    monkeypatch.setattr(module.db.messages, "list_by_session_paginated", messages)
    monkeypatch.setattr(module, "derive_envelope", None, raising=False)
    calls = []
    monkeypatch.setattr(api, "enqueue", lambda *a, **k: calls.append((a, k)) or SimpleNamespace(status="queued"))
    monkeypatch.setattr("core.session_inputs.derive_envelope", lambda *a, **k: {
        "source_event_ids": [], "evidence": "inferred", "tainted": True, "writer": "backflow"})

    await module.KnowledgeBackflowHook()._execute_inner(
        module.HookContext("s1", "a", 1, "", "title"))
    args, kwargs = calls[0]
    assert args[:3] == ("backflow", "message-7", "document")
    assert args[3]["kind"] == "backflow" and args[3]["message_id"] == "message-7"
    assert kwargs["envelope"]["tainted"] is True


@pytest.mark.asyncio
async def test_governed_put_requires_expected_revision(monkeypatch, tmp_path):
    from core.ingestion import api
    from routers import workspace_api

    target = tmp_path / "MEMORY.md"
    target.write_text("old")
    monkeypatch.setattr(workspace_api, "_get_workspace_path", lambda: _async_value(str(tmp_path)))
    monkeypatch.setattr(workspace_api, "_resolve_file_path", lambda *a, **k: (target, False))
    monkeypatch.setattr(api, "is_governed", lambda path: True)

    with pytest.raises(HTTPException) as missing:
        await workspace_api.put_workspace_file(path="MEMORY.md", body={"content": "new"})
    assert missing.value.status_code == 409

    monkeypatch.setattr(api, "apply_user_edit", lambda *a, **k: (_ for _ in ()).throw(api.StaleRevision()))
    with pytest.raises(HTTPException) as stale:
        await workspace_api.put_workspace_file(
            path="MEMORY.md", body={"content": "new", "expected_revision": "old"})
    assert stale.value.status_code == 409 and target.read_text() == "old"


@pytest.mark.asyncio
async def test_directory_delete_rejects_governed_descendant(monkeypatch, tmp_path):
    from core.ingestion import api
    from routers import workspace_api

    tree = tmp_path / "custom"
    tree.mkdir()
    governed = tree / "note.md"
    governed.write_text("keep")
    monkeypatch.setattr(workspace_api, "_get_workspace_path", lambda: _async_value(str(tmp_path)))
    monkeypatch.setattr(api, "is_governed", lambda path: path == governed)
    with pytest.raises(HTTPException) as blocked:
        await workspace_api.delete_folder(workspace_api.FolderDeleteRequest(path="custom"))
    assert blocked.value.status_code == 409 and governed.exists()


@pytest.mark.asyncio
async def _async_value(value):
    return value


def test_memory_health_is_read_only(monkeypatch, tmp_path):
    from jobs.handlers import memory_health

    memory = tmp_path / "MEMORY.md"
    evolution = tmp_path / "EVOLUTION.md"
    memory.write_text("## Principles\nkeep\n")
    evolution.write_text("## Corrections Captured\nkeep\n")
    monkeypatch.setattr(memory_health, "CONTEXT_DIR", tmp_path)
    monkeypatch.setattr(memory_health, "DAILY_DIR", tmp_path / "DailyActivity")
    monkeypatch.setattr(memory_health, "SWARMWS", tmp_path)
    monkeypatch.setattr(memory_health, "_run_integrity_checks", lambda text: [])
    monkeypatch.setattr(memory_health, "_call_llm", lambda prompt: {"stale_memories": [{"entry_prefix": "keep"}]})
    for gone in ("_apply_report", "_resolve_open_thread", "_remove_memory_entry",
                 "_remove_evolution_entry"):
        assert not hasattr(memory_health, gone), gone   # the legacy mutators are deleted
    monkeypatch.setattr(memory_health, "_write_summary_to_daily", lambda *a: None)
    monkeypatch.setattr(memory_health, "_update_health_findings", lambda *a: None)

    result = memory_health.run_memory_health(dry_run=False)
    assert result["actions"] == ["Would review 1 stale memories"]
    assert memory.read_text() == "## Principles\nkeep\n"
    assert evolution.read_text() == "## Corrections Captured\nkeep\n"



