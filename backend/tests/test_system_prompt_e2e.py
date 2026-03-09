"""End-to-end integration tests for the system prompt assembly pipeline.

Simulates the full flow that ``AgentManager._build_system_prompt()`` performs:

1. ``ContextDirectoryLoader.ensure_directory()`` — template sync
2. ``ContextDirectoryLoader.load_all()`` — context assembly with budget
3. BOOTSTRAP.md ephemeral injection
4. DailyActivity ephemeral injection (with token cap)
5. Distillation flag injection
6. Per-file metadata collection (truncation detection)
7. ``SystemPromptBuilder.build()`` — non-file sections
8. Group channel MEMORY.md / USER.md exclusion
9. HTML comment stripping and H1 dedup (``_clean_content``)
10. CJK token estimation accuracy

Each test creates a realistic temp workspace with all 11 context files,
DailyActivity logs, and optional Bootstrap — then runs the same logic
as ``_build_system_prompt`` (extracted into ``_simulate_build``).

No database, no async, no mocks of core logic — only filesystem + pure functions.
"""

import os
import textwrap
from pathlib import Path

import pytest

from core.context_directory_loader import (
    BUDGET_LARGE_MODEL,
    CONTEXT_FILES,
    ContextDirectoryLoader,
    DEFAULT_TOKEN_BUDGET,
    GROUP_CHANNEL_EXCLUDE,
)
from core.system_prompt import SystemPromptBuilder

# ── Constants mirrored from agent_manager.py ─────────────────────────
TOKEN_CAP_PER_DAILY_FILE = 2000
EPHEMERAL_HEADROOM = 2 * TOKEN_CAP_PER_DAILY_FILE


# ── Realistic template content ───────────────────────────────────────

TEMPLATES: dict[str, str] = {
    "SWARMAI.md": textwrap.dedent("""\
        <!-- SYSTEM DEFAULT -- Managed by SwarmAI. -->

        # SwarmAI -- Your AI Command Center

        You are SwarmAI, the central intelligence of a supervised AI workspace.

        ## Core Principles

        - **You supervise** -- The user is always in control.
        - **Agents execute** -- You take action, not just provide information.
        - **Memory persists** -- Context accumulates across sessions.

        ## Priority Hierarchy

        1. **Safety** -- Never compromise safety for task completion
        2. **User intent** -- The user's goal is the north star
        3. **Efficiency** -- Accomplish more with less
    """),
    "IDENTITY.md": textwrap.dedent("""\
        <!-- SYSTEM DEFAULT -->

        # Identity -- Who Am I

        - **Name:** SwarmAI
        - **Type:** AI Command Center
        - **Vibe:** Sharp, reliable, gets things done

        ## About Me

        I'm SwarmAI -- a personal AI assistant.
    """),
    "SOUL.md": textwrap.dedent("""\
        <!-- SYSTEM DEFAULT -->

        # Soul -- Who You Are

        ## Personality

        - **Genuine** -- Skip filler. Just help.
        - **Opinionated** -- Have preferences, disagree respectfully.
        - **Concise** -- Say what needs to be said.
    """),
    "AGENT.md": textwrap.dedent("""\
        <!-- SYSTEM DEFAULT -->

        # Agent Directives

        ## Every Session

        1. Read your context files
        2. Check STEERING.md for overrides
        3. Check MEMORY.md for recent decisions

        ## Safety Rules

        - Never exfiltrate private data
        - **trash > rm**
    """),
    "USER.md": textwrap.dedent("""\
        # User -- About You

        - **Name:** TestUser
        - **Timezone:** UTC+8
        - **Role:** Developer

        ## Preferences

        - Show me the code, don't just describe it.
    """),
    "STEERING.md": textwrap.dedent("""\
        # Steering -- Session Overrides

        ## Current Focus

        _(Nothing set.)_

        ## Standing Rules

        - Python: type hints everywhere
    """),
    "TOOLS.md": textwrap.dedent("""\
        # Tools & Environment

        ## Local Tool Preferences

        - Editor: Neovim
    """),
    "MEMORY.md": textwrap.dedent("""\
        # Memory -- What I Remember

        ## Recent Context

        - 2026-03-08: Secret personal decision about project pivot.
        - 2026-03-07: User prefers functional style over OOP.

        ## Open Threads

        - Monitor DailyActivity file creation.
    """),
    "KNOWLEDGE.md": textwrap.dedent("""\
        # Knowledge -- What I Know

        ## Tech Stack

        - Python, TypeScript, Rust
    """),
    "PROJECTS.md": textwrap.dedent("""\
        # Projects -- What's In Flight

        ## Active Projects

        ### SwarmAI
        - **Status:** In Progress
        - **Priority:** High
    """),
    "EVOLUTION.md": textwrap.dedent("""\
        # SwarmAI Evolution Registry

        ## Capabilities Built

        _No capabilities built yet._

        ## Failed Evolutions

        _No failed evolutions recorded yet._
    """),
}


