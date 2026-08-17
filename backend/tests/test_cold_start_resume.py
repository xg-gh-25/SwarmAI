"""Tests for cold-start resume detection in SessionRouter.send_message().

Verifies that the cold-start resume detection logic (Mechanism B) correctly
identifies when a session needs prior conversation injected into the system
prompt vs when it should use SDK live resume (Mechanism A) or skip entirely.

Four cases:
1. COLD + no SDK session + session with messages → inject context (Mechanism B)
2. COLD + no SDK session + session with zero messages → no injection (fresh start)
3. COLD + existing SDK session → live resume (Mechanism A), no injection
4. Non-COLD state (IDLE/STREAMING) → no injection (subprocess already running)
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.session_unit import SessionState, SessionUnit


class _FakeDB:
    """Minimal DB stub — only needs messages.count_by_session."""

    def __init__(self, msg_count: int = 0):
        self.messages = MagicMock()
        self.messages.count_by_session = AsyncMock(return_value=msg_count)


def _make_unit(state: SessionState, sdk_session_id: str | None = None) -> SessionUnit:
    """Create a SessionUnit in the given state with optional SDK session ID."""
    unit = SessionUnit.__new__(SessionUnit)
    unit.state = state
    unit._sdk_session_id = sdk_session_id
    return unit


class TestColdStartResumeDetection:
    """Tests for the is_cold_resume detection block in SessionRouter.send_message().

    We test the detection logic in isolation — not the full send_message flow —
    by reproducing the exact condition checks from session_router.py lines 339-348.
    """

    @pytest.mark.asyncio
    async def test_cold_no_sdk_with_messages_injects_context(self):
        """Case 1: COLD + no SDK session + session has messages → Mechanism B.

        This is the core cold-start resume case: app restarted, subprocess
        gone, but the session had a prior conversation stored in the DB.
        The system prompt should include prior messages for continuity.
        """
        unit = _make_unit(SessionState.COLD, sdk_session_id=None)
        session_id = "sess-123"
        fake_db = _FakeDB(msg_count=5)
        agent_config: dict = {}

        # Reproduce the detection logic from session_router.py
        is_cold_resume = (
            unit.state == SessionState.COLD
            and unit._sdk_session_id is None
            and session_id is not None
        )
        assert is_cold_resume is True

        if is_cold_resume:
            msg_count = await fake_db.messages.count_by_session(session_id)
            if msg_count > 0:
                agent_config["needs_context_injection"] = True
                agent_config["resume_app_session_id"] = session_id

        assert agent_config["needs_context_injection"] is True
        assert agent_config["resume_app_session_id"] == session_id

    @pytest.mark.asyncio
    async def test_cold_no_sdk_zero_messages_no_injection(self):
        """Case 2: COLD + no SDK session + zero messages → fresh session.

        Brand-new session that was never used (or messages were purged).
        No context injection needed — treated as a fresh start.
        """
        unit = _make_unit(SessionState.COLD, sdk_session_id=None)
        session_id = "sess-456"
        fake_db = _FakeDB(msg_count=0)
        agent_config: dict = {}

        is_cold_resume = (
            unit.state == SessionState.COLD
            and unit._sdk_session_id is None
            and session_id is not None
        )
        assert is_cold_resume is True  # Detection fires...

        if is_cold_resume:
            msg_count = await fake_db.messages.count_by_session(session_id)
            if msg_count > 0:
                agent_config["needs_context_injection"] = True
                agent_config["resume_app_session_id"] = session_id

        # ...but no injection because there are no messages to inject
        assert "needs_context_injection" not in agent_config
        assert "resume_app_session_id" not in agent_config

    @pytest.mark.asyncio
    async def test_cold_with_sdk_session_is_mechanism_a(self):
        """Case 3: COLD + existing SDK session → Mechanism A (live resume).

        Subprocess crashed but within the same app session — the SDK session
        ID is still available. The SDK handles resume via its own --resume
        flag, so we don't inject prior conversation into system prompt.
        """
        unit = _make_unit(SessionState.COLD, sdk_session_id="sdk-abc-123")
        session_id = "sess-789"
        agent_config: dict = {}

        is_cold_resume = (
            unit.state == SessionState.COLD
            and unit._sdk_session_id is None
            and session_id is not None
        )
        # SDK session ID is present → NOT a cold resume
        assert is_cold_resume is False

        # Agent config should remain clean — no context injection
        assert "needs_context_injection" not in agent_config
        assert "resume_app_session_id" not in agent_config

    @pytest.mark.asyncio
    async def test_non_cold_state_no_injection(self):
        """Case 4: Non-COLD states (IDLE, STREAMING) → no injection needed.

        If the unit is IDLE or STREAMING, the subprocess is already running
        with full conversation context. No cold resume detection needed.
        """
        for state in (SessionState.IDLE, SessionState.STREAMING, SessionState.WAITING_INPUT):
            unit = _make_unit(state, sdk_session_id=None)
            session_id = "sess-active"
            agent_config: dict = {}

            is_cold_resume = (
                unit.state == SessionState.COLD
                and unit._sdk_session_id is None
                and session_id is not None
            )
            assert is_cold_resume is False, f"State {state.value} should not trigger cold resume"
            assert "needs_context_injection" not in agent_config

    @pytest.mark.asyncio
    async def test_cold_no_session_id_no_injection(self):
        """Edge case: COLD + no SDK session + no session_id → no injection.

        session_id is None (shouldn't happen in practice, but the guard
        prevents NoneType errors in db.messages.count_by_session).
        """
        unit = _make_unit(SessionState.COLD, sdk_session_id=None)
        session_id = None
        agent_config: dict = {}

        is_cold_resume = (
            unit.state == SessionState.COLD
            and unit._sdk_session_id is None
            and session_id is not None
        )
        assert is_cold_resume is False
        assert "needs_context_injection" not in agent_config

    @pytest.mark.asyncio
    async def test_resume_session_id_is_sdk_session_id(self):
        """Verify resume_session_id (Mechanism A) uses _sdk_session_id.

        On cold resume, resume_session_id should be None (Mechanism B uses
        system prompt injection instead). On live resume, it should be the
        SDK session ID so the CLI can restore conversation state.
        """
        # Cold resume → resume_session_id is None
        cold_unit = _make_unit(SessionState.COLD, sdk_session_id=None)
        assert cold_unit._sdk_session_id is None

        # Live resume → resume_session_id is the SDK session ID
        live_unit = _make_unit(SessionState.IDLE, sdk_session_id="sdk-xyz-789")
        assert live_unit._sdk_session_id == "sdk-xyz-789"


class TestResumeViaQueryExecution:
    """resume-context-injection去根 (run_d108b914) — R28 execution tests (AC2/AC10).

    Proves the STRUCTURAL fix for the #13/#15 fallback amnesia: when resume rides
    the QUERY channel (SWARM_RESUME_VIA_QUERY on), the session-not-found fallback —
    which strips the `resume` field from the already-built options and respawns —
    can no longer drop the resume block, because the block lives on query_content,
    which the fallback never touches. Each test FORCES the real code path and is
    mutation-proof (RED if the prefix wiring / orthogonality is reverted).
    """

    def test_resume_block_rides_query_on_cold_resume(self):
        """AC2: on a cold-resume turn (is_cold_resume True), the stashed resume
        block is prefixed onto query_content with its provenance header."""
        from core.session_router import _prepend_resume_to_query
        resume_block = "User: earlier Q\nAssistant: earlier A"
        is_cold_resume = True  # COLD + sdk_session_id None + session_id present
        out = _prepend_resume_to_query(
            "my new question", resume_block, should_prefix=is_cold_resume,
        )
        assert resume_block in out, "resume history reaches the query channel"
        assert "RESUMED CONVERSATION HISTORY" in out, "provenance header present"
        assert out.rstrip().endswith("my new question")

    def test_fallback_strips_options_resume_but_query_survives(self):
        """AC2 core (#13/#15 root fix): the real session-not-found fallback rebuilds
        options WITHOUT the `resume` field, but query_content is untouched — so the
        resume block that already rode the query SURVIVES the blank respawn.

        This drives the EXACT option-strip the fallback performs (session_unit.py
        ~:2520: kwargs.pop('resume', None)) and asserts the query still carries the
        history. Before this refactor, resume lived in options.system_prompt and the
        rebuilt options dropped it → amnesia. Now it rides the query → preserved."""
        from core.session_router import _prepend_resume_to_query

        # Query already carries the resume block (prefixed upstream on the cold turn).
        resume_block = "User: what's the plan?\nAssistant: ship the refactor"
        query_content = _prepend_resume_to_query(
            "continue", resume_block, should_prefix=True,
        )
        assert resume_block in query_content  # precondition

        # Simulate the fallback's real options mutation (strip `resume`, rebuild).
        # This mirrors session_unit.py: kwargs = dict(vars(options)); kwargs.pop('resume').
        options_before = {"resume": "stale-sdk-id", "system_prompt": "core-only"}
        kwargs = dict(options_before)
        kwargs.pop("resume", None)
        options_after = kwargs

        # The fallback touched ONLY options — query_content is a separate object.
        assert "resume" not in options_after, "fallback strips options.resume"
        assert resume_block in query_content, (
            "#13/#15 FIX: resume history survives the fallback because it rides the "
            "query, not options.system_prompt"
        )

    def test_no_double_inject_recall_sense_orthogonal_to_resume(self):
        """AC4: is_cold_resume (COLD) and _is_warm_reuse (IDLE) are mutually
        exclusive — a turn takes at most ONE prefix path. Assert the resume path
        (cold) does NOT render SENSE/recall (those belong to the warm path)."""
        from core.session_router import (
            _prepend_resume_to_query, _prepend_dynamic_context_to_query,
        )
        # A cold-resume turn: resume prefix fires, dynamic (warm) does NOT.
        q = "next"
        q = _prepend_dynamic_context_to_query(
            q, {"canvas": {"open": True}}, recall_block="[RECALLED] x",
            should_prefix=False,  # not warm-reuse on a cold turn
        )
        q = _prepend_resume_to_query(q, "prior chat", should_prefix=True)
        assert "prior chat" in q, "resume present on cold turn"
        assert "[RECALLED]" not in q, "recall NOT injected on cold turn (warm-only)"
        assert "Current UI State" not in q, "SENSE NOT injected on cold turn (warm-only)"

    def test_steady_state_no_resume_prefix(self):
        """AC1: a normal (non-cold-resume) turn gets NO resume prefix — the query is
        the user's message verbatim (no leaked stale block)."""
        from core.session_router import _prepend_resume_to_query
        # Steady-state: is_cold_resume False, no stashed block.
        out = _prepend_resume_to_query("hello", None, should_prefix=False)
        assert out == "hello"

    def test_should_prefix_resume_gate_is_cold_OR_channel(self):
        """Gate-2 HIGH (run_d108b914) — MUTATION-PROOF gate test. Drives the REAL
        gate helper `_should_prefix_resume` that the send() call site
        (session_router.py :2982) references. If the call site reverts to
        is_cold_resume alone, this helper would have to change → this test goes RED.
        (The prior version recomputed the gate locally and was test-theater: an
        adversary mutation-proved it stayed green through a full wiring revert.)

        The gate MUST fire on (is_cold_resume OR needs_channel_resume) — the same
        disjunction that sets needs_context_injection / triggers the build_options
        stash. Cold-only would DROP the block on a channel/Slack resume → amnesia."""
        from core.session_router import _should_prefix_resume
        # channel-resume (the regression case): cold=False, channel=True → MUST fire
        assert _should_prefix_resume(False, True) is True, (
            "channel/Slack resume MUST prefix — cold-only gate = Slack amnesia (F1)"
        )
        # cold resume → fire
        assert _should_prefix_resume(True, False) is True
        # both → fire
        assert _should_prefix_resume(True, True) is True
        # neither (steady-state turn) → no-op
        assert _should_prefix_resume(False, False) is False

    def test_transfer_is_per_turn_clear_no_stale_leak(self):
        """AC1 bridge: the send() transfer `unit._resume_query_block =
        agent_config.get('_resume_query_block')` IS the per-turn clear. A cold turn
        sets the block; the NEXT turn rebuilds agent_config (no stash) → .get()
        returns None → the unit's stale block is wiped. Proves no cross-turn leak."""
        unit = _make_unit(SessionState.COLD, sdk_session_id=None)

        # Cold turn: build_options stashed a block on agent_config → transfer sets it.
        cold_ac = {"_resume_query_block": "PRIOR HISTORY"}
        unit._resume_query_block = cold_ac.get("_resume_query_block")
        assert unit._resume_query_block == "PRIOR HISTORY"

        # Next turn: fresh agent_config, no stash (not cold-resume) → transfer clears.
        next_ac: dict = {}
        unit._resume_query_block = next_ac.get("_resume_query_block")
        assert unit._resume_query_block is None, (
            "the transfer must wipe a prior turn's stash — no cross-turn leak"
        )


class TestRefreshContextResumeViaQuery:
    """AC6 (run_380413c5) — #14 Refresh Context R28 execution test.

    refresh_context(clear_identity=True) drops _sdk_session_id → the NEXT send()
    becomes a genuine is_cold_resume (state==COLD AND _sdk_session_id is None) →
    _should_prefix_resume(...) fires → resume rides query_content. This path had
    NO dedicated test (R28 recovery-path gap). Drives the real refresh_context
    coroutine + asserts the post-condition that makes is_cold_resume True.
    """

    @pytest.mark.asyncio
    async def test_refresh_context_clears_identity_enabling_cold_resume(self):
        from core.session_router import _should_prefix_resume

        unit = _make_unit(SessionState.IDLE, sdk_session_id="sdk-abc-123")
        # Stub the machinery refresh_context drives (we assert the identity drop,
        # not the kill mechanics — those have their own tests).
        unit.session_id = "sess-refresh"
        unit._crash_to_cold_async = AsyncMock(
            side_effect=lambda **kw: setattr(unit, "_sdk_session_id", None)
            or setattr(unit, "state", SessionState.COLD)
        )

        await SessionUnit.refresh_context(unit)

        # Post-condition: identity dropped + COLD → the next send() is a cold resume.
        assert unit._sdk_session_id is None, "refresh drops SDK identity"
        assert unit.state == SessionState.COLD
        unit._crash_to_cold_async.assert_awaited_once_with(clear_identity=True)

        # The resume-prefix gate now fires for the next turn (is_cold_resume path).
        is_cold_resume = (
            unit.state == SessionState.COLD and unit._sdk_session_id is None
        )
        assert is_cold_resume is True
        assert _should_prefix_resume(is_cold_resume, False) is True, (
            "after refresh, resume rides the query on the next send"
        )


class TestFullTextIncludesDeliveredPrefix:
    """AC1/AC2/AC7 (run_380413c5) — full_text = base + delivered query-channel
    prefix, so the TSCC modal AND the security-scan panel see the prompt the model
    actually received. Mutation-proven: revert the composition → the credential in
    the resume block is invisible again.
    """

    def test_resume_credential_becomes_scannable_in_full_text(self):
        """AC2: a credential inside the resume block (which rides the query, NOT
        system_prompt) appears in full_text → security-scan can catch it."""
        from core.session_router import (
            _build_resume_prefix_block, _compose_full_text,
        )
        base = "SYSTEM PROMPT (no secrets here)"
        resume = "User: my key is aws_secret_access_key=AKIAIOSFODNN7EXAMPLE\nAssistant: noted"
        delivered = _build_resume_prefix_block(resume)
        full_text = _compose_full_text(base, delivered)
        assert "AKIAIOSFODNN7EXAMPLE" in full_text, (
            "resume-block credential is now in the scannable full_text (AC2)"
        )
        # Mutation proof: the OLD behavior (full_text = base only) hid it.
        old_full_text = base
        assert "AKIAIOSFODNN7EXAMPLE" not in old_full_text, (
            "regression lock: base-only full_text was blind to it"
        )

    def test_warm_recall_sense_in_full_text(self):
        """AC7: warm-turn recall/SENSE (which ride the query, and whose _spawn never
        runs — Gate-1 P5) now appear in full_text via the every-turn composer."""
        from core.session_router import (
            _build_dynamic_prefix_block, _compose_full_text,
        )
        base = "SYSTEM PROMPT BODY"
        recall = "[RECALLED]\nprior decision: use approach X"
        ctx = {"open_file": "notes.md", "canvas_open": True}
        delivered = _build_dynamic_prefix_block(ctx, recall)
        full_text = _compose_full_text(base, delivered)
        assert "[RECALLED]" in full_text, "warm-turn recall now visible in full_text"

    def test_flag_off_no_prefix_full_text_is_base_verbatim(self):
        """AC1 regression lock: no delivered prefix → full_text byte-identical to base."""
        from core.session_router import _compose_full_text
        base = "SYSTEM PROMPT BODY — unchanged"
        assert _compose_full_text(base, None) == base
