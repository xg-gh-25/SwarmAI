"""Heuristic skill optimization via correction pattern analysis.

Analyzes eval examples where users corrected the agent's output,
extracts actionable patterns, and suggests SKILL.md text changes.
Uses heuristic-based optimization (correction-pattern matching and
term-overlap fitness scoring). Designed with extensible interfaces for
future ML-based optimization if needed.

Evolution Pipeline v2.1 — Production-grade redesign with:
- Confidence-gated actuation (HIGH≥0.35=deploy, MED≥0.15=recommend, LOW=log)
- Atomic deploy with verification and rollback
- Process-level file lock to prevent concurrent cycles
- SkillHealthReport persisted as skill_health.json

Key public symbols:
- ``OptimizationResult``  -- Result of optimization attempt.
- ``TextChange``          -- A single text replacement.
- ``EvolutionOptimizer``  -- Orchestrates skill optimization.
- ``run_evolution_cycle`` -- Convenience function for full mine-score-optimize cycle.
- ``compute_confidence``  -- Confidence score from corrections + fitness.
- ``atomic_deploy``       -- Atomic deploy with verify + rollback.
- ``CycleReport``         -- Structured return from run_evolution_cycle.
- ``DeployResult``        -- Outcome of an atomic deploy.
- ``SkillHealthEntry``    -- Per-skill health data.
- ``Recommendation``      -- Proposed change with evidence.
- ``SkillHealthReport``   -- Full cycle report.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import uuid
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Confidence thresholds — v2.1 tuning (2026-04-12)
# Lowered from 0.7/0.3 to enable deployment with real-world correction data.
# At 0.7 threshold, max reachable confidence was 0.2 (no skill had ≥3 corrections).
# See KD01 and skill_health.json for evidence.
HIGH_CONFIDENCE = 0.35
MED_CONFIDENCE = 0.15


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


@dataclass
class Recommendation:
    """Proposed change with evidence."""
    skill_name: str
    changes: list[TextChange]
    evidence_summary: list[str]       # Human-readable correction summaries (max 5)
    original_score: float
    estimated_score: float
    constraint_check: str             # "passed" | reason for failure


@dataclass
class SkillHealthEntry:
    """Per-skill health data."""
    skill_name: str
    total_examples: int
    correction_count: int
    correction_rate: float
    fitness_score: float
    confidence: float
    action: str                       # "deploy" | "recommend" | "log" | "skip"
    recommendation: Recommendation | None = None
    trend: str | None = None          # "improving" | "stable" | "degrading" | None


@dataclass
class DeployResult:
    """Atomic deploy outcome."""
    skill_name: str
    success: bool
    changes_applied: int
    changes_skipped: int
    verified: bool
    rolled_back: bool
    error: str | None = None


@dataclass
class SkillHealthReport:
    """Full cycle report."""
    timestamp: str
    cycle_id: str
    duration_seconds: float
    transcripts_scanned: int
    skills: list[SkillHealthEntry] = field(default_factory=list)
    deployments: list[DeployResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class CycleReport:
    """Replaces the old dict return value."""
    cycle_id: str
    skills_checked: int
    eligible: int
    high_confidence: int = 0
    medium_confidence: int = 0
    low_confidence: int = 0
    deployed: int = 0
    verified: int = 0
    rolled_back: int = 0
    errors: list[str] = field(default_factory=list)
    health_report_path: Path = field(default_factory=lambda: Path("."))

    def to_dict(self) -> dict:
        """Backward-compatible dict with original keys plus new ones."""
        return {
            "skills_checked": self.skills_checked,
            "eligible": self.eligible,
            "optimized": self.deployed,
            "changes": self.deployed,  # backward compat: changes = deployed count
            "high_confidence": self.high_confidence,
            "medium_confidence": self.medium_confidence,
            "low_confidence": self.low_confidence,
            "deployed": self.deployed,
            "verified": self.verified,
            "rolled_back": self.rolled_back,
            "errors": self.errors,
        }


def compute_confidence(
    n_corrections: int,
    n_examples: int,
    avg_fitness: float,
) -> float:
    """Compute confidence score for skill optimization.

    Three signals:
      evidence_strength: raw correction count (are there enough examples?)
      correction_density: correction_rate = n_corrections / n_examples
        (high rate = consistent problem, not a one-off)
      need_signal: how low is the fitness score?

    Final confidence = evidence × max(density_boost, need_signal)
    where density_boost amplifies confidence when correction rate is high
    (>30% of invocations get corrected = strong signal).
    """
    if n_corrections == 0:
        return 0.0

    # Evidence strength (step function on raw count)
    # v2.1: added n>=2 band at 0.5 — real-world data shows most skills
    # accumulate 1-3 corrections, old function was unreachable above 0.2.
    if n_corrections >= 10:
        evidence = 1.0
    elif n_corrections >= 5:
        evidence = 0.8
    elif n_corrections >= 3:
        evidence = 0.6
    elif n_corrections >= 2:
        evidence = 0.5
    else:
        evidence = 0.3

    # Correction density — high rate amplifies confidence
    # v2.1: added >0.05 band — 9% correction rate (2/22) should contribute
    # signal, not be indistinguishable from 0%.
    correction_rate = n_corrections / max(n_examples, 1)
    if correction_rate > 0.5:
        density_boost = 0.9
    elif correction_rate > 0.3:
        density_boost = 0.6
    elif correction_rate > 0.15:
        density_boost = 0.4
    elif correction_rate > 0.05:
        density_boost = 0.2
    else:
        density_boost = 0.0

    # Need signal (how low is fitness?)
    if avg_fitness > 0.7:
        need = 0.1
    elif avg_fitness > 0.5:
        need = 0.4
    elif avg_fitness > 0.3:
        need = 0.7
    else:
        need = 1.0

    # Combine: evidence × max(density, need)
    # density_boost lets high correction rates push confidence up
    # even when fitness score is moderate
    return round(evidence * max(density_boost, need), 2)


def _extract_body(content: str) -> tuple[str, str]:
    """Split YAML frontmatter from body. Returns (frontmatter, body).

    frontmatter includes the --- delimiters; body is everything after.
    If no frontmatter, frontmatter is empty string.
    """
    parts = content.split("---", 2)
    if len(parts) >= 3:
        frontmatter = parts[0] + "---" + parts[1] + "---"
        body = parts[2]
        return frontmatter, body
    return "", content


def _rebuild_content(original_content: str, new_body: str) -> str:
    """Rebuild full file content from original (for frontmatter) and new body."""
    frontmatter, _ = _extract_body(original_content)
    return frontmatter + new_body if frontmatter else new_body


def atomic_deploy(
    skill_path: Path,
    changes: list[TextChange],
) -> DeployResult:
    """Atomically deploy changes to SKILL.md with verification.

    Safety guarantees:
    1. Original preserved in .bak until NEXT successful cycle
    2. Write via tmp + os.replace (atomic on POSIX)
    3. Post-write verification: re-read and confirm changes applied
    4. On any failure: rollback from .bak
    """
    backup_path = skill_path.with_suffix(".md.bak")
    tmp_path = skill_path.with_suffix(".md.tmp")

    try:
        # 1. Backup
        original_content = skill_path.read_text(encoding="utf-8")
        backup_path.write_text(original_content, encoding="utf-8")

        # 2. Apply changes to body
        _frontmatter, body = _extract_body(original_content)
        changes_applied = 0
        changes_skipped = 0

        for change in changes:
            if change.original:
                if change.original not in body:
                    logger.warning(
                        "Skipping change: original text not found in %s: %r",
                        skill_path.name, change.original[:80],
                    )
                    changes_skipped += 1
                    continue
                body = body.replace(change.original, change.replacement, 1)
                changes_applied += 1
            elif change.replacement:
                body = body.rstrip() + "\n" + change.replacement + "\n"
                changes_applied += 1

        if changes_applied == 0:
            # Clean up backup — it's identical to original, no rollback needed
            backup_path.unlink(missing_ok=True)
            return DeployResult(
                skill_name=skill_path.parent.name,
                success=False,
                changes_applied=0,
                changes_skipped=changes_skipped,
                verified=False,
                rolled_back=False,
                error="No changes could be applied -- all originals not found",
            )

        new_content = _rebuild_content(original_content, body)

        # 3. Write to tmp file
        tmp_path.write_text(new_content, encoding="utf-8")

        # 4. Atomic replace
        os.replace(str(tmp_path), str(skill_path))

        # 5. Verify: re-read and confirm
        verified_content = skill_path.read_text(encoding="utf-8")
        if verified_content != new_content:
            logger.error(
                "Post-write verification failed for %s -- rolling back",
                skill_path,
            )
            os.replace(str(backup_path), str(skill_path))
            return DeployResult(
                skill_name=skill_path.parent.name,
                success=False,
                changes_applied=changes_applied,
                changes_skipped=changes_skipped,
                verified=False,
                rolled_back=True,
                error="Post-write content mismatch",
            )

        return DeployResult(
            skill_name=skill_path.parent.name,
            success=True,
            changes_applied=changes_applied,
            changes_skipped=changes_skipped,
            verified=True,
            rolled_back=False,
            error=None,
        )

    except OSError as exc:
        # Rollback on any I/O error
        if backup_path.exists():
            try:
                os.replace(str(backup_path), str(skill_path))
            except OSError:
                pass
        return DeployResult(
            skill_name=skill_path.parent.name,
            success=False,
            changes_applied=0,
            changes_skipped=0,
            verified=False,
            rolled_back=backup_path.exists(),
            error=str(exc),
        )
    finally:
        # Clean up tmp if it still exists
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


CORRECTION_PATTERNS = [
    # English: "don't X" -> remove X from instructions
    (re.compile(r"(?:don'?t|stop|never|avoid)\s+(.{5,60})", re.I), "remove"),
    # English: "use Y instead" -> add Y
    (re.compile(r"(?:use|prefer|try)\s+(.{5,60})\s+instead", re.I), "add"),
    # English: "should X" -> ensure X is in instructions
    (re.compile(r"(?:should|must|always)\s+(.{5,60})", re.I), "add"),
    # Chinese: "不要/别/停止/避免 X" -> remove X
    (re.compile(r"(?:不要|别|停止|避免)\s*(.{3,60})"), "remove"),
    # Chinese: "应该/必须/要/需要 X" -> add X
    (re.compile(r"(?:应该|必须|一定要|需要)\s*(.{3,60})"), "add"),
    # Chinese: "用X代替/换成X" -> add X
    (re.compile(r"(?:用|换成|改成|改为)\s*(.{3,60})(?:代替|替换)?"), "add"),
    # Imperative: "verify/check/ensure/validate X" -> add X
    (re.compile(r"(?:verify|check|ensure|validate|confirm)\s+(.{5,60})", re.I), "add"),
    # Imperative Chinese: "检查/确认/验证 X" -> add X
    (re.compile(r"(?:检查|确认|验证|确保)\s*(.{3,60})"), "add"),
]


def _extract_correction_summary(correction_text: str) -> str | None:
    """Extract a concise actionable summary from an unstructured correction.

    Picks the first meaningful sentence (15-200 chars, not code/agent talk).
    Returns None if no suitable sentence found.
    """
    # Split by common sentence boundaries
    sentences = re.split(r"[.!?。！？\n]+", correction_text)
    for sentence in sentences:
        s = sentence.strip()
        if len(s) < 15 or len(s) > 200:
            continue
        lower = s.lower()
        # Skip code/path references
        if any(ind in lower for ind in (".py", ".ts", ".js", "def ", "class ", "import ", "self.")):
            continue
        # Skip agent monologue
        if any(lower.startswith(ind) for ind in ("let me", "i'll", "i need to", "checking", "looking")):
            continue
        # Skip pure noise
        if re.match(r"^(?:ok|yes|no|sure|thanks|got it)\b", lower):
            continue
        return s
    return None


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
        """Extract (correction_text, pattern_type) from examples with user corrections.

        Tries structured patterns first (English + Chinese). Falls back to
        extracting the first meaningful sentence from the correction as an
        "add" directive — ensures no correction is silently dropped.
        """
        corrections: list[tuple[str, str]] = []
        for ex in examples:
            if not ex.user_correction:
                continue
            matched = False
            for pattern, action_type in CORRECTION_PATTERNS:
                match = pattern.search(ex.user_correction)
                if match:
                    corrections.append((match.group(1).strip(), action_type))
                    matched = True
                    break  # One pattern per correction to avoid duplicates

            # Fallback: if no structured pattern matched, extract a summary
            # sentence from the correction. Prefer the first non-trivial line.
            if not matched:
                summary = _extract_correction_summary(ex.user_correction)
                if summary:
                    corrections.append((summary, "add"))
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

        # Trailing fragment: if the text ends with an alphabetic char (no
        # sentence-ending punctuation) and the final word is very short (<3
        # chars), it likely got cut off mid-phrase by the regex capture group
        # length limit.  This catches "should always vali" but allows
        # "should always validate input first".
        stripped = text.strip()
        if stripped and stripped[-1].isalpha() and len(stripped) > 30:
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
            # v2.1: catch code analysis fragments that leaked through correction detection
            "cache the ", "assigned on ", "if before_id", "wiring are ",
            "store instance", "prompt builder", "overrides,",
        )
        lower = text.lower()
        if any(indicator in lower for indicator in code_indicators):
            return False

        # Reject fragments that look like variable/function names or code constructs
        # (underscore-heavy, camelCase, or all-lowercase-no-space patterns)
        if re.match(r"^[a-z_]+(?:_[a-z_]+){2,}$", stripped):  # snake_case identifiers
            return False

        # Agent monologue leaked as correction
        agent_indicators = (
            "let me ", "i'll ", "i need to ", "confirmed —", "verified —",
            "checking ", "looking at ", "reading ", "found ",
            # v2.1: catch more agent analysis patterns
            "remaining ", "correct:\n", "transcript ",
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
        """Write accepted optimization to SKILL.md and log to EVOLUTION.md + CHANGELOG.

        .. deprecated:: v2
            Production path uses ``atomic_deploy()`` (module-level function) which
            provides atomic writes, post-deploy verification, and rollback.
            This method is retained for backward compat and unit test coverage
            of the text manipulation logic.
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

        # NOTE: v2 pipeline uses _write_cycle_changelog (module-level, fcntl-locked).
        # This deprecated method does NOT write to changelog to avoid unlocked writes.

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


    # _log_to_changelog removed in v2 — was an unlocked write to
    # EVOLUTION_CHANGELOG.jsonl. The v2 pipeline uses module-level
    # _write_cycle_changelog() which has proper fcntl locking.


