"""Tests for the runtime session-health probe (run_f646b175).

AC1 probe core (each sub-check pass+fail), AC2 RP41-safe wedged discriminator
(healthy long turn must NOT false-alarm; genuine wedge must; unreadable = fail-safe).
"""
from __future__ import annotations

from core import session_health_probe as shp


# ── AC2: wedged discriminator (RP41 — the highest-risk piece) ──────────────

def _noop_sleep(_):  # don't actually sleep in tests
    return None


def test_ac2_healthy_long_turn_cpu_burning_not_wedged():
    """A turn burning CPU over the interval (delta >= epsilon) is NOT wedged,
    even with no log progress — CPU signal alone clears it."""
    samples = iter([10.0, 10.5])  # delta 0.5 >> epsilon
    wedged = shp.is_session_wedged(
        123, cpu_sampler=lambda pid: next(samples),
        log_progressed=False, sleep_fn=_noop_sleep)
    assert wedged is False


def test_ac2_healthy_long_turn_io_wait_but_log_progressing_not_wedged():
    """The RP41 trap: a turn waiting on Bedrock IO burns ~0 CPU — but if it is
    emitting streaming events (log_progressed True), it is NOT wedged."""
    samples = iter([10.0, 10.0])  # zero CPU delta — looks idle
    wedged = shp.is_session_wedged(
        123, cpu_sampler=lambda pid: next(samples),
        log_progressed=True, sleep_fn=_noop_sleep)
    assert wedged is False, "IO-wait turn with log progress must NOT alarm (RP41)"


def test_ac2_genuine_wedge_zero_cpu_and_no_log_progress_is_wedged():
    """Both signals agree on dead: zero CPU delta AND no new log events."""
    samples = iter([10.0, 10.0])
    wedged = shp.is_session_wedged(
        123, cpu_sampler=lambda pid: next(samples),
        log_progressed=False, sleep_fn=_noop_sleep)
    assert wedged is True


def test_ac2_unreadable_cpu_fails_safe():
    """tree_cpu_seconds returns None (psutil missing / process gone) → never alarm."""
    wedged = shp.is_session_wedged(
        123, cpu_sampler=lambda pid: None,
        log_progressed=False, sleep_fn=_noop_sleep)
    assert wedged is False


def test_ac2_unknown_log_progress_fails_safe():
    """log_progressed None (session not visible in log) → never alarm."""
    samples = iter([10.0, 10.0])
    wedged = shp.is_session_wedged(
        123, cpu_sampler=lambda pid: next(samples),
        log_progressed=None, sleep_fn=_noop_sleep)
    assert wedged is False


# ── scan_unrecovered_events: recovery suppresses the marker ────────────────

def test_recovered_failure_is_not_flagged():
    log = "streaming_timeout for session abc\nRetry 1/3 after backoff --resume\nrecovered ok"
    assert shp.scan_unrecovered_events(log) == []


def test_unrecovered_failure_is_flagged():
    # A GENUINE terminal failure with no recovery after → flagged. Uses
    # streaming_timeout (a real _FAILURE_MARKERS entry) — NOT force_unstick,
    # which is a RECOVERY action, not a failure (run_67a391a4).
    log = "session_unit.streaming_timeout on session xyz45678\n...nothing after..."
    out = shp.scan_unrecovered_events(log)
    assert len(out) == 1 and "streaming_timeout" in out[0]


# ── run_67a391a4: the daemon's OWN self-heal must NOT be flagged as a failure ──
#
# Root cause of the "FAILED: no_unrecovered_events" false-positive: the
# waiting_input self-heal path (force_unstick_waiting_input → recovery_checkpoint_armed
# → transition to=cold → force_kill_tree) was mis-classified as unrecovered failures,
# because (1) force_unstick / stuck were in _FAILURE_MARKERS (they are RECOVERY
# actions / a recovery reason-field substring, not failures) and (2) _RECOVERY_PATTERN
# did not recognize the to=cold self-heal vocabulary (it only knew Retry/--resume).
# These use the EXACT line shapes copied from the real backend-daemon.log 16:54 sequence.

