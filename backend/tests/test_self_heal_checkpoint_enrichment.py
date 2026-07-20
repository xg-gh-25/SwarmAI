"""Exploration + (later) preservation tests for self-heal checkpoint enrichment.

WHAT IS TESTED
--------------
The residual self-heal gaps on HEAD `87a037e3` for the
`self-heal-rich-checkpoint-and-wrapup` bugfix spec:

- GAP 1 — the rich checkpoint omits available task context. `build_rich_checkpoint`
  fills only `files_modified`, `uncommitted_changes`, and a thin filename-list
  `key_findings`; `completed_steps`, `pending_steps`, `active_file_context`, and the
  pipeline fields are left at empty/None defaults because the builder has no parameter
  to thread them in and the heal call site never derives them.
- GAP 2 (central) — the `turn_approaching` wrap-up conclusion produced by the agent is
  streamed to the user and then discarded; there is no parameter on
  `build_rich_checkpoint` to fold it into the checkpoint, so the agent's own
  "where I left off" summary never reaches `to_continuation_prompt()`.

METHODOLOGY (exploratory bugfix)
--------------------------------
These are **bug-condition exploration tests** written and run on UNFIXED code BEFORE
the fix. They encode the EXPECTED (post-fix) behavior, so they MUST FAIL on current
HEAD — a failure here is the SUCCESS case: it confirms the residual gaps exist. They
are re-run unchanged in task 4.5 and should flip to PASS only after the fix lands. Do
NOT weaken these assertions and do NOT modify production code to satisfy them here.

The failure mode on unfixed code is that `build_rich_checkpoint` rejects the
enrichment parameters the fixed call site will pass (`agent_conclusion`,
`completed_steps`, `pending_steps`, `active_file`) — there is literally no parameter
to thread the conclusion / progress / active-file context in.

PROPERTIES
----------
- Property 1 (Bug Condition): for a heal that fires with in-flight progress, the fixed
  heal path produces a `TaskCheckpoint` whose `to_continuation_prompt()` carries the
  available context — at minimum the agent's wrap-up conclusion when one exists, plus
  whatever progress / findings / pipeline context can be determined at heal time.
  Validates Requirements 2.1, 2.2, 2.5.
"""

import pytest

from core.session_healing import build_rich_checkpoint

# ─── Shared fixtures / constants ─────────────────────────────────────────────

# A representative agent wrap-up conclusion produced in response to WRAP_UP_PROMPT
# on a `turn_approaching` heal. The respawned agent's continuation prompt MUST carry
# this forward so it knows where it left off.
WRAPUP_CONCLUSION = "Parser done; only error-path tests remain"

ORIGINAL_REQUEST = "Implement the config parser and wire it into the router"


@pytest.fixture
def wrapup_conclusion() -> str:
    """The agent's concluding summary from a turn_approaching wrap-up turn."""
    return WRAPUP_CONCLUSION


@pytest.fixture
def completed_steps() -> list[str]:
    """Completed work derivable from session history (decisions)."""
    return [
        "Wrote config parser in parser.py",
        "Wired parser into router.py",
    ]


@pytest.fixture
def pending_steps() -> list[str]:
    """Remaining work derivable from session history (open questions / topics)."""
    return [
        "Add error-path tests for the parser",
        "Handle malformed-config edge case",
    ]


# ─── Property 1: Bug Condition exploration tests (MUST FAIL on unfixed code) ──