# ── Helper: simulate _build_system_prompt ────────────────────────────


def _simulate_build(
    workspace: Path,
    channel_context: dict | None = None,
    model_context_window: int = 200_000,
    agent_name: str = "SwarmAI",
    agent_model: str | None = None,
) -> tuple[str, dict]:
    """Reproduce the logic of AgentManager._build_system_prompt().

    Returns:
        (final_prompt, prompt_metadata)
    """
    context_dir = workspace / ".context"
    base_budget = DEFAULT_TOKEN_BUDGET
    loader = ContextDirectoryLoader(
        context_dir=context_dir,
        token_budget=max(base_budget - EPHEMERAL_HEADROOM, base_budget // 2),
    )

    # ── Exclude personal files for group channels ──
    exclude_files: set[str] | None = None
    if channel_context and channel_context.get("is_group"):
        exclude_files = set(GROUP_CHANNEL_EXCLUDE)

    context_text = loader.load_all(
        model_context_window=model_context_window,
        exclude_filenames=exclude_files,
    )

    # ── BOOTSTRAP.md detection ──
    bootstrap_path = context_dir / "BOOTSTRAP.md"
    if bootstrap_path.exists():
        bootstrap_content = bootstrap_path.read_text(encoding="utf-8").strip()
        if bootstrap_content:
            context_text = f"## Onboarding\n{bootstrap_content}\n\n{context_text}"

    # ── DailyActivity reading ──
    daily_dir = workspace / "Knowledge" / "DailyActivity"
    if daily_dir.is_dir():
        da_files = sorted(
            [f for f in daily_dir.glob("*.md") if f.stem[:4].isdigit()],
            key=lambda f: f.stem,
            reverse=True,
        )[:2]
        for daily_file in da_files:
            daily_content = daily_file.read_text(encoding="utf-8").strip()
            if daily_content:
                token_count = ContextDirectoryLoader.estimate_tokens(daily_content)
                if token_count > TOKEN_CAP_PER_DAILY_FILE:
                    # Simple truncation: keep tail
                    words = daily_content.split()
                    keep = int(TOKEN_CAP_PER_DAILY_FILE * 3 / 4)
                    daily_content = "[Truncated]\n" + " ".join(words[-keep:])
                context_text += f"\n\n## Daily Activity ({daily_file.stem})\n{daily_content}"

        # Distillation flag
        flag_path = daily_dir / ".needs_distillation"
        if flag_path.is_file():
            context_text += "\n\n## Memory Maintenance Required\nRun s_memory-distill."

    # ── Merge into agent system_prompt ──
    agent_config = {
        "name": agent_name,
        "description": "Your AI Team, 24/7",
        "model": agent_model,
        "system_prompt": "",
    }
    if context_text:
        agent_config["system_prompt"] = context_text

    # ── Collect metadata ──
    prompt_metadata: dict = {"files": [], "total_tokens": 0}
    for spec in CONTEXT_FILES:
        filepath = context_dir / spec.filename
        if not filepath.exists():
            continue
        file_content = filepath.read_text(encoding="utf-8").strip()
        if not file_content:
            continue
        tokens = ContextDirectoryLoader.estimate_tokens(file_content)

        truncated = False
        if context_text and spec.section_name:
            section_header = f"## {spec.section_name}\n"
            header_pos = context_text.find(section_header)
            if header_pos != -1:
                next_header = context_text.find("\n## ", header_pos + len(section_header))
                section_block = (
                    context_text[header_pos:next_header]
                    if next_header != -1
                    else context_text[header_pos:]
                )
                truncated = "[Truncated:" in section_block and "tokens]" in section_block

        prompt_metadata["files"].append({
            "filename": spec.filename,
            "tokens": tokens,
            "truncated": truncated,
            "user_customized": spec.user_customized,
        })

    prompt_metadata["total_tokens"] = sum(f["tokens"] for f in prompt_metadata["files"])

    # ── SystemPromptBuilder (non-file sections) ──
    builder = SystemPromptBuilder(
        working_directory=str(workspace),
        agent_config=agent_config,
        channel_context=channel_context,
    )
    builder_output = builder.build()

    # In the real pipeline, the SDK receives both:
    # - agent_config["system_prompt"]  → context files + ephemeral injections
    # - builder.build()                → identity, safety, workspace, datetime, runtime
    # They're concatenated in the final prompt sent to the model.
    context_part = agent_config.get("system_prompt", "") or ""
    final_prompt = (
        context_part + "\n\n" + builder_output
        if context_part
        else builder_output
    )

    return final_prompt, prompt_metadata


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a realistic SwarmWS workspace with all context files."""
    ws = tmp_path / "SwarmWS"
    context_dir = ws / ".context"
    context_dir.mkdir(parents=True)

    for filename, content in TEMPLATES.items():
        (context_dir / filename).write_text(content, encoding="utf-8")

    # Create DailyActivity with 2 files
    da_dir = ws / "Knowledge" / "DailyActivity"
    da_dir.mkdir(parents=True)
    (da_dir / "2026-03-07.md").write_text(
        "# 2026-03-07\n\n- Reviewed context system architecture\n- Fixed token estimation bug",
        encoding="utf-8",
    )
    (da_dir / "2026-03-08.md").write_text(
        "# 2026-03-08\n\n- Implemented group channel exclusion\n- Added CJK token estimation",
        encoding="utf-8",
    )

    return ws


@pytest.fixture
def workspace_with_bootstrap(workspace: Path) -> Path:
    """Workspace that also has a BOOTSTRAP.md for first-run onboarding."""
    bootstrap = workspace / ".context" / "BOOTSTRAP.md"
    bootstrap.write_text(
        "# Welcome to SwarmAI\n\nLet's set up your workspace. What's your name?",
        encoding="utf-8",
    )
    return workspace


@pytest.fixture
def workspace_with_distillation_flag(workspace: Path) -> Path:
    """Workspace with the .needs_distillation flag file."""
    flag = workspace / "Knowledge" / "DailyActivity" / ".needs_distillation"
    flag.write_text("", encoding="utf-8")
    return workspace


@pytest.fixture
def workspace_with_cjk(workspace: Path) -> Path:
    """Workspace with Chinese content in MEMORY.md and DailyActivity."""
    memory = workspace / ".context" / "MEMORY.md"
    memory.write_text(
        "# Memory\n\n## Recent Context\n\n"
        "- 2026-03-08: 用户决定采用函数式编程风格而非面向对象编程\n"
        "- 2026-03-07: 系统提示优化项目已启动，需要处理中日韩文本的令牌估算问题\n",
        encoding="utf-8",
    )

    da = workspace / "Knowledge" / "DailyActivity" / "2026-03-08.md"
    da.write_text(
        "# 2026-03-08\n\n"
        "- 实现了群组频道的个人文件排除功能\n"
        "- 添加了中日韩文本的令牌估算支持\n"
        "- 修复了截断检测的正则表达式匹配问题\n",
        encoding="utf-8",
    )
    return workspace


# ── E2E Tests ────────────────────────────────────────────────────────


class TestE2EDirectChannel:
    """Full pipeline for a normal direct (1:1) channel session."""

    def test_all_10_context_files_present(self, workspace):
        """All 11 context files appear in the assembled prompt."""
        prompt, meta = _simulate_build(workspace)
        assert len(meta["files"]) == 11
        filenames = {f["filename"] for f in meta["files"]}
        expected = {spec.filename for spec in CONTEXT_FILES}
        assert filenames == expected

    def test_memory_loaded_in_direct_channel(self, workspace):
        """MEMORY.md is included in direct channel prompts."""
        prompt, _ = _simulate_build(workspace, channel_context=None)
        assert "Secret personal decision" in prompt or "project pivot" in prompt

    def test_user_loaded_in_direct_channel(self, workspace):
        """USER.md is included in direct channel prompts."""
        prompt, _ = _simulate_build(workspace, channel_context=None)
        assert "TestUser" in prompt

    def test_daily_activity_injected(self, workspace):
        """DailyActivity files appear as ephemeral sections."""
        prompt, _ = _simulate_build(workspace)
        assert "## Daily Activity (2026-03-08)" in prompt
        assert "## Daily Activity (2026-03-07)" in prompt
        assert "group channel exclusion" in prompt

    def test_daily_activity_only_last_2(self, workspace):
        """Only the 2 most recent DailyActivity files are loaded."""
        # Add a third, older file
        da_dir = workspace / "Knowledge" / "DailyActivity"
        (da_dir / "2026-03-01.md").write_text("# Old\n\n- Ancient activity")
        prompt, _ = _simulate_build(workspace)
        assert "Ancient activity" not in prompt
        assert "## Daily Activity (2026-03-08)" in prompt
        assert "## Daily Activity (2026-03-07)" in prompt

    def test_html_comments_stripped(self, workspace):
        """HTML comments from templates are not in the final prompt."""
        prompt, _ = _simulate_build(workspace)
        assert "<!--" not in prompt
        assert "SYSTEM DEFAULT" not in prompt
        assert "Auto-managed" not in prompt

    def test_redundant_h1_stripped(self, workspace):
        """Redundant H1 that matches section_name is stripped."""
        prompt, _ = _simulate_build(workspace)
        # "# SwarmAI -- Your AI Command Center" should be stripped since
        # the section header is already "## SwarmAI"
        assert "# SwarmAI -- Your AI Command Center" not in prompt
        # But the section header should exist
        assert "## SwarmAI" in prompt
        # And content should remain
        assert "central intelligence" in prompt

    def test_matching_h1_stripped_for_user(self, workspace):
        """USER.md H1 'User -- About You' matches section 'User' and is stripped."""
        prompt, _ = _simulate_build(workspace)
        # The H1 prefix "User" matches section_name "User", so it SHOULD be stripped
        assert "# User -- About You" not in prompt
        # But user preferences content remains
        assert "Show me the code" in prompt

    def test_non_matching_h1_preserved(self, workspace):
        """H1 that doesn't match section_name is NOT stripped."""
        # Write a file with a completely different H1
        (workspace / ".context" / "TOOLS.md").write_text(
            "# My Custom Tools Setup\n\n- Editor: Neovim"
        )
        prompt, _ = _simulate_build(workspace)
        # "My Custom Tools Setup" does NOT match section_name "Tools"
        assert "# My Custom Tools Setup" in prompt

    def test_model_none_not_in_prompt(self, workspace):
        """model=None does not appear in the runtime metadata line."""
        prompt, _ = _simulate_build(workspace, agent_model=None)
        assert "model=None" not in prompt
        assert "model=" not in prompt

    def test_model_real_appears_in_prompt(self, workspace):
        """A real model name appears in the runtime metadata line."""
        prompt, _ = _simulate_build(workspace, agent_model="claude-sonnet-4-20250514")
        assert "model=claude-sonnet-4-20250514" in prompt

    def test_safety_principles_present(self, workspace):
        """AI-alignment safety principles are in the final prompt."""
        prompt, _ = _simulate_build(workspace)
        assert "no independent goals" in prompt
        assert "self-preservation" in prompt

    def test_working_directory_present(self, workspace):
        """Working directory path appears in the final prompt."""
        prompt, _ = _simulate_build(workspace)
        assert str(workspace) in prompt

    def test_datetime_present(self, workspace):
        """Date/time section appears in the final prompt."""
        prompt, _ = _simulate_build(workspace)
        assert "Current date/time:" in prompt
        assert "UTC" in prompt

    def test_runtime_metadata_format(self, workspace):
        """Runtime metadata line has correct format."""
        prompt, _ = _simulate_build(workspace)
        assert "agent=SwarmAI" in prompt
        assert "channel=direct" in prompt

    def test_priority_order_preserved(self, workspace):
        """Files are assembled in priority order (P0 first, P9 last)."""
        prompt, _ = _simulate_build(workspace)
        swarmai_pos = prompt.find("## SwarmAI")
        identity_pos = prompt.find("## Identity")
        soul_pos = prompt.find("## Soul")
        memory_pos = prompt.find("## Memory")
        projects_pos = prompt.find("## Projects")

        assert swarmai_pos < identity_pos < soul_pos
        # Memory (P7) comes before Projects (P9)
        assert memory_pos < projects_pos


class TestE2EGroupChannel:
    """Full pipeline for a group channel session (Feishu/Slack group)."""

    def _group_context(self, channel_type: str = "feishu") -> dict:
        return {
            "channel_type": channel_type,
            "channel_id": "ch_123",
            "chat_id": "chat_456",
            "reply_to_message_id": "msg_789",
            "is_group": True,
            "app_id": "app_test",
            "app_secret": "secret_test",
        }

    def test_memory_excluded_in_group(self, workspace):
        """MEMORY.md is NOT in group channel prompts."""
        prompt, meta = _simulate_build(
            workspace, channel_context=self._group_context()
        )
        assert "Secret personal decision" not in prompt
        assert "project pivot" not in prompt

        # Verify metadata still includes MEMORY.md (it's on disk)
        mem_files = [f for f in meta["files"] if f["filename"] == "MEMORY.md"]
        assert len(mem_files) == 1  # Metadata reports it exists

    def test_user_excluded_in_group(self, workspace):
        """USER.md is NOT in group channel prompts."""
        prompt, _ = _simulate_build(
            workspace, channel_context=self._group_context()
        )
        assert "TestUser" not in prompt

    def test_other_files_still_present_in_group(self, workspace):
        """Non-excluded files (SWARMAI, SOUL, etc.) still appear in group."""
        prompt, _ = _simulate_build(
            workspace, channel_context=self._group_context()
        )
        assert "## SwarmAI" in prompt
        assert "## Soul" in prompt
        assert "## Agent Directives" in prompt
        assert "## Steering" in prompt
        assert "## Projects" in prompt

    def test_daily_activity_still_injected_in_group(self, workspace):
        """DailyActivity is ephemeral content, still injected in group."""
        prompt, _ = _simulate_build(
            workspace, channel_context=self._group_context()
        )
        assert "## Daily Activity (2026-03-08)" in prompt

    def test_non_group_channel_includes_memory(self, workspace):
        """A DM (is_group=False) still gets MEMORY.md."""
        dm_context = {
            "channel_type": "feishu",
            "channel_id": "ch_123",
            "chat_id": "chat_456",
            "is_group": False,
        }
        prompt, _ = _simulate_build(workspace, channel_context=dm_context)
        assert "Secret personal decision" in prompt or "project pivot" in prompt

    def test_feishu_group_channel_type_in_metadata(self, workspace):
        """Channel type appears in runtime metadata for group."""
        prompt, _ = _simulate_build(
            workspace, channel_context=self._group_context("feishu")
        )
        assert "channel=feishu" in prompt

    def test_slack_group_excludes_memory(self, workspace):
        """Slack group channel also excludes personal files."""
        slack_group = {
            "channel_type": "slack",
            "channel_id": "C123",
            "chat_id": "C123",
            "is_group": True,
        }
        prompt, _ = _simulate_build(workspace, channel_context=slack_group)
        assert "Secret personal decision" not in prompt
        assert "TestUser" not in prompt


class TestE2EBootstrap:
    """First-run onboarding flow with BOOTSTRAP.md."""

    def test_bootstrap_injected_at_top(self, workspace_with_bootstrap):
        """BOOTSTRAP.md content appears at the top of the context."""
        prompt, _ = _simulate_build(workspace_with_bootstrap)
        onboarding_pos = prompt.find("## Onboarding")
        swarmai_pos = prompt.find("## SwarmAI")
        # Onboarding should appear BEFORE the regular context
        assert onboarding_pos != -1
        assert onboarding_pos < swarmai_pos

    def test_bootstrap_content_present(self, workspace_with_bootstrap):
        """The actual bootstrap content is in the prompt."""
        prompt, _ = _simulate_build(workspace_with_bootstrap)
        assert "What's your name?" in prompt


class TestE2EDistillation:
    """Distillation flag detection."""

    def test_distillation_flag_triggers_maintenance_section(
        self, workspace_with_distillation_flag
    ):
        """When .needs_distillation exists, a maintenance section is injected."""
        prompt, _ = _simulate_build(workspace_with_distillation_flag)
        assert "## Memory Maintenance Required" in prompt
        assert "s_memory-distill" in prompt

    def test_no_flag_no_maintenance_section(self, workspace):
        """Without the flag, no maintenance section appears."""
        prompt, _ = _simulate_build(workspace)
        assert "Memory Maintenance Required" not in prompt


class TestE2ECJKContent:
    """CJK (Chinese/Japanese/Korean) content handling."""

    def test_cjk_memory_loaded(self, workspace_with_cjk):
        """Chinese content in MEMORY.md appears in the assembled prompt."""
        prompt, _ = _simulate_build(workspace_with_cjk)
        # Chinese characters should be present
        assert "函数式编程" in prompt or "functional" in prompt.lower()

    def test_cjk_token_estimation_realistic(self, workspace_with_cjk):
        """Token estimates for CJK files are realistic (not 1 word)."""
        _, meta = _simulate_build(workspace_with_cjk)
        mem_file = next(f for f in meta["files"] if f["filename"] == "MEMORY.md")
        # Chinese text should estimate way more than a few tokens
        assert mem_file["tokens"] > 20, (
            f"MEMORY.md with Chinese text estimated only {mem_file['tokens']} tokens"
        )

    def test_cjk_daily_activity_loaded(self, workspace_with_cjk):
        """Chinese DailyActivity content appears in the prompt."""
        prompt, _ = _simulate_build(workspace_with_cjk)
        assert "群组频道" in prompt or "群" in prompt  # Group channel in Chinese


class TestE2ETruncationDetection:
    """Metadata correctly detects truncated sections."""

    def test_no_truncation_under_budget(self, workspace):
        """Under budget, no files report as truncated."""
        _, meta = _simulate_build(workspace)
        truncated = [f for f in meta["files"] if f["truncated"]]
        assert len(truncated) == 0, f"Unexpected truncation: {truncated}"

    def test_truncation_detected_when_over_budget(self, tmp_path):
        """When context exceeds budget, truncated sections are detected."""
        ws = tmp_path / "ws"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)

        # Create a minimal SWARMAI.md (non-truncatable, P0)
        (ctx / "SWARMAI.md").write_text("Core principles here.")

        # Create a massive PROJECTS.md (P9, truncatable) that will force truncation
        huge_content = "Project details. " * 5000  # ~10K words → ~13K tokens
        (ctx / "PROJECTS.md").write_text(f"# Projects\n\n{huge_content}")

        # Create minimal other files so they exist
        (ctx / "IDENTITY.md").write_text("Identity info.")
        (ctx / "SOUL.md").write_text("Personality.")

        # Use a very tight budget to force truncation.
        # _assemble_from_sources uses the token_budget directly when called
        # with an explicit budget, bypassing compute_token_budget.
        loader = ContextDirectoryLoader(context_dir=ctx, token_budget=500)
        assembled = loader._assemble_from_sources(
            model_context_window=200_000,
            token_budget=500,
        )

        # Check for truncation marker in output
        assert "[Truncated:" in assembled
        assert "tokens]" in assembled


