"""Heuristic skill optimization via correction pattern analysis.

Analyzes eval examples where users corrected the agent's output,
extracts actionable patterns, and suggests SKILL.md text changes.
Uses heuristic-based optimization (correction-pattern matching and
term-overlap fitness scoring). Designed with extensible interfaces for
future ML-based optimization if needed.

Evolution Pipeline v2.1 — Production-grade redesign with:
- Confidence-gated actuation (HIGH≥0.15=deploy, MED≥0.08=recommend, LOW=log)
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

import json
import logging
import os
import re
import uuid
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from utils.file_lock import flock_exclusive, flock_exclusive_nb, flock_unlock

logger = logging.getLogger(__name__)

# Confidence thresholds — defaults, overridable via config.evolution.high_confidence / med_confidence
# v2.1 (2026-04-12): lowered from 0.7/0.3 — old thresholds were unreachable with real data.
# v2.2 (2026-05-03): lowered HIGH from 0.35 to 0.15 — real data (autonomous-pipeline: 5 corrections,
#   64 examples, conf=0.16) couldn't reach 0.35. ACT phase was permanently frozen.
#   0.15 allows skills with genuine correction evidence to deploy. Safety: atomic rollback
#   + regression detection (>0.1 fitness drop auto-reverts). MED lowered proportionally.
HIGH_CONFIDENCE = 0.15
MED_CONFIDENCE = 0.08


def _get_confidence_thresholds() -> tuple[float, float]:
    """Read thresholds from app config, falling back to module defaults."""
    try:
        from core.app_config_manager import app_config_manager
        if app_config_manager is not None:
            evo = app_config_manager.get("evolution", {})
            if isinstance(evo, dict):
                return (
                    float(evo.get("high_confidence", HIGH_CONFIDENCE)),
                    float(evo.get("med_confidence", MED_CONFIDENCE)),
                )
    except (ImportError, Exception):
        pass
    return HIGH_CONFIDENCE, MED_CONFIDENCE


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
    llm_tokens: int = 0               # Bedrock tokens used for this skill's LLM optimization
    optimizer_used: str = "none"      # "llm" | "heuristic" | "none" — distinguishes LLM vs heuristic vs no-op
    # v2.3 GEPA-inspired fields
    llm_judge_score: float | None = None   # Layer 2 score (None if skipped/failed)
    combined_fitness: float | None = None   # 0.4×L1 + 0.6×L2 (None if L2 unavailable)
    anti_patterns_count: int = 0            # Number of anti-patterns generated


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
    dry_run: bool = True
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
            "dry_run": self.dry_run,
            "errors": self.errors,
        }


def compute_confidence(
    n_corrections: int,
    n_examples: int,
    avg_fitness: float,
    recent_corrections: int = 0,
    repeat_count: int = 0,
) -> float:
    """Compute confidence score for skill optimization.

    Three base signals:
      evidence_strength: raw correction count — step function with bands at
        1 (0.3), 2 (0.5), 3 (0.6), 5 (0.8), 10 (1.0).
      correction_density: correction_rate = n_corrections / n_examples.
        Bands: >5% (0.2), >15% (0.4), >30% (0.6), >50% (0.9).
      need_signal: how much improvement does the skill need?
        fitness ≤0.3 → 1.0, ≤0.5 → 0.7, ≤0.7 → 0.5, else 0.3 (v2.4 floor).

    Two additive boosts (v2.2):
      recency_boost: +0.05 per correction within 7 days, capped at +0.15.
      repeat_boost: +0.05 per repeat on same skill (repeat_count - 1),
        capped at +0.10.

    Final confidence = evidence × max(density_boost, need_signal)
                     + recency_boost + repeat_boost.
    Capped at 1.0.  Both factors must be present — pure count without need,
    or pure need without evidence, cannot produce high confidence alone.
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
    # v2.4 (2026-05-25): raised floor from 0.1 to 0.3 for high-fitness skills.
    # Rationale: if corrections exist (n_corrections > 0, enforced by early return L203),
    # the skill IS producing errors even at high fitness. Old floor (0.1) made
    # it structurally impossible for 1-2 correction skills to reach MED (0.08)
    # threshold — evidence × max(density, 0.1) = 0.3 × 0.1 = 0.03. This kept
    # the evolution pipeline permanently frozen despite real correction evidence.
    # New floor (0.3) allows: 0.3 × 0.3 = 0.09 ≥ MED (0.08) → proposals surface.
    if avg_fitness > 0.7:
        need = 0.3
    elif avg_fitness > 0.5:
        need = 0.5
    elif avg_fitness > 0.3:
        need = 0.7
    else:
        need = 1.0

    # Base: evidence × max(density, need)
    # density_boost lets high correction rates push confidence up
    # even when fitness score is moderate
    base = evidence * max(density_boost, need)

    # v2.2: Additive boosts for recent and repeated corrections.
    # These make the threshold reachable for skills with ongoing issues.
    recency_boost = min(0.15, recent_corrections * 0.05)
    repeat_boost = min(0.10, max(0, repeat_count - 1) * 0.05)

    return round(min(1.0, base + recency_boost + repeat_boost), 2)


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


# ── GEPA-inspired components (v2.3) ──


