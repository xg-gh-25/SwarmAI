"""Needs You: an Inbox source whose sign-in expired is one blocking item (design §9,
§11 #11). A healthy, a merely erroring or a disabled source gives none; a database older
than v20 (no inbox_sources table) is silent."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from config import get_app_data_dir
from core import attention_authority as aa
from database.sqlite import SQLiteDatabase, close_all_pools


async def _db() -> Path:
    path = Path(get_app_data_dir()) / "data.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    await SQLiteDatabase(db_path=str(path)).initialize()
    await close_all_pools()
    return path


def _source(path: Path, sid: str, kind: str, health: str, fix: str = "", enabled: int = 1) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO inbox_sources (id, kind, backend, category, config, enabled, interval_s, "
        "health, fix, last_error, created_at, updated_at) VALUES (?, ?, 'x', 'conversation', "
        "'{}', ?, 900, ?, ?, 'SourceAuthError: token expired', 't', 't')",
        (sid, kind, enabled, health, fix))
    conn.commit()
    conn.close()


async def test_auth_failed_source_is_one_blocking_item():
    path = await _db()
    _source(path, "mail-1", "mail", "auth_failed", "run mwinit -f")
    _source(path, "slack-1", "slack", "ok")
    _source(path, "gh-1", "github", "error")
    _source(path, "gh-2", "github", "auth_failed", "gh auth login", enabled=0)
    items = aa._collect_inbox_sources()
    assert [(i.id, i.source, i.tier, i.brain) for i in items] == [
        ("inbox:mail-1", "inbox", aa.TIER_BLOCKING, None)]
    assert "Mail" in items[0].title and "run mwinit -f" in items[0].title
    assert "run mwinit -f" in items[0].dispatch["message"]
    assert "inbox:mail-1" in [i.id for i in aa.collect(Path(get_app_data_dir())).items]


async def test_old_database_is_silent():
    path = await _db()
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE inbox_sources")
    conn.commit()
    conn.close()
    assert aa._collect_inbox_sources() == []
