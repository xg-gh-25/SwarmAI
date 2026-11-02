"""Swarm Out run states after design 2026-11-02-swarm-out-hive-deaths-diagnostics-states
§3 (S1, S3, S5).

Questions each test answers:
- is a run whose Hive does not answer (``state`` unknown) kept from settling, which
  would tell its Hive to release the workspace, even with a pass verdict, while the same
  run settles once its Hive answers that it stopped? (S3)
- does an agent's discard of such a run stay refused? (S3)
- does such a run still expire once nothing changed in it for RETENTION_DAYS, so a Hive
  that never answers again does not keep it open for good? (S3)
- do the rows ``list_runs`` returns carry ``state`` and no ``running`` / ``waiting``,
  and does no row carry ``settle.verdict``? (S3, S5)
- does every way a run ends give the one stage ``closed``, with how it ended in
  ``why``, on a live row and on a settled one? (S1)
- does a closed run built through ``derive`` keep its waits out of Needs You? (S1)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from core import hive_return, swarm_out, swarm_out_actor as act, swarm_out_results, swarm_out_settle as st

DID = "so_bbbbbbbbbbbb"
ENDED = "2026-10-30T10:00:00Z"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)
    rows: dict[str, dict] = {}

    def log_row(dispatch_id):
        if dispatch_id not in rows:
            raise swarm_out.SwarmOutError("no dispatch")
        return rows[dispatch_id]
    monkeypatch.setattr(swarm_out, "_log_row_of", log_row)
    monkeypatch.setattr(hive_return, "load", lambda d, k: None)
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {
        "records": {"closed": True},
        "result": {"ended": "completed", "provisional": False, "ended_at": ENDED}})
    return rows


def _row(**kw):
    r = {"dispatch_id": DID, "hive_id": "h1", "session_id": "s1",
         "sent_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "project_agent": {"returns": "answer"}, "state": "stopped", "state_reason": "idle",
         "result": {"ended": "completed", "provisional": False, "ended_at": ENDED,
                    "lessons": None, "work": {"repos": []}}}
    r.update(kw)
    return r


def _pass_verdict():
    st._update(DID, lambda rec: rec.update(verdict={"value": "pass", "ended_at": ENDED}))


def test_an_unknown_run_with_a_pass_verdict_is_not_settled(env):
    env[DID] = _row(state="unknown", state_reason="hive_silent")
    _pass_verdict()
    d = st.derive(env[DID], {"records": {"closed": True}})
    assert d["verdict"] == "pass" and d["why_not"] == [] and d["settles"] is False
    st.annotate([env[DID]])
    assert not st.load(DID).get("settled")
    # the same run once its Hive answers that the session stopped: it settles
    env[DID] = _row()
    st.annotate([env[DID]])
    assert st.load(DID)["settled"]["outcome"] == "passed"


def test_an_unknown_run_is_refused_for_the_agents_discard(env):
    row = {**_row(state="unknown", state_reason="hive_silent"), "settle": {"why_not": []}}
    why = act.discard_blocker(row)
    assert why is not None and "not answering" in why and "diagnose" in why
    assert act.discard_blocker({**row, "state": "stopped"}) is None


def test_an_unknown_run_idle_past_retention_expires(env, monkeypatch):
    from core import hive_records
    old = (datetime.now(timezone.utc) - timedelta(days=hive_records.RETENTION_DAYS + 1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    env[DID] = _row(state="unknown", state_reason="hive_silent", sent_at=old,
                    result={"ended": "completed", "provisional": False, "ended_at": old,
                            "lessons": None, "work": {"repos": []}})
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {"records": {"closed": False, "last_at": old}})
    r = dict(env[DID])
    st.annotate([r])
    assert st.load(DID)["settled"]["outcome"] == "expired"
    assert r["stage"] == "closed" and r["why"] == "expired"


from tests.test_swarm_out_binding import INST  # noqa: E402 — the same test Hive the list_runs tests use


def test_rows_carry_no_running_waiting_or_verdict(env, monkeypatch, tmp_path):
    async def h1_running():
        return {"h1": "running"}
    monkeypatch.setattr(swarm_out, "_hive_statuses", h1_running)
    rows = [{"dispatch_id": DID, "hive_id": "h1", "hive": "worker-1", "session_id": "s1", "title": "T",
             "project_agent": {"returns": "answer"}, "sent_at": ENDED},
            {"dispatch_id": "so_cccccccccccc", "hive_id": "h1", "hive": "worker-1", "session_id": None,
             "title": "Timed out"}]
    monkeypatch.setattr(swarm_out, "_read_log", lambda: list(rows))

    async def fake_instance(ref):
        return swarm_out._validate_instance(INST, {"id": "a1"})
    monkeypatch.setattr(swarm_out, "_instance", fake_instance)
    monkeypatch.setattr(swarm_out, "_s3", lambda inst, purpose="manage": None)
    monkeypatch.setattr(swarm_out, "_read_decisions", lambda s3, b, h: [])
    monkeypatch.setattr(swarm_out, "_open_asks", lambda s3, b, h, *_a: [])
    out = asyncio.run(swarm_out.list_runs())
    assert out["runs"], "list_runs returned no rows"
    for r in out["runs"]:
        assert r["state"] in ("waiting", "running", "stopped", "unknown")
        assert "running" not in r and "waiting" not in r
        assert "verdict" not in (r.get("settle") or {})
    # derive reads state alone: the same row with the old keys added derives the same
    row = _row(state="waiting", state_reason="question")
    assert st.derive({**row, "waiting": False, "running": True}, None)["stage"] == st.derive(row, None)["stage"] \
        == "needs_answer"


def _ended(how, monkeypatch):
    """A row that ended ``how``: discarded, reverted, expired, or its Hive gone."""
    from core import hive_records
    if how == "discarded":
        st._update(DID, lambda rec: rec.update(abandoned_at="2026-11-01T00:00:00Z"))
        return _row()
    if how == "reverted":
        st._update(DID, lambda rec: rec.update(ask={"state": "reverted"}))
        return _row()
    if how == "hive_gone":
        return _row(state_reason="hive_gone", result=None)
    old = (datetime.now(timezone.utc) - timedelta(days=hive_records.RETENTION_DAYS + 1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {"records": {"closed": False, "last_at": old}})
    return _row(sent_at=old, result={"ended": "completed", "provisional": False, "ended_at": old,
                                     "lessons": None, "work": {"repos": []}})


@pytest.mark.parametrize("how", ["discarded", "reverted", "hive_gone", "expired"])
def test_every_ending_gives_stage_closed_with_its_reason(env, monkeypatch, how):
    from core import swarm_out_record
    if how == "reverted":
        monkeypatch.setattr(swarm_out_record, "ask", lambda rec: rec.get("ask"))
    env[DID] = _ended(how, monkeypatch)
    if how != "expired":                           # expiry is decided by annotate, not derive
        d = st.derive(env[DID], {"records": {"closed": True}})
        assert (d["stage"], d["why"]) == ("closed", how)
    # the look that settles it, and every later look at the settled row
    for _ in range(2):
        r = dict(env[DID])
        st.annotate([r])
        assert (r["stage"], r["why"], r["actions"]) == ("closed", how, [])
        assert "verdict" not in r["settle"]
    assert st.load(DID)["settled"]["outcome"] == {"discarded": "abandoned", "hive_gone": "gone"}.get(how, how)


def test_a_closed_runs_waits_stay_out_of_needs_you(env, monkeypatch):
    from core import attention_authority as aa
    st._update(DID, lambda rec: rec.update(abandoned_at="2026-11-01T00:00:00Z"))
    closed = {**_row(state="waiting", state_reason="decision"), "hive": "h", "title": "discarded run"}
    d = st.derive(closed, {"records": {"closed": True}})
    assert d["stage"] == "closed"
    closed["stage"], closed["why"] = d["stage"], d["why"]
    open_run = {**_row(), "dispatch_id": "so_dddddddddddd", "hive": "h", "session_id": "s2",
                "stage": "running", "title": "live run"}
    snap = {"runs": [closed, open_run],
            "asks": [{"hive": "h", "hive_id": "h1", "session_id": "s1", "request_id": "q-closed",
                      "kind": "decision", "approval_class": "question"},
                     {"hive": "h", "hive_id": "h1", "session_id": "s2", "request_id": "q-open",
                      "kind": "decision", "approval_class": "question"}]}
    monkeypatch.setattr(swarm_out, "snapshot", lambda: snap)
    seen = []
    monkeypatch.setattr(aa, "_host_wait_items", lambda runs, asks: (seen.extend(asks) or [], []))
    aa._collect_swarm_out()
    ids = {a["request_id"] for a in seen}
    assert "q-closed" not in ids and "q-open" in ids


@pytest.mark.parametrize("state", [None, "paused", ""])
def test_a_row_with_no_known_state_does_not_settle(env, state):
    """Settling is allowed on the states that permit it, never on one it does not name
    (review round 1: a deny-list would let a new or missing state settle)."""
    row = _row(state=state)
    if state is None:
        row.pop("state")
    env[DID] = row
    _pass_verdict()
    assert st.derive(row, {"records": {"closed": True}})["settles"] is False
