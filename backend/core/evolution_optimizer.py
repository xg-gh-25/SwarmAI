"""Heuristic skill optimization via correction pattern analysis.

Analyzes eval examples where users corrected the agent's output,
extracts actionable patterns, and suggests SKILL.md text changes.
DSPy/GEPA integration is optional -- falls back to heuristic.

Key public symbols:
- ``OptimizationResult``  -- Result of optimization attempt.
- ``TextChange``          -- A single text replacement.
- ``EvolutionOptimizer``  -- Orchestrates skill optimization.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TextChange:
    original: str
    replacement: str
    reason: str


@dataclass
class OptimizationResult:
    skill_name: str
    original_score: float
    optimized_score: float    # Estimated after changes
    changes: list[TextChange]
    accepted: bool            # True if passed constraint gates
    reason: str               # Why accepted/rejected


CORRECTION_PATTERNS = [
    # "don't X" -> remove X from instructions
    (re.compile(r"(?:don'?t|stop|never|avoid)\s+(.{5,60})", re.I), "remove"),
    # "use Y instead" -> add Y
    (re.compile(r"(?:use|prefer|try)\s+(.{5,60})\s+instead", re.I), "add"),
    # "should X" -> ensure X is in instructions
    (re.compile(r"(?:should|must|always)\s+(.{5,60})", re.I), "add"),
]


class EvolutionOptimizer:
    def __init__(self, skills_dir: Path) -> None:
        self._skills_dir = skills_dir

    def _read_skill_text(self, skill_name: str) -> str | None:
        """Read SKILL.md body text (below YAML frontmatter)."""
        path = self._skills_dir / f"s_{skill_name}" / "SKILL.md"
        if not path.exists():
            return None
        content = path.read_text(encoding="utf-8")
        # Skip YAML frontmatter (between --- markers)
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
        return content

    def _extract_corrections(self, examples: list) -> list[tuple[str, str]]:
        """Extract (correction_text, pattern_type) from examples with user corrections."""
        corrections: list[tuple[str, str]] = []
        for ex in examples:
            if ex.user_correction:
                for pattern, action_type in CORRECTION_PATTERNS:
                    match = pattern.search(ex.user_correction)
                    if match:
                        corrections.append((match.group(1).strip(), action_type))
        return corrections

    def _apply_heuristic_changes(
        self, skill_text: str, corrections: list[tuple[str, str]]
    ) -> tuple[str, list[TextChange]]:
        """Apply correction patterns to skill text. Returns (new_text, changes)."""
        changes: list[TextChange] = []
        new_text = skill_text
        for correction, action_type in corrections:
            if action_type == "remove":
                # Find similar phrase in skill text and mark for removal
                if correction.lower() in new_text.lower():
                    idx = new_text.lower().index(correction.lower())
                    original = new_text[idx : idx + len(correction)]
                    new_text = new_text[:idx] + new_text[idx + len(correction) :]
                    changes.append(TextChange(
                        original=original,
                        replacement="",
                        reason=f"User said don't: '{correction}'",
                    ))
            elif action_type == "add":
                # Append to instructions
                addition = f"\n- {correction}"
                new_text += addition
                changes.append(TextChange(
                    original="",
                    replacement=addition.strip(),
                    reason=f"User said should: '{correction}'",
                ))
        return new_text, changes

    def _validate_constraints(
        self, skill_name: str, new_text: str, original_text: str
    ) -> tuple[bool, str]:
        """Check constraint gates: size, growth, no injection."""
        # Size check: 15KB max
        if len(new_text.encode("utf-8")) > 15 * 1024:
            return False, f"Exceeds 15KB limit ({len(new_text.encode('utf-8'))} bytes)"

        # Growth check: 20% max
        if original_text:
            growth = (len(new_text) - len(original_text)) / max(len(original_text), 1)
            if growth > 0.20:
                return False, f"Growth {growth:.0%} exceeds 20% limit"

        # Injection check via SkillGuard
        try:
            from core.skill_guard import SCAN_PATTERNS
            for cat, patterns in SCAN_PATTERNS.items():
                for name, pat, sev in patterns:
                    if pat.search(new_text):
                        if sev == "high":
                            return False, f"Injection detected: {name}"
        except ImportError:
            pass  # SkillGuard not available, skip check

        return True, "All constraints passed"

    def optimize_skill(self, skill_name: str, eval_examples: list) -> OptimizationResult:
        """Run heuristic optimization on a skill."""
        original_text = self._read_skill_text(skill_name)
        if original_text is None:
            return OptimizationResult(
                skill_name=skill_name,
                original_score=0.0,
                optimized_score=0.0,
                changes=[],
                accepted=False,
                reason=f"Skill {skill_name} not found",
            )

        corrections = self._extract_corrections(eval_examples)
        if not corrections:
            return OptimizationResult(
                skill_name=skill_name,
                original_score=0.0,
                optimized_score=0.0,
                changes=[],
                accepted=False,
                reason="No correction patterns found",
            )

        new_text, changes = self._apply_heuristic_changes(original_text, corrections)

        if not changes:
            return OptimizationResult(
                skill_name=skill_name,
                original_score=0.0,
                optimized_score=0.0,
                changes=[],
                accepted=False,
                reason="No applicable changes found",
            )

        # Score improvement estimate
        from core.skill_fitness import SkillFitnessEvaluator

        evaluator = SkillFitnessEvaluator()
        corrections_str = str(corrections)
        original_score = evaluator.score(corrections_str, original_text).overall
        optimized_score = evaluator.score(corrections_str, new_text).overall

        passed, reason = self._validate_constraints(skill_name, new_text, original_text)

        return OptimizationResult(
            skill_name=skill_name,
            original_score=original_score,
            optimized_score=optimized_score,
            changes=changes,
            accepted=passed,
            reason=reason,
        )
