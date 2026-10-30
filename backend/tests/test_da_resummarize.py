"""The DailyActivity history repair (scripts/da_resummarize.py, design
docs/design/2026-10-31-daily-activity-useful.md §4).

- the day file splits into segments that rejoin to the same text; an unterminated
  marker is text;
- a rebuilt day keeps captures and rows it cannot rebuild, drops checkpoint and
  day-summary blocks and re-summaries of other days, and writes one row per session
  that has messages that day;
- nothing is written unless the backup of the directory is verified;
- a second run asks the model nothing (rows are complete).

Mock boundaries: the model call and the git scan; data.db is a temp file.
"""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.summarization import SummarizationPipeline
from hooks import daily_activity_hook as H
from scripts import da_resummarize as R

DAY_FILE = """---
date: "2026-10-20"
sessions_count: 3
distilled: false
---
**🔔 High-signal capture** (09:01): keep this capture

<!-- da:checkpoint:sess-a -->
## 09:30 | sess-a | 📸 Mid-session checkpoint
**Files:** `x.py`
<!-- /da -->

<!-- da:summary:sess-a -->
## 10:00 | sess-a | 你看看这个问题
Summary: 你看看这个问题；还有那个
<!-- /da -->

<!-- da:summary:sess-b -->
## 11:00 | sess-b | re-summary of another day
<!-- /da -->

## 12:00 | gone1234 | Legacy block of a session without messages
- kept

<!-- daily-summary:2026-10-20 -->
## 日汇总
主线：拼接
交付：
- 无
"""

ANSWER = {"title": "Parser fix", "summary_sentence": "Fixed the parser crash.",
          "deliverables": ["parser skips blank lines"], "key_outputs": [], "validation_status": "",
          "next_items": ["Release 2.4"], "resolves": [], "coe_signal": "", "coe_topic": ""}


def _db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE messages (id TEXT, session_id TEXT, role TEXT, content TEXT, "
                 "metadata TEXT, created_at TEXT, sent INTEGER DEFAULT 1)")
    conn.execute("CREATE TABLE sessions (id TEXT, title TEXT, created_at TEXT, agent_id TEXT)")
    rows = [("1", "sess-a", "user", "please fix the parser", "{}", "2026-10-20T10:00:00", 1),
            ("2", "sess-a", "assistant", "Fixed.", "{}", "2026-10-20T10:05:00", 1),
            ("3", "sess-b", "user", "docs please", "{}", "2026-10-19T10:00:00", 1),
            ("4", "sess-b", "assistant", "Written.", "{}", "2026-10-19T10:05:00", 1),
            ("5", "sess-a", "user", "pending, not sent", "{}", "2026-10-20T10:06:00", 0)]
    conn.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO sessions VALUES (?,?,?,?)",
                     [("sess-a", "tab a", "2026-10-20T10:00:00", "default"),
                      ("sess-b", "tab b", "2026-10-19T10:00:00", "default")])
    conn.commit()
    conn.close()