_REAL_WAITING_INPUT_SELFHEAL = (
    "2026-07-07 16:54:43,941 - core.session_unit - WARNING - session_unit.reap_dead_waiting_input session_id=ce7ab76c-f980-4581-a24c-06cbb7c59bb2 pid=82827\n"
    "2026-07-07 16:54:43,941 - core.session_unit - WARNING - session_unit.force_unstick_waiting_input session_id=ce7ab76c-f980-4581-a24c-06cbb7c59bb2 pid=82827 — frontend never answered, forcing COLD for recovery\n"
    "2026-07-07 16:54:47,290 - core.session_unit - INFO - session_unit.recovery_checkpoint_armed session_id=ce7ab76c-f980-4581-a24c-06cbb7c59bb2 trigger=stuck_waiting_input fields=[completed=0]\n"
    "2026-07-07 16:54:47,291 - core.session_unit - INFO - session_unit.transition session_id=ce7ab76c-f980-4581-a24c-06cbb7c59bb2 from=waiting_input to=dead pid=82827\n"
    "2026-07-07 16:54:47,467 - core.session_unit - INFO - session_unit.force_kill_tree session_id=ce7ab76c-f980-4581-a24c-06cbb7c59bb2 pid=82827 tree_size=9\n"
    "2026-07-07 16:54:47,520 - core.session_unit - INFO - session_unit.transition session_id=ce7ab76c-f980-4581-a24c-06cbb7c59bb2 from=dead to=cold pid=None\n"
)


def test_ac1_waiting_input_selfheal_is_not_flagged():
    """AC1 (mutation-proven): the REAL waiting_input self-heal sequence is the
    daemon recovering itself — it must scan clean. Pre-fix this flagged 2 events
    (force_unstick_waiting_input + recovery_checkpoint_armed:trigger=stuck_*)."""
    assert shp.scan_unrecovered_events(_REAL_WAITING_INPUT_SELFHEAL) == []


def test_ac1_streaming_timeout_selfheal_to_cold_is_not_flagged():
    """AC1: the streaming_timeout → force_unstick → to=cold path (no Retry line)
    must also be recognized as recovered via the to=cold vocabulary."""
    log = (
        "2026-07-07 09:28:08,640 - core.lifecycle_manager - WARNING - lifecycle_manager.streaming_timeout session_id=2560c9b7-5d43-487b stall=608s > timeout=600s — forcing unstick\n"
        "2026-07-07 09:28:08,640 - core.session_unit - WARNING - session_unit.force_unstick session_id=2560c9b7-5d43-487b pid=53139 attempt=1\n"
        "2026-07-07 09:28:15,009 - core.session_unit - INFO - session_unit.recovery_checkpoint_armed session_id=2560c9b7-5d43-487b trigger=stuck_streaming\n"
        "2026-07-07 09:28:15,100 - core.session_unit - INFO - session_unit.transition session_id=2560c9b7-5d43-487b from=streaming to=cold pid=None\n"
    )
    assert shp.scan_unrecovered_events(log) == []


def test_ac2_genuine_sigkill_without_recovery_still_flagged():
    """AC2: detection is NOT weakened — a genuine SIGKILL with no recovery after
    must STILL be flagged."""
    log = "2026-07-07 10:00:00,000 - core.session_unit - ERROR - subprocess SIGKILL session_id=deadbeef pid=111 (OOM, no recovery)\n"
    out = shp.scan_unrecovered_events(log)
    assert len(out) == 1 and "SIGKILL" in out[0]


def test_ac2_genuine_streaming_timeout_without_recovery_still_flagged():
    """AC2: a streaming_timeout that never reaches to=cold/Retry (recovery itself
    hung/died) must STILL be flagged — the true failure the probe exists to catch."""
    log = "2026-07-07 10:00:00,000 - core.lifecycle_manager - WARNING - lifecycle_manager.streaming_timeout session_id=beefcafe stall=900s\n(recovery never completed)\n"
    out = shp.scan_unrecovered_events(log)
    assert len(out) == 1 and "streaming_timeout" in out[0]


def test_ac3_streaming_timeout_retry_resume_still_absolved():
    """AC3 (no regression): the pre-existing streaming_timeout → Retry N/N --resume
    path must stay absolved."""
    log = (
        "2026-07-07 10:00:00,000 - core.session_unit - WARNING - session_unit.streaming_timeout session_id=aaaa1111\n"
        "2026-07-07 10:00:05,000 - core.retry_manager - INFO - Retry 1/3 for session aaaa1111 --resume\n"
    )
    assert shp.scan_unrecovered_events(log) == []


def test_ac4_cross_session_to_cold_does_not_absolve_other_failure():
    """AC4: a to=cold recovery for session B must NOT absolve a genuine
    streaming_timeout failure for session A (cross-session correlation preserved
    — the _SID_RE guard). Otherwise a busy self-healing daemon would mask real
    failures on unrelated sessions."""
    log = (
        "2026-07-07 10:00:00,000 - core.lifecycle_manager - WARNING - lifecycle_manager.streaming_timeout session_id=aaaa1111 stall=900s\n"
        "2026-07-07 10:00:01,000 - core.session_unit - INFO - session_unit.transition session_id=bbbb2222 from=dead to=cold pid=None\n"
    )
    out = shp.scan_unrecovered_events(log)
    assert len(out) == 1 and "aaaa1111" in out[0], "cross-session to=cold wrongly absolved a real failure"


