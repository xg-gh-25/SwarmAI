"""The Hive run decision funnel (design docs/design/2026-11-02-hive-run-decision-record.md).

Each test answers one question of the design's §5:
- F1/F7 Needs You takes a run whose dispatching session is alive only once its result is
  on this host and the agent handed it over (ask-person); runs no live session answers
  for keep the older items; no timer hands an agent's wait to the person;
- the dispatching session is woken, once per change, only while it is idle;
- F3 an agent lands the repository's protected paths but never git-executing ones;
- F4 an agent discards unlanded commits once they are on this host;
- F2 ask-person only on a landed result, and the run waits for the person meanwhile;
- F5 keep settles, revert waits for a revert this host verifies;
- F6 every decision is one entry, its facts written here, kept whole;
- an agent's own landing waits for its judgment.
"""
from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core import (attention_authority as aa, hive_return, swarm_out, swarm_out_actor as act,
                  swarm_out_follow, swarm_out_record as rc, swarm_out_results, swarm_out_settle as st,
                  swarm_out_wait_card as wc, swarm_out_wake as wake)

DID = "so_aaaaaaaaaaaa"
SID = "s-abc123"
ENDED = "2026-11-02T10:00:00Z"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)
    rows: dict[str, dict] = {}
    returns: dict[tuple, dict] = {}

    def log_row(dispatch_id):
        if dispatch_id not in rows:
            raise swarm_out.SwarmOutError("no dispatch")
        return rows[dispatch_id]
    monkeypatch.setattr(swarm_out, "_log_row_of", log_row)
    real_load = hive_return.load
    monkeypatch.setattr(hive_return, "load", lambda d, k: returns.get((d, k)) or real_load(d, k))
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {
        "records": {"closed": True},
        "result": {"ended": "completed", "provisional": False, "ended_at": ENDED}})
    monkeypatch.setattr(aa, "_session_title", lambda sid: (sid != "gone", "Main"))
    monkeypatch.setattr(aa, "session_confirmed", lambda sid: sid != "gone")
    monkeypatch.setattr(wc, "escalation", lambda d, r: None)
    return rows, returns


