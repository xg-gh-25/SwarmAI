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
    log = "force_unstick fired on session xyz\n...nothing after..."
    out = shp.scan_unrecovered_events(log)
    assert len(out) == 1 and "force_unstick" in out[0]


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
    """A Retry for a DIFFERENT session must NOT mark this session's failure recovered."""
    log = ("force_unstick fired on session aaaaaaaa\n"
           + "unrelated line\n" * 3
           + "Retry 1/3 for session bbbbbbbb --resume\n")
    out = shp.scan_unrecovered_events(log)
    assert len(out) == 1 and "aaaaaaaa" in out[0], "cross-session recovery wrongly absolved"


def test_m2_same_session_recovery_within_window_absolves():
    log = ("streaming_timeout for session aaaaaaaa\n"
           "Retry 1/3 for session aaaaaaaa --resume\n")
    assert shp.scan_unrecovered_events(log) == []


def test_m2_recovery_outside_window_does_not_absolve():
    log = ("force_unstick on session aaaaaaaa\n"
           + "filler\n" * 60
           + "Retry 1/3 for session aaaaaaaa --resume\n")
    out = shp.scan_unrecovered_events(log)
    assert len(out) == 1, "recovery beyond the window must not absolve"
