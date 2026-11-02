"""Swarm Out E2E round 2, host side (design
docs/design/2026-11-02-swarm-out-e2e-round2-fixing-spec.md G1-G3).

Questions: (G1) is a run's reply kept and shown whole, and is a result kept by older
summary rules read again; (G2) do check statuses come from the Hive's check record when
there is one, whatever the session's Bash calls look like, and are they marked inferred
when there is none; (G3) is a result that ended before a re-send not final (no accept
offered, an agent's accept refused) until a newer result is kept, and does a running run
never carry the reason of a wait.
"""
from __future__ import annotations

import pytest

from core import swarm_out, swarm_out_actor, swarm_out_settle, swarm_out_results as res
from core.swarm_out_results import summarize
from tests.test_swarm_out_results import ROW, A, T, U, hive, out  # noqa: F401 — the fixture

SID = "11111111-1111-4111-8111-111111111111"
AGENT = {"brain": "B", "mode": "code-change", "execution": "native", "returns": "commits",
         "validations": [{"id": "lint", "command": "python scripts/lint_skills.py"},
                         {"id": "smoke", "command": "python scripts/smoke_import.py"},
                         {"id": "types", "command": "npx tsc --noEmit"}]}


def Bash(cmd, i):
    return {"type": "tool_use", "name": "Bash", "id": f"t{i}", "display": {"object": cmd}}


def R(i, err=False):
    return {"type": "tool_result", "tool_use_id": f"t{i}", "is_error": err}


# ── G1 ────────────────────────────────────────────────────────────────────────

def test_long_reply_is_kept_whole():
    reply = "".join(f"line {n}: the finding and its evidence.\n" for n in range(300))
    assert len(reply) > 9000
    r = summarize([U(), A(T(reply), out())])
    assert r["report"] == reply and r["report_chars"] == len(reply)


@pytest.mark.asyncio
async def test_older_summary_version_is_summarised_again(hive):
    seen, reply = hive
    long = "x" * 9000
    reply["body"] = [U(), A(T(long), out())]
    await res.fetch(ROW)
    kept = res.load(ROW["dispatch_id"])
    # a result kept by the rules before this change: the reply cut at 4000 characters
    res.save({**kept, "summary_version": res.SUMMARY_VERSION - 1,
              "result": {**kept["result"], "report": long[:4000]}})
    await res.fetch(ROW)
    again = res.load(ROW["dispatch_id"])
    assert "if-none-match" not in seen[-1].headers           # read again in full
    assert again["result"]["report"] == long and again["summary_version"] == res.SUMMARY_VERSION


# ── G2 ────────────────────────────────────────────────────────────────────────

def _session_with_a_loop():
    """The 2026-11-02 run's shape: three checks run in one for-loop through a variable
    (no command line the text matcher can read), the type check in a call that errored."""
    return [U(), A(Bash('P=backend/.venv/bin/python; for s in lint_skills smoke_import; do $P scripts/$s.py; done', 1),
                   R(1), Bash("perl -e 'alarm 280; exec @ARGV' npx tsc --noEmit > /tmp/t.log", 2), R(2, err=True),
                   T("All checks pass."), out())]


def test_checks_come_from_the_record():
    records = [{"id": "lint", "started_at": "a"}, {"id": "lint", "exit": 0},
               {"id": "smoke", "exit": 0}, {"id": "types", "exit": 2}]
    r = summarize(_session_with_a_loop(), agent=AGENT, check_records=records)
    assert {c["id"]: c["status"] for c in r["checks"]} == {"lint": "ran", "smoke": "ran", "types": "errored"}
    assert r["checks_inferred"] is False and r["checks_from_record"] is True
    assert res.public({"result": r}, agent=AGENT)["checks_from_record"] is True
    # a check started and never ended reads as unknown; one with no line as not seen
    r2 = summarize(_session_with_a_loop(), agent=AGENT, check_records=[{"id": "lint", "started_at": "a"}])
    assert {c["id"]: c["status"] for c in r2["checks"]} == {"lint": "unknown", "smoke": "not_seen", "types": "not_seen"}
    assert res.public({"result": r}, agent=AGENT)["checks_inferred"] is False


def test_checks_without_record_are_inferred():
    r = summarize(_session_with_a_loop(), agent=AGENT)
    assert r["checks_inferred"] is True and r["checks_from_record"] is False
    # the text matcher cannot see the loop; the errored call is seen
    assert {c["id"]: c["status"] for c in r["checks"]} == {"lint": "not_seen", "smoke": "not_seen", "types": "errored"}
    assert res.public({"result": r}, agent=AGENT)["checks_inferred"] is True


