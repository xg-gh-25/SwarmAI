"""Completion summary inputs: resume-class acks and the DELIVER ``outcome`` shape.

Two defects in what the completion summary reads, tested at the place each one is
decided:

- ``core.run_delivery_summary._is_ack`` decides which user messages are not the run's
  request. ``resume`` and similar control words were missing from ``_ACKS``, so a run
  created right after "resume" recorded "resume" as its intent.
- ``pipeline_validator.validate_artifact_data('deliver', ...)`` never checked
  ``outcome``. ``review_summary`` reads only ``{problem_before, result_now,
  approach[]}``, so a plain-string outcome passed DELIVER and the summary showed 未记录
  with no error. The deliver template also lacked ``outcome``, so authors had no shape
  to copy. An ABSENT outcome stays valid: report-and-commit.md defines it as a visible
  未记录.
"""
import copy
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

_scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from core import run_delivery_summary as rds  # noqa: E402


# ── bug 1: resume-class acks ────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "resume", "Resume.", "RESUME", "resume please", "please continue", "continue please",
    "keep going", "go ahead", "carry on", "继续做", "接着做", "请继续", "接着来",
])
def test_resume_class_words_are_acks(text):
    assert rds._is_ack(text) is True


@pytest.mark.parametrize("text", [
    "resume the upload feature", "go ahead with the migration", "keep going on the export bug",
    "修复登录bug",
])
def test_requests_starting_with_ack_words_are_not_acks(text):
    assert rds._is_ack(text) is False


@pytest.fixture
def shanghai_tz(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _msg_db(path: Path, rows):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL,"
                 " content TEXT NOT NULL, created_at TEXT NOT NULL, sent INTEGER)")
    for i, (text, created) in enumerate(rows):
        conn.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)",
                     (str(i), "s1", "user",
                      json.dumps([{"type": "text", "text": text}], ensure_ascii=False), created, 1))
    conn.commit()
    conn.close()
    return path


def test_capture_intent_skips_trailing_resume(tmp_path, shanghai_tz):
    real = "起pipeline 让 PLAN 阶段直接拒绝目录引用"
    db = _msg_db(tmp_path / "m.db", [
        (real, "2026-10-17T16:50:00"),
        ("resume", "2026-10-17T16:58:00"),
        ("Resume.", "2026-10-17T16:59:00"),
    ])
    before = "2026-10-17T09:02:48+00:00"  # 17:02:48 local
    assert rds.capture_intent("s1", before, db_path=db) == (real, "user_message")


# ── bug 2: DELIVER outcome shape ────────────────────────────────────────────
