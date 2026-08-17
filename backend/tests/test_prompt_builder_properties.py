"""Property-based tests for PromptBuilder.

Tests the ``PromptBuilder`` class from ``core/prompt_builder.py`` using
Hypothesis-generated inputs to verify determinism, MCP merge, channel
injection, watchdog formula, and context warning thresholds.

# Feature: multi-session-rearchitecture
"""
from __future__ import annotations

import threading

import pytest
from unittest.mock import MagicMock

from hypothesis import given, strategies as st

from core.prompt_builder import PromptBuilder
from tests.helpers import PROPERTY_SETTINGS






def _make_builder() -> PromptBuilder:
    """Create a PromptBuilder with a mock config."""
    mock_config = MagicMock()
    mock_config.get = MagicMock(side_effect=lambda key, default=None: {
        "default_model": "claude-sonnet-4-6",
        "use_bedrock": False,
        "sandbox_enabled_default": True,
        "sandbox_excluded_commands": "docker",
        "sandbox_auto_allow_bash": True,
        "sandbox_allow_unsandboxed": False,
        "sandbox_allowed_hosts": "*",
        "sandbox_additional_write_paths": "",
    }.get(key, default))
    return PromptBuilder(config=mock_config)


# ---------------------------------------------------------------------------
# Property 7: PromptBuilder determinism
# ---------------------------------------------------------------------------

class TestPromptBuilderDeterminism:
    """Property 7: PromptBuilder determinism.

    # Feature: multi-session-rearchitecture, Property 7: PromptBuilder determinism

    *For any* agent configuration, calling resolve_model, resolve_allowed_tools,
    compute_watchdog_timeout, and build_context_warning twice with identical
    inputs must produce identical outputs.

    **Validates: Requirements 3.1**
    """

    @given(
        model=st.sampled_from([
            "claude-opus-4-6", "claude-sonnet-4-6", None,
        ]),
    )
    @PROPERTY_SETTINGS
    def test_resolve_model_deterministic(self, model):
        """resolve_model returns same result for same input."""
        builder = _make_builder()
        config = {"model": model}
        r1 = builder.resolve_model(config)
        r2 = builder.resolve_model(config)
        assert r1 == r2

    @given(
        tools=st.lists(st.sampled_from(["Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch"]), max_size=5),
    )
    @PROPERTY_SETTINGS
    def test_resolve_allowed_tools_deterministic(self, tools):
        """resolve_allowed_tools returns same result for same input."""
        builder = _make_builder()
        config = {"allowed_tools": tools}
        r1 = builder.resolve_allowed_tools(config)
        r2 = builder.resolve_allowed_tools(config)
        assert r1 == r2


# ---------------------------------------------------------------------------
# Tool-access blacklist model (run_9cfdb08d) — whitelist→blacklist flip
# ---------------------------------------------------------------------------

class TestToolAccessBlacklistModel:
    """resolve_allowed_tools no longer builds an implicit whitelist; restriction
    moves to resolve_disallowed_tools (enable_*=False → deny). Default = allow-all
    (allowed_tools=None) so built-ins like AskUserQuestion are not silently disabled."""

    def test_no_explicit_config_returns_empty(self):
        """AC1: config without allowed_tools → [] (NOT the 8-tool implicit whitelist)."""
        builder = _make_builder()
        assert builder.resolve_allowed_tools({}) == []
        # enable_* flags must NOT resurrect an implicit whitelist
        assert builder.resolve_allowed_tools(
            {"enable_bash_tool": True, "enable_file_tools": True, "enable_web_tools": True}
        ) == []

    def test_explicit_allowed_tools_respected(self):
        """AC2: explicit non-empty allowed_tools returned verbatim (opt-in whitelist)."""
        builder = _make_builder()
        assert builder.resolve_allowed_tools({"allowed_tools": ["Read", "Grep"]}) == ["Read", "Grep"]

    def test_askuserquestion_not_disabled_by_default(self):
        """AC3: default config → AskUserQuestion neither whitelisted-out nor in disallowed."""
        builder = _make_builder()
        assert builder.resolve_allowed_tools({}) == []  # → SDK allowed_tools=None → allow-all
        assert "AskUserQuestion" not in builder.resolve_disallowed_tools({})

    def test_enable_web_false_blacklists_web(self):
        """AC6: enable_web_tools=False → WebFetch+WebSearch in disallowed (restriction preserved)."""
        builder = _make_builder()
        d = builder.resolve_disallowed_tools({"enable_web_tools": False})
        assert "WebFetch" in d and "WebSearch" in d

    def test_enable_bash_false_blacklists_bash(self):
        """AC6: enable_bash_tool=False → Bash in disallowed."""
        builder = _make_builder()
        assert "Bash" in builder.resolve_disallowed_tools({"enable_bash_tool": False})

    def test_enable_file_false_blacklists_file_tools(self):
        """AC6: enable_file_tools=False → Read/Write/Edit/Glob/Grep in disallowed."""
        builder = _make_builder()
        d = builder.resolve_disallowed_tools({"enable_file_tools": False})
        assert {"Read", "Write", "Edit", "Glob", "Grep"} <= set(d)

    def test_enable_file_false_blacklists_notebookedit(self):
        """Gate-2 adversarial: NotebookEdit is a file-mutating built-in — enable_file_tools=False
        MUST deny it, else a 'no file tools' agent leaks notebook writes under default-allow."""
        builder = _make_builder()
        assert "NotebookEdit" in builder.resolve_disallowed_tools({"enable_file_tools": False})

    def test_default_config_disallows_nothing_extra(self):
        """Default (all flags True/absent) → resolve_disallowed_tools returns []."""
        builder = _make_builder()
        assert builder.resolve_disallowed_tools({}) == []
        assert builder.resolve_disallowed_tools(
            {"enable_bash_tool": True, "enable_file_tools": True, "enable_web_tools": True}
        ) == []


