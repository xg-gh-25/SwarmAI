"""Schema v15: transcript revisions, kept by triggers.

- Every transcript write bumps the session's rev in its own transaction: a
  message inserted, re-put, changed (content / sent / metadata), deleted; a
  compaction inserted or its summary set. A write that touches nothing the
  transcript shows (expires_at) does not.
- ``transcript_since(rev)`` returns exactly the rows changed after ``rev`` (with
  their rev), leaves pending rows out, and says ``reset`` once a row was deleted
  after ``rev``.
- The rev triggers do not re-run the FTS index (search still finds the row once).
- A v14 database migrates to v15, idempotently.
"""
from __future__ import annotations


import pytest

from core.compact_summary import build_record


async def _put(db, sid, mid, text, role="user", **extra):
    await db.messages.put({"id": mid, "session_id": sid, "role": role,
                           "content": [{"type": "text", "text": text}], **extra})


@pytest.mark.asyncio
async def test_every_transcript_write_bumps_the_session_rev():
    from database import db
    sid = "s-rev-1"
    assert await db.transcript_rev(sid) == 0
    await _put(db, sid, "r1", "one")
    await _put(db, sid, "r2", "two", role="assistant")
    assert await db.transcript_rev(sid) == 2
    await _put(db, sid, "r1", "one, edited")                       # re-put
    assert await db.transcript_rev(sid) == 3
    await db.messages.update_permission_decision(sid, "nope", "approve")   # matches nothing
    assert await db.transcript_rev(sid) == 3
    await db.insert_compaction(sid, build_record({"trigger": "auto"}, "sum"))
    assert await db.transcript_rev(sid) == 4
    await db.messages.delete("r2")
    assert await db.transcript_rev(sid) == 5
    assert await db.transcript_rev("s-rev-other") == 0


@pytest.mark.asyncio
async def test_transcript_since_returns_what_changed_after_a_rev():
    from database import db
    sid = "s-rev-2"
    await _put(db, sid, "a1", "first")
    r = await db.transcript_rev(sid)
    await _put(db, sid, "a2", "second")
    await _put(db, sid, "a3", "queued", sent=0, pending_seq=1)      # pending: not shown
    await db.insert_compaction(sid, {**build_record({"trigger": "auto"}, "s"), "id": "c1"})
    out = await db.transcript_since(sid, r)
    assert [(rv, m["id"]) for rv, m in out["messages"]] == [(r + 1, "a2")]
    assert [c["id"] for _, c in out["compactions"]] == ["c1"]
    assert out["rev"] == r + 3 and out["reset"] is False
    assert out["messages"][0][1]["content"] == [{"type": "text", "text": "second"}]
    await db.messages.delete("a1")
    assert (await db.transcript_since(sid, r))["reset"] is True
    assert (await db.transcript_since(sid, out["rev"] + 1))["reset"] is False


@pytest.mark.asyncio
async def test_rev_triggers_leave_search_alone():
    import aiosqlite
    from database import db
    sid = "s-rev-3"
    await _put(db, sid, "f1", "zebracorn appears once")
    async with aiosqlite.connect(str(db.db_path)) as conn:
        n = (await (await conn.execute(
            "SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'zebracorn'")).fetchone())[0]
    assert n == 1


@pytest.mark.asyncio
async def test_v14_database_migrates_to_v15(tmp_path):
    import aiosqlite
    from database.sqlite import SQLiteDatabase
    path = str(tmp_path / "m.db")
    d = SQLiteDatabase(path)
    await d.initialize()
    async with aiosqlite.connect(path) as conn:
        for t in ("messages_rev_insert", "messages_rev_update", "messages_rev_delete",
                  "compactions_rev_insert", "compactions_rev_update", "compactions_rev_delete"):
            await conn.execute(f"DROP TRIGGER {t}")
        await conn.execute("DROP TABLE session_revs")
        await conn.execute("DROP TABLE transcript_revs")
        await conn.execute("PRAGMA user_version = 14")
        await conn.commit()
        await d._run_versioned_migrations(conn, 14)
        await d._run_versioned_migrations(conn, 14)
        assert (await (await conn.execute("PRAGMA user_version")).fetchone())[0] == 17
        names = {r[0] for r in await (await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE '%rev%'")).fetchall()}
    assert len(names) == 6