@pytest.fixture
def ws(tmp_path):
    da = tmp_path / "SwarmWS" / "Knowledge" / "DailyActivity"
    da.mkdir(parents=True)
    (da / "2026-10-20.md").write_text(DAY_FILE, encoding="utf-8")
    rows = [{"session_id": "sess-a", "title": "你看看这个问题", "summary_sentence": "你看看这个问题"},
            {"session_id": "sess-b", "title": "re-summary"},
            {"session_id": "gone1234-0000", "title": "kept row"}]
    (da / "2026-10-20.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    (da / "2026-10-19.jsonl").write_text(json.dumps({"session_id": "sess-b", "title": "docs"}) + "\n")
    _db(tmp_path / "data.db")
    return tmp_path


def _args(ws, **kw):
    base = dict(workspace=str(ws / "SwarmWS"), db=str(ws / "data.db"), backup_root=str(ws / "backups"),
                since="2026-10-19", until="2026-10-20", jobs=2, apply=True, restore_archives=False)
    base.update(kw)
    return SimpleNamespace(**base)


def _run(ws, calls, **kw):
    def _llm(prompt):
        calls.append(prompt)
        return ANSWER
    with patch.object(SummarizationPipeline, "_call_enrichment_llm", staticmethod(_llm)), \
         patch.object(H.DailyActivityExtractionHook, "_capture_git_activity",
                      staticmethod(lambda *a, **k: [])), \
         patch.object(H, "_open_problem_titles", return_value=[]):
        import asyncio
        return asyncio.run(R.repair(_args(ws, **kw)))


def test_segments_rejoin_to_the_body_and_an_open_marker_is_text():
    body = DAY_FILE.split("---\n", 2)[2]
    segments = R.split_day(body)
    assert "\n".join(s.text for s in segments) == body
    assert [s.kind for s in segments if s.kind != "text"] == [
        "checkpoint", "summary", "summary", "legacy", "day_summary"]
    broken = "<!-- da:summary:x -->\n## 10:00 | x | no end marker\nuser notes"
    assert all(s.kind != "summary" for s in R.split_day(broken))


def test_a_rebuilt_day_keeps_what_it_cannot_rebuild(ws):
    calls: list[str] = []
    _run(ws, calls)
    da = ws / "SwarmWS" / "Knowledge" / "DailyActivity"
    text = (da / "2026-10-20.md").read_text(encoding="utf-8")
    assert "keep this capture" in text                       # free text stays
    assert "Legacy block of a session without messages" in text
    assert "Mid-session checkpoint" not in text and "日汇总" not in text
    assert "re-summary of another day" not in text           # sess-b had no messages that day
    assert "你看看这个问题" not in text                          # the old row is replaced
    assert text.count("<!-- da:summary:sess-a -->") == 1 and "Fixed the parser crash." in text
    rows = {r["session_id"]: r for r in R._rows(da / "2026-10-20.jsonl")}
    assert set(rows) == {"sess-a", "gone1234-0000"}
    assert rows["sess-a"]["enriched"] is True and rows["sess-a"]["message_count"] == 2
    assert "pending, not sent" not in "".join(calls)         # unsent messages are not summarized
    assert list((ws / "backups").iterdir())                  # backup taken first


def test_nothing_is_written_when_the_backup_check_fails(ws):
    da = ws / "SwarmWS" / "Knowledge" / "DailyActivity"
    before = {p.name: p.read_bytes() for p in da.iterdir()}
    with patch.object(R, "_sha", side_effect=["a", "b"] * 10):
        with pytest.raises(SystemExit):
            _run(ws, [])
    assert {p.name: p.read_bytes() for p in da.iterdir()} == before


def test_a_second_run_asks_the_model_nothing(ws):
    first: list[str] = []
    _run(ws, first)
    assert first
    second: list[str] = []
    _run(ws, second)
    assert second == []


def test_dry_run_writes_nothing(ws):
    da = ws / "SwarmWS" / "Knowledge" / "DailyActivity"
    before = {p.name: p.read_bytes() for p in da.iterdir()}
    _run(ws, [], apply=False)
    assert {p.name: p.read_bytes() for p in da.iterdir()} == before
    assert not (ws / "backups").exists()


def _run_with(ws, llm, **kw):
    with patch.object(SummarizationPipeline, "_call_enrichment_llm", staticmethod(llm)), \
         patch.object(H.DailyActivityExtractionHook, "_capture_git_activity",
                      staticmethod(lambda *a, **k: [])), \
         patch.object(H, "_open_problem_titles", return_value=[]):
        import asyncio
        return asyncio.run(R.repair(_args(ws, **kw)))


def test_a_capture_after_an_old_block_is_kept(ws):
    da = ws / "SwarmWS" / "Knowledge" / "DailyActivity"
    md = da / "2026-10-20.md"
    md.write_text(md.read_text().replace(
        "- kept\n", "- kept\n**🔔 High-signal capture** (12:30): AFTER-LEGACY capture\n"
        "second line of that capture\n").replace(
        "- 无\n", "- 无\n**🔔 High-signal capture** (23:00): AFTER-DAY-SUMMARY capture\n"
    ).replace("## 12:00 | gone1234 |", "## 12:00 | sess-a |"), encoding="utf-8")
    _run(ws, [])
    text = md.read_text(encoding="utf-8")
    assert "AFTER-LEGACY capture\nsecond line of that capture" in text
    assert "AFTER-DAY-SUMMARY capture" in text


def test_a_failed_or_unanswered_rebuild_keeps_the_old_row_and_block(ws):
    da = ws / "SwarmWS" / "Knowledge" / "DailyActivity"
    for llm in (lambda prompt: {}, lambda prompt: (_ for _ in ()).throw(RuntimeError("down"))):
        with patch.object(H.DailyActivityExtractionHook, "_is_noise_summary",
                          staticmethod(lambda summary: False)):
            _run_with(ws, llm)
        text = (da / "2026-10-20.md").read_text(encoding="utf-8")
        assert "你看看这个问题；还有那个" in text            # old block kept
        rows = {r["session_id"] for r in R._rows(da / "2026-10-20.jsonl")}
        assert "sess-a" in rows                              # and its row


def test_a_row_whose_real_day_is_outside_the_run_is_kept(ws):
    # sess-b's real day (10-19) is not rebuilt in a run of 10-20 only
    _run(ws, [], since="2026-10-20", until="2026-10-20")
    da = ws / "SwarmWS" / "Knowledge" / "DailyActivity"
    assert "sess-b" in {r["session_id"] for r in R._rows(da / "2026-10-20.jsonl")}


def test_a_later_session_sees_the_followups_an_earlier_one_opened_that_day(ws):
    import sqlite3 as _sq
    conn = _sq.connect(ws / "data.db")
    conn.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", [
        ("6", "sess-c", "user", "release it", "{}", "2026-10-20T15:00:00", 1),
        ("7", "sess-c", "assistant", "Released.", "{}", "2026-10-20T15:05:00", 1)])
    conn.execute("INSERT INTO sessions VALUES ('sess-c', 'tab c', '2026-10-20T15:00:00', 'default')")
    conn.commit()
    conn.close()
    da = ws / "SwarmWS" / "Knowledge" / "DailyActivity"
    with (da / "2026-10-20.jsonl").open("a") as fh:
        fh.write(json.dumps({"session_id": "sess-c", "title": "c"}) + "\n")
    prompts: list[str] = []

    def llm(prompt):
        prompts.append(prompt)
        mark = "FROM-A" if "please fix the parser" in prompt else "FROM-OTHER"
        return dict(ANSWER, next_items=[f"follow-up {mark}"])
    _run_with(ws, llm, jobs=1)
    later = next(p for p in prompts if "release it" in p)
    assert "follow-up FROM-A" in later                       # sess-a's follow-up, same day


