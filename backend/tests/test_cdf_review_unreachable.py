"""A session review while the model cannot be reached (core/ingestion/worker.py,
core/ingestion/s2_jobs.py).

What is tested:
- With credentials the chat spawn would refuse (the readiness snapshot says ``expired``)
  no review runs and no extraction attempt is recorded: the job is postponed, its
  attempt unspent, never failed, however often it is claimed.
- ``valid``, ``unknown`` and an unreadable snapshot let the review run (fail open, as chat).
- A review that fails with the CLI's credentials, ENOTFOUND, ECONNREFUSED or region-block
  error is postponed without spending an attempt, and the lane claims nothing else until
  POSTPONE_DELAY passes.
- Any other error is recorded on the job, retried and failed after MAX_ATTEMPTS as before.
- Only the CLI's own error result counts as unreachable (what ``sdk_runner`` raises for
  it), never model-written text that quotes it: such a reply spends its attempt and
  holds nothing.
- A relations turn that cannot reach the model fails the review (postponed) instead of
  writing the units uncompared; any other relations failure still leaves them as new.
- A job postponed for hours is reported by ``status.stalls``.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

CREDENTIALS = ("review failed: success: API Error: Could not load AWS credentials "
               "· Could not load credentials from any providers")
ENOTFOUND = ("review failed: success: API Error: Can't reach the API server "
             "— check your internet or DNS (ENOTFOUND)")
ECONNREFUSED = ("review failed: success: API Error: Connection refused — a firewall or proxy "
                "may be blocking it (ECONNREFUSED)")
REGION = ("review failed: success: API Error: 400 Access to Anthropic models is not allowed "
          "from unsupported countries, regions, or territories")


@pytest.fixture
def lane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A job database, an executor whose admission and memory check pass, and a review
    that records each call and does what ``lane["review"]`` says."""
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    from core.ingestion import s2_jobs, worker
    from core.ingestion.sources import s7_review
    from core.ingestion.store import connect_rw
    monkeypatch.setattr(s2_jobs, "_WAKER", None)
    monkeypatch.setattr(worker, "ingestion_admission", lambda lane, **k: worker.Admission(True, True))
    monkeypatch.setattr(worker, "_can_fork_review", lambda: True)
    state: dict = {"calls": [], "review": lambda sid: {"status": "nothing_new", "reviewed_to": None, "units": 0}}

    def review_session(session_id, **kwargs):
        state["calls"].append(session_id)
        return state["review"](session_id)

    monkeypatch.setattr(s7_review, "review_session", review_session)
    conn = connect_rw()
    state.update(conn=conn, executor=worker.S2JobExecutor())
    yield state
    conn.close()


def _auth(monkeypatch, value):
    from core.readiness_sampler import readiness_cache
    if value == "raise":
        def snapshot():
            raise RuntimeError("readiness unavailable")
    else:
        def snapshot():
            return {"db_healthy": True, "auth": value, "stale": False, "age_s": 1.0}
    monkeypatch.setattr(readiness_cache, "snapshot", snapshot)


def _row(conn, sid):
    return conn.execute("SELECT state, attempts, error, next_attempt_at FROM ingestion_s2_jobs "
                        "WHERE session_id=?", (sid,)).fetchone()


def _due_now(conn):
    conn.execute("UPDATE ingestion_s2_jobs SET next_attempt_at=NULL")
    conn.commit()


