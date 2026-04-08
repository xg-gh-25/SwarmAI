"""Tests for failure classification and smart retry backoff.

Verifies that classify_failure() correctly identifies failure types from
hook-captured context and string patterns, and that compute_backoff()
returns appropriate wait times for each failure type.
"""
import time
import pytest

from core.session_utils import (
    FailureType,
    classify_failure,
    compute_backoff,
    _extract_resets_at,
    _is_rate_limit_notification,
)


# ---------------------------------------------------------------------------
# classify_failure — hook context takes priority over string matching
# ---------------------------------------------------------------------------

class TestClassifyFailure:
    """Structured failure classification."""

    def test_oom_exit_code_minus_9(self):
        ft, meta = classify_failure("Command failed with exit code -9")
        assert ft == FailureType.OOM

    def test_oom_sigkill(self):
        ft, _ = classify_failure("Process received SIGKILL")
        assert ft == FailureType.OOM

    def test_oom_jetsam(self):
        ft, _ = classify_failure("jetsam killed the process")
        assert ft == FailureType.OOM

    def test_oom_terminated_process(self):
        ft, _ = classify_failure("Cannot write to terminated process")
        assert ft == FailureType.OOM

    def test_rate_limit_string_pattern(self):
        ft, _ = classify_failure("rate limit exceeded")
        assert ft == FailureType.RATE_LIMIT

    def test_rate_limit_throttling(self):
        ft, _ = classify_failure("Request throttled by Bedrock")
        assert ft == FailureType.RATE_LIMIT

    def test_rate_limit_too_many_requests(self):
        ft, _ = classify_failure("Too many requests")
        assert ft == FailureType.RATE_LIMIT

    def test_rate_limit_from_hook_context(self):
        """Hook-captured notification takes priority."""
        hook_ctx = {
            "_last_notification": {
                "type": "rate_limit",
                "message": "Rate limit hit, resets at 1711612800",
            }
        }
        ft, meta = classify_failure("some generic error", hook_ctx)
        assert ft == FailureType.RATE_LIMIT
        assert meta.get("resets_at") == 1711612800.0

    def test_rate_limit_hook_with_retry_after(self):
        """Hook notification with 'retry after N' seconds."""
        now = time.time()
        hook_ctx = {
            "_last_notification": {
                "type": "rate_limit_warning",
                "message": "Throttled. Retry after 30 seconds.",
            }
        }
        ft, meta = classify_failure("throttled", hook_ctx)
        assert ft == FailureType.RATE_LIMIT
        assert meta["resets_at"] >= now + 29  # within tolerance

    def test_timeout(self):
        ft, _ = classify_failure(
            "Streaming timeout (init): no SDK response for 180s"
        )
        assert ft == FailureType.TIMEOUT

    def test_api_error_service_unavailable(self):
        ft, _ = classify_failure("service unavailable")
        assert ft == FailureType.API_ERROR

    def test_api_error_connection_reset(self):
        ft, _ = classify_failure("ECONNRESET on socket")
        assert ft == FailureType.API_ERROR

    def test_api_error_overloaded(self):
        ft, _ = classify_failure("API overloaded, try again")
        assert ft == FailureType.API_ERROR

    def test_api_error_broken_pipe(self):
        ft, _ = classify_failure("broken pipe")
        assert ft == FailureType.API_ERROR

    def test_unknown_fallback(self):
        ft, _ = classify_failure("some completely unknown error string")
        assert ft == FailureType.UNKNOWN

    def test_hook_context_none_safe(self):
        """Works fine when hook_context is None."""
        ft, _ = classify_failure("exit code -9", None)
        assert ft == FailureType.OOM

    def test_hook_context_empty_notification(self):
        """Empty notification dict doesn't crash."""
        hook_ctx = {"_last_notification": {}}
        ft, _ = classify_failure("unknown error", hook_ctx)
        assert ft == FailureType.UNKNOWN

    def test_oom_takes_priority_over_hook_notification(self):
        """OOM patterns (string) beat non-rate-limit notifications."""
        hook_ctx = {
            "_last_notification": {
                "type": "info",
                "message": "Session starting",
            }
        }
        ft, _ = classify_failure("exit code: -9", hook_ctx)
        assert ft == FailureType.OOM

    def test_rate_limit_hook_beats_oom_string(self):
        """If hook says rate limit AND string says OOM, hook wins (checked first)."""
        hook_ctx = {
            "_last_notification": {
                "type": "rate_limit",
                "message": "Rate limited",
            }
        }
        # Unlikely combo, but tests priority order
        ft, _ = classify_failure("exit code -9 rate limit", hook_ctx)
        assert ft == FailureType.RATE_LIMIT