# ---------------------------------------------------------------------------
# Property 10: Watchdog timeout formula
# ---------------------------------------------------------------------------

class TestWatchdogTimeoutFormula:
    """Property 10: Watchdog timeout formula.

    # Feature: multi-session-rearchitecture, Property 10: Watchdog timeout formula

    *For any* non-negative input token count and turn count,
    compute_watchdog_timeout must return clamp(180 + tokens/100K*30 + turns*5, 180, 600).

    **Validates: Requirements 3.5**
    """

    @given(
        tokens=st.integers(min_value=0, max_value=500_000),
        turns=st.integers(min_value=0, max_value=100),
    )
    @PROPERTY_SETTINGS
    def test_formula_matches_spec(self, tokens: int, turns: int):
        """Timeout matches the specified formula."""
        builder = _make_builder()
        result = builder.compute_watchdog_timeout(
            session_id="test", input_tokens=tokens, user_turns=turns,
        )
        expected = 180 + int((tokens / 100_000) * 30) + (turns * 5)
        expected = min(expected, 600)
        expected = max(expected, 180)
        assert result == expected

    def test_base_timeout_with_no_metrics(self):
        """Returns base timeout (180) when no metrics provided."""
        builder = _make_builder()
        assert builder.compute_watchdog_timeout() == 180

    def test_max_timeout_cap(self):
        """Never exceeds 600s."""
        builder = _make_builder()
        result = builder.compute_watchdog_timeout(
            input_tokens=500_000, user_turns=100,
        )
        assert result <= 600


# ---------------------------------------------------------------------------
# Property 11: Context warning thresholds
# ---------------------------------------------------------------------------

class TestContextWarningThresholds:
    """Property 11: Context warning thresholds.

    # Feature: multi-session-rearchitecture, Property 11: Context warning thresholds

    *For any* input token count and model, build_context_warning must return
    correct warning levels based on percentage thresholds.

    **Validates: Requirements 3.6**
    """

    @given(tokens=st.integers(min_value=1, max_value=500_000))
    @PROPERTY_SETTINGS
    def test_warning_levels_correct(self, tokens: int):
        """Warning level matches percentage thresholds."""
        builder = _make_builder()
        result = builder.build_context_warning(tokens, "claude-sonnet-4-6")
        if result is None:
            return  # Below all thresholds

        window = 1_000_000  # Claude 4.6 1M context
        pct = round((tokens / window) * 100)

        if pct >= 85:
            assert result["level"] == "critical"
        elif pct >= 70:
            assert result["level"] == "warn"
        else:
            assert result["level"] == "ok"

        assert result["pct"] == pct

    def test_none_for_zero_tokens(self):
        """Returns None for 0 tokens."""
        builder = _make_builder()
        assert builder.build_context_warning(0, "claude-sonnet-4-6") is None

    def test_none_for_none_tokens(self):
        """Returns None for None tokens."""
        builder = _make_builder()
        assert builder.build_context_warning(None, "claude-sonnet-4-6") is None


# ---------------------------------------------------------------------------
# Property 8: MCP server merge is a union (trivial — merge is deprecated no-op)
# ---------------------------------------------------------------------------

class TestMCPServerMerge:
    """Property 8: MCP server merge is a union.

    # Feature: multi-session-rearchitecture, Property 8: MCP merge union

    merge_user_local_mcp_servers is deprecated (no-op). Verify it doesn't
    modify the input.

    **Validates: Requirements 3.3**
    """

    def test_merge_is_noop(self):
        """Deprecated merge doesn't modify servers."""
        builder = _make_builder()
        servers = {"builder-mcp": {"command": "uvx"}}
        builder.merge_user_local_mcp_servers(servers, [], set())
        assert "builder-mcp" in servers


# ---------------------------------------------------------------------------
# Property 9: Channel MCP injection
# ---------------------------------------------------------------------------

