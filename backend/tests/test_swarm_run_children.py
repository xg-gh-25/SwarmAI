"""A Swarm Run's children on the Host: who answers for them and what no one may do with
them (design docs/design/2026-10-31-swarm-run.md §5.3, §5.6, §6; impl
docs/design/2026-11-03-swarm-run-impl.md step 2-3; invariants I2, I3, I6).

The lead run is faked at ``core.swarm_run.lead`` (its owner and status as the audit log
would say); the children are dispatch-log rows with ``parent_run``.
"""
from __future__ import annotations

import asyncio

import pytest

from core import swarm_out, swarm_out_actor as act, swarm_run
from tests.test_task_spec import ws  # noqa: F401 — the fixture

RUN = "run_0123456789ab"
LEAD = "s-lead01"
DID = "so_cccccccccccc"


@pytest.fixture
def leads(monkeypatch):
    """run_id -> Lead; tests change the owner or status in place."""
    table = {RUN: swarm_run.Lead(RUN, "Lib", LEAD, "active", "build")}
    monkeypatch.setattr(swarm_run, "lead", lambda run_id: table.get(run_id))
    monkeypatch.setattr(swarm_run, "_INFLIGHT", {})
    return table


def _child(**kw):
    r = {"dispatch_id": DID, "state": "stopped", "stage": "awaiting", "parent_run": RUN, "wp": "W1",
         "project": "Lib", "dispatched_from": LEAD,
         "result": {"ended": "completed", "provisional": False, "ended_at": "2026-11-03T00:00:00Z"},
         "settle": {"why_not": ["verdict"]},
         "actions": [{"key": "discard"}, {"key": "stop"}],
         "lead_actions": [{"key": "accept", "variant": "child"}, {"key": "rerun"}, {"key": "discard"},
                          {"key": "stop"}]}
    r.update(kw)
    return r


# ── who answers for a child ─────────────────────────────────────────────────

def test_a_child_is_its_lead_runs_current_owners(leads):
    assert swarm_run.owner_of(_child()) == LEAD
    # the lead run was taken over: responsibility follows the run, not the dispatch
    leads[RUN] = swarm_run.Lead(RUN, "Lib", "s-new002", "active", "build")
    assert swarm_run.owner_of(_child()) == "s-new002"
    # a lead run that cannot be read: nobody answers for the child
    leads.clear()
    assert swarm_run.owner_of(_child()) is None
    # an ordinary run: the session that dispatched it
    assert swarm_run.owner_of({"dispatched_from": "s-abc123"}) == "s-abc123"
    assert swarm_run.owner_of({}) is None


def test_commands_on_a_child_follow_the_lead_runs_owner(leads):
    lead, old = act.actor("agent", LEAD, "w"), act.actor("agent", "s-new002", "w")
    assert act.row_blocker(lead, "stop", _child()) is None
    assert "another session" in act.row_blocker(old, "stop", _child())
    leads[RUN] = swarm_run.Lead(RUN, "Lib", "s-new002", "active", "build")
    assert act.row_blocker(old, "stop", _child()) is None
    assert "another session" in act.row_blocker(lead, "stop", _child())
    leads.clear()
    assert "cannot be read" in act.row_blocker(lead, "stop", _child())


def test_no_one_lands_a_child_and_its_commits_may_still_be_taken_back(leads):
    """I3: return-land and manual-landed are refused on a child, for the person too;
    return-prepare (only into refs/hive) stays the lead run's."""
    person, lead = act.actor("person", None, ""), act.actor("agent", LEAD, "w")
    for who in (person, lead):
        for cmd in ("return_land", "manual_landed"):
            assert "child of Swarm Run" in act.row_blocker(who, cmd, _child()), (who, cmd)
    assert act.row_blocker(lead, "return_prepare", _child()) is None
    # an ordinary run is not affected
    assert act.row_blocker(person, "return_land", _child(parent_run=None)) is None


def test_a_child_is_never_handed_to_the_person(leads, monkeypatch):
    from core import swarm_out_record
    with pytest.raises(swarm_out_record.RecordError, match="child of a Swarm Run"):
        swarm_out_record.ask_person(DID, _child(stage="judging"), LEAD, "keep it?", "fine", "taste")


# ── dispatching a child ─────────────────────────────────────────────────────

