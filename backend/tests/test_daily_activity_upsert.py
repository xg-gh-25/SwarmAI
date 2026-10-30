"""DailyActivity day file: one current block per session and kind (run_1ec5ea9d).

Verifies the single writer's identity rule (upsert_session_block):
- a second write for the same session + kind replaces its block, never appends one
- different kinds (checkpoint / summary) and different sessions keep their own blocks
- text outside the session's own markers (legacy blocks, captures) is byte-identical
- frontmatter sessions_count grows once per new summary block, not per write
- the JSONL sidecar keeps one record per session (latest wins)
- writers serialize on the shared lock, so concurrent writes all land
"""
import asyncio
import json
import threading
from pathlib import Path

import pytest

from core.daily_activity_writer import (
    append_daily_text,
    parse_frontmatter,
    read_jsonl_sidecar,
    upsert_session_block,
    write_daily_activity,
)
from core.session_hooks import HookContext
from core.summarization import StructuredSummary


def _ctx(sid="sess-aaaa-1111"):
    return HookContext(session_id=sid, agent_id="a", message_count=5,
                       session_start_time="2026-10-21T08:00:00", session_title="T")


def _summary(title="Summary title one", sentence="sentence body"):
    return StructuredSummary(topics=["t"], deliverables=["did a thing"],
                             summary_sentence=sentence, session_title=title)


def test_same_session_and_kind_is_replaced_not_appended(tmp_path):
    f = tmp_path / "2026-10-21.md"
    upsert_session_block(f, "s1", "checkpoint", "## 10:00 | s1 | 📸 Mid-session checkpoint\n**Files:** `a.py`\n")
    upsert_session_block(f, "s1", "checkpoint", "## 10:05 | s1 | 📸 Mid-session checkpoint\n**Files:** `a.py`, `b.py`\n")
    text = f.read_text(encoding="utf-8")
    assert text.count("Mid-session checkpoint") == 1
    assert "`b.py`" in text and "10:00" not in text


def test_kinds_and_sessions_keep_their_own_blocks(tmp_path):
    f = tmp_path / "2026-10-21.md"
    upsert_session_block(f, "s1", "checkpoint", "## 10:00 | s1 | 📸 Mid-session checkpoint\n")
    upsert_session_block(f, "s1", "summary", "## 10:10 | s1 | Session summary\n")
    upsert_session_block(f, "s2", "checkpoint", "## 10:20 | s2 | 📸 Mid-session checkpoint\n")
    upsert_session_block(f, "s1", "checkpoint", "## 10:30 | s1 | 📸 Mid-session checkpoint\n")
    text = f.read_text(encoding="utf-8")
    assert text.count("| s1 | 📸") == 1 and text.count("| s2 | 📸") == 1
    assert text.count("Session summary") == 1
    assert "10:30 | s1" in text


def test_text_outside_the_session_markers_is_byte_identical(tmp_path):
    f = tmp_path / "2026-10-21.md"
    legacy = ("---\ndate: \"2026-10-21\"\nsessions_count: 3\ndistilled: false\n---\n"
              "## 09:00 | old11111 | Legacy block\n- kept\n\n"
              "**🔔 High-signal capture** (09:01): a capture line\n"
              "## 09:30 | old11111 | 📸 Mid-session checkpoint\n**Files:** `x.py`\n")
    f.write_text(legacy, encoding="utf-8")
    upsert_session_block(f, "new1", "checkpoint", "## 11:00 | new1 | 📸 Mid-session checkpoint\n")
    upsert_session_block(f, "new1", "checkpoint", "## 11:05 | new1 | 📸 Mid-session checkpoint\n")
    text = f.read_text(encoding="utf-8")
    assert text.startswith(legacy.rstrip("\n"))
    assert text.count("11:05 | new1") == 1 and "11:00 | new1" not in text


def test_broken_marker_never_deletes_text(tmp_path):
    # F1: a start marker without its end marker (hand edit) is left alone.
    f = tmp_path / "2026-10-21.md"
    f.write_text("<!-- da:checkpoint:s1 -->\n## 09:00 | s1 | broken\nuser notes here\n", encoding="utf-8")
    upsert_session_block(f, "s1", "checkpoint", "## 10:00 | s1 | 📸 Mid-session checkpoint\n")
    upsert_session_block(f, "s1", "checkpoint", "## 10:05 | s1 | 📸 Mid-session checkpoint\n")
    text = f.read_text(encoding="utf-8")
    assert "user notes here" in text and "09:00 | s1 | broken" in text
    assert text.count("📸 Mid-session checkpoint") == 1 and "10:05" in text


