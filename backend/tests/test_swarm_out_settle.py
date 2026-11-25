"""Accepting and settling a dispatched task (core/swarm_out_settle; designs
docs/design/2026-10-31-swarm-out-run-review.md §1.2, §3.2, §3.5 and
docs/design/2026-10-30-dispatch-center.md §6).

Each test answers one question:
- each run has one stage, and its actions are exactly that stage's row of §1.2 (at
  most one primary, "open session" while the session can open, "discard" last); a
  newer turn voids the verdict; not good enough is not a verdict;
- commits are accepted by landing them (landed or landed by hand = pass) and there is
  no second accept button for them;
- an unsettled run nothing changed in for RETENTION_DAYS expires;
- a task settles only when the verdict and the code are handled and the lessons are in
  (record closed, none left to queue: no one takes them, CDF design D13), a
  Hive that is gone still settles, and a settled task's Hive is told to release it;
- the settle actions are the person's: a command calling them is refused.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from core import hive_return, swarm_out, swarm_out_results, swarm_out_settle as st

DID = "so_aaaaaaaaaaaa"
ENDED = "2026-10-30T10:00:00Z"


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
    monkeypatch.setattr(hive_return, "load", lambda d, k: returns.get((d, k)))
    # the kept record: its records, and the result a verdict is taken on (set_verdict
    # takes one only on the run's current final result)
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {
        "records": {"closed": True},
        "result": {"ended": "completed", "provisional": False, "ended_at": ENDED}})
    return rows, returns


def K(closed):
    """The kept record derive reads: its records' closed state (None: no record)."""
    if closed is None:
        return None
    return {"records": {"closed": closed, "last_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}}


def _row(returns="answer", **kw):
    r = {"dispatch_id": DID, "hive_id": "h1", "session_id": "s1",
         "sent_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "project_agent": {"returns": returns}, "state": "stopped",
         "state_reason": "idle",
         "result": {"ended": "completed", "provisional": False, "ended_at": ENDED,
                    "lessons": None, "work": {"repos": []}}}
    r.update(kw)
    return r


#: the Hive commit a result names for a repository (its ``head``), and the one a take-back
#: state took (its ``tip``): the state is of the result only when they are the same
HEAD_A, HEAD_B = "a" * 40, "b" * 40


def _with_bundle(row):
    row["result"]["work"]["repos"] = [{"dir": "Hive/work/x/repos/a", "head": HEAD_A, "bundle": {"available": True}}]
    return row


def _code_state(returns, status, tip=HEAD_A):
    from core import hive_bundle
    returns[(DID, hive_bundle.key("Hive/work/x/repos/a"))] = {"status": status, "tip": tip}


def A(d):
    """The actions as written in §1.2: ``*`` marks the primary, ``:variant`` follows."""
    return [("*" if a["primary"] else "") + a["key"] + (":" + a["variant"] if a.get("variant") else "")
            for a in d["actions"]]


# ── §1.2: one stage, and exactly its actions ─────────────────────────────────

def test_stage_order(env):
    rows, _ = env
    rows[DID] = _row()
    d = st.derive(_row(state="waiting"), K(True))
    assert d["stage"] == "needs_answer" and A(d) == ["answer:hint", "stop", "open_session", "discard"]
    d = st.derive(_row(state="waiting", state_reason="answer_refused"), K(True))
    assert d["stage"] == "needs_answer" and A(d) == ["*open_session", "stop", "discard"]
    d = st.derive(_row(state="running", state_reason="answer_sent"), K(True))
    assert d["stage"] == "running" and A(d) == ["open_session", "discard"]
    d = st.derive(_row(state="running"), K(True))
    assert d["stage"] == "running" and A(d) == ["*stop", "open_session", "discard"]
    r = _row()
    r["result"]["provisional"] = True
    assert st.derive(r, K(True))["stage"] == "running"
    d = st.derive(_row(), K(True))
    assert d["stage"] == "awaiting" and A(d) == ["*accept:verdict", "rerun", "open_session", "discard"]
    st.set_verdict(DID, "pass", "", ENDED)
    r = _row()
    r["result"]["lessons"] = {"pending": 2, "accepted": False, "judging": 0}
    d = st.derive(r, K(True))                                # lessons need no one
    assert d["stage"] == "accepted" and A(d) == ["open_session:read_only"] and not d["settles"]
    assert st.derive(_row(), K(True))["settles"]


@pytest.mark.parametrize("ended", ["error", "stopped", "crashed", "gave_up", "turn_limit"])
def test_a_result_that_did_not_deliver_is_stopped(env, ended):
    rows, _ = env
    rows[DID] = _row()
    r = _row()
    r["result"]["ended"] = ended
    d = st.derive(r, K(True))
    assert d["stage"] == "stopped" and d["why"] == ended
    assert A(d) == ["*rerun", "open_session", "discard"]


@pytest.mark.parametrize("stage,alive,accept,want,reason", [
    ("running", True, None, ["*stop", "open_session", "discard"], None),
    ("running", False, None, ["*stop", "discard"], None),
    ("running", True, None, ["open_session", "discard"], "answer_sent"),
    ("needs_answer", True, None, ["answer:hint", "stop", "open_session", "discard"], None),
    ("needs_answer", True, None, ["*open_session", "stop", "discard"], "answer_refused"),
    ("needs_answer", False, None, ["stop", "discard"], "answer_refused"),
    ("awaiting", True, "verdict", ["*accept:verdict", "rerun", "open_session", "discard"], None),
    ("awaiting", True, "land", ["*accept:land", "rerun", "open_session", "discard"], None),
    ("awaiting", True, "manual", ["*accept:manual", "rerun", "open_session", "discard"], None),
    ("awaiting", True, None, ["*rerun", "open_session", "discard"], None),
    ("awaiting", False, "verdict", ["*accept:verdict", "discard"], None),
    ("stopped", True, None, ["*rerun", "open_session", "discard"], None),
    ("stopped", False, None, ["discard"], None),
    ("accepted", True, None, ["open_session:read_only"], None),
    ("accepted", False, None, [], None),
    ("closed", True, None, [], None),
])
def test_every_stage_offers_exactly_its_row(stage, alive, accept, want, reason):
    got = st._actions(stage, alive, accept, reason)
    assert A({"actions": got}) == want
    assert sum(1 for a in got if a["primary"]) <= 1
    assert all(a["key"] != "discard" for a in got[:-1])     # discard is last when offered


def test_newer_turn_voids_the_verdict(env):
    rows, _ = env
    rows[DID] = _row()
    st.set_verdict(DID, "pass", "fine", ENDED)
    assert st.derive(_row(), K(True))["verdict"] == "pass"
    r = _row()
    r["result"]["ended_at"] = "2026-10-30T11:00:00Z"
    d = st.derive(r, K(True))
    assert d["verdict"] is None and d["stage"] == "awaiting" and not d["settles"]


def test_not_good_enough_is_not_a_verdict_discard_settles(env):
    rows, _ = env
    rows[DID] = _row()
    with pytest.raises(st.SettleError, match="re-run"):
        st.set_verdict(DID, "fail", "missing the numbers", ENDED)
    st.discard(DID, "  wrong   numbers ")
    assert st.load(DID)["discard_note"] == "wrong numbers"
    d = st.derive(_row(), K(True))
    assert d["verdict"] == "abandoned" and d["settles"] and d["stage"] == "closed" and d["why"] == "discarded" and A(d) == []
    with pytest.raises(st.SettleError):
        st.discard(DID)


def test_no_session_row_ends_only_by_discarding(env):
    rows, _ = env
    rows[DID] = _row(session_id=None, result=None, state_reason="no_session_id", state="unknown")
    d = st.derive(rows[DID], K(None))
    assert d["stage"] == "stopped" and d["why"] == "no_session" and A(d) == ["discard"]
    assert not d["settles"]
    with pytest.raises(st.SettleError):
        st.set_verdict(DID, "pass", "", ENDED)
    st.discard(DID)
    assert st.derive(rows[DID], K(None))["settles"]


# ── invariant 4: commits are accepted by landing them ────────────────────────

def test_commits_verdict_is_the_landing(env):
    rows, returns = env
    rows[DID] = _with_bundle(_row("commits"))
    _code_state(returns, "prepared")
    d = st.derive(rows[DID], K(True))
    assert d["stage"] == "awaiting" and A(d)[0] == "*accept:land" and d["verdict"] is None
    assert not d["settles"]
    _code_state(returns, "landed")
    d = st.derive(rows[DID], K(True))
    assert d["verdict"] == "pass" and d["settles"]
    # rejected by an older Host (nothing rejects any more): nothing to land, so no
    # accept: re-run or discard
    _code_state(returns, "rejected")
    d = st.derive(rows[DID], K(True))
    assert d["verdict"] is None and A(d) == ["*rerun", "open_session", "discard"] and not d["settles"]


def test_commits_asked_for_and_never_came_cannot_be_accepted(env):
    rows, _ = env
    rows[DID] = _row("commits")
    d = st.derive(rows[DID], K(True))
    assert d["stage"] == "awaiting" and A(d) == ["*rerun", "open_session", "discard"]


def test_landed_by_hand_is_a_pass(env):
    rows, returns = env
    rows[DID] = _with_bundle(_row("commits"))
    _code_state(returns, "prepared")
    st.set_manual_landed(DID)
    d = st.derive(rows[DID], K(True))
    assert d["code"] == "landed" and d["verdict"] == "pass" and d["settles"]


def test_no_second_accept_for_commits(env):
    rows, _ = env
    rows[DID] = _with_bundle(_row("commits"))
    with pytest.raises(st.SettleError):
        st.set_verdict(DID, "pass", "", ENDED)
    rows[DID] = _row("document")
    with pytest.raises(st.SettleError):
        st.set_manual_landed(DID)


# ── invariant 5: settling ────────────────────────────────────────────────────

def test_each_condition_holds_settling_back(env):
    rows, returns = env
    # verdict missing
    rows[DID] = _row()
    assert st.derive(rows[DID], K(True))["why_not"] == ["verdict"]
    # code waiting (an answer task that also brought commits back)
    st.set_verdict(DID, "pass", "", ENDED)
    rows[DID] = _with_bundle(_row())
    _code_state(returns, "prepared")
    assert st.derive(rows[DID], K(True))["why_not"] == ["code"]
    _code_state(returns, "landed")
    # a lesson not yet queued, then the record not closed
    r = _with_bundle(_row())
    r["result"]["lessons"] = {"pending": 1, "accepted": False, "judging": 0}
    assert st.derive(r, K(True))["why_not"] == ["lessons"]
    r["result"]["lessons"] = {"pending": 0, "accepted": False, "judging": 3}
    assert st.derive(r, K(False))["why_not"] == ["lessons"]
    assert st.derive(r, K(True))["settles"]                  # queued: the CDF's from here
    assert st.derive(r, K(None))["settles"]                  # no record: came the older way
    r["result"]["lessons"] = {"pending": 1, "accepted": False, "judging": 0}
    r["state_reason"] = "hive_stopped"
    assert st.derive(r, K(False))["settles"]                 # a stopped Hive sends no more


def test_hive_gone_settles_without_release(env, monkeypatch):
    rows, _ = env
    rows[DID] = _row(result=None, state_reason="hive_gone")
    d = st.derive(rows[DID], K(None))
    assert d["verdict"] == "gone" and d["settles"]
    due = st.annotate([rows[DID]])
    assert due == []
    assert st.load(DID)["settled"]["outcome"] == "gone"
    assert st.load(DID)["settled"]["released"] is None


def _client(monkeypatch, calls, code):
    class Resp:
        def __init__(self, c):
            self.status_code = c

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url):
            calls.append(url)
            return Resp(code["v"])

    async def inst(hive_id):
        return {"id": hive_id}
    monkeypatch.setattr(swarm_out, "_instance", inst)
    monkeypatch.setattr(swarm_out, "_client", lambda i, t: Client())


def test_annotate_settles_once_and_release_backs_off(env, monkeypatch):
    rows, _ = env
    rows[DID] = _row()
    st.set_verdict(DID, "pass", "", ENDED)
    row = _row()
    tell = st.annotate([row])
    assert [r["dispatch_id"] for r in tell] == [DID]
    assert row["settle"]["settled"]["released"] is False and row["stage"] == "accepted"
    assert A(row) == ["open_session:read_only"]
    # a settled task takes no new verdict and is not settled twice
    with pytest.raises(st.SettleError):
        st.set_verdict(DID, "pass", "", ENDED)
    first = st.load(DID)["settled"]["at"]
    calls, code = [], {"v": 409}
    _client(monkeypatch, calls, code)
    asyncio.run(st.release_due(tell))
    assert calls == [f"/api/hive/work/{DID}/release"]
    s = st.load(DID)["settled"]
    assert s["released"] is False and s["tries"] == 1 and s["at"] == first
    assert st.annotate([_row()]) == []                     # busy: not before the backoff
    st._update(DID, lambda rec: rec["settled"].update(next_try_at=0))
    assert st.load(DID)["settled"]["tries"] == 1
    assert st.annotate([_row(state_reason="hive_stopped")]) == []   # a stopped Hive waits
    tell = st.annotate([_row()])
    assert len(tell) == 1 and st.annotate([_row()]) == []  # handed out once
    code["v"] = 200
    asyncio.run(st.release_due(tell))
    assert st.load(DID)["settled"]["released"] is True
    tries = st.load(DID)["settled"]["tries"]
    st.mark_released(DID, False)                            # a released row is left alone
    assert st.load(DID)["settled"]["released"] is True and st.load(DID)["settled"]["tries"] == tries
    assert st.annotate([_row()]) == []


def test_release_never_raises(env, monkeypatch):
    rows, _ = env
    rows[DID] = _row()
    st.set_verdict(DID, "pass", "", ENDED)
    tell = st.annotate([_row()])

    def boom(*a):
        raise OSError("disk full")
    monkeypatch.setattr(st, "mark_released", boom)
    _client(monkeypatch, [], {"v": 200})
    asyncio.run(st.release_due(tell))                       # no exception escapes


def test_concurrent_settles_write_once(env):
    import threading
    rows, _ = env
    rows[DID] = _row()
    st.set_verdict(DID, "pass", "", ENDED)
    out, errors = [], []

    def go():
        try:
            out.append(st.annotate([_row()]))
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    assert sum(len(x) for x in out) == 1                    # settled and told once
    assert st.load(DID)["verdict"]["value"] == "pass"       # nothing lost


def test_cleanup_reminder_counts_from_the_last_change(env, monkeypatch):
    from core import hive_records
    rows, _ = env
    old = (datetime.now(timezone.utc) - timedelta(days=hive_records.RETENTION_DAYS - st.REMIND_DAYS, hours=1))
    stamp = old.strftime("%Y-%m-%dT%H:%M:%SZ")
    r = _row(sent_at=stamp)
    r["result"]["ended_at"] = stamp
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {"records": {"closed": True, "last_at": stamp}})
    st.annotate([r])
    assert r["cleanup_soon"] is True
    r = _row(sent_at=stamp)                                 # a turn ended since: not soon
    st.annotate([r])
    assert r["cleanup_soon"] is False


