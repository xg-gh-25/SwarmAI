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
