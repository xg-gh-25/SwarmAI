"""Property-based tests for HealingLoop state machine transitions.

Uses Hypothesis to verify invariants hold across all valid input sequences:
1. Total heals is monotonically non-decreasing
2. After MAX_HEAL_ATTEMPTS, can_heal always returns False (until success reset)
3. Cooldown is always respected (no heal within HEAL_COOLDOWN_S of last)
4. record_heal_success resets attempts to 0

These properties catch edge cases that example-based tests miss
(e.g., rapid fire heal attempts, time boundary conditions).
"""

import time

from hypothesis import given, settings
from hypothesis import strategies as st

from core.session_healing import (
    HealingLoop,
    HealthSensor,
    HEAL_COOLDOWN_S,
    MAX_HEAL_ATTEMPTS,
)


# ═══════════════════════════════════════════════════════════════════
# Strategy: Action sequences for HealingLoop
# ═══════════════════════════════════════════════════════════════════

# Actions that can be applied to a HealingLoop
ACTIONS = st.sampled_from(["start", "success", "failure", "check"])


@st.composite
def action_sequences(draw):
    """Generate a sequence of 1-20 HealingLoop actions."""
    length = draw(st.integers(min_value=1, max_value=20))
    return [draw(ACTIONS) for _ in range(length)]


# ═══════════════════════════════════════════════════════════════════
# Property 1: Total heals is monotonically non-decreasing
# ═══════════════════════════════════════════════════════════════════


class TestProperty1TotalHealsMonotonic:
    """total_heals never decreases regardless of action sequence."""

    @settings(max_examples=100, deadline=None)
    @given(actions=action_sequences())
    def test_total_heals_never_decreases(self, actions):
        loop = HealingLoop()
        prev_total = 0

        for action in actions:
            if action == "start":
                if loop.can_heal()[0]:
                    loop.record_heal_start()
                    # Force past cooldown for next iteration
                    loop._last_heal_time = time.time() - HEAL_COOLDOWN_S - 1
            elif action == "success":
                loop.record_heal_success()
            elif action == "failure":
                loop.record_heal_failure("test_reason")
            # "check" does nothing (just reads state)

            assert loop.total_heals >= prev_total, (
                f"total_heals decreased: {prev_total} -> {loop.total_heals} "
                f"after action={action}"
            )
            prev_total = loop.total_heals


# ═══════════════════════════════════════════════════════════════════
# Property 2: MAX_HEAL_ATTEMPTS is a hard ceiling
# ═══════════════════════════════════════════════════════════════════


class TestProperty2MaxAttemptsHardCeiling:
    """After MAX_HEAL_ATTEMPTS consecutive starts without success,
    can_heal always returns False."""

    @settings(max_examples=80, deadline=None)
    @given(extra_starts=st.integers(min_value=0, max_value=10))
    def test_max_attempts_blocks(self, extra_starts):
        loop = HealingLoop()

        # Exhaust all attempts
        for i in range(MAX_HEAL_ATTEMPTS):
            loop.record_heal_start()
            loop._last_heal_time = time.time() - HEAL_COOLDOWN_S - 1

        # Now try more starts — can_heal must say no
        for _ in range(extra_starts):
            can, reason = loop.can_heal()
            assert can is False
            assert "max_attempts" in reason

    @settings(max_examples=50, deadline=None)
    @given(attempts_before_success=st.integers(min_value=1, max_value=MAX_HEAL_ATTEMPTS - 1))
    def test_success_before_max_allows_more(self, attempts_before_success):
        """record_heal_success before max → more attempts available."""
        loop = HealingLoop()

        for _ in range(attempts_before_success):
            loop.record_heal_start()
            loop._last_heal_time = time.time() - HEAL_COOLDOWN_S - 1

        loop.record_heal_success()
        loop._last_heal_time = time.time() - HEAL_COOLDOWN_S - 1

        # After success reset, can_heal should be True
        can, _ = loop.can_heal()
        assert can is True


# ═══════════════════════════════════════════════════════════════════
# Property 3: Cooldown is always respected
# ═══════════════════════════════════════════════════════════════════