# ── nothing the release removes is lost ──────────────────────────────────────

def test_every_repo_must_be_handled(env):
    from core import hive_bundle
    rows, returns = env
    rows[DID] = _row("commits")
    rows[DID]["result"]["work"]["repos"] = [
        {"dir": "Hive/work/x/repos/a", "head": HEAD_A, "bundle": {"available": True}},
        {"dir": "Hive/work/x/repos/b", "head": HEAD_B, "bundle": {"available": True}}]
    returns[(DID, hive_bundle.key("Hive/work/x/repos/a"))] = {"status": "landed", "tip": HEAD_A}
    d = st.derive(rows[DID], K(True))
    assert d["code"] == "waiting" and A(d)[0] == "*accept:land" and not d["settles"]
    returns[(DID, hive_bundle.key("Hive/work/x/repos/b"))] = {"status": "rejected", "tip": HEAD_B}
    d = st.derive(rows[DID], K(True))
    assert d["code"] == "landed" and d["verdict"] == "pass" and d["settles"]


def test_an_earlier_turns_landing_does_not_land_a_later_turn(env):
    """Turn 1's commits landed; a re-run's turn 2 returned more (the result names a new
    head) that nobody took back: the run's code waits, it is not landed."""
    rows, returns = env
    rows[DID] = _with_bundle(_row("commits"))
    _code_state(returns, "landed")
    assert st.derive(rows[DID], K(True))["code"] == "landed"
    rows[DID]["result"]["work"]["repos"][0]["head"] = HEAD_B
    d = st.derive(rows[DID], K(True))
    assert d["code"] == "waiting" and d["verdict"] is None and not d["settles"]
    # a take-back state that names a mapped base keeps the Hive's commit as hive_tip
    _code_state(returns, "landed", tip="c" * 40)
    returns[next(iter(returns))]["hive_tip"] = HEAD_B
    assert st.derive(rows[DID], K(True))["code"] == "landed"


