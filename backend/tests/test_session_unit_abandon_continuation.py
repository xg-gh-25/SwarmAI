"""Tests for Resume-Fallback Context Preservation bugfix.

Validates that when _retry_with_resume abandons --resume after 2 consecutive
timeouts, an enriched conversation continuation is prepended to query_content
exactly once, preserving context for the respawned subprocess.

Properties tested:
- Property 1: Bug condition → continuation injected
- Property 2: Non-bug inputs → behavior unchanged
- Property 3: Idempotent injection (at most once)
- Property 4: Graceful degradation on failure
- Property 5: Session identity preserved (app_session_id only)
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_unit(session_id: str = "test-session-1") -> SessionUnit:
    """Create a minimal SessionUnit for testing."""
    unit = SessionUnit(session_id=session_id, agent_id="agent-1")
    unit._app_session_id = "app-session-123"
    unit._model_name = "claude-opus-4-8"
    unit._sdk_session_id = "sdk-session-456"
    return unit


def _make_options_mock():
    """Create a mock options that survives _build_retry_options(vars())."""
    opts = MagicMock()
    # _build_retry_options does vars(options), so we need a proper __dict__
    opts.__dict__ = {"model": "claude-opus-4-8", "resume": None}
    return opts


# ---------------------------------------------------------------------------
# Tests for _inject_abandon_continuation
# ---------------------------------------------------------------------------


class TestInjectAbandonContinuation:
    """Unit tests for the _inject_abandon_continuation helper."""

    @pytest.mark.asyncio
    async def test_string_query_gets_continuation_prepended(self):
        """AC1: String query_content gets continuation prepended with separator."""
        unit = _make_unit()
        fake_context = "## Recent Conversation\nUser asked about X\nAgent replied Y"

        with patch(
            "core.context_injector.build_resume_context",
            new_callable=AsyncMock,
            return_value=fake_context,
        ):
            result, injected = await unit._inject_abandon_continuation(
                "What is the weather?"
            )

        assert injected is True
        assert result.startswith(fake_context)
        assert "\n\n---\n\n" in result
        assert result.endswith("What is the weather?")

    @pytest.mark.asyncio
    async def test_multimodal_query_gets_text_block_prepended(self):
        """AC1: Multimodal list query gets a text block prepended."""
        unit = _make_unit()
        fake_context = "## Continuation context"
        original_blocks = [
            {"type": "text", "text": "Hello"},
            {"type": "image", "source": {"data": "base64..."}},
        ]

        with patch(
            "core.context_injector.build_resume_context",
            new_callable=AsyncMock,
            return_value=fake_context,
        ):
            result, injected = await unit._inject_abandon_continuation(
                list(original_blocks)  # copy to avoid mutation check issues
            )

        assert injected is True
        assert isinstance(result, list)
        assert len(result) == 3  # continuation + 2 original
        assert result[0] == {"type": "text", "text": fake_context}
        assert result[1:] == original_blocks

    @pytest.mark.asyncio
    async def test_empty_context_returns_false(self):
        """AC3/3.5: Empty build_resume_context → no injection, blank respawn."""
        unit = _make_unit()

        with patch(
            "core.context_injector.build_resume_context",
            new_callable=AsyncMock,
            return_value="",
        ):
            result, injected = await unit._inject_abandon_continuation(
                "original message"
            )

        assert injected is False
        assert result == "original message"

    @pytest.mark.asyncio
    async def test_none_context_returns_false(self):
        """AC3: None/falsy from build_resume_context → no injection.

        Note: build_resume_context normally returns "" not None, but the
        guard handles both via `if not continuation or not continuation.strip()`.
        This test validates defensive behavior against unexpected None.
        """
        unit = _make_unit()

        with patch(
            "core.context_injector.build_resume_context",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result, injected = await unit._inject_abandon_continuation(
                "original message"
            )

        assert injected is False
        assert result == "original message"

    @pytest.mark.asyncio
    async def test_exception_returns_false_gracefully(self):
        """AC3/Property 4: Exception in build_resume_context → graceful degradation."""
        unit = _make_unit()

        with patch(
            "core.context_injector.build_resume_context",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB locked"),
        ):
            result, injected = await unit._inject_abandon_continuation(
                "original message"
            )

        assert injected is False
        assert result == "original message"

    @pytest.mark.asyncio
    async def test_timeout_returns_false_gracefully(self):
        """AC5/Property 4: Timeout in build_resume_context → graceful degradation."""
        unit = _make_unit()

        async def slow_build(*args, **kwargs):
            await asyncio.sleep(10)  # Will be cancelled by wait_for
            return "never reaches here"

        with patch(
            "core.context_injector.build_resume_context",
            side_effect=slow_build,
        ):
            result, injected = await unit._inject_abandon_continuation(
                "original message"
            )

        assert injected is False
        assert result == "original message"

    @pytest.mark.asyncio
    async def test_uses_app_session_id_not_sdk_id(self):
        """AC/Property 5: build_resume_context called with app_session_id."""
        unit = _make_unit()
        unit._app_session_id = "the-app-id"
        unit._sdk_session_id = "the-sdk-id"  # should NOT be used

        with patch(
            "core.context_injector.build_resume_context",
            new_callable=AsyncMock,
            return_value="context",
        ) as mock_build:
            await unit._inject_abandon_continuation("msg")

        mock_build.assert_called_once()
        call_args = mock_build.call_args
        # First positional arg should be app_session_id
        assert call_args[0][0] == "the-app-id"

    @pytest.mark.asyncio
    async def test_conservative_budget_cap(self):
        """AC6: Budget is capped at min(model_window * 0.1, 30000)."""
        unit = _make_unit()
        unit._model_name = "claude-opus-4-8"  # 1M window

        with patch(
            "core.context_injector.build_resume_context",
            new_callable=AsyncMock,
            return_value="ctx",
        ) as mock_build:
            with patch(
                "core.prompt_builder.PromptBuilder.get_model_context_window",
                return_value=1_000_000,
            ):
                await unit._inject_abandon_continuation("msg")

        # 1M * 0.1 = 100K, but capped at 30K
        call_kwargs = mock_build.call_args[1]
        assert call_kwargs["token_budget"] == 30_000

    @pytest.mark.asyncio
    async def test_small_model_uses_proportional_budget(self):
        """AC6: For a 200K model, budget = 200K * 0.1 = 20K (< 30K cap)."""
        unit = _make_unit()
        unit._model_name = "claude-sonnet-4-5"

        with patch(
            "core.context_injector.build_resume_context",
            new_callable=AsyncMock,
            return_value="ctx",
        ) as mock_build:
            with patch(
                "core.prompt_builder.PromptBuilder.get_model_context_window",
                return_value=200_000,
            ):
                await unit._inject_abandon_continuation("msg")

        call_kwargs = mock_build.call_args[1]
        assert call_kwargs["token_budget"] == 20_000


# ---------------------------------------------------------------------------
# Tests for double-injection guard and edge cases
# ---------------------------------------------------------------------------


class TestDoubleInjectionGuard:
    """Finding 1: Prevent double context injection (heal-checkpoint + abandon)."""

    @pytest.mark.asyncio
    async def test_already_enriched_string_skips_injection(self):
        """If query_content already has a heal-checkpoint separator, skip."""
        unit = _make_unit()
        # Simulate query_content that was already enriched by heal-checkpoint
        enriched_query = "heal checkpoint context\n\n---\n\noriginal user message"

        with patch(
            "core.context_injector.build_resume_context",
            new_callable=AsyncMock,
            return_value="abandon context that should NOT be added",
        ):
            result, injected = await unit._inject_abandon_continuation(enriched_query)

        assert injected is False
        assert result == enriched_query  # unchanged

    @pytest.mark.asyncio
    async def test_already_enriched_multimodal_skips_injection(self):
        """If multimodal query_content already has enrichment text block, skip."""
        unit = _make_unit()
        # Simulate multimodal already enriched by heal-checkpoint
        enriched_query = [
            {"type": "text", "text": "heal context\n\n---\n\nmore stuff"},
            {"type": "text", "text": "actual user message"},
        ]

        with patch(
            "core.context_injector.build_resume_context",
            new_callable=AsyncMock,
            return_value="abandon context",
        ):
            result, injected = await unit._inject_abandon_continuation(enriched_query)

        assert injected is False
        assert result == enriched_query

    @pytest.mark.asyncio
    async def test_fresh_string_query_still_gets_injected(self):
        """Normal string without separator still gets continuation."""
        unit = _make_unit()

        with patch(
            "core.context_injector.build_resume_context",
            new_callable=AsyncMock,
            return_value="fresh continuation",
        ):
            result, injected = await unit._inject_abandon_continuation(
                "plain user message without any separator"
            )

        assert injected is True
        assert "fresh continuation" in result
        assert "plain user message" in result

    @pytest.mark.asyncio
    async def test_whitespace_only_context_returns_false(self):
        """Finding 6: Whitespace-only build_resume_context → no injection."""
        unit = _make_unit()

        with patch(
            "core.context_injector.build_resume_context",
            new_callable=AsyncMock,
            return_value="   \n\n  \t  ",
        ):
            result, injected = await unit._inject_abandon_continuation("msg")

        assert injected is False
        assert result == "msg"


# ---------------------------------------------------------------------------
# Tests for abandon branch integration in _retry_with_resume
# ---------------------------------------------------------------------------


class TestAbandonBranchIntegration:
    """Integration tests for the abandon branch within _retry_with_resume."""

    def _resource_patches(self, unit):
        """Common patches for resource_monitor used inside _retry_with_resume."""
        budget_mock = MagicMock()
        budget_mock.can_spawn = True
        budget_mock.reason = ""
        return [
            patch("core.session_unit._oom_cooldown_until", 0),
            patch("core.session_unit._spawn_lock", asyncio.Lock()),
            patch(
                "core.resource_monitor.resource_monitor.compute_max_tabs",
                return_value=4,
            ),
            patch(
                "core.resource_monitor.resource_monitor.spawn_budget",
                return_value=budget_mock,
            ),
            # Avoid ClaudeAgentOptions construction from MagicMock
            patch.object(
                unit, "_build_retry_options",
                return_value=MagicMock(),
            ),
        ]

    @pytest.mark.asyncio
    async def test_abandon_injects_continuation_on_two_timeouts(self):
        """Property 1: Two consecutive timeouts with resume → continuation injected."""
        unit = _make_unit()
        unit.state = SessionState.STREAMING
        unit._retry_count = 0
        unit.MAX_RETRY_ATTEMPTS = 5
        unit.RETRY_BACKOFF_SECONDS = 0.01

        call_count = 0
        injected_query = None

        async def fake_stream(query, **kwargs):
            nonlocal call_count, injected_query
            call_count += 1
            if call_count <= 2:
                # First two: timeout errors
                raise Exception("Streaming timeout (init): no SDK response for 60s")
            # Third: capture the query_content and succeed
            injected_query = query
            yield {"type": "result", "result": "success"}

        fake_continuation = "## Resume Context\nPrior conversation summary"

        patches = self._resource_patches(unit) + [
            patch.object(unit._streaming_orchestrator, "_stream_response", side_effect=fake_stream),
            patch.object(unit, "_crash_to_cold_async", new_callable=AsyncMock),
            patch.object(unit, "_spawn", new_callable=AsyncMock),
            patch.object(unit, "_transition"),
            patch(
                "core.context_injector.build_resume_context",
                new_callable=AsyncMock,
                return_value=fake_continuation,
            ),
        ]
        with patch("asyncio.sleep", new_callable=AsyncMock):
            for p in patches:
                p.start()
            try:
                events = []
                async for event in unit._retry_with_resume(
                    "user message",
                    MagicMock(),  # options
                    None,  # config
                    "Streaming timeout (init): no SDK response for 60s",
                    "",
                ):
                    events.append(event)
            finally:
                for p in patches:
                    p.stop()

        # The third stream call should have received enriched query
        assert injected_query is not None
        assert fake_continuation in injected_query
        assert "user message" in injected_query

    @pytest.mark.asyncio
    async def test_no_injection_on_non_timeout_failure(self):
        """Property 2/3.3: Non-timeout failures don't trigger injection."""
        unit = _make_unit()
        unit.state = SessionState.STREAMING
        unit._retry_count = 0
        unit.MAX_RETRY_ATTEMPTS = 3
        unit.RETRY_BACKOFF_SECONDS = 0.01

        call_count = 0
        captured_queries = []

        async def fake_stream(query, **kwargs):
            nonlocal call_count
            call_count += 1
            captured_queries.append(query)
            if call_count == 1:
                # Non-timeout failure (OOM) — should NOT trigger abandon
                raise Exception("Command failed with exit code -9 (exit code: -9)")
            # Second call succeeds
            yield {"type": "result", "result": "success"}

        mock_build = AsyncMock(return_value="should not appear")

        patches = self._resource_patches(unit) + [
            patch.object(unit._streaming_orchestrator, "_stream_response", side_effect=fake_stream),
            patch.object(unit, "_crash_to_cold_async", new_callable=AsyncMock),
            patch.object(unit, "_spawn", new_callable=AsyncMock),
            patch.object(unit, "_transition"),
            patch("core.context_injector.build_resume_context", mock_build),
        ]
        with patch("asyncio.sleep", new_callable=AsyncMock):
            for p in patches:
                p.start()
            try:
                events = []
                async for event in unit._retry_with_resume(
                    "original message",
                    MagicMock(),
                    None,
                    # Initial error is OOM (not timeout) — consecutive_timeouts stays 0
                    "Command failed with exit code -9 (exit code: -9)",
                    "",
                ):
                    events.append(event)
            finally:
                for p in patches:
                    p.stop()

        # build_resume_context never called (no abandon triggered)
        mock_build.assert_not_called()
        # Stream should get the ORIGINAL message (no injection)
        assert len(captured_queries) >= 1
        assert captured_queries[-1] == "original message"

    @pytest.mark.asyncio
    async def test_no_injection_without_app_session_id(self):
        """Property 2/3.5: No app_session_id → no injection attempt."""
        unit = _make_unit()
        unit._app_session_id = None  # No app session ID
        unit.state = SessionState.STREAMING
        unit._retry_count = 0
        unit.MAX_RETRY_ATTEMPTS = 5
        unit.RETRY_BACKOFF_SECONDS = 0.01

        call_count = 0

        async def fake_stream(query, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise Exception("Streaming timeout (init): no SDK response for 60s")
            yield {"type": "result", "result": "success"}

        mock_build = AsyncMock(return_value="should not be called")

        patches = self._resource_patches(unit) + [
            patch.object(unit._streaming_orchestrator, "_stream_response", side_effect=fake_stream),
            patch.object(unit, "_crash_to_cold_async", new_callable=AsyncMock),
            patch.object(unit, "_spawn", new_callable=AsyncMock),
            patch.object(unit, "_transition"),
            patch("core.context_injector.build_resume_context", mock_build),
        ]
        with patch("asyncio.sleep", new_callable=AsyncMock):
            for p in patches:
                p.start()
            try:
                events = []
                async for event in unit._retry_with_resume(
                    "msg",
                    MagicMock(),
                    None,
                    "Streaming timeout (init): no SDK response for 60s",
                    "",
                ):
                    events.append(event)
            finally:
                for p in patches:
                    p.stop()

        # build_resume_context should never be called
        mock_build.assert_not_called()


class TestAbandonIdempotency:
    """Property 3: Continuation injected at most once across multiple iterations."""

    @pytest.mark.asyncio
    async def test_multiple_abandons_inject_only_once(self):
        """Even if loop iterates 3+ times after abandon, inject once."""
        unit = _make_unit()
        unit.state = SessionState.STREAMING
        unit._retry_count = 0
        unit.MAX_RETRY_ATTEMPTS = 6
        unit.RETRY_BACKOFF_SECONDS = 0.01

        call_count = 0
        captured_queries = []

        async def fake_stream(query, **kwargs):
            nonlocal call_count
            call_count += 1
            captured_queries.append(query)
            if call_count <= 4:
                raise Exception("Streaming timeout (init): no SDK response for 60s")
            yield {"type": "result", "result": "success"}

        budget_mock = MagicMock()
        budget_mock.can_spawn = True
        budget_mock.reason = ""

        mock_build = AsyncMock(return_value="INJECTED_CONTEXT")

        patches = [
            patch.object(unit._streaming_orchestrator, "_stream_response", side_effect=fake_stream),
            patch.object(unit, "_crash_to_cold_async", new_callable=AsyncMock),
            patch.object(unit, "_spawn", new_callable=AsyncMock),
            patch.object(unit, "_transition"),
            patch.object(unit, "_build_retry_options", return_value=MagicMock()),
            patch("core.session_unit._oom_cooldown_until", 0),
            patch("core.session_unit._spawn_lock", asyncio.Lock()),
            patch(
                "core.resource_monitor.resource_monitor.compute_max_tabs",
                return_value=4,
            ),
            patch(
                "core.resource_monitor.resource_monitor.spawn_budget",
                return_value=budget_mock,
            ),
            patch("core.context_injector.build_resume_context", mock_build),
        ]
        with patch("asyncio.sleep", new_callable=AsyncMock):
            for p in patches:
                p.start()
            try:
                events = []
                async for event in unit._retry_with_resume(
                    "user msg",
                    MagicMock(),
                    None,
                    "Streaming timeout (init): no SDK response for 60s",
                    "",
                ):
                    events.append(event)
            finally:
                for p in patches:
                    p.stop()

        # build_resume_context called exactly once (on first abandon)
        assert mock_build.call_count == 1

        # Count how many queries contain the injected context
        injected_count = sum(
            1 for q in captured_queries
            if isinstance(q, str) and "INJECTED_CONTEXT" in q
        )
        # Should appear in all queries AFTER the injection point (once injected,
        # query_content is modified for all subsequent iterations)
        assert injected_count >= 1
        # But build_resume_context itself was only called once
        assert mock_build.call_count == 1


class TestAbandonContinuationHeaderStranglerGated:
    """Gate-2 MED (run_d108b914): the resume-provenance header wrap on the abandon
    continuation is strangler-gated by SWARM_RESUME_VIA_QUERY. The abandon path
    fires on retry timeouts INDEPENDENTLY of the flag, so wrapping it unconditionally
    would change prod behavior while the flag is OFF — breaking the "flag OFF =
    byte-identical prod" invariant. Assert: OFF → no header (legacy); ON → header."""

    @pytest.mark.asyncio
    async def test_flag_off_no_provenance_header(self, monkeypatch):
        monkeypatch.delenv("SWARM_RESUME_VIA_QUERY", raising=False)
        unit = _make_unit()
        fake_context = "## Recent Conversation\nprior stuff"
        with patch(
            "core.context_injector.build_resume_context",
            new_callable=AsyncMock, return_value=fake_context,
        ):
            result, injected = await unit._inject_abandon_continuation("now what?")
        assert injected is True
        assert "RESUMED CONVERSATION HISTORY" not in result, (
            "flag OFF must NOT add the provenance header (byte-identical prod)"
        )
        assert result.startswith(fake_context), "legacy shape preserved"

    @pytest.mark.asyncio
    async def test_flag_on_adds_provenance_header(self, monkeypatch):
        monkeypatch.setenv("SWARM_RESUME_VIA_QUERY", "true")
        unit = _make_unit()
        fake_context = "## Recent Conversation\nprior stuff"
        with patch(
            "core.context_injector.build_resume_context",
            new_callable=AsyncMock, return_value=fake_context,
        ):
            result, injected = await unit._inject_abandon_continuation("now what?")
        assert injected is True
        assert "RESUMED CONVERSATION HISTORY" in result, (
            "flag ON unifies the abandon continuation under the resume provenance header"
        )
        assert fake_context in result
