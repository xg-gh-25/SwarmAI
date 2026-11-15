"""Tests for request-size overflow: classification, transcript media shedding, recovery.

What is tested
--------------
1. ``core.session_utils`` — an overflow text ("API Error: 400 Input is too long." and
   the other forms) maps to CONTEXT_TOO_LARGE with honest text, is never retriable,
   and other errors keep their code and text.
2. ``core.transcript_media`` on real files in tmp_path — the live-history media
   measure and the shed (backup byte for byte, oldest media first, everything else
   kept, atomic, fail-soft).
3. The orchestrator trigger — an overflow result raises the shed marker only on the
   eligible primary stream; otherwise the turn ends with the terminal error.
4. ``RetryManager._handle_media_shed_restart`` on a REAL SessionUnit, mocking only the
   SDK boundary (_crash_to_cold_async, _spawn, stream_query).
5. The proactive path — the post-turn measure sets ``_media_shed_due`` only for a
   large, grown transcript, and send() sheds before the spawn.

Key invariants
--------------
- INV1: an overflow never yields "Send your message again to continue."
- INV2: a shed removes only image/document blocks; every text, tool_use id and
  tool_result id survives; the backup is byte-identical to the pre-image.
- INV3: the restart kills BEFORE shedding, resumes the SAME sdk session id with the
  shed continuation prompt, runs at most once per user message, and ends in one
  terminal CONTEXT_TOO_LARGE error when nothing could be removed.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import transcript_media as tm
from core.session_utils import (
    CONTEXT_TOO_LARGE_CODE,
    MEDIA_SHED_MARKER,
    _is_retriable_error,
    _sanitize_sdk_error,
    is_context_overflow,
    sdk_error_code,
)

SID = "0b5f7b2d-1111-2222-3333-444455556666"
GENERIC = "Send your message again to continue."

OVERFLOW_TEXTS = [
    "API Error: 400 Input is too long.",
    "API Error: 400 Input is too long for requested model.",
    'API Error: 400 {"type":"error","error":{"type":"invalid_request_error",'
    '"message":"prompt is too long: 1048576 tokens > 1000000 maximum"}}',
    "Request too large (max 32MB). Run /compact.",
    '{"error":{"type":"request_too_large"}}',
    "input length and `max_tokens` exceed context limit: 990000 + 32000 > 1000000",
    "bedrock thinking gateway: request too large",
]


# ── 1. classification ───────────────────────────────────────────────────────

@pytest.mark.parametrize("text", OVERFLOW_TEXTS)
def test_overflow_texts_classified(text):
    assert is_context_overflow(text)
    friendly, suggested = _sanitize_sdk_error(text)
    assert sdk_error_code(text) == CONTEXT_TOO_LARGE_CODE
    assert "too large" in friendly
    assert GENERIC not in (suggested or "") and "send your message again" not in friendly.lower()
    assert "new chat tab" in (suggested or "")


@pytest.mark.parametrize("text", OVERFLOW_TEXTS + [
    # an overflow text that also names a retriable word must still not retry
    "API Error: 400 Input is too long. (capacity)",
])
def test_overflow_never_retriable(text):
    assert _is_retriable_error(text) is False
    assert _is_retriable_error(text, "Traceback ...") is False


@pytest.mark.parametrize("text,code", [
    ("API Error: 400 due to tool use concurrency issues.", "SDK_ERROR"),
    ("API Error: 401 Unauthorized", "CREDENTIALS_EXPIRED"),
    ("Command failed with exit code -9", "SDK_ERROR"),
])
def test_other_errors_unchanged(text, code):
    assert not is_context_overflow(text)
    assert sdk_error_code(text) == code
    assert _sanitize_sdk_error(text)[0] != "This conversation is too large for the model's request limit."
    # transient errors stay retriable
    assert _is_retriable_error("API Error: 529 overloaded") is True
    assert not _is_retriable_error(f"{MEDIA_SHED_MARKER} (session_id=x)")


# ── 2. transcript media ─────────────────────────────────────────────────────

def _img(n: int) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                        "data": "A" * n}}


def _read_use(tid: str, path: str) -> dict:
    return {"type": "assistant", "uuid": f"a-{tid}",
            "message": {"id": f"m-{tid}", "role": "assistant", "content": [
                {"type": "text", "text": f"look at {path}"},
                {"type": "tool_use", "id": tid, "name": "Read", "input": {"file_path": path}}]}}


def _read_result(tid: str, n: int) -> dict:
    return {"type": "user", "uuid": f"u-{tid}",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": [_img(n)]}]}}


def _write(tmp_path, monkeypatch, records, tail="") -> Path:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    d = tmp_path / "projects" / "-Users-x-proj"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{SID}.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n"
                         for r in records) + tail)
    return p


def _session(tmp_path, monkeypatch, sizes, tail=""):
    recs = [{"type": "user", "uuid": "u0", "message": {"role": "user", "content": "hi"}}]
    for i, n in enumerate(sizes):
        recs += [_read_use(f"t{i}", f"/tmp/shot{i}.png"), _read_result(f"t{i}", n)]
    return _write(tmp_path, monkeypatch, recs, tail)


def _ids_and_texts(path: Path):
    ids, texts, imgs = [], [], 0
    for line in path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        for b in (rec.get("message") or {}).get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                ids.append(("use", b["id"]))
            if b.get("type") == "tool_result":
                ids.append(("result", b["tool_use_id"]))
                for sub in b.get("content") or []:
                    imgs += sub.get("type") == "image"
                    if sub.get("type") == "text":
                        texts.append(sub["text"])
            if b.get("type") == "text":
                texts.append(b["text"])
    return ids, texts, imgs


def test_shed_keeps_everything_but_media(tmp_path, monkeypatch):
    torn = '{"type":"user","message":{"content":[{"type":"image"'  # killed writer
    p = _session(tmp_path, monkeypatch, [100, 100, 100, 100], tail=torn)
    before = p.read_bytes()
    ids_before, texts_before, _ = _ids_and_texts(p)

    r = tm.shed_transcript_media(p, target_bytes=150)

    assert r.removed_blocks == 3 and r.removed_bytes == 300 and r.remaining_bytes == 100
    assert r.backup_path is not None and r.backup_path.read_bytes() == before
    assert not r.backup_path.name.endswith(".jsonl")
    ids_after, texts_after, imgs_after = _ids_and_texts(p)
    assert ids_after == ids_before, "every tool_use / tool_result id kept"
    assert set(texts_before) <= set(texts_after), "every text kept"
    assert imgs_after == 1, "the most recent image survives"
    markers = [t for t in texts_after if "removed from this conversation" in t]
    assert [("/tmp/shot%d.png" % i) in m for i, m in enumerate(markers)] == [True] * 3
    assert p.read_text().endswith(torn), "a torn last line is kept byte for byte"
    lines_before = before.decode().splitlines(keepends=True)
    lines_after = p.read_text().splitlines(keepends=True)
    assert len(lines_after) == len(lines_before)
    assert lines_after[-3:] == lines_before[-3:], "lines without removed media unchanged"
    assert not list(p.parent.glob("*.tmp"))


def test_shed_under_target_does_not_rewrite(tmp_path, monkeypatch):
    p = _session(tmp_path, monkeypatch, [10, 10])
    mtime, before = p.stat().st_mtime_ns, p.read_bytes()
    r = tm.shed_transcript_media(p, target_bytes=1000)
    assert r.removed_blocks == 0 and r.backup_path is None and r.remaining_bytes == 20
    assert p.read_bytes() == before and p.stat().st_mtime_ns == mtime
    assert not list(p.parent.glob("*.bak"))


def test_shed_counts_only_live_history(tmp_path, monkeypatch):
    recs = [_read_use("t0", "/a.png"), _read_result("t0", 500),
            {"type": "system", "subtype": "compact_boundary", "uuid": "cb"},
            _read_use("t1", "/b.png"), _read_result("t1", 50),
            dict(_read_result("t2", 900), isSidechain=True)]
    p = _write(tmp_path, monkeypatch, recs)
    assert tm.live_media_bytes(p) == 50
    assert tm.shed_transcript_media(p, target_bytes=100).removed_blocks == 0


def test_shed_is_fail_soft(tmp_path):
    r = tm.shed_transcript_media(tmp_path / "missing.jsonl", target_bytes=0)
    assert r.removed_blocks == 0 and r.error
    assert tm.live_media_bytes(tmp_path / "missing.jsonl") is None


def test_shed_write_failure_leaves_file(tmp_path, monkeypatch):
    p = _session(tmp_path, monkeypatch, [100, 100])
    before = p.read_bytes()

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(tm.os, "replace", _boom)
    r = tm.shed_transcript_media(p, target_bytes=0)
    assert r.removed_blocks == 0 and "disk full" in r.error
    assert p.read_bytes() == before
    assert not list(p.parent.glob("*.tmp"))


# ── 3. orchestrator trigger ─────────────────────────────────────────────────

def _error_result(text="API Error: 400 Input is too long."):
    from claude_agent_sdk import ResultMessage

    return ResultMessage(subtype="success", duration_ms=10, duration_api_ms=10,
                         is_error=True, num_turns=1, session_id="sdk-test",
                         result=text, usage={"input_tokens": 0, "output_tokens": 0},
                         total_cost_usd=0.0)


def _orch(eligible: bool):
    from tests.test_tool_call_leak_guard import _make_orchestrator

    orch = _make_orchestrator()
    p = orch._parent
    p._streaming_start_time = 0.0
    p._media_shed_eligible = eligible
    p._user_stopped_current_turn = False
    p.pid = None
    p._lifecycle_response_count = 0
    p._model_name = "test-model"
    p._emit_post_stream_metadata = lambda *a, **k: iter(())
    return orch


@pytest.mark.parametrize("eligible", [True, False])
def test_orchestrator_raises_marker_only_when_eligible(eligible):
    from tests.test_tool_call_leak_guard import _drive

    orch = _orch(eligible)
    events, raised = asyncio.run(_drive(orch, [_error_result()]))
    if eligible:
        assert raised is not None and MEDIA_SHED_MARKER in str(raised)
        assert orch._parent._media_shed_eligible is False, "one-shot"
        assert not [e for e in events if e.get("type") == "error"]
    else:
        assert raised is None or MEDIA_SHED_MARKER not in str(raised)
        errors = [e for e in events if e.get("type") == "error"]
        assert errors and errors[0]["code"] == CONTEXT_TOO_LARGE_CODE
        assert GENERIC not in json.dumps(errors)


# ── 4. restart handler on a real SessionUnit ────────────────────────────────

def _unit(calls, stream_events=(), spawn_exc=None):
    from core.session_unit import SessionState, SessionUnit

    u = SessionUnit(session_id="test-media-shed", agent_id="default")
    u._sdk_session_id = SID

    async def _crash(*a, clear_identity=False, **k):
        calls.append(("crash", clear_identity))
        u.state = SessionState.COLD
        if clear_identity:
            u._sdk_session_id = None

    async def _spawn(options, config=None):
        calls.append(("spawn", options.resume))
        if spawn_exc:
            raise spawn_exc
        u.state = SessionState.STREAMING

    async def _stream(query):
        calls.append(("query", query))
        for ev in stream_events:
            yield ev

    u._crash_to_cold_async = _crash
    u._spawn = _spawn
    u._streaming_orchestrator.stream_query = _stream
    return u


def _opts():
    from claude_agent_sdk import ClaudeAgentOptions

    return ClaudeAgentOptions()


async def _collect(agen):
    return [ev async for ev in agen]


async def test_shed_restart_strips_and_resumes(tmp_path, monkeypatch):
    from core.streaming_orchestrator import StreamingOrchestrator

    big = tm.MEDIA_SHED_TARGET_BYTES // 2 + 1
    p = _session(tmp_path, monkeypatch, [big, big, big])
    calls, order = [], []
    real = tm.shed_transcript_media

    def _spy(path, *a, **k):
        order.append([c[0] for c in calls])
        return real(path, *a, **k)

    monkeypatch.setattr(tm, "shed_transcript_media", _spy)
    u = _unit(calls, stream_events=[{"type": "assistant", "content": []}])

    events = await _collect(u._retry_manager._handle_media_shed_restart(_opts(), None))

    assert order == [["crash"]], "the CLI is stopped BEFORE the file is rewritten"
    assert calls[0] == ("crash", False), "identity kept for --resume"
    assert calls[1] == ("spawn", SID), "resumes the SAME sdk session"
    assert calls[2] == ("query", StreamingOrchestrator._MEDIA_SHED_CONTINUATION_PROMPT)
    notice = [e for e in events if e.get("type") == "reconnecting"]
    assert notice and notice[0]["reason"] == "context_too_large"
    assert "images were removed" in notice[0]["message"]
    assert events[-1] == {"_recovered": True}
    assert u._media_shed_done is True
    assert tm.live_media_bytes(p) <= tm.MEDIA_SHED_TARGET_BYTES
    assert list(p.parent.glob(f"{SID}.jsonl{tm.BACKUP_INFIX}*.bak"))


@pytest.mark.parametrize("setup", ["no_media", "no_transcript"])
async def test_shed_restart_terminal_when_nothing_to_strip(tmp_path, monkeypatch, setup):
    if setup == "no_media":
        p = _write(tmp_path, monkeypatch, [{"type": "user", "uuid": "u0",
                                            "message": {"role": "user", "content": "x" * 50}}])
        before = p.read_bytes()
    else:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    calls = []
    u = _unit(calls)
    events = await _collect(u._retry_manager._handle_media_shed_restart(_opts(), None))
    assert events[-1] == {"_abort": True}
    err = events[-2]
    assert err["type"] == "error" and err["code"] == CONTEXT_TOO_LARGE_CODE
    assert GENERIC not in json.dumps(err)
    assert [c[0] for c in calls] == ["crash"], "no respawn of the same oversized history"
    if setup == "no_media":
        assert p.read_bytes() == before
        assert not list(p.parent.glob("*.bak"))


async def test_shed_restart_spawn_failure_aborts(tmp_path, monkeypatch):
    _session(tmp_path, monkeypatch, [tm.MEDIA_SHED_TARGET_BYTES + 10])
    calls = []
    u = _unit(calls, spawn_exc=RuntimeError("boom"))
    events = await _collect(u._retry_manager._handle_media_shed_restart(_opts(), None))
    assert events[-1] == {"_abort": True}
    assert events[-2].get("type") == "error"
    assert ("crash", True) in calls


# ── 5. proactive shed ───────────────────────────────────────────────────────

async def test_measure_skips_small_or_unchanged(tmp_path, monkeypatch):
    from core.session_unit import SessionUnit

    p = _session(tmp_path, monkeypatch, [100])
    u = SessionUnit(session_id="test-media-measure", agent_id="default")
    u._sdk_session_id = SID
    parsed = []
    real = tm.live_media_bytes
    monkeypatch.setattr(tm, "live_media_bytes", lambda path: parsed.append(path) or real(path))

    await u._measure_transcript_media()  # small file: one stat, no parse
    assert parsed == [] and u._media_shed_due is False

    monkeypatch.setattr(tm, "MEDIA_SHED_TRIGGER_BYTES", 50)
    monkeypatch.setattr(tm, "MIB", 1)
    await u._measure_transcript_media()  # over the trigger: parsed in a thread
    assert parsed == [p] and u._media_shed_due is True

    u._media_shed_due = False
    await u._measure_transcript_media()  # did not grow: no second parse
    assert parsed == [p] and u._media_shed_due is False


async def test_proactive_shed_before_send(tmp_path, monkeypatch):
    from tests.test_session_unit_dead_resume import _make_unit
    from core.session_unit import SessionState

    unit = _make_unit(state=SessionState.COLD, sdk_session_id=SID)
    unit._media_shed_due = True
    order = []

    async def _shed():
        order.append("shed")
        unit._media_shed_due = False

    class _Stop(Exception):
        pass

    async def _spawn_stop(*a, **k):
        order.append("spawn")
        raise _Stop()
        yield {}  # pragma: no cover

    with patch.object(unit, "_shed_transcript_media_if_due", _shed), \
         patch.object(unit, "_ensure_spawned", _spawn_stop), \
         patch.object(unit, "_await_streaming_slot", AsyncMock()):
        with pytest.raises(_Stop):
            async for _ in unit.send("next", MagicMock(), app_session_id="app-sess-456"):
                pass
    assert order == ["shed", "spawn"]


async def test_shed_if_due_recycles_then_sheds(tmp_path, monkeypatch, caplog):
    from core.session_unit import SessionUnit

    big = tm.MEDIA_SHED_TARGET_BYTES // 2 + 1
    p = _session(tmp_path, monkeypatch, [big, big, big])
    u = SessionUnit(session_id="test-media-due", agent_id="default")
    u._sdk_session_id = SID
    u._media_shed_due = True
    calls = []

    async def _crash(*a, clear_identity=False, **k):
        calls.append(("crash", clear_identity, tm.live_media_bytes(p)))

    monkeypatch.setattr(type(u), "is_alive", property(lambda self: True))
    u._crash_to_cold_async = _crash
    u._arm_recovery_checkpoint = AsyncMock()
    with caplog.at_level("WARNING"):
        await u._shed_transcript_media_if_due()
    assert calls == [("crash", False, 3 * big)], "stopped before the rewrite, identity kept"
    timed = [r.getMessage() for r in caplog.records
             if "media_shed_before_send" in r.getMessage() or "transcript_media.shed " in r.getMessage()]
    assert len(timed) == 2 and all("dur_ms=" in m for m in timed), "the pre-send shed is timed"
    assert u._media_shed_due is False
    assert tm.live_media_bytes(p) <= tm.MEDIA_SHED_TARGET_BYTES


# ── 6. review round 1: encoding, file mode, identity change, a raising shed ──

def test_shed_survives_lone_surrogate_and_torn_utf8_tail(tmp_path, monkeypatch):
    p = _session(tmp_path, monkeypatch, [100, 100])
    # a tool output the CLI cut mid-emoji: JSON.stringify saves a lone surrogate escape
    lines = p.read_text().splitlines(keepends=True)
    rec = json.loads(lines[2])
    rec["message"]["content"][0]["content"].append({"type": "text", "text": "cut \ud83d"})
    lines[2] = json.dumps(rec, ensure_ascii=True, separators=(",", ":")) + "\n"
    torn = "{\"type\":\"user\",\"x\":\"中".encode()[:-1]  # half a CJK character
    p.write_bytes("".join(lines).encode() + torn)
    before_tail = p.read_bytes()[-len(torn):]

    assert tm.live_media_bytes(p) == 200
    r = tm.shed_transcript_media(p, target_bytes=150)
    assert r.error is None and r.removed_blocks == 1
    assert p.read_bytes().endswith(before_tail), "torn tail kept byte for byte"
    assert json.loads(p.read_text(errors="surrogateescape").splitlines()[2])
    assert not list(p.parent.glob("*.tmp"))


def test_shed_keeps_transcript_mode(tmp_path, monkeypatch):
    import os
    import stat as _stat

    p = _session(tmp_path, monkeypatch, [100, 100])
    os.chmod(p, 0o600)
    monkeypatch.setattr(os, "umask", os.umask)  # no-op: the real umask stays in force
    assert tm.shed_transcript_media(p, target_bytes=50).removed_blocks == 2
    assert _stat.S_IMODE(p.stat().st_mode) == 0o600


async def test_shed_restart_terminal_when_shed_raises(tmp_path, monkeypatch):
    _session(tmp_path, monkeypatch, [tm.MEDIA_SHED_TARGET_BYTES + 10])

    def _boom(path, *a, **k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(tm, "shed_transcript_media", _boom)
    calls = []
    u = _unit(calls)
    events = await _collect(u._retry_manager._handle_media_shed_restart(_opts(), None))
    assert events[-1] == {"_abort": True}
    assert events[-2]["code"] == CONTEXT_TOO_LARGE_CODE
    assert [c[0] for c in calls] == ["crash"]


async def test_measure_baseline_resets_on_new_session(tmp_path, monkeypatch):
    from core.session_unit import SessionUnit

    p = _session(tmp_path, monkeypatch, [100])
    u = SessionUnit(session_id="test-media-sid", agent_id="default")
    u._sdk_session_id = SID
    u._media_measured_sid = "ffffffff-0000-0000-0000-000000000000"
    u._media_measured_size = 10 ** 9  # the previous session's much larger file
    monkeypatch.setattr(tm, "MEDIA_SHED_TRIGGER_BYTES", 50)
    monkeypatch.setattr(tm, "MIB", 1)
    await u._measure_transcript_media()
    assert u._media_shed_due is True and u._media_measured_sid == SID
    assert u._media_measured_size == p.stat().st_size


# ── 7. backup lifetime and transcript mode in the leak cleaner ──────────────

def test_sweep_removes_only_old_backups_of_gone_transcripts(tmp_path, monkeypatch):
    import os
    import time

    monkeypatch.setattr(tm, "_ORPHAN_SINCE", {})
    d = tmp_path / "projects" / "-Users-x-proj"
    d.mkdir(parents=True)
    live = d / f"{SID}.jsonl"
    live.write_text("{}\n")
    gone = "ffffffff-0000-0000-0000-000000000000"
    dangling = "eeeeeeee-0000-0000-0000-000000000000"
    (d / f"{dangling}.jsonl").symlink_to(tmp_path / "unmounted" / "x.jsonl")
    keep_live = d / f"{SID}.jsonl{tm.BACKUP_INFIX}20261101T000000Z.bak"
    orphan = d / f"{gone}.jsonl{tm.BACKUP_INFIX}20261101T000000Z.bak"
    behind_link = d / f"{dangling}.jsonl{tm.BACKUP_INFIX}20261101T000000Z.bak"
    other = d / "notes.bak"
    ancient = time.time() - 30 * 86400  # copy2 keeps the transcript's old mtime
    for f in (keep_live, orphan, behind_link, other):
        f.write_text("x" * 10)
        os.utime(f, (ancient, ancient))
    t0 = time.time()

    first = tm.sweep_orphan_backups(tmp_path / "projects", now=t0)
    assert first["removed"] == 0 and orphan.exists(), \
        "a transcript just seen missing keeps its backup, however old the backup's mtime"

    later = t0 + tm.ORPHAN_BACKUP_MIN_AGE_S + 1
    out = tm.sweep_orphan_backups(tmp_path / "projects", now=later)
    assert out["removed"] == 1 and out["removed_bytes"] == 10 and out["errors"] == 0
    assert not orphan.exists()
    assert keep_live.exists(), "a backup lives as long as its transcript"
    assert behind_link.exists(), "a dangling transcript symlink counts as present"
    assert other.exists() and live.exists(), "nothing but shed backups is touched"


def test_sweep_wait_resets_when_transcript_returns(tmp_path, monkeypatch):
    import time

    monkeypatch.setattr(tm, "_ORPHAN_SINCE", {})
    d = tmp_path / "projects" / "-p"
    d.mkdir(parents=True)
    t = d / f"{SID}.jsonl"
    bak = d / f"{SID}.jsonl{tm.BACKUP_INFIX}x.bak"
    bak.write_text("b")
    t0 = time.time()
    tm.sweep_orphan_backups(tmp_path / "projects", now=t0)          # missing: wait starts
    t.write_text("{}\n")
    tm.sweep_orphan_backups(tmp_path / "projects", now=t0 + 10)     # back: wait cleared
    t.unlink()
    out = tm.sweep_orphan_backups(tmp_path / "projects", now=t0 + tm.ORPHAN_BACKUP_MIN_AGE_S + 5)
    assert out["removed"] == 0 and bak.exists(), "the wait starts again after a return"


def test_shed_backup_mtime_is_creation_time(tmp_path, monkeypatch):
    import os
    import time

    p = _session(tmp_path, monkeypatch, [100, 100])
    old = time.time() - 10 * 86400
    os.utime(p, (old, old))
    r = tm.shed_transcript_media(p, target_bytes=50)
    assert time.time() - r.backup_path.stat().st_mtime < 60


def test_sweep_is_fail_soft(tmp_path):
    assert tm.sweep_orphan_backups(tmp_path / "missing")["removed"] == 0


async def test_lifecycle_sweep_runs_in_thread_and_never_raises(monkeypatch):
    from core.lifecycle_manager import LifecycleManager

    seen = []
    monkeypatch.setattr(tm, "sweep_orphan_backups", lambda: seen.append(1) or {})
    lm = LifecycleManager.__new__(LifecycleManager)
    await lm._sweep_media_shed_backups()
    assert seen == [1]

    def _boom():
        raise RuntimeError("x")

    monkeypatch.setattr(tm, "sweep_orphan_backups", _boom)
    await lm._sweep_media_shed_backups()  # logged, not raised


def test_leak_clean_keeps_transcript_mode(tmp_path, monkeypatch):
    import os
    import stat as _stat
    from core.leak_transcript import clean_transcript_leaks
    from tests.test_tool_call_leak_guard import _DEADEND_LEAK

    rec = {"type": "assistant", "uuid": "m1",
           "message": {"id": "m1", "role": "assistant",
                       "content": [{"type": "text", "text": _DEADEND_LEAK}]}}
    p = _write(tmp_path, monkeypatch, [rec])
    os.chmod(p, 0o600)
    assert clean_transcript_leaks(p) == 1
    assert _stat.S_IMODE(p.stat().st_mode) == 0o600