class TestBugConditionCheckpointEnrichment:
    """Exploration tests proving GAP 1 + GAP 2 on current HEAD.

    Each test calls `build_rich_checkpoint` the way the FIXED heal call site will —
    threading the agent's wrap-up conclusion and the history-derived progress /
    active-file context. On unfixed code the builder has no parameter to accept this
    enrichment, so these calls raise / the context never reaches the continuation
    prompt. The tests therefore FAIL now (correct) and PASS after the fix.
    """

    @pytest.mark.asyncio
    async def test_2a_turn_approaching_conclusion_reaches_continuation_prompt(
        self, wrapup_conclusion
    ):
        """2a (GAP 2, central): the wrap-up conclusion must reach the continuation prompt.

        On a `turn_approaching` heal the agent produced a concluding summary. The fixed
        builder leads `key_findings` with it so `to_continuation_prompt()` carries it
        forward. On UNFIXED code there is no `agent_conclusion` parameter to thread the
        conclusion in, so this FAILS (counterexample: conclusion absent from / unable to
        reach the continuation prompt).

        Validates: Requirements 2.5
        """
        checkpoint = await build_rich_checkpoint(
            original_request=ORIGINAL_REQUEST,
            working_dir=None,
            trigger="turn_approaching",
            turn_count=482,
            agent_conclusion=wrapup_conclusion,
        )

        prompt = checkpoint.to_continuation_prompt()
        assert wrapup_conclusion in prompt, (
            "turn_approaching wrap-up conclusion was discarded — it does not reach "
            "to_continuation_prompt(); there is no parameter to thread it into the "
            "checkpoint on unfixed code"
        )

    @pytest.mark.asyncio
    async def test_2b_progress_fields_populated_from_history(
        self, completed_steps, pending_steps
    ):
        """2b (GAP 1): completed/pending steps must be populated from history.

        A `memory_growth` heal fires with session history available. The fixed
        builder accepts derived `completed_steps` / `pending_steps` and renders the
        `**Completed:**` / `**Next:**` lines. On UNFIXED code these parameters do not
        exist and the fields stay empty, so this FAILS (counterexample:
        completed_steps == [] / pending_steps == [] and the lines are absent).

        Validates: Requirements 2.1, 2.2
        """
        checkpoint = await build_rich_checkpoint(
            original_request=ORIGINAL_REQUEST,
            working_dir=None,
            trigger="memory_growth",
            turn_count=60,
            completed_steps=completed_steps,
            pending_steps=pending_steps,
        )

        assert checkpoint.completed_steps == completed_steps, (
            "completed_steps stays empty — history-derived completed work is not "
            "populated on unfixed code"
        )
        assert checkpoint.pending_steps == pending_steps, (
            "pending_steps stays empty — history-derived remaining work is not "
            "populated on unfixed code"
        )

        prompt = checkpoint.to_continuation_prompt()
        assert "**Completed:**" in prompt, "**Completed:** line absent on unfixed code"
        assert "**Next:**" in prompt, "**Next:** line absent on unfixed code"

    @pytest.mark.asyncio
    async def test_2c_pipeline_and_active_file_context_threaded(self):
        """2c (GAP 1): pipeline + active-file context must reach the checkpoint.

        When a pipeline is active and an active file is known, the FIXED heal call site
        threads `active_file` (new enrichment param) plus `pipeline_run_id` /
        `pipeline_stage` so the checkpoint carries pipeline context and the active file.
        On UNFIXED code the call site never derives/passes this enrichment and the
        builder has no `active_file` parameter, so this FAILS (counterexample: the
        enriched call is rejected; `pipeline_run_id` / `pipeline_stage` stay None and the
        `**Pipeline:**` line is absent).

        Validates: Requirements 2.1, 2.2
        """
        checkpoint = await build_rich_checkpoint(
            original_request=ORIGINAL_REQUEST,
            working_dir=None,
            trigger="memory_growth",
            turn_count=60,
            active_file="parser.py",
            pipeline_run_id="run-1234",
            pipeline_stage="BUILD",
        )

        assert checkpoint.pipeline_run_id == "run-1234", (
            "pipeline_run_id is None — the call site never threads pipeline context "
            "on unfixed code"
        )
        assert checkpoint.pipeline_stage == "BUILD"
        assert "parser.py" in checkpoint.active_file_context, (
            "active_file_context is empty — there is no active_file parameter to thread "
            "the active file in on unfixed code"
        )

        prompt = checkpoint.to_continuation_prompt()
        assert "**Pipeline:**" in prompt, "**Pipeline:** line absent on unfixed code"
        assert "run-1234" in prompt


# ═════════════════════════════════════════════════════════════════════════════
# Property 2: PRESERVATION tests (Non-Bug Behavior Unchanged)
# ═════════════════════════════════════════════════════════════════════════════
#
# METHODOLOGY (observation-first): each test below was written by observing the
# behavior of UNFIXED HEAD `87a037e3` and encoding the observed output as an
# assertion. They MUST PASS on unfixed code — they capture the baseline that the
# Task 4 fix is forbidden to change (bugfix.md clauses 3.1-3.10). They live in a
# clearly-separated section and use distinct class names so they never collide
# with the Task 2 exploration classes above nor the 41 committed tests in
# tests/test_session_healing.py.
#
# Clause 3.9 (resume-fallback ownership: `_retry_with_resume` /
# `_inject_abandon_continuation`) is intentionally OUT OF SCOPE for this file and
# is owned by the `resume-fallback-context-preservation` spec — noted here only.
# ─────────────────────────────────────────────────────────────────────────────

import time  # noqa: E402

from core import session_healing  # noqa: E402
from core.session_healing import (  # noqa: E402
    HEAL_COOLDOWN_S,
    HANG_TIMEOUT_S,
    MAX_HEAL_ATTEMPTS,
    HealingLoop,
    HealthSensor,
    TaskCheckpoint,
    WRAP_UP_PROMPT,
    is_self_heal_enabled,
    parse_self_heal_mode,
    release_canary,
)