def test_sessions_count_grows_once_per_new_summary(tmp_path):
    ws = tmp_path
    for _ in range(3):
        asyncio.run(write_daily_activity(_summary(), _ctx("s-one-0001"), workspace_path=ws))
    asyncio.run(write_daily_activity(_summary("Second session title"), _ctx("s-two-0002"), workspace_path=ws))
    da = next((ws / "Knowledge" / "DailyActivity").glob("*.md"))
    fm, body = parse_frontmatter(da.read_text(encoding="utf-8"))
    assert fm["sessions_count"] == 2
    assert body.count("| s-one-0001 |") == 1 and body.count("| s-two-0002 |") == 1


def test_jsonl_sidecar_keeps_one_record_per_session_latest_wins(tmp_path):
    ws = tmp_path
    asyncio.run(write_daily_activity(_summary(sentence="first"), _ctx("s-one-0001"), workspace_path=ws))
    asyncio.run(write_daily_activity(_summary(sentence="second"), _ctx("s-one-0001"), workspace_path=ws))
    asyncio.run(write_daily_activity(_summary(sentence="other"), _ctx("s-two-0002"), workspace_path=ws))
    j = next((ws / "Knowledge" / "DailyActivity").glob("*.jsonl"))
    recs = read_jsonl_sidecar(j)
    assert [r["session_id"] for r in recs] == ["s-one-0001", "s-two-0002"]
    assert recs[0]["summary_sentence"] == "second"