def test_unsealed_commits_hold_until_landed_by_hand_or_discarded(env):
    rows, _ = env
    for returns in ("commits", None):
        rows[DID] = _row(returns)
        rows[DID]["result"]["work"]["repos"] = [{"dir": "Hive/work/x/repos/a", "commits_total": 3,
                                                 "bundle": {"available": False, "error": "too_large"}}]
        d = st.derive(rows[DID], K(True))
        assert d["code"] == "unsealed" and A(d)[0] == "*accept:manual" and not d["settles"], returns
    # an answer task's pass does not release them either
    rows[DID] = _row("answer")
    rows[DID]["result"]["work"]["repos"] = [{"dir": "Hive/work/x/repos/a", "commits_total": 3, "bundle": None}]
    st.set_verdict(DID, "pass", "", ENDED)
    assert st.derive(rows[DID], K(True))["why_not"] == ["code"]
    st.discard(DID)
    assert st.derive(rows[DID], K(True))["settles"]


HOME_OUT = "/home/swarm/.swarm-ai/SwarmWS/Outputs/" + DID + "/r.md"


def test_files_not_placed_hold(env, monkeypatch):
    from core import hive_files
    rows, _ = env
    rows[DID] = _row("document")
    st.set_verdict(DID, "pass", "", ENDED)
    placed: list = []
    monkeypatch.setattr(hive_files, "placed", lambda d: placed)
    kept = K(True)
    kept["result"] = {"work": {"files": [{"path": HOME_OUT, "abs": HOME_OUT}]}}
    assert st.derive(rows[DID], kept)["why_not"] == ["files"]          # in the release's scope
    placed.append({"listed": HOME_OUT, "path": f"Outputs/{DID}/r.md", "local": None, "error": "OSError"})
    assert st.derive(rows[DID], kept)["why_not"] == ["files"]          # placing it failed
    placed[0]["local"] = "Projects/A/deliverables/hive/r.md"
    assert st.derive(rows[DID], kept)["settles"]