def K():
    return {"records": {"closed": True,
                        "last_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}}


def _row(returns="answer", **kw):
    r = {"dispatch_id": DID, "hive_id": "h1", "session_id": "hs1", "dispatched_from": SID,
         "sent_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "project_agent": {"returns": returns}, "waiting": False, "running": False, "state_reason": "idle",
         "result": {"ended": "completed", "provisional": False, "ended_at": ENDED, "lessons": None,
                    "work": {"repos": []}}}
    r.update(kw)
    return r


def _commits_row(rows, returns, status="landed"):
    from core import hive_bundle
    r = _row("commits")
    r["result"]["work"]["repos"] = [{"dir": "Hive/work/x/repos/a", "bundle": {"available": True}}]
    rows[DID] = r
    returns[(DID, hive_bundle.key("Hive/work/x/repos/a"))] = {"status": status}
    return r


def A(d):
    return [("*" if a["primary"] else "") + a["key"] + (":" + a["variant"] if a.get("variant") else "")
            for a in d["actions"]]


def _annotated(r):
    d = st.derive(r, K())
    return {**r, "stage": d["stage"], "actions": d["actions"], "why": d["why"], "funnel": d["funnel"],
            "settle": {"code": d["code"], "why_not": d["why_not"], "settled": d["settled"],
                       "verdict": d["verdict"]}}


# ── Needs You ────────────────────────────────────────────────────────────────

def test_needs_you_takes_only_landed_questions(env, monkeypatch):
    runs = [
        {"dispatch_id": "so_aaaaaaaaaaa1", "dispatched_from": SID, "stage": "awaiting", "session_id": "a"},
        {"dispatch_id": "so_aaaaaaaaaaa2", "dispatched_from": SID, "stage": "stopped", "session_id": "b"},
        {"dispatch_id": "so_aaaaaaaaaaa3", "dispatched_from": SID, "stage": "running", "session_id": "c",
         "cleanup_soon": True},
        {"dispatch_id": "so_aaaaaaaaaaa4", "dispatched_from": SID, "stage": "awaiting", "session_id": "d",
         "title": "Bundle size",
         "funnel": {"ask": {"state": "open", "decide": "Keep the 40 kB chunk?", "judgment": "keep",
                            "why": "a preference", "landed": {"commits": ["4be20a1"], "files": []}}}},
        # its session is gone: nobody answers for it but the person
        {"dispatch_id": "so_aaaaaaaaaaa5", "dispatched_from": "gone", "stage": "awaiting", "session_id": "e"},
    ]
    asks = [
        # the agent's own class, waiting a long time: still the agent's
        {"hive": "h", "session_id": "a", "request_id": "q1", "approval_class": "question",
         "asked_at": "2020-01-01T00:00:00Z"},
        # the person's class
        {"hive": "h", "session_id": "b", "request_id": "q2", "approval_class": "off_machine"},
    ]
    monkeypatch.setattr(swarm_out, "snapshot", lambda: {"runs": runs, "asks": asks})
    items = {i.id: i for i in aa._collect_swarm_out()}
    assert set(items) == {"swarm_out:ask:so_aaaaaaaaaaa4", "swarm_out:wait:so_aaaaaaaaaaa2:q2",
                          "swarm_out:decide"}
    assert items["swarm_out:decide"].title.startswith("1 ")          # only the run nobody owns
    ask = items["swarm_out:ask:so_aaaaaaaaaaa4"]
    assert ask.tier == aa.TIER_BLOCKING
    for text in ("Keep the 40 kB chunk?", "4be20a1", "a preference"):
        assert text in ask.detail
    assert "a preference" not in ask.dispatch["message"]              # the agent's words stay data


def test_needs_you_unchanged_for_runs_without_a_dispatcher(env, monkeypatch):
    runs = [{"dispatch_id": "so_aaaaaaaaaaa1", "stage": "awaiting", "session_id": "a"},
            {"dispatch_id": "so_aaaaaaaaaaa2", "stage": "running", "session_id": "b", "cleanup_soon": True}]
    asks = [{"hive": "h", "session_id": "a", "request_id": "q1"}]
    monkeypatch.setattr(swarm_out, "snapshot", lambda: {"runs": runs, "asks": asks})
    assert {i.id for i in aa._collect_swarm_out()} == {"swarm_out:questions", "swarm_out:decide",
                                                         "swarm_out:cleanup"}


# ── waking the dispatching session ──────────────────────────────────────────

class _Router:
    def __init__(self):
        self.busy, self.sent = False, []

    def is_busy(self, sid):
        return self.busy

    def run_conversation(self, **kw):
        self.sent.append(kw)

        async def gen():
            yield {"type": "accepted", "clientId": kw["client_id"]}
        return gen()


def test_wake_once_only_when_idle(env, monkeypatch):
    run = {"dispatch_id": DID, "dispatched_from": SID, "stage": "awaiting", "state": "idle",
           "session_id": "hs1", "title": "T", "settle": {"why_not": ["verdict"]}}
    data = {"runs": [run], "asks": [], "answered": []}
    monkeypatch.setattr(swarm_out, "snapshot", lambda: data)
    from core import session_pending

    async def none(sid):
        return 0
    monkeypatch.setattr(session_pending, "count_pending", none)
    router = _Router()
    router.busy = True
    assert asyncio.run(wake.wake_sessions(router)) == [] and router.sent == []   # busy: not now
    router.busy = False
    assert asyncio.run(wake.wake_sessions(router)) == [SID]
    sent = router.sent[-1]
    assert sent["session_id"] == SID and sent["input_origin"] == "system"
    assert sent["user_message"].startswith(wake.MARKER)
    # not seen yet (the woken turn did not start): the same change is the same send
    asyncio.run(wake.wake_sessions(router))
    assert router.sent[-1]["client_id"] == sent["client_id"]
    # the woken turn's digest marks it seen: no second wake for the same change
    _block, shown = swarm_out_follow.digest(SID)
    swarm_out_follow.mark_seen(SID, shown)
    n = len(router.sent)
    asyncio.run(wake.wake_sessions(router))
    assert len(router.sent) == n
    # a run waiting on the person (an open ask) wakes nobody
    run.update(stage="awaiting", why="ask_person", funnel={"ask": {"state": "open"}})
    asyncio.run(wake.wake_sessions(router))
    assert len(router.sent) == n


def test_wake_reads_the_woken_turn_to_its_end(env, monkeypatch):
    """Closing run_conversation early ends the turn it started: the wake reads it to its
    end in a task of its own, and wakes no session whose last wake is still being read."""
    run = {"dispatch_id": DID, "dispatched_from": SID, "stage": "awaiting", "state": "idle",
           "session_id": "hs1", "title": "T", "settle": {"why_not": ["verdict"]}}
    monkeypatch.setattr(swarm_out, "snapshot", lambda: {"runs": [run], "asks": [], "answered": []})
    from core import session_pending

    async def none(sid):
        return 0
    monkeypatch.setattr(session_pending, "count_pending", none)
    seen = {"ended": False, "sent": 0}

    async def go():
        release = asyncio.Event()

        class R(_Router):
            def run_conversation(self, **kw):
                seen["sent"] += 1

                async def gen():
                    yield {"type": "accepted"}
                    await release.wait()
                    yield {"type": "result"}
                    seen["ended"] = True
                return gen()
        router = R()
        assert await wake.wake_sessions(router) == [SID]
        assert await wake.wake_sessions(router) == [] and seen["sent"] == 1
        release.set()
        await asyncio.gather(*list(wake._DRAINS))
        assert seen["ended"] and not wake._DRAINING
    asyncio.run(go())


def test_owner_is_only_a_session_the_table_confirms(tmp_path, monkeypatch):
    monkeypatch.setattr(aa, "get_app_data_dir", lambda: str(tmp_path))
    assert aa.session_confirmed(SID) is False                     # no database: not an owner
    (tmp_path / "data.db").write_text("not a database")
    assert aa.session_confirmed(SID) is False                     # unreadable: not an owner
    import sqlite3
    (tmp_path / "data.db").unlink()
    c = sqlite3.connect(tmp_path / "data.db")
    c.execute("CREATE TABLE sessions (id TEXT, title TEXT)")
    c.execute("INSERT INTO sessions VALUES (?, 'Main')", (SID,))
    c.commit()
    c.close()
    assert aa.session_confirmed(SID) is True and aa.session_confirmed("other") is False


# ── landing protected paths ──────────────────────────────────────────────────

def test_agent_lands_protected_but_not_git_executing_paths(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import swarm_out as so_router
    calls = []
    real_land = hive_return.land
    monkeypatch.setattr(hive_return, "land", lambda *a: calls.append(a) or {"landed": True})

    async def row(d):
        return {"dispatch_id": d, "dispatched_from": SID,
                "actions": [{"key": "accept", "variant": "land"}], "stage": "awaiting"}
    monkeypatch.setattr(swarm_out, "run_row", row)
    monkeypatch.setattr(swarm_out, "_refresh_soon", lambda: None)
    app = FastAPI()
    app.include_router(so_router.router)
    c = TestClient(app)
    r = c.post(f"/runs/{DID}/return/land", json={"by": "agent", "session": SID, "why": "reviewed the diff",
                                                 "key": "0123456789abcdef", "tip": "a" * 40})
    assert r.status_code == 200, r.text
    _did, _key, _tip, allow_git_exec, _summary = calls[-1]
    assert allow_git_exec is False
    e = rc.entries(st.load(DID))[-1]
    assert (e["by"], e["decision"], e["why"]) == ("agent", "land", "reviewed the diff")
    # the git gate in land itself: hooks are refused, a reviewed protected path goes on
    class P:
        returncode, stdout = 0, b"t" * 40 + b"\n"
    state = {"repo": "/tmp", "ref": "refs/hive/x/k", "tip": "t" * 40, "files": [], "files_total": 0, "branch": "refs/heads/main",
             "protected_files": [".husky/pre-commit", "backend/core/security_hooks.py"]}
    monkeypatch.setattr(hive_return, "_prepared", lambda *a: state)
    monkeypatch.setattr(hive_return, "_git", lambda *a, **k: P())

    def reached(*a):
        raise hive_return.ReturnError("reached the merge")
    monkeypatch.setattr(hive_return, "_target_branch", reached)
    with pytest.raises(hive_return.ReturnError, match="git hooks"):
        real_land(DID, "k", "t" * 40, False)
    state["protected_files"] = ["backend/core/security_hooks.py", "backend/tests/conftest.py",
                                ".claude/settings.json"]
    with pytest.raises(hive_return.ReturnError, match="reached the merge"):
        real_land(DID, "k", "t" * 40, False)


# ── discarding unlanded commits that are on this host ───────────────────────

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("one\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "base")
    return repo


def test_agent_discard_needs_local_commits(env, tmp_path, monkeypatch):
    rows, returns = env
    repo = _repo(tmp_path)
    tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", f"refs/hive/{DID}/k", tip)
    from core import hive_bundle
    key = hive_bundle.key("Hive/work/x/repos/a")
    res = {"ended": "completed", "provisional": False,
           "work": {"repos": [{"dir": "Hive/work/x/repos/a", "bundle": {"available": True}}]}}
    row = {"dispatch_id": DID, "running": False, "waiting": False, "result": res,
           "settle": {"why_not": ["verdict", "code"]}}
    returns[(DID, key)] = {"status": "prepared", "repo": str(repo), "ref": f"refs/hive/{DID}/k", "tip": tip}
    assert act.discard_blocker(row) is None
    _git(repo, "update-ref", "-d", f"refs/hive/{DID}/k")
    assert "not landed" in (act.discard_blocker(row) or "")
    returns.pop((DID, key))                                   # never taken back
    assert "not landed" in (act.discard_blocker(row) or "")


# ── ask-person, keep, revert ────────────────────────────────────────────────

def test_ask_person_only_on_landed_results_and_holds_settlement(env):
    rows, returns = env
    r = _commits_row(rows, returns, "prepared")
    with pytest.raises(rc.RecordError, match="not landed"):
        rc.ask_person(DID, _annotated(r), SID, "Keep it?", "keep", "a preference")
    assert "ask_person" not in st.load(DID)
    _commits_row(rows, returns, "landed")
    r = _annotated(rows[DID])
    with pytest.raises(rc.RecordError, match="only the session"):
        rc.ask_person(DID, r, "s-other", "Keep it?", "keep", "a preference")
    with pytest.raises(rc.RecordError, match="say what"):
        rc.ask_person(DID, r, SID, "Keep it?", "", "a preference")
    assert "ask_person" not in st.load(DID)
    rc.ask_person(DID, r, SID, "Keep it?", "technically fine", "a preference")
    d = st.derive(rows[DID], K())
    assert d["stage"] == "awaiting" and d["why"] == "ask_person" and not d["settles"]
    assert A(d)[:2] == ["*keep", "revert"]
    assert d["funnel"]["gates"][-1]["state"] == "person"
    # an answer result that is not final is not on this host
    rows[DID] = _row("answer", result={"ended": "completed", "provisional": True, "ended_at": ENDED,
                                       "work": {"repos": []}})
    st._update(DID, lambda rec: rec.pop("ask_person", None))
    with pytest.raises(rc.RecordError, match="final result"):
        rc.ask_person(DID, _annotated(rows[DID]), SID, "q", "j", "w")


def _landed_repo(tmp_path: Path, returns):
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("two\n")
    _git(repo, "commit", "-qam", "landed")
    landed = _git(repo, "rev-parse", "HEAD")
    d = tmp_path / "returns" / DID
    d.mkdir(parents=True)
    (d / "0123456789abcdef.json").write_text(__import__("json").dumps(
        {"status": "landed", "landed": landed, "landed_tip": landed, "repo": str(repo),
         "branch": "refs/heads/main", "url": "u", "base": base}))
    return repo, landed


def test_keep_and_revert(env, tmp_path):
    rows, returns = env
    # keep: an answer run passes on the result the person kept
    rows[DID] = _row("answer")
    rc.ask_person(DID, _annotated(rows[DID]), SID, "Use it?", "good", "a taste call")
    with pytest.raises(rc.RecordError):
        rc.answer_ask(DID, "maybe")
    rc.answer_ask(DID, "keep", "fine")
    d = st.derive(rows[DID], K())
    assert d["verdict"] == "pass" and d["settles"]
    es = rc.entries(st.load(DID))
    assert [(e["by"], e["decision"]) for e in es] == [("agent", "ask_person"), ("person", "keep")]
    # revert: commits landed here, the agent undoes them and reports the revert
    st._update(DID, lambda rec: rec.clear())
    repo, landed = _landed_repo(tmp_path, returns)
    r = _commits_row(rows, returns, "landed")
    rc.ask_person(DID, _annotated(r), SID, "Keep the change?", "fine", "a preference")
    rc.answer_ask(DID, "revert", "too big")
    d = st.derive(rows[DID], K())
    assert d["stage"] == "awaiting" and d["why"] == "revert" and not d["settles"]
    assert "keep" not in [a["key"] for a in d["actions"]]
    # nothing is offered that the backend refuses (what landed counts as accepted); an
    # agent reports its revert instead of discarding
    assert "discard" not in [a["key"] for a in d["actions"]]
    assert "reverted" in (act.discard_blocker({**_annotated(rows[DID]), "running": False}) or "")
    unrelated = (repo / "b.txt")
    unrelated.write_text("x\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-qm", "unrelated")
    with pytest.raises(rc.RecordError, match="undoes the landing"):
        rc.reverted(DID, _annotated(rows[DID]), SID, [_git(repo, "rev-parse", "HEAD")], "done")
    assert rc.ask(st.load(DID))["state"] == "revert"
    _git(repo, "revert", "--no-edit", landed)
    rev = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(rc.RecordError, match="only the session"):
        rc.reverted(DID, _annotated(rows[DID]), "s-other", [rev], "done")
    rc.reverted(DID, _annotated(rows[DID]), SID, [rev], "reverted it")
    d = st.derive(rows[DID], K())
    assert d["verdict"] == "reverted" and d["settles"] and d["stage"] == "discarded"


# ── the record ───────────────────────────────────────────────────────────────

def test_decision_record_entries(env):
    rows, _ = env
    rows[DID] = _row()
    act.record(DID, act.actor("agent", SID, "the acceptance holds"), "accept")
    act.record(DID, act.actor("person", None, "fine by me"), "stop")
    for _ in range(60):
        rc.append(DID, "agent", "review", "looked", SID, extra={"observed": {"fake": True}})
    es = rc.entries(st.load(DID))
    assert len(es) == 62                                         # kept whole
    assert (es[0]["by"], es[0]["decision"], es[0]["session"]) == ("agent", "accept", SID)
    assert (es[1]["by"], es[1]["decision"], es[1]["why"]) == ("person", "stop", "fine by me")
    assert all(e["observed"].get("ended_at") == ENDED and "fake" not in e["observed"] for e in es)
    assert rc.person_decisions(st.load(DID), True) == 1
    # on a run the person dispatched, judging it is not stepping in; steering it is
    own = {"commands": [rc.entry("person", "dispatch", "", None, "dispatch", None, {}),
                        rc.entry("person", "accept", "", None, "accept", None, {})]}
    assert rc.person_decisions(own, False) == 0
    own["commands"].append(rc.entry("person", "said", "", None, "said", None, {}))
    assert rc.person_decisions(own, False) == 1
    theirs = {"commands": [rc.entry("agent", "dispatch", "", SID, "dispatch", None, {}),
                           rc.entry("person", "accept", "", None, "accept", None, {})]}
    assert rc.person_decisions(theirs, True) == 1
    # an older record: its entries name a command only
    old = {"commands": [{"by": "agent", "command": "return_land", "why": "w"}]}
    assert rc.entries(old)[0]["decision"] == "land" and rc.person_decisions(old, True) is None
    assert not rc.judgment_pending(old)                          # its landing was its pass
    doc = act.__doc__ or ""
    assert "accepted by the agent" not in doc and "decision record" in doc


def test_agent_landing_waits_for_judgment(env, monkeypatch):
    rows, returns = env
    _commits_row(rows, returns, "landed")
    # the kept result names the repository's bundle (its sha256), as on this host
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {
        "records": {"closed": True},
        "result": {"ended": "completed", "provisional": False, "ended_at": ENDED,
                   "work": {"repos": [{"dir": "Hive/work/x/repos/a", "bundle": {"sha256": "x"}}]}}})
    rc.append(DID, "agent", "land", "reviewed", SID, "return_land")
    d = st.derive(rows[DID], K())
    assert d["verdict"] is None and d["stage"] == "awaiting" and d["why"] == "judge" and not d["settles"]
    assert A(d)[0] == "*accept:verdict"
    # what landed is judged, not re-run or discarded (the backend refuses those here)
    assert not [a for a in A(d) if a.startswith(("rerun", "discard"))]
    assert st.set_verdict(DID, "pass", "", ENDED) == {"judged": True}   # no verdict written
    assert "verdict" not in st.load(DID)
    act.record(DID, act.actor("agent", SID, "acceptance holds"), "accept")
    d = st.derive(rows[DID], K())
    assert d["verdict"] == "pass" and d["settles"]
    # the person's landing is the person's judgment
    st._update(DID, lambda rec: rec.clear())
    rc.append(DID, "person", "land", "", None, "return_land")
    assert st.derive(rows[DID], K())["verdict"] == "pass"


def test_a_slow_wake_never_holds_the_refresh(env, tmp_path, monkeypatch):
    """The wake pass runs in a task of its own, one at a time: refreshes go on while a
    send waits."""
    from core import swarm_out_wake
    calls = {"refresh": 0, "wake": 0}
    held = asyncio.Event()

    async def refresh():
        calls["refresh"] += 1

    async def slow_wake(router=None):
        calls["wake"] += 1
        await held.wait()
        return []
    log = tmp_path / "log.jsonl"
    log.write_text("")
    monkeypatch.setattr(swarm_out, "_log_path", lambda: log)
    monkeypatch.setattr(swarm_out, "refresh_runs", refresh)
    monkeypatch.setattr(swarm_out_wake, "wake_sessions", slow_wake)
    monkeypatch.setattr(swarm_out, "REFRESH_EVERY_S", 0.01)
    monkeypatch.setattr(swarm_out, "_WAKE", None)

    async def go():
        loop = asyncio.create_task(swarm_out.run_refresh_loop())
        await asyncio.sleep(0.3)
        loop.cancel()
        held.set()
        await asyncio.gather(loop, return_exceptions=True)
    asyncio.run(go())
    assert calls["refresh"] >= 5 and calls["wake"] == 1