@pytest.fixture
def _reset_canary():
    """Reset the module-level canary owner around canary tests (test infra only).

    Guarantees no module-level state leaks BETWEEN tests — supports clause 3.8's
    "no new module-level mutable state introduced by tests" invariant.
    """
    session_healing._canary_session_id = None
    yield
    session_healing._canary_session_id = None


class TestPreservationHealLimits:
    """3.1 — heal attempt + cooldown limits unchanged (HealingLoop)."""

    def test_3_1_constants_unchanged(self):
        """MAX_HEAL_ATTEMPTS == 3 and HEAL_COOLDOWN_S == 60s, as today."""
        assert MAX_HEAL_ATTEMPTS == 3
        assert HEAL_COOLDOWN_S == 60.0

    def test_3_1_fresh_loop_can_heal(self):
        """A fresh HealingLoop permits healing (no attempts, no cooldown)."""
        loop = HealingLoop()
        assert loop.can_heal() == (True, "")
        assert loop.heal_attempts == 0
        assert loop.total_heals == 0

    def test_3_1_cooldown_active_after_one_start(self):
        """After one heal start, cooldown blocks the next heal (attempts < max)."""
        loop = HealingLoop()
        loop.record_heal_start()
        ok, reason = loop.can_heal()
        assert ok is False
        assert reason.startswith("cooldown_active")
        assert loop.heal_attempts == 1
        assert loop.total_heals == 1

    def test_3_1_max_attempts_exhausted_and_escalate(self):
        """Three starts exhaust attempts; can_heal denies and should_escalate True."""
        loop = HealingLoop()
        for _ in range(MAX_HEAL_ATTEMPTS):
            loop.record_heal_start()
        ok, reason = loop.can_heal()
        assert ok is False
        assert reason == "max_attempts_exhausted"
        assert loop.should_escalate() is True

    def test_3_1_success_resets_attempts(self):
        """record_heal_success resets the attempt counter; escalation clears."""
        loop = HealingLoop()
        for _ in range(MAX_HEAL_ATTEMPTS):
            loop.record_heal_start()
        assert loop.should_escalate() is True
        loop.record_heal_success()
        assert loop.heal_attempts == 0
        assert loop.should_escalate() is False
        # total_heals is cumulative and not reset by success
        assert loop.total_heals == MAX_HEAL_ATTEMPTS

    def test_3_1_record_heal_failure_is_side_effect_free_logging(self):
        """record_heal_failure only logs — never raises, never changes counters."""
        loop = HealingLoop()
        loop.record_heal_start()
        before = loop.heal_attempts
        loop.record_heal_failure("some reason")  # must not raise
        assert loop.heal_attempts == before


class TestPreservationSensor:
    """3.2 — HealthSensor thresholds + the five signal names unchanged."""

    def test_3_2_healthy_sensor_no_checkpoint(self):
        """A fresh sensor reports no heal needed: (False, "")."""
        sensor = HealthSensor(max_turns=500)
        assert sensor.should_checkpoint() == (False, "")

    def test_3_2_latency_no_longer_triggers(self):
        """Latency degradation was REMOVED (run_099724ca): a >2.5x shape must NOT heal."""
        sensor = HealthSensor(max_turns=500)
        for _ in range(10):
            sensor.record_turn(latency_ms=10.0, rss_mb=100, had_error=False)
        for _ in range(5):
            sensor.record_turn(latency_ms=100.0, rss_mb=100, had_error=False)
        assert sensor.should_checkpoint() == (False, "")

    def test_3_2_memory_growth_trigger(self):
        """RSS growth over the window >400MB → memory_growth (signal 2)."""
        sensor = HealthSensor(max_turns=500)
        for v in (100, 200, 300, 400, 500, 600, 700, 800, 900, 1000):
            sensor.record_turn(latency_ms=10.0, rss_mb=v, had_error=False)
        assert sensor.should_checkpoint() == (True, "memory_growth")

    def test_3_2_error_cascade_trigger(self):
        """Three consecutive errored turns → error_cascade (signal 3)."""
        sensor = HealthSensor(max_turns=500)
        for _ in range(3):
            sensor.record_turn(latency_ms=10.0, rss_mb=100, had_error=True)
        assert sensor.should_checkpoint() == (True, "error_cascade")

    def test_3_2_turn_approaching_trigger(self):
        """turn_count within TURN_APPROACH_BUFFER of max → turn_approaching (signal 4)."""
        sensor = HealthSensor(max_turns=25)  # threshold = 25 - 20 = 5
        for _ in range(5):
            sensor.record_turn(latency_ms=10.0, rss_mb=100, had_error=False)
        assert sensor.should_checkpoint() == (True, "turn_approaching")

    def test_3_2_hang_detected_trigger(self):
        """No activity for > HANG_TIMEOUT_S → hang_detected (signal 5)."""
        sensor = HealthSensor(max_turns=500)
        sensor._last_activity_time = time.time() - (HANG_TIMEOUT_S + 5)
        assert sensor.should_checkpoint() == (True, "hang_detected")

    def test_3_2_signal_names_are_the_known_set(self):
        """The producible trigger names are exactly the documented signals.

        latency_degradation was removed (run_099724ca), so the known set is now
        four: memory_growth, error_cascade, turn_approaching, hang_detected.
        (turn_hard_floor shares the turn-limit family and is not separately
        producible here.)
        """
        observed: set[str] = set()

        s2 = HealthSensor(max_turns=500)
        for v in (100, 200, 300, 400, 500, 600, 700, 800, 900, 1000):
            s2.record_turn(latency_ms=10.0, rss_mb=v, had_error=False)
        observed.add(s2.should_checkpoint()[1])

        s3 = HealthSensor(max_turns=500)
        for _ in range(3):
            s3.record_turn(latency_ms=10.0, rss_mb=100, had_error=True)
        observed.add(s3.should_checkpoint()[1])

        s4 = HealthSensor(max_turns=25)
        for _ in range(5):
            s4.record_turn(latency_ms=10.0, rss_mb=100, had_error=False)
        observed.add(s4.should_checkpoint()[1])

        s5 = HealthSensor(max_turns=500)
        s5._last_activity_time = time.time() - (HANG_TIMEOUT_S + 5)
        observed.add(s5.should_checkpoint()[1])

        assert observed == {
            "memory_growth",
            "error_cascade",
            "turn_approaching",
            "hang_detected",
        }


