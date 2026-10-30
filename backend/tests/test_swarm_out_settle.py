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
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {"records": {"closed": True}})
    return rows, returns


def K(closed):
    """The kept record derive reads: its records' closed state (None: no record)."""
    if closed is None:
        return None
    return {"records": {"closed": closed, "last_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}}


def _row(returns="answer", **kw):
    r = {"dispatch_id": DID, "hive_id": "h1", "session_id": "s1",
         "sent_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "project_agent": {"returns": returns}, "waiting": False, "running": False,
         "state_reason": "idle",
         "result": {"ended": "completed", "provisional": False, "ended_at": ENDED,
                    "lessons": None, "work": {"repos": []}}}
    r.update(kw)
    return r


def _with_bundle(row):
    row["result"]["work"]["repos"] = [{"dir": "Hive/work/x/repos/a", "bundle": {"available": True}}]
    return row


def _code_state(returns, status):
    from core import hive_bundle
    returns[(DID, hive_bundle.key("Hive/work/x/repos/a"))] = {"status": status}


def A(d):
    """The actions as written in §1.2: ``*`` marks the primary, ``:variant`` follows."""
    return [("*" if a["primary"] else "") + a["key"] + (":" + a["variant"] if a.get("variant") else "")
            for a in d["actions"]]


# ── §1.2: one stage, and exactly its actions ─────────────────────────────────

def test_stage_order(env):
    rows, _ = env
    rows[DID] = _row()
    d = st.derive(_row(waiting=True, running=True), K(True))
    assert d["stage"] == "needs_answer" and A(d) == ["*answer", "open_session", "discard"]
    d = st.derive(_row(running=True), K(True))
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


@pytest.mark.parametrize("stage,alive,accept,want", [
    ("running", True, None, ["*stop", "open_session", "discard"]),
    ("running", False, None, ["*stop", "discard"]),
    ("needs_answer", True, None, ["*answer", "open_session", "discard"]),
    ("awaiting", True, "verdict", ["*accept:verdict", "rerun", "open_session", "discard"]),
    ("awaiting", True, "land", ["*accept:land", "rerun", "open_session", "discard"]),
    ("awaiting", True, "manual", ["*accept:manual", "rerun", "open_session", "discard"]),
    ("awaiting", True, None, ["*rerun", "open_session", "discard"]),
    ("awaiting", False, "verdict", ["*accept:verdict", "discard"]),
    ("stopped", True, None, ["*rerun", "open_session", "discard"]),
    ("stopped", False, None, ["discard"]),
    ("accepted", True, None, ["open_session:read_only"]),
    ("accepted", False, None, []),
    ("discarded", True, None, []),
    ("expired", True, None, []),
])
def test_every_stage_offers_exactly_its_row(stage, alive, accept, want):
    got = st._actions(stage, alive, accept)
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
    assert d["verdict"] == "abandoned" and d["settles"] and d["stage"] == "discarded" and A(d) == []
    with pytest.raises(st.SettleError):
        st.discard(DID)


def test_no_session_row_ends_only_by_discarding(env):
    rows, _ = env
    rows[DID] = _row(session_id=None, result=None, state_reason="no_session_id", running=None)
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
    rows[DID] = _row(result=None, state_reason="hive_gone", running=False)
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
        {"dir": "Hive/work/x/repos/a", "bundle": {"available": True}},
        {"dir": "Hive/work/x/repos/b", "bundle": {"available": True}}]
    returns[(DID, hive_bundle.key("Hive/work/x/repos/a"))] = {"status": "landed"}
    d = st.derive(rows[DID], K(True))
    assert d["code"] == "waiting" and A(d)[0] == "*accept:land" and not d["settles"]
    returns[(DID, hive_bundle.key("Hive/work/x/repos/b"))] = {"status": "rejected"}
    d = st.derive(rows[DID], K(True))
    assert d["code"] == "landed" and d["verdict"] == "pass" and d["settles"]


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
    rows[DID] = _row(session_id=None, result=None, state_reason="no_session_id", running=None)
    st.discard(DID)
    r = dict(rows[DID])
    st.annotate([r])
    assert r["settle"]["settled"]["outcome"] == "abandoned" and r["stage"] == "discarded" and r["actions"] == []


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
    assert r["stage"] == "expired" and r["actions"] == [] and r["cleanup_soon"] is False
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
    r["running"] = True
    st.annotate([r])
    assert not st.load(DID).get("settled")
    r = _idle_row(monkeypatch, hive_records.RETENTION_DAYS + 1)
    r["waiting"] = True
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


# ── the person's actions only ────────────────────────────────────────────────

@pytest.mark.parametrize("tail", ["verdict", "manual-landed", "return/land",
                                  "rerun", "stop", "discard"])
def test_settle_routes_refused_from_a_command(tail):
    from core import run_owner_gate
    base = "curl -s -X POST http://127.0.0.1:18321/api/swarm-out/runs/" + DID + "/"
    deny = run_owner_gate._user_only_deny(f"{base}{tail} -d '{{}}'")
    assert deny is not None
    assert run_owner_gate._user_only_deny(
        f"curl -s http://127.0.0.1:18321/api/swarm-out/runs/{DID}/result") is None
    # the forms the server routes the same way
    enc = f"%{ord(tail[0]):02x}{tail[1:]}"
    for form in (f"{base}{enc}", f"{base}%25{ord(tail[0]):02x}{tail[1:]}", f"{base}x/../{tail}", f"{base}./{tail}"):
        assert run_owner_gate._user_only_deny(form) is not None, form


@pytest.mark.parametrize("cmd", [
    # the same actions through the Host proxy to the run's Hive session
    "curl -s -X POST http://127.0.0.1:18321/api/swarm-out/remote/sess-1/chat/stream -d '{}'",
    "curl -s -X POST http://127.0.0.1:18321/api/swarm-out/remote/sess-1/chat/stop/sess-1",
    "curl -s -X POST http://127.0.0.1:18321/api/swarm-out/remote/sess-1/chat/answer-question",
    "curl -s -X POST http://127.0.0.1:18321/api/swarm-out/remote/sess-1/chat/cmd-permission-continue",
    "curl -s -X POST http://127.0.0.1:18321/api/swarm-out/remote/sess-1/chat/compact/sess-1",
    # quotes the shell removes
    'curl -s -X POST "http://127.0.0.1:18321/api/swarm-out/runs/"$D"/discard"',
    "curl -s -X POST http://127.0.0.1:18321/api/swarm-out/runs/'\"$D\"'/stop",
    'curl -s -X POST http://127.0.0.1:18321/api/swarm-out/runs/$D/disc""ard',
    "curl -s -X POST http://127.0.0.1:18321/api/swarm-out/runs/$D/re\\run",
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


def test_the_live_hook_refuses_a_settle_route():
    from core import run_owner_gate
    gate = run_owner_gate.create_run_owner_gate({"sdk_session_id": "s1"})
    cmd = "curl -s -X POST http://127.0.0.1:18321/api/swarm-out/runs/" + DID + "/discard"
    out = asyncio.run(gate({"tool_name": "Bash", "tool_input": {"command": cmd}}, "t1", None))
    assert (out.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"