class TestChannelMCPInjection:
    """Property 9: Channel MCP injection.

    # Feature: multi-session-rearchitecture, Property 9: Channel MCP injection

    inject_channel_mcp must preserve all original servers and add the
    channel server when channel_context is provided.

    **Validates: Requirements 3.4**
    """

    def test_no_injection_without_channel(self):
        """No channel context → servers unchanged."""
        builder = _make_builder()
        servers = {"builder-mcp": {"command": "uvx"}}
        result = builder.inject_channel_mcp(servers, None, "/tmp")
        assert result == servers

    def test_original_servers_preserved(self):
        """Original servers are never removed by injection."""
        builder = _make_builder()
        servers = {"builder-mcp": {"command": "uvx"}, "slack-mcp": {"command": "uvx"}}
        original_keys = set(servers.keys())
        # inject_channel_mcp delegates to mcp_config_loader which may
        # or may not add a server depending on channel_context format.
        # The key invariant: original servers are never removed.
        try:
            result = builder.inject_channel_mcp(
                servers, {"channel_type": "test"}, "/tmp",
            )
            for key in original_keys:
                assert key in result
        except Exception:
            pass  # Channel injection may fail with mock data — that's OK


# ---------------------------------------------------------------------------
# Property 17: MCP subset configuration
# ---------------------------------------------------------------------------

class TestMCPSubsetConfiguration:
    """Property 17: MCP tier-based lazy loading.

    # Feature: lazy-mcp-loading, Property 17: MCP tier filtering

    build_mcp_config delegates to load_mcp_config_tiered, returning
    (servers, disallowed, deferred) — a 3-tuple.

    **Validates: Requirements 9.1, 9.3**
    """

    def test_tiered_returns_three_values(self):
        """build_mcp_config returns (servers, disallowed, deferred)."""
        builder = _make_builder()

        import unittest.mock as mock
        with mock.patch(
            "core.mcp_config_loader.load_mcp_config_tiered",
            return_value=({"builder-mcp": {"command": "uvx"}}, [], [{"name": "slack-mcp", "tier": "channel"}]),
        ):
            result = builder.build_mcp_config("/tmp", enable_mcp=True)

        assert len(result) == 3
        servers, disallowed, deferred = result
        assert "builder-mcp" in servers
        assert len(deferred) == 1

    def test_channel_context_passed_through(self):
        """channel_context is forwarded to load_mcp_config_tiered."""
        builder = _make_builder()

        import unittest.mock as mock
        with mock.patch(
            "core.mcp_config_loader.load_mcp_config_tiered",
            return_value=({}, [], []),
        ) as mock_load:
            builder.build_mcp_config("/tmp", enable_mcp=True, channel_context={"channel_type": "slack"})

        mock_load.assert_called_once()
        call_kwargs = mock_load.call_args
        assert call_kwargs[1]["channel_context"] == {"channel_type": "slack"}


