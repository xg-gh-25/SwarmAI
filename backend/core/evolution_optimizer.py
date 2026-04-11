"""Heuristic skill optimization via correction pattern analysis.

Analyzes eval examples where users corrected the agent's output,
extracts actionable patterns, and suggests SKILL.md text changes.
Uses heuristic-based optimization (correction-pattern matching and
term-overlap fitness scoring). Designed with extensible interfaces for
future ML-based optimization if needed.

Key public symbols:
- ``OptimizationResult``  -- Result of optimization attempt.
- ``TextChange``          -- A single text replacement.
- ``EvolutionOptimizer``  -- Orchestrates skill optimization.
- ``run_evolution_cycle`` -- Convenience function for full mine-score-optimize cycle.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
        """Apply correction patterns to skill text. Returns (new_text, changes).

        Quality gates:
        - Max 3 changes per optimization pass (prevent runaway appends)
        - Dedup: skip corrections already present in skill text
        - Completeness: reject fragments (mid-word truncation, <15 chars)
        - Coherence: reject if it looks like code, a path, or agent monologue
        """
        changes: list[TextChange] = []
        new_text = skill_text
        skill_lower = skill_text.lower()
        max_changes = 3

        for correction, action_type in corrections:
            if len(changes) >= max_changes:
                break

            # Quality gate: reject garbage fragments
            if not self._is_quality_correction(correction, skill_lower):
                continue

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

    @staticmethod
    def _is_quality_correction(text: str, existing_skill_lower: str) -> bool:
        """Quality gate: reject low-quality correction fragments.

        Returns True only if the correction is actionable, complete, and novel.
        """
        # Too short — likely a garbage fragment
        if len(text.strip()) < 15:
            return False

        # Truncated mid-word (no sentence-ending punctuation and ends abruptly)
        stripped = text.strip()
        if stripped and stripped[-1].isalpha() and len(stripped) > 30:
            # Last word incomplete — check if it ends without natural boundary
            last_word = stripped.split()[-1] if stripped.split() else ""
            if len(last_word) < 3:
                return False

        # Already present in skill text (dedup)
        if text.strip().lower() in existing_skill_lower:
            return False

        # Looks like code, a file path, or line number reference (not a directive)
        code_indicators = (
            "line ", "def ", "class ", "import ", "from ", "return ",
            ".py", ".ts", ".js", "self.", "this.", "→", "→",
        )
        lower = text.lower()
        if any(indicator in lower for indicator in code_indicators):
            return False

        # Agent monologue leaked as correction
        agent_indicators = (
            "let me ", "i'll ", "i need to ", "confirmed —", "verified —",
            "checking ", "looking at ", "reading ", "found ",
        )
        if any(lower.startswith(ind) for ind in agent_indicators):
            return False

        return True

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

    def deploy_optimization(self, result: OptimizationResult) -> bool:
        """Write accepted optimization to SKILL.md and log to EVOLUTION.md.

        1. Read current SKILL.md (full content including YAML frontmatter).
        2. Apply changes to body text (below frontmatter).
        3. Write back to file.
        4. Log to EVOLUTION.md K-entry (if EVOLUTION.md exists).
        5. Return True if deployed successfully.
        """
        if not result.accepted or not result.changes:
            return False

        skill_path = self._skills_dir / f"s_{result.skill_name}" / "SKILL.md"
        if not skill_path.exists():
            logger.warning("Cannot deploy: %s not found", skill_path)
            return False

        try:
            content = skill_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", skill_path, exc)
            return False

        # Backup original before applying changes — enables manual rollback.
        # Cleaned up after successful write (no stale .bak files left behind).
        backup_path = skill_path.with_suffix(".md.bak")
        try:
            backup_path.write_text(content, encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot create backup %s: %s", backup_path, exc)
            # Continue anyway — backup is best-effort, not blocking

        # Split into frontmatter and body
        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[0] + "---" + parts[1] + "---"
            body = parts[2]
        else:
            frontmatter = ""
            body = content

        # Apply each change to the body
        new_body = body
        for change in result.changes:
            if change.original and change.replacement:
                # Replace
                new_body = new_body.replace(change.original, change.replacement, 1)
            elif change.original and not change.replacement:
                # Remove
                new_body = new_body.replace(change.original, "", 1)
            elif not change.original and change.replacement:
                # Add (append)
                new_body = new_body.rstrip() + "\n" + change.replacement + "\n"

        # Write back
        new_content = frontmatter + new_body if frontmatter else new_body
        try:
            skill_path.write_text(new_content, encoding="utf-8")
            logger.info(
                "Deployed %d changes to %s (score %.2f -> %.2f)",
                len(result.changes),
                skill_path.name,
                result.original_score,
                result.optimized_score,
            )
            # Clean up backup after successful write — no stale .bak files
            backup_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Cannot write %s: %s", skill_path, exc)
            return False

        # Log to EVOLUTION.md if it exists
        self._log_to_evolution(result)

        return True

    def _log_to_evolution(self, result: OptimizationResult) -> None:
        """Append an optimization entry to EVOLUTION.md (best-effort).

        Uses config-based workspace path resolution first, falls back to
        relative path heuristics from skills_dir for robustness.
        """
        try:
            evo_path = None
            # Preferred: resolve via app config (works regardless of skills_dir location)
            try:
                from core.app_config_manager import app_config_manager
                if app_config_manager is not None:
                    ws_path = app_config_manager.get("workspace_path")
                    if ws_path:
                        candidate = Path(ws_path) / ".context" / "EVOLUTION.md"
                        if candidate.is_file():
                            evo_path = candidate
            except (ImportError, Exception):
                pass  # config not available — fall through to heuristics

            # Fallback: relative path heuristics from skills_dir
            if evo_path is None:
                for candidate in [
                    self._skills_dir.parent.parent / ".context" / "EVOLUTION.md",
                    self._skills_dir.parent / ".context" / "EVOLUTION.md",
                ]:
                    if candidate.is_file():
                        evo_path = candidate
                        break

            if evo_path is None:
                return

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            changes_summary = "; ".join(c.reason for c in result.changes[:3])
            entry = (
                f"\n- **[{today}]** Auto-optimized `{result.skill_name}` "
                f"(score {result.original_score:.2f} -> {result.optimized_score:.2f}): "
                f"{changes_summary}\n"
            )

            # Append to Competence Learned section if it exists, else append at end
            content = evo_path.read_text(encoding="utf-8")
            if "## Competence Learned" in content:
                from scripts.locked_write import locked_read_modify_write
                locked_read_modify_write(evo_path, "Competence Learned", entry, "append")
            else:
                with open(evo_path, "a", encoding="utf-8") as f:
                    f.write(entry)
        except Exception as exc:
            logger.debug("Failed to log to EVOLUTION.md: %s", exc)


def run_evolution_cycle(skills_dir: Path, transcripts_dir: Path, evals_dir: Path) -> dict:
    """Run a full evolution cycle: mine -> score -> optimize for all eligible skills.

    This is the primary entry point for the ``s_job-manager`` scheduled job
    system and for manual invocation from the CLI or a hook.

    Returns summary dict with keys:
        ``skills_checked``, ``eligible``, ``optimized``, ``changes``.

    Usage (scheduled job via ``s_job-manager``)::

        from core.evolution_optimizer import run_evolution_cycle
        result = run_evolution_cycle(
            skills_dir=Path("backend/skills"),
            transcripts_dir=Path.home() / ".claude" / "projects",
            evals_dir=Path.home() / ".swarm-ai/SwarmWS/.context/SkillEvals",
        )

    Usage (manual one-off from backend/)::

        python -c "
        from pathlib import Path
        from core.evolution_optimizer import run_evolution_cycle
        print(run_evolution_cycle(
            Path('skills'),
            Path.home() / '.claude/projects',
            Path.home() / '.swarm-ai/SwarmWS/.context/SkillEvals',
        ))
        "

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

    # Consult SkillMetrics for priority candidates (skills with high
    # correction rates or low success rates from actual usage data).
    priority_skills: set[str] = set()
    try:
        from core.skill_metrics import SkillMetricsStore
        from core.app_config_manager import app_config_manager
        data_dir = Path(
            app_config_manager.get("data_dir", str(Path.home() / ".swarm-ai"))
            if app_config_manager is not None
            else str(Path.home() / ".swarm-ai")
        )
        db_path = data_dir / "data.db"
        if db_path.exists():
            store = SkillMetricsStore(str(db_path))
            candidates = store.get_evolution_candidates()
            priority_skills = set(candidates)
            if priority_skills:
                logger.info("SkillMetrics priority candidates: %s", priority_skills)
    except Exception:
        pass  # Graceful degradation if metrics not available

    # Step 1: Mine all skills
    all_examples = miner.mine_all()
    skills_checked = len(all_examples)

    # Step 2: Filter eligible (>=5 examples), prioritize metrics candidates
    eligible_skills: list[str] = []
    for name, examples in all_examples.items():
        if len(examples) >= 5:
            eligible_skills.append(name)
        elif name in priority_skills and len(examples) >= 3:
            # Lower threshold for metrics-flagged skills
            eligible_skills.append(name)

    # Step 3: Score and optimize
    optimized_count = 0
    total_changes = 0
    results: list[OptimizationResult] = []

    for skill_name in eligible_skills:
        examples = all_examples[skill_name]

        # Score current fitness using correction examples.
        # Filter out examples with empty/trivial agent_actions — scoring
        # (expected, "") always yields ~0.0 which triggers false optimizations.
        score_pairs = []
        for ex in examples:
            if ex.user_correction and len(ex.agent_actions.strip()) > 20:
                score_pairs.append((ex.user_correction, ex.agent_actions))
        avg_score = evaluator.score_batch(score_pairs) if score_pairs else 1.0

        # Adaptive threshold: require more evidence (lower threshold) when
        # example count is low to avoid noisy optimizations on sparse data.
        # 5 examples → 0.5, 10 examples → 0.6, 20+ examples → 0.7
        n = len(score_pairs)
        threshold = min(0.7, 0.4 + 0.015 * n) if n > 0 else 0.7

        if avg_score < threshold:
            result = optimizer.optimize_skill(skill_name, examples)
            results.append(result)
            if result.accepted and result.changes:
                # Deploy the accepted changes to SKILL.md
                deployed = optimizer.deploy_optimization(result)
                if deployed:
                    optimized_count += 1
                    total_changes += len(result.changes)
                    # Save eval examples for audit trail
                    miner.save_evals(skill_name, examples)
                    logger.info(
                        "Evolution cycle: optimized and deployed %s "
                        "(score %.2f -> %.2f, %d changes)",
                        skill_name, result.original_score, result.optimized_score,
                        len(result.changes),
                    )
                else:
                    logger.warning(
                        "Evolution cycle: optimization accepted but deploy failed for %s",
                        skill_name,
                    )

    summary = {
        "skills_checked": skills_checked,
        "eligible": len(eligible_skills),
        "optimized": optimized_count,
        "changes": total_changes,
    }
    logger.info("Evolution cycle complete: %s", summary)
    return summary
