"""One DailyActivity row per session per local day, about that day (design
docs/design/2026-10-31-daily-activity-useful.md D4-D6).

- a session spanning midnight is summarized from today's messages only, and the
  model is told what was already recorded for it on an earlier day;
- no model call when today's row is complete and nothing new arrived; a row the
  model did not write is tried again;
- without a model answer the sentence is the day's commits, never the user's words.

Mock boundaries: the transcript reader, the workspace path, the model call
(_call_enrichment_llm) and the git scan (_capture_git_activity).
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from core.session_hooks import HookContext
from core.summarization import SummarizationPipeline
from hooks import daily_activity_hook as H


def _msg(role, text, when):
    return {"id": f"{role}-{when.isoformat()}", "role": role, "content": text,
            "created_at": when.isoformat()}


def _ctx(sid="sess-day-0001"):
    return HookContext(session_id=sid, agent_id="default", message_count=0,
                       session_start_time="2026-01-01T00:00:00", session_title="tab title")


def _run(ws, messages, *, answer=None, commits=(), prompts=None):
    prompts = prompts if prompts is not None else []

    def _llm(prompt):
        prompts.append(prompt)
        return answer or {}

    hook = H.DailyActivityExtractionHook(SummarizationPipeline(), MagicMock())
    with patch.object(H, "_read_complete_transcript", return_value=messages), \
         patch("core.initialization_manager.initialization_manager.get_cached_workspace_path",
               return_value=str(ws)), \
         patch.object(H, "_open_problem_titles", return_value=[]), \
         patch.object(H, "STATE_DIR", ws / "state"), \
         patch.object(H.DailyActivityExtractionHook, "_capture_git_activity",
                      staticmethod(lambda *a, **k: list(commits))), \
         patch.object(SummarizationPipeline, "_call_enrichment_llm", staticmethod(_llm)):
        asyncio.run(hook.execute(_ctx()))
    return prompts


def _rows(ws, day):
    path = ws / "Knowledge" / "DailyActivity" / f"{day.isoformat()}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] \
        if path.exists() else []


ANSWER = {"title": "Parser fix", "summary_sentence": "Fixed the parser crash; tests green.",
          "deliverables": ["parser skips blank lines"], "key_outputs": [], "validation_status": "",
          "next_items": [], "resolves": [], "coe_signal": "", "coe_topic": ""}


def test_messages_on_day_keeps_only_that_local_day():
    day = date(2026, 10, 31)
    msgs = [_msg("user", "a", datetime(2026, 10, 30, 23, 59)),
            _msg("user", "b", datetime(2026, 10, 31, 0, 1))]
    assert [m["content"] for m in H.messages_on_day(msgs, day)] == ["b"]


def test_a_session_spanning_midnight_is_summarized_from_todays_messages(tmp_path):
    today = date.today()
    now = datetime.combine(today, datetime.min.time())
    yesterday_row = {"session_id": "sess-day-0001", "title": "Parser work",
                     "summary_sentence": "Found the crash.", "next_items": [], "resolves": [],
                     "enriched": True, "message_count": 2}
    da = tmp_path / "Knowledge" / "DailyActivity"
    da.mkdir(parents=True)
    (da / f"{(today - timedelta(days=1)).isoformat()}.jsonl").write_text(json.dumps(yesterday_row) + "\n")
    msgs = [_msg("user", "YESTERDAY-ONLY question", now - timedelta(hours=1)),
            _msg("assistant", "YESTERDAY-ONLY answer", now - timedelta(minutes=50)),
            _msg("user", "please fix it now", now + timedelta(minutes=10)),
            _msg("assistant", "Fixed.", now + timedelta(minutes=20))]
    prompts = _run(tmp_path, msgs, answer=ANSWER)
    (prompt,) = prompts
    assert "YESTERDAY-ONLY" not in prompt and "please fix it now" in prompt
    assert f"Already recorded on {(today - timedelta(days=1)).isoformat()}: Parser work: Found the crash." in prompt
    (row,) = _rows(tmp_path, today)
    assert row["message_count"] == 2 and row["enriched"] is True
    assert row["title"] == "Parser fix" and row["timestamp"] == "00:20"


def test_no_model_call_when_the_row_is_complete_and_nothing_is_new(tmp_path):
    now = datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=9)
    msgs = [_msg("user", "please fix the parser", now), _msg("assistant", "Fixed.", now)]
    assert len(_run(tmp_path, msgs, answer=ANSWER)) == 1
    assert _run(tmp_path, msgs, answer=ANSWER) == []           # unchanged: no call
    more = msgs + [_msg("user", "and the docs?", now), _msg("assistant", "Done.", now)]
    assert len(_run(tmp_path, more, answer=ANSWER)) == 1      # new messages: called


def test_a_row_without_a_model_answer_is_tried_again_and_shows_commits(tmp_path):
    now = datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=9)
    msgs = [_msg("user", "what did the user say here exactly", now), _msg("assistant", "ok", now)]
    _run(tmp_path, msgs, answer=None, commits=["abc1234 fix: parser"])
    (row,) = _rows(tmp_path, date.today())
    assert row["enriched"] is False
    assert row["summary_sentence"] == "Commits: abc1234 fix: parser"
    assert "what did the user say" not in row["summary_sentence"]
    assert len(_run(tmp_path, msgs, answer=ANSWER)) == 1      # retried
    (row,) = _rows(tmp_path, date.today())
    assert row["enriched"] is True


def test_messages_older_than_seven_days_write_nothing(tmp_path):
    then = datetime.combine(date.today(), datetime.min.time()) - timedelta(days=8)
    msgs = [_msg("user", "old question here", then), _msg("assistant", "old", then)]
    assert _run(tmp_path, msgs, answer=ANSWER) == []
    assert not (tmp_path / "Knowledge" / "DailyActivity").exists()


def test_the_tail_of_yesterday_is_added_after_midnight(tmp_path):
    # the last idle of a day can fire after midnight: yesterday's row must still
    # reach its last messages, from the new ones only, continuing the record
    today = date.today()
    y = datetime.combine(today, datetime.min.time()) - timedelta(hours=1)
    da = tmp_path / "Knowledge" / "DailyActivity"
    da.mkdir(parents=True)
    row = {"session_id": "sess-day-0001", "title": "Parser work", "summary_sentence": "Found the crash.",
           "deliverables": ["repro script"], "next_items": [], "resolves": [],
           "enriched": True, "message_count": 2, "files_modified": ["a.py"]}
    (da / f"{(today - timedelta(days=1)).isoformat()}.jsonl").write_text(json.dumps(row) + "\n")
    msgs = [_msg("user", "FIRST-PART find the crash", y), _msg("assistant", "found", y),
            _msg("user", "LATE now fix it", y + timedelta(minutes=50)),
            _msg("assistant", "Fixed.", y + timedelta(minutes=55))]
    (prompt,) = _run(tmp_path, msgs, answer=ANSWER)
    assert "LATE now fix it" in prompt and "FIRST-PART" not in prompt
    assert "record so far" in prompt and "repro script" in prompt
    (new,) = _rows(tmp_path, today - timedelta(days=1))
    assert new["message_count"] == 4 and new["enriched"] is True
    assert new["files_modified"][0] == "a.py"            # earlier rule-based fields kept


def test_a_failed_continuation_never_replaces_the_models_row(tmp_path):
    now = datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=9)
    msgs = [_msg("user", "please fix the parser", now), _msg("assistant", "Fixed.", now)]
    _run(tmp_path, msgs, answer=ANSWER)
    more = msgs + [_msg("user", "and the docs?", now), _msg("assistant", "Done.", now)]
    _run(tmp_path, more, answer=None)                      # model unavailable
    (row,) = _rows(tmp_path, date.today())
    assert row["enriched"] is True and row["message_count"] == 2
    assert row["summary_sentence"] == ANSWER["summary_sentence"]


def test_a_long_day_is_written_after_each_part(tmp_path, monkeypatch):
    import core.summarization as sm
    monkeypatch.setattr(sm, "_CHUNK_TOKENS", 60)        # two messages per part
    now = datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=9)
    msgs = [_msg("user" if i % 2 == 0 else "assistant", f"message number {i} " + "x" * 60, now)
            for i in range(4)]
    seen: list[int] = []
    real = H.write_daily_activity

    async def spy(summary, ctx, *a, **k):
        seen.append(ctx.message_count)
        return await real(summary, ctx, tmp_path, **k)
    monkeypatch.setattr(H, "write_daily_activity", spy)
    prompts = _run(tmp_path, msgs, answer=ANSWER)
    assert len(prompts) == 2 and seen == [2, 4]           # progress kept part by part
    assert "record so far" in prompts[1]



def test_the_hook_declares_its_own_hang_guard_and_others_keep_the_default():
    from core.session_hooks import SessionLifecycleHookManager

    class _Other:
        name = "other"

    manager = SessionLifecycleHookManager(timeout_seconds=180.0)
    hook = H.DailyActivityExtractionHook(SummarizationPipeline(), MagicMock())
    assert manager.timeout_for(hook) == 900.0
    assert manager.timeout_for(_Other()) == 180.0
    assert manager.timeout_for(MagicMock()) == 180.0      # a mock's attribute is no number


def test_a_slow_hook_with_its_own_timeout_is_not_cut_at_the_default():
    from core.session_hooks import SessionLifecycleHookManager

    done: list[str] = []

    class _Slow:
        name = "slow"
        timeout_seconds = 5.0

        async def execute(self, context):
            await asyncio.sleep(0.3)
            done.append("slow")

    manager = SessionLifecycleHookManager(timeout_seconds=0.1)
    manager.register(_Slow())
    asyncio.run(manager.fire_post_session_close(_ctx()))
    assert done == ["slow"]


def test_the_sessions_own_followups_of_today_are_not_offered_for_resolving(tmp_path):
    now = datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=9)
    msgs = [_msg("user", "please fix the parser", now), _msg("assistant", "Fixed.", now)]
    first = dict(ANSWER, next_items=["Release 2.4"])
    _run(tmp_path, msgs, answer=first)
    (row,) = _rows(tmp_path, date.today())
    own_id = row["next_items"][0]["next_id"]
    prompts = _run(tmp_path, msgs + [_msg("user", "anything else?", now), _msg("assistant", "No.", now)],
                   answer=ANSWER)
    assert own_id not in prompts[0]


def test_a_short_session_with_a_model_summary_is_kept(tmp_path):
    # no deliverables, no files, a short topic: still a real record when the model
    # wrote a sentence or a follow-up
    now = datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=9)
    msgs = [_msg("user", "status?", now), _msg("assistant", "Green.", now)]
    answer = dict(ANSWER, deliverables=[], summary_sentence="Checked the build: green.")
    _run(tmp_path, msgs, answer=answer)
    (row,) = _rows(tmp_path, date.today())
    assert row["summary_sentence"] == "Checked the build: green."