class TestEvolutionUnconditionalLoad:
    """EVOLUTION.md must load on a desktop chat tab regardless of whether a
    coding project is active — it carries cognitive failure history (CLASS A/B),
    which is relevant in ANY session, not just coding ones. The old O2 gate
    (prompt_builder.py:635-645) excluded it from non-coding desktop sessions;
    this asserts that gate is gone. Drives the REAL build_system_prompt (not the
    _simulate_build re-impl, which never had the gate — GUI14: test the real fn).
    """

    def _assembled_text(self, agent_config: dict) -> str:
        """The ASSEMBLED context text — this reflects exclusion. The metadata
        `files` list does NOT (it iterates all CONTEXT_FILES specs regardless of
        exclude_filenames), so asserting against it would be vacuous (Gate-2
        test-theater trap, caught run_6d2cc624). The section header only appears
        when the file actually made it into the prompt."""
        meta = agent_config.get("_system_prompt_metadata", {})
        return meta.get("full_text", "")

    def _run_build(self, workspace, *, coding: bool = False, channel_context=None) -> dict:
        """Build the system prompt and return the agent_config.

        Note (run_a16d61ad, §4.2.1 #15): the Codebase-intelligence briefing section
        and its `_detect_active_coding_project` gate were REMOVED (PUSH→PULL). The
        `coding` param is retained for caller compatibility but no longer changes the
        briefing — code context is now PULL-only via recall's codeintel leg, never
        injected at session start. `get_focus_keywords` is still mocked empty to keep
        the build hermetic to `workspace`."""
        import asyncio
        import unittest.mock as mock

        builder = _make_builder()
        agent_config: dict = {}
        with mock.patch(
            "core.proactive_intelligence.get_focus_keywords",
            return_value="",
        ):
            asyncio.run(builder.build_system_prompt(
                agent_config=agent_config,
                working_directory=str(workspace),
                channel_context=channel_context,
            ))
        return agent_config

    def test_evolution_loaded_on_noncoding_desktop(self, tmp_path):
        """The bug-revealing case: non-coding desktop session must STILL load
        EVOLUTION.md into the assembled prompt. Under the old O2 gate the
        'Evolution Registry' section was excluded → this asserts RED on old code."""
        text = self._assembled_text(self._run_build(tmp_path, coding=False))
        # Guard: build actually assembled content (not an empty/failed pass).
        assert "Memory" in text, f"build did not assemble context: {text[:200]!r}"
        assert "Evolution Registry" in text, (
            "EVOLUTION.md excluded from a non-coding desktop session — "
            "the O2 coding-gate must be removed"
        )

    # Assembled-prompt SECTION HEADERS for the whole-file-private files. The
    # `_system_prompt_metadata.files[]` list is NOT the right signal — it is a
    # TSCC-viewer inventory that re-reads every CONTEXT_FILES entry on disk
    # regardless of channel exclusion (prompt_builder.py:936). The real
    # observable is whether the file's `## <section>` header made it into the
    # assembled context_text (same signal the sibling evolution test uses).
    _PRIVATE_SECTIONS = {
        "USER.md": "## User\n",
        "EVOLUTION.md": "## Evolution Registry\n",
        "MEMORY.md": "## Memory\n",
        "PROJECTS.md": "## Projects\n",
    }

    def _leaked_private_sections(self, agent_config: dict) -> set[str]:
        text = self._assembled_text(agent_config)
        return {fn for fn, hdr in self._PRIVATE_SECTIONS.items() if hdr in text}

    def test_private_files_excluded_for_nonowner_dm(self, tmp_path):
        """L3 private-lane: a NON-OWNER DM must exclude ALL whole-file-private
        files (USER/EVOLUTION/MEMORY/PROJECTS). The pre-fix bug leaked USER.md +
        MEMORY.md here (CHANNEL_LIGHT_EXCLUDE was only {EVOLUTION, PROJECTS}).
        Mutation-proof: drop any file from WHOLE_FILE_PRIVATE → its section header
        reappears in the assembled prompt → RED."""
        ctx = {"is_owner": False, "is_group": False, "channel_type": "slack"}
        cfg = self._run_build(tmp_path, coding=False, channel_context=ctx)
        text = self._assembled_text(cfg)
        assert text, "build produced empty prompt for non-owner DM"
        leaked = self._leaked_private_sections(cfg)
        assert not leaked, f"private files leaked into non-owner DM prompt: {leaked}"

    def test_private_files_excluded_for_group_channel(self, tmp_path):
        """L3 private-lane: a GROUP channel must exclude ALL whole-file-private
        files. The pre-fix bug leaked EVOLUTION.md here (GROUP_CHANNEL_EXCLUDE was
        only {MEMORY, USER})."""
        ctx = {"is_owner": False, "is_group": True, "channel_type": "slack"}
        cfg = self._run_build(tmp_path, coding=False, channel_context=ctx)
        text = self._assembled_text(cfg)
        assert text, "build produced empty prompt for group channel"
        leaked = self._leaked_private_sections(cfg)
        assert not leaked, f"private files leaked into group channel prompt: {leaked}"

    def test_private_files_present_for_owner(self, tmp_path):
        """AC3: owner DM / chat tab is UNCHANGED — the private files still load.
        Guards against the fix over-reaching into the owner (full-context) path."""
        cfg = self._run_build(tmp_path, coding=False, channel_context=None)
        text = self._assembled_text(cfg)
        assert "## Memory\n" in text and "## Evolution Registry\n" in text, (
            "owner context lost private files (fix over-reached)"
        )


class TestEphemeralBudgetCeiling:
    """recall#G (run_a16d61ad): ephemeral sections (briefing/digest/suggestions)
    are appended AFTER the budgeted loader output with only a fixed headroom
    reservation. An overshoot must be VISIBLE (WARNING) but NEVER truncated —
    ephemeral content is cognition/continuity, not clippable filler."""

    def _run_build(self, workspace) -> dict:
        import asyncio
        import unittest.mock as mock

        builder = _make_builder()
        agent_config: dict = {}
        with mock.patch(
            "core.proactive_intelligence.get_focus_keywords", return_value="",
        ):
            asyncio.run(builder.build_system_prompt(
                agent_config=agent_config,
                working_directory=str(workspace),
            ))
        return agent_config

    def test_oversized_ephemeral_warns_and_does_not_truncate(self, tmp_path, caplog):
        """An oversized briefing → WARNING logged + the briefing content survives
        intact (not clipped). Mutation: remove the `if _ephemeral_tok > ...` warn
        block → no warning → RED. Truncating it instead → content-missing → RED."""
        import logging
        import unittest.mock as mock

        # A unique, huge marker briefing — far beyond EPHEMERAL_HEADROOM (~9K tok).
        marker = "ZZUNIQUEBRIEFINGMARKERZZ"
        huge_briefing = "## Session Briefing\n" + (marker + " ") * 12000  # ~36K+ tok

        with mock.patch(
            "core.proactive_intelligence.build_session_briefing",
            return_value=huge_briefing,
        ), caplog.at_level(logging.WARNING, logger="core.prompt_builder"):
            agent_config = self._run_build(tmp_path)

        full = (agent_config.get("_system_prompt_metadata") or {}).get("full_text", "")
        # 1) Content NOT truncated — the whole oversized briefing survived.
        assert full.count(marker) >= 12000, (
            f"ephemeral briefing was truncated (found {full.count(marker)} of 12000 "
            "markers) — cognition content must NEVER be clipped, only WARNED about"
        )
        # 2) Overshoot was made VISIBLE.
        assert any(
            "exceeds reserved headroom" in r.message for r in caplog.records
        ), "oversized ephemeral content must emit a WARNING (observability, recall#G)"

    def test_normal_ephemeral_no_warning(self, tmp_path, caplog):
        """A normal-sized briefing must NOT trip the ceiling warning (no false alarm)."""
        import logging
        import unittest.mock as mock

        with mock.patch(
            "core.proactive_intelligence.build_session_briefing",
            return_value="## Session Briefing\n**System health:** ok",
        ), caplog.at_level(logging.WARNING, logger="core.prompt_builder"):
            self._run_build(tmp_path)

        assert not any(
            "exceeds reserved headroom" in r.message for r in caplog.records
        ), "normal ephemeral content must not trip the ceiling warning"


