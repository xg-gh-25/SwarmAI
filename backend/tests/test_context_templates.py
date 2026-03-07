"""Unit tests for context file template content verification.

Verifies that all context file templates in ``backend/context/`` contain
the required markers, sections, directives, and that removed content is
absent. Also checks that the total system-default token count stays
within the 3,000-token budget.

Testing methodology: unit tests with direct file reads.
Key invariants:
- Each template preserves its correct marker (⚙️/👤/🤖)
- Required Chinese directives are present where specified
- Removed legacy content is absent
- System-default files fit within token budget
"""
import pytest
from pathlib import Path


TEMPLATES_DIR = Path(__file__).parent.parent / "context"


def _read_template(filename: str) -> str:
    """Read a template file and return its content."""
    path = TEMPLATES_DIR / filename
    assert path.is_file(), f"Template {filename} not found at {path}"
    return path.read_text(encoding="utf-8")


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: word count * 1.3 (conservative for mixed CJK/English)."""
    words = text.split()
    return int(len(words) * 1.3)


# ---------------------------------------------------------------------------
# Marker verification
# ---------------------------------------------------------------------------

class TestTemplateMarkers:
    """Verify each template contains its correct inline comment marker."""

    @pytest.mark.parametrize("filename", [
        "SWARMAI.md", "IDENTITY.md", "SOUL.md", "AGENT.md",
    ])
    def test_system_default_marker(self, filename: str):
        content = _read_template(filename)
        assert "⚙️ SYSTEM DEFAULT" in content, (
            f"{filename} missing ⚙️ SYSTEM DEFAULT marker"
        )

    @pytest.mark.parametrize("filename", [
        "USER.md", "STEERING.md", "TOOLS.md", "KNOWLEDGE.md", "PROJECTS.md",
    ])
    def test_user_customized_marker(self, filename: str):
        content = _read_template(filename)
        assert "✏️ YOUR FILE" in content, (
            f"{filename} missing ✏️ YOUR FILE marker"
        )

    def test_memory_agent_managed_marker(self):
        content = _read_template("MEMORY.md")
        assert "🧠 MEMORY" in content, (
            "MEMORY.md missing 🧠 MEMORY marker"
        )


# ---------------------------------------------------------------------------
# SOUL.md content verification
# ---------------------------------------------------------------------------

class TestSoulTemplate:
    """Verify SOUL.md has required OpenClaw-inspired content."""

    def test_chinese_framing(self):
        content = _read_template("SOUL.md")
        assert "You're not a chatbot" in content
        assert "becoming someone" in content

    def test_good_bad_examples(self):
        content = _read_template("SOUL.md")
        assert "Good:" in content
        assert "Bad:" in content

    def test_continuity_section(self):
        content = _read_template("SOUL.md")
        assert "## Continuity" in content
        assert "context files ARE your memory" in content


# ---------------------------------------------------------------------------
# IDENTITY.md content verification
# ---------------------------------------------------------------------------

class TestIdentityTemplate:
    """Verify IDENTITY.md has avatar field and evolving identity guidance."""

    def test_avatar_field(self):
        content = _read_template("IDENTITY.md")
        assert "Avatar:" in content or "avatar" in content.lower()

    def test_evolving_identity(self):
        content = _read_template("IDENTITY.md")
        assert "Evolving Identity" in content or "evolving" in content.lower()


# ---------------------------------------------------------------------------
# AGENT.md content verification
# ---------------------------------------------------------------------------

class TestAgentTemplate:
    """Verify AGENT.md has write-it-down directive, safety rules, and channel behavior."""

    def test_write_it_down_directive(self):
        content = _read_template("AGENT.md")
        assert "Write it down" in content

    def test_trash_over_rm_rule(self):
        content = _read_template("AGENT.md")
        assert "trash > rm" in content

    def test_channel_behavior_section(self):
        content = _read_template("AGENT.md")
        assert "## Channel Behavior" in content

    def test_channel_feishu(self):
        content = _read_template("AGENT.md")
        assert "Feishu" in content

    def test_channel_slack(self):
        content = _read_template("AGENT.md")
        assert "Slack" in content

    def test_channel_cli(self):
        content = _read_template("AGENT.md")
        assert "CLI" in content

    def test_channel_web(self):
        content = _read_template("AGENT.md")
        assert "Web" in content

    def test_memory_writing_rules(self):
        content = _read_template("AGENT.md")
        assert "DailyActivity" in content
        assert "MEMORY.md" in content


# ---------------------------------------------------------------------------
# USER.md content verification
# ---------------------------------------------------------------------------

class TestUserTemplate:
    """Verify USER.md has Background section and humanistic footer."""

    def test_background_section(self):
        content = _read_template("USER.md")
        assert "## Background" in content

    def test_humanistic_footer(self):
        content = _read_template("USER.md")
        assert "getting to know a person" in content
        assert "not building a dossier" in content


# ---------------------------------------------------------------------------
# STEERING.md content verification
# ---------------------------------------------------------------------------

class TestSteeringTemplate:
    """Verify STEERING.md has revised Memory Protocol and updated structure."""

    def test_write_it_down_directive(self):
        """Write-it-down directive now lives in AGENT.md only; STEERING extends with two-tier details."""
        content = _read_template("STEERING.md")
        assert "Two-tier model" in content or "two-tier" in content.lower()

    def test_no_mental_notes(self):
        content = _read_template("STEERING.md")
        assert "note important discoveries mentally" not in content

    def test_two_tier_model(self):
        content = _read_template("STEERING.md")
        assert "DailyActivity" in content
        assert "Two-tier model" in content or "two-tier" in content.lower()

    def test_distillation_rules(self):
        content = _read_template("STEERING.md")
        assert "Distillation" in content or "distill" in content.lower()

    def test_updated_directory_structure(self):
        """Directory structure now lives in AGENT.md; STEERING only has user overrides."""
        content = _read_template("AGENT.md")
        assert "TOOLS.md" in content
        assert "Library/" in content or "Library" in content
        assert "DailyActivity/" in content or "DailyActivity" in content
        assert "Archives/" in content or "Archives" in content

    def test_no_knowledge_base_reference(self):
        content = _read_template("STEERING.md")
        assert "Knowledge Base/" not in content

    def test_file_saving_rules(self):
        """File routing rules now live in AGENT.md Workspace Layout section."""
        content = _read_template("AGENT.md")
        assert "Workspace Layout" in content
        assert "Knowledge/" in content


# ---------------------------------------------------------------------------
# MEMORY.md content verification
# ---------------------------------------------------------------------------

class TestMemoryTemplate:
    """Verify MEMORY.md has two-tier model guidance and distillation."""

    def test_two_tier_guidance(self):
        content = _read_template("MEMORY.md")
        assert "DailyActivity" in content
        assert "two-tier" in content.lower() or "Two-tier" in content

    def test_distillation_instructions(self):
        content = _read_template("MEMORY.md")
        assert "distill" in content.lower()


# ---------------------------------------------------------------------------
# KNOWLEDGE.md content verification
# ---------------------------------------------------------------------------

class TestKnowledgeTemplate:
    """Verify KNOWLEDGE.md is restructured as Knowledge Directory index."""

    def test_no_old_sections(self):
        content = _read_template("KNOWLEDGE.md")
        assert "## Tech Stack" not in content
        assert "## Coding Conventions" not in content
        assert "## Architecture Notes" not in content
        assert "## Reference" not in content

    def test_subfolder_sections(self):
        content = _read_template("KNOWLEDGE.md")
        assert "Notes" in content
        assert "Reports" in content
        assert "Meetings" in content
        assert "Library" in content
        assert "Archives" in content
        assert "DailyActivity" in content

    def test_index_guidance(self):
        content = _read_template("KNOWLEDGE.md")
        assert "index" in content.lower() or "Index" in content


# ---------------------------------------------------------------------------
# PROJECTS.md content verification
# ---------------------------------------------------------------------------

class TestProjectsTemplate:
    """Verify PROJECTS.md has project folder linking guidance."""

    def test_folder_linking(self):
        content = _read_template("PROJECTS.md")
        assert "SwarmWS/Projects/" in content or "Projects/" in content
        assert "Folder:" in content or "folder" in content.lower()


# ---------------------------------------------------------------------------
# Token budget verification
# ---------------------------------------------------------------------------

class TestTokenBudget:
    """Verify total system-default token count stays within budget."""

    SYSTEM_DEFAULT_FILES = ["SWARMAI.md", "IDENTITY.md", "SOUL.md", "AGENT.md"]

    def test_total_system_default_tokens_under_3000(self):
        """Req 13.20: system-default files must total ≤ 3,000 tokens."""
        total = 0
        for filename in self.SYSTEM_DEFAULT_FILES:
            content = _read_template(filename)
            tokens = _estimate_tokens(content)
            total += tokens
        assert total <= 3000, (
            f"System-default files total {total} tokens, exceeds 3,000 budget"
        )

    @pytest.mark.parametrize("filename", SYSTEM_DEFAULT_FILES)
    def test_individual_file_reasonable_size(self, filename: str):
        """Each system-default file should be under 1,500 tokens individually."""
        content = _read_template(filename)
        tokens = _estimate_tokens(content)
        assert tokens <= 1500, (
            f"{filename} is {tokens} tokens, exceeds 1,500 individual limit"
        )


# ---------------------------------------------------------------------------
# Session-start Open Threads verification (Req 5.2, 5.3)
# ---------------------------------------------------------------------------

SKILLS_DIR = Path(__file__).parent.parent / "skills"


class TestSessionStartOpenThreads:
    """Verify AGENT.md and STEERING.md use session-start Open Threads review."""

    def test_agent_md_session_start_open_threads(self):
        """Req 5.2: AGENT.md contains 'At session start' directive for Open Threads."""
        content = _read_template("AGENT.md")
        assert "At session start" in content, (
            "AGENT.md missing 'At session start' directive"
        )
        assert "Open Threads" in content, (
            "AGENT.md missing 'Open Threads' reference"
        )

    def test_agent_md_no_session_end_open_threads(self):
        """Req 5.2: AGENT.md should NOT have 'At session end' for Open Threads."""
        content = _read_template("AGENT.md")
        assert "At session end" not in content, (
            "AGENT.md still contains 'At session end' directive"
        )

    def test_steering_md_extended_memory_protocol(self):
        """Req 5.3: STEERING.md contains extended memory protocol (distillation rules)."""
        content = _read_template("STEERING.md")
        assert "Memory Protocol" in content, (
            "STEERING.md missing 'Memory Protocol' section"
        )
        assert "extend" in content.lower(), (
            "STEERING.md should reference extending AGENT.md base rules"
        )

    def test_steering_md_no_session_end_block(self):
        """Req 5.3: STEERING.md should NOT have 'At session end (if asked)' block."""
        content = _read_template("STEERING.md")
        assert "At session end (if asked)" not in content, (
            "STEERING.md still contains 'At session end (if asked)' block"
        )

    def test_steering_md_distillation_in_place(self):
        """Req 5.5: STEERING.md distillation marks files in place, not moves."""
        content = _read_template("STEERING.md")
        assert "frontmatter in place" in content, (
            "STEERING.md missing 'frontmatter in place' distillation directive"
        )


# ---------------------------------------------------------------------------
# Distillation skill verification (Req 3.1, 3.6)
# ---------------------------------------------------------------------------

class TestDistillationSkill:
    """Verify s_memory-distill/SKILL.md exists and contains required content."""

    def test_skill_file_exists(self):
        """Req 3.1: SKILL.md exists at the expected path."""
        skill_path = SKILLS_DIR / "s_memory-distill" / "SKILL.md"
        assert skill_path.is_file(), (
            f"Distillation skill not found at {skill_path}"
        )

    def test_skill_has_yaml_frontmatter(self):
        """SKILL.md has YAML frontmatter with name and description."""
        content = (SKILLS_DIR / "s_memory-distill" / "SKILL.md").read_text()
        assert content.startswith("---"), "SKILL.md missing YAML frontmatter"
        assert "name: Memory Distill" in content

    def test_skill_has_detection_section(self):
        """Req 3.2/3.3: SKILL.md has Detection section with threshold."""
        content = (SKILLS_DIR / "s_memory-distill" / "SKILL.md").read_text()
        assert "Detection" in content
        assert "≤ 7" in content or "<= 7" in content or "≤7" in content

    def test_skill_has_extraction_section(self):
        """Req 3.4: SKILL.md has Extraction section."""
        content = (SKILLS_DIR / "s_memory-distill" / "SKILL.md").read_text()
        assert "Extraction" in content
        assert "key decisions" in content.lower()
        assert "lessons" in content.lower()

    def test_skill_has_writing_section(self):
        """Req 3.5/3.8: SKILL.md has Writing section with locked_write."""
        content = (SKILLS_DIR / "s_memory-distill" / "SKILL.md").read_text()
        assert "Writing" in content or "MEMORY.md" in content
        assert "locked_write" in content

    def test_skill_has_marking_section(self):
        """Req 3.6: SKILL.md references distilled: true frontmatter marking."""
        content = (SKILLS_DIR / "s_memory-distill" / "SKILL.md").read_text()
        assert "distilled: true" in content
        assert "distilled_date" in content

    def test_skill_has_archiving_section(self):
        """Req 3.7: SKILL.md has Archiving section with age thresholds."""
        content = (SKILLS_DIR / "s_memory-distill" / "SKILL.md").read_text()
        assert "Archiving" in content or "Archives" in content
        assert "30" in content  # 30-day threshold
        assert "90" in content  # 90-day threshold

    def test_skill_has_open_threads_section(self):
        """Req 5.4: SKILL.md has Open Threads cross-reference."""
        content = (SKILLS_DIR / "s_memory-distill" / "SKILL.md").read_text()
        assert "Open Threads" in content

    def test_skill_is_silent(self):
        """Req 3.9: SKILL.md specifies silent operation."""
        content = (SKILLS_DIR / "s_memory-distill" / "SKILL.md").read_text()
        assert "silent" in content.lower()

    def test_skill_has_fallback_section(self):
        """Req 3.5: SKILL.md has fallback ## Distilled section."""
        content = (SKILLS_DIR / "s_memory-distill" / "SKILL.md").read_text()
        assert "Distilled" in content
        assert "fallback" in content.lower()