def test_a_listed_file_the_hive_reported_gone_does_not_hold(env, monkeypatch):
    """A scratch file listed, then moved or deleted before its turn ended: its Hive says
    it is gone, so nothing of it is left for the release to remove, and the report that
    came back is placed: the accepted run settles."""
    from core import hive_files
    rows, _ = env
    rows[DID] = _row("document")
    st.set_verdict(DID, "pass", "", ENDED)
    scratch = HOME_OUT.replace("r.md", "scratch.md")
    monkeypatch.setattr(hive_files, "placed", lambda d: [
        {"listed": HOME_OUT, "path": f"Outputs/{DID}/r.md", "local": "Projects/A/deliverables/hive/r.md"}])
    reasons: dict = {}
    monkeypatch.setattr(hive_files, "not_sealed", lambda d: reasons)
    kept = K(True)
    kept["result"] = {"work": {"files": [{"path": p, "abs": p} for p in (HOME_OUT, scratch)]}}
    assert st.derive(rows[DID], kept)["why_not"] == ["files"]          # no word from the Hive yet
    reasons[scratch] = "gone"
    d = st.derive(rows[DID], kept)
    assert "files" not in d["why_not"] and d["settles"]


def test_a_listed_file_under_hive_does_not_hold(env, monkeypatch):
    """A file the agent wrote under Hive/ (here its outputs path resolved under its work
    folder): its Hive never returns anything there, so no pull can bring it and the run
    settles instead of waiting until its records expire."""
    from core import hive_files
    rows, _ = env
    rows[DID] = _row("document")
    st.set_verdict(DID, "pass", "", ENDED)
    stray = f"/home/swarm/.swarm-ai/SwarmWS/Hive/work/{DID}/Outputs/{DID}/requirements.json"
    monkeypatch.setattr(hive_files, "placed", lambda d: [])
    kept = K(True)
    kept["result"] = {"work": {"files": [{"path": stray, "abs": stray}]}}
    monkeypatch.setattr(hive_files, "not_sealed", lambda d: {})
    assert st.derive(rows[DID], kept)["why_not"] == ["files"]          # no word from the Hive yet
    monkeypatch.setattr(hive_files, "not_sealed", lambda d: {stray: "hive"})
    d = st.derive(rows[DID], kept)
    assert "files" not in d["why_not"] and d["settles"]