# ---------------------------------------------------------------------------
# compute_backoff — failure-type-aware wait times
# ---------------------------------------------------------------------------

class TestComputeBackoff:
    """Failure-type-aware backoff computation."""

    def test_oom_exponential_backoff(self):
        """OOM uses exponential backoff: 30s, 60s, 120s (capped)."""
        assert compute_backoff(FailureType.OOM, {}, retry_count=1) == 30.0
        assert compute_backoff(FailureType.OOM, {}, retry_count=2) == 60.0
        assert compute_backoff(FailureType.OOM, {}, retry_count=3) == 120.0

    def test_rate_limit_default_60s(self):
        """No resets_at → 60s default."""
        assert compute_backoff(FailureType.RATE_LIMIT, {}, retry_count=1) == 60.0

    def test_rate_limit_with_resets_at(self):
        """Wait until resets_at + 2s buffer."""
        future = time.time() + 45.0
        backoff = compute_backoff(
            FailureType.RATE_LIMIT,
            {"resets_at": future},
            retry_count=1,
        )
        assert 44.0 <= backoff <= 48.0  # ~45 + 2s buffer, within tolerance

    def test_rate_limit_resets_at_capped_at_300s(self):
        """Don't wait forever even if resets_at is far future."""
        far_future = time.time() + 600.0
        backoff = compute_backoff(
            FailureType.RATE_LIMIT,
            {"resets_at": far_future},
            retry_count=1,
        )
        assert backoff == 300.0

    def test_rate_limit_resets_at_in_past(self):
        """If resets_at already passed, use 2s buffer (min)."""
        past = time.time() - 10.0
        backoff = compute_backoff(
            FailureType.RATE_LIMIT,
            {"resets_at": past},
            retry_count=1,
        )
        assert backoff == 2.0  # max(0, past-now) + 2 = 2

    def test_timeout_exponential(self):
        b1 = compute_backoff(FailureType.TIMEOUT, {}, retry_count=1, base_backoff=5.0)
        b2 = compute_backoff(FailureType.TIMEOUT, {}, retry_count=2, base_backoff=5.0)
        b3 = compute_backoff(FailureType.TIMEOUT, {}, retry_count=3, base_backoff=5.0)
        assert b1 == 5.0
        assert b2 == 10.0
        assert b3 == 15.0

    def test_timeout_capped_at_60s(self):
        backoff = compute_backoff(FailureType.TIMEOUT, {}, retry_count=100, base_backoff=5.0)
        assert backoff == 60.0

    def test_api_error_exponential(self):
        b = compute_backoff(FailureType.API_ERROR, {}, retry_count=2, base_backoff=5.0)
        assert b == 10.0

    def test_unknown_exponential(self):
        b = compute_backoff(FailureType.UNKNOWN, {}, retry_count=1, base_backoff=5.0)
        assert b == 5.0


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

class TestExtractResetsAt:
    """Timestamp extraction from notification messages."""

    def test_resets_at_unix_seconds(self):
        assert _extract_resets_at("resets at 1711612800") == 1711612800.0

    def test_resets_at_unix_millis(self):
        ts = _extract_resets_at("resets_at: 1711612800000")
        assert ts == pytest.approx(1711612800.0, abs=1)

    def test_retry_after_seconds(self):
        now = time.time()
        ts = _extract_resets_at("Retry after 60 seconds")
        assert ts >= now + 59

    def test_no_match(self):
        assert _extract_resets_at("Some random message") is None

    def test_empty_string(self):
        assert _extract_resets_at("") is None