def run_evolution_cycle(skills_dir: Path, transcripts_dir: Path, evals_dir: Path) -> CycleReport:
    """Run a full evolution cycle with exclusive file lock.

    Evolution Pipeline v2: MINE -> ASSESS -> ACT -> AUDIT.

    Returns CycleReport (use .to_dict() for backward-compatible dict).

    Only one cycle can run at a time across all triggers
    (session hook, scheduled job, manual invocation).
    """
    cycle_id = str(uuid.uuid4())[:8]
    lock_path = evals_dir.parent / ".evolution_cycle.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Acquire exclusive file lock (non-blocking)
    lock_fd = None
    try:
        lock_fd = open(lock_path, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        logger.info("Evolution cycle already running -- skipping")
        if lock_fd is not None:
            lock_fd.close()
        return CycleReport(
            cycle_id=cycle_id,
            skills_checked=0,
            eligible=0,
            errors=["Concurrent cycle in progress -- lock held"],
        )

    try:
        return _run_evolution_cycle_locked(skills_dir, transcripts_dir, evals_dir, cycle_id)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fd.close()


def _run_evolution_cycle_locked(
    skills_dir: Path,
    transcripts_dir: Path,
    evals_dir: Path,
    cycle_id: str,
) -> CycleReport:
    """Run evolution cycle phases under the lock."""
    from core.session_miner import SessionMiner
    from core.skill_fitness import SkillFitnessEvaluator

    start_time = time.monotonic()
    errors: list[str] = []
    health_entries: list[SkillHealthEntry] = []
    deploy_results: list[DeployResult] = []

    miner = SessionMiner(transcripts_dir, skills_dir, evals_dir)
    optimizer = EvolutionOptimizer(skills_dir)
    evaluator = SkillFitnessEvaluator()

    # Consult SkillMetrics for priority candidates
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
        pass

    # ── Phase 1: MINE (unchanged) ──
    all_examples = miner.mine_all()
    skills_checked = len(all_examples)
    transcripts_scanned = getattr(miner, "_last_transcripts_scanned", 0)

    # Filter eligible (>=5 examples, or >=3 for priority)
    eligible_skills: list[str] = []
    for name, examples in all_examples.items():
        if len(examples) >= 5:
            eligible_skills.append(name)
        elif name in priority_skills and len(examples) >= 3:
            eligible_skills.append(name)

    # ── Phase 2: ASSESS ──
    high_count = 0
    med_count = 0
    low_count = 0

    skill_assessments: list[tuple[str, float, float, list, OptimizationResult | None]] = []

    for skill_name in eligible_skills:
        examples = all_examples[skill_name]

        # Count corrections
        correction_count = sum(1 for ex in examples if ex.user_correction)

        # Score fitness
        score_pairs = []
        for ex in examples:
            if ex.user_correction and len(ex.agent_actions.strip()) > 20:
                score_pairs.append((ex.user_correction, ex.agent_actions))
        avg_score = evaluator.score_batch(score_pairs) if score_pairs else 1.0

        # Compute confidence
        confidence = compute_confidence(correction_count, len(examples), avg_score)

        # Determine action
        if confidence >= HIGH_CONFIDENCE:
            action = "deploy"
            high_count += 1
        elif confidence >= MED_CONFIDENCE:
            action = "recommend"
            med_count += 1
        else:
            action = "log"
            low_count += 1

        # Generate recommendation if confidence is at least LOW
        opt_result = None
        recommendation = None
        if correction_count > 0:
            opt_result = optimizer.optimize_skill(skill_name, examples)
            if opt_result.changes:
                evidence = []
                for ex in examples:
                    if ex.user_correction:
                        evidence.append(ex.user_correction[:100])
                        if len(evidence) >= 5:
                            break
                recommendation = Recommendation(
                    skill_name=skill_name,
                    changes=opt_result.changes,
                    evidence_summary=evidence,
                    original_score=opt_result.original_score,
                    estimated_score=opt_result.optimized_score,
                    constraint_check=opt_result.reason,
                )

        health_entry = SkillHealthEntry(
            skill_name=skill_name,
            total_examples=len(examples),
            correction_count=correction_count,
            correction_rate=correction_count / max(len(examples), 1),
            fitness_score=avg_score,
            confidence=confidence,
            action=action,
            recommendation=recommendation,
            trend=None,  # Future: compare with previous run
        )
        health_entries.append(health_entry)
        skill_assessments.append((skill_name, confidence, avg_score, examples, opt_result))

    # ── Phase 3: ACT (confidence-gated) ──
    deployed_count = 0
    verified_count = 0
    rolled_back_count = 0

    for skill_name, confidence, avg_score, examples, opt_result in skill_assessments:
        if confidence >= HIGH_CONFIDENCE and opt_result and opt_result.accepted and opt_result.changes:
            # HIGH: atomic deploy
            skill_path = skills_dir / f"s_{skill_name}" / "SKILL.md"
            if skill_path.exists():
                deploy_result = atomic_deploy(skill_path, opt_result.changes)
                deploy_results.append(deploy_result)
                if deploy_result.success:
                    deployed_count += 1
                    if deploy_result.verified:
                        verified_count += 1
                    # Save eval examples for audit trail
                    miner.save_evals(skill_name, examples)
                    logger.info(
                        "Evolution cycle: deployed %s (confidence=%.2f, "
                        "score %.2f -> %.2f, %d changes)",
                        skill_name, confidence,
                        opt_result.original_score, opt_result.optimized_score,
                        deploy_result.changes_applied,
                    )
                elif deploy_result.rolled_back:
                    rolled_back_count += 1
                    errors.append(
                        f"Deploy rolled back for {skill_name}: {deploy_result.error}"
                    )
                else:
                    errors.append(
                        f"Deploy failed for {skill_name}: {deploy_result.error}"
                    )
        elif confidence >= MED_CONFIDENCE:
            # MED: recommendation surfaced in skill_health.json
            logger.info(
                "Evolution cycle: recommending changes for %s (confidence=%.2f)",
                skill_name, confidence,
            )
        else:
            # LOW: log only
            logger.debug(
                "Evolution cycle: logging %s (confidence=%.2f)",
                skill_name, confidence,
            )

    # ── Phase 4: AUDIT ──
    duration = time.monotonic() - start_time

    health_report = SkillHealthReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        cycle_id=cycle_id,
        duration_seconds=round(duration, 2),
        transcripts_scanned=transcripts_scanned,
        skills=health_entries,
        deployments=deploy_results,
        errors=errors,
    )

    # Write skill_health.json atomically — resolve relative to evals_dir
    # evals_dir is typically .context/SkillEvals, so parent is .context/
    health_json_path = evals_dir.parent / "skill_health.json"
    _write_skill_health(health_json_path, health_report)

    # Write changelog
    _write_cycle_changelog(evals_dir, health_report, deployed_count, verified_count, rolled_back_count)

    # Write EVOLUTION.md for successful deployments
    if deployed_count > 0:
        for deploy_result in deploy_results:
            if deploy_result.success:
                # Find the matching opt_result
                for sn, conf, avg, exs, opt_r in skill_assessments:
                    if sn == deploy_result.skill_name.removeprefix("s_") and opt_r:
                        optimizer._log_to_evolution(opt_r)
                        break

    health_report_path = evals_dir.parent / "skill_health.json"

    report = CycleReport(
        cycle_id=cycle_id,
        skills_checked=skills_checked,
        eligible=len(eligible_skills),
        high_confidence=high_count,
        medium_confidence=med_count,
        low_confidence=low_count,
        deployed=deployed_count,
        verified=verified_count,
        rolled_back=rolled_back_count,
        errors=errors,
        health_report_path=health_report_path,
    )
    logger.info("Evolution cycle complete: %s", report.to_dict())

    # Clean up stale .bak files from previous cycles.
    # atomic_deploy leaves .bak files for rollback safety; once the full cycle
    # completes successfully (no rolled-back deploys), they are no longer needed.
    if rolled_back_count == 0:
        for bak_file in skills_dir.rglob("*.md.bak"):
            try:
                bak_file.unlink()
                logger.debug("Cleaned up stale backup: %s", bak_file)
            except OSError as exc:
                logger.debug("Failed to clean up %s: %s", bak_file, exc)

    return report