# ── Gate-2 HIGH (run_67a391a4): the blinding holes the adversarial review caught ──

def test_gate2_same_session_benign_cold_does_not_absolve_genuine_failure():
    """Gate-2 HIGH (correctness+security, multi-confirmed): a GENUINE failure whose
    recovery ACTUALLY FAILED must NOT be absolved by a later BENIGN same-session
    `to=cold` transition (idle→cold reclaim, dead→cold cleanup). This is why bare
    `to=cold` was removed from _RECOVERY_PATTERN — only the specific self-heal
    events (force_unstick / recovery_checkpoint_armed) count as recovery.
    Mutation proof: if `|to=cold` is re-added to _RECOVERY_PATTERN, this goes RED."""
    log = (
        "2026-07-07 10:00:00,000 - core.session_unit - ERROR - subprocess SIGKILL session_id=aaaa1111 pid=111 (recovery FAILED, no self-heal)\n"
        "2026-07-07 10:00:01,000 - core.session_unit - INFO - filler line\n"
        "2026-07-07 10:00:05,000 - core.session_unit - INFO - session_unit.transition session_id=aaaa1111 from=idle to=cold pid=None\n"
    )
    out = shp.scan_unrecovered_events(log)
    assert len(out) == 1 and "SIGKILL" in out[0], (
        "a benign same-session idle→cold reclaim must NOT absolve a genuine SIGKILL")


def test_gate2_substring_sid_collision_does_not_absolve():
    """Gate-2 HIGH (security): sid correlation must be TOKEN equality, not substring.
    The failure's 8-hex token embedded inside a LONGER unbounded hex run on an
    unrelated recovery line (a hex pid, a request-id) must NOT absolve it — there
    is no bounded 8-hex sid token on the recovery line that equals the failure sid.
    Mutation proof: revert to `fail_sid.group(1) in wl` (substring) and this goes RED
    (substring 'deadbeef' IS inside 'deadbeef99abc')."""
    log = (
        "2026-07-07 10:00:00,000 - core.session_unit - ERROR - subprocess SIGKILL session_id=deadbeef pid=111\n"
        "2026-07-07 10:00:01,000 - core.session_unit - WARNING - session_unit.force_unstick req_id=deadbeef99abc pid=222\n"
    )
    out = shp.scan_unrecovered_events(log)
    assert len(out) == 1 and "deadbeef" in out[0], (
        "an embedded hex collision (no bounded matching sid token) must not absolve via substring match")


# ── AC1: probe core (each sub-check, pass + fail) ──────────────────────────

def _healthy_kwargs(**over):
    base = dict(
        health_fetcher=lambda: {"database": {"healthy": True},
                                "channel_gateway": {"startup_state": "started"}},
        streaming_fetcher=lambda: [],
        rss_fetcher=lambda: 1200.0,
        cpu_sampler=lambda pid: 10.0,
        log_reader=lambda: "",
        sleep_fn=_noop_sleep,
    )
    base.update(over)
    return base


def test_ac1_all_healthy():
    r = shp.session_health_probe(**_healthy_kwargs())
    assert r.status == "healthy" and not r.red
    assert {c.name for c in r.checks} >= {"daemon_health", "total_rss",
                                          "no_wedged_sessions", "no_unrecovered_events"}


def test_ac1_db_down_degraded():
    r = shp.session_health_probe(**_healthy_kwargs(
        health_fetcher=lambda: {"database": {"healthy": False},
                                "channel_gateway": {"startup_state": "started"}}))
    assert r.red
    assert any(c.name == "daemon_health" and not c.ok for c in r.checks)


def test_ac1_rss_over_budget_degraded():
    r = shp.session_health_probe(**_healthy_kwargs(rss_fetcher=lambda: 3600.0))
    assert r.red
    assert any(c.name == "total_rss" and not c.ok for c in r.checks)


def test_ac1_deployed_commit_mismatch_degraded():
    r = shp.session_health_probe(**_healthy_kwargs(
        expected_commit="abc123def456",
        deployed_commit_fetcher=lambda: "999999999999"))
    assert r.red
    assert any(c.name == "deployed_commit" and not c.ok for c in r.checks)


def test_ac1_unrecovered_event_degraded():
    r = shp.session_health_probe(**_healthy_kwargs(
        log_reader=lambda: "SIGKILL session foo\n(no recovery after)"))
    assert r.red
    assert any(c.name == "no_unrecovered_events" and not c.ok for c in r.checks)


def test_ac1_health_unreachable_is_degraded_not_crash():
    def boom():
        raise ConnectionError("daemon down")
    r = shp.session_health_probe(**_healthy_kwargs(health_fetcher=boom))
    assert r.red
    assert any(c.name == "daemon_health" and not c.ok for c in r.checks)


# ── AC3: job handler + red→notify path ─────────────────────────────────────