def test_only_the_lead_runs_owner_dispatches_its_children_and_at_most_three_times(leads):
    rows = []
    with pytest.raises(swarm_run.ChildRefused, match="only the session that drives"):
        swarm_run.check_child_dispatch(RUN, "W1", "s-other", rows)
    with pytest.raises(swarm_run.ChildRefused, match="work package id"):
        swarm_run.check_child_dispatch(RUN, "W 1", LEAD, rows)
    with pytest.raises(swarm_run.ChildRefused, match="not a pipeline run"):
        swarm_run.check_child_dispatch("run_ffffffffffff", "W1", LEAD, rows)
    # two logged dispatches of W1 and one in flight: a fourth is refused until one ends
    rows = [{"parent_run": RUN, "wp": "W1"}, {"parent_run": RUN, "wp": "W1"}, {"parent_run": RUN, "wp": "W2"}]
    swarm_run.check_child_dispatch(RUN, "W1", LEAD, rows)
    with pytest.raises(swarm_run.ChildRefused, match="dispatched 3 times"):
        swarm_run.check_child_dispatch(RUN, "W1", LEAD, rows)
    swarm_run.release(RUN, "W1")
    swarm_run.check_child_dispatch(RUN, "W1", LEAD, rows)          # the in-flight one ended unsent
    swarm_run.check_child_dispatch(RUN, "W2", LEAD, rows)          # another package counts alone
    leads[RUN] = swarm_run.Lead(RUN, "Lib", LEAD, "completed", "reflect")
    with pytest.raises(swarm_run.ChildRefused, match="has ended"):
        swarm_run.check_child_dispatch(RUN, "W3", LEAD, rows)


def test_a_child_dispatch_is_recorded_with_its_lead_run_and_package(leads, ws, monkeypatch):  # noqa: F811
    """I1: a child's dispatch record is the standard one plus parent_run, wp, project."""
    from core.project_agent import revision
    from tests.test_task_spec import _brain, _fake_hive
    rev = revision(_brain(ws))
    calls = _fake_hive(monkeypatch, "0" * 64)
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [])
    asyncio.run(swarm_out.dispatch_spec("hive3", "t", "Lib", "fix it", "tests pass", execution="native",
                                        returns="commits", seen_revision=rev, dispatched_from=LEAD,
                                        parent_run=RUN, wp="W1"))
    extra = calls["dispatch"][-1]["extra"]
    assert (extra["parent_run"], extra["wp"], extra["project"], extra["dispatched_from"]) == (RUN, "W1", "Lib", LEAD)
    assert swarm_run._INFLIGHT == {}                                  # released when it ended
    with pytest.raises(swarm_out.SwarmOutError, match="only the session that drives"):
        asyncio.run(swarm_out.dispatch_spec("hive3", "t", "Lib", "fix it", "tests pass", execution="native",
                                            returns="commits", seen_revision=rev, dispatched_from="s-other",
                                            parent_run=RUN, wp="W1"))
    leads[RUN] = swarm_run.Lead(RUN, "Other", LEAD, "active", "build")
    with pytest.raises(swarm_out.SwarmOutError, match="its children go under that Brain"):
        asyncio.run(swarm_out.dispatch_spec("hive3", "t", "Lib", "fix it", "tests pass", execution="native",
                                            returns="commits", seen_revision=rev, dispatched_from=LEAD,
                                            parent_run=RUN, wp="W1"))
    assert swarm_run._INFLIGHT == {}


# ── following and waking a child ────────────────────────────────────────────

def test_the_lead_runs_owner_is_woken_for_its_children(leads, monkeypatch):
    from core import swarm_out_record, swarm_out_wake
    monkeypatch.setattr(swarm_out_record, "owner_alive", lambda r: True)
    monkeypatch.setattr(swarm_out_record, "actionable", lambda r, asks: True)
    data = {"runs": [_child(), _child(dispatch_id="so_dddddddddddd", parent_run=None,
                                      dispatched_from="s-abc123")], "asks": []}
    assert swarm_out_wake.candidates(data) == {LEAD: [DID], "s-abc123": ["so_dddddddddddd"]}
    leads[RUN] = swarm_run.Lead(RUN, "Lib", "s-new002", "active", "build")
    assert swarm_out_wake.candidates(data)["s-new002"] == [DID]


def test_the_lead_run_is_read_from_its_audit_log_and_follows_a_takeover(tmp_path, monkeypatch):
    """``swarm_run.lead`` on a real v1 run: the creator owns it, a logged takeover moves
    it, and the read is cached until the log changes."""
    from tests.pipeline_v1_rig import PROJECT, SESSION, Rig
    rig = Rig(tmp_path, monkeypatch, profile="direct")
    rig.create()
    monkeypatch.setattr(swarm_run, "_CACHE", {})
    found = swarm_run.lead(rig.run_id)
    assert (found.project, found.owner, found.status, found.ended) == (PROJECT, SESSION, "active", False)
    assert swarm_run.lead(rig.run_id) is found                          # cached: the log did not change
    assert rig.engine().transfer_owner("bbbbbbbb-1111-2222-3333-444444444444", reason="takeover") is True
    assert swarm_run.lead(rig.run_id).owner == "bbbbbbbb-1111-2222-3333-444444444444"
    assert swarm_run.lead("run_ffffffffffff") is None and swarm_run.lead("../x") is None