def test_concurrent_writers_all_land(tmp_path):
    # F2: block upserts and plain captures from many threads serialize on one lock.
    f = tmp_path / "2026-10-21.md"
    def block(i):
        upsert_session_block(f, f"s{i}", "checkpoint", f"## 10:00 | s{i} | 📸 Mid-session checkpoint\n")
    def capture(i):
        append_daily_text(f, f"**🔔 High-signal capture** (10:00): capture {i}\n")
    ts = [threading.Thread(target=block, args=(i,)) for i in range(15)]
    ts += [threading.Thread(target=capture, args=(i,)) for i in range(15)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    text = f.read_text(encoding="utf-8")
    assert all(f"| s{i} |" in text for i in range(15))
    assert all(f"capture {i}\n" in text for i in range(15))


def test_quoted_markers_in_text_do_not_break_the_block(tmp_path):
    f = tmp_path / "2026-10-21.md"
    for i in range(3):
        upsert_session_block(f, "s1", "summary",
                             f"## 10:0{i} | s1 | about markers\nquoting <!-- /da --> and <!-- da:summary:x --> v{i}\n")
    text = f.read_text(encoding="utf-8")
    assert text.count("about markers") == 1 and "v2" in text and "v0" not in text and "v1" not in text


def test_session_without_an_id_never_overwrites_another(tmp_path):
    f = tmp_path / "2026-10-21.md"
    upsert_session_block(f, "unknown", "checkpoint", "## 10:00 | unknown | first\n")
    upsert_session_block(f, "unknown", "checkpoint", "## 10:01 | unknown | second\n")
    text = f.read_text(encoding="utf-8")
    assert "first" in text and "second" in text


def test_rewrite_keeps_the_day_file_mode(tmp_path):
    import os
    import stat
    f = tmp_path / "2026-10-21.md"
    f.write_text("---\ndate: \"x\"\n---\n", encoding="utf-8")
    os.chmod(f, 0o644)
    upsert_session_block(f, "s1", "checkpoint", "## 10:00 | s1 | c\n")
    assert stat.S_IMODE(f.stat().st_mode) == 0o644


def test_long_session_rerun_keeps_one_summary_block(tmp_path):
    # Re-runs of the post-session hook on a session past the 500-message window
    # (count 520, then 600) replace the one summary block and record.
    ws = tmp_path
    for n, title in ((520, "First window title"), (600, "Later window title")):
        ctx = HookContext(session_id="long-sess-01", agent_id="a", message_count=n,
                          session_start_time="2026-10-21T08:00:00", session_title="T")
        asyncio.run(write_daily_activity(_summary(title), ctx, workspace_path=ws))
    da = next((ws / "Knowledge" / "DailyActivity").glob("*.md"))
    text = da.read_text(encoding="utf-8")
    assert text.count("| long-sess-01 |") == 1 and "Later window title" in text
    assert len(read_jsonl_sidecar(da.with_suffix(".jsonl"))) == 1
    fm, _ = parse_frontmatter(text)
    assert fm["sessions_count"] == 1


def test_rewritten_block_moves_to_the_end(tmp_path):
    f = tmp_path / "2026-10-21.md"
    upsert_session_block(f, "early", "checkpoint", "## 09:00 | early | 📸 Mid-session checkpoint\nv1\n")
    upsert_session_block(f, "other", "summary", "## 10:00 | other | Other session\n")
    upsert_session_block(f, "early", "checkpoint", "## 11:00 | early | 📸 Mid-session checkpoint\nv2\n")
    text = f.read_text(encoding="utf-8")
    assert text.rstrip().endswith("v2\n<!-- /da -->".rstrip())
    assert text.index("Other session") < text.index("v2") and "v1" not in text


def test_day_file_lock_is_hidden(tmp_path):
    f = tmp_path / "2026-10-21.md"
    upsert_session_block(f, "s1", "checkpoint", "## 10:00 | s1 | c\n")
    append_daily_text(f, "capture\n")
    names = sorted(p.name for p in tmp_path.iterdir())
    assert all(n.startswith(".") for n in names if n.endswith(".lock")), names
    assert not any(n.endswith(".md.lock") or n.endswith(".jsonl.lock") for n in names)


def test_backlog_skip_sees_only_a_summary_not_a_checkpoint(tmp_path):
    # lifecycle_manager's startup backlog skips a session only once it is summarized:
    # a checkpoint or crash-recovery block (which carries the full id) is not enough.
    from core.daily_activity_writer import has_session_summary
    f = tmp_path / "2026-10-21.md"
    upsert_session_block(f, "sess-full-id-1", "checkpoint", "## 10:00 | sess-ful | 📸 Mid-session checkpoint\n")
    assert not has_session_summary(f.read_text(encoding="utf-8"), "sess-full-id-1")
    upsert_session_block(f, "sess-full-id-1", "summary", "## 10:30 | sess-ful | Summary\n")
    assert has_session_summary(f.read_text(encoding="utf-8"), "sess-full-id-1")


def test_a_past_day_is_written_to_its_own_file_with_its_own_date(tmp_path):
    # the history repair writes days other than today (design 2026-10-31 §4)
    from datetime import date
    ws = tmp_path
    asyncio.run(write_daily_activity(_summary(), _ctx("s-old-0001"), workspace_path=ws,
                                     day=date(2026, 9, 3)))
    da = ws / "Knowledge" / "DailyActivity" / "2026-09-03.md"
    fm, _ = parse_frontmatter(da.read_text(encoding="utf-8"))
    assert fm["date"] == "2026-09-03"
    assert read_jsonl_sidecar(da.with_suffix(".jsonl"))[0]["session_id"] == "s-old-0001"


def test_the_sidecar_row_records_what_it_covers(tmp_path):
    ws = tmp_path
    summary = _summary()
    summary.enriched = True
    asyncio.run(write_daily_activity(summary, _ctx("s-cov-0001"), workspace_path=ws))
    rec = read_jsonl_sidecar(next((ws / "Knowledge" / "DailyActivity").glob("*.jsonl")))[0]
    assert rec["message_count"] == 5 and rec["enriched"] is True
    for gone in ("lessons", "lessons_typed", "rejected_approaches", "corrections",
                 "process_reflection", "signal_driven_actions", "continue_from"):
        assert gone not in rec


def test_a_short_model_title_is_kept_and_a_garbage_tab_title_is_not(tmp_path):
    ws = tmp_path
    asyncio.run(write_daily_activity(_summary(title="库膨胀修复"), _ctx("s-ttl-0001"), workspace_path=ws))
    ctx = HookContext(session_id="s-ttl-0002", agent_id="a", message_count=5,
                      session_start_time="2026-10-21T08:00:00", session_title="ok")
    asyncio.run(write_daily_activity(_summary(title=""), ctx, workspace_path=ws))
    recs = read_jsonl_sidecar(next((ws / "Knowledge" / "DailyActivity").glob("*.jsonl")))
    assert [r["title"] for r in recs] == ["库膨胀修复", "Untitled"]
