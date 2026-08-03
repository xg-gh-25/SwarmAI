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

    # ── Recycle-kill (-9) must NOT be classified OOM ──────────────────
    # An app-initiated fast recycle (flush_recycle / interrupt_recycle of a
    # poisoned subprocess) force-kills the tree → "exit code -9", the SAME
    # string as an OS OOM SIGKILL. Without the recycle_kill flag it fell into
    # the OOM branch → 30/60/120s cooldown → tens of seconds of dead air on a
    # recycle that should --resume in ~0.5s ("回答死/卡半路").

    def test_recycle_kill_minus9_is_zombie_not_oom(self):
        ft, meta = classify_failure(
            "Command failed with exit code -9", recycle_kill=True,
        )
        assert ft == FailureType.ZOMBIE
        assert meta.get("recycle_kill") is True

    def test_recycle_kill_sigkill_is_zombie(self):
        ft, _ = classify_failure("Process received SIGKILL", recycle_kill=True)
        assert ft == FailureType.ZOMBIE

    def test_minus9_without_recycle_flag_stays_oom(self):
        # A real OS/Jetsam OOM kill (no recycle flag) must STILL be OOM so the
        # memory-pressure cooldown protects against death spirals.
        ft, _ = classify_failure(
            "Command failed with exit code -9", recycle_kill=False,
        )
        assert ft == FailureType.OOM

    def test_recycle_flag_only_reclassifies_sigkill(self):
        # The flag must NOT turn a coincident rate-limit/timeout into ZOMBIE —
        # only the -9/SIGKILL signature is reclassified.
        ft, _ = classify_failure("rate limit exceeded", recycle_kill=True)
        assert ft == FailureType.RATE_LIMIT
        ft2, _ = classify_failure("The operation timed out", recycle_kill=True)
        assert ft2 == FailureType.TIMEOUT

    def test_recycle_zombie_backoff_is_fast_vs_oom(self):
        # The whole point: recycle -9 respawns near-instantly, not after 30s.
        zombie_wait = compute_backoff(FailureType.ZOMBIE, {}, retry_count=1)
        oom_wait = compute_backoff(FailureType.OOM, {}, retry_count=1)
        assert zombie_wait <= 1.0
        assert oom_wait >= 30.0

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

    def test_timeout_operation_timed_out(self):
        """Regression: 'The operation timed out' from Bedrock must classify as TIMEOUT."""
        ft, _ = classify_failure("The operation timed out")
        assert ft == FailureType.TIMEOUT

    def test_timeout_connection_timed_out(self):
        ft, _ = classify_failure("Connection timed out after 30s")
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
        from unittest.mock import MagicMock

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
        assert result == {"decision": "approve"}
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
        assert result == {"decision": "approve"}
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


# ---------------------------------------------------------------------------
# Zombie subprocess (Fix B) — deterministic poison, near-zero backoff
# ---------------------------------------------------------------------------

class TestZombieClassification:
    """A reused-then-poisoned subprocess must skip the exponential backoff."""

    ZOMBIE_MSG = (
        "Zombie subprocess detected: error_during_execution with no content "
        "in 0.0s (session_id=4ffe4100-005b-4211-b489-fda503c5374b)"
    )

    def test_zombie_classified_as_zombie(self):
        ft, _ = classify_failure(self.ZOMBIE_MSG)
        assert ft == FailureType.ZOMBIE

    def test_zombie_case_insensitive(self):
        ft, _ = classify_failure(self.ZOMBIE_MSG.upper())
        assert ft == FailureType.ZOMBIE

    def test_zombie_backoff_is_near_zero(self):
        """The whole point of Fix B: respawn at once, not after 5s/10s/15s."""
        for retry_count in (1, 2, 3):
            backoff = compute_backoff(
                FailureType.ZOMBIE, {}, retry_count, base_backoff=5.0
            )
            assert backoff <= 1.0, (
                f"zombie backoff must be near-zero, got {backoff}s at retry {retry_count}"
            )

    def test_zombie_backoff_far_below_unknown(self):
        """Regression guard: zombie must be much faster than the old UNKNOWN path
        (which is what it used to classify as → 5s base exponential)."""
        zombie = compute_backoff(FailureType.ZOMBIE, {}, 1, base_backoff=5.0)
        unknown = compute_backoff(FailureType.UNKNOWN, {}, 1, base_backoff=5.0)
        assert zombie < unknown

    def test_zombie_does_not_shadow_oom(self):
        """An OOM message must still classify as OOM, not zombie."""
        ft, _ = classify_failure("Command failed with exit code -9")
        assert ft == FailureType.OOM