import pytest


@pytest.fixture(autouse=True)
def _isolate_alert_state(tmp_path, monkeypatch):
    """Each test gets a fresh alert-state file so M1 dedup never leaks across
    tests (and never touches the real ~/.swarm-ai state)."""
    from jobs.handlers import session_health_probe as h
    monkeypatch.setattr(h, "_ALERT_STATE", tmp_path / ".session_health_alert")


def test_ac3_handler_notifies_on_red():
    """A degraded probe result must trigger the notifier exactly once."""
    from jobs.handlers import session_health_probe as h
    from core.session_health_probe import ProbeResult, Check

    sent = {}

    def fake_notifier(**kwargs):
        sent.update(kwargs)
        return {"slack": {"success": True}}

    def fake_probe():
        return ProbeResult(status="degraded",
                           checks=[Check("total_rss", False, "3600MB / 3500MB")],
                           summary="FAILED: total_rss")

    out = h.run_session_health_probe(notifier=fake_notifier, probe_fn=fake_probe)
    assert out["probe_status"] == "degraded"
    assert out["notified"] is True
    assert "total_rss" in out["failed"]
    assert sent and "total_rss" in sent["message"]
    assert "DEGRADED" in sent["title"]


def test_ac3_handler_silent_when_healthy():
    from jobs.handlers import session_health_probe as h
    from core.session_health_probe import ProbeResult, Check

    sent = {}
    def fake_notifier(**kwargs):
        sent.update(kwargs); return {}

    def fake_probe():
        return ProbeResult(status="healthy",
                           checks=[Check("total_rss", True, "1200MB")],
                           summary="all checks pass")

    out = h.run_session_health_probe(notifier=fake_notifier, probe_fn=fake_probe)
    assert out["probe_status"] == "healthy"
    assert out["notified"] is False
    assert sent == {}, "must NOT notify when healthy"


def test_ac3_dry_run_never_notifies():
    from jobs.handlers import session_health_probe as h
    from core.session_health_probe import ProbeResult, Check

    called = {"n": 0}
    def fake_notifier(**kwargs):
        called["n"] += 1; return {}

    def fake_probe():
        return ProbeResult(status="degraded", checks=[Check("x", False)], summary="bad")

    h.run_session_health_probe(dry_run=True, notifier=fake_notifier, probe_fn=fake_probe)
    assert called["n"] == 0


# ── M1: alarm dedup (no 15-min storm) ──────────────────────────────────────

def test_m1_no_alarm_storm_same_failure_notifies_once():
    """A sustained identical degraded state must notify ONCE, not every tick."""
    from jobs.handlers import session_health_probe as h
    from core.session_health_probe import ProbeResult, Check

    calls = {"n": 0}
    def notifier(**kwargs):
        calls["n"] += 1; return {}

    def degraded():
        return ProbeResult(status="degraded",
                           checks=[Check("total_rss", False, "3600MB")],
                           summary="FAILED: total_rss")

    # Three consecutive identical degraded ticks → exactly one notification.
    for _ in range(3):
        h.run_session_health_probe(notifier=notifier, probe_fn=degraded)
    assert calls["n"] == 1, f"alarm storm: notified {calls['n']}x for one sustained issue"


def test_m1_recovery_sends_one_green_then_silent():
    from jobs.handlers import session_health_probe as h
    from core.session_health_probe import ProbeResult, Check

    msgs = []
    def notifier(**kwargs):
        msgs.append(f"{kwargs['title']} | {kwargs['message']}"); return {}

    def degraded():
        return ProbeResult(status="degraded", checks=[Check("total_rss", False)], summary="bad")
    def healthy():
        return ProbeResult(status="healthy", checks=[Check("total_rss", True)], summary="ok")

    h.run_session_health_probe(notifier=notifier, probe_fn=degraded)   # red → 1 alert
    h.run_session_health_probe(notifier=notifier, probe_fn=healthy)    # green → 1 recovery
    h.run_session_health_probe(notifier=notifier, probe_fn=healthy)    # still green → silent
    assert len(msgs) == 2
    assert "DEGRADED" in msgs[0] and "RECOVERED" in msgs[1]


def test_m1_changed_failure_set_re_notifies():
    from jobs.handlers import session_health_probe as h
    from core.session_health_probe import ProbeResult, Check

    calls = {"n": 0}
    def notifier(**kwargs):
        calls["n"] += 1; return {}

    h.run_session_health_probe(notifier=notifier,
        probe_fn=lambda: ProbeResult("degraded", [Check("total_rss", False)], "a"))
    # A DIFFERENT failure set is a new condition → re-notify.
    h.run_session_health_probe(notifier=notifier,
        probe_fn=lambda: ProbeResult("degraded", [Check("daemon_health", False)], "b"))
    assert calls["n"] == 2