def test_restore_archives_puts_back_deleted_days_only(tmp_path):
    import subprocess
    ws = tmp_path / "ws"
    arch = ws / "Knowledge" / "Archives"
    arch.mkdir(parents=True)
    git = ["git", "-c", "user.email=a@b", "-c", "user.name=n"]
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    (arch / "2026-03-05.md").write_text("old day", encoding="utf-8")
    (arch / "2026-03-06.md").write_text("another day", encoding="utf-8")
    (arch / "notes-draft.md").write_text("not a day", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
    subprocess.run(git + ["commit", "-qm", "add"], cwd=ws, check=True)
    for name in ("2026-03-05.md", "2026-03-06.md", "notes-draft.md"):
        (arch / name).unlink()
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
    subprocess.run(git + ["commit", "-qm", "prune"], cwd=ws, check=True)
    (arch / "2026-03-06.md").write_text("recreated since", encoding="utf-8")
    assert R.restore_archives(ws, apply=False) == 0 and not (arch / "2026-03-05.md").exists()
    R.restore_archives(ws, apply=True)
    assert (arch / "2026-03-05.md").read_text() == "old day"
    assert (arch / "2026-03-06.md").read_text() == "recreated since"   # existing file untouched
    assert not (arch / "notes-draft.md").exists()                      # not a day file