def test_a_file_not_sealed_for_another_reason_or_unreported_still_holds(env, monkeypatch):
    """Over size, secret, a link, an error: bytes only the Hive may have, so the run
    holds; and a record from before the reasons (none kept here) holds as before."""
    from core import hive_files
    real = hive_files.not_sealed
    rows, _ = env
    rows[DID] = _row("document")
    st.set_verdict(DID, "pass", "", ENDED)
    monkeypatch.setattr(hive_files, "placed", lambda d: [])
    kept = K(True)
    kept["result"] = {"work": {"files": [{"path": HOME_OUT, "abs": HOME_OUT}]}}
    for why in ("too_large", "secret", "link", "not_regular", "unreadable", "error"):
        monkeypatch.setattr(hive_files, "not_sealed", lambda d, why=why: {HOME_OUT: why})
        assert st.derive(rows[DID], kept)["why_not"] == ["files"], why
    monkeypatch.setattr(hive_files, "not_sealed", lambda d: {"/elsewhere/r.md": "gone"})   # another file
    assert st.derive(rows[DID], kept)["why_not"] == ["files"]
    # an older copy is placed, but the newest word is that a newer one was too large
    monkeypatch.setattr(hive_files, "placed", lambda d: [
        {"listed": HOME_OUT, "path": f"Outputs/{DID}/r.md", "local": "Projects/A/deliverables/hive/r.md"}])
    monkeypatch.setattr(hive_files, "not_sealed", lambda d: {HOME_OUT: "too_large"})
    assert st.derive(rows[DID], kept)["why_not"] == ["files"]
    monkeypatch.setattr(hive_files, "placed", lambda d: [])
    monkeypatch.setattr(hive_files, "not_sealed", real)
    assert hive_files.not_sealed(DID) == {}                            # an old record: no reasons
    assert st.derive(rows[DID], kept)["why_not"] == ["files"]


def test_files_are_read_from_the_placement_record_not_the_summary(env, monkeypatch):
    """A summary written before ``abs`` / ``local`` existed (a run from before them) never
    carries ``local``: the placement record decides, and a file outside Outputs/<id>/ is
    not the release's to remove."""
    from core import hive_files
    rows, _ = env
    rows[DID] = _row(None)
    st.set_verdict(DID, "pass", "", ENDED)
    monkeypatch.setattr(hive_files, "placed", lambda d: [])
    kept = K(True)
    kept["result"] = {"work": {"files": [
        {"path": "/home/swarm/.swarm-ai/SwarmWS/Knowledge/Reports/r.md", "kind": "content"},
        {"path": "Projects/SwarmAI/.artifacts/runs/run_x/REPORT.md", "kind": "knowledge"}]}}
    assert st.derive(rows[DID], kept)["settles"]
    monkeypatch.setattr(hive_files, "placed", lambda d: [
        {"listed": HOME_OUT, "path": f"Outputs/{DID}/r.md", "local": "Projects/A/d/r.md", "error": None}])
    kept["result"] = {"work": {"files": [{"path": HOME_OUT, "kind": "content"}]}}     # no abs, no local
    assert st.derive(rows[DID], kept)["settles"]


def test_manual_landed_needs_commits_on_an_old_row(env, monkeypatch):
    rows, _ = env
    rows[DID] = _row(None)
    with pytest.raises(st.SettleError):
        st.set_manual_landed(DID)
    monkeypatch.setattr(swarm_out_results, "load",
                        lambda d: {"result": {"work": {"repos": [{"commits_total": 2}]}}})
    st.set_manual_landed(DID)


# ── nothing stays stuck ──────────────────────────────────────────────────────