# ── C1/C2 regression: the REAL _fetch_rss wiring must work (not a fake) ─────

def test_c1c2_real_fetch_rss_returns_positive_mb():
    """Adversarial C1/C2: _fetch_rss called a non-existent method/field and was
    silently dead. This exercises the REAL wiring against the live process."""
    from jobs.handlers.session_health_probe import _fetch_rss
    mb = _fetch_rss()
    assert isinstance(mb, float)
    assert mb > 0, "real process-tree RSS must be positive (was silently 0 → dead check)"


# ── M2: session-correlated recovery (no cross-session false-green) ─────────

def test_m2_unrelated_session_recovery_does_not_absolve():
    """A Retry for a DIFFERENT session must NOT mark this session's failure recovered.
    Failure fixture is a GENUINE marker (streaming_timeout), not force_unstick
    (a recovery action) — run_67a391a4."""
    log = ("streaming_timeout on session aaaaaaaa\n"
           + "unrelated line\n" * 3
           + "Retry 1/3 for session bbbbbbbb --resume\n")
    out = shp.scan_unrecovered_events(log)
    assert len(out) == 1 and "aaaaaaaa" in out[0], "cross-session recovery wrongly absolved"


def test_m2_same_session_recovery_within_window_absolves():
    log = ("streaming_timeout for session aaaaaaaa\n"
           "Retry 1/3 for session aaaaaaaa --resume\n")
    assert shp.scan_unrecovered_events(log) == []


def test_m2_recovery_outside_window_does_not_absolve():
    # Genuine-failure fixture (streaming_timeout), not force_unstick — run_67a391a4.
    log = ("streaming_timeout on session aaaaaaaa\n"
           + "filler\n" * 60
           + "Retry 1/3 for session aaaaaaaa --resume\n")
    out = shp.scan_unrecovered_events(log)
    assert len(out) == 1, "recovery beyond the window must not absolve"


# ── run_dc86c466: the 3 silent-fail-safe bugs (dict shape / missing pid / log path) ──
#
# These exercise the REAL wiring (real _fetch_streaming, real core probe, real log
# path resolution) against the REAL endpoint payload shape ({"sessions": {sid: {...}}}).
# Every prior streaming test used a hand-rolled LIST fixture, which structurally hid
# the fact that the live endpoint returns a dict-of-dicts. See EVOLUTION skeptic verdict.

def test_bug1_fetch_streaming_normalizes_dict_of_dicts(monkeypatch):
    """AC1: the REAL endpoint returns {"sessions": {sid: {...}}} (dict-of-dicts).
    _fetch_streaming MUST normalize that into a list[dict] where each item carries
    session_id (injected from the key). Pre-fix it returned the inner dict verbatim,
    so the core probe iterated string keys and crashed into fail-safe."""
    from jobs.handlers import session_health_probe as h
    real_payload = {"sessions": {
        "sid-aaaa": {"streaming": True, "state": "streaming", "pid": 4242},
        "sid-bbbb": {"streaming": False, "state": "idle", "pid": None},
    }}
    monkeypatch.setattr(h, "_http_json", lambda path, timeout=4.0: real_payload)
    out = h._fetch_streaming()
    assert isinstance(out, list), f"must be list, got {type(out).__name__}"
    assert len(out) == 2
    for item in out:
        assert isinstance(item, dict), "each item must be a dict, not a bare key string"
        assert "session_id" in item, "session_id must be injected from the dict key"
        assert "state" in item and "streaming" in item
    by_id = {i["session_id"]: i for i in out}
    assert by_id["sid-aaaa"]["pid"] == 4242
    assert by_id["sid-aaaa"]["state"] == "streaming"


def test_bug1_fetch_streaming_still_handles_list_and_wrapped_list(monkeypatch):
    """Regression: the pre-existing {"sessions": [...]} and bare-list shapes must
    still normalize (don't break the shapes the old code handled)."""
    from jobs.handlers import session_health_probe as h
    monkeypatch.setattr(h, "_http_json",
                        lambda p, timeout=4.0: {"sessions": [{"session_id": "x", "state": "idle"}]})
    assert h._fetch_streaming() == [{"session_id": "x", "state": "idle"}]
    monkeypatch.setattr(h, "_http_json",
                        lambda p, timeout=4.0: [{"session_id": "y", "state": "idle"}])
    assert h._fetch_streaming() == [{"session_id": "y", "state": "idle"}]


def test_bug3_log_path_points_at_real_daemon_log():
    """AC3: the handler's log path must be the REAL file the daemon writes
    (backend-daemon.log), not the non-existent daemon.log. Sourced from the single
    source of truth, not a second hardcoded string that can drift."""
    from jobs.handlers import session_health_probe as h
    from config import get_log_file_path
    assert h._LOG_PATH == get_log_file_path(), (
        f"handler log path {h._LOG_PATH} != source-of-truth {get_log_file_path()}")
    assert h._LOG_PATH.name == "backend-daemon.log"