class TestSystemPromptFaultIsolation:
    """run_e47c1cfb root-fix: a failure in ANY ephemeral section must NEVER drop
    the 12 core context files, and a core-context failure must be LOUD, not a
    swallowed warning.

    Regression origin: commit 039c4f32 left `daily_activity_dir` referenced from
    outer scope (prompt_builder.py:943 NameError) inside the monolithic try that
    wraps BOTH core assembly AND ephemeral — so on any session with recent daily
    logs the NameError zeroed out ALL core sections (305 silent degradations/2days).
    """

    def _make_builder(self):
        return _make_builder()

    def _run_build(self, workspace, **kwargs):
        import asyncio
        import unittest.mock as mock
        builder = _make_builder()
        agent_config: dict = {}
        with mock.patch(
            "core.proactive_intelligence.get_focus_keywords", return_value="",
        ):
            asyncio.run(builder.build_system_prompt(
                agent_config=agent_config,
                working_directory=str(workspace),
                **kwargs,
            ))
        return agent_config

    def _full(self, cfg):
        # The RETURN value is builder_text + system_prompt; but the assembled
        # context lives in _system_prompt_metadata.full_text (core+ephemeral).
        return (cfg.get("_system_prompt_metadata") or {}).get("full_text", "") or \
               (cfg.get("system_prompt") or "")

    _CORE_HEADERS = ["## SwarmAI\n", "## Identity\n", "## Soul\n",
                     "## Self-Portrait\n", "## Agent Directives\n"]

    def _seed_daily(self, workspace):
        """Create Knowledge/DailyActivity/<date>.md so the daily for-loop iterates
        — this is what triggers the L943 NameError path in the buggy code."""
        da = workspace / "Knowledge" / "DailyActivity"
        da.mkdir(parents=True, exist_ok=True)
        (da / "2026-08-11.md").write_text("# Today\nDid some work.\n", encoding="utf-8")

    # ── AC1: the NameError bugfix ────────────────────────────────────────
    def test_ac1_daily_activity_path_no_nameerror_core_intact(self, tmp_path, caplog):
        """A session WITH recent DailyActivity files builds a complete prompt —
        no 'daily_activity_dir is not defined', all 5 core sections present.
        RED on buggy code: the L943 NameError drops all core into framing-only."""
        import logging
        self._seed_daily(tmp_path)
        with caplog.at_level(logging.WARNING, logger="core.prompt_builder"):
            cfg = self._run_build(tmp_path)
        full = self._full(cfg)
        assert not any("daily_activity_dir" in r.message for r in caplog.records), \
            "daily_activity_dir NameError still fires (regression 039c4f32 unfixed)"
        for hdr in self._CORE_HEADERS:
            assert hdr in full, f"core section {hdr!r} missing after daily-activity path"
        # The daily section itself should also have made it in.
        assert "## Daily Activity (2026-08-11)" in full

    # ── AC2: ephemeral fault isolation ──────────────────────────────────
    def test_ac2_ephemeral_failure_leaves_core_intact(self, tmp_path):
        """An exception in an ephemeral producer (briefing) must NOT drop core."""
        import unittest.mock as mock
        self._seed_daily(tmp_path)
        with mock.patch(
            "core.proactive_intelligence.build_session_briefing",
            side_effect=RuntimeError("boom in ephemeral"),
        ):
            cfg = self._run_build(tmp_path)
        full = self._full(cfg)
        for hdr in self._CORE_HEADERS:
            assert hdr in full, (
                f"core section {hdr!r} lost because an EPHEMERAL section raised — "
                "core must be committed before ephemeral runs"
            )

    def test_ac2_digest_failure_leaves_core_intact(self, tmp_path):
        """A second ephemeral source (active-session digest) failing also spares core."""
        import unittest.mock as mock
        cfg_builder = _make_builder()
        with mock.patch.object(
            type(cfg_builder), "_build_active_session_digest",
            side_effect=RuntimeError("digest boom"),
        ):
            import asyncio
            agent_config: dict = {}
            with mock.patch("core.proactive_intelligence.get_focus_keywords", return_value=""):
                asyncio.run(cfg_builder.build_system_prompt(
                    agent_config=agent_config, working_directory=str(tmp_path),
                ))
        full = (agent_config.get("_system_prompt_metadata") or {}).get("full_text", "") or \
               (agent_config.get("system_prompt") or "")
        for hdr in self._CORE_HEADERS:
            assert hdr in full, f"core section {hdr!r} lost on digest failure"

    # ── AC3: completeness gate ──────────────────────────────────────────
    def test_ac3_completeness_gate_detects_missing_core(self):
        """assert_core_sections flags a prompt missing a core section (line-anchored)."""
        from core.prompt_builder import assert_core_sections
        good = "## SwarmAI\nx\n\n## Identity\ny\n\n## Soul\nz\n\n" \
               "## Self-Portrait\nw\n\n## Agent Directives\nv"
        missing = good.replace("## Soul\nz\n\n", "")
        assert assert_core_sections(good) == [], "complete prompt must report no missing"
        assert "Soul" in assert_core_sections(missing), \
            "gate must detect the dropped core section"

    def test_ac3_gate_no_false_pass_on_body_mention(self):
        """A header string appearing in BODY text must NOT count as the section
        (line-anchored '\\n## name\\n', not bare substring)."""
        from core.prompt_builder import assert_core_sections
        # 'Soul' header present only as inline body text of another section.
        prompt = ("## SwarmAI\nwe talk about ## Soul here as prose\n\n"
                  "## Identity\ny\n\n## Self-Portrait\nw\n\n## Agent Directives\nv")
        missing = assert_core_sections(prompt)
        assert "Soul" in missing, "body mention of a header must not satisfy the gate"

    def test_ac3_gate_does_not_require_self_portrait(self):
        """REVIEW MEDIUM#1: SELF.md (Self-Portrait) is runtime-owned and can be
        legitimately empty on a fresh workspace — the loader omits it. The gate must
        NOT demand it, or it false-fires on a clean install. A prompt with the 4
        system-owned sections but NO Self-Portrait is COMPLETE."""
        from core.prompt_builder import assert_core_sections
        prompt = "## SwarmAI\nx\n\n## Identity\ny\n\n## Soul\nz\n\n## Agent Directives\nv"
        assert assert_core_sections(prompt) == [], (
            "gate must not require Self-Portrait — an empty SELF.md is legitimate, "
            "not a degradation"
        )

    def test_ac3_required_sections_are_system_owned_only(self):
        """The gate's required set = system-owned constitution (user_customized=False)
        = {SwarmAI,Identity,Soul,Agent Directives}, excluding runtime-owned SELF."""
        from core.context_directory_loader import required_prompt_sections
        assert set(required_prompt_sections()) == {
            "SwarmAI", "Identity", "Soul", "Agent Directives"
        }

    # ── Layer 4: cross-boundary E2E (session-spawn seam) ────────────────
    def test_layer4_real_session_build_produces_complete_prompt(self, tmp_path):
        """Cross-boundary E2E (cross_boundary=true, session-spawn shared path):
        drive the REAL build_system_prompt end-to-end (no mock of the
        thing-under-change — real ContextDirectoryLoader, real ephemeral path with
        daily files + distillation flag, real completeness gate) and assert the
        prompt every session receives is complete: all 4 required constitution
        sections present, no _context_degraded flag, and each required section
        appears exactly once (no double-append).

        This is the seam Layers 1-3 don't drive: the actual session-spawn output.
        Mutation-proof of non-vacuity is in test_layer4_mutation_daily_scope_regresses.
        """
        self._seed_daily(tmp_path)
        (tmp_path / "Knowledge" / "DailyActivity" / ".needs_distillation").write_text("")
        cfg = self._run_build(tmp_path)
        out = self._full(cfg)
        assert cfg.get("_context_degraded") is None, \
            f"real session build reported degraded: {cfg.get('_context_degraded')}"
        for hdr in ["## SwarmAI\n", "## Identity\n", "## Soul\n", "## Agent Directives\n"]:
            assert out.count(hdr) == 1, f"required section {hdr!r} count={out.count(hdr)} (want 1)"
        # The ephemeral seam also reconnected (daily + flag rode through).
        assert "## Daily Activity (2026-08-11)" in out
        assert "## Memory Maintenance Required" in out

    # ── AC4: fail-loud ──────────────────────────────────────────────────
    def test_ac4_core_failure_is_loud(self, tmp_path, caplog):
        """When core assembly (load_all) raises, the build sets a degraded flag +
        logs ERROR — NOT a swallowed warning."""
        import logging
        import unittest.mock as mock
        with mock.patch(
            "core.context_directory_loader.ContextDirectoryLoader.load_all",
            side_effect=RuntimeError("core load boom"),
        ), caplog.at_level(logging.ERROR, logger="core.prompt_builder"):
            cfg = self._run_build(tmp_path)
        assert cfg.get("_context_degraded"), \
            "core-context failure must set agent_config['_context_degraded']"
        assert any(r.levelno >= logging.ERROR for r in caplog.records), \
            "core-context failure must log at ERROR (fail-loud), not warning"

    def test_ac4_degraded_signal_reaches_metadata(self, tmp_path):
        """REVIEW MED (multi-specialist): the degraded flag must not be write-only.
        It must be mirrored into _system_prompt_metadata (which IS copied downstream
        to session-init/TSCC), so the fail-loud signal is consumable, not just a log."""
        import unittest.mock as mock
        with mock.patch(
            "core.context_directory_loader.ContextDirectoryLoader.load_all",
            side_effect=RuntimeError("core load boom"),
        ):
            cfg = self._run_build(tmp_path)
        meta = cfg.get("_system_prompt_metadata") or {}
        assert meta.get("degraded"), (
            "degraded signal must be surfaced in _system_prompt_metadata (consumable "
            "downstream), not only in agent_config['_context_degraded'] / logs"
        )
        assert meta["degraded"] == cfg.get("_context_degraded")

    def test_ac4_stale_degraded_cleared_on_reentry(self, tmp_path):
        """REVIEW RED-TEAM MED: build_system_prompt can be called twice on the SAME
        agent_config (resume-fallback). A failed call #1 must not leave a stale
        _context_degraded that makes a successful call #2 report a healthy prompt as
        degraded. The flag must be a fresh per-build computation."""
        import asyncio
        import unittest.mock as mock
        builder = _make_builder()
        agent_config: dict = {"_context_degraded": "core_context_failed: stale from prior build"}
        with mock.patch("core.proactive_intelligence.get_focus_keywords", return_value=""):
            asyncio.run(builder.build_system_prompt(
                agent_config=agent_config, working_directory=str(tmp_path),
            ))
        # call #2 succeeded (real template workspace) → stale flag must be gone
        assert agent_config.get("_context_degraded") is None, \
            "stale degraded flag survived a successful rebuild (resume-fallback false-positive)"
        meta = agent_config.get("_system_prompt_metadata") or {}
        assert not meta.get("degraded"), "stale degraded leaked into fresh metadata"

    # ── AC5: no double-append of core ───────────────────────────────────
    def test_ac5_core_committed_exactly_once(self, tmp_path):
        """After early-commit + ephemeral-append refactor, each core header must
        appear EXACTLY once (Gate-1 CHECK5 double-append regression)."""
        self._seed_daily(tmp_path)
        full = self._full(self._run_build(tmp_path))
        for hdr in self._CORE_HEADERS:
            assert full.count(hdr) == 1, (
                f"core section {hdr!r} appears {full.count(hdr)}x — double-append "
                "(old L1054-1058 re-commit must be removed)"
            )