def test_lessons_in_after_the_backstop_or_a_discard(env):
    rows, _ = env
    rows[DID] = _row()
    st.set_verdict(DID, "pass", "", ENDED)
    old = (datetime.now(timezone.utc) - timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh = K(False)
    assert st.derive(rows[DID], fresh)["why_not"] == ["lessons"]
    assert st.derive(rows[DID], {"records": {"closed": False, "last_at": old}})["settles"]
    st.discard(DID)
    assert st.derive(rows[DID], fresh)["settles"]


def test_a_passing_fetch_error_is_still_running(env):
    rows, _ = env
    rows[DID] = _row(result=None, state="stopped", state_reason="idle", result_status="failed",
                     result_error="hive_silent")
    assert st.derive(rows[DID], None)["stage"] == "running"


def test_a_file_that_never_came_holds_an_accepted_run(env):
    rows, _ = env
    rows[DID] = _row("document")
    st.set_verdict(DID, "pass", "", ENDED)
    kept = K(True)
    kept["result"] = {"work": {"files": [{"path": f"Outputs/{DID}/big.bin"}]}}      # skipped
    d = st.derive(rows[DID], kept)
    assert d["stage"] == "accepted" and d["why_not"] == ["files"] and not d["settles"]


def test_no_row_is_handed_out_while_a_release_runs(env):
    rows, _ = env
    rows[DID] = _row()
    st.set_verdict(DID, "pass", "", ENDED)
    assert st.annotate([_row()], can_tell=False) == []
    assert [r["dispatch_id"] for r in st.annotate([_row()])] == [DID]


def test_a_stopped_run_without_a_result_can_be_discarded(env):
    rows, _ = env
    rows[DID] = _row(result=None, state="stopped", state_reason="not_held", result_status="failed",
                     result_error="session_gone")
    d = st.derive(rows[DID], None)
    assert d["stage"] == "stopped" and d["why"] == "no_result" and A(d) == ["discard"]
    assert not d["settles"]
    st.discard(DID)
    assert st.derive(rows[DID], None)["settles"]


def test_the_settling_look_shows_the_settled_stage(env):
    rows, _ = env
    rows[DID] = _row(session_id=None, result=None, state_reason="no_session_id", state="unknown")
    st.discard(DID)
    r = dict(rows[DID])
    st.annotate([r])
    assert r["settle"]["settled"]["outcome"] == "abandoned" and r["stage"] == "closed" and r["why"] == "discarded" and r["actions"] == []


# ── §3.5: expiry ─────────────────────────────────────────────────────────────

def _idle_row(monkeypatch, days):
    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = _row(sent_at=stamp)
    r["result"]["ended_at"] = stamp
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {"records": {"closed": True, "last_at": stamp}})
    return r


def test_an_unsettled_run_expires_after_the_retention(env, monkeypatch):
    from core import hive_records
    rows, _ = env
    rows[DID] = _row()
    r = _idle_row(monkeypatch, hive_records.RETENTION_DAYS + 0.1)
    tell = st.annotate([r])
    assert st.load(DID)["settled"]["outcome"] == "expired"
    assert r["stage"] == "closed" and r["why"] == "expired" and r["actions"] == [] and r["cleanup_soon"] is False
    assert [x["dispatch_id"] for x in tell] == [DID]        # its Hive is told to release


def test_a_run_just_inside_the_retention_does_not_expire(env, monkeypatch):
    from core import hive_records
    rows, _ = env
    rows[DID] = _row()
    r = _idle_row(monkeypatch, hive_records.RETENTION_DAYS - 0.1)
    st.annotate([r])
    assert not st.load(DID).get("settled") and r["stage"] == "awaiting" and r["cleanup_soon"] is True


def test_a_running_run_does_not_expire_one_asking_nobody_answered_does(env, monkeypatch):
    from core import hive_records
    rows, _ = env
    rows[DID] = _row()
    r = _idle_row(monkeypatch, hive_records.RETENTION_DAYS + 1)
    r["state"] = "running"
    st.annotate([r])
    assert not st.load(DID).get("settled")
    r = _idle_row(monkeypatch, hive_records.RETENTION_DAYS + 1)
    r["state"] = "waiting"
    st.annotate([r])
    assert st.load(DID)["settled"]["outcome"] == "expired"


def test_closed_state_is_one_rule_for_every_refusal(env):
    rows, _ = env
    rows[DID] = _row()
    assert st.closed_state(st.load(DID)) is None
    st.set_verdict(DID, "pass", "", ENDED)                 # not settled yet: lessons may be coming
    assert st.closed_state(st.load(DID)) == "accepted"
    assert st.closed_state({"manual_landed_at": "x"}) == "accepted"
    assert st.closed_state({"settled": {"outcome": "passed"}}) == "accepted"
    for closed in ({"abandoned_at": "x"}, {"settled": {"outcome": "expired"}}, {"settled": {"outcome": "gone"}}):
        assert st.closed_state(closed) == "closed", closed


def test_path_refuses_what_is_not_a_dispatch_id():
    for bad in ("../x", "so_aaaaaaaaaaaa/../../etc", "", "so_AAAAAAAAAAAA"):
        with pytest.raises(st.SettleError):
            st._path(bad)


# ── who may give the commands on a run ───────────────────────────────────────
# Design 2026-10-31-swarm-out-agent-owns-its-runs §2.1: the dispatching agent gives them
# too (its limits are core/swarm_out_actor's, tested in test_swarm_out_actor.py); a
# Hive's wait is still answered by the person, so its route keeps every spelling refused.

_ANSWER = "curl -s -X POST http://127.0.0.1:18321/api/swarm-out/asks/hv1/sess-1/"


@pytest.mark.parametrize("tail", ["verdict", "return/prepare", "return/land",
                                  "rerun", "stop", "discard"])
def test_run_commands_are_given_through_the_cli_never_by_a_direct_call(tail):
    """The dispatching agent gives them too, through scripts.swarm_out (which says it is
    the agent's); a direct HTTP call from a command is refused in every spelling the
    server routes the same way (design 2026-10-31-swarm-out-agent-owns-its-runs §9)."""
    from core import run_owner_gate
    base = "curl -s -X POST http://127.0.0.1:18321/api/swarm-out/runs/" + DID + "/"
    enc = f"%{ord(tail[0]):02x}{tail[1:]}"
    for form in (f"{base}{tail} -d '{{}}'", f"{base}{enc}", f"{base}x/../{tail}", f"{base}./{tail}"):
        assert run_owner_gate._user_only_deny(form) is not None, form