def test_bug3_read_log_reads_a_real_file(tmp_path, monkeypatch):
    """AC3: _read_log() must return the file's content (pre-fix it pointed at a
    nonexistent path → always returned "" → no_unrecovered_events scanned nothing)."""
    from jobs.handlers import session_health_probe as h
    logf = tmp_path / "backend-daemon.log"
    logf.write_text("streaming_timeout on session zzzzzzzz\n(no recovery after)\n")
    monkeypatch.setattr(h, "_LOG_PATH", logf)
    content = h._read_log()
    assert "streaming_timeout" in content, "must read the real log content, not empty string"


def test_bug2_and_bug1_end_to_end_checks_actually_execute(tmp_path, monkeypatch):
    """AC4 (mutation-proven): drive the REAL _fetch_streaming + REAL core probe
    against the REAL dict-of-dicts payload and a REAL temp log. Both previously-dead
    checks must now EXECUTE:
      - no_wedged_sessions produces a COUNT detail ("N streaming, wedged=...") — NOT
        the "session scan unreadable (fail-safe)" string.
      - no_unrecovered_events flags the unrecovered marker from the temp log.
    """
    from jobs.handlers import session_health_probe as h

    # Real log with an unrecovered failure marker + the streaming session's id so
    # _session_log_progressed can measure it. Uses a GENUINE terminal marker
    # (streaming_timeout, no recovery after) — NOT force_unstick, which is a
    # recovery action (run_67a391a4).
    logf = tmp_path / "backend-daemon.log"
    logf.write_text(
        "sid-wedged streaming turn began\n"
        "lifecycle_manager.streaming_timeout session other999 stall=900s\n"  # unrecovered → must flag
    )
    monkeypatch.setattr(h, "_LOG_PATH", logf)

    # Real endpoint shape: one streaming session WITH a pid so the wedged loop
    # does not skip it (proves bug #2 fixed: pid present + reachable).
    payload = {"sessions": {
        "sid-wedged": {"streaming": True, "state": "streaming", "pid": 999999},
    }}
    monkeypatch.setattr(h, "_http_json", lambda p, timeout=4.0: payload)

    # cpu_sampler: deterministic zero-delta (idle) so — combined with no NEW log
    # progress — the double-signal declares wedged. This proves the check RAN.
    cpu_vals = iter([5.0, 5.0])
    result = shp.session_health_probe(
        health_fetcher=lambda: {"database": {"healthy": True},
                                "channel_gateway": {"startup_state": "started"}},
        streaming_fetcher=h._fetch_streaming,          # REAL fetcher
        rss_fetcher=lambda: 1000.0,
        cpu_sampler=lambda pid: next(cpu_vals),
        log_reader=h._read_log,                        # REAL log reader
        sleep_fn=_noop_sleep,
    )
    checks = {c.name: c for c in result.checks}

    wedged_detail = checks["no_wedged_sessions"].detail
    assert "unreadable" not in wedged_detail, (
        f"no_wedged_sessions still fail-safing: {wedged_detail!r}")
    assert "streaming" in wedged_detail, (
        f"expected a real count detail, got {wedged_detail!r}")
    # zero-cpu + no-log-progress for a visible session → genuinely wedged → flagged
    assert checks["no_wedged_sessions"].ok is False, "wedged session must be detected"

    assert checks["no_unrecovered_events"].ok is False, (
        "unrecovered streaming_timeout in the real log must be flagged")


# ── run_6b10ea1c: bug1 recency-based progress + bug2 daemon-pid resolution ──
#
# bug1: the OLD _session_log_progressed used a 2s before/after delta. The ONLY
# per-session line the daemon emits between turn events is
# lifecycle_manager.memory_sample (~60s cadence, emitted for EVERY live session
# incl. wedged ones). So a HEALTHY turn mid-Bedrock-inference showed no new line
# in the 2s window → progressed=False → combined with IO-wait's ~0 CPU delta, a
# healthy turn was flagged wedged (the live false-positive on session 90732703).
# The fix: progress = a REAL (non-housekeeping) turn event within a recency
# window > the heartbeat period.

from datetime import datetime, timedelta

_NOW = datetime(2026, 7, 3, 20, 16, 0)


def _ts(delta_s: int) -> str:
    return (_NOW - timedelta(seconds=delta_s)).strftime("%Y-%m-%d %H:%M:%S")


def test_bug1_recent_real_event_is_progress():
    """AC1: a healthy IO-wait turn with a REAL turn event within the recency
    window → progressed=True (NOT wedged). This is the live false-positive."""
    log = f"{_ts(30)},123 - core.streaming_orchestrator - INFO - Query sent for session abcd1234-1e8a\n"
    assert shp._session_log_progressed("abcd1234", log, now=_NOW) is True