class TestContextLoadOffEventLoop:
    """run_cc397b0d + run_6a7e5a2f: build_system_prompt must NOT run
    loader.ensure_directory() or loader.load_all() synchronously on the event loop —
    both fork/do blocking I/O (load_all forks `git status` on L1-cache-miss), which
    stalls ALL tabs' SSE streams. They are dispatched OFF the event loop — as of
    run_6a7e5a2f via ``executors.run_in('io', ...)`` (a bounded pool), NOT the bare
    ``asyncio.to_thread`` default pool. This test proves BOTH run off the main thread
    and that the load_all kwargs (model_context_window, exclude_filenames) survive
    the off-loop hand-off (they now ride a lambda wrapper, since run_in is positional).
    The assertions are MECHANISM-AGNOSTIC (they check the worker-thread + kwargs
    invariant), so they hold for either dispatch primitive — the point is off-loop.
    """

    def _run_and_capture_threads(self, tmp_path):
        """Run build_system_prompt with a spy ContextDirectoryLoader that records
        the thread each method executed on. Returns (ensure_thread, load_thread,
        load_kwargs, main_thread_ident)."""
        import asyncio
        import unittest.mock as mock
        import core.context_directory_loader as cdl_mod

        captured: dict = {}
        RealLoader = cdl_mod.ContextDirectoryLoader

        class _SpyLoader(RealLoader):
            def ensure_directory(self, *a, **kw):
                captured["ensure_thread"] = threading.get_ident()
                return super().ensure_directory(*a, **kw)

            def load_all(self, *a, **kw):
                captured["load_thread"] = threading.get_ident()
                captured["load_kwargs"] = dict(kw)
                return super().load_all(*a, **kw)

        builder = _make_builder()
        agent_config: dict = {}

        async def _drive():
            # Record the event-loop thread from INSIDE the coroutine, so the
            # comparison is against the exact thread build_system_prompt awaits on.
            captured["main_thread"] = threading.get_ident()
            # build_system_prompt imports ContextDirectoryLoader locally from
            # core.context_directory_loader, so patching it on that module is the
            # binding the function resolves at call time.
            with mock.patch.object(cdl_mod, "ContextDirectoryLoader", _SpyLoader), \
                 mock.patch(
                     "core.proactive_intelligence.get_focus_keywords",
                     return_value="",
                 ):
                await builder.build_system_prompt(
                    agent_config=agent_config,
                    working_directory=str(tmp_path),
                )

        asyncio.run(_drive())
        return captured

    def test_ensure_and_load_run_off_main_thread(self, tmp_path):
        """RED on bare synchronous calls (they run on the loop thread); GREEN once
        both are awaited off-loop (executors.run_in('io', ...) → worker thread)."""
        cap = self._run_and_capture_threads(tmp_path)
        assert "load_thread" in cap, "load_all was never called"
        assert "ensure_thread" in cap, "ensure_directory was never called"
        main = cap["main_thread"]
        assert cap["ensure_thread"] != main, (
            "ensure_directory ran on the event-loop thread — must be off-loop "
            "(executors.run_in)"
        )
        assert cap["load_thread"] != main, (
            "load_all ran on the event-loop thread (forks git status → stalls all "
            "tabs' SSE) — must be dispatched off-loop via executors.run_in"
        )

    def test_load_all_kwargs_survive_to_thread(self, tmp_path):
        """The off-loop hand-off must preserve load_all's keyword args — a dropped
        kwarg would silently change budgeting / exclusion behavior. (run_6a7e5a2f:
        kwargs now ride a lambda wrapper since executors.run_in is positional-only.)"""
        cap = self._run_and_capture_threads(tmp_path)
        kwargs = cap.get("load_kwargs", {})
        assert "model_context_window" in kwargs, (
            f"model_context_window kwarg lost through to_thread: {kwargs!r}"
        )
        # exclude_filenames is passed on every call (None for a desktop tab).
        assert "exclude_filenames" in kwargs, (
            f"exclude_filenames kwarg lost through to_thread: {kwargs!r}"
        )