class TestPreservationGitFloor:
    """3.3 — build_rich_checkpoint git floor + empty fallback unchanged."""

    @pytest.mark.asyncio
    async def test_3_3_working_dir_none_empty_floor(self):
        """working_dir=None → no git extraction; floor fields empty, never raises."""
        cp = await build_rich_checkpoint(original_request="X", working_dir=None)
        assert cp.files_modified == []
        assert cp.uncommitted_changes == ""
        # uncommitted_changes is always capped at 500 chars
        assert len(cp.uncommitted_changes) <= 500

    @pytest.mark.asyncio
    async def test_3_3_non_git_dir_empty_fallback(self, tmp_path):
        """A non-git working_dir → git commands fail → graceful empty fallback."""
        cp = await build_rich_checkpoint(
            original_request="X", working_dir=str(tmp_path)
        )
        assert cp.files_modified == []
        assert cp.uncommitted_changes == ""

    @pytest.mark.asyncio
    async def test_3_3_file_tracker_key_findings_preserved(self):
        """file_tracker_paths still produces the thin 'Files touched' key_findings."""
        cp = await build_rich_checkpoint(
            original_request="X",
            working_dir=None,
            file_tracker_paths=["a.py", "b.py"],
        )
        assert cp.key_findings == "Files touched this session: a.py, b.py"
        assert "**Key context:**" in cp.to_continuation_prompt()


class TestPreservationKillContract:
    """3.7 — build_rich_checkpoint never-raise contract + defaults intact."""

    @pytest.mark.asyncio
    async def test_3_7_never_raises_and_defaults_intact(self):
        """Edge inputs yield a valid TaskCheckpoint with untouched empty defaults."""
        cp = await build_rich_checkpoint(
            original_request="",
            working_dir=None,
            trigger="hang_detected",
            turn_count=999,
            heal_attempt=2,
        )
        assert isinstance(cp, TaskCheckpoint)
        # Enrichment fields remain at empty defaults on unfixed code
        assert cp.completed_steps == []
        assert cp.pending_steps == []
        assert cp.pipeline_run_id is None
        assert cp.pipeline_stage is None
        assert cp.active_file_context == ""
        # Metadata threaded through unchanged
        assert cp.trigger == "hang_detected"
        assert cp.turn_count == 999
        assert cp.heal_attempt == 2
        # Empty original_request stays empty (no crash on slicing)
        assert cp.original_request == ""


