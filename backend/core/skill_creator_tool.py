"""Autonomous skill creation tool with security gates.

Allows the agent to create new skills programmatically, with
SkillGuard scanning and optional user approval flow.

Key public symbols:
- ``SkillResult``      -- Result of skill creation attempt.
- ``SkillProposal``    -- Deferred proposal awaiting user approval.
- ``SkillCreatorTool`` -- Creates skills with validation.
"""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


@dataclass
class SkillResult:
    success: bool
    skill_name: str
    skill_path: str | None
    message: str


@dataclass
class SkillProposal:
    skill_name: str
    reason: str
    draft_content: str
    proposed_at: str
    status: str = "pending"  # pending, approved, rejected


class SkillCreatorTool:
    def __init__(self, skills_dir: Path, config: dict | None = None) -> None:
        self._skills_dir = skills_dir
        self._config = config or {}
        self._creation_count = 0

    def _validate_frontmatter(self, content: str) -> tuple[bool, str]:
        """Check YAML frontmatter is valid."""
        if not content.startswith("---"):
            return False, "Missing YAML frontmatter (must start with ---)"
        parts = content.split("---", 2)
        if len(parts) < 3:
            return False, "Invalid YAML frontmatter (missing closing ---)"
        try:
            if yaml is not None:
                meta = yaml.safe_load(parts[1])
            else:
                # Fallback: basic parsing without PyYAML
                meta = {}
                for line in parts[1].strip().split("\n"):
                    if ":" in line:
                        key = line.split(":", 1)[0].strip()
                        val = line.split(":", 1)[1].strip()
                        if key and val:
                            meta[key] = val
                if not meta:
                    meta = None
            if not isinstance(meta, dict):
                return False, "Frontmatter must be a YAML mapping"
            if "name" not in meta:
                return False, "Missing required field: name"
            if "description" not in meta:
                return False, "Missing required field: description"
        except Exception as e:
            return False, f"Invalid YAML: {e}"
        return True, "Valid"

    def create(
        self,
        name: str,
        description: str,
        trigger: str,
        instructions: str,
        category: str = "Other",
    ) -> SkillResult:
        """Create a new skill with validation and security scanning."""
        # Build content
        content = (
            f"---\nname: {name}\ndescription: >\n"
            f"  {description}\n  TRIGGER: {trigger}\n---\n\n{instructions}\n"
        )

        # Validate
        valid, msg = self._validate_frontmatter(content)
        if not valid:
            return SkillResult(
                success=False,
                skill_name=name,
                skill_path=None,
                message=f"Validation failed: {msg}",
            )

        # SkillGuard scan
        try:
            from core.skill_guard import SkillGuard, TrustLevel

            guard = SkillGuard()
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", delete=False
            ) as f:
                f.write(content)
                tmp_path = Path(f.name)
            try:
                result = guard.scan_skill(tmp_path, TrustLevel.AGENT_CREATED)
                if not result.allowed:
                    return SkillResult(
                        success=False,
                        skill_name=name,
                        skill_path=None,
                        message=f"SkillGuard blocked: {[f.pattern_name for f in result.findings]}",
                    )
            finally:
                tmp_path.unlink(missing_ok=True)
        except ImportError:
            logger.warning("SkillGuard not available, skipping scan")

        # Auto-approve check
        auto_approve = self._config.get("auto_approve_skills", False)
        if not auto_approve and self._creation_count < 3:
            return self._to_proposal(
                name, "Auto-approve disabled, requires user approval", content
            )

        # Write skill
        skill_dir = self._skills_dir / f"s_{name}"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(content, encoding="utf-8")
        self._creation_count += 1

        logger.info("Created skill: %s at %s", name, skill_path)
        return SkillResult(
            success=True,
            skill_name=name,
            skill_path=str(skill_path),
            message="Skill created successfully",
        )

    def _to_proposal(self, name: str, reason: str, content: str) -> SkillResult:
        """Convert to a proposal instead of creating directly."""
        return SkillResult(
            success=False,
            skill_name=name,
            skill_path=None,
            message=f"Proposal: {reason}. Use propose() for deferred creation.",
        )

    def propose(self, name: str, reason: str, draft: str) -> SkillProposal:
        """Create a deferred proposal without writing the skill."""
        return SkillProposal(
            skill_name=name,
            reason=reason,
            draft_content=draft,
            proposed_at=datetime.now().isoformat(),
            status="pending",
        )