def _attempt_rows(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM extraction_attempts").fetchone()[0]


def test_no_review_runs_while_credentials_are_expired(lane, monkeypatch):
    from core.ingestion import s2_jobs
    _auth(monkeypatch, "expired")
    s2_jobs.request("s-1", "session_end")
    conn = lane["conn"]
    for _ in range(s2_jobs.MAX_ATTEMPTS + 1):           # more claims than a job has attempts
        _due_now(conn)
        assert lane["executor"].drain(conn) == 0
        state, attempts, error, next_at = _row(conn, "s-1")
        assert (state, attempts, error) == ("queued", 0, None) and next_at
    assert lane["calls"] == [] and _attempt_rows(conn) == 0


@pytest.mark.parametrize("auth", ["valid", "unknown", "raise"])
def test_the_review_runs_when_credentials_are_valid_or_unknown(lane, monkeypatch, auth):
    from core.ingestion import s2_jobs
    _auth(monkeypatch, auth)
    s2_jobs.request("s-1", "session_end")
    assert lane["executor"].drain(lane["conn"]) == 1
    assert lane["calls"] == ["s-1"] and _row(lane["conn"], "s-1")[0] == "done"


def test_a_stale_snapshot_lets_the_review_run(lane):
    """No sampler has run in this process: the real snapshot is stale, read as unknown."""
    from core.ingestion import s2_jobs
    from core.readiness_sampler import readiness_cache
    assert readiness_cache.snapshot()["auth"] == "unknown"
    s2_jobs.request("s-1", "session_end")
    assert lane["executor"].drain(lane["conn"]) == 1 and lane["calls"] == ["s-1"]


@pytest.mark.parametrize("message", [CREDENTIALS, ENOTFOUND, ECONNREFUSED, REGION])
def test_an_unreachable_model_postpones_without_spending_an_attempt(lane, message):
    from core.ingestion import s2_jobs

    def review(sid):
        raise RuntimeError(message)

    lane["review"] = review
    s2_jobs.request("a", "session_end")
    s2_jobs.request("b", "session_end")
    conn, executor = lane["conn"], lane["executor"]
    assert executor.drain(conn) == 0
    state, attempts, error, next_at = _row(conn, "a")
    assert (state, attempts, error) == ("queued", 0, None)
    assert datetime.fromisoformat(next_at) > datetime.now(timezone.utc) + timedelta(minutes=4)
    # the outage is the lane's: the other job is not claimed in this pass nor the next
    assert lane["calls"] == ["a"] and _row(conn, "b")[:3] == ("queued", 0, None)
    _due_now(conn)
    assert executor.drain(conn) == 0 and lane["calls"] == ["a"]
    # every later look that fails the same way spends nothing either
    for _ in range(s2_jobs.MAX_ATTEMPTS + 1):
        executor._held_until = 0.0                      # the hold has passed
        _due_now(conn)
        executor.drain(conn)
    assert {_row(conn, sid)[:3] for sid in ("a", "b")} == {("queued", 0, None)}
    assert len(lane["calls"]) == s2_jobs.MAX_ATTEMPTS + 2
    # the model is back: the next look reviews
    lane["review"] = lambda sid: {"status": "nothing_new", "reviewed_to": None, "units": 0}
    executor._held_until = 0.0
    _due_now(conn)
    assert executor.drain(conn) == 2
    assert {_row(conn, sid)[0] for sid in ("a", "b")} == {"done"}


def test_a_postpone_that_fails_neither_finishes_nor_raises(lane, monkeypatch):
    from core.ingestion import s2_jobs

    def review(sid):
        raise RuntimeError(CREDENTIALS)

    def busy(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    lane["review"] = review
    monkeypatch.setattr(s2_jobs, "postpone", busy)
    s2_jobs.request("a", "session_end")
    assert lane["executor"].drain(lane["conn"]) == 0
    state, _attempts, error, _next = _row(lane["conn"], "a")
    assert state == "running" and error is None          # recover puts it back later


@pytest.mark.parametrize("message", [
    "model down",
    "review failed: error_during_execution: Prompt is too long",
    "review failed: success: API Error: 403 You don't have access to the model with the specified model ID.",
])
def test_other_review_errors_spend_attempts_as_before(lane, message):
    from core.ingestion import s2_jobs

    def review(sid):
        if sid == "bad":
            raise RuntimeError(message)
        return {"status": "nothing_new", "reviewed_to": None, "units": 0}

    lane["review"] = review
    s2_jobs.request("bad", "session_end")
    s2_jobs.request("good", "session_end")
    conn, executor = lane["conn"], lane["executor"]
    assert executor.drain(conn) == 2                    # no hold: the next job runs
    assert _row(conn, "good")[0] == "done"
    state, attempts, error, _next = _row(conn, "bad")
    assert (state, attempts) == ("queued", 1) and message in error
    for _ in range(s2_jobs.MAX_ATTEMPTS - 1):
        _due_now(conn)                                  # its retry delay has passed
        executor.drain(conn)
    state, attempts, error, _next = _row(conn, "bad")
    assert (state, attempts) == ("failed", s2_jobs.MAX_ATTEMPTS) and message in error


def test_only_the_clis_api_error_report_counts_as_unreachable():
    from core.ingestion.s2_jobs import model_unreachable
    assert model_unreachable(CREDENTIALS) and model_unreachable(ENOTFOUND)
    assert model_unreachable(ECONNREFUSED) and model_unreachable(REGION)
    assert model_unreachable("RuntimeError: " + CREDENTIALS)            # as recorded
    assert model_unreachable("RuntimeError: " + REGION) and model_unreachable("RuntimeError: " + ECONNREFUSED)
    assert model_unreachable("review failed: success: API Error: connect ECONNREFUSED 127.0.0.1:443")
    assert model_unreachable("review failed: success: API Error: Can’t reach the API server (ENOTFOUND)")
    assert model_unreachable("review failed: success: API Error: getaddrinfo ENOTFOUND "
                             "bedrock-runtime.us-east-1.amazonaws.com")
    quoted = "API Error: Could not load AWS credentials"
    for other in (None, "", "model down", quoted, "API Error: Can't reach the API server (ENOTFOUND)",
                  f"reply missing: no structured output ({quoted})",
                  f"UntypedOutput: reply missing: no structured output (review failed: success: {quoted})",
                  "review failed: success: the session fixed ENOTFOUND in its DNS code",
                  "review failed: success: the session handled ECONNREFUSED in its client",
                  "API Error: 400 Access to Anthropic models is not allowed from unsupported countries",
                  "reply missing: no structured output (" + REGION + ")",
                  "review failed: success: API Error: 500 Internal server error",
                  "review failed: success: API Error: 403 You don't have access to the model"):
        assert not model_unreachable(other), other


def test_what_the_review_raises_for_the_clis_error_result_is_classified(monkeypatch):
    """The text model_unreachable reads is the one sdk_runner raises: an error result
    with the credentials text is unreachable, a reply that only quotes it is not."""
    from core.ingestion.s2_jobs import model_unreachable
    from core.ingestion.sources import extract_llm, s7_review
    from tests.test_cdf_agent_decides_review_fixes import _sdk
    said = "API Error: Could not load AWS credentials · Could not load credentials from any providers"
    _sdk(monkeypatch, {"is_error": True, "result": said})
    with pytest.raises(RuntimeError) as error:
        s7_review.sdk_runner(None, "/w", "p", {"type": "object"}, fork=False, model="m")
    assert model_unreachable(str(error.value))
    _sdk(monkeypatch, {"result": said})                       # the model's words, no answer
    with pytest.raises(extract_llm.UntypedOutput) as error:
        s7_review.sdk_runner(None, "/w", "p", {"type": "object"}, fork=False, model="m")
    assert said in str(error.value) and not model_unreachable(str(error.value))
    assert not model_unreachable(f"{type(error.value).__name__}: {error.value}")


def test_a_reply_quoting_the_error_spends_its_attempt_and_holds_nothing(lane):
    from core.ingestion import s2_jobs
    from core.ingestion.sources import extract_llm

    def review(sid):
        if sid == "a":
            raise extract_llm.UntypedOutput(
                "reply missing: no structured output (API Error: Could not load AWS credentials)")
        return {"status": "nothing_new", "reviewed_to": None, "units": 0}

    lane["review"] = review
    s2_jobs.request("a", "session_end")
    s2_jobs.request("b", "session_end")
    assert lane["executor"].drain(lane["conn"]) == 2
    assert lane["calls"] == ["a", "b"] and _row(lane["conn"], "b")[0] == "done"
    state, attempts, error, _next = _row(lane["conn"], "a")
    assert (state, attempts) == ("queued", 1) and "Could not load AWS credentials" in error


def test_a_job_postponed_for_hours_is_a_stall(lane):
    from core.ingestion import s2_jobs, status
    now = datetime.now(timezone.utc)
    s2_jobs.request("a", "session_end")
    conn = lane["conn"]
    later = (now + timedelta(minutes=5)).isoformat()
    conn.execute("UPDATE ingestion_s2_jobs SET requested_at=?, next_attempt_at=?",
                 ((now - timedelta(hours=1)).isoformat(), later))
    conn.commit()
    assert not [s for s in status.stalls(conn, now=now) if "session job" in s]
    conn.execute("UPDATE ingestion_s2_jobs SET requested_at=?",
                 ((now - s2_jobs.MAX_WAIT - status.JOB_OVERDUE_AFTER - timedelta(hours=1)).isoformat(),))
    conn.commit()
    assert [s for s in status.stalls(conn, now=now) if "session job" in s] == [
        "a session job requested 6 h ago has not been reviewed"]


# ── the relations turn: an outage fails the review, any other failure leaves the units new

from tests.test_cdf_s7_review import (  # noqa: E402,F401  (fixtures)
    _agent_correction, _cli_transcript, _conversation, cdf_env, model,
)
from tests.test_cdf_v2_e2e import SESSION  # noqa: E402


def _relations_failing(users, message):
    def runner(resume, cwd, prompt, schema, *, fork, model):
        from core.ingestion.sources import s7_review
        if prompt.startswith(s7_review.RELATIONS_OPENING):
            raise RuntimeError(message)
        return {"cognition_units": [_agent_correction(f"event:{users[1]}")]}, "fork-1"
    return runner


def _s7_rows():
    from core.ingestion.api import connect_ro
    conn = connect_ro()
    try:
        attempts = conn.execute("SELECT status, error, evidence_range FROM extraction_attempts "
                                "WHERE source='S7'").fetchall()
        units = conn.execute("SELECT COUNT(*) FROM ingestion_inbox WHERE source_v2='S7'").fetchone()[0]
    finally:
        conn.close()
    return attempts, units


def test_a_relations_turn_that_cannot_reach_the_model_fails_the_review(cdf_env, tmp_path, monkeypatch, model):
    from core.ingestion.s2_jobs import model_unreachable
    from core.ingestion.sources import s7_review
    sdk = _cli_transcript(tmp_path, monkeypatch)
    users = _conversation(cdf_env)
    monkeypatch.setattr(s7_review, "_drop_fork", lambda fork, original: None)
    with pytest.raises(RuntimeError) as error:
        s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=None,
                                 runner=_relations_failing(users, ENOTFOUND))
    assert model_unreachable(str(error.value))
    attempts, units = _s7_rows()
    assert units == 0                                       # nothing written uncompared
    assert [(a[0], model_unreachable(a[1])) for a in attempts] == [("failed", True)]


def test_any_other_relations_failure_still_leaves_the_units_new(cdf_env, tmp_path, monkeypatch, model):
    from core.ingestion.sources import s7_review
    sdk = _cli_transcript(tmp_path, monkeypatch)
    users = _conversation(cdf_env)
    monkeypatch.setattr(s7_review, "_drop_fork", lambda fork, original: None)
    out = s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=None,
                                   runner=_relations_failing(users, "review failed: success: Prompt is too long"))
    assert out["units"] == 1
    attempts, units = _s7_rows()
    assert units == 1 and attempts[0][0] == "ok" and "relations_failed" in attempts[0][2]