# ---------------------------------------------------------------------------
# Adversarial — try to break the zombie classification / ordering
# ---------------------------------------------------------------------------

class TestZombieAdversarial:
    """Probe the failure modes of Fix B: message-drift, ordering shadow,
    over-broad matching, and OOM precedence."""

    # The EXACT messages raised by streaming_orchestrator (both sites). If these
    # drift, classification silently regresses to UNKNOWN (5s backoff) — so pin
    # both real shapes here.
    ERROR_PATH = (
        "Zombie subprocess detected: error_during_execution with no content "
        "in 0.0s (session_id=4ffe4100-005b-4211-b489-fda503c5374b)"
    )
    EMPTY_STREAM = (
        "Zombie subprocess detected: stream ended in 0.1s with no content "
        "(session_id=4ffe4100-005b-4211-b489-fda503c5374b)"
    )

    def test_both_raise_sites_classify_zombie(self):
        assert classify_failure(self.ERROR_PATH)[0] == FailureType.ZOMBIE
        assert classify_failure(self.EMPTY_STREAM)[0] == FailureType.ZOMBIE

    def test_session_id_containing_dash_9_still_zombie_not_oom(self):
        """A UUID segment starting with 9 yields the substring '-9' (an OOM
        token). Zombie-first ordering must still win — this IS a zombie, and a
        0.5s respawn is correct, not the 30s OOM cooldown."""
        msg = (
            "Zombie subprocess detected: stream ended in 0.0s with no content "
            "(session_id=4ffe4100-005b-4211-b489-9da503c5374b)"  # '-9da' → '-9'
        )
        assert "-9" in msg.lower()  # the trap is real
        assert classify_failure(msg)[0] == FailureType.ZOMBIE

    def test_real_oom_is_NOT_shadowed_by_zombie_first_ordering(self):
        """The reverse must hold: a genuine OOM (no zombie phrase) must NOT be
        captured by the zombie branch — it must still get the 30s OOM cooldown,
        or fast respawn re-introduces the death spiral the OOM backoff prevents."""
        for oom_msg in (
            "Command failed with exit code -9",
            "Process received SIGKILL",
            "jetsam killed the process",
        ):
            ft, _ = classify_failure(oom_msg)
            assert ft == FailureType.OOM, f"{oom_msg!r} must stay OOM, got {ft}"
            # And its backoff must be the slow OOM one, never the 0.5s zombie path.
            assert compute_backoff(ft, {}, 1, 5.0) >= 30.0

    def test_error_during_execution_alone_is_not_zombie(self):
        """Over-broad-match guard: only the explicit 'zombie subprocess detected'
        phrase triggers the fast path. A bare error_during_execution string (no
        zombie prefix) must NOT be classified ZOMBIE."""
        ft, _ = classify_failure("error_during_execution: something went wrong")
        assert ft != FailureType.ZOMBIE

    def test_rate_limit_not_misread_as_zombie(self):
        """A normal rate-limit error must keep its long backoff, not the 0.5s."""
        ft, _ = classify_failure("rate limit exceeded, throttled")
        assert ft == FailureType.RATE_LIMIT
        assert compute_backoff(ft, {}, 1, 5.0) >= 30.0