@pytest.mark.asyncio
async def test_check_records_are_read_from_the_hive_and_kept(hive, monkeypatch):
    """A task sent with checks by id: the fetch reads the Hive's record and keeps it, so
    a later fetch after the Hive released the workspace still reads the statuses."""
    seen, reply = hive
    row = {**ROW, "project_agent": {**AGENT, "checks_by_id": True}}
    real = reply["body"]
    answers = {"checks": {"checks": [{"id": "lint", "exit": 0}, {"id": "types", "exit": 1}]}}

    async def take(row_, inst, rec):          # what the Hive's GET /work/<id>/checks answers
        if answers["checks"] is not None:
            rec["check_records"] = answers["checks"]["checks"]
    monkeypatch.setattr(res, "_take_check_records", take)
    reply["body"] = real
    await res.fetch(row)
    kept = res.load(ROW["dispatch_id"])
    assert {c["id"]: c["status"] for c in kept["result"]["checks"]} == {"lint": "ran", "smoke": "not_seen",
                                                                        "types": "errored"}
    answers["checks"] = None                  # the workspace is released: no record there now
    res.save({**kept, "summary_version": res.SUMMARY_VERSION - 1})
    await res.fetch(row)
    again = res.load(ROW["dispatch_id"])
    assert {c["id"]: c["status"] for c in again["result"]["checks"]}["types"] == "errored"
    assert again["result"]["checks_inferred"] is False


# ── G3 ────────────────────────────────────────────────────────────────────────

def _kept(tmp_path, monkeypatch, ended_at, resent_at=None):
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)
    r = summarize([U(), A(T("done"), {**out(), "at": ended_at})])
    assert r["ended_at"] and r["provisional"] is False
    rec = {"dispatch_id": ROW["dispatch_id"], "result": r}
    if resent_at:
        rec["resent_at"] = resent_at
    res.save(rec)
    return res.load(ROW["dispatch_id"])


def _row(kept):
    row = {**ROW, "dispatched_from": SID, "project_agent": {"brain": "B", "execution": "native",
                                                            "returns": "answer"},
           "state": "stopped", "state_reason": "idle",
           "result": res.public(kept)}
    return {**row, **swarm_out_settle.derive(row, kept)}


def test_result_before_resend_is_not_final(tmp_path, monkeypatch):
    kept = _kept(tmp_path, monkeypatch, "2026-11-02T01:42:20+00:00", resent_at="2026-11-02T01:42:56+00:00")
    assert kept["result"]["provisional"] is True and kept["result"]["superseded"] is True
    assert swarm_out_settle._final(kept["result"]) is False
    row = _row(kept)
    assert "accept" not in {a["key"] for a in row["actions"]}
    agent = swarm_out_actor.Actor("agent", SID, "it meets the acceptance")
    why = swarm_out_actor.row_blocker(agent, "accept", row, ended_at=kept["result"]["ended_at"])
    assert why is not None


def test_result_after_resend_is_final(tmp_path, monkeypatch):
    kept = _kept(tmp_path, monkeypatch, "2026-11-02T01:44:28+00:00", resent_at="2026-11-02T01:42:56+00:00")
    assert kept["result"]["provisional"] is False and "superseded" not in kept["result"]
    assert swarm_out_settle._final(kept["result"]) is True
    row = _row(kept)
    assert "accept" in {a["key"] for a in row["actions"]}
    agent = swarm_out_actor.Actor("agent", SID, "it meets the acceptance")
    assert swarm_out_actor.row_blocker(agent, "accept", row, ended_at=kept["result"]["ended_at"]) is None


def test_turn_started_marks_the_kept_result_at_once(tmp_path, monkeypatch):
    """The re-send itself makes the kept result not final, before any fetch."""
    _kept(tmp_path, monkeypatch, "2026-11-02T01:42:20+00:00")
    res.turn_started(ROW["dispatch_id"])
    kept = res.load(ROW["dispatch_id"])
    assert kept["resent_at"] and kept["result"]["provisional"] is True


def test_running_reason_is_never_waiting():
    for activity in ("waiting", "waiting_for_model", "thinking", None):
        state, reason = swarm_out.run_state(
            {**ROW, "session_id": "sess-1"}, "running",
            {"sess-1": {"streaming": True, "waiting_input": False, "activity": activity,
                        "background_work": False}}, False)
        assert state == "running" and not reason.startswith("waiting")
    # a real wait is still a wait
    state, reason = swarm_out.run_state(
        {**ROW, "session_id": "sess-1"}, "running",
        {"sess-1": {"streaming": False, "waiting_input": True, "activity": "waiting_input",
                    "background_work": False}}, False)
    assert (state, reason) == ("waiting", "waiting_input")


# ── G5: the returned file's two versions (tests in test_return_file_versions) ─
from tests.test_return_file_versions import (  # noqa: E402,F401 — collected here too: the design's exit criteria name this file
    world, test_file_versions_for_listed_paths, test_file_versions_needs_prepare,
    test_file_versions_refuses_unlisted_paths)


# ── REVIEW round 1 fixes ──────────────────────────────────────────────────────

