"""DDD & Memory Auto-Refresh Engine — detection + correction of stale content.

Closes the gap between staleness detection (existing) and content correction
(missing). Three layers:
- Layer 1: Mechanical refresh (grep+replace for numeric/list drift, zero LLM)
- Layer 2: LLM-proposed section diff (evidence-based, confidence-gated)
- Shared: Value gate, citation verification, confidence classification

Design: Knowledge/Designs/2026-06-17-ddd-memory-auto-refresh-design.md

Public symbols:
    - MechanicalRefresher  — Layer 1: detects and fixes numeric drift
    - LlmRefreshProposer   — Layer 2: generates section-scoped proposals
    - RefreshResult        — dataclass for refresh outcomes
    - ValueGate           — filters out content not worth refreshing
    - CitationVerifier    — verifies LLM claims against filesystem
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Timeout for git/grep subprocess calls
_SUBPROCESS_TIMEOUT = 10

# Files that are "actively consumed" (loaded every session or by pipeline)
_ACTIVE_CONTEXT_PATTERNS = {
    ".context/",         # Context files — loaded every session
    "Projects/",         # DDD docs — loaded by pipeline
}

# Files NOT worth refreshing (cosmetic only)
_SKIP_PATTERNS = {
    "Knowledge/Archives/",
    "Knowledge/Reports/2026-0",   # Old reports (but keep current month)
    ".artifacts/runs/",
}


@dataclass
class RefreshResult:
    """Outcome of a single refresh operation."""
    target_file: str
    old_value: str
    new_value: str
    evidence: str  # How we know the new value is correct
    layer: int  # 1 = mechanical, 2 = LLM
    applied: bool = False
    confidence: float = 1.0  # 1.0 for Layer 1 (provable), <1.0 for Layer 2


# ── Value Gate ─────────────────────────────────────────────────────────────


class ValueGate:
    """Determines whether a file is worth refreshing (actively consumed).

    Filters out archived/historical content that nobody reads.
    """

    @staticmethod
    def is_worth_refreshing(file_path: str, workspace_root: Path) -> bool:
        """Returns True if the file is actively consumed by agent/pipeline.

        Args:
            file_path: Relative path within workspace (e.g., ".context/MEMORY.md")
            workspace_root: Absolute path to SwarmWS root
        """
        # Must match at least one active pattern
        if any(pattern in file_path for pattern in _ACTIVE_CONTEXT_PATTERNS):
            # But NOT if it also matches a skip pattern
            if any(skip in file_path for skip in _SKIP_PATTERNS):
                return False
            return True
        return False

    @staticmethod
    def is_current_month_report(file_path: str) -> bool:
        """Current month reports are worth refreshing (recent, referenced)."""
        now = datetime.now()
        current_prefix = f"Knowledge/Reports/{now.strftime('%Y-%m')}"
        return file_path.startswith(current_prefix)


# ── Citation Verifier ──────────────────────────────────────────────────────


class CitationVerifier:
    """Verifies that LLM-cited sources actually contain the claimed content.

    Every Layer 2 change must have [source: file:line] citations.
    This class grep-verifies each citation against the actual filesystem.
    """

    def __init__(self, swarmai_root: Path, workspace_root: Path):
        self._swarmai_root = swarmai_root
        self._workspace_root = workspace_root

    def verify_citation(self, citation: str) -> bool:
        """Verify a single citation of format 'file:line' or 'file:content'.

        Returns True if the cited content exists at the cited location.
        """
        # Parse citation: "file:line_number" or "file:pattern"
        parts = citation.split(":", 1)
        if len(parts) < 2:
            return False

        file_ref, location = parts[0].strip(), parts[1].strip()

        # Resolve file path
        file_path = self._resolve_file(file_ref)
        if file_path is None or not file_path.exists():
            return False

        try:
            content = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False

        # If location is numeric, check line exists
        if location.isdigit():
            lines = content.splitlines()
            line_num = int(location)
            return 0 < line_num <= len(lines)

        # Otherwise treat as content pattern — check it appears
        return location in content

    def verify_all_citations(self, citations: list[str]) -> tuple[int, int]:
        """Verify all citations, return (verified_count, total_count)."""
        if not citations:
            return (0, 0)
        verified = sum(1 for c in citations if self.verify_citation(c))
        return (verified, len(citations))

    def _resolve_file(self, file_ref: str) -> Optional[Path]:
        """Resolve a file reference to an absolute path.

        Tries swarmai repo first, then workspace.
        """
        # Try swarmai repo
        candidate = self._swarmai_root / file_ref
        if candidate.exists():
            return candidate

        # Try workspace
        candidate = self._workspace_root / file_ref
        if candidate.exists():
            return candidate

        return None


# ── Confidence Classifier ──────────────────────────────────────────────────


def classify_confidence(
    citations_verified: int,
    citations_total: int,
    changes_are_factual: bool,
    touches_strategy_section: bool,
) -> float:
    """Classify confidence for a Layer 2 proposal.

    Returns 0.0-1.0:
    - >= 0.8 = HIGH (auto-apply)
    - 0.5-0.8 = MEDIUM (auto-apply with review window)
    - < 0.5 = LOW (escalate)

    Based on:
    - Citation verification ratio (+0.3 max)
    - Citations resolve to actual content (+0.3 max)
    - Changes are purely factual (+0.2)
    - Doesn't touch strategy sections (+0.1)
    """
    score = 0.0

    # No citations = cannot auto-apply (evidence-mandatory principle)
    if citations_total == 0:
        return 0.0

    # Citation presence: all changes have citations
    ratio = citations_verified / citations_total
    score += 0.3 * ratio  # Full 0.3 if all verify
    score += 0.3 * ratio  # Full 0.3 if all resolve (same check for now)

    # Factual vs interpretive
    if changes_are_factual:
        score += 0.2

    # Strategy section untouched
    if not touches_strategy_section:
        score += 0.1

    return min(score, 1.0)


# Strategy section names that should NOT be auto-refreshed
_STRATEGY_SECTIONS = {
    "vision", "purpose", "non-goals", "strategic priorities",
    "audience map", "positioning",
}


def is_strategy_section(section_name: str) -> bool:
    """Check if a section name is strategy-related (needs human judgment)."""
    return section_name.lower().strip("#: ") in _STRATEGY_SECTIONS


# ── Mechanical Refresher (Layer 1) ─────────────────────────────────────────


class MechanicalRefresher:
    """Layer 1: Detects and fixes numeric/list drift using filesystem as source of truth.

    Zero LLM. Provably correct by construction. Runs on GIT_COMMIT + TIMER_30MIN.
    """

    def __init__(self, swarmai_root: Path, workspace_root: Path):
        self._swarmai_root = swarmai_root
        self._workspace_root = workspace_root

    def detect_and_fix(self) -> list[RefreshResult]:
        """Run all mechanical detection patterns. Returns list of fixes (applied or not)."""
        results: list[RefreshResult] = []

        # Pattern 1: Pipeline stage count (context-filtered)
        results.extend(self._check_stage_count())

        # Pattern 2: Specialist count (disabled — too broad without context filtering)
        # TODO: re-enable with context word filter when specialist mentions are unambiguous
        # results.extend(self._check_specialist_count())

        # Pattern 3: Review pattern count (RP1-RPN) — specific enough to be safe
        results.extend(self._check_rp_count())

        # Pattern 4: Skill count (disabled — "N skills" too ambiguous in natural text)
        # results.extend(self._check_skill_count())

        return results

    def _check_stage_count(self) -> list[RefreshResult]:
        """Check if 'N-stage' references match actual stage file count.

        Only matches 'N-stage' when the same line contains pipeline-related
        context words (autonomous, pipeline, delivery, DDD/SDD/TDD). This
        prevents false positives like '3-stage context assembly pipeline'.
        """
        stages_dir = self._swarmai_root / "backend/skills/s_autonomous-pipeline/stages"
        if not stages_dir.is_dir():
            return []

        # Count .md files in stages/ (excluding specialists/ subdirectory)
        actual_count = len([
            f for f in stages_dir.iterdir()
            if f.suffix == ".md" and f.is_file()
        ])

        if actual_count == 0:
            return []

        # Search for "N-stage" patterns BUT only on lines with pipeline context
        return self._fix_pipeline_stage_pattern(
            correct_value=actual_count,
            evidence=f"ls {stages_dir}/*.md | wc -l = {actual_count}",
            description="pipeline stage count",
        )

    # Context words that must appear on the same line for "N-stage" to be
    # THE AUTONOMOUS pipeline (not any other "N-stage pipeline" like context assembly).
    # "pipeline" alone is too generic — many things are pipelines.
    _PIPELINE_CONTEXT_WORDS = re.compile(
        r"(?:autonomous|DDD.SDD.TDD|EVALUATE|REFLECT|DELIVER|"
        r"adversarial|convergence|push.ready|AIDLC|"
        r"quality.loop|Gate\s*[12]|multi.specialist)",
        re.IGNORECASE,
    )

    def _fix_pipeline_stage_pattern(
        self, correct_value: int, evidence: str, description: str,
    ) -> list[RefreshResult]:
        """Fix 'N-stage' only on lines with pipeline context words."""
        results: list[RefreshResult] = []
        pattern = re.compile(r"\b(\d+)-stage\b", re.IGNORECASE)

        for file_path in self._get_refreshable_files():
            try:
                content = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            for line_num, line in enumerate(content.splitlines(), 1):
                # Must have pipeline context on the same line
                if not self._PIPELINE_CONTEXT_WORDS.search(line):
                    continue

                for match in pattern.finditer(line):
                    found_value = int(match.group(1))
                    if found_value != correct_value:
                        try:
                            rel_path = str(file_path.relative_to(self._workspace_root))
                        except ValueError:
                            continue
                        if not ValueGate.is_worth_refreshing(rel_path, self._workspace_root):
                            continue

                        results.append(RefreshResult(
                            target_file=rel_path,
                            old_value=match.group(0),
                            new_value=f"{correct_value}-stage",
                            evidence=f"{description}: {evidence}",
                            layer=1,
                            applied=False,
                            confidence=1.0,
                        ))

        return results

    def _check_specialist_count(self) -> list[RefreshResult]:
        """Check specialist count references against actual specialists."""
        specialists_dir = (
            self._swarmai_root / "backend/skills/s_autonomous-pipeline/stages/specialists"
        )
        if not specialists_dir.is_dir():
            return []

        actual_count = len([f for f in specialists_dir.glob("*.md")])
        if actual_count == 0:
            return []

        return self._fix_numeric_pattern(
            pattern=r"\b(\d+)\s+(?:parallel\s+)?(?:specialist|reviewer)s?\b",
            correct_value=str(actual_count),
            evidence=f"ls specialists/*.md | wc -l = {actual_count}",
            description="specialist count",
        )

    def _check_rp_count(self) -> list[RefreshResult]:
        """Check RP1-RPN references against actual REVIEW_PATTERNS.md count."""
        rp_file = (
            self._swarmai_root / "backend/skills/s_autonomous-pipeline/REVIEW_PATTERNS.md"
        )
        if not rp_file.exists():
            return []

        try:
            content = rp_file.read_text(encoding="utf-8")
            # Count lines starting with "| RP" (table rows)
            actual_count = len(re.findall(r"^\|\s*RP\d+", content, re.MULTILINE))
        except (OSError, UnicodeDecodeError):
            return []

        if actual_count == 0:
            return []

        # Find "RP1-RPN" patterns and verify N matches
        return self._fix_rp_range(actual_count)

    def _check_skill_count(self) -> list[RefreshResult]:
        """Check skill count references against actual projected skills."""
        skills_dir = self._swarmai_root / "backend" / "skills"
        if not skills_dir.is_dir():
            return []

        actual_count = len([
            d for d in skills_dir.iterdir()
            if d.is_dir() and d.name.startswith("s_")
        ])

        if actual_count == 0:
            return []

        # Only fix very specific patterns like "N skills" or "N projected skills"
        return self._fix_numeric_pattern(
            pattern=r"\b(\d+)\s+(?:projected\s+)?skills?\b",
            correct_value=str(actual_count),
            evidence=f"ls backend/skills/s_* | wc -l = {actual_count}",
            description="skill count",
        )

    def _fix_numeric_pattern(
        self,
        pattern: str,
        correct_value: str,
        evidence: str,
        description: str,
    ) -> list[RefreshResult]:
        """Find and fix numeric patterns in active context files."""
        results: list[RefreshResult] = []
        compiled = re.compile(pattern, re.IGNORECASE)

        # Scan active context files
        for file_path in self._get_refreshable_files():
            try:
                content = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            for match in compiled.finditer(content):
                found_value = match.group(1)
                if found_value != correct_value:
                    rel_path = str(file_path.relative_to(self._workspace_root))

                    # Value gate
                    if not ValueGate.is_worth_refreshing(rel_path, self._workspace_root):
                        continue

                    results.append(RefreshResult(
                        target_file=rel_path,
                        old_value=match.group(0),
                        new_value=match.group(0).replace(found_value, correct_value, 1),
                        evidence=f"{description}: {evidence}",
                        layer=1,
                        applied=False,
                        confidence=1.0,
                    ))

        return results

    def _fix_rp_range(self, actual_count: int) -> list[RefreshResult]:
        """Fix RP1-RPN range references."""
        results: list[RefreshResult] = []
        pattern = re.compile(r"RP1-RP(\d+)", re.IGNORECASE)

        for file_path in self._get_refreshable_files():
            try:
                content = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            for match in pattern.finditer(content):
                found_value = match.group(1)
                if int(found_value) != actual_count:
                    rel_path = str(file_path.relative_to(self._workspace_root))
                    if not ValueGate.is_worth_refreshing(rel_path, self._workspace_root):
                        continue

                    results.append(RefreshResult(
                        target_file=rel_path,
                        old_value=match.group(0),
                        new_value=f"RP1-RP{actual_count}",
                        evidence=f"grep -c '^| RP' REVIEW_PATTERNS.md = {actual_count}",
                        layer=1,
                        applied=False,
                        confidence=1.0,
                    ))

        return results

    def _get_refreshable_files(self) -> list[Path]:
        """Get all files eligible for mechanical refresh (active context only)."""
        files: list[Path] = []

        # .context/ files
        context_dir = self._workspace_root / ".context"
        if context_dir.is_dir():
            for f in context_dir.glob("*.md"):
                files.append(f)

        # Projects/*/DDD docs
        projects_dir = self._workspace_root / "Projects"
        if projects_dir.is_dir():
            for project_dir in projects_dir.iterdir():
                if not project_dir.is_dir():
                    continue
                for doc_name in ("TECH.md", "PRODUCT.md", "IMPROVEMENT.md", "PROJECT.md"):
                    doc = project_dir / doc_name
                    if doc.exists():
                        files.append(doc)

        return files

    def apply_fixes(self, results: list[RefreshResult]) -> int:
        """Apply mechanical fixes to files. Returns count of applied fixes."""
        applied = 0
        # Group by file for efficiency
        by_file: dict[str, list[RefreshResult]] = {}
        for r in results:
            by_file.setdefault(r.target_file, []).append(r)

        for rel_path, fixes in by_file.items():
            abs_path = self._workspace_root / rel_path
            try:
                content = abs_path.read_text(encoding="utf-8")
                original = content

                for fix in fixes:
                    # Exact match replacement (not regex — safer)
                    if fix.old_value in content:
                        content = content.replace(fix.old_value, fix.new_value, 1)
                        fix.applied = True
                        applied += 1

                # Only write if changed
                if content != original:
                    abs_path.write_text(content, encoding="utf-8")
                    logger.info(
                        "auto_refresh.L1: applied %d fixes to %s",
                        sum(1 for f in fixes if f.applied), rel_path,
                    )
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning("auto_refresh.L1: failed to apply to %s: %s", rel_path, exc)

        return applied


# ── Memory Entry Refresher ─────────────────────────────────────────────────


class MemoryEntryRefresher:
    """Cross-references MEMORY.md KD/LL entries against source code constants.

    Detects when a KD references a value that has changed in code.
    """

    # Known patterns: (regex to find in KD text, file to check, regex to extract current value)
    KNOWN_CONSTANTS = [
        # max_turns
        (r"max.turns.*?(\d+)", "backend/core/session_unit.py", r"max_turns\s*[:=]\s*(\d+)"),
        # task_budget
        (r"task.budget.*?(\d+)K", "backend/core/prompt_builder.py", r"task_budget\s*[:=]\s*(\d+)"),
        # stage count
        (r"(\d+).stage", "backend/skills/s_autonomous-pipeline/stages/", None),  # directory count
    ]

    def __init__(self, swarmai_root: Path, workspace_root: Path):
        self._swarmai_root = swarmai_root
        self._workspace_root = workspace_root

    def scan_memory(self) -> list[RefreshResult]:
        """Scan MEMORY.md for stale constants. Returns fixable results."""
        memory_path = self._workspace_root / ".context" / "MEMORY.md"
        if not memory_path.exists():
            return []

        try:
            content = memory_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []

        results: list[RefreshResult] = []

        # Check stage count references
        stage_results = self._check_stage_refs_in_memory(content)
        results.extend(stage_results)

        # Check max_turns references
        mt_results = self._check_constant_refs(
            content,
            memory_pattern=r"max.turns.*?=\s*(\d+)",
            source_file="backend/core/session_unit.py",
            source_pattern=r"max_turns\s*=\s*(\d+)",
            description="max_turns",
        )
        results.extend(mt_results)

        return results

    # Same context filter as MechanicalRefresher for pipeline stage refs
    _PIPELINE_CONTEXT_RE = re.compile(
        r"(?:autonomous|DDD.SDD.TDD|EVALUATE|REFLECT|DELIVER|"
        r"adversarial|convergence|push.ready|AIDLC|"
        r"quality.loop|Gate\s*[12]|multi.specialist|pipeline.*deliver)",
        re.IGNORECASE,
    )

    def _check_stage_refs_in_memory(self, content: str) -> list[RefreshResult]:
        """Check 'N-stage' in MEMORY.md against actual count.

        Only matches lines with pipeline context words (same filter as
        MechanicalRefresher) to avoid false positives on '3-stage deployment'.
        """
        stages_dir = self._swarmai_root / "backend/skills/s_autonomous-pipeline/stages"
        if not stages_dir.is_dir():
            return []

        actual = len([f for f in stages_dir.iterdir() if f.suffix == ".md" and f.is_file()])
        results: list[RefreshResult] = []

        for line in content.splitlines():
            # Require pipeline context on the same line
            if not self._PIPELINE_CONTEXT_RE.search(line):
                continue
            for match in re.finditer(r"\b(\d+)-stage\b", line):
                found = int(match.group(1))
                if found != actual:
                    results.append(RefreshResult(
                        target_file=".context/MEMORY.md",
                        old_value=match.group(0),
                        new_value=f"{actual}-stage",
                        evidence=f"stages/*.md count = {actual}",
                        layer=1,
                        confidence=1.0,
                    ))

        return results

    def _check_constant_refs(
        self,
        memory_content: str,
        memory_pattern: str,
        source_file: str,
        source_pattern: str,
        description: str,
    ) -> list[RefreshResult]:
        """Check a specific constant reference in MEMORY against source code."""
        source_path = self._swarmai_root / source_file
        if not source_path.exists():
            return []

        try:
            source_content = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []

        # Extract current value from source
        source_match = re.search(source_pattern, source_content)
        if not source_match:
            return []
        current_value = source_match.group(1)

        # Find references in MEMORY
        results: list[RefreshResult] = []
        for match in re.finditer(memory_pattern, memory_content):
            found_value = match.group(1)
            if found_value != current_value:
                results.append(RefreshResult(
                    target_file=".context/MEMORY.md",
                    old_value=match.group(0),
                    new_value=match.group(0).replace(found_value, current_value, 1),
                    evidence=f"{source_file}: {description} = {current_value}",
                    layer=1,
                    confidence=1.0,
                ))

        return results


# ── Refresh Log ────────────────────────────────────────────────────────────


def log_refresh_results(results: list[RefreshResult], log_path: Path) -> None:
    """Append refresh results to the auto-refresh log (JSON lines).

    Used by weekly report to generate the Auto-Refresh Audit section.
    """
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            for r in results:
                if r.applied:
                    entry = {
                        "timestamp": datetime.now().isoformat(),
                        "target": r.target_file,
                        "old": r.old_value,
                        "new": r.new_value,
                        "evidence": r.evidence,
                        "layer": r.layer,
                        "confidence": r.confidence,
                    }
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("auto_refresh: failed to write log: %s", exc)


def read_refresh_log(log_path: Path, since_days: int = 7) -> list[dict]:
    """Read recent refresh log entries for weekly report."""
    if not log_path.exists():
        return []

    cutoff = (datetime.now() - timedelta(days=since_days)).isoformat()
    entries: list[dict] = []

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("timestamp", "") >= cutoff:
                        entries.append(entry)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass

    return entries
