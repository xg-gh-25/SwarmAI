"""Compaction summaries: capture → clean → store → show → re-inject.

What is tested:
1. ``extract_summary``: the CLI's raw ``<analysis>…<summary>…`` shape → the
   summary body; leak text, glitch opener word and non-strings handled; capped.
2. Leak cleaning of the CLI transcript's compact-summary record (a USER record
   the model wrote, replayed by ``--resume``) — and ordinary user text untouched.
3. Hook → boundary hand-off through session_context (incl. the late-hook and
   stale-row cases), the boundary event's ``compaction`` record, and the read
   loop persisting it.
4. DB: v12 table + methods; the migration does not skip a failed v11.
5. History splice: placement after the merged bubble, page bounds, never first.
6. Cold resume: the dated "Compaction Summary" section, cache key, cleaning.
7. Summary instructions: the settings hook command prints exactly the text.

Methodology: real functions on real inputs (temp SQLite DB, temp transcript
files, a real ``sh`` for the hook command); leak fixtures are built with
``tc()`` so no literal tool-call markup sits in this file.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import compact_summary as cs
from core.leak_transcript import clean_transcript_leaks
from tests._tool_markup import tc

LEAK = tc("count\n‹invoke name=\"Bash\">\n‹parameter name=\"command\">ls‹/parameter>\n‹/invoke>")
RAW = "<analysis>private reasoning</analysis>\n\n<summary>\n1. **Primary Request**\n   rename foo\n</summary>"


# ── 1. extract_summary ──────────────────────────────────────────────────────

def test_extract_keeps_summary_body_drops_analysis():
    assert cs.extract_summary(RAW) == "1. **Primary Request**\n   rename foo"


def test_extract_without_tags_keeps_text_and_rejects_non_strings():
    assert cs.extract_summary("  plain summary  ") == "plain summary"
    assert cs.extract_summary(None) == "" and cs.extract_summary({"a": 1}) == ""


def test_extract_strips_leak_and_glitch_word():
    out = cs.extract_summary(f"<summary>Done: tests pass.\n\n{LEAK}\n\ncore 跑测试。</summary>")
    assert "<invoke" not in out and "<parameter" not in out
    assert out.startswith("Done: tests pass.") and "跑测试。" in out
    assert "core 跑" not in out


def test_extract_unclosed_analysis_drops_the_rest_and_caps():
    assert cs.extract_summary("<summary>x</summary><analysis>never closed") == "x"
    # Non-repetitive filler: a repeated word is (correctly) collapsed as a degenerate run.
    big = cs.extract_summary("<summary>" + " ".join(f"w{i}" for i in range(12_000)) + "</summary>")
    assert len(big) <= cs.MAX_SUMMARY_CHARS + 40 and big.endswith("[summary truncated]")


# ── 2. transcript compact-summary record ────────────────────────────────────

def _write(tmp_path, recs):
    p = tmp_path / "t.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in recs),
                 encoding="utf-8")
    return p


def test_transcript_summary_record_is_cleaned_string_and_list(tmp_path):
    summ_str = {"type": "user", "isCompactSummary": True, "isVisibleInTranscriptOnly": True,
                "message": {"role": "user", "content": f"Summary:\nok\n\n{LEAK}"}}
    summ_list = {"type": "user", "isCompactSummary": True,
                 "message": {"role": "user", "content": [{"type": "text", "text": f"a\n\n{LEAK}"}]}}
    plain_user = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": f"q\n\n{LEAK}"}]}}
    p = _write(tmp_path, [summ_str, summ_list, plain_user])
    assert clean_transcript_leaks(p) == 2
    out = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]
    assert "<invoke" not in out[0]["message"]["content"] and out[0]["message"]["content"].startswith("Summary:")
    assert out[0]["isCompactSummary"] is True and out[0]["isVisibleInTranscriptOnly"] is True
    assert "<invoke" not in out[1]["message"]["content"][0]["text"]
    assert out[2] == plain_user  # a real user message is never rewritten
    assert clean_transcript_leaks(p) == 0  # idempotent


# ── 3. hook → boundary hand-off, event record, read-loop persist ────────────

@pytest.mark.asyncio
async def test_post_compact_hook_stashes_clean_summary_for_the_boundary():
    from core.hook_builder import build_hooks

    pm = MagicMock()
    pm.is_command_approved = MagicMock(return_value=False)
    ctx: dict = {"sdk_session_id": "s"}
    hooks, _, _ = await build_hooks(
        agent_config={"enable_tool_logging": False, "global_user_mode": True},
        enable_skills=False, enable_mcp=False, resume_session_id=None,
        session_context=ctx, permission_manager=pm,
    )
    for matcher in hooks["PostCompact"]:
        for fn in matcher.hooks:
            assert await fn({"hook_event_name": "PostCompact", "trigger": "auto",
                             "compact_summary": f"<summary>ok\n\n{LEAK}</summary>"}, None, None) == {}
    assert cs.take_summary(ctx) == "ok"
    assert cs.take_summary(ctx) == ""  # one summary per compaction


@pytest.mark.asyncio
async def test_late_hook_updates_the_stored_row_but_not_a_stale_one():
    upd = AsyncMock(return_value=True)
    fake_db = MagicMock(update_compaction_summary=upd)
    with patch.dict(sys.modules, {"database": type("M", (), {"db": fake_db})}):
        ctx: dict = {}
        cs.mark_unsummarized(ctx, "rec-1")
        await cs.on_post_compact(ctx, "<summary>late</summary>")
        upd.assert_awaited_once_with("rec-1", "late")
        assert cs.take_summary(ctx) == ""
        # An old unsummarized row (its hook never fired) must not take the
        # NEXT compaction's summary.
        ctx[cs._UNSUMMARIZED_KEY] = ("old", time.monotonic() - cs._LATE_WINDOW_S - 1)
        await cs.on_post_compact(ctx, "<summary>next</summary>")
        assert upd.await_count == 1 and cs.take_summary(ctx) == "next"


def _unit():
    from core.session_unit import SessionState, SessionUnit
    unit = SessionUnit(session_id="sess-cs", agent_id="default")
    unit.state = SessionState.STREAMING
    unit._hook_session_context = {"_compact_notice_pending": True}
    return unit


BOUNDARY = {"compact_metadata": {"trigger": "auto", "pre_tokens": 750_361,
                                 "post_tokens": 12_833, "duration_ms": 54_523}}


def test_boundary_event_carries_the_record_with_summary():
    from core.streaming_orchestrator import StreamingOrchestrator
    unit = _unit()
    unit._hook_session_context[cs._PENDING_KEY] = "the summary"
    evt = StreamingOrchestrator(unit)._compaction_events("compact_boundary", BOUNDARY)[0]
    rec = evt["compaction"]
    assert set(rec) == set(cs._WIRE_KEYS)
    assert (rec["pre_tokens"], rec["post_tokens"], rec["duration_ms"]) == (750_361, 12_833, 54_523)
    assert rec["summary"] == "the summary" and rec["trigger"] == "auto" and rec["id"]
    assert cs._UNSUMMARIZED_KEY not in unit._hook_session_context


def test_boundary_without_summary_marks_the_row_for_a_late_hook():
    from core.streaming_orchestrator import StreamingOrchestrator
    unit = _unit()
    evt = StreamingOrchestrator(unit)._compaction_events("compact_boundary", BOUNDARY)[0]
    assert evt["compaction"]["summary"] == ""
    assert unit._hook_session_context[cs._UNSUMMARIZED_KEY][0] == evt["compaction"]["id"]


@pytest.mark.asyncio
async def test_read_loop_persists_the_compaction_before_yielding_it():
    from claude_agent_sdk import SystemMessage
    from core import permission_manager as _pm_mod
    from core.streaming_orchestrator import StreamingOrchestrator

    def _sys(subtype, **data):
        return SystemMessage(subtype=subtype, data={"type": "system", "subtype": subtype, **data})

    class _Script:
        def __init__(self, msgs):
            self._m = list(msgs)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._m:
                return self._m.pop(0)
            raise RuntimeError("end")

    unit = _unit()
    unit._hook_session_context[cs._PENDING_KEY] = "S"
    unit._streaming_start_time = unit._last_event_time = time.time()
    unit._client = MagicMock()
    unit._client.receive_response = lambda: _Script(
        [_sys("init", session_id="sdk-1"), _sys("compact_boundary", **BOUNDARY)])
    stored: list = []

    async def _persist(sid, rec):
        stored.append((sid, rec))
        return True

    events = []
    with contextlib.nullcontext(), \
            patch("core.compact_summary.persist", _persist):
        with pytest.raises(Exception):
            async for ev in StreamingOrchestrator(unit)._read_formatted_response():
                events.append(ev)
    done = [e for e in events if e["type"] == "context_compacted"]
    assert len(done) == 1 and stored == [("sess-cs", done[0]["compaction"])]


# ── 4. DB table + migration ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_db_compaction_roundtrip(tmp_path):
    from database.sqlite import SQLiteDatabase
    db = SQLiteDatabase(str(tmp_path / "c.db"))
    await db.initialize()
    r1 = {**cs.build_record(BOUNDARY["compact_metadata"], ""), "created_at": "2026-10-18T12:00:00.000001"}
    r2 = {**cs.build_record({"trigger": "manual"}, "second"), "created_at": "2026-10-18T13:00:00.000001"}
    assert await db.insert_compaction("s1", r1) and await db.insert_compaction("s1", r2)
    await db.insert_compaction("s2", {**cs.build_record(None, "other"), "created_at": "2026-10-18T14:00:00"})
    assert await db.update_compaction_summary(r1["id"], "first")
    rows = await db.list_compactions("s1")
    assert [r["summary"] for r in rows] == ["first", "second"]
    assert rows[0]["pre_tokens"] == 750_361 and rows[1]["trigger"] == "manual"
    assert (await db.get_latest_compaction("s1"))["id"] == r2["id"]
    assert await db.count_compactions("s1") == 2
    assert await db.delete_compactions_by_session("s1") == 2
    assert await db.get_latest_compaction("s1") is None and await db.count_compactions("s2") == 1


@pytest.mark.asyncio
async def test_v12_does_not_skip_a_failed_v11(tmp_path):
    """v11 leaves user_version at 10 on failure so the next start retries it;
    v12 must not bump past it (the fast path would then never retry v11)."""
    import aiosqlite
    from database.sqlite import SQLiteDatabase
    db = SQLiteDatabase(str(tmp_path / "m.db"))
    await db.initialize()
    # Run only the v12 block (current_version=11) at two stored versions.
    async with aiosqlite.connect(str(tmp_path / "m.db")) as conn:
        await conn.execute("PRAGMA user_version = 11")
        await conn.commit()
        await db._run_versioned_migrations(conn, 11)
        # v12 bumps 11 -> 12, then v13, v14 and v15 chain on.
        assert (await (await conn.execute("PRAGMA user_version")).fetchone())[0] == 17
        await conn.execute("PRAGMA user_version = 10")
        await conn.execute("DROP TABLE compactions")
        await conn.commit()
        await db._run_versioned_migrations(conn, 11)  # v12 only (v11 not re-run)
        assert (await (await conn.execute("PRAGMA user_version")).fetchone())[0] == 10
        assert await (await conn.execute(
            "SELECT name FROM sqlite_master WHERE name='compactions'")).fetchone()


# ── 5. history splice ───────────────────────────────────────────────────────

def _m(i, role, at):
    return {"id": f"m{i}", "session_id": "s", "role": role, "content": [], "created_at": at}


def _c(cid, at, summary="S"):
    return {"id": cid, "created_at": at, "trigger": "auto", "pre_tokens": 750_000,
            "post_tokens": 12_000, "duration_ms": 55_000, "summary": summary}


def test_splice_after_the_turn_bubble_with_record_metadata():
    from routers.chat import _merge_consecutive_assistant_messages
    rows = [_m(1, "user", "2026-10-18T10:00:00"), _m(2, "assistant", "2026-10-18T10:00:05"),
            _m(3, "assistant", "2026-10-18T10:09:00"),  # same turn, after the compaction
            _m(4, "user", "2026-10-18T10:20:00")]
    merged = _merge_consecutive_assistant_messages(rows)
    out = cs.splice_into_history("s", merged, [_c("c1", "2026-10-18T10:05:00")], upper=None)
    assert [m["id"] for m in out] == ["m1", "m2", "compact-c1", "m4"]
    sys_msg = out[2]
    assert sys_msg["role"] == "system" and sys_msg["metadata"]["compaction"]["summary"] == "S"
    assert "750K → 12K tokens in 55s" in sys_msg["content"][0]["text"]


def test_splice_page_bounds_and_never_first():
    page = [_m(1, "user", "2026-10-18T10:00:00"), _m(2, "assistant", "2026-10-18T10:01:00")]
    comps = [_c("early", "2026-10-18T09:00:00"), _c("in", "2026-10-18T10:00:30"),
             _c("after", "2026-10-18T11:00:00")]
    older_page = cs.splice_into_history("s", page, comps, upper="2026-10-18T10:30:00")
    assert [m["id"] for m in older_page] == ["m1", "compact-in", "m2"]
    newest = cs.splice_into_history("s", page, comps, upper=None)
    assert [m["id"] for m in newest] == ["m1", "compact-in", "m2", "compact-after"]
    assert cs.splice_into_history("s", [], comps, upper=None) == []


def test_splice_every_compaction_lands_on_exactly_one_page():
    """Raw rows U7 A7a [C1] A7b | [C2] A8 (| = page cut, A8 is the cursor row).
    The merged A7 bubble carries A7a's time, so the old `<= last` bound lost C1;
    a compaction in the gap before the cut (C2) belongs to the older page."""
    from routers.chat import _merge_consecutive_assistant_messages
    older_raw = [_m(7, "user", "2026-10-18T10:00:00"), _m(71, "assistant", "2026-10-18T10:01:00"),
                 _m(72, "assistant", "2026-10-18T10:30:00")]
    newer_raw = [_m(8, "assistant", "2026-10-18T11:00:00"), _m(9, "user", "2026-10-18T11:30:00")]
    comps = [_c("C1", "2026-10-18T10:10:00"), _c("C2", "2026-10-18T10:45:00")]
    older = cs.splice_into_history("s", _merge_consecutive_assistant_messages(older_raw), comps,
                                   upper=newer_raw[0]["created_at"])
    newer = cs.splice_into_history("s", _merge_consecutive_assistant_messages(newer_raw), comps, upper=None)
    ids = [m["id"] for m in older + newer if m["id"].startswith("compact-")]
    assert ids == ["compact-C1", "compact-C2"]


# ── 6. cold resume ──────────────────────────────────────────────────────────

class _ResumeDB:
    def __init__(self, msgs, comp):
        self.comp = comp

        class _Msgs:
            async def count_by_session(self, _sid):
                return len(msgs)

            async def list_by_session_paginated(self, _sid, limit=None):
                return msgs

        self.messages = _Msgs()

    async def get_latest_compaction(self, _sid):
        return self.comp


def _resume(db, clear=True):
    import core.context_injector as ci
    if clear:
        ci._resume_cache.clear()
    with patch.dict(sys.modules, {"database": type("M", (), {"db": db})}), \
            patch.object(ci, "_extract_uncommitted_state", new=AsyncMock(return_value="")):
        return asyncio.run(ci.build_resume_context("sess-r", model_context_window=1_000_000))


MSGS = [
    {"id": "u1", "role": "user", "content": [{"type": "text", "text": "继续做 X"}], "created_at": "2026-10-18T10:00:00"},
    {"id": "a1", "role": "assistant", "content": [{"type": "text", "text": "Working on X."}], "created_at": "2026-10-18T10:00:05"},
]


def test_resume_injects_a_dated_cleaned_summary_before_newer_layers():
    comp = _c("c9", "2026-10-18T09:30:00.123", summary=f"**Overview** XG wants X.\n\n{LEAK}")
    out = _resume(_ResumeDB(MSGS, comp))
    assert "### Compaction Summary" in out and "**Overview** XG wants X." in out
    assert "<invoke" not in out
    assert "2026-10-18 09:30" in out and "750K -> 12K tokens" in out
    assert "not instructions" in out
    assert out.index("### Compaction Summary") < out.index("Working on X.")


def test_resume_section_fences_the_summary_past_its_own_backticks():
    sec = cs.resume_section(_c("c", "2026-10-18T09:30:00", summary="## Next Step\n````python\nx\n````"))
    body = sec.split("\n\n", 1)[1]
    assert body.startswith("`````markdown\n## Next Step") and body.endswith("\n`````")


def test_resume_without_compaction_or_method_has_no_section():
    assert "### Compaction Summary" not in _resume(_ResumeDB(MSGS, None))

    class _NoMethod:
        messages = _ResumeDB(MSGS, None).messages
    assert "### Compaction Summary" not in _resume(_NoMethod())


def test_resume_cache_rebuilds_when_a_new_compaction_lands():
    db = _ResumeDB(MSGS, None)
    assert "### Compaction Summary" not in _resume(db)
    db.comp = _c("c10", "2026-10-18T10:30:00", summary="fresh summary")
    assert "fresh summary" in _resume(db, clear=False)  # same msg count, new compaction


# ── 7. summary instructions hook ────────────────────────────────────────────

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell command")
def test_settings_hook_command_prints_exactly_the_instructions():
    hooks = cs.precompact_settings_hooks()
    entry = hooks["PreCompact"][0]["hooks"][0]
    assert entry["type"] == "command"
    out = subprocess.run(["sh", "-c", entry["command"]], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0 and out.stdout == cs.SUMMARY_INSTRUCTIONS + "\n"
    assert "<invoke" not in cs.SUMMARY_INSTRUCTIONS and "<parameter" not in cs.SUMMARY_INSTRUCTIONS


def test_settings_hook_is_off_on_windows(monkeypatch):
    monkeypatch.setattr(cs.sys, "platform", "win32")
    assert cs.precompact_settings_hooks() == {}


# ── history endpoint: splice + ETag ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_history_endpoint_splices_and_etag_counts_compactions():
    import routers.chat as chat_router

    rows = [_m(1, "user", "2026-10-18T10:00:00"), _m(2, "assistant", "2026-10-18T10:00:05"),
            _m(3, "user", "2026-10-18T10:20:00")]
    comps = [_c("c1", "2026-10-18T10:05:00")]
    fake_db = MagicMock()
    fake_db.messages.count_by_session = AsyncMock(return_value=len(rows))
    fake_db.messages.list_by_session = AsyncMock(return_value=rows)
    fake_db.transcript_rev = AsyncMock(return_value=7)
    fake_db.list_compactions = AsyncMock(return_value=comps)
    req = MagicMock()
    req.headers = {}
    with patch.object(chat_router, "db", fake_db), \
            patch.object(chat_router.session_manager, "get_session", AsyncMock(return_value=object())):
        resp = await chat_router.get_session_messages("s", req, limit=None, before_id=None)
        body = json.loads(resp.body)
        assert [m["id"] for m in body] == ["m1", "m2", "compact-c1", "m3"]
        # ...:-: = no permission prompt is live (core/permission_prompt etag_part)
        assert resp.headers["ETag"] == '"s:r7:-:all"'
        req.headers = {"if-none-match": '"s:r7:-:all"'}
        assert (await chat_router.get_session_messages("s", req, limit=None, before_id=None)).status_code == 304
        # Older page: bounded by the cursor row, so a compaction after it is not here.
        fake_db.messages.list_by_session_paginated = AsyncMock(return_value=rows[:2])
        fake_db.messages.get = AsyncMock(return_value={"created_at": "2026-10-18T10:04:00"})
        req.headers = {}
        body = json.loads((await chat_router.get_session_messages("s", req, limit=50, before_id="m3")).body)
        assert [m["id"] for m in body] == ["m1", "m2"]
        # A failing compaction lookup must not break history, and the response
        # must not carry an ETag that already counts the missing rows.
        fake_db.list_compactions = AsyncMock(side_effect=RuntimeError("no table"))
        resp = await chat_router.get_session_messages("s", req, limit=None, before_id=None)
        assert [m["id"] for m in json.loads(resp.body)] == ["m1", "m2", "m3"]
        assert "ETag" not in resp.headers


@pytest.mark.asyncio
async def test_db_signature_changes_when_a_late_summary_lands(tmp_path):
    from database.sqlite import SQLiteDatabase
    db = SQLiteDatabase(str(tmp_path / "sig.db"))
    await db.initialize()
    rec = cs.build_record(None, "")
    await db.insert_compaction("s", rec)
    before = await db.compaction_signature("s")
    await db.update_compaction_summary(rec["id"], "late summary")
    assert await db.compaction_signature("s") != before


@pytest.mark.asyncio
async def test_hook_drops_a_leftover_summary_and_retries_a_late_update():
    ctx: dict = {cs._PENDING_KEY: "left by an interrupted compaction"}
    await cs.on_post_compact(ctx, "")  # this compaction produced no text
    assert cs.take_summary(ctx) == ""
    upd = AsyncMock(side_effect=[False, True])  # row inserted just after the first try
    fake_db = MagicMock(update_compaction_summary=upd)
    with patch.dict(sys.modules, {"database": type("M", (), {"db": fake_db})}), \
            patch.object(cs, "_LATE_RETRY_S", 0.01):
        cs.mark_unsummarized(ctx, "rec-2")
        await cs.on_post_compact(ctx, "<summary>late</summary>")
    assert upd.await_count == 2


@pytest.mark.asyncio
async def test_manual_compact_persists_and_clears_the_fallback_flag():
    """SessionUnit.compact() (manual /compact, no SSE): the record is stored and
    the PreCompact fallback flag is cleared, so the next turn adds no second divider."""
    from claude_agent_sdk import SystemMessage
    from core.session_unit import SessionState

    unit = _unit()
    unit.state = SessionState.IDLE
    unit._hook_session_context = {"_compact_notice_pending": True, cs._PENDING_KEY: "manual S"}

    async def _resp():
        yield SystemMessage(subtype="compact_boundary", data={
            "type": "system", "subtype": "compact_boundary", **BOUNDARY})

    unit._client = MagicMock()
    unit._client.query = AsyncMock()
    unit._client.receive_response = _resp
    stored: list = []

    async def _persist(sid, rec):
        stored.append((sid, rec))
        return True

    with patch("core.compact_summary.persist", _persist):
        result = await unit.compact()
    assert result["success"] is True
    assert unit._hook_session_context["_compact_notice_pending"] is False
    assert stored and stored[0][0] == "sess-cs" and stored[0][1]["summary"] == "manual S"


# ── retention: compactions do not outlive their messages ───────────────────

@pytest.mark.asyncio
async def test_compactions_expire_with_the_messages_ttl_and_with_their_last_message(tmp_path):
    from datetime import datetime, timedelta
    from database.sqlite import SQLiteDatabase, SQLiteMessagesTable
    db = SQLiteDatabase(str(tmp_path / "ttl.db"))
    await db.initialize()
    await db.messages.put({"session_id": "live", "role": "user", "content": [{"type": "text", "text": "q"}]})
    old_at = (datetime.now() - timedelta(seconds=SQLiteMessagesTable.TTL_SECONDS + 3600)).isoformat()
    recent = {**cs.build_record(None, "keep"), "created_at": datetime.now().isoformat()}
    stale = {**cs.build_record(None, "past the TTL"), "created_at": old_at}
    orphan = {**cs.build_record(None, "its messages are gone"), "created_at": datetime.now().isoformat()}
    await db.insert_compaction("live", recent)
    await db.insert_compaction("live", stale)
    await db.insert_compaction("gone", orphan)
    assert await db.cleanup_expired_compactions() == 2
    assert [r["id"] for r in await db.list_compactions("live")] == [recent["id"]]
    assert await db.list_compactions("gone") == []
    assert await db.cleanup_expired_compactions() == 0  # idempotent


@pytest.mark.asyncio
async def test_maintenance_runs_compaction_cleanup_even_if_message_cleanup_fails():
    from core.lifecycle_manager import LifecycleManager
    mgr = LifecycleManager.__new__(LifecycleManager)
    fake_db = MagicMock()
    fake_db.cleanup_expired_messages = AsyncMock(side_effect=RuntimeError("locked"))
    fake_db.cleanup_expired_compactions = AsyncMock(return_value=3)
    with patch("database.db", fake_db):
        await mgr._cleanup_expired_messages()  # must not raise
    fake_db.cleanup_expired_compactions.assert_awaited_once()
    fake_db.cleanup_expired_messages = AsyncMock(return_value=1)
    fake_db.cleanup_expired_compactions = AsyncMock(side_effect=RuntimeError("no table"))
    with patch("database.db", fake_db):
        await mgr._cleanup_expired_messages()  # a compaction failure is contained too
