"""Round-trip frontmatter field preservation for skill_manager.

Guards the bug where ``format_skill_md`` emitted only ``name/description/version``
and thus DROPPED every other frontmatter key (``tier``, ``platform``,
``disable-model-invocation``, ``project_scope``, ``trigger``, ``do_not_use``,
``consumes_artifacts``, ``produces_artifact``, …) on ``update_skill`` write-back.

Root cause + fix: format_skill_md must accept the full original frontmatter dict
and override ONLY name/description/version, preserving all other keys in order.
(run_3467799d — surfaced by the disable-model-invocation adoption's adversarial gate.)

Methodology: mutation-proof — each assertion fails RED against the pre-fix code
(3-key hardcoded meta) and GREEN after the preserve-unknown-fields fix.
"""

from __future__ import annotations

import pytest

from core.skill_manager import (
    SkillManager,
    format_skill_md,
    parse_frontmatter,
)


class TestFormatSkillMdPreservesFields:
    """format_skill_md must preserve all frontmatter keys, override only 3."""

    def test_preserves_unknown_frontmatter_keys(self):
        # AC2: a full meta dict with extras must round-trip intact.
        meta = {
            "name": "swarm-build",
            "disable-model-invocation": True,
            "tier": "lazy",
            "platform": "desktop",
            "project_scope": "SwarmAI",
            "description": "old desc",
            "version": "1.0.0",
        }
        out = format_skill_md(
            meta=meta,
            content="# Body\n",
        )
        parsed = _frontmatter_of(out)
        # the 3 overridable keys reflect meta values
        assert parsed["name"] == "swarm-build"
        assert parsed["description"] == "old desc"
        assert parsed["version"] == "1.0.0"
        # the extras MUST survive (this is what the bug dropped)
        assert parsed["disable-model-invocation"] is True
        assert parsed["tier"] == "lazy"
        assert parsed["platform"] == "desktop"
        assert parsed["project_scope"] == "SwarmAI"

    def test_preserves_key_order(self):
        # AC2: original insertion order is kept (sort_keys=False semantics).
        meta = {
            "name": "x",
            "tier": "lazy",
            "platform": "all",
            "disable-model-invocation": True,
            "description": "d",
            "version": "2.0.0",
        }
        out = format_skill_md(meta=meta, content="body")
        # keys appear in the frontmatter in the same relative order they were given
        fm = out.split("---")[1]
        positions = [fm.index(k) for k in ("name", "tier", "platform",
                                           "disable-model-invocation")]
        assert positions == sorted(positions), "frontmatter key order not preserved"

    def test_overrides_name_description_version_from_meta(self):
        # AC2: when meta carries the 3 core keys, they are emitted as-is.
        meta = {"name": "foo", "description": "bar", "version": "3.1.4",
                "custom_key": "kept"}
        parsed = _frontmatter_of(format_skill_md(meta=meta, content="c"))
        assert parsed["name"] == "foo"
        assert parsed["description"] == "bar"
        assert parsed["version"] == "3.1.4"
        assert parsed["custom_key"] == "kept"

    def test_name_lowercasing_preserved(self):
        # AC3: name must still be lowercased for SDK slash-command matching.
        meta = {"name": "SwarmBuild", "description": "d", "version": "1.0.0"}
        parsed = _frontmatter_of(format_skill_md(meta=meta, content="c"))
        assert parsed["name"] == "swarmbuild"

    def test_fresh_skill_three_keys(self):
        # AC4: a brand-new skill (create path) with no extras still produces
        # a valid 3-key frontmatter.
        meta = {"name": "newone", "description": "fresh", "version": "1.0.0"}
        parsed = _frontmatter_of(format_skill_md(meta=meta, content="body"))
        assert parsed == {"name": "newone", "description": "fresh",
                          "version": "1.0.0"}


class TestUpdateSkillRoundTrip:
    """update_skill must not drop extra frontmatter on write-back."""

    @pytest.mark.asyncio
    async def test_update_preserves_extra_fields(self, tmp_path):
        # AC1: a user skill carrying dmi+tier+platform survives an update
        # that only changes the description.
        user_skills = tmp_path / "skills"
        skill_dir = user_skills / "ops-thing"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: ops-thing\n"
            "disable-model-invocation: true\n"
            "tier: lazy\n"
            "platform: desktop\n"
            "description: original description\n"
            "version: 1.0.0\n"
            "---\n\n"
            "# Ops Thing\nBody content.\n",
            encoding="utf-8",
        )

        mgr = SkillManager(user_skills_path=user_skills)
        await mgr.update_skill(
            folder_name="ops-thing",
            description="a brand new description",
        )

        meta, _ = parse_frontmatter(skill_dir / "SKILL.md")
        # the updated field changed…
        assert meta["description"] == "a brand new description"
        # …and every extra field SURVIVED (the bug dropped these)
        assert meta.get("disable-model-invocation") is True, \
            "disable-model-invocation was dropped on update"
        assert meta.get("tier") == "lazy", "tier was dropped on update"
        assert meta.get("platform") == "desktop", "platform was dropped on update"

    @pytest.mark.asyncio
    async def test_update_backfills_missing_required_keys(self, tmp_path):
        # Regression guard (Gate-2 MEDIUM): the raw parse_frontmatter path must
        # NOT lose the self-healing fallbacks the old parse_skill_md path had.
        # A hand-edited file missing name/description/version must still be
        # written back WITH them (else it fails scripts/lint_skills.py CI).
        user_skills = tmp_path / "skills"
        skill_dir = user_skills / "bare-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "tier: lazy\n"
            "platform: all\n"
            "---\n\n"
            "# Bare\nNo name/description/version in frontmatter.\n",
            encoding="utf-8",
        )

        mgr = SkillManager(user_skills_path=user_skills)
        await mgr.update_skill(folder_name="bare-skill", content="# Updated\n")

        meta, _ = parse_frontmatter(skill_dir / "SKILL.md")
        # required keys self-healed (mirrors old parse_skill_md fallbacks)
        assert meta.get("name") == "bare-skill", "name not backfilled"
        assert meta.get("description") == "Skill: bare-skill", \
            "description not backfilled"
        assert meta.get("version") == "1.0.0", "version not backfilled"
        # and the extras still survive
        assert meta.get("tier") == "lazy"
        assert meta.get("platform") == "all"


def _frontmatter_of(skill_md: str) -> dict:
    """Parse the YAML frontmatter block out of a format_skill_md() string."""
    import yaml

    assert skill_md.startswith("---\n")
    block = skill_md.split("---", 2)[1]
    return yaml.safe_load(block)
