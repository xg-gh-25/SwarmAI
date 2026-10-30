"""Tests for compaction tuning (run_4f312c3c, XG directive 2026-10-14):

1. the autocompact trigger is DERIVED from the model context window
   (0.75 × window desktop, 400K channel) and reaches the CLI as the
   `autoCompactWindow` setting (window = trigger + measured 33K CLI buffer).
   task_budget — which never moved the threshold — is no longer sent. A named
   floor guards a hypothetical tiny-window model.
2. post_compact_injection enriches the continuation context with REAL
   session_context signal (recent activity / test status / commits /
   corrections / active run), fire-once preserved, and NEVER pollutes R19
   language classification (it rides the separate additionalContext channel).

Methodology: unit tests. The trigger helpers are pure functions of the window;
build_options is driven for real with a mocked config manager. The injection hook is awaited directly with
a hand-built session_context dict (the system boundary is the SDK hook input, a
plain dict — nothing external to mock).
"""

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Change 1 — autocompact trigger via the CLI `autoCompactWindow` setting
# ---------------------------------------------------------------------------
# Measured 2026-10-17 (get_context_usage on claude-opus-5-5[1m]): CLI threshold =
# autoCompactWindow - 33K; task_budget does NOT move it (967K either way). So the
# trigger must ride `settings`, and task_budget must no longer be sent (it only
# tells the model a budget to "wrap up" against).

class TestAutocompactTrigger:
    def test_desktop_trigger_is_075_of_window_1M(self):
        from core.prompt_builder import (
            AUTOCOMPACT_TRIGGER_FRACTION,
            _compute_desktop_autocompact_trigger,
        )
        assert AUTOCOMPACT_TRIGGER_FRACTION == 0.75
        assert _compute_desktop_autocompact_trigger(1_000_000) == 750_000

    def test_floor_guards_tiny_window(self):
        from core.prompt_builder import (
            AUTOCOMPACT_TRIGGER_FLOOR,
            _compute_desktop_autocompact_trigger,
        )
        assert _compute_desktop_autocompact_trigger(10_000) == AUTOCOMPACT_TRIGGER_FLOOR
        assert AUTOCOMPACT_TRIGGER_FLOOR >= 128_000

    def test_default_window_gives_workable_trigger(self):
        from core.prompt_builder import _compute_desktop_autocompact_trigger
        assert _compute_desktop_autocompact_trigger(200_000) == 150_000
        assert isinstance(_compute_desktop_autocompact_trigger(1_000_000), int)

    def test_window_setting_adds_measured_cli_buffer(self):
        """window = trigger + 33K so the CLI's (window - 33K) lands ON the trigger."""
        from core.prompt_builder import (
            AUTOCOMPACT_CLI_BUFFER,
            _compute_autocompact_window_setting,
        )
        assert AUTOCOMPACT_CLI_BUFFER == 33_000
        assert _compute_autocompact_window_setting(750_000, 1_000_000) == 783_000
        # never above the model window
        assert _compute_autocompact_window_setting(990_000, 1_000_000) == 1_000_000

    def test_source_no_longer_calls_it_a_cost_cap(self):
        src = (Path(__file__).resolve().parents[1] / "core" / "prompt_builder.py").read_text()
        assert "independent cost cap" not in src


async def _options(model="claude-opus-5-5", channel_context=None):
    from core.prompt_builder import PromptBuilder
    cfg = MagicMock()
    values = {"thinking_mode": "adaptive", "thinking_effort": "high"}
    cfg.get = MagicMock(side_effect=lambda k, d=None: values.get(k, d))
    cfg.get_model = MagicMock(return_value=model)
    pb = PromptBuilder(cfg)
    return await pb.build_options(
        agent_config={"model": model},
        enable_skills=False, enable_mcp=False, resume_session_id=None,
        session_context={"sdk_session_id": "sid"}, channel_context=channel_context,
        editor_context=None, terminal_context=None,
        cached_system_prompt="CACHED",
    )


class TestBuildOptionsAutocompact:
    """Drive the real build_options: the trigger reaches the CLI as a setting."""

    @pytest.mark.asyncio
    async def test_desktop_1m_model_sets_window_783k(self):
        import json
        opts = await _options()
        settings = json.loads(opts.settings)
        assert settings["autoCompactWindow"] == 783_000  # → CLI threshold 750K
        assert "skillOverrides" in settings  # existing key preserved
        # the skill-listing description cap rides the same --settings (core/skill_listing)
        from core.skill_listing import SKILL_DESC_CAP
        assert settings["skillListingMaxDescChars"] == SKILL_DESC_CAP

    @pytest.mark.asyncio
    async def test_summary_instructions_hook_reaches_the_settings(self):
        """The PreCompact command hook (core/compact_summary.py) rides in the same
        --settings JSON; on Windows it is intentionally absent."""
        import json
        import sys
        from core.compact_summary import precompact_settings_hooks
        settings = json.loads((await _options()).settings)
        if sys.platform == "win32":
            assert "hooks" not in settings
        else:
            assert settings["hooks"] == precompact_settings_hooks()

    @pytest.mark.asyncio
    async def test_task_budget_no_longer_sent(self):
        opts = await _options()
        assert opts.task_budget is None

    @pytest.mark.asyncio
    async def test_channel_triggers_at_400k(self):
        import json
        opts = await _options(channel_context={"channel_id": "c1", "channel_type": "slack"})
        assert json.loads(opts.settings)["autoCompactWindow"] == 433_000  # → 400K