class ExecutionTraceCollector:
    """Extract agent reasoning blocks from session transcripts for a skill.

    GEPA's key insight: feeding execution traces (agent reasoning + failure context)
    to the reflection/optimization step produces targeted improvements instead of
    blind pattern matching. This collector extracts the "why" behind failures.
    """

    def __init__(self, transcripts_dir: Path | None = None):
        self._transcripts_dir = transcripts_dir

    def collect_traces(self, skill_name: str, eval_examples: list, max_traces: int = 5) -> list[str]:
        """Extract execution trace context from eval examples.

        Accepts any object with user_correction, user_prompt, agent_actions attrs
        (EvalExample dataclass or duck-typed equivalent from manual cycle runs).

        Returns up to max_traces trace strings, each capped at 2000 chars.
        """
        traces: list[str] = []
        for ex in eval_examples:
            correction = getattr(ex, "user_correction", None) or ""
            if not correction:
                continue  # Only trace failed executions

            parts = []
            user_prompt = getattr(ex, "user_prompt", None) or ""
            agent_actions = getattr(ex, "agent_actions", None) or ""
            if user_prompt:
                parts.append(f"User asked: {user_prompt[:300]}")
            if agent_actions:
                parts.append(f"Agent did: {agent_actions[:800]}")
            parts.append(f"User corrected: {correction[:500]}")

            trace = "\n".join(parts)
            if trace:
                traces.append(trace[:2000])

            if len(traces) >= max_traces:
                break

        return traces


class AntiPatternGenerator:
    """Generate anti-patterns section from accumulated corrections.

    GEPA's observation: the optimized skill included 13 anti-patterns auto-generated
    from failure feedback. We replicate this by clustering "remove" corrections
    into a deduplicated, structured markdown section.
    """

    MAX_ANTI_PATTERNS = 10  # Cap to prevent unbounded growth

    def generate(self, corrections: list[tuple[str, str, str]]) -> str:
        """Generate anti-patterns markdown from correction evidence.

        Args:
            corrections: List of (text, action_type, confidence) tuples.
                         Only "remove" actions become anti-patterns.

        Returns:
            Markdown string with anti-patterns section, or empty string if none.
        """
        # Filter to "remove" corrections only
        remove_items = [
            text.strip() for text, action, _ in corrections
            if action == "remove" and text.strip()
        ]

        if not remove_items:
            return ""

        # Deduplicate by lowercased content
        seen: set[str] = set()
        unique: list[str] = []
        for item in remove_items:
            key = item.lower().strip()
            if key not in seen and len(key) > 5:  # Skip trivial fragments
                seen.add(key)
                unique.append(item)

        if not unique:
            return ""

        # Cap at max
        unique = unique[: self.MAX_ANTI_PATTERNS]

        # Format as markdown
        lines = ["\n## Anti-patterns (auto-generated from corrections)\n"]
        for item in unique:
            # Normalize: ensure it reads as an anti-pattern instruction
            if not item.startswith(("Don't", "don't", "Never", "never", "Avoid", "avoid")):
                lines.append(f"- ❌ Don't {item}")
            else:
                lines.append(f"- ❌ {item}")

        return "\n".join(lines) + "\n"

    def merge_with_existing(self, skill_text: str, new_anti_patterns: str) -> str:
        """Merge new anti-patterns into skill text, deduplicating with existing.

        If skill already has an anti-patterns section, appends new unique items.
        If not, appends the entire section at the end.
        """
        if not new_anti_patterns:
            return skill_text

        # Check for existing anti-patterns section
        existing_match = re.search(
            r"^## Anti-patterns.*?(?=^## |\Z)",
            skill_text,
            re.MULTILINE | re.DOTALL,
        )

        if existing_match:
            existing_section = existing_match.group(0)
            existing_lower = existing_section.lower()

            # Extract new items and add only truly new ones
            new_items = [
                line for line in new_anti_patterns.split("\n")
                if line.startswith("- ❌") and line.lower() not in existing_lower
            ]

            if not new_items:
                return skill_text  # All already present

            # Insert new items at end of existing section content (before next heading)
            # existing_match.end() points to the start of the next ## or \Z
            # We insert with a leading newline to maintain proper markdown spacing
            insertion_point = existing_match.end()
            new_block = "\n".join(new_items) + "\n\n"
            return skill_text[:insertion_point] + new_block + skill_text[insertion_point:]
        else:
            # No existing section — append at end
            return skill_text.rstrip() + "\n" + new_anti_patterns