class TestProperty3CooldownRespected:
    """No heal is permitted within HEAL_COOLDOWN_S of last heal start."""

    @settings(max_examples=80, deadline=None)
    @given(elapsed_fraction=st.floats(min_value=0.0, max_value=0.99))
    def test_within_cooldown_always_blocked(self, elapsed_fraction):
        """Any time within cooldown window → blocked."""
        loop = HealingLoop()
        loop.record_heal_start()

        # Set time to within cooldown
        elapsed = HEAL_COOLDOWN_S * elapsed_fraction
        loop._last_heal_time = time.time() - elapsed

        can, reason = loop.can_heal()
        assert can is False
        assert "cooldown" in reason

    @settings(max_examples=80, deadline=None)
    @given(extra_seconds=st.floats(min_value=0.1, max_value=100.0))
    def test_past_cooldown_allowed(self, extra_seconds):
        """Any time past cooldown → allowed (if attempts not exhausted)."""
        loop = HealingLoop()
        loop.record_heal_start()

        # Set time past cooldown
        loop._last_heal_time = time.time() - HEAL_COOLDOWN_S - extra_seconds

        can, reason = loop.can_heal()
        assert can is True
        assert reason == ""


# ═══════════════════════════════════════════════════════════════════
# Property 4: record_heal_success resets attempts to 0
# ═══════════════════════════════════════════════════════════════════


class TestProperty4SuccessResetsAttempts:
    """record_heal_success always resets _heal_attempts to 0,
    regardless of how many attempts preceded it."""

    @settings(max_examples=80, deadline=None)
    @given(num_starts=st.integers(min_value=1, max_value=MAX_HEAL_ATTEMPTS))
    def test_success_resets_after_any_count(self, num_starts):
        loop = HealingLoop()

        for _ in range(num_starts):
            loop.record_heal_start()
            loop._last_heal_time = time.time() - HEAL_COOLDOWN_S - 1

        loop.record_heal_success()
        assert loop.heal_attempts == 0


# ═══════════════════════════════════════════════════════════════════
# Property 5: HealthSensor trigger detection consistency
# ═══════════════════════════════════════════════════════════════════


class TestProperty5HealthSensorTriggers:
    """HealthSensor triggers are deterministic for the same input sequence."""

    @settings(max_examples=60, deadline=None)
    @given(
        turns=st.lists(
            st.tuples(
                st.floats(min_value=50, max_value=5000),  # latency_ms
                st.integers(min_value=500, max_value=8000),  # rss_mb
                st.booleans(),  # is_error
            ),
            min_size=1,
            max_size=20,
        )
    )
    def test_same_inputs_same_result(self, turns):
        """Running the same sequence twice produces identical trigger decisions."""
        results_a = []
        results_b = []

        for run_results in [results_a, results_b]:
            sensor = HealthSensor(max_turns=500)
            for latency, rss, is_err in turns:
                sensor.record_turn(latency, rss, is_err)
                should, trigger = sensor.should_checkpoint()
                run_results.append((should, trigger))

        assert results_a == results_b, "HealthSensor is non-deterministic!"

    @settings(max_examples=40, deadline=None)
    @given(
        max_turns=st.integers(min_value=20, max_value=500),
        offset=st.integers(min_value=0, max_value=10),
    )
    def test_turn_approach_fires_near_limit(self, max_turns, offset):
        """When turn count approaches max_turns, a turn-limit trigger fires.

        Root 2 / AC3 (G2) added ``turn_hard_floor`` at max_turns-HARD_FLOOR_BUFFER
        (-5), checked BEFORE ``turn_approaching`` (-20) so the more-urgent floor
        wins when both apply. So within 10 of the limit (offset<=5) the trigger is
        ``turn_hard_floor``; between -10 and -5 it is ``turn_approaching``. Either
        is a valid turn-limit checkpoint — the property is "near the limit ⇒ a
        turn-limit trigger fires", not "it is always turn_approaching".
        """
        from core.session_healing import HARD_FLOOR_BUFFER

        current_turn = max_turns - offset

        sensor = HealthSensor(max_turns=max_turns)
        # Simulate reaching that turn count
        for _ in range(current_turn):
            sensor.record_turn(100.0, 1000, False)

        should, trigger = sensor.should_checkpoint()
        # Within 10 of max_turns → should trigger a turn-limit checkpoint.
        if offset <= 10:
            assert should is True
            if offset <= HARD_FLOOR_BUFFER:
                assert trigger == "turn_hard_floor"
            else:
                assert trigger == "turn_approaching"