class TestPreservationWrapUpAndGate:
    """3.4 wrap-up prompt constant + 3.5 three-mode gate semantics unchanged."""

    def test_3_4_wrap_up_prompt_constant_intact(self):
        """WRAP_UP_PROMPT exists, is a non-empty string, and keeps its contract."""
        assert isinstance(WRAP_UP_PROMPT, str)
        assert WRAP_UP_PROMPT
        lowered = WRAP_UP_PROMPT.lower()
        assert "turn limit" in lowered
        # Instructs the agent NOT to acknowledge the system note to the user
        assert "do not" in lowered

    def test_3_5_parse_self_heal_mode_semantics(self):
        """parse_self_heal_mode maps env values to off/all/canary as today."""
        assert parse_self_heal_mode("0") == "off"
        assert parse_self_heal_mode("1") == "all"
        assert parse_self_heal_mode("canary") == "canary"
        assert parse_self_heal_mode("") == "off"
        # strip + lowercase normalization preserved
        assert parse_self_heal_mode("  CANARY  ") == "canary"
        assert parse_self_heal_mode("1 ") == "all"
        # any other value defaults to off
        assert parse_self_heal_mode("anything") == "off"

    def test_3_5_gate_off(self, monkeypatch, _reset_canary):
        """Gate off → is_self_heal_enabled always False."""
        monkeypatch.setenv("SWARMAI_SELF_HEAL", "0")
        assert is_self_heal_enabled("s1") is False
        assert is_self_heal_enabled("s1", is_channel=True) is False

    def test_3_5_gate_all(self, monkeypatch, _reset_canary):
        """Gate all → is_self_heal_enabled always True."""
        monkeypatch.setenv("SWARMAI_SELF_HEAL", "1")
        assert is_self_heal_enabled("s1") is True
        assert is_self_heal_enabled("s2") is True

    def test_3_5_gate_canary_keyed_by_session(self, monkeypatch, _reset_canary):
        """Canary: first non-channel session claims; others denied; channels denied."""
        monkeypatch.setenv("SWARMAI_SELF_HEAL", "canary")
        assert is_self_heal_enabled("sessA") is True   # first claims
        assert is_self_heal_enabled("sessA") is True   # same session keeps it
        assert is_self_heal_enabled("sessB") is False  # a different session denied
        assert is_self_heal_enabled("chan", is_channel=True) is False  # channels never

    def test_3_5_release_canary_frees_ownership(self, monkeypatch, _reset_canary):
        """release_canary frees the owner so a later session can claim it."""
        monkeypatch.setenv("SWARMAI_SELF_HEAL", "canary")
        assert is_self_heal_enabled("sessA") is True
        release_canary("sessA")
        assert session_healing._canary_session_id is None
        # next session can now claim canary
        assert is_self_heal_enabled("sessB") is True


class TestPreservationContinuationPrompt:
    """3.6 consumption-path shape + 3.10 healthy/no-progress rendering unchanged."""

    def test_3_6_minimal_checkpoint_renders_base_sections_only(self):
        """A minimal TaskCheckpoint renders base sections and no enrichment lines."""
        cp = TaskCheckpoint(original_request="Build the parser")
        prompt = cp.to_continuation_prompt()
        # Base sections always present
        assert "## Task Continuation" in prompt
        assert "**Original request:** Build the parser" in prompt
        assert "Pick up exactly where you left off" in prompt
        # No enrichment → none of the optional lines appear
        assert "**Completed:**" not in prompt
        assert "**Next:**" not in prompt
        assert "**Pipeline:**" not in prompt
        assert "**Working state:**" not in prompt
        assert "**Key context:**" not in prompt

    @pytest.mark.asyncio
    async def test_3_10_build_rich_checkpoint_no_enrichment_lines(self):
        """A no-enrichment build renders only base sections (no Completed/Next/Pipeline)."""
        cp = await build_rich_checkpoint(original_request="X", working_dir=None)
        prompt = cp.to_continuation_prompt()
        assert "**Completed:**" not in prompt
        assert "**Next:**" not in prompt
        assert "**Pipeline:**" not in prompt
        assert "**Original request:** X" in prompt


class TestPreservationIdentityIsolation:
    """3.8 — identity/isolation: canary is the only module-level mutable state and
    it resets cleanly via release_canary; tests introduce no new module-level state.
    """

    def test_3_8_canary_resets_via_release_and_no_leak(self, monkeypatch, _reset_canary):
        """_canary_session_id is claimed on a non-owner no-op release, freed by owner."""
        monkeypatch.setenv("SWARMAI_SELF_HEAL", "canary")
        assert is_self_heal_enabled("owner") is True
        assert session_healing._canary_session_id == "owner"
        # releasing a non-owner is a no-op (does not steal ownership)
        release_canary("not-owner")
        assert session_healing._canary_session_id == "owner"
        # owner release frees it back to None
        release_canary("owner")
        assert session_healing._canary_session_id is None

    def test_3_8_canary_state_is_clean_at_start(self, _reset_canary):
        """With the reset fixture applied, no canary owner leaks in from prior tests."""
        assert session_healing._canary_session_id is None


