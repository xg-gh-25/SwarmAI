"""Ingestion hygiene: DailyActivity credential redaction and heading-only chunks.

Properties:
- a credential in session text never reaches the day file or its JSONL sidecar
  (session blocks, session-less appends, nested sidecar values);
- ordinary text is unchanged;
- chunk_markdown skips sections with no body (bare headings, a heading directly
  followed by its subheading, a lone '---') and keeps sections with content.
"""

import json

from core.daily_activity_writer import (
    _write_jsonl_sidecar, append_daily_text, upsert_session_block)
from core.knowledge_store import chunk_markdown

TOKEN = "ghp_" + "A1b2C3d4E5" * 4  # GitHub token shape, not a real token
SLACK = "xoxb-1234567890-" + "abcdefghij" * 2


def test_session_block_and_append_are_redacted(tmp_path):
    day = tmp_path / "2026-10-22.md"
    upsert_session_block(day, "sess-1", "summary",
                         f"## 10:00 | sess-1 | t\n\n**What happened:**\n- use {TOKEN} for push\n")
    append_daily_text(day, f"- capture: slack {SLACK}\n")
    text = day.read_text()
    assert TOKEN not in text and SLACK not in text
    assert "[REDACTED]" in text and "for push" in text


def test_jsonl_sidecar_is_redacted_in_nested_values(tmp_path):
    side = tmp_path / "2026-10-22.jsonl"
    _write_jsonl_sidecar(side, {"session_id": "s", "topics": [f"token {TOKEN}"],
                                "meta": {"note": SLACK}, "n": 3})
    rec = json.loads(side.read_text().splitlines()[0])
    assert TOKEN not in json.dumps(rec) and SLACK not in json.dumps(rec)
    assert rec["n"] == 3 and rec["topics"] == ["token [REDACTED]"]


def test_ordinary_text_unchanged(tmp_path):
    day = tmp_path / "d.md"
    body = "## 10:00 | s | t\n\n- fixed recall; commit 16672a60 on main\n"
    upsert_session_block(day, "s", "summary", body)
    assert "fixed recall; commit 16672a60 on main" in day.read_text()


def test_heading_only_sections_are_not_chunks():
    md = (
        "## Signals\n"
        "### 🔴 Act Now\n\n"
        "## 🟡 Worth Knowing\n\n"
        "## Divider\n---\n"
        "## Real\nThe recall leg now has a deadline.\n"
    )
    chunks = chunk_markdown(md, "Signals/x.md")
    assert [c["heading"] for c in chunks] == ["Real"]
    assert "deadline" in chunks[0]["content"]
