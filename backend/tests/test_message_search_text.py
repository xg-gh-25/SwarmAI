"""Tests for core/message_search_text.py -- what messages.content_seg is built from.

Properties checked:
- text blocks and tool_use summaries are indexed (search still finds them);
- base64 payloads, thinking, tool results, ids and JSON keys are not;
- legacy plain-string content is indexed whole;
- every writer (SQLiteMessagesTable.put, session_pending.persist_pending)
  stores the same value, so search and migration agree.
"""

import json

from core.cjk_index import expand_cjk
from core.message_search_text import message_content_seg, message_search_text

_BLOCKS = [
    {"type": "text", "text": "部署 daemon 失败"},
    {"type": "thinking", "thinking": "private reasoning words", "signature": "SIGxyz"},
    {"type": "tool_use", "id": "toolu_bdrk_01ABC", "name": "Bash",
     "summary": "Running: pytest tests/test_foo.py",
     "display": {"tool": "Bash", "object": "pytest tests/test_foo.py"}},
    {"type": "tool_result", "tool_use_id": "toolu_bdrk_01ABC", "outcome": "noisy output"},
    {"type": "image", "source": {"type": "base64", "data": "iVBORw0KGgoAAAA"}},
    {"type": "document", "source": {"type": "base64", "data": "UEsDBBQABgAI"}},
]


def test_keeps_text_and_tool_summary_only():
    text = message_search_text(_BLOCKS)
    assert "部署 daemon 失败" in text
    assert "pytest tests/test_foo.py" in text
    for junk in ("private reasoning", "SIGxyz", "toolu_bdrk", "noisy output",
                 "iVBORw0KGgo", "UEsDBBQ", "tool_use_id", "base64"):
        assert junk not in text, junk


def test_json_string_and_list_agree():
    assert message_search_text(json.dumps(_BLOCKS)) == message_search_text(_BLOCKS)


def test_tool_use_without_summary_falls_back_to_name():
    assert message_search_text([{"type": "tool_use", "name": "Grep"}]) == "Grep"


def test_plain_string_indexed_whole():
    assert message_search_text("hello 世界") == "hello 世界"
    assert message_search_text("42") == "42"  # valid JSON, not a block list


def test_seg_is_expanded_search_text():
    assert message_content_seg(_BLOCKS) == expand_cjk(message_search_text(_BLOCKS))
    assert message_content_seg(None) == ""


async def test_put_stores_search_text_seg(tmp_path):
    from database.sqlite import SQLiteDatabase

    db = SQLiteDatabase(db_path=str(tmp_path / "t.db"))
    await db.initialize()
    await db.messages.put({"id": "m1", "session_id": "s1", "role": "assistant",
                           "content": _BLOCKS})
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    seg = conn.execute("SELECT content_seg FROM messages WHERE id='m1'").fetchone()[0]
    hits = conn.execute(
        "SELECT count(*) FROM messages_fts WHERE messages_fts MATCH ?",
        (expand_cjk('"部署"'),)).fetchone()[0]
    conn.close()
    assert seg == message_content_seg(_BLOCKS)
    assert "iVBORw0KGgo" not in seg
    assert hits == 1


def test_turn_products_are_searchable_by_path_and_target():
    blocks = [
        {"type": "turn_product", "toolUseId": "toolu_x", "product": "file",
         "path": "Knowledge/Notes/q3-report.md", "absolutePath": "/w/Knowledge/Notes/q3-report.md",
         "fileKind": "content"},
        {"type": "turn_product", "toolUseId": "toolu_y", "product": "remote",
         "tool": "mcp__slack-mcp__post_message", "action": "Posted to Slack", "target": "#launch"},
    ]
    text = message_search_text(blocks)
    assert "q3-report.md" in text and "Posted to Slack #launch" in text
    assert "toolu_" not in text and "/w/" not in text