class TestBuildOptionsTurnCeiling:
    """The CLI's per-message turn ceiling reaches the CLI as ``max_turns``.

    Desktop is a runaway guard only (autocompact keeps a long message in the
    window): one productive message ran 501 model calls in 2 hours and was cut
    off at the old 500. Channel stays at the unattended 100.
    """

    @pytest.mark.asyncio
    async def test_desktop_ceiling_is_5000(self):
        opts = await _options()
        assert opts.max_turns == 5000

    @pytest.mark.asyncio
    async def test_channel_ceiling_stays_100(self):
        opts = await _options(channel_context={"channel_id": "c1", "channel_type": "slack"})
        assert opts.max_turns == 100


# ---------------------------------------------------------------------------
# Change 2 — enriched post_compact_injection
# ---------------------------------------------------------------------------

class _FakeObs:
    def __init__(self, tool_name, intent="", result_status="success"):
        self.tool_name = tool_name
        self.intent = intent
        self.result_status = result_status


class _FakeRing:
    def __init__(self, obs):
        self._obs = obs

    def snapshot(self, last_n=10):
        return self._obs[-last_n:]

    def all_completed(self):
        return self._obs


def _ctx(**overrides):
    base = {"_compacted": True, "sdk_session_id": "sess-1"}
    base.update(overrides)
    return base


async def _invoke(ctx):
    from core.runtime_hooks import create_post_compact_injection
    hook = create_post_compact_injection(ctx)
    return await hook({"prompt": "继续"}, "tu-1", MagicMock())


class TestPostCompactEnrichment:
    @pytest.mark.asyncio
    async def test_no_op_when_not_compacted(self):
        """Fire-once gate: no injection unless _compacted is set."""
        result = await _invoke(_ctx(_compacted=False))
        assert result == {}

    @pytest.mark.asyncio
    async def test_fire_once_resets_flag(self):
        """After injecting, _compacted resets to False (fire-once)."""
        ctx = _ctx(_files_touched={"a.py"})
        await _invoke(ctx)
        assert ctx["_compacted"] is False
        # second call → no injection
        second = await _invoke(ctx)
        assert second == {}

    @pytest.mark.asyncio
    async def test_files_touched_still_injected(self):
        """The existing files-touched line is preserved."""
        ctx = _ctx(_files_touched={"backend/core/foo.py", "backend/core/bar.py"})
        result = await _invoke(ctx)
        text = result["hookSpecificOutput"]["additionalContext"]
        assert "foo.py" in text

    @pytest.mark.asyncio
    async def test_enriched_with_recent_activity(self):
        """Dominant-tool / recent activity from the _observations ring appears."""
        ring = _FakeRing([_FakeObs("Bash", "pytest tests/"), _FakeObs("Bash", "grep"), _FakeObs("Read", "read foo")])
        ctx = _ctx(_observations=ring)
        text = (await _invoke(ctx))["hookSpecificOutput"]["additionalContext"]
        assert "Bash" in text  # dominant tool surfaced

    @pytest.mark.asyncio
    async def test_enriched_with_test_status(self):
        """_ran_tests signal surfaces a test-status line."""
        ctx = _ctx(_ran_tests=True)
        text = (await _invoke(ctx))["hookSpecificOutput"]["additionalContext"]
        assert "test" in text.lower()

    @pytest.mark.asyncio
    async def test_enriched_with_active_run(self):
        """_active_pipeline_run_id surfaces so a compacted pipeline keeps its thread."""
        ctx = _ctx(_active_pipeline_run_id="run_abc123")
        text = (await _invoke(ctx))["hookSpecificOutput"]["additionalContext"]
        assert "run_abc123" in text

    @pytest.mark.asyncio
    async def test_bare_session_only_base_line(self):
        """No signal → only the base compaction line, no fabricated enrichment."""
        ctx = _ctx()  # no files, no ring, no tests, no run
        result = await _invoke(ctx)
        text = result["hookSpecificOutput"]["additionalContext"]
        assert "compact" in text.lower()
        # no invented activity/test/run lines
        assert "run_" not in text


class TestR19NotPolluted:
    """The enriched injection rides additionalContext — it must never enter the
    string _classify_message_language sees (which is only the user prompt)."""

    def test_classifier_reads_only_user_prompt(self):
        from core.runtime_hooks import _classify_message_language
        # A Chinese user prompt stays zh regardless of any English injection text
        # (the injection is a SEPARATE channel, never concatenated into this arg).
        assert _classify_message_language("帮我看看这个问题") == "zh"

    def test_english_injection_text_is_not_the_classified_input(self):
        """Even if the enriched block is long English prose, it is returned via
        hookSpecificOutput.additionalContext, not fed to the classifier."""
        from core.runtime_hooks import _classify_message_language
        # classifier judges THIS string only — a zh prompt is zh.
        assert _classify_message_language("继续做压缩这个功能") == "zh"