def test_bug1_only_housekeeping_is_not_progress():
    """AC3: a session visible ONLY via lifecycle_manager.memory_sample
    (housekeeping — fires for wedged sessions too) has NO real progress → False.
    The RECENT housekeeping line must NOT be mistaken for turn progress."""
    log = (f"{_ts(5)},1 - core.lifecycle_manager - INFO - memory_sample "
           f"total=1000MB sessions=[abcd1234=500MB(peak=600MB,STREAMING)]\n")
    assert shp._session_log_progressed("abcd1234", log, now=_NOW) is False


def test_bug1_stale_real_event_beyond_window_is_not_progress():
    """AC1: a real event older than the recency window → not progressing → False."""
    log = f"{_ts(300)},1 - core.streaming_orchestrator - INFO - Query sent for session abcd1234\n"
    assert shp._session_log_progressed("abcd1234", log, now=_NOW) is False


def test_bug1_unseen_session_is_none_failsafe():
    """AC2: session id never appears → None → caller fails safe (never alarm)."""
    assert shp._session_log_progressed("zzzz9999", "unrelated line\n", now=_NOW) is None


def test_bug1_recency_boundary_inclusive():
    """Adversarial LOW: pin the recency window boundary (uses <=, inclusive).
    Exactly recency_s ago → True; one second beyond → False. Guards against a
    `<=`→`<` flip or an off-by-one in _PROGRESS_RECENCY_S."""
    at_edge = f"{_ts(int(shp._PROGRESS_RECENCY_S))},1 - core.streaming_orchestrator - INFO - Query sent for session abcd1234\n"
    assert shp._session_log_progressed("abcd1234", at_edge, now=_NOW) is True, "boundary is inclusive"
    beyond = f"{_ts(int(shp._PROGRESS_RECENCY_S) + 1)},1 - core.streaming_orchestrator - INFO - Query sent for session abcd1234\n"
    assert shp._session_log_progressed("abcd1234", beyond, now=_NOW) is False, "one second past window → stale"


def test_bug1_recent_housekeeping_but_stale_real_is_not_progress():
    """AC3 (the exact wedge signature): fresh memory_sample housekeeping (~5s ago)
    but the last REAL event is 5min old → the housekeeping must NOT rescue it →
    False. This is what makes a genuine wedge detectable despite the heartbeat."""
    log = (f"{_ts(300)},1 - core.streaming_orchestrator - INFO - Query sent for session abcd1234\n"
           f"{_ts(5)},1 - core.lifecycle_manager - INFO - memory_sample sessions=[abcd1234=500MB(STREAMING)]\n")
    assert shp._session_log_progressed("abcd1234", log, now=_NOW) is False


def test_bug1_healthy_turn_not_wedged_end_to_end(monkeypatch):
    """AC1 end-to-end: drive the FULL probe with a real dict-of-dicts payload and
    a log whose only recent line is a REAL turn event (idle CPU in the slice).
    Pre-fix this false-flagged wedged; post-fix the recency signal clears it."""
    from jobs.handlers import session_health_probe as h
    # NOTE: build the log relative to real now so the recency check sees it fresh.
    fresh = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logf = _tmp_log(monkeypatch, h,
                    f"{fresh},1 - core.streaming_orchestrator - INFO - Query sent for session live5678\n")
    payload = {"sessions": {"live5678": {"streaming": True, "state": "streaming", "pid": 999999}}}
    monkeypatch.setattr(h, "_http_json", lambda p, timeout=4.0: payload)
    cpu_vals = iter([5.0, 5.0])  # zero CPU delta — IO-wait; only the log signal can clear it
    result = shp.session_health_probe(
        health_fetcher=lambda: {"database": {"healthy": True},
                                "channel_gateway": {"startup_state": "started"}},
        streaming_fetcher=h._fetch_streaming,
        rss_fetcher=lambda: 1000.0,
        cpu_sampler=lambda pid: next(cpu_vals),
        log_reader=h._read_log,
        sleep_fn=_noop_sleep,
    )
    checks = {c.name: c for c in result.checks}
    assert checks["no_wedged_sessions"].ok is True, (
        f"healthy IO-wait turn with a recent real event must NOT be flagged wedged; "
        f"got {checks['no_wedged_sessions'].detail!r}")


def _tmp_log(monkeypatch, h, content: str):
    import tempfile, pathlib
    p = pathlib.Path(tempfile.mkstemp(suffix="-backend-daemon.log")[1])
    p.write_text(content)
    monkeypatch.setattr(h, "_LOG_PATH", p)
    return p


