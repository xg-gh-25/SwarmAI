"""Inbox store (schema v20) and privacy.

- v20 creates every ``inbox_*`` table on a v19 database and lands at 20; a second run
  changes nothing.
- One message is one row: ``(source_id, external_id)`` is unique.
- At most one reserved / unknown send per item, and one send per source message id
  (the partial unique indexes).
- Privacy: no ``inbox_*`` table is in ``backup_manager.L2_TABLES`` (the explicit list a
  backup and git sync export); the Inbox modules write no file and call no recall,
  Library, knowledge or CDF module.
"""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from core.inbox import store
from core.inbox.models import Lane
from database.sqlite import CURRENT_SCHEMA_VERSION, SQLiteDatabase, close_all_pools
from tests import inbox_fakes as fx

BACKEND = Path(__file__).resolve().parents[1]
INBOX_MODULES = [BACKEND / "core" / "inbox" / f"{m}.py"
                 for m in ("store", "runner", "send", "caller", "submit")] + [BACKEND / "routers" / "inbox.py"]


def _tables(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")}
    finally:
        conn.close()


def _version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


async def test_v20_migration_creates_tables_and_is_idempotent(tmp_path):
    path = tmp_path / "data.db"
    await SQLiteDatabase(db_path=str(path)).initialize()
    await close_all_pools()
    assert _version(path) == CURRENT_SCHEMA_VERSION
    # back to a v19 database: the Inbox tables are gone
    conn = sqlite3.connect(path)
    for t in fx.INBOX_TABLES:
        conn.execute(f"DROP TABLE {t}")
    conn.execute("PRAGMA user_version = 19")
    conn.commit()
    conn.close()
    await SQLiteDatabase(db_path=str(path)).initialize()
    await close_all_pools()
    names = _tables(path)
    assert set(fx.INBOX_TABLES) <= names
    assert {"ux_inbox_sends_open", "ux_inbox_sends_msg"} <= names
    assert _version(path) == CURRENT_SCHEMA_VERSION
    before = sqlite3.connect(path).execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    await SQLiteDatabase(db_path=str(path)).initialize()
    await close_all_pools()
    after = sqlite3.connect(path).execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    assert before == after and _version(path) == CURRENT_SCHEMA_VERSION


async def test_one_message_is_one_row():
    from database import db
    await fx.clear()
    await fx.add_source()
    first = await db.inbox_insert_items([store.message_to_item_row("s1", fx.msg("e1"), Lane.REPLY, None)])
    again = await db.inbox_insert_items([store.message_to_item_row("s1", fx.msg("e1"), Lane.REPLY, None)])
    assert first["e1"][1] is True and again["e1"] == (first["e1"][0], False)
    assert len(await db.inbox_list_items("reply")) == 1


def _send(item_id: str, sid: str, status: str, msg_id=None) -> dict:
    stamp = store.ts(store.now())
    return {"id": store.new_id("snd"), "item_id": item_id, "source_id": "s1", "draft_id": None,
            "origin": "click", "target": "{}", "target_sha": "x", "text": "hi", "status": status,
            "external_msg_id": msg_id, "url": "", "error": "", "created_at": stamp,
            "sent_at": None, "updated_at": stamp}


async def test_at_most_one_open_send_per_item_and_one_row_per_message_id():
    from database import db
    await fx.clear()
    await fx.add_source()
    item = await fx.add_item()
    assert await db.inbox_insert_send(_send(item["id"], "s1", "reserved"))
    assert not await db.inbox_insert_send(_send(item["id"], "s1", "reserved"))
    assert not await db.inbox_insert_send(_send(item["id"], "s1", "unknown"))
    assert await db.inbox_insert_send(_send(item["id"], "s1", "failed"))  # failed rows may repeat
    assert await db.inbox_insert_send(_send(item["id"], "s1", "sent", msg_id="m1"))
    assert not await db.inbox_insert_send(_send(item["id"], "s1", "sent", msg_id="m1"))
    assert len(await db.inbox_sends(("reserved", "unknown"))) == 1


def test_inbox_tables_are_never_backed_up():
    from core.backup_manager import L2_TABLES
    leaked = [t for t in L2_TABLES if t.startswith("inbox")]
    assert leaked == [], f"the person's messages must never be in a backup: {leaked}"
    # and the list the v20 DDL creates is exactly the Inbox's own tables
    from database.sqlite import _INBOX_DDL
    created = {s.split("EXISTS", 1)[1].split("(", 1)[0].strip()
               for s in _INBOX_DDL if s.lstrip().startswith("CREATE TABLE")}
    assert created == set(fx.INBOX_TABLES)
    assert not created & set(L2_TABLES)


_FORBIDDEN_IMPORTS = ("recall", "library", "knowledge", "memory_index", "ingestion", "cdf",
                      "distillation", "backup")


def test_inbox_code_writes_no_file_and_feeds_no_index():
    for path in INBOX_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                for name in names:
                    assert not any(w in name.lower() for w in _FORBIDDEN_IMPORTS), \
                        f"{path.name} imports {name}: Inbox items never reach a knowledge or recall store"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in ("write_text", "write_bytes", "mkdir", "touch"), \
                    f"{path.name} writes a file"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                mode = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else "r"
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = kw.value.value
                assert not set(str(mode)) & set("wax+"), f"{path.name} opens a file for writing"


@pytest.mark.parametrize("kind,label", [("slack", "Slack"), ("mail", "邮件"), ("github", "GitHub"),
                                        ("feed:rss", "信息流"), ("teams", "teams")])
def test_channel_label(kind, label):
    assert store.channel_label(kind) == label


def test_inbox_code_reaches_the_database_only_through_its_methods():
    """Inbox SQL lives in database/sqlite.py (db.inbox_*): no Inbox module borrows the
    database's raw connection pool to run its own statements."""
    for path in sorted((BACKEND / "core" / "inbox").rglob("*.py")) + [BACKEND / "routers" / "inbox.py"]:
        text = path.read_text(encoding="utf-8")
        assert "_get_pool" not in text, f"{path.relative_to(BACKEND)} borrows the database pool"


async def test_delete_source_removes_its_rows_and_waits_for_a_send():
    from database import db
    await fx.clear()
    await fx.add_source()
    item = (await db.inbox_insert_items([store.message_to_item_row("s1", fx.msg("e1"), Lane.REPLY, None)]))["e1"][0]
    assert await store.delete_source_rows("nope") is None
    reserved = _send(item, "x", "reserved")
    assert await db.inbox_insert_send(reserved)
    with pytest.raises(store.SendInFlight):
        await store.delete_source_rows("s1")
    assert await db.inbox_get_source("s1") is not None, "a send in flight keeps the source"
    await store.purge_send(reserved["id"])
    assert await db.inbox_get_send(reserved["id"]) is None
    assert await store.delete_source_rows("s1") == {"items": 1, "drafts": 0, "sends": 0}
    assert await db.inbox_get_source("s1") is None and await db.inbox_get_item(item) is None
