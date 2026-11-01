"""The Swarm Out E2E fixes (docs/design/2026-11-01-swarm-out-e2e-fixes.md).

Each test answers one question:
- #2 a verdict is taken only on the result there is now, and an old pass verdict
  does not hold the run's commands back;
- #5 a stopped run whose last turn has no ending recorded is shown stopped, with a
  re-run, once that has lasted INTERRUPTED_AFTER_S, and the time is kept per fetch;
- #6 a discarded run settles though a wait stays open, and its waits leave Needs You;
- #4 an answer the Hive refused is a Needs You item;
- #7 commits landed here make the run accepted.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
         "waiting": False, "running": False,
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
    row["running"] = None                                        # not known to have stopped
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
    d = st.derive(_row(waiting=True), {"records": {"closed": True}})
    assert d["stage"] == "discarded" and d["settles"] is True


# ── #4 / #6: Needs You ──────────────────────────────────────────────────────

def test_needs_you_names_a_refused_answer_and_drops_a_discarded_runs_waits(monkeypatch):
    from core import attention_authority as aa
    runs = [
        {"dispatch_id": "so_r1", "hive": "h", "session_id": "s-ref", "stage": "needs_answer",
         "state_reason": "answer_refused", "title": "a"},
        {"dispatch_id": "so_r2", "hive": "h", "session_id": "s-gone", "stage": "discarded", "title": "b"},
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