# ═════════════════════════════════════════════════════════════════════════════
# Task 5: PROPERTY-BASED + ROBUSTNESS tests (hypothesis)
# ═════════════════════════════════════════════════════════════════════════════
#
# METHODOLOGY (property-based, per design "Testing Strategy")
# -----------------------------------------------------------
# These encode the design's three correctness properties as hypothesis-driven
# generators over the checkpoint input domain. They are run AFTER the Task 4 fix
# and MUST PASS. They are additive: they use distinct class names and never touch
# the Task 2 exploration classes, the Task 3 preservation classes above, nor the
# 41 committed tests in tests/test_session_healing.py.
#
# `build_rich_checkpoint` is async; hypothesis `@given` requires SYNC test bodies,
# so each example drives the coroutine via `asyncio.run` (no `@pytest.mark.asyncio`
# on these — that would conflict with `@given`). working_dir=None keeps the common
# path subprocess-free and fast; a bounded non-git-tempdir test covers the git path.
#
# PROPERTIES
# ----------
# - Property 1 (C inputs): for random in-flight states the fixed builder's
#   `to_continuation_prompt()` carries forward the available context — the agent
#   conclusion when present, plus the Completed/Next/Active file/Pipeline lines for
#   whatever progress/working-state was determinable. Validates 2.1, 2.2, 2.4, 2.5.
# - Property 2 (¬C inputs): for random no-enrichment calls the prompt is byte-for-byte
#   the minimal-checkpoint baseline (no Completed/Next/Pipeline/Working state/Key
#   context/Active file lines beyond what the original request itself contains).
#   Validates 3.x preservation of the base rendering.
# - Property 3 (total robustness): `build_rich_checkpoint` never raises across random
#   inputs (empty/long/unicode requests, None vs [] progress, working_dir=None and a
#   non-git dir); the wrap-up capture buffer is length-bounded to `_WRAPUP_CAP`; and
#   the enrichment helper returns {} (never raises) without an app_session_id.
#   Validates 2.6.

import asyncio  # noqa: E402

from hypothesis import HealthCheck, assume, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

# ─── Shared strategies / helpers ─────────────────────────────────────────────

_TRIGGERS = [
    "memory_growth",
    "error_cascade",
    "turn_approaching",
    "hang_detected",
]

# Enrichment-line markers that must NOT appear (beyond the original request text)
# in a no-enrichment continuation prompt.
_ENRICHMENT_MARKERS = (
    "**Completed:**",
    "**Next:**",
    "**Pipeline:**",
    "**Working state:**",
    "**Key context:**",
    "**Active file:**",
)

_step_list = st.lists(st.text(min_size=1, max_size=60), max_size=6)
_pipeline_id = st.one_of(st.none(), st.text(min_size=1, max_size=40))

_SUPPRESS = [HealthCheck.too_slow, HealthCheck.filter_too_much]


def _run_async(coro):
    """Drive a coroutine to completion in a throwaway event loop.

    Used so hypothesis `@given` (which needs a sync body) can exercise the async
    `build_rich_checkpoint`. asyncio.run creates + tears down a fresh loop per
    example; safe because there is no outer running loop in these sync tests.
    """
    return asyncio.run(coro)


# ─── Property 1: C inputs carry forward available context ────────────────────


class TestProperty1RichContextCarriedForward:
    """Property 1 — random in-flight states reach the continuation prompt.

    For any bug-condition input (a heal with some available context), the fixed
    `build_rich_checkpoint().to_continuation_prompt()` carries that context forward:
    a non-empty agent conclusion leads `key_findings` (and appears in the prompt);
    provided completed/pending steps render the `**Completed:**` / `**Next:**` lines;
    a provided active file renders the active-file line; a pipeline id renders the
    `**Pipeline:**` line.

    Validates: Requirements 2.1, 2.2, 2.4, 2.5
    """

    @settings(max_examples=80, deadline=None, suppress_health_check=_SUPPRESS)
    @given(
        original=st.text(max_size=200),
        trigger=st.sampled_from(_TRIGGERS),
        conclusion=st.text(max_size=200),
        completed=_step_list,
        pending=_step_list,
        active=st.text(max_size=80),
        pipeline_id=_pipeline_id,
        turn_count=st.integers(min_value=0, max_value=100_000),
    )
    def test_available_context_reaches_prompt(
        self, original, trigger, conclusion, completed, pending, active,
        pipeline_id, turn_count,
    ):
        # C: at least one piece of context is available to carry forward.
        assume(bool(conclusion.strip() or completed or pending or active or pipeline_id))

        cp = _run_async(
            build_rich_checkpoint(
                original_request=original,
                working_dir=None,
                trigger=trigger,
                turn_count=turn_count,
                agent_conclusion=conclusion,
                completed_steps=completed or None,
                pending_steps=pending or None,
                active_file=active or None,
                pipeline_run_id=pipeline_id,
                pipeline_stage="BUILD" if pipeline_id else None,
            )
        )
        prompt = cp.to_continuation_prompt()

        # Base sections are always present regardless of enrichment.
        assert "## Task Continuation" in prompt
        assert "Pick up exactly where you left off" in prompt

        # Conclusion leads key_findings and surfaces in the prompt (GAP 2 / 2.5).
        if conclusion.strip():
            assert conclusion.strip() in cp.key_findings
            assert conclusion.strip() in prompt
        # Progress lines render when steps were provided (GAP 1 / 2.1, 2.2).
        if completed:
            assert "**Completed:**" in prompt
        if pending:
            assert "**Next:**" in prompt
        # Active file line renders when an active file was provided.
        if active:
            assert "**Active file:**" in prompt
            assert active in prompt
        # Pipeline line renders when a pipeline run id was provided (2.4).
        if pipeline_id:
            assert "**Pipeline:**" in prompt
            assert pipeline_id in prompt