def test_landed_by_hand_stays_the_persons():
    from core import run_owner_gate
    base = "curl -s -X POST http://127.0.0.1:18321/api/swarm-out/runs/" + DID + "/"
    assert run_owner_gate._user_only_deny(f"{base}manual-landed -d '{{}}'") is not None


def test_answering_a_hives_wait_stays_refused_in_every_spelling():
    from core import run_owner_gate
    tail = "answer"
    assert run_owner_gate._user_only_deny(f"{_ANSWER}req1/{tail} -d '{{}}'") is not None
    # the forms the server routes the same way
    enc = f"%{ord(tail[0]):02x}{tail[1:]}"
    for form in (f"{_ANSWER}req1/{enc}", f"{_ANSWER}req1/%25{ord(tail[0]):02x}{tail[1:]}",
                 f"{_ANSWER}req1/x/../{tail}", f"{_ANSWER}req1/./{tail}"):
        assert run_owner_gate._user_only_deny(form) is not None, form


@pytest.mark.parametrize("cmd", [
    # the same actions through the Host proxy to the run's Hive session
    "curl -s -X POST http://127.0.0.1:18321/api/swarm-out/remote/sess-1/chat/stream -d '{}'",
    "curl -s -X POST http://127.0.0.1:18321/api/swarm-out/remote/sess-1/chat/stop/sess-1",
    "curl -s -X POST http://127.0.0.1:18321/api/swarm-out/remote/sess-1/chat/answer-question",
    "curl -s -X POST http://127.0.0.1:18321/api/swarm-out/remote/sess-1/chat/cmd-permission-continue",
    "curl -s -X POST http://127.0.0.1:18321/api/swarm-out/remote/sess-1/chat/compact/sess-1",
    # quotes the shell removes
    'curl -s -X POST "http://127.0.0.1:18321/api/swarm-out/asks/"$H"/sess-1/req1/answer"',
    "curl -s -X POST http://127.0.0.1:18321/api/swarm-out/asks/'\"$H\"'/sess-1/req1/answer",
    'curl -s -X POST http://127.0.0.1:18321/api/swarm-out/asks/$H/sess-1/req1/ans""wer',
    "curl -s -X POST http://127.0.0.1:18321/api/swarm-out/asks/$H/sess-1/req1/an\\swer",
    # the chat's own question card (P10): its answer is recorded as the person's
    "curl -s -X POST http://127.0.0.1:18321/api/chat/answer-question -d '{}'",
    'curl -s -X POST "http://127.0.0.1:18321/api/chat/"answer-question',
])
def test_aliases_and_quoted_forms_are_refused(cmd):
    from core import run_owner_gate
    assert run_owner_gate._user_only_deny(cmd) is not None, cmd


def test_reading_a_runs_session_through_the_proxy_is_not_refused():
    from core import run_owner_gate
    for cmd in ("curl -s http://127.0.0.1:18321/api/swarm-out/remote/sess-1/chat/sessions/sess-1",
                "curl -s http://127.0.0.1:18321/api/swarm-out/remote/sess-1/chat/sessions/sess-1/messages",
                "curl -s http://127.0.0.1:18321/api/swarm-out/runs"):
        assert run_owner_gate._user_only_deny(cmd) is None, cmd


def test_the_live_hook_refuses_answering_a_hives_wait():
    from core import run_owner_gate
    gate = run_owner_gate.create_run_owner_gate({"sdk_session_id": "s1"})
    cmd = _ANSWER + "req1/answer"
    out = asyncio.run(gate({"tool_name": "Bash", "tool_input": {"command": cmd}}, "t1", None))
    assert (out.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"


# ── how the run's session ended (``end``): written once, never by a later machine state ──

def _commands(*cmds):
    """Commands as the run's record keeps them (core/swarm_out_record.entry)."""
    def change(rec):
        rec.setdefault("commands", []).extend(
            {"at": at, "by": by, "decision": c, "command": c} for c, by, at in cmds)
    st._update(DID, change)


def test_end_is_written_once_and_never_by_a_later_machine_state(env, monkeypatch):
    rows, _ = env
    rows[DID] = _row()
    r = _row()
    st.annotate([r])
    assert r["end"] == {"reason": "completed", "at": ENDED}
    assert st.load(DID)["end"] == {"reason": "completed", "at": ENDED}
    # later looks: the Hive is stopped now, or the Elastic carrier's lifetime passed, and
    # the result reads otherwise; state_reason stays the machine's state of the moment
    for reason, ended in (("hive_stopped", "daemon_stopped"), ("carrier_expired", "crashed")):
        later = _row(state_reason=reason)
        later["result"]["ended"] = ended
        later["result"]["ended_at"] = "2026-10-30T18:00:00Z"
        st.annotate([later])
        assert later["end"] == {"reason": "completed", "at": ENDED}
        assert later["state_reason"] == reason
    assert st.load(DID)["end"] == {"reason": "completed", "at": ENDED}
    # a record that cannot be written: the row lists without an end, nothing raises
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: __import__("pathlib").Path("/proc/no-such-dir"))
    other = _row()
    st.annotate([other])
    assert other.get("end") is None


