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
         "actions": [{"key": "accept", "variant": "land"}, {"key": "rerun"}, {"key": "discard"},
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