# ─── Property 2: ¬C inputs render the minimal-checkpoint baseline ────────────


class TestProperty2NonBugBaselineUnchanged:
    """Property 2 — no-enrichment calls match the minimal-checkpoint baseline.

    For any non-bug input (no conclusion, empty/None progress, no active file, no
    pipeline) the prompt is byte-for-byte identical to a bare
    `TaskCheckpoint(original_request=...).to_continuation_prompt()`, and introduces
    none of the enrichment lines beyond whatever the original request text itself
    contains (count-equality vs the baseline makes the assertion robust to a request
    that happens to embed a marker literal).

    Validates: base rendering preservation (3.x).
    """

    @settings(max_examples=80, deadline=None, suppress_health_check=_SUPPRESS)
    @given(
        original=st.text(max_size=200),
        trigger=st.sampled_from(_TRIGGERS),
        completed=st.sampled_from([None, []]),
        pending=st.sampled_from([None, []]),
        turn_count=st.integers(min_value=0, max_value=100_000),
        heal_attempt=st.integers(min_value=0, max_value=3),
    )
    def test_no_enrichment_matches_minimal_baseline(
        self, original, trigger, completed, pending, turn_count, heal_attempt,
    ):
        cp = _run_async(
            build_rich_checkpoint(
                original_request=original,
                working_dir=None,
                trigger=trigger,
                turn_count=turn_count,
                heal_attempt=heal_attempt,
                agent_conclusion="",
                completed_steps=completed,
                pending_steps=pending,
                active_file=None,
                pipeline_run_id=None,
                pipeline_stage=None,
            )
        )
        prompt = cp.to_continuation_prompt()

        # Byte-for-byte equal to the minimal baseline (same capped original_request).
        expected_or = original[:500] if original else ""
        baseline = TaskCheckpoint(original_request=expected_or).to_continuation_prompt()
        assert prompt == baseline

        # No enrichment line is introduced beyond what the request text contributes.
        for marker in _ENRICHMENT_MARKERS:
            assert prompt.count(marker) == baseline.count(marker)


# ─── Property 3: total robustness — never raise ──────────────────────────────


class TestProperty3TotalRobustness:
    """Property 3 — `build_rich_checkpoint` never raises across the input domain.

    Random original requests (empty / very long / unicode), random triggers
    (including unknown strings), random conclusions (empty / long / unicode),
    None vs [] vs populated progress, and an optional active file / pipeline id —
    the builder always returns a usable `TaskCheckpoint` and never propagates an
    exception into the streaming loop. The capped `original_request` (<=500) is
    asserted as a bounded-capture invariant.

    Validates: Requirements 2.6
    """

    @settings(max_examples=120, deadline=None, suppress_health_check=_SUPPRESS)
    @given(
        original=st.one_of(
            st.just(""),
            st.text(max_size=2000),
            st.text(min_size=600, max_size=1200),  # exceeds the 500-char cap
        ),
        trigger=st.one_of(st.just(""), st.sampled_from(_TRIGGERS), st.text(max_size=30)),
        conclusion=st.one_of(st.just(""), st.text(max_size=6000)),
        completed=st.one_of(st.none(), st.just([]), _step_list),
        pending=st.one_of(st.none(), st.just([]), _step_list),
        active=st.one_of(st.none(), st.text(max_size=120)),
        pipeline_id=_pipeline_id,
        turn_count=st.integers(min_value=0, max_value=10**9),
        heal_attempt=st.integers(min_value=0, max_value=10),
    )
    def test_never_raises_working_dir_none(
        self, original, trigger, conclusion, completed, pending, active,
        pipeline_id, turn_count, heal_attempt,
    ):
        cp = _run_async(
            build_rich_checkpoint(
                original_request=original,
                working_dir=None,
                trigger=trigger,
                turn_count=turn_count,
                heal_attempt=heal_attempt,
                agent_conclusion=conclusion,
                completed_steps=completed,
                pending_steps=pending,
                active_file=active,
                pipeline_run_id=pipeline_id,
                pipeline_stage="BUILD" if pipeline_id else None,
            )
        )
        assert isinstance(cp, TaskCheckpoint)
        # original_request is always capped at 500 chars (bounded capture).
        assert len(cp.original_request) <= 500
        # to_continuation_prompt() itself must never raise on any populated state.
        assert isinstance(cp.to_continuation_prompt(), str)

    @settings(max_examples=20, deadline=None, suppress_health_check=_SUPPRESS)
    @given(
        original=st.text(max_size=300),
        trigger=st.sampled_from(_TRIGGERS),
        conclusion=st.text(max_size=500),
        progress=st.one_of(st.none(), st.just([]), _step_list),
    )
    def test_never_raises_non_git_tempdir(self, original, trigger, conclusion, progress):
        """A non-git working_dir → git commands fail → graceful empty floor, no raise."""
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            cp = _run_async(
                build_rich_checkpoint(
                    original_request=original,
                    working_dir=d,
                    trigger=trigger,
                    agent_conclusion=conclusion,
                    completed_steps=progress,
                    pending_steps=progress,
                )
            )
        assert isinstance(cp, TaskCheckpoint)
        # Non-git dir → git floor falls back to empty (preserves 3.3 contract).
        assert cp.files_modified == []
        assert cp.uncommitted_changes == ""


