"""Tests for core/message_attachments.py -- attachment bytes in files, refs in rows.

Properties checked:
- externalize moves base64 data to a content-addressed file and leaves a ref;
- it never mutates its input and leaves non-attachment content untouched;
- rehydrate restores the exact original block (round trip);
- a missing file becomes a text block, never an invalid image block;
- SQLiteMessagesTable.put stores the ref, not the base64;
- a pending message with an image is re-delivered with its bytes intact.
"""

import base64
import json
import sqlite3

import pytest

from core.message_attachments import (
    REF_TYPE, attachments_dir, externalize_attachments, rehydrate_attachments)

_RAW = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
_B64 = base64.b64encode(_RAW).decode()
_IMG = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _B64}}
_TXT = {"type": "text", "text": "看这张图"}


def test_externalize_writes_file_and_ref(tmp_path):
    original = [_TXT, _IMG]
    snapshot = json.dumps(original)
    out = externalize_attachments(original, tmp_path)
    assert json.dumps(original) == snapshot  # input not mutated
    assert out[0] == _TXT
    src = out[1]["source"]
    assert src["type"] == REF_TYPE and src["media_type"] == "image/png"
    assert src["bytes"] == len(_RAW) and "data" not in src
    assert (tmp_path / src["sha256"]).read_bytes() == _RAW


def test_same_bytes_stored_once(tmp_path):
    externalize_attachments([_IMG], tmp_path)
    externalize_attachments([_IMG], tmp_path)
    assert len([p for p in tmp_path.iterdir() if not p.name.startswith(".")]) == 1


def test_round_trip(tmp_path):
    out = externalize_attachments([_TXT, _IMG], tmp_path)
    assert rehydrate_attachments(out, tmp_path) == [_TXT, _IMG]


def test_json_string_content(tmp_path):
    out = externalize_attachments(json.dumps([_IMG]), tmp_path)
    assert isinstance(out, str) and _B64 not in out


def test_untouched_when_nothing_to_move(tmp_path):
    content = [_TXT]
    assert externalize_attachments(content, tmp_path) is content
    assert externalize_attachments("plain", tmp_path) == "plain"
    bad = [{"type": "image", "source": {"type": "base64", "data": "!!not base64!!"}}]
    assert externalize_attachments(bad, tmp_path) == bad


def test_missing_file_becomes_text(tmp_path):
    out = externalize_attachments([_IMG], tmp_path)
    (tmp_path / out[0]["source"]["sha256"]).unlink()
    assert rehydrate_attachments(out, tmp_path) == [
        {"type": "text", "text": "[image attachment unavailable]"}]


def test_rejects_path_like_digest(tmp_path):
    ref = [{"type": "image", "source": {"type": REF_TYPE, "sha256": "../../etc/passwd"}}]
    assert rehydrate_attachments(ref, tmp_path)[0]["type"] == "text"


async def test_put_stores_ref_not_base64(tmp_path):
    from database.sqlite import SQLiteDatabase

    db_file = tmp_path / "t.db"
    db = SQLiteDatabase(db_path=str(db_file))
    await db.initialize()
    await db.messages.put({"id": "m1", "session_id": "s1", "role": "user",
                           "content": [_TXT, _IMG]})
    conn = sqlite3.connect(str(db_file))
    stored = conn.execute("SELECT content FROM messages WHERE id='m1'").fetchone()[0]
    conn.close()
    assert _B64 not in stored and REF_TYPE in stored
    assert any(attachments_dir(db_file).iterdir())


async def test_pending_image_redelivered_intact(tmp_path, monkeypatch):
    from core import session_pending
    from database.sqlite import SQLiteDatabase

    db_file = tmp_path / "t.db"
    db = SQLiteDatabase(db_path=str(db_file))
    await db.initialize()
    monkeypatch.setattr(session_pending, "_db_path_override", str(db_file))
    await session_pending.persist_pending(
        "s1", user_message=None, content=[_TXT, _IMG], agent_id="a")
    conn = sqlite3.connect(str(db_file))
    stored = conn.execute("SELECT content FROM messages WHERE sent=0").fetchone()[0]
    conn.close()
    assert _B64 not in stored
    claimed = await session_pending.claim_pending_batch("s1")
    _text, blocks = session_pending.combine_pending(claimed)
    assert blocks == [_TXT, _IMG]
