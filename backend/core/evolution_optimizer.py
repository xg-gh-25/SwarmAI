"""Heuristic skill optimization via correction pattern analysis.

Analyzes eval examples where users corrected the agent's output,
extracts actionable patterns, and suggests SKILL.md text changes.
DSPy/GEPA integration is optional -- falls back to heuristic.

Key public symbols:
- ``OptimizationResult``  -- Result of optimization attempt.
- ``TextChange``          -- A single text replacement.
- ``EvolutionOptimizer``  -- Orchestrates skill optimization.
- ``run_evolution_cycle`` -- Convenience function for full mine-score-optimize cycle.
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
                # Find similar phrase in skill text using re.search for
                # safe case-insensitive matching (handles non-ASCII correctly).
                match = re.search(re.escape(correction), new_text, re.IGNORECASE)
                if match:
                    original = match.group()
                    new_text = new_text[:match.start()] + new_text[match.end():]
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

        # Injection check via SkillGuard (uses full scan with trust gate)
        try:
            import tempfile as _tmpfile
            from core.skill_guard import SkillGuard, TrustLevel
            guard = SkillGuard()
            tmp = _tmpfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
            tmp_path = Path(tmp.name)
            try:
                tmp.write(new_text)
                tmp.close()
                result = guard.scan_skill(tmp_path, TrustLevel.AGENT_CREATED)
                if not result.allowed:
                    high_findings = [f.pattern_name for f in result.findings if f.severity == "high"]
                    return False, f"SkillGuard blocked: {high_findings}"
            finally:
                tmp_path.unlink(missing_ok=True)
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

        # Score improvement estimate: build a natural language "expected"
        # string from the correction texts (not Python repr).
        from core.skill_fitness import SkillFitnessEvaluator

        evaluator = SkillFitnessEvaluator()
        expected_text = " ".join(text for text, _ in corrections)
        original_score = evaluator.score(expected_text, original_text).overall
        optimized_score = evaluator.score(expected_text, new_text).overall

        passed, reason = self._validate_constraints(skill_name, new_text, original_text)

        return OptimizationResult(
            skill_name=skill_name,
            original_score=original_score,
            optimized_score=optimized_score,
            changes=changes,
            accepted=passed,
            reason=reason,
        )


def run_evolution_cycle(skills_dir: Path, transcripts_dir: Path, evals_dir: Path) -> dict:
    """Run a full evolution cycle: mine -> score -> optimize for all eligible skills.

    Returns summary dict with {skills_checked, eligible, optimized, changes}.
    Can be invoked manually or from a scheduled job.

    Steps:
    1. Creates SessionMiner, mines all skills for eval examples.
    2. For each eligible skill (>=5 examples), runs SkillFitnessEvaluator.
    3. For skills scoring < 0.7, runs EvolutionOptimizer.
    4. Returns summary.
    """
    from core.session_miner import SessionMiner
    from core.skill_fitness import SkillFitnessEvaluator

    miner = SessionMiner(transcripts_dir, skills_dir, evals_dir)
    optimizer = EvolutionOptimizer(skills_dir)
    evaluator = SkillFitnessEvaluator()

    # Step 1: Mine all skills
    all_examples = miner.mine_all()
    skills_checked = len(all_examples)

    # Step 2: Filter eligible (>=5 examples)
    eligible_skills: list[str] = []
    for name, examples in all_examples.items():
        if len(examples) >= 5:
            eligible_skills.append(name)

    # Step 3: Score and optimize
    optimized_count = 0
    total_changes = 0
    results: list[OptimizationResult] = []

    for skill_name in eligible_skills:
        examples = all_examples[skill_name]

        # Score current fitness using correction examples
        score_pairs = []
        for ex in examples:
            if ex.user_correction:
                score_pairs.append((ex.user_correction, ex.agent_actions))
        avg_score = evaluator.score_batch(score_pairs) if score_pairs else 1.0

        if avg_score < 0.7:
            result = optimizer.optimize_skill(skill_name, examples)
            results.append(result)
            if result.accepted and result.changes:
                optimized_count += 1
                total_changes += len(result.changes)
                # Save eval examples for audit trail
                miner.save_evals(skill_name, examples)
                logger.info(
                    "Evolution cycle: optimized %s (score %.2f -> %.2f, %d changes)",
                    skill_name, result.original_score, result.optimized_score,
                    len(result.changes),
                )

    summary = {
        "skills_checked": skills_checked,
        "eligible": len(eligible_skills),
        "optimized": optimized_count,
        "changes": total_changes,
    }
    logger.info("Evolution cycle complete: %s", summary)
    return summary