def test_resend_mark_survives_a_check_that_loaded_the_record_before_it(tmp_path, monkeypatch):
    """C1: a check loads the record, a re-send marks it, the check saves its copy: the
    mark is kept and the old result is superseded."""
    kept = _kept(tmp_path, monkeypatch, "2026-11-02T01:42:20+00:00")
    loaded_by_the_check = dict(kept)                 # before the re-send: no resent_at
    res.turn_started(ROW["dispatch_id"])
    res.save(loaded_by_the_check)                    # the check's save, after the re-send
    after = res.load(ROW["dispatch_id"])
    assert after["resent_at"] and after["result"]["provisional"] is True
    assert after["result"]["superseded"] is True


def test_resend_also_keeps_the_restarted_no_ending_timer(tmp_path, monkeypatch):
    """The re-send restarted the "no ending recorded" timer; a check that loaded the
    record before it does not bring the old start back."""
    kept = _kept(tmp_path, monkeypatch, "2026-11-02T01:42:20+00:00")
    loaded_by_the_check = {**kept, "no_record_since": "2026-11-02T01:40:00Z",
                           "no_record_of": kept["result"]["ended_at"]}
    res.turn_started(ROW["dispatch_id"])
    res.save(loaded_by_the_check)
    after = res.load(ROW["dispatch_id"])
    assert after["no_record_since"] is None and after["no_record_of"] is None


@pytest.mark.asyncio
async def test_a_changed_check_record_is_read_again_without_new_messages(monkeypatch, tmp_path):
    """C2: the messages answer 304 to a matching ETag; a check record that changed
    since the last check still gets the statuses read again."""
    import httpx
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)
    from tests.test_swarm_out_results import INST
    body = [U(), A(T("done"), out())]

    def handler(request):
        if request.headers.get("if-none-match") == '"e1"':
            return httpx.Response(304, headers={"etag": '"e1"'})
        return httpx.Response(200, json=body, headers={"etag": '"e1"'})

    async def fake_instance(ref):
        return swarm_out._validate_instance(INST, {"id": "a1"})

    def client(inst, timeout=None):
        return httpx.AsyncClient(base_url=swarm_out._base(inst), headers=swarm_out._auth(inst),
                                 transport=httpx.MockTransport(handler))
    monkeypatch.setattr(swarm_out, "_instance", fake_instance)
    monkeypatch.setattr(swarm_out, "_client", client)
    record = {"lines": [{"id": "lint", "started_at": "a"}]}

    async def take(row_, inst, rec):
        rec["check_records"] = list(record["lines"])
    monkeypatch.setattr(res, "_take_check_records", take)
    row = {**ROW, "project_agent": {**AGENT, "checks_by_id": True}}
    await res.fetch(row)
    assert {c["id"]: c["status"] for c in res.load(ROW["dispatch_id"])["result"]["checks"]}["lint"] == "unknown"
    record["lines"] = [{"id": "lint", "started_at": "a"}, {"id": "lint", "exit": 0}]
    await res.fetch(row)
    assert {c["id"]: c["status"] for c in res.load(ROW["dispatch_id"])["result"]["checks"]}["lint"] == "ran"
    await res.fetch(row)                             # nothing changed: the 304 path keeps it
    assert {c["id"]: c["status"] for c in res.load(ROW["dispatch_id"])["result"]["checks"]}["lint"] == "ran"


def test_an_unreadable_run_of_another_session_does_not_block_the_release(monkeypatch):
    """C3: run.json naming another session is that session's run and is not read; a
    run naming no session (it may be this one's) is still read, and its failure raises."""
    from pathlib import Path
    from types import SimpleNamespace

    from core import pipeline_human, run_index
    runs = [SimpleNamespace(path=Path("/w/run_other/run.json"),
                            data={"status": "awaiting_human", "decision_cap": 2, "session_id": "s-other"}),
            SimpleNamespace(path=Path("/w/run_mine/run.json"),
                            data={"status": "active", "decision_cap": 2, "session_id": "s-mine"})]
    loaded = []

    class Engine:
        def __init__(self, run_id):
            self.run_id = run_id
            self.abandoned = False

        def owner_session(self):
            return "s-mine"

        def projection(self):
            return {"status": "active"}

        def abandon(self, reason):
            self.abandoned = reason

    def load(run_id):
        loaded.append(run_id)
        if run_id == "run_other":
            raise ValueError("broken contract")
        return Engine(run_id)
    monkeypatch.setattr(run_index, "runs", lambda ws: list(runs))
    monkeypatch.setattr(pipeline_human, "load_engine", load)
    assert pipeline_human.end_session_runs("s-mine") == ["run_mine"]
    assert loaded == ["run_mine"]                    # the other session's run was never read
    # a run whose projection names no session may be this one's: its failure still raises
    runs[0].data.pop("session_id")
    loaded.clear()
    with pytest.raises(ValueError):
        pipeline_human.end_session_runs("s-mine")