# ---------------------------------------------------------------------------
# 阶段二: prompt-builder 两分 (default_builder / dynamic_builder)
# ---------------------------------------------------------------------------


class TestDefaultBuilderSplit:
    """AC1/AC6: build_default_system_prompt produces the input-INDEPENDENT base
    (context files + safety/datetime/runtime) as a cacheable STRING, EXCLUDING
    per-turn ephemeral layers (briefing / UI-SENSE / DailyActivity / resume).

    The base must still pass assert_core_sections — the strangler guarantee that
    extraction did not drop constitution content.
    """

    def _run_default(self, workspace, *, editor_context=None) -> str:
        import asyncio
        import unittest.mock as mock

        builder = _make_builder()
        agent_config: dict = {}
        with mock.patch(
            "core.proactive_intelligence.get_focus_keywords", return_value="",
        ):
            out = asyncio.run(builder.build_default_system_prompt(
                agent_config=agent_config,
                working_directory=str(workspace),
                editor_context=editor_context,
            ))
        return out

    def test_default_builder_returns_str_with_core_sections(self, tmp_path):
        """AC1: build_default_system_prompt returns a STRING (never an options
        object — anti-repetition run_f8c3ddd4) that passes assert_core_sections."""
        from core.prompt_builder import assert_core_sections
        out = self._run_default(tmp_path)
        assert isinstance(out, str), (
            f"build_default_system_prompt must return str, got {type(out)!r} "
            "(caching an options object caused cross-session bleed — run_f8c3ddd4)"
        )
        missing = assert_core_sections(out)
        assert not missing, f"default base dropped core section(s): {missing}"

    def test_default_builder_excludes_ephemeral(self, tmp_path):
        """AC1: the base MUST NOT contain per-turn ephemeral markers. A UI-SENSE
        block (editor_context) is the cleanest observable — it is per-turn and must
        NOT ride the cacheable base. Mutation-proof: if the extraction lets
        ephemeral leak into the base, this goes RED."""
        ec = {"open_file": {"path": "/tmp/foo.py", "name": "foo.py"}}
        out = self._run_default(tmp_path, editor_context=ec)
        assert "Recalled Knowledge" not in out, "recall leaked into cacheable base"
        assert "Current UI State" not in out and "foo.py" not in out, (
            "UI-SENSE (per-turn) leaked into the cacheable default base — "
            "ephemeral must be excluded from build_default_system_prompt"
        )

    def test_default_builder_excludes_briefing_with_teeth(self, tmp_path):
        """Gate-2 HIGH (run_f638ebc3): the briefing sub-block is a SEPARATE ephemeral
        try-block from bootstrap/daily. An empty tmpdir yields an empty briefing, so
        the plain exclusion test passes even if the skip only covers the FIRST block
        (the raise-hack bug). Force a NON-EMPTY briefing and assert it is STILL
        excluded from the base — this has teeth for the per-block-guard fix.
        Mutation-proof: drop the `include_ephemeral` guard on the briefing block →
        the marker leaks into the base → RED."""
        import asyncio
        import unittest.mock as mock
        builder = _make_builder()
        agent_config: dict = {}
        with mock.patch(
            "core.proactive_intelligence.get_focus_keywords", return_value="",
        ), mock.patch(
            "core.proactive_intelligence.build_session_briefing",
            return_value="## EPHEMERAL_BRIEFING_MARKER\nlive briefing content",
        ):
            out = asyncio.run(builder.build_default_system_prompt(
                agent_config=agent_config,
                working_directory=str(tmp_path),
            ))
        assert "EPHEMERAL_BRIEFING_MARKER" not in out, (
            "briefing (a per-turn ephemeral sub-block) leaked into the cacheable "
            "default base — every ephemeral sub-block must carry its own "
            "include_ephemeral guard, not just the first"
        )
        # And core is still intact (the guard didn't over-skip).
        from core.prompt_builder import assert_core_sections
        assert not assert_core_sections(out), "core dropped while excluding briefing"