# ── a child's outcome: handled by its Swarm Run (§5.7, I10, I12) ────────────

from tests.test_swarm_out_settle import A, K, env, _row as _srow, _with_bundle, _code_state  # noqa: E402,F401
from core import swarm_out_settle as st  # noqa: E402

SDID = "so_aaaaaaaaaaaa"
ENDED = "2026-10-30T10:00:00Z"


def _schild(returns="commits", **kw):
    return _srow(returns, parent_run=RUN, wp="W1", dispatched_from=LEAD, **kw)


def test_the_person_is_not_offered_taking_or_correcting_a_child_and_the_lead_run_is(leads, env):
    rows, _ = env
    rows[SDID] = _schild()
    d = st.derive(_schild(), K(True))
    assert d["stage"] == "awaiting" and A(d) == ["open_session", "discard"]
    assert [a["key"] + (":" + a["variant"] if a.get("variant") else "") for a in d["lead_actions"]] == \
        ["accept:child", "rerun", "open_session", "discard"]
    row = {**_schild(), **d}
    person, lead = act.actor("person", None, ""), act.actor("agent", LEAD, "w")
    assert "lead run takes or corrects it" in act.row_blocker(person, "accept", row, ENDED)
    assert "lead run takes or corrects it" in act.row_blocker(person, "rerun", row)
    assert act.row_blocker(person, "discard", row) is None
    assert act.row_blocker(lead, "accept", row, ENDED) is None
    assert act.row_blocker(lead, "rerun", row) is None
    # an ordinary commits run is unchanged: the person is offered no accept until it lands
    plain = st.derive(_srow("commits"), K(True))
    assert "lead_actions" not in plain


def test_taking_a_child_needs_its_commits_here_and_then_settles_it_as_handled(leads, env, monkeypatch):
    rows, returns = env
    rows[SDID] = _with_bundle(_schild())
    monkeypatch.setattr(act, "commits_here", lambda d, res: False)
    monkeypatch.setattr(st, "_brought_commits", lambda d: True)
    with pytest.raises(st.SettleError, match="take them back first"):
        st.set_verdict(SDID, "pass", "W1 is what the run needs", ENDED)
    monkeypatch.setattr(act, "commits_here", lambda d, res: True)
    with pytest.raises(st.SettleError, match="result changed"):
        st.set_verdict(SDID, "pass", "W1", "2026-10-29T00:00:00Z")
    assert st.set_verdict(SDID, "pass", "W1 is what the run needs", ENDED) == {"handled": True}
    rec = st.load(SDID)
    assert rec["handled"]["by"] == "lead" and st.closed_state(rec) == "accepted"
    d = st.derive(_with_bundle(_schild()), K(True))          # its commits never land here
    assert (d["stage"], d["why"], d["verdict"], d["why_not"]) == ("accepted", "swarm_run", "handled", [])
    assert d["settles"]
    tell = st.annotate([_with_bundle(_schild())])
    assert st.load(SDID)["settled"]["outcome"] == "handled" and len(tell) == 1
    # an ordinary run cannot be handled
    rows[SDID] = _srow("answer")
    with pytest.raises(st.SettleError, match="only a Swarm Run's child"):
        st.set_handled(SDID, "x", ENDED)


def test_the_children_of_an_ended_lead_run_are_stopped_and_handled(leads, env, monkeypatch):
    from core import swarm_run_conductor
    rows, _ = env
    rows[SDID] = _schild(state="running")
    rows["so_bbbbbbbbbbbb"] = _schild(dispatch_id="so_bbbbbbbbbbbb")
    stopped = []

    async def stop(did):
        stopped.append(did)
        return {"stopped": True}
    monkeypatch.setattr(swarm_out, "stop_run", stop)
    data = {"runs": list(rows.values())}
    assert asyncio.run(swarm_run_conductor.end_children(data)) == []          # the run is still going
    leads[RUN] = swarm_run.Lead(RUN, "Lib", LEAD, "completed", "reflect")
    assert sorted(asyncio.run(swarm_run_conductor.end_children(data))) == ["so_aaaaaaaaaaaa", "so_bbbbbbbbbbbb"]
    assert stopped == [SDID]                                                    # only the running one
    assert st.load(SDID)["handled"]["by"] == "run_end"
    assert asyncio.run(swarm_run_conductor.end_children(data)) == []          # once