# ── bug2: _fetch_rss must measure the resolved DAEMON pid, not os.getpid() ──
#
# The scheduled job runs in-process (os.getpid()==daemon), but a standalone
# `python -c` / on-demand call measures the caller temp process (reported 45MB
# while the daemon was ~1600MB) — the RSS check was silently dead for that path.

def test_bug2_resolve_daemon_pid_env_first(monkeypatch):
    """env pid is trusted when it is LIVE (os.getpid() is always live)."""
    import os as _os
    from jobs.handlers import session_health_probe as h
    monkeypatch.setenv("SWARMAI_OWNER_PID", str(_os.getpid()))
    assert h._resolve_daemon_pid() == _os.getpid()


def test_bug2_stale_env_pid_falls_through_to_launchctl(monkeypatch):
    """Adversarial MED: a STALE/dead SWARMAI_OWNER_PID (daemon restarted, env
    inherited) must NOT be trusted — else a dead pid → process_tree_rss 0 →
    RSS check silently passes (bug2's own failure mode). Fall through to launchctl."""
    from jobs.handlers import session_health_probe as h
    monkeypatch.setenv("SWARMAI_OWNER_PID", "999999")  # not a live pid
    monkeypatch.setattr(h, "_pid_alive", lambda pid: False)  # force dead

    class _R:
        returncode = 0
        stdout = '\t"PID" = 21510;\n\t"Label" = "com.swarmai.backend";\n'

    monkeypatch.setattr(h.subprocess, "run", lambda *a, **k: _R())
    assert h._resolve_daemon_pid() == 21510, "stale env pid must fall through, not be trusted"


def test_bug2_pid_alive_probe(monkeypatch):
    import os as _os
    from jobs.handlers import session_health_probe as h
    assert h._pid_alive(_os.getpid()) is True
    assert h._pid_alive(999999) is False


def test_bug2_resolve_daemon_pid_launchctl_fallback(monkeypatch):
    from jobs.handlers import session_health_probe as h
    monkeypatch.delenv("SWARMAI_OWNER_PID", raising=False)

    class _R:
        returncode = 0
        stdout = '\t"PID" = 21510;\n\t"Label" = "com.swarmai.backend";\n'

    monkeypatch.setattr(h.subprocess, "run", lambda *a, **k: _R())
    assert h._resolve_daemon_pid() == 21510


def test_bug2_resolve_daemon_pid_last_resort_getpid(monkeypatch):
    """No env, launchctl unavailable (Linux/CI or no daemon) → os.getpid()."""
    import os as _os
    from jobs.handlers import session_health_probe as h
    monkeypatch.delenv("SWARMAI_OWNER_PID", raising=False)

    def _boom(*a, **k):
        raise FileNotFoundError("launchctl not found")

    monkeypatch.setattr(h.subprocess, "run", _boom)
    assert h._resolve_daemon_pid() == _os.getpid()


def test_bug2_fetch_rss_measures_resolved_pid(monkeypatch):
    """AC4: _fetch_rss must call process_tree_rss with the RESOLVED daemon pid,
    not os.getpid() of a (possibly standalone) caller."""
    from jobs.handlers import session_health_probe as h
    monkeypatch.setattr(h, "_resolve_daemon_pid", lambda: 424242)
    seen = {}

    class _FakeRM:
        def process_tree_rss(self, pid):
            seen["pid"] = pid
            return 1600 * 1024 * 1024

    monkeypatch.setattr("core.resource_monitor.resource_monitor", _FakeRM())
    mb = h._fetch_rss()
    assert seen["pid"] == 424242, "must measure the resolved daemon pid, not os.getpid()"
    assert abs(mb - 1600.0) < 1.0


def test_bug1_mutation_prefix_dict_makes_wedged_check_fail_safe():
    """MUTATION PROOF that the shape fix is load-bearing: feed the core probe the
    PRE-FIX shape (the raw inner dict, exactly what the old _fetch_streaming returned)
    and confirm no_wedged_sessions crashes into fail-safe. If this test ever goes
    green with the buggy shape, the fix was cosmetic."""
    raw_dict = {"sid-aaaa": {"streaming": True, "state": "streaming", "pid": 1}}
    result = shp.session_health_probe(
        health_fetcher=lambda: {"database": {"healthy": True},
                                "channel_gateway": {"startup_state": "started"}},
        streaming_fetcher=lambda: raw_dict,   # BUG shape: dict, not list
        rss_fetcher=lambda: 1000.0,
        cpu_sampler=lambda pid: 5.0,
        log_reader=lambda: "",
        sleep_fn=_noop_sleep,
    )
    detail = {c.name: c for c in result.checks}["no_wedged_sessions"].detail
    assert "unreadable" in detail, (
        "the raw-dict shape MUST fail-safe (proves the normalize fix is load-bearing); "
        f"got {detail!r}")
