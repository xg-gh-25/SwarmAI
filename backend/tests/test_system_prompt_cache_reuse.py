"""Regression tests for per-session system-prompt cache reuse (run_1dc710db).

BUG (retro run_864c23b4, point 1): run_conversation calls build_options →
build_system_prompt on EVERY user message, re-assembling the full ~85K system
prompt (build_session_briefing alone ~1.087s of 1.54s). But the system prompt of
an active chat-tab session is essentially constant for the session's life — it is
built once at first spawn and thereafter, on a warm reuse, DISCARDED (send() reuses
the live subprocess via client.query() with only the user message; options.system_prompt's
sole consumer is _spawn(), state==COLD). So re-building it every turn is waste.

FIX (the simple, robust one): cache the built system_prompt string on the unit at
first build; on subsequent build_options calls REUSE the cached string instead of
re-assembling — UNLESS this turn needs a fresh build (cold/channel resume, signalled
by agent_config['needs_context_injection']), which must inject prior conversation.

Why this is safe where the earlier "empty placeholder" design was NOT:
- The cache always holds a REAL, COMPLETE prompt (never an empty placeholder), so
  ANY spawn path (entry, mid-stream retry, recovery) that uses it gets a valid prompt
  — no degraded subprocess, no fragile empty-string signal.
- A resume turn (needs_context_injection) rebuilds fresh, so Mechanism B history
  injection is never served a stale cache.

METHODOLOGY: spy the REAL build_system_prompt; call the REAL build_options with and
without a cached prompt + with/without the resume flag; assert the spy call-count.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_builder():
    from core.prompt_builder import PromptBuilder

    cfg = MagicMock()
    cfg.get = MagicMock(side_effect=lambda k, d=None: d)
    cfg.get_model = MagicMock(return_value="claude-opus-4-8")
    return PromptBuilder(cfg)


async def _build(builder, *, agent_config, cached_system_prompt=None):
    return await builder.build_options(
        agent_config=agent_config,
        enable_skills=True,
        enable_mcp=False,
        resume_session_id=None,
        session_context={"sdk_session_id": "sid"},
        channel_context=None,
        editor_context=None,
        terminal_context=None,
        cached_system_prompt=cached_system_prompt,
    )


class TestSystemPromptCacheReuse:
    @pytest.mark.asyncio
    async def test_cached_prompt_reused_skips_assembly(self, monkeypatch):
        """AC1 (mutation-proof): a valid cached_system_prompt on a normal turn →
        build_system_prompt is NOT called; options.system_prompt == the cache."""
        builder = _make_builder()
        spy = AsyncMock(return_value="FRESH ASSEMBLY")
        monkeypatch.setattr(builder, "build_system_prompt", spy)

        options = await _build(
            builder,
            agent_config={"model": "claude-opus-4-8"},
            cached_system_prompt="CACHED PROMPT FROM FIRST SPAWN",
        )

        assert spy.await_count == 0, (
            "a valid cached system prompt must be REUSED (build_system_prompt skipped) "
            f"— but it was assembled {spy.await_count} time(s)"
        )
        assert options.system_prompt == "CACHED PROMPT FROM FIRST SPAWN", (
            "options must carry the cached prompt verbatim"
        )

    @pytest.mark.asyncio
    async def test_no_cache_builds_fresh(self, monkeypatch):
        """AC2: no cache (first spawn) → build_system_prompt IS called once."""
        builder = _make_builder()
        spy = AsyncMock(return_value="FRESH ASSEMBLY")
        monkeypatch.setattr(builder, "build_system_prompt", spy)

        options = await _build(
            builder, agent_config={"model": "claude-opus-4-8"}, cached_system_prompt=None
        )

        assert spy.await_count == 1, (
            f"first build (no cache) must assemble once — got {spy.await_count}"
        )
        assert options.system_prompt == "FRESH ASSEMBLY"

    @pytest.mark.asyncio
    async def test_resume_turn_rebuilds_even_with_cache(self, monkeypatch):
        """AC3 (死守): a resume turn (needs_context_injection=True) must REBUILD
        even if a cache exists — the fresh build injects prior conversation
        (Mechanism B). A stale cache would drop the resume history."""
        builder = _make_builder()
        spy = AsyncMock(return_value="FRESH ASSEMBLY WITH RESUME CONTEXT")
        monkeypatch.setattr(builder, "build_system_prompt", spy)

        options = await _build(
            builder,
            agent_config={"model": "claude-opus-4-8", "needs_context_injection": True},
            cached_system_prompt="STALE CACHED PROMPT (no resume history)",
        )

        assert spy.await_count == 1, (
            "a resume turn (needs_context_injection) MUST rebuild fresh even with a "
            f"cache — got {spy.await_count} (a stale cache would drop resume history)"
        )
        assert options.system_prompt != "STALE CACHED PROMPT (no resume history)", (
            "resume turn must NOT serve the stale cache"
        )