class TestIsRateLimitNotification:
    """Rate limit notification detection."""

    def test_type_rate_limit(self):
        assert _is_rate_limit_notification(
            {"type": "rate_limit", "message": "anything"}, ""
        )

    def test_message_contains_rate_limit(self):
        assert _is_rate_limit_notification(
            {"type": "warning", "message": "Rate limit approaching"}, ""
        )

    def test_message_throttled(self):
        assert _is_rate_limit_notification(
            {"type": "error", "message": "Request throttled"}, ""
        )

    def test_error_matches_rate_pattern(self):
        assert _is_rate_limit_notification(
            {"type": "info", "message": "generic"}, "rate limit exceeded"
        )

    def test_no_rate_limit(self):
        assert not _is_rate_limit_notification(
            {"type": "info", "message": "Session started"}, "unknown error"
        )


# ---------------------------------------------------------------------------
# Hook builder integration — verify hooks write to session_context
# ---------------------------------------------------------------------------

class TestHookBuilderFailureHooks:
    """Verify hook_builder.py creates Notification and Stop hooks."""

    @pytest.mark.asyncio
    async def test_notification_hook_writes_context(self):
        """Notification hook writes _last_notification to session_context."""
        from core.hook_builder import build_hooks
        from unittest.mock import AsyncMock, MagicMock

        pm = MagicMock()
        pm.is_command_approved = MagicMock(return_value=False)

        session_ctx: dict = {"sdk_session_id": "test-123"}
        hooks, _, _ = await build_hooks(
            agent_config={"enable_tool_logging": False, "global_user_mode": True},
            enable_skills=False,
            enable_mcp=False,
            resume_session_id=None,
            session_context=session_ctx,
            permission_manager=pm,
        )

        assert "Notification" in hooks
        assert len(hooks["Notification"]) == 1

        # Call the hook
        hook_fn = hooks["Notification"][0].hooks[0]
        result = await hook_fn(
            {"message": "Rate limit approaching", "notification_type": "rate_limit"},
            None, None,
        )
        assert result == {}
        assert session_ctx["_last_notification"]["type"] == "rate_limit"
        assert "Rate limit" in session_ctx["_last_notification"]["message"]

    @pytest.mark.asyncio
    async def test_stop_hook_writes_context(self):
        """Stop hook writes _stop_info to session_context."""
        from core.hook_builder import build_hooks
        from unittest.mock import MagicMock

        pm = MagicMock()
        pm.is_command_approved = MagicMock(return_value=False)

        session_ctx: dict = {"sdk_session_id": "test-456"}
        hooks, _, _ = await build_hooks(
            agent_config={"enable_tool_logging": False, "global_user_mode": True},
            enable_skills=False,
            enable_mcp=False,
            resume_session_id=None,
            session_context=session_ctx,
            permission_manager=pm,
        )

        assert "Stop" in hooks
        hook_fn = hooks["Stop"][0].hooks[0]
        result = await hook_fn(
            {"stop_hook_active": True},
            None, None,
        )
        assert result == {}
        assert session_ctx["_stop_info"]["stop_hook_active"] is True

    @pytest.mark.asyncio
    async def test_no_hooks_without_session_context(self):
        """No failure hooks when session_context is None."""
        from core.hook_builder import build_hooks
        from unittest.mock import MagicMock

        pm = MagicMock()
        pm.is_command_approved = MagicMock(return_value=False)

        hooks, _, _ = await build_hooks(
            agent_config={"enable_tool_logging": False, "global_user_mode": True},
            enable_skills=False,
            enable_mcp=False,
            resume_session_id=None,
            session_context=None,
            permission_manager=pm,
        )

        assert "Notification" not in hooks
        assert "Stop" not in hooks
        assert "PreCompact" not in hooks
