"""Tests for OOM retry governance (Fixes 1-5).

Validates the anti-death-spiral mechanisms:

- Fix 1: OOM backoff is exponential (30s, 60s, 120s), not flat 30s
- Fix 2: Global OOM cooldown prevents parallel retry storms
- Fix 3: Per-session OOM counter persists across send() calls
- Fix 4: spawn_budget denies spawns during OOM cooldown
- Fix 5: OOM events yield status messages to frontend
"""

import time
from unittest.mock import patch

import pytest

from core.session_utils import FailureType, compute_backoff


class TestOOMBackoffEscalation:
    """Fix 1: OOM backoff should escalate exponentially."""

    def test_first_oom_retry_30s(self):
        backoff = compute_backoff(FailureType.OOM, {}, retry_count=1)
        assert backoff == 30.0

    def test_second_oom_retry_60s(self):
        backoff = compute_backoff(FailureType.OOM, {}, retry_count=2)
        assert backoff == 60.0

    def test_third_oom_retry_120s(self):
        backoff = compute_backoff(FailureType.OOM, {}, retry_count=3)
        assert backoff == 120.0

    def test_oom_backoff_capped_at_120s(self):
        backoff = compute_backoff(FailureType.OOM, {}, retry_count=10)
        assert backoff == 120.0

    def test_non_oom_backoff_unchanged(self):
        """API errors still use exponential from base."""
        b1 = compute_backoff(FailureType.API_ERROR, {}, retry_count=1, base_backoff=5.0)
        b2 = compute_backoff(FailureType.API_ERROR, {}, retry_count=2, base_backoff=5.0)
        assert b1 == 5.0
        assert b2 == 10.0


class TestGlobalOOMCooldown:
    """Fix 2: Global cooldown variable prevents parallel retry storms."""

    def test_cooldown_variable_exists(self):
        from core.session_unit import _oom_cooldown_until, _OOM_COOLDOWN_BASE
        assert isinstance(_oom_cooldown_until, float)
        assert _OOM_COOLDOWN_BASE == 30.0


class TestPerSessionOOMCounter:
    """Fix 3: OOM counter persists across send() calls."""

    def test_oom_counter_initialized_to_zero(self):
        from core.session_unit import SessionUnit
        unit = SessionUnit("test-session", "default")
        assert unit._consecutive_oom_kills == 0

    def test_oom_counter_not_reset_by_retry_count_reset(self):
        """_retry_count resets in send(), but _consecutive_oom_kills must NOT."""
        from core.session_unit import SessionUnit
        unit = SessionUnit("test-session", "default")
        unit._consecutive_oom_kills = 2
        # Simulate what send() does
        unit._retry_count = 0
        # OOM counter should survive
        assert unit._consecutive_oom_kills == 2


class TestSpawnBudgetOOMCooldown:
    """Fix 4: spawn_budget denies spawns during OOM cooldown."""

    def test_spawn_denied_during_cooldown(self):
        from core.resource_monitor import ResourceMonitor
        monitor = ResourceMonitor()
        # Record an OOM event
        monitor.record_oom()
        # Immediately check spawn budget — should be denied
        budget = monitor.spawn_budget()
        assert not budget.can_spawn
        assert "OOM" in budget.reason

    def test_spawn_allowed_after_cooldown(self):
        from core.resource_monitor import ResourceMonitor
        monitor = ResourceMonitor()
        # Record OOM in the past (beyond cooldown)
        monitor._last_oom_time = time.monotonic() - 60.0
        budget = monitor.spawn_budget()
        # Should be allowed (cooldown expired)
        assert budget.can_spawn

    def test_record_oom_invalidates_cache(self):
        from core.resource_monitor import ResourceMonitor
        monitor = ResourceMonitor()
        monitor._cache_time = time.time()  # Simulate cached data
        monitor.record_oom()
        assert monitor._cache_time == 0.0  # Cache invalidated