@pytest.mark.parametrize("ended,cmds,want", [
    ("completed", (), "completed"),
    ("turn_limit", (), "turn_limit"),
    ("error", (), "crashed"),
    ("crashed", (), "crashed"),
    ("gave_up", (), "crashed"),
    ("daemon_stopped", (), "hive_stopped"),
    ("stopped", (("stop", "agent", "2026-10-30T09:59:00Z"),), "stopped_by_agent"),
    ("stopped", (("stop", "agent", "2026-10-30T09:30:00Z"), ("discard", "person", "2026-10-30T09:59:30Z")),
     "stopped_by_person"),
    ("stopped", (), "stopped_by_person"),                    # stopped on the Hive's own page
    ("stopped", (("stop", "agent", "2026-10-30T11:00:00Z"),), "stopped_by_person"),  # after it ended
    # the agent stopped the turn before the re-send; this turn was stopped on the Hive's page
    ("stopped", (("stop", "agent", "2026-10-30T08:00:00Z"),), "stopped_by_person"),
    ("paused", (), None),                                    # the session did not end
    ("no_terminal", (), None),
])
def test_end_reason_of_each_ending(env, monkeypatch, ended, cmds, want):
    rows, _ = env
    rows[DID] = _row()
    # the turn this result ends was sent at 09:00 (a re-send)
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {
        "records": {"closed": True}, "resent_at": "2026-10-30T09:00:00Z",
        "result": {"ended": "completed", "provisional": False, "ended_at": ENDED}})
    _commands(*cmds)
    r = _row()
    r["result"]["ended"] = ended
    st.annotate([r])
    assert (r.get("end") or {}).get("reason") == want
    assert st.load(DID).get("end") == ({"reason": want, "at": ENDED} if want else None)


def test_end_when_no_result_can_come(env, monkeypatch):
    rows, _ = env
    rows[DID] = _row()
    mark = {"round": 1, "run_ref": "s1", "checked_at": "2026-10-30T19:00:00Z"}
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {"records": {"closed": False}})
    # a Hive that is only stopped, or an Elastic round past its lifetime whose store was
    # not read whole yet, may still bring the result: no end yet
    for reason in ("hive_stopped", "carrier_expired"):
        r = _row(result=None, state_reason=reason, result_status="pending")
        st.annotate([r])
        assert r.get("end") is None and "end" not in st.load(DID)
    # an Elastic round past its lifetime whose store was read whole: carrier_expired
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {"records": {"closed": False}, "round_end": mark})
    monkeypatch.setattr(swarm_out_results, "round_end_of", lambda row, kept: mark)
    r = _row(result=None, state_reason="carrier_expired", result_status="pending")
    st.annotate([r])
    assert r["end"] == {"reason": "carrier_expired", "at": mark["checked_at"]}


@pytest.mark.parametrize("over", [
    {"state_reason": "hive_gone"},
    {"state_reason": "not_held", "result_status": "failed", "result_error": "session_gone"},
])
def test_a_session_gone_without_a_result_ends_lost(env, over):
    rows, _ = env
    rows[DID] = _row()
    r = _row(result=None, **{"result_status": "pending", **over})
    st.annotate([r])
    assert r["end"]["reason"] == "lost" and st.load(DID)["end"]["reason"] == "lost"


def test_a_turn_cut_off_by_the_timer_gets_no_end(env, monkeypatch):
    """``interrupted`` is a timer's guess: the real ending may still come."""
    rows, _ = env
    rows[DID] = _row()
    long_ago = "2026-10-30T09:00:00Z"
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {"records": {"closed": True},
                                                            "no_record_since": long_ago})
    r = _row()
    r["result"]["ended"] = "no_record"
    assert st.interrupted(r, r["result"], swarm_out_results.load(DID))
    st.annotate([r])
    assert r.get("end") is None and "end" not in st.load(DID)


def test_a_rerun_gets_its_own_end(env, monkeypatch):
    rows, _ = env
    rows[DID] = _row()
    st.annotate([_row()])
    assert st.load(DID)["end"]["at"] == ENDED
    # a correction went into the same session after that end: the old end is not shown
    # while the new turn runs, and the new turn's end is written
    resent = "2026-10-30T12:00:00Z"
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {"records": {"closed": True}, "resent_at": resent})
    r = _row(state="running", state_reason="streaming")
    r["result"]["provisional"] = True
    st.annotate([r])
    assert r.get("end") is None and st.load(DID)["end"]["at"] == ENDED
    r = _row()
    r["result"]["ended"], r["result"]["ended_at"] = "crashed", "2026-10-30T13:00:00Z"
    st.annotate([r])
    assert r["end"] == {"reason": "crashed", "at": "2026-10-30T13:00:00Z"}
    assert st.load(DID)["end"] == {"reason": "crashed", "at": "2026-10-30T13:00:00Z"}


def test_a_stopped_hive_without_a_result_is_not_running(env):
    rows, _ = env
    rows[DID] = _row()
    for res in (None, {"ended": "completed", "provisional": True, "ended_at": ENDED, "lessons": None,
                       "work": {"repos": []}}):
        r = _row(result=res, state_reason="hive_stopped", result_status="pending")
        d = st.derive(r, K(True))
        assert d["stage"] == "stopped" and d["why"] == "hive_stopped"
        assert "stop" not in [a["key"] for a in d["actions"]] and not d["settles"]
    # a result that did come is judged as before
    assert st.derive(_row(state_reason="hive_stopped"), K(True))["stage"] == "awaiting"
