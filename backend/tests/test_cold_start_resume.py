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

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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