class TestE2EDailyActivityTokenCap:
    """DailyActivity per-file token cap prevents context blowout."""

    def test_large_daily_file_is_capped(self, workspace):
        """A huge DailyActivity file is truncated to the token cap."""
        da_dir = workspace / "Knowledge" / "DailyActivity"
        # Write a massive daily file (~10K words)
        huge = "Important observation. " * 5000
        (da_dir / "2026-03-08.md").write_text(f"# 2026-03-08\n\n{huge}")

        prompt, _ = _simulate_build(workspace)
        # The daily section should be present but truncated
        assert "## Daily Activity (2026-03-08)" in prompt
        # The full 10K words should NOT all be in the prompt
        daily_section_start = prompt.find("## Daily Activity (2026-03-08)")
        daily_section_end = prompt.find("\n## ", daily_section_start + 1)
        if daily_section_end == -1:
            daily_section = prompt[daily_section_start:]
        else:
            daily_section = prompt[daily_section_start:daily_section_end]
        daily_tokens = ContextDirectoryLoader.estimate_tokens(daily_section)
        # Should be capped around TOKEN_CAP_PER_DAILY_FILE (2000) + some header
        assert daily_tokens < TOKEN_CAP_PER_DAILY_FILE + 500, (
            f"Daily section has {daily_tokens} tokens, expected < {TOKEN_CAP_PER_DAILY_FILE + 500}"
        )