class EvolutionOptimizer:
    def __init__(self, skills_dir: Path) -> None:
        self._skills_dir = skills_dir
        self.last_llm_tokens: int = 0  # Tokens used by last optimize_skill call

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

    def _extract_corrections(self, examples: list) -> list[tuple[str, str, str]]:
        """Extract (correction_text, pattern_type, confidence) from examples.

        Tries structured patterns first (English + Chinese) → "high" confidence.
        Falls back to extracting a summary sentence → "low" confidence.
        Only "high" confidence corrections are auto-deployed; "low" ones are
        surfaced in recommendations but not applied to SKILL.md.
        """
        corrections: list[tuple[str, str, str]] = []
        for ex in examples:
            if not ex.user_correction:
                continue
            matched = False
            for pattern, action_type in CORRECTION_PATTERNS:
                match = pattern.search(ex.user_correction)
                if match:
                    corrections.append((match.group(1).strip(), action_type, "high"))
                    matched = True
                    break  # One pattern per correction to avoid duplicates

            # Fallback: extract a summary sentence → low confidence.
            # These are informational (included in recommendations) but not
            # auto-deployed — prevents raw user remarks from becoming instructions.
            if not matched:
                summary = _extract_correction_summary(ex.user_correction)
                if summary:
                    corrections.append((summary, "add", "low"))
        return corrections

    def _apply_heuristic_changes(
        self, skill_text: str, corrections: list[tuple[str, str, str]]
    ) -> tuple[str, list[TextChange]]:
        """Apply correction patterns to skill text. Returns (new_text, changes).

        Quality gates:
        - Max 3 changes per optimization pass (prevent runaway appends)
        - Only "high" confidence corrections auto-applied (structured pattern match)
        - "low" confidence (fallback sentences) skipped — prevents raw user
          remarks from becoming skill instructions
        - Dedup: skip corrections already present in skill text
        - Completeness: reject fragments (mid-word truncation, <15 chars)
        - Coherence: reject if it looks like code, a path, or agent monologue
        """
        changes: list[TextChange] = []
        new_text = skill_text
        skill_lower = skill_text.lower()
        max_changes = 3

        for correction, action_type, confidence in corrections:
            if len(changes) >= max_changes:
                break

            # Only auto-apply high-confidence corrections (structured pattern match).
            # Low-confidence (fallback sentences) are surfaced in recommendations
            # but not deployed — they need human/GEPA judgment to become instructions.
            if confidence == "low":
                continue

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

        # Trailing fragment detection — catches corrections truncated mid-phrase.
        stripped = text.strip()
        if stripped and len(stripped) > 30:
            last_char = stripped[-1]
            # English: ends with short word fragment (e.g. "should always vali")
            if last_char.isascii() and last_char.isalpha():
                last_word = stripped.split()[-1] if stripped.split() else ""
                if len(last_word) < 3:
                    return False
            # CJK: ends without sentence-ending particle or punctuation.
            # Common endings: 了的吗呢吧啊哦嘛呀。！？
            # If the last char is CJK but NOT a natural endpoint, likely truncated.
            elif "\u4e00" <= last_char <= "\u9fff":
                _cjk_endings = set("了的吗呢吧啊哦嘛呀么")
                if last_char not in _cjk_endings:
                    return False

        # Already present in skill text (dedup)
        if text.strip().lower() in existing_skill_lower:
            return False

        # Looks like code, a file path, or line number reference (not a directive)
        code_indicators = (
            "line ", "def ", "class ", "import ", "from ", "return ",
            ".py", ".ts", ".js", "self.", "this.", "→",
        )
        lower = text.lower()
        if any(indicator in lower for indicator in code_indicators):
            return False

        # Reject fragments that look like variable/function names or code constructs
        if re.match(r"^[a-z_]+(?:_[a-z_]+){2,}$", stripped):  # snake_case identifiers
            return False

        # Reject text containing unbalanced parens/brackets (partial code)
        if stripped.count("(") != stripped.count(")"):
            return False
        if stripped.count("[") != stripped.count("]"):
            return False

        # Agent monologue leaked as correction
        agent_indicators = (
            "let me ", "i'll ", "i need to ", "confirmed —", "verified —",
            "checking ", "looking at ", "reading ", "found ",
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

        # Growth check: 20% max (in bytes, not chars — consistent with size check,
        # and correct for CJK content where 1 char = 3 bytes UTF-8).
        if original_text:
            new_bytes = len(new_text.encode("utf-8"))
            orig_bytes = len(original_text.encode("utf-8"))
            growth = (new_bytes - orig_bytes) / max(orig_bytes, 1)
            if growth > 0.20:
                return False, f"Growth {growth:.0%} exceeds 20% limit ({new_bytes - orig_bytes} bytes)"

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
            logger.debug("SkillGuard not available — skipping injection check for %s", skill_name)

        return True, "All constraints passed"

    def optimize_skill(
        self,
        skill_name: str,
        eval_examples: list,
        *,
        force_heuristic: bool = False,
        _precomputed_corrections: list | None = None,
    ) -> OptimizationResult:
        """Run optimization on a skill (LLM or heuristic, config-gated).

        Config ``evolution.optimizer``:
        - ``"auto"`` (default): try LLM → fallback to heuristic on any failure
        - ``"llm"``: LLM only (returns no-changes on failure)
        - ``"heuristic"``: heuristic only (original v2.1 behavior)

        Args:
            force_heuristic: If True, skip LLM regardless of config (used when
                LLM budget is exhausted for this cycle).
            _precomputed_corrections: Pre-extracted corrections from caller (avoids
                double extraction when cycle code peeks at heuristic before calling).
        """
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

        corrections = _precomputed_corrections if _precomputed_corrections is not None else self._extract_corrections(eval_examples)
        if not corrections:
            return OptimizationResult(
                skill_name=skill_name,
                original_score=0.0,
                optimized_score=0.0,
                changes=[],
                accepted=False,
                reason="No correction patterns found",
            )

        # Optimizer strategy: LLM first, heuristic fallback.
        # force_heuristic skips LLM entirely (used when LLM budget exhausted).
        use_heuristic_only = force_heuristic

        changes: list[TextChange] = []
        llm_tokens_used = 0

        # Pre-check: if skill already exceeds 15KB, any deploy will be
        # rejected by _validate_constraints. Skip the LLM call entirely
        # to avoid wasting tokens (~$0.05) on changes that can't land.
        skill_size = len(original_text.encode("utf-8"))
        if skill_size > 15 * 1024 and not use_heuristic_only:
            logger.info(
                "Skipping LLM for %s: skill text already %dKB > 15KB limit",
                skill_name, skill_size // 1024,
            )
            use_heuristic_only = True

        # v2.3: Collect execution traces for trace-guided mutation (GEPA-inspired)
        trace_collector = ExecutionTraceCollector()
        execution_traces = trace_collector.collect_traces(skill_name, eval_examples)

        # Try LLM optimizer first (unless forced to heuristic)
        if not use_heuristic_only:
            changes, llm_tokens_used = self._try_llm_optimization(
                skill_name, original_text, corrections,
                execution_traces=execution_traces,
            )

        self.last_llm_tokens = llm_tokens_used

        # Fallback to heuristic if LLM produced nothing (or was skipped)
        heuristic_text = None
        if not changes:
            heuristic_text, changes = self._apply_heuristic_changes(original_text, corrections)

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
        expected_text = " ".join(text for text, *_ in corrections)
        original_score = evaluator.score(expected_text, original_text).overall

        # Use heuristic's pre-built text directly when available (avoids
        # divergence between regex-based removal and exact string replacement).
        # For LLM changes, reconstruct by applying changes sequentially.
        if heuristic_text is not None:
            optimized_text = heuristic_text
        else:
            optimized_text = original_text
            for change in changes:
                if change.original and change.original in optimized_text:
                    optimized_text = optimized_text.replace(change.original, change.replacement, 1)
                elif not change.original and change.replacement:
                    optimized_text = optimized_text.rstrip() + "\n" + change.replacement

        optimized_score = evaluator.score(expected_text, optimized_text).overall

        passed, reason = self._validate_constraints(skill_name, optimized_text, original_text)

        return OptimizationResult(
            skill_name=skill_name,
            original_score=original_score,
            optimized_score=optimized_score,
            changes=changes,
            accepted=passed,
            reason=reason,
        )

    @staticmethod
    def _try_llm_optimization(
        skill_name: str,
        skill_text: str,
        corrections: list[tuple[str, str, str]],
        execution_traces: list[str] | None = None,
    ) -> tuple[list[TextChange], int]:
        """Try LLM-based optimization with trace-guided mutation (v2.3).

        Returns (changes, tokens_used).
        Returns ([], 0) on any failure — caller falls back to heuristic.
        """
        try:
            from core.llm_optimizer import optimize_skill_with_llm

            changes, usage = optimize_skill_with_llm(
                skill_text, corrections, skill_name,
                execution_traces=execution_traces,
            )
            return changes, usage.input_tokens + usage.output_tokens
        except Exception as exc:
            logger.warning("LLM optimizer unavailable for %s: %s", skill_name, exc)
            return [], 0

    # deploy_optimization() DELETED — deprecated v2 method.
    # Production path: atomic_deploy() (module-level function) with
    # atomic writes, post-deploy verification, and rollback.

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


def _break_stale_lock(lock_path: Path, max_age_seconds: int = 3600) -> bool:
    """Break a stale lock file if holder process is dead AND lock is old.

    Two-factor check prevents race condition:
    1. Lock file mtime > max_age_seconds (time-based staleness)
    2. PID written in lock file is no longer alive (process-based staleness)

    Both must be true to break. This avoids the unlink-while-held race:
    flock is inode-based, so unlinking a locked file doesn't release the lock
    on the original fd — a new file at the same path gets a new inode, allowing
    two processes to both "hold the lock" on different inodes.

    Returns True if the lock was broken (deleted), False otherwise.
    """
    import time

    if not lock_path.exists():
        return False

    try:
        mtime = lock_path.stat().st_mtime
        age = time.time() - mtime
        if age <= max_age_seconds:
            return False  # Too young — holder might still be running

        # Check if holder process is alive (PID written at lock acquire)
        content = lock_path.read_text().strip()
        if content.isdigit():
            pid = int(content)
            try:
                os.kill(pid, 0)  # Signal 0 = check existence only
                return False  # Process alive — lock is valid despite age
            except ProcessLookupError:
                pass  # Process dead — safe to break
            except PermissionError:
                return False  # Process exists but different user — don't break

        # Lock is old AND holder process is dead (or no PID recorded)
        lock_path.unlink(missing_ok=True)
        logger.warning(
            "Broke stale evolution lock: %s (age: %.0fs, holder PID %s dead)",
            lock_path, age, content or "unknown",
        )
        return True
    except OSError:
        pass
    return False


def run_evolution_cycle(
    skills_dir: Path,
    transcripts_dir: Path,
    evals_dir: Path,
    *,
    dry_run: bool = True,
) -> CycleReport:
    """Run a full evolution cycle with exclusive file lock.

    Evolution Pipeline v2: MINE -> ASSESS -> ACT -> AUDIT.

    Args:
        skills_dir: Path to skills directory.
        transcripts_dir: Path to transcripts directory.
        evals_dir: Path to evals directory.
        dry_run: If True (default), log proposed changes without writing
            to SKILL.md files. Set to False for live deployment after
            manual validation of dry-run output quality.

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
        flock_exclusive_nb(lock_fd)
        # Write PID for stale lock detection by future processes
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
    except (OSError, BlockingIOError):
        # Check for stale lock: if lock file is older than 1 hour,
        # the holding process likely crashed without releasing.
        if _break_stale_lock(lock_path, max_age_seconds=3600):
            logger.warning("Broke stale evolution lock (>1hr old) — retrying acquire")
            if lock_fd is not None:
                lock_fd.close()
            # Retry after breaking stale lock
            try:
                lock_fd = open(lock_path, "w")
                flock_exclusive_nb(lock_fd)
                lock_fd.write(str(os.getpid()))
                lock_fd.flush()
            except (OSError, BlockingIOError):
                logger.info("Evolution cycle already running -- skipping (retry failed)")
                if lock_fd is not None:
                    lock_fd.close()
                return CycleReport(
                    cycle_id=cycle_id,
                    skills_checked=0,
                    eligible=0,
                    dry_run=dry_run,
                    errors=["Concurrent cycle in progress -- lock held"],
                )
        else:
            logger.info("Evolution cycle already running -- skipping")
            if lock_fd is not None:
                lock_fd.close()
            return CycleReport(
                cycle_id=cycle_id,
                skills_checked=0,
                eligible=0,
                dry_run=dry_run,
                errors=["Concurrent cycle in progress -- lock held"],
            )

    try:
        return _run_evolution_cycle_locked(skills_dir, transcripts_dir, evals_dir, cycle_id, dry_run=dry_run)
    finally:
        try:
            flock_unlock(lock_fd)
        except OSError:
            pass
        lock_fd.close()


def _run_evolution_cycle_locked(
    skills_dir: Path,
    transcripts_dir: Path,
    evals_dir: Path,
    cycle_id: str,
    *,
    dry_run: bool = True,
) -> CycleReport:
    """Run evolution cycle phases under the lock."""
    from core.session_miner import SessionMiner
    from core.skill_fitness import SkillFitnessEvaluator

    # Force fresh Bedrock client for each cycle (credential rotation safety)
    try:
        from core.llm_optimizer import reset_bedrock_client
        reset_bedrock_client()
    except ImportError:
        pass

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
        from jobs.paths import DB_PATH as _db_path_evo
        db_path = _db_path_evo
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
    transcripts_scanned = miner.last_transcripts_scanned

    # Merge historical corrections from persisted evals that fresh mining missed.
    # This preserves corrections detected in prior runs even if transcripts
    # aged out or parse logic changed (prevents correction amnesia).
    for name, examples in all_examples.items():
        historical = miner.load_historical_corrections(name)
        if historical:
            # Dedup by (prompt[:200], correction[:200]) to avoid double-counting
            existing_keys = {
                (ex.user_prompt[:200], (ex.user_correction or "")[:200])
                for ex in examples
            }
            merged = 0
            for hist_ex in historical:
                key = (hist_ex.user_prompt[:200], (hist_ex.user_correction or "")[:200])
                if key not in existing_keys:
                    examples.append(hist_ex)
                    existing_keys.add(key)
                    merged += 1
            if merged > 0:
                logger.info(
                    "Evolution cycle: merged %d historical corrections for %s",
                    merged, name,
                )

    # Filter eligible (>=5 examples, or >=3 for priority)
    eligible_skills: list[str] = []
    for name, examples in all_examples.items():
        if len(examples) >= 5:
            eligible_skills.append(name)
        elif name in priority_skills and len(examples) >= 3:
            eligible_skills.append(name)

    # ── LLM cost cap: limit how many skills get LLM optimization per cycle ──
    max_llm_skills = 5  # default
    try:
        from core.app_config_manager import app_config_manager
        if app_config_manager is not None:
            evo = app_config_manager.get("evolution", {})
            if isinstance(evo, dict):
                max_llm_skills = int(evo.get("max_llm_skills_per_cycle", 5))
    except (ImportError, Exception):
        pass

    # ── Read previous health for regression detection + trend ──
    previous_health: dict[str, dict] = {}
    health_json_path = evals_dir.parent / "skill_health.json"
    try:
        if health_json_path.exists():
            prev_data = json.loads(health_json_path.read_text(encoding="utf-8"))
            for s in prev_data.get("skills", []):
                previous_health[s["skill_name"]] = s
    except (json.JSONDecodeError, OSError, KeyError):
        pass  # No previous data — skip regression check

    # ── Regression gate: revert previously-deployed skills that degraded ──
    high_threshold, med_threshold = _get_confidence_thresholds()

    # ── Phase 2: ASSESS ──
    high_count = 0
    med_count = 0
    low_count = 0

    skill_assessments: list[tuple[str, float, float, list, OptimizationResult | None]] = []

    # Pre-compute confidence for all eligible skills so we can rank-order
    # for LLM budget allocation (highest confidence gets LLM first).
    #
    # Read runtime correction stats (corrections.jsonl) for recency/repeat
    # boosts.  Graceful degradation: empty dict if file doesn't exist yet.
    try:
        from core.runtime_hooks import read_correction_stats
        correction_stats = read_correction_stats()
    except Exception:
        correction_stats = {}

    skill_pre_assess: list[tuple[str, int, float, float]] = []
    for skill_name in eligible_skills:
        examples = all_examples[skill_name]
        correction_count = sum(1 for ex in examples if ex.user_correction)
        score_pairs = []
        for ex in examples:
            if ex.user_correction and len(ex.agent_actions.strip()) > 20:
                score_pairs.append((ex.user_correction, ex.agent_actions))
        avg_score = evaluator.score_batch(score_pairs) if score_pairs else 1.0

        # Fetch runtime stats for this skill (if any corrections were captured)
        skill_stats = correction_stats.get(skill_name, {})
        confidence = compute_confidence(
            correction_count, len(examples), avg_score,
            recent_corrections=skill_stats.get("recent_corrections", 0),
            repeat_count=skill_stats.get("repeat_count", 0),
        )
        skill_pre_assess.append((skill_name, correction_count, avg_score, confidence))

    # Sort by confidence descending — highest-value skills get LLM budget first
    skill_pre_assess.sort(key=lambda x: -x[3])
    llm_budget_remaining = max_llm_skills

    for skill_name, correction_count, avg_score, confidence in skill_pre_assess:
        examples = all_examples[skill_name]

        # Determine action using config-read thresholds
        if confidence >= high_threshold:
            action = "deploy"
            high_count += 1
        elif confidence >= med_threshold:
            action = "recommend"
            med_count += 1
        else:
            action = "log"
            low_count += 1

        # Compute trend vs previous cycle
        trend = None
        if skill_name in previous_health:
            prev_fitness = previous_health[skill_name].get("fitness_score", 1.0)
            delta = avg_score - prev_fitness
            if delta > 0.05:
                trend = "improving"
            elif delta < -0.05:
                trend = "degrading"
            else:
                trend = "stable"

        # Generate recommendation if corrections exist.
        # LLM optimizer is budget-capped: only top N skills by confidence
        # get LLM optimization (~$0.05/skill). The rest use heuristic only.
        opt_result = None
        recommendation = None
        skill_llm_tokens = 0
        optimizer_used = "none"
        if correction_count > 0:
            # LLM is only worthwhile for deploy/recommend tiers.
            # Log-tier skills (confidence < med_threshold) won't surface
            # changes anywhere — don't waste LLM tokens on them.
            #
            # G4: For recommend-tier (med..high), try heuristic first.
            # The goal of recommend-tier is ANALYSIS (evidence for user),
            # not deployment. Heuristic produces equivalent evidence at
            # zero token cost. Only escalate to LLM if heuristic finds
            # nothing (corrections are semantically complex).
            is_recommend_tier = med_threshold <= confidence < high_threshold
            use_heuristic_only = (
                llm_budget_remaining <= 0
                or confidence < med_threshold
            )
            # Pre-extract corrections once — reused by peek and optimize_skill.
            precomputed_corrections = optimizer._extract_corrections(examples)
            if is_recommend_tier and not use_heuristic_only and precomputed_corrections:
                # Peek: does heuristic find patterns? If so, skip LLM.
                original_text = optimizer._read_skill_text(skill_name)
                if original_text:
                    _, peek_changes = optimizer._apply_heuristic_changes(
                        original_text, precomputed_corrections,
                    )
                    if peek_changes:
                        use_heuristic_only = True  # Heuristic suffices, skip LLM
            opt_result = optimizer.optimize_skill(
                skill_name, examples,
                force_heuristic=use_heuristic_only,
                _precomputed_corrections=precomputed_corrections,
            )
            skill_llm_tokens = optimizer.last_llm_tokens
            if skill_llm_tokens > 0:
                llm_budget_remaining -= 1
                optimizer_used = "llm"
            elif opt_result.changes:
                optimizer_used = "heuristic"

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

        # v2.3: Generate anti-patterns from "remove" corrections
        anti_patterns_count = 0
        if correction_count > 0 and precomputed_corrections:
            anti_gen = AntiPatternGenerator()
            anti_section = anti_gen.generate(precomputed_corrections)
            if anti_section:
                anti_patterns_count = sum(1 for line in anti_section.split("\n") if line.startswith("- "))

        # v2.3: LLM judge scoring (Layer 2) — run for skills with corrections
        llm_judge_score = None
        combined_fitness = None
        if correction_count > 0 and score_pairs:
            try:
                from core.skill_fitness import LLMJudge
                judge = LLMJudge()
                skill_text = optimizer._read_skill_text(skill_name) or ""
                judge_result = judge.score_batch(
                    skill_text=skill_text,
                    examples=score_pairs,
                    corrections=[ex.user_correction for ex in examples if ex.user_correction],
                )
                llm_judge_score = judge_result.layer2_score
                combined_fitness = judge_result.score
            except Exception as exc:
                logger.warning("LLM judge failed for %s: %s", skill_name, exc)

        health_entry = SkillHealthEntry(
            skill_name=skill_name,
            total_examples=len(examples),
            correction_count=correction_count,
            correction_rate=correction_count / max(len(examples), 1),
            fitness_score=avg_score,
            confidence=confidence,
            action=action,
            recommendation=recommendation,
            trend=trend,
            llm_tokens=skill_llm_tokens,
            optimizer_used=optimizer_used,
            llm_judge_score=llm_judge_score,
            combined_fitness=combined_fitness,
            anti_patterns_count=anti_patterns_count,
        )
        health_entries.append(health_entry)
        skill_assessments.append((skill_name, confidence, avg_score, examples, opt_result))

    # ── Phase 3: ACT (confidence-gated) ──
    deployed_count = 0
    verified_count = 0
    rolled_back_count = 0

    # 3a. Regression gate — revert previously-deployed skills that degraded
    for skill_name, confidence, avg_score, examples, opt_result in skill_assessments:
        if skill_name not in previous_health:
            continue
        prev = previous_health[skill_name]
        if prev.get("action") != "deploy":
            continue
        prev_fitness = prev.get("fitness_score", 1.0)
        # Degraded by more than 0.1 → auto-revert from backup
        if avg_score < prev_fitness - 0.1:
            bak_path = skills_dir / f"s_{skill_name}" / "SKILL.md.bak"
            if bak_path.exists():
                skill_path = skills_dir / f"s_{skill_name}" / "SKILL.md"
                try:
                    os.replace(str(bak_path), str(skill_path))
                    rolled_back_count += 1
                    deploy_results.append(DeployResult(
                        skill_name=f"s_{skill_name}",
                        success=False,
                        changes_applied=0,
                        changes_skipped=0,
                        verified=False,
                        rolled_back=True,
                        error=f"Regression auto-revert: fitness {prev_fitness:.2f} → {avg_score:.2f}",
                    ))
                    logger.warning(
                        "Evolution: auto-reverted %s — regression detected "
                        "(fitness %.2f → %.2f)",
                        skill_name, prev_fitness, avg_score,
                    )
                except OSError as exc:
                    errors.append(f"Failed to revert {skill_name}: {exc}")

    # 3b. Deploy new optimizations (or log proposed changes in dry-run mode)
    for skill_name, confidence, avg_score, examples, opt_result in skill_assessments:
        # v2.4: Minimum 2 corrections required for HIGH-confidence proposal path.
        # Single corrections are too thin (could be typo/preference) — surface as
        # MED recommendation only. Prevents proposal flooding from priority skills
        # with 3 examples and 1 correction reaching HIGH via density boost.
        n_corr = sum(1 for ex in examples if ex.user_correction)
        if confidence >= high_threshold and n_corr < 2:
            confidence = med_threshold  # Demote to recommend tier
        if confidence >= high_threshold and opt_result and opt_result.accepted and opt_result.changes:
            # v2.3: Generate and append anti-patterns as an additional TextChange
            precomputed = optimizer._extract_corrections(examples)
            if precomputed:
                anti_gen = AntiPatternGenerator()
                anti_section = anti_gen.generate(precomputed)
                if anti_section:
                    # Read current skill to check for existing anti-patterns section
                    current_text = optimizer._read_skill_text(skill_name) or ""
                    merged = anti_gen.merge_with_existing(current_text, anti_section)
                    if merged != current_text:
                        # Add as a TextChange — appends anti-patterns section
                        diff_start = len(current_text)
                        added_text = merged[diff_start:]
                        if added_text.strip():
                            opt_result.changes.append(TextChange(
                                original="",  # append-only change
                                replacement=added_text.strip(),
                                reason="Auto-generated anti-patterns from correction history (GEPA v2.3)",
                            ))

            if dry_run:
                # DRY RUN: log what would be deployed without writing SKILL.md
                logger.info(
                    "Evolution DRY RUN: would deploy %s (confidence=%.2f, "
                    "score %.2f→%.2f, %d changes)",
                    skill_name, confidence,
                    opt_result.original_score, opt_result.optimized_score,
                    len(opt_result.changes),
                )
                for change in opt_result.changes:
                    logger.info(
                        "  DRY RUN change: %s", change.reason[:100],
                    )
                deploy_results.append(DeployResult(
                    skill_name=f"s_{skill_name}",
                    success=False,
                    changes_applied=0,
                    changes_skipped=len(opt_result.changes),
                    verified=False,
                    rolled_back=False,
                    error="dry_run=True — proposed changes logged, not applied",
                ))
                # Still save evals for audit trail
                miner.save_evals(skill_name, examples)
                continue

            # LIVE: write proposal for human approval instead of silent deploy.
            # Proposals are stored in .context/.evolution_proposals.json and
            # surfaced as a Radar todo. The next cycle (or manual approval)
            # deploys them. This closes the "evolution observes but never acts"
            # gap while keeping human in the loop.

            # ── Freshness check (LL02): re-read current file and filter out
            # changes already present. The target file may have been updated
            # since the MINE phase ran (same session or manual edit).
            current_text = optimizer._read_skill_text(skill_name) or ""
            current_lower = current_text.lower()
            novel_changes = [
                c for c in opt_result.changes
                if c.replacement.strip()
                and c.replacement.strip().lower() not in current_lower
            ]
            if not novel_changes:
                logger.info(
                    "Evolution cycle: all proposed changes for %s already present "
                    "in current file — skipping proposal.",
                    skill_name,
                )
                miner.save_evals(skill_name, examples)
                continue

            proposal = {
                "skill_name": skill_name,
                "confidence": round(confidence, 3),
                "score_before": round(opt_result.original_score, 3),
                "score_after": round(opt_result.optimized_score, 3),
                "changes": [{"reason": c.reason, "preview": c.replacement[:200]} for c in novel_changes],
                "proposed_at": datetime.now(timezone.utc).isoformat(),
            }
            _write_evolution_proposal(evals_dir.parent, proposal)
            deployed_count += 1  # Count as "actioned" for reporting
            miner.save_evals(skill_name, examples)
            logger.info(
                "Evolution cycle: PROPOSED %s for approval (confidence=%.2f, "
                "score %.2f → %.2f, %d changes, %d filtered as already-present). "
                "Radar todo created.",
                skill_name, confidence,
                opt_result.original_score, opt_result.optimized_score,
                len(novel_changes),
                len(opt_result.changes) - len(novel_changes),
            )
        elif confidence >= med_threshold:
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

    # ── Phase 3c: GOVERNANCE MINING (L1) ──
    # Mine EVOLUTION.md for recurring judgment patterns and generate
    # governance proposals (STEERING/AGENT rule candidates).
    # Never writes to governance files — proposals only.
    governance_proposals_count = 0
    try:
        from core.evolution.governance_miner import generate_governance_proposals

        # Locate context files relative to evals_dir
        # evals_dir = ~/.swarm-ai/SwarmWS/.context/SkillEvals → parent = .context/
        ctx_dir = evals_dir.parent
        evolution_md_path = ctx_dir / "EVOLUTION.md"
        steering_md_path = ctx_dir / "STEERING.md"

        if evolution_md_path.exists():
            gov_proposals = generate_governance_proposals(
                evolution_md_path, steering_md_path, threshold=3
            )
            if gov_proposals:
                # Write governance proposals to the same proposals file
                proposals_path = ctx_dir / ".evolution_proposals.json"
                # v3 Phase 2 (adversarial HIGH): route through the SHARED flock-safe
                # _append_proposal helper instead of an unlocked read-text/write-text.
                # The optimizer (evolution cycle) and escalate_class (session hook) write
                # the SAME file; a plain write_text races the flocked writer and clobbers
                # appended proposals. _append_proposal does flock + tmp+atomic-replace +
                # kind-aware (gc_id OR source_class+kind) dedup — one source of truth.
                from core.evolution.governance_router import _append_proposal

                for gp in gov_proposals:
                    _append_proposal(gp.to_proposal_dict(), proposals_path)
                governance_proposals_count = len(gov_proposals)
                logger.info(
                    "Evolution cycle: generated %d governance proposals (L1)",
                    governance_proposals_count,
                )
    except Exception as exc:
        errors.append(f"Governance mining failed: {exc}")
        logger.warning("Governance mining error: %s", exc)

    # ── Phase 3d: CLOSED-LOOP AUDIT (⑥→① feedback edge, design §5) ──
    # The Goodhart guard: a falling correction count is only real evolution if it
    # falls from FEWER MISTAKES (known-class recurrence dropped after a gate), not
    # from LOGGING LESS (capture collapsed). Pure audit over live tracker state;
    # logs the verdict (unhealthy → escalate signal). Non-blocking by design.
    try:
        from core.evolution.correction_tracker import CorrectionClassTracker
        from core.evolution.closed_loop import audit_recurrence

        tracker = CorrectionClassTracker()
        tracker_state = {name: tracker.get_class(name) for name in tracker.class_names()}
        # Capture-rate snapshot: total recorded corrections across tracked classes.
        # prev==cur here (single-cycle view) so capture-collapse cannot false-fire;
        # the cross-period delta is the scheduled-job's concern. This in-cycle audit
        # surfaces gate_failed / recurring, which are period-independent.
        total = sum((e or {}).get("count", 0) for e in tracker_state.values())
        recurrence_audit = audit_recurrence(
            tracker_state,
            capture_stats={"total_this_period": total, "total_prev_period": total},
        )
        if not recurrence_audit["healthy"]:
            logger.warning(
                "Closed-loop audit UNHEALTHY [%s]: %s",
                recurrence_audit["reason_class"], recurrence_audit["detail"],
            )
        else:
            logger.info(
                "Closed-loop audit healthy [%s]", recurrence_audit["reason_class"]
            )
    except Exception as exc:
        errors.append(f"Closed-loop audit failed: {exc}")
        logger.warning("Closed-loop audit error: %s", exc)

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
        dry_run=dry_run,
        errors=errors,
        health_report_path=health_report_path,
    )
    logger.info("Evolution cycle complete: %s", report.to_dict())

    # Clean up stale .bak files from PREVIOUS cycles only.
    # Keep .bak files for skills deployed THIS cycle — needed for regression
    # gate in the NEXT cycle. Only clean pre-existing .bak files.
    deployed_this_cycle = {d.skill_name for d in deploy_results if d.success}
    for bak_file in skills_dir.rglob("*.md.bak"):
        skill_folder = bak_file.parent.name
        if skill_folder in deployed_this_cycle:
            continue  # Keep for regression check next cycle
        try:
            bak_file.unlink()
            logger.debug("Cleaned up stale backup: %s", bak_file)
        except OSError as exc:
            logger.debug("Failed to clean up %s: %s", bak_file, exc)

    return report


def _write_evolution_proposal(ctx_dir: Path, proposal: dict) -> None:
    """Write an evolution proposal to its persistent home for approval.

    Proposals accumulate in .context/.evolution_proposals.json. Each proposal
    is a skill optimization that reached HIGH confidence but awaits human
    approval before deployment. This file IS the proposal's home — the human
    reviews it there. No Radar todo is written (run_50db230a): the ToDo card is
    a pure user-planning surface, not a system-proposal feed.
    """
    proposals_path = ctx_dir / ".evolution_proposals.json"
    proposals = []
    if proposals_path.exists():
        try:
            proposals = json.loads(proposals_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # Deduplicate: replace existing proposal for same skill
    proposals = [p for p in proposals if p.get("skill_name") != proposal["skill_name"]]
    proposals.append(proposal)
    proposals_path.write_text(json.dumps(proposals, indent=2), encoding="utf-8")


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
                    "llm_tokens": s.llm_tokens,
                    "optimizer_used": s.optimizer_used,
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

        total_llm_tokens = sum(s.llm_tokens for s in report.skills)
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
            "llm_tokens": total_llm_tokens,
            "errors": report.errors,
            "source": "evolution_optimizer_v2",
        }

        changelog_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = changelog_path.with_suffix(".jsonl.lock")
        try:
            with open(lock_path, "w") as lock_fd:
                flock_exclusive(lock_fd)
                try:
                    with open(changelog_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(entry) + "\n")
                    # Rotate inside lock to prevent concurrent rotation
                    from utils.jsonl_rotation import rotate_jsonl_if_oversized
                    rotate_jsonl_if_oversized(changelog_path)
                finally:
                    flock_unlock(lock_fd)
        except OSError:
            with open(changelog_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

    except Exception as exc:
        logger.debug("Failed to write evolution changelog: %s", exc)