class TestProperty3WrapupCaptureBounded:
    """Property 3 — the wrap-up conclusion buffer is length-bounded (`_WRAPUP_CAP`).

    `SessionUnit._capture_wrapup_text` accumulates assistant text emitted during the
    graceful wrap-up turn into the instance-scoped `_wrapup_conclusion`, capped at
    `_WRAPUP_CAP` chars, and never raises on malformed events. Exercised directly on
    a bare instance (`__new__`) — no DB, no subprocess, no event loop.
    """

    @staticmethod
    def _new_unit():
        from core.session_unit import SessionUnit

        u = SessionUnit.__new__(SessionUnit)
        u._wrapup_conclusion = ""
        return u

    @settings(max_examples=60, deadline=None, suppress_health_check=_SUPPRESS)
    @given(text=st.text(max_size=10_000))
    def test_single_event_capture_is_bounded(self, text):
        from core.session_unit import SessionUnit

        u = self._new_unit()
        event = {"type": "assistant", "content": [{"type": "text", "text": text}]}
        SessionUnit._capture_wrapup_text(u, event)
        assert len(u._wrapup_conclusion) <= SessionUnit._WRAPUP_CAP

    def test_capture_caps_across_many_events(self):
        from core.session_unit import SessionUnit

        u = self._new_unit()
        big = {"type": "assistant", "content": [{"type": "text", "text": "x" * 5000}]}
        for _ in range(10):
            SessionUnit._capture_wrapup_text(u, big)
        # Never exceeds the cap no matter how much text streams in.
        assert len(u._wrapup_conclusion) == SessionUnit._WRAPUP_CAP

    def test_capture_never_raises_on_malformed_events(self):
        from core.session_unit import SessionUnit

        u = self._new_unit()
        malformed = (
            None,
            "not-a-dict",
            {},
            {"type": "assistant"},
            {"type": "assistant", "content": None},
            {"type": "tool_use", "content": [{"type": "text", "text": "ignored"}]},
            {"type": "assistant", "content": [{"type": "text"}]},
            {"type": "assistant", "content": [{"no_type": "x"}]},
        )
        for ev in malformed:
            SessionUnit._capture_wrapup_text(u, ev)  # must not raise
        # Only the (absent) assistant-text blocks would have contributed: stays bounded.
        assert len(u._wrapup_conclusion) <= SessionUnit._WRAPUP_CAP


class TestProperty3EnrichmentHelperNeverRaises:
    """Property 3 — `_derive_heal_enrichment` returns {} (never raises) with no app id.

    The design's never-raise guarantee for the enrichment helper, exercised without a
    real DB: when `_app_session_id` is missing or None the helper short-circuits to {}
    before any DB import, so it can never raise into the heal/streaming path (2.6).
    """

    def test_returns_empty_when_app_session_id_attr_missing(self):
        from core.session_unit import SessionUnit

        u = SessionUnit.__new__(SessionUnit)  # no _app_session_id set
        assert asyncio.run(SessionUnit._derive_heal_enrichment(u)) == {}

    def test_returns_empty_when_app_session_id_is_none(self):
        from core.session_unit import SessionUnit

        u = SessionUnit.__new__(SessionUnit)
        u._app_session_id = None
        assert asyncio.run(SessionUnit._derive_heal_enrichment(u)) == {}