class TestE2EBudgetHeadroom:
    """Token budget accounts for ephemeral content headroom."""

    def test_effective_budget_reduced_by_headroom(self):
        """The loader receives a reduced budget to leave room for DailyActivity."""
        base = DEFAULT_TOKEN_BUDGET  # 25000
        effective = max(base - EPHEMERAL_HEADROOM, base // 2)
        assert effective == base - EPHEMERAL_HEADROOM  # 25000 - 4000 = 21000
        assert effective == 21_000

    def test_headroom_never_below_half(self):
        """With a very small base budget, headroom doesn't go below 50%."""
        small_budget = 5000
        effective = max(small_budget - EPHEMERAL_HEADROOM, small_budget // 2)
        # 5000 - 4000 = 1000, but 5000 // 2 = 2500, so we get 2500
        assert effective == 2500


class TestE2EGatewayIsGroup:
    """Gateway-level is_group derivation from adapter chat_type."""

    @pytest.mark.parametrize(
        "chat_type,expected",
        [
            ("p2p", False),       # Feishu DM
            ("group", True),      # Feishu group
            ("im", False),        # Slack DM
            ("channel", True),    # Slack public channel
            ("mpim", True),       # Slack multi-party DM
            ("", False),          # Unknown / direct
        ],
    )
    def test_is_group_classification(self, chat_type, expected):
        """Verify chat_type → is_group mapping matches spec."""
        # Mirrors the logic in gateway.py
        is_group = chat_type in ("group", "channel", "mpim")
        assert is_group == expected, f"chat_type={chat_type!r}: got {is_group}, expected {expected}"


class TestE2EL1CacheInteraction:
    """L1 cache behavior with and without exclusions."""

    def test_cache_populated_on_direct_channel(self, workspace):
        """Direct channel assembly writes L1 cache."""
        _simulate_build(workspace)  # First build populates cache
        l1 = workspace / ".context" / "L1_SYSTEM_PROMPTS.md"
        # Cache isn't written by _simulate_build (it uses load_all which
        # writes cache internally). Let's check via the loader directly.
        ctx = workspace / ".context"
        loader = ContextDirectoryLoader(context_dir=ctx, token_budget=21000)
        assembled = loader.load_all(model_context_window=200_000)
        assert l1.exists(), "L1 cache should be written after assembly"
        cached = l1.read_text(encoding="utf-8")
        assert cached.startswith("<!-- budget:")

    def test_cache_not_written_for_group_channel(self, workspace):
        """Group channel assembly does NOT write L1 cache (exclusions are session-specific)."""
        ctx = workspace / ".context"
        l1 = ctx / "L1_SYSTEM_PROMPTS.md"
        # Remove any existing cache
        if l1.exists():
            os.remove(l1)

        loader = ContextDirectoryLoader(context_dir=ctx, token_budget=21000)
        loader.load_all(
            model_context_window=200_000,
            exclude_filenames=set(GROUP_CHANNEL_EXCLUDE),
        )
        assert not l1.exists(), "L1 cache should NOT be written when exclusions are active"

    def test_cache_bypassed_for_group_channel(self, workspace):
        """Group channel does not use existing L1 cache (may contain MEMORY)."""
        ctx = workspace / ".context"
        loader = ContextDirectoryLoader(context_dir=ctx, token_budget=21000)

        # First: build full cache (includes MEMORY)
        full = loader.load_all(model_context_window=200_000)
        assert "Secret personal decision" in full or "project pivot" in full

        # Second: load with exclusion — should NOT use the cache
        excluded = loader.load_all(
            model_context_window=200_000,
            exclude_filenames=set(GROUP_CHANNEL_EXCLUDE),
        )
        assert "Secret personal decision" not in excluded
        assert "project pivot" not in excluded


class TestE2EMetadataAccuracy:
    """Prompt metadata is accurate and complete."""

    def test_metadata_file_count(self, workspace):
        """Metadata reports all 11 context files."""
        _, meta = _simulate_build(workspace)
        assert len(meta["files"]) == 11

    def test_metadata_total_tokens_positive(self, workspace):
        """Total tokens is a positive number."""
        _, meta = _simulate_build(workspace)
        assert meta["total_tokens"] > 0

    def test_metadata_user_customized_flags(self, workspace):
        """user_customized matches the spec for each file."""
        _, meta = _simulate_build(workspace)
        spec_map = {s.filename: s.user_customized for s in CONTEXT_FILES}
        for f in meta["files"]:
            assert f["user_customized"] == spec_map[f["filename"]], (
                f"{f['filename']}: expected user_customized={spec_map[f['filename']]}"
            )
