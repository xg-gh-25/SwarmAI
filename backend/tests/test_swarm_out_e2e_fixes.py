"""The Swarm Out E2E fixes (docs/design/2026-11-01-swarm-out-e2e-fixes.md).

Each test answers one question:
- #2 a verdict is taken only on the result there is now, and an old pass verdict
  does not hold the run's commands back;
- #5 a stopped run whose last turn has no ending recorded is shown stopped, with a
  re-run, once that has lasted INTERRUPTED_AFTER_S, and the time is kept per fetch;
- #6 a discarded run settles though a wait stays open, and its waits leave Needs You;
- #4 an answer the Hive refused is a Needs You item;
- #7 commits landed here make the run accepted;
- #3 a dispatch the Hive took (it answered 200, or the reply was cut off) but that named
  no session is recorded as "may be running" with its work order kept; one that never
  reached the Hive is an ordinary failure that releases the work order.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from core import hive_return, swarm_out, swarm_out_results, swarm_out_settle as st

DID = "so_bbbbbbbbbbbb"
OLD, NEW = "2026-10-30T10:00:00Z", "2026-10-31T10:00:00Z"


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)
    kept = {"result": {"ended": "completed", "provisional": False, "ended_at": NEW}, "records": {"closed": True}}
    row = {"dispatch_id": DID, "hive_id": "h1", "session_id": "s1", "project_agent": {"returns": "answer"},
           "sent_at": _iso(datetime.now(timezone.utc))}
    monkeypatch.setattr(swarm_out, "_log_row_of", lambda d: row)
    monkeypatch.setattr(swarm_out_results, "load", lambda d: kept)
    monkeypatch.setattr(hive_return, "load", lambda d, k: None)
    return row, kept


def _row(**kw):
    r = {"dispatch_id": DID, "hive_id": "h1", "session_id": "s1", "state": "stopped", "state_reason": "not_held",
         "sent_at": _iso(datetime.now(timezone.utc)), "project_agent": {"returns": "answer"},
         "state": "stopped",
         "result": {"ended": "completed", "provisional": False, "ended_at": NEW, "lessons": None,
                    "work": {"repos": []}}}
    r.update(kw)
    return r


# ── #2 ──────────────────────────────────────────────────────────────────────

def test_a_verdict_on_an_old_result_is_refused_and_one_on_the_current_is_taken(env):
    with pytest.raises(st.SettleError, match="changed since it was shown"):
        st.set_verdict(DID, "pass", "", OLD)
    st.set_verdict(DID, "pass", "", NEW)
    assert st.closed_state(st.load(DID), DID) == "accepted"


def test_an_old_pass_verdict_does_not_hold_the_runs_commands_back(env):
    _row_, kept = env
    st.set_verdict(DID, "pass", "", NEW)
    kept["result"] = {"ended": "completed", "provisional": False, "ended_at": "2026-11-01T10:00:00Z"}
    assert st.closed_state(st.load(DID), DID) is None            # a newer turn replaced it
    import asyncio
    asyncio.run(swarm_out._not_closed(DID))                      # commands go through


def test_a_verdict_while_the_run_has_no_final_result_is_refused(env):
    _r, kept = env
    kept["result"] = {"ended": "no_record", "provisional": True, "ended_at": NEW}
    with pytest.raises(st.SettleError, match="changed since it was shown"):
        st.set_verdict(DID, "pass", "", NEW)


# ── #5 ──────────────────────────────────────────────────────────────────────

def _cut(since_s_ago):
    res = {"ended": "no_record", "provisional": True, "ended_at": NEW, "lessons": None, "work": {"repos": []}}
    kept = {"records": {"closed": False, "last_at": _iso(datetime.now(timezone.utc))},
            "no_record_since": _iso(datetime.now(timezone.utc) - timedelta(seconds=since_s_ago))}
    return _row(result=res), kept


def test_a_run_cut_off_mid_turn_is_stopped_with_a_rerun_once_it_has_lasted(env):
    row, kept = _cut(st.INTERRUPTED_AFTER_S + 60)
    d = st.derive(row, kept)
    assert d["stage"] == "stopped" and d["why"] == "no_record"
    assert [a["key"] for a in d["actions"]][0] == "rerun"
    row, kept = _cut(30)                                         # the end record may still come
    assert st.derive(row, kept)["stage"] == "running"
    row, kept = _cut(st.INTERRUPTED_AFTER_S + 60)
    row["state"] = "unknown"                                     # not known to have stopped
    assert st.derive(row, kept)["stage"] == "running"


def test_the_kept_record_says_since_when_there_is_no_ending():
    rec = {"checked_at": "2026-11-01T10:00:00Z", "no_record_since": None,
           "result": {"ended": "no_record"}}
    swarm_out_results._note_no_record(rec)
    assert rec["no_record_since"] == "2026-11-01T10:00:00Z"
    rec.update(checked_at="2026-11-01T10:05:00Z")
    swarm_out_results._note_no_record(rec)
    assert rec["no_record_since"] == "2026-11-01T10:00:00Z"      # kept from the first check
    rec["result"] = {"ended": "completed"}
    swarm_out_results._note_no_record(rec)
    assert rec["no_record_since"] is None


# ── #6 ──────────────────────────────────────────────────────────────────────

def test_a_discarded_run_settles_though_a_wait_stays_open(env):
    st.discard(DID, "not needed")
    d = st.derive(_row(state="waiting"), {"records": {"closed": True}})
    assert d["stage"] == "closed" and d["why"] == "discarded" and d["settles"] is True


# ── #4 / #6: Needs You ──────────────────────────────────────────────────────

def test_needs_you_names_a_refused_answer_and_drops_a_discarded_runs_waits(monkeypatch):
    from core import attention_authority as aa
    runs = [
        {"dispatch_id": "so_r1", "hive": "h", "session_id": "s-ref", "stage": "needs_answer",
         "state_reason": "answer_refused", "title": "a"},
        {"dispatch_id": "so_r2", "hive": "h", "session_id": "s-gone", "stage": "closed", "why": "discarded", "title": "b"},
    ]
    asks = [{"hive": "h", "session_id": "s-gone", "request_id": "q1", "kind": "decision",
             "approval_class": "decision", "asked_at": OLD}]
    monkeypatch.setattr(swarm_out, "snapshot", lambda: {"runs": runs, "asks": asks})
    ids = {i.id for i in aa._collect_swarm_out()}
    assert "swarm_out:refused" in ids
    assert "swarm_out:questions" not in ids and not any(i.startswith("swarm_out:wait:") for i in ids)


# ── #7 ──────────────────────────────────────────────────────────────────────

def _repos(env, monkeypatch, states):
    """A commits run whose repositories each brought a bundle back; *states*: each one's
    landing status here (core/hive_return)."""
    from core import hive_bundle
    row, kept = env
    row["project_agent"] = {"returns": "commits"}
    dirs = [f"Hive/work/x/repos/r{i}" for i in range(len(states))]
    kept["result"] = {**kept["result"], "work": {"repos": [{"dir": d, "bundle": {"key": hive_bundle.key(d), "sha256": "s" * 64}}
                                                           for d in dirs]}}
    by_key = {hive_bundle.key(d): {"status": st_} for d, st_ in zip(dirs, states)}
    monkeypatch.setattr(hive_return, "load", lambda d, k: by_key.get(k))
    return by_key, dirs


def test_commits_landed_in_every_repository_make_the_run_accepted(env, monkeypatch):
    import asyncio
    by_key, dirs = _repos(env, monkeypatch, ["landed"])
    assert st.mark_code_landed(DID) is True
    assert st.closed_state(st.load(DID), DID) == "accepted"
    with pytest.raises(swarm_out.SwarmOutError, match="accepted"):
        asyncio.run(swarm_out._not_closed(DID))


def test_one_repository_landed_of_two_leaves_rerun_and_discard_open(env, monkeypatch):
    import asyncio
    from core import hive_bundle
    by_key, dirs = _repos(env, monkeypatch, ["landed", "prepared"])
    assert st.mark_code_landed(DID) is False
    assert st.closed_state(st.load(DID), DID) is None
    asyncio.run(swarm_out._not_closed(DID))                       # discard / re-run still go
    by_key[hive_bundle.key(dirs[1])]["status"] = "landed"
    assert st.mark_code_landed(DID) is True


def test_no_ending_time_is_carried_by_the_fetch_and_restarts_with_a_new_turn(monkeypatch):
    import asyncio
    since = "2026-11-01T10:00:00Z"
    prev = {"dispatch_id": DID, "result": {"ended": "no_record", "ended_at": OLD},
            "no_record_since": since, "no_record_of": OLD, "checked_at": since}
    saved = []
    monkeypatch.setattr(swarm_out_results, "load", lambda d: dict(prev))
    monkeypatch.setattr(swarm_out_results, "save", lambda r: saved.append(dict(r)))

    async def down(h):
        raise swarm_out.SwarmOutError("Hive h1 is not running")
    monkeypatch.setattr(swarm_out, "_instance", down)
    row = {"dispatch_id": DID, "hive_id": "h1", "session_id": "s1"}
    asyncio.run(swarm_out_results._fetch(row))
    assert saved[-1]["no_record_since"] == since                  # the same turn: kept
    prev["result"] = {"ended": "no_record", "ended_at": NEW}      # another turn, still no ending
    asyncio.run(swarm_out_results._fetch(row))
    assert saved[-1]["no_record_since"] != since and saved[-1]["no_record_of"] == NEW


def test_a_rerun_starts_the_no_ending_time_again(monkeypatch):
    rec = {"dispatch_id": DID, "no_record_since": "2026-11-01T10:00:00Z", "no_record_of": OLD}
    saved = []
    monkeypatch.setattr(swarm_out_results, "load", lambda d: dict(rec))
    monkeypatch.setattr(swarm_out_results, "save", lambda r: saved.append(r))
    swarm_out_results.turn_started(DID)
    assert saved and saved[-1]["no_record_since"] is None


def test_an_older_kept_record_without_the_time_counts_from_its_last_check():
    row, kept = _cut(0)
    kept.pop("no_record_since")
    kept["checked_at"] = _iso(datetime.now(timezone.utc) - timedelta(seconds=st.INTERRUPTED_AFTER_S + 60))
    assert st.derive(row, kept)["stage"] == "stopped"


# ── #3 ──────────────────────────────────────────────────────────────────────

class _CutStream(httpx.AsyncByteStream):
    """A 200 reply whose connection breaks after a first event that names no session."""
    def __init__(self, exc):
        self.exc = exc

    async def __aiter__(self):
        yield b'data: {"type": "heartbeat"}\n\n'
        raise self.exc("connection reset by peer")


def _hive(monkeypatch, tmp_path, stream):
    """A fake Hive whose session request is answered by *stream(request)*; returns the
    paths asked."""
    from tests import _hive_work_fake as work
    from tests.test_swarm_out_host import INST
    seen = []

    async def instance(ref):
        return swarm_out._validate_instance(INST, {"id": "a1"})

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/api/chat/sessions/admission-state":
            return httpx.Response(200, json=work.ADMISSION)
        if work.answer(request) is not None:
            return work.answer(request)
        if request.url.path == "/api/chat/stream":
            return stream(request)
        return httpx.Response(404)

    monkeypatch.setattr(swarm_out, "_instance", instance)
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(swarm_out, "_client", lambda inst, timeout=None: httpx.AsyncClient(
        base_url=swarm_out._base(inst), transport=httpx.MockTransport(handler)))
    return seen


def _rows(tmp_path):
    p = tmp_path / "dispatches.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []


def _raise(exc):
    def stream(request):
        raise exc("Server disconnected without sending a response", request=request)
    return stream


@pytest.mark.parametrize("stream", [
    pytest.param(lambda r: httpx.Response(200, stream=_CutStream(httpx.ReadError)), id="reset-after-200"),
    pytest.param(lambda r: httpx.Response(200, stream=_CutStream(httpx.RemoteProtocolError)), id="protocol-cut-after-200"),
    pytest.param(lambda r: httpx.Response(200, stream=_CutStream(httpx.DecodingError)), id="other-error-after-200"),
    pytest.param(lambda r: httpx.Response(200, text='data: {"type": "heartbeat"}\n\n'), id="stream-ended-no-session"),
    pytest.param(lambda r: httpx.Response(200, text='data: {"session_id": "../bad"}\n\n'), id="unusable-session-id"),
    pytest.param(_raise(httpx.RemoteProtocolError), id="cut-before-the-reply"),
])
def test_a_dispatch_the_hive_took_without_naming_a_session_may_be_running(monkeypatch, tmp_path, stream):
    seen = _hive(monkeypatch, tmp_path, stream)
    with pytest.raises(swarm_out.SwarmOutMaybeSent, match="may be running there"):
        asyncio.run(swarm_out.dispatch("worker-1", "Fix it", "do X"))
    (row,) = _rows(tmp_path)
    assert row["session_id"] is None and row["title"] == "Fix it" and row["dispatch_id"]
    assert not any(p.endswith("/release") for p in seen)


@pytest.mark.parametrize("stream", [
    pytest.param(_raise(httpx.ConnectError), id="cannot-connect"),
    pytest.param(lambda r: httpx.Response(503), id="hive-refused"),
])
def test_a_dispatch_that_never_reached_the_hive_fails_and_releases(monkeypatch, tmp_path, stream):
    seen = _hive(monkeypatch, tmp_path, stream)
    with pytest.raises(Exception) as e:
        asyncio.run(swarm_out.dispatch("worker-1", "Fix it", "do X"))
    assert not isinstance(e.value, swarm_out.SwarmOutMaybeSent)
    assert seen[-1].endswith("/release") and _rows(tmp_path) == []


class _HangStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"type": "heartbeat"}\n\n'
        await asyncio.sleep(30)


def test_a_dispatch_cancelled_after_the_hive_took_it_keeps_the_row_and_the_work_order(monkeypatch, tmp_path):
    seen = _hive(monkeypatch, tmp_path, lambda r: httpx.Response(200, stream=_HangStream()))

    async def go():
        t = asyncio.create_task(swarm_out.dispatch("worker-1", "Fix it", "do X"))
        while "/api/chat/stream" not in seen:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    asyncio.run(go())
    (row,) = _rows(tmp_path)
    assert row["session_id"] is None
    assert not any(p.endswith("/release") for p in seen)
