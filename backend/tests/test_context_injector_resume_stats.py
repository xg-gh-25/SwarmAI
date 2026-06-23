"""Resume-context observability counters (R1: fail-loud not fail-hard).

Tests that build_resume_context increments a DISTINCT module-level counter for
each outcome, so a data-loss EXCEPTION failure is no longer indistinguishable
from a legitimate-empty result.

Root cause being fixed: context_injector.py:1100 `except Exception: return ""`
was logged at WARNING with no counter — a silent data-loss path masquerading as
a legitimate-empty path (no-session / no-messages / legacy-empty). All four
returned "" and looked identical in logs and to callers.

Invariant under test: the return CONTRACT is unchanged (still str, empty-string
semantics preserved) — only OBSERVABILITY is added. Control flow is byte-identical.
"""

import logging

import pytest

from core import context_injector
from core.context_injector import (
    build_resume_context,
    get_resume_stats,
    reset_resume_stats,
)


@pytest.fixture(autouse=True)
def _isolate_stats():
    """Module-level counters + cache need reset between tests (call-twice safety)."""
    reset_resume_stats()
    context_injector._resume_cache.clear()
    yield
    reset_resume_stats()
    context_injector._resume_cache.clear()


# ─── AC1: distinct counter per outcome ───────────────────────────────────


async def test_empty_no_session_increments_distinct_counter():
    """app_session_id=None → empty_no_session, NOT failed_exception."""
    result = await build_resume_context(None)
    assert result == ""  # contract preserved
    stats = get_resume_stats()
    assert stats["empty_no_session"] == 1
    assert stats["failed_exception"] == 0
    assert stats["empty_no_messages"] == 0


async def test_empty_no_messages_increments_distinct_counter(monkeypatch):
    """No messages in DB → empty_no_messages, NOT failed_exception."""

    class _FakeMessages:
        async def count_by_session(self, sid):
            return 0

        async def list_by_session_paginated(self, sid, limit=None):
            return []

    class _FakeDB:
        messages = _FakeMessages()

    monkeypatch.setitem(__import__("sys").modules, "database",
                        type("M", (), {"db": _FakeDB()}))

    result = await build_resume_context("sess-empty")
    assert result == ""
    stats = get_resume_stats()
    assert stats["empty_no_messages"] == 1
    assert stats["failed_exception"] == 0


# ─── AC2: exception path is OBSERVABLE and DISTINCT (the core bug) ────────


async def test_exception_path_increments_failed_not_empty(monkeypatch, caplog):
    """Forced DB exception → failed_exception+1, ERROR log, empty_* untouched.

    This is the bug R1 fixes: a data-loss exception was conflated with
    legitimate-empty. The counter must distinguish them, and the log must be
    ERROR (data loss), not WARNING.
    """

    class _BoomMessages:
        async def count_by_session(self, sid):
            raise RuntimeError("DB exploded")

        async def list_by_session_paginated(self, sid, limit=None):
            raise RuntimeError("DB exploded")

    class _BoomDB:
        messages = _BoomMessages()

    monkeypatch.setitem(__import__("sys").modules, "database",
                        type("M", (), {"db": _BoomDB()}))

    with caplog.at_level(logging.ERROR, logger="core.context_injector"):
        result = await build_resume_context("sess-boom")

    assert result == ""  # contract preserved — still fail-soft, startup not blocked
    stats = get_resume_stats()
    assert stats["failed_exception"] == 1, "exception must be its OWN counter"
    # The conflation that WAS the bug:
    assert stats["empty_no_session"] == 0
    assert stats["empty_no_messages"] == 0
    assert stats["empty_legacy"] == 0
    # fail-LOUD: ERROR-level log emitted
    assert any(r.levelno == logging.ERROR for r in caplog.records), \
        "exception path must log at ERROR (data loss), not WARNING"


# ─── Adversarial MED: inner-helper failure must be DISTINCT, not conflated ──


async def test_enrichment_failure_is_degraded_not_conflated(monkeypatch, caplog):
    """Inner enrichment except → enrichment_degraded+1, NOT failed_exception,
    NOT a clean legacy_success/empty_legacy with no signal.

    This is the adversarial-MED finding: the original fix only instrumented the
    TOP-LEVEL except. An enrichment-helper failure (checkpoint/conclusions/etc.)
    falls through to legacy and was recorded as a clean outcome — the same
    silent-data-loss conflation, one layer down. This forces that path.
    """
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
    ]

    class _Messages:
        async def count_by_session(self, sid):
            return len(msgs)

        async def list_by_session_paginated(self, sid, limit=None):
            return msgs

    class _DB:
        messages = _Messages()

    monkeypatch.setitem(__import__("sys").modules, "database",
                        type("M", (), {"db": _DB()}))
    # Make an INNER enrichment helper raise (inside the inner try block).
    monkeypatch.setattr(context_injector, "_build_checkpoint",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    with caplog.at_level(logging.WARNING, logger="core.context_injector"):
        result = await build_resume_context("sess-degraded")

    stats = get_resume_stats()
    assert stats["enrichment_degraded"] == 1, \
        "inner enrichment failure must be counted, not silently dropped"
    assert stats["failed_exception"] == 0, \
        "inner failure is NOT a top-level exception"
    # It degraded to legacy or empty_legacy — either way the degradation is now
    # observable via enrichment_degraded, which is the whole point.
    assert isinstance(result, str)  # contract preserved


# ─── AC3: accessor reflects increments ────────────────────────────────────


async def test_get_resume_stats_returns_all_keys():
    stats = get_resume_stats()
    for key in (
        "cache_hit", "enriched_success", "legacy_success",
        "empty_no_session", "empty_no_messages", "empty_legacy",
        "failed_exception", "enrichment_degraded",
    ):
        assert key in stats, f"missing counter key: {key}"
        assert stats[key] == 0


async def test_get_resume_stats_is_a_copy():
    """Accessor must not leak the live dict (mutation isolation)."""
    snap = get_resume_stats()
    snap["failed_exception"] = 999
    assert get_resume_stats()["failed_exception"] == 0


async def test_call_twice_accumulates(monkeypatch):
    """Module-level counter must accumulate across calls (call-twice correctness)."""
    result1 = await build_resume_context(None)
    result2 = await build_resume_context(None)
    assert result1 == "" and result2 == ""
    assert get_resume_stats()["empty_no_session"] == 2
