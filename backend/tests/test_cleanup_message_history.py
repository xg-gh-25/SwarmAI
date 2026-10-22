"""Tests for scripts/cleanup_message_history.py -- bring old message rows to the lean shape.

Properties: signatures removed, base64 attachments moved to files (and still
re-sendable from a pending row), content_seg rebuilt from searchable words only
so FTS finds text but not base64, untouched rows stay byte-identical, a backup
is written first, and a second run changes nothing.
"""

import asyncio
import base64
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cleanup_message_history as cmh  # noqa: E402
from core.cjk_index import expand_cjk, expand_cjk_query  # noqa: E402
from core.message_attachments import attachments_dir, rehydrate_attachments  # noqa: E402

B64 = base64.b64encode(b"\x89PNG" + bytes(range(200))).decode()
OLD_ASSISTANT = [
    {"type": "thinking", "thinking": "let me think", "signature": "SIG" * 50},
    {"type": "text", "text": "部署 已经完成"},
]
OLD_USER = [{"type": "text", "text": "看图"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": B64}}]
PLAIN = [{"type": "text", "text": "nothing to clean"}]


def _db(tmp_path):
    from database.sqlite import SQLiteDatabase
    db_file = tmp_path / "data.db"
    asyncio.run(SQLiteDatabase(str(db_file)).initialize())
    conn = sqlite3.connect(str(db_file))
    for mid, role, content, sent in (("a", "assistant", OLD_ASSISTANT, 1),
                                     ("u", "user", OLD_USER, 0), ("p", "user", PLAIN, 1)):
        raw = json.dumps(content, ensure_ascii=False)
        conn.execute(
            "INSERT INTO messages(id, session_id, role, content, content_seg, sent, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,'t','t')",
            (mid, "s", role, raw, expand_cjk(raw), sent))  # the OLD seg: raw JSON
    conn.commit()
    plain_before = conn.execute("SELECT content, content_seg FROM messages WHERE id='p'").fetchone()
    conn.close()
    return db_file, plain_before


def test_cleanup_rewrites_old_rows(tmp_path):
    db_file, plain_before = _db(tmp_path)
    assert cmh.main(["--db", str(db_file), "--apply", "--pause", "0"]) == 0
    assert list(tmp_path.glob("data.pre-msg-cleanup-*.db"))

    conn = sqlite3.connect(str(db_file))
    rows = dict(conn.execute("SELECT id, content FROM messages"))
    a, u = json.loads(rows["a"]), json.loads(rows["u"])
    assert "signature" not in a[0] and a[0]["thinking"] == "let me think"
    assert u[1]["source"]["type"] == "attachment_ref" and B64 not in rows["u"]
    assert rehydrate_attachments(u, attachments_dir(db_file)) == OLD_USER

    def hits(term):
        return conn.execute("SELECT count(*) FROM messages_fts WHERE messages_fts MATCH ?",
                            (expand_cjk_query(term),)).fetchone()[0]
    assert hits("部署") == 1
    assert hits("signature") == 0 and hits("base64") == 0
    plain_after = conn.execute("SELECT content, content_seg FROM messages WHERE id='p'").fetchone()
    assert plain_after[0] == plain_before[0]
    conn.close()

    # idempotent: a second run has nothing left to change
    snap = sqlite3.connect(str(db_file)).execute(
        "SELECT id, content, content_seg FROM messages ORDER BY id").fetchall()
    cmh.main(["--db", str(db_file), "--apply", "--pause", "0"])
    again = sqlite3.connect(str(db_file)).execute(
        "SELECT id, content, content_seg FROM messages ORDER BY id").fetchall()
    assert again == snap


def test_dry_run_writes_nothing(tmp_path):
    db_file, _ = _db(tmp_path)
    before = sqlite3.connect(str(db_file)).execute(
        "SELECT id, content, content_seg FROM messages ORDER BY id").fetchall()
    cmh.main(["--db", str(db_file)])
    after = sqlite3.connect(str(db_file)).execute(
        "SELECT id, content, content_seg FROM messages ORDER BY id").fetchall()
    assert after == before
    assert not list(tmp_path.glob("data.pre-msg-cleanup-*.db"))
    assert not attachments_dir(db_file).exists()