# ── the freeze: one Brain, one definition, one model per Swarm Run (§5.1, I7, I15) ──

def _child_dispatch(rev, **kw):
    return asyncio.run(swarm_out.dispatch_spec("hive3", "t", "Lib", "fix it", "tests pass", execution="native",
                                               returns="commits", seen_revision=rev, dispatched_from=LEAD,
                                               parent_run=RUN, **kw))


def test_every_child_gets_the_brain_and_model_frozen_at_the_first(leads, ws, monkeypatch):
    from core import swarm_out_model
    from core.project_agent import revision
    from tests.test_task_spec import _brain, _fake_hive
    b = _brain(ws)
    rev = revision(b)
    calls = _fake_hive(monkeypatch, "0" * 64)
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [])
    monkeypatch.setattr(swarm_out_model, "host_model", lambda: "model-a")
    first = _child_dispatch(rev, wp="W1")
    assert first["frozen"]["revision"] == rev and first["frozen"]["model"] == "model-a"
    pkg = calls["packages"][-1]
    assert pkg is not None and pkg[1] == first["frozen"]["digest"]
    # the host learns more and changes its model: the next child still gets the frozen ones
    (b / "2-understanding" / "PRODUCT.md").write_text("# PRODUCT\nlearned since\n")
    monkeypatch.setattr(swarm_out_model, "host_model", lambda: "model-b")
    second = _child_dispatch(rev, wp="W2")
    assert calls["packages"][-1] == pkg and second["frozen"]["digest"] == first["frozen"]["digest"]
    assert calls["dispatch"][-1]["model"] == "model-a"
    # the Project Agent changed during the run: a child is refused, not sent on another one
    (b / "project-agent.yaml").write_text((b / "project-agent.yaml").read_text() + "\n# changed\n")
    with pytest.raises(swarm_out.SwarmOutError, match="changed during Swarm Run"):
        _child_dispatch(revision(b), wp="W3")


def test_another_version_of_a_frozen_brain_is_not_put_where_the_run_uses_it(leads, ws, monkeypatch):
    from core.project_agent import revision
    from tests.test_task_spec import _brain, _fake_hive
    b = _brain(ws)
    rev = revision(b)
    _fake_hive(monkeypatch, "0" * 64)
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [])
    _child_dispatch(rev, wp="W1")
    log = [{"dispatch_id": DID, "parent_run": RUN, "wp": "W1", "project": "Lib", "hive_id": "i1"}]
    monkeypatch.setattr(swarm_out, "_read_log", lambda: log)
    (b / "2-understanding" / "PRODUCT.md").write_text("# PRODUCT\nanother version\n")
    plain = dict(execution="native", returns="commits", seen_revision=rev, dispatched_from="s-abc123")
    with pytest.raises(swarm_out.SwarmOutError, match="holds Lib for a running Swarm Run"):
        asyncio.run(swarm_out.dispatch_spec("hive3", "t", "Lib", "x", "y", **plain))
    # the run's own children may go there, and once it has ended anyone may
    _child_dispatch(rev, wp="W2")
    leads[RUN] = swarm_run.Lead(RUN, "Lib", LEAD, "completed", "reflect")
    asyncio.run(swarm_out.dispatch_spec("hive3", "t", "Lib", "x", "y", **plain))
    # pick_hive leaves the held Hives out, and says so when nothing else is left
    hives = [{"id": "i1", "name": "a", "status": "running", "reachable": True, "saturated": False,
              "streaming": 0, "capable": True},
             {"id": "i2", "name": "b", "status": "running", "reachable": True, "saturated": False,
              "streaming": 3, "capable": True}]
    assert swarm_out.pick_hive(hives, exclude=frozenset({"i1"}))[0] == "b"
    with pytest.raises(swarm_out.SwarmOutError, match="holds this Brain"):
        swarm_out.pick_hive(hives, exclude=frozenset({"i1", "i2"}))


def test_an_ended_runs_freeze_is_dropped(leads, env, monkeypatch, tmp_path):
    from core import swarm_run_conductor
    d = swarm_run._freeze_dir(RUN)
    d.mkdir(parents=True)
    (d / "freeze.json").write_text('{"brain": "Lib", "digest": "x", "revision": "r"}')
    rows, _ = env
    rows[SDID] = _schild(state="stopped")
    leads[RUN] = swarm_run.Lead(RUN, "Lib", LEAD, "abandoned", "build")
    asyncio.run(swarm_run_conductor.end_children({"runs": list(rows.values())}))
    assert swarm_run.frozen(RUN) is None and not d.exists()