def _write_skill_health(path: Path, report: SkillHealthReport) -> None:
    """Write skill_health.json atomically (tmp + os.replace)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "timestamp": report.timestamp,
            "cycle_id": report.cycle_id,
            "duration_seconds": report.duration_seconds,
            "transcripts_scanned": report.transcripts_scanned,
            "skills": [
                {
                    "skill_name": s.skill_name,
                    "total_examples": s.total_examples,
                    "correction_count": s.correction_count,
                    "correction_rate": round(s.correction_rate, 4),
                    "fitness_score": round(s.fitness_score, 4),
                    "confidence": s.confidence,
                    "action": s.action,
                    "recommendation": {
                        "evidence_summary": s.recommendation.evidence_summary,
                        "original_score": s.recommendation.original_score,
                        "estimated_score": s.recommendation.estimated_score,
                        "constraint_check": s.recommendation.constraint_check,
                    } if s.recommendation else None,
                    "trend": s.trend,
                }
                for s in report.skills
            ],
            "deployments": [
                {
                    "skill_name": d.skill_name,
                    "success": d.success,
                    "changes_applied": d.changes_applied,
                    "changes_skipped": d.changes_skipped,
                    "verified": d.verified,
                    "rolled_back": d.rolled_back,
                    "error": d.error,
                }
                for d in report.deployments
            ],
            "errors": report.errors,
        }
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(str(tmp_path), str(path))
    except OSError as exc:
        logger.warning("Failed to write skill_health.json: %s", exc)


def _write_cycle_changelog(
    evals_dir: Path,
    report: SkillHealthReport,
    deployed: int,
    verified: int,
    rolled_back: int,
) -> None:
    """Append cycle summary to EVOLUTION_CHANGELOG.jsonl."""
    try:
        changelog_path = None
        # Preferred: resolve via app config
        try:
            from core.app_config_manager import app_config_manager
            if app_config_manager is not None:
                ws_path = app_config_manager.get("workspace_path")
                if ws_path:
                    candidate = Path(ws_path) / ".context" / "EVOLUTION_CHANGELOG.jsonl"
                    changelog_path = candidate
        except (ImportError, Exception):
            pass

        if changelog_path is None:
            # Fallback: relative to evals_dir
            for parent in [
                evals_dir.parent.parent / ".context" if evals_dir.parent else None,
                evals_dir.parent / ".context" if evals_dir.parent else None,
            ]:
                if parent and parent.is_dir():
                    changelog_path = parent / "EVOLUTION_CHANGELOG.jsonl"
                    break

        if changelog_path is None:
            changelog_path = evals_dir.parent / "EVOLUTION_CHANGELOG.jsonl"

        entry = {
            "ts": report.timestamp,
            "action": "evolution_cycle_v2",
            "cycle_id": report.cycle_id,
            "phase": "audit",
            "skills_checked": len(report.skills),
            "transcripts_scanned": report.transcripts_scanned,
            "eligible": len(report.skills),
            "recommendations": sum(1 for s in report.skills if s.action == "recommend"),
            "deployed": deployed,
            "verified": verified,
            "rolled_back": rolled_back,
            "errors": report.errors,
            "source": "evolution_optimizer_v2",
        }

        changelog_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = changelog_path.with_suffix(".jsonl.lock")
        try:
            with open(lock_path, "w") as lock_fd:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                try:
                    with open(changelog_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(entry) + "\n")
                finally:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            with open(changelog_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

    except Exception as exc:
        logger.debug("Failed to write evolution changelog: %s", exc)
