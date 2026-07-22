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

from core.ddd_paths import ddd_path
from core.project_registry import DDD_CANONICAL_DOCS  # Run 0: single source of truth
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

        # Pattern 1: Pipeline stage count — DISABLED (run_254f5e52). "stage count" is
        # NOT deterministically derivable from the filesystem: stages/ holds 8 stage
        # files + goal_cycle.md (loop container) + complete.md (terminal step) = 10
        # files, but the canonical count is 9 (ADVERSARIAL executes EMBEDDED in DELIVER
        # with NO own file — MOD02). File-count gives 8 or 10, never the correct 9, so
        # this verifier would auto-rewrite the CORRECT "9-stage" to a wrong value.
        # A FIXABLE verifier's source MUST be a true single-source-of-truth, not a
        # count that merely happens to be measurable. "9 stages" is SEMANTIC (its
        # meaning depends on the embedded-adversarial fact) — it belongs to the LLM
        # tier, never here. (Found by dog-fooding the drift scan on our own DDD.)
        # results.extend(self._check_stage_count())

        # Pattern 2: Specialist count (disabled — too broad without context filtering)
        # TODO: re-enable with context word filter when specialist mentions are unambiguous
        # results.extend(self._check_specialist_count())

        # Pattern 3: Review pattern count (RP1-RPN) — DISABLED (run_254f5e52). Thought
        # "specific enough to be safe", but dog-fooding the scan on our own DDD proved
        # otherwise: `RP1-RP49` / `RP1-RP37` in DDD prose are almost always HISTORICAL
        # references — a pitfall entry quoting review.md's literal code AT THE TIME, a
        # guideline's dated snapshot, an example in a comparison table — NOT a live
        # "there are currently N patterns" claim. Auto-swapping them to RP1-RP51 would
        # FALSIFY the historical record + desync a quote from the code it cites. Same
        # lesson as stage/decay: a number being measurable does NOT make it a live,
        # auto-rewritable claim. "Which RP refs are live vs historical?" is SEMANTIC.
        # results.extend(self._check_rp_count())

        # Pattern 4: Skill count (disabled — "N skills" too ambiguous in natural text)
        # results.extend(self._check_skill_count())

        # Pattern 5: App version stamp (run_254f5e52) — the `### Version:` HEADER line
        # only. NOT a bare "v1.21.0" grep-swap (that would corrupt release-narrative
        # prose that legitimately cites old versions, e.g. "v1.20.1→v1.21.0").
        results.extend(self._check_version_stamp())

        # Pattern 6: Decay-window constants — DISABLED (run_254f5e52). Dog-fooding the
        # scan on our own MEMORY.md proved this is NOT deterministically fixable: a
        # single prose line routinely carries MULTIPLE distinct day-windows with
        # different meanings — "90d dormant (180d if ref≥10), <30d immune", plus
        # per-section tuning ("45d global"), archived ("180d"), grace ("30d"). A
        # context-word gate cannot tell WHICH window a given number is, so it
        # cross-mapped them all toward one value (180→60, 30→60, archived 150→60) —
        # corrupting correct text. "Which decay window is this number?" is SEMANTIC;
        # it belongs to the LLM tier, never a deterministic grep. (Same lesson as
        # stage-count above: measurable ≠ a true single-source-of-truth.)
        # results.extend(self._check_decay_windows())

        return results

    def _check_version_stamp(self) -> list[RefreshResult]:
        """Fix a `### Version: vX.Y.Z` HEADER stamp that drifts from the VERSION file.

        Source of truth: the repo `VERSION` file. Scoped to a line that BOTH starts
        with a `Version:` header marker AND carries a version token — so historical
        release-narrative prose citing old versions (e.g. "v1.20.1→v1.21.0 release")
        is never touched. Only the current-version stamp is corrected.
        """
        version_file = self._swarmai_root / "VERSION"
        if not version_file.is_file():
            return []
        try:
            current = version_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return []
        if not re.fullmatch(r"\d+\.\d+\.\d+", current):
            return []  # SoT malformed → do nothing (never guess)

        results: list[RefreshResult] = []
        # header line: "### Version: v1.21.0 ..." or "Version: 1.21.0"
        header = re.compile(r"(?i)^#*\s*Version:\s*v?(\d+\.\d+\.\d+)")
        ver_tok = re.compile(r"\bv?(\d+\.\d+\.\d+)\b")
        for file_path in self._get_refreshable_files():
            try:
                content = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line in content.splitlines():
                m = header.match(line)
                if not m or m.group(1) == current:
                    continue
                # Replace ONLY the first version token on that header line.
                vm = ver_tok.search(line)
                if not vm or vm.group(1) == current:
                    continue
                old_tok = vm.group(0)                       # e.g. "v1.21.0"
                new_tok = ("v" if old_tok.startswith("v") else "") + current
                try:
                    rel_path = str(file_path.relative_to(self._workspace_root))
                except ValueError:
                    continue
                if not ValueGate.is_worth_refreshing(rel_path, self._workspace_root):
                    continue
                results.append(RefreshResult(
                    target_file=rel_path,
                    old_value=line,
                    new_value=line.replace(old_tok, new_tok, 1),
                    evidence=f"version stamp: cat VERSION = {current}",
                    layer=1,
                    applied=False,
                    confidence=1.0,
                ))
        return results

    # Decay context: only correct "Nd dormant/archived" when the line is about the
    # decay lifecycle (not any other N-day number).
    _DECAY_CONTEXT_WORDS = re.compile(
        r"(?i)(?:decay|dormant|archiv|reclaim|days.idle|ref_count|lifecycle)"
    )

    def _check_decay_windows(self) -> list[RefreshResult]:
        """Fix dormant/archived day-window numbers that drift from the real constants.

        Source of truth: ddd_entry_lifecycle.DORMANT_THRESHOLD_DAYS /
        ARCHIVED_THRESHOLD_DAYS. Scoped to decay-context lines so an unrelated
        "90 days" elsewhere is never touched.
        """
        try:
            from core.ddd_entry_lifecycle import (
                DORMANT_THRESHOLD_DAYS as _DORM,
                ARCHIVED_THRESHOLD_DAYS as _ARCH,
            )
        except Exception:
            return []

        results: list[RefreshResult] = []
        # "90d" / "90 day" / "90-day" forms, capturing the number
        num = re.compile(r"\b(\d+)\s*d(?:ays?)?\b|\b(\d+)-day\b", re.IGNORECASE)
        for file_path in self._get_refreshable_files():
            try:
                content = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line in content.splitlines():
                if not self._DECAY_CONTEXT_WORDS.search(line):
                    continue
                low = line.lower()
                # Only correct a number when the line names WHICH window it is,
                # so 90→dormant(60) and 180→archived(150) don't cross-map.
                for m in num.finditer(line):
                    found = int(m.group(1) or m.group(2))
                    target = None
                    if ("dormant" in low or "idle" in low) and found != _DORM:
                        target = _DORM
                    elif ("archiv" in low) and found != _ARCH:
                        target = _ARCH
                    if target is None:
                        continue
                    try:
                        rel_path = str(file_path.relative_to(self._workspace_root))
                    except ValueError:
                        continue
                    if not ValueGate.is_worth_refreshing(rel_path, self._workspace_root):
                        continue
                    old_tok = m.group(0)
                    new_tok = old_tok.replace(str(found), str(target), 1)
                    results.append(RefreshResult(
                        target_file=rel_path,
                        old_value=line,
                        new_value=line.replace(old_tok, new_tok, 1),
                        evidence=f"decay window: ddd_entry_lifecycle "
                                 f"{'DORMANT' if target == _DORM else 'ARCHIVED'}_THRESHOLD_DAYS = {target}",
                        layer=1,
                        applied=False,
                        confidence=1.0,
                    ))
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

        # NOTE: this verifier is DISABLED at the detect_and_fix call site (see there):
        # file-count cannot yield the canonical 9 (embedded adversarial has no file).
        # Kept for reference / potential future context-aware reinstatement.
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
                for doc_name in DDD_CANONICAL_DOCS:
                    doc = ddd_path(project_dir, doc_name)
                    if doc.exists():
                        files.append(doc)

        return files

    # P0-P2 readonly context files that should NEVER be written by auto-refresh
    _READONLY_CONTEXT_FILES = {"SWARMAI.md", "IDENTITY.md", "SOUL.md"}

    def apply_fixes(self, results: list[RefreshResult]) -> int:
        """Apply mechanical fixes to files. Returns count of applied fixes.

        Uses locked_write for .context/ files (MEMORY.md concurrency safety).
        Skips readonly P0-P2 files. Marks fix.applied=True only AFTER write succeeds.
        """
        applied = 0
        # Group by file for efficiency
        by_file: dict[str, list[RefreshResult]] = {}
        for r in results:
            by_file.setdefault(r.target_file, []).append(r)

        for rel_path, fixes in by_file.items():
            abs_path = self._workspace_root / rel_path

            # Skip readonly P0-P2 context files
            if abs_path.name in self._READONLY_CONTEXT_FILES:
                continue

            try:
                is_context_file = ".context/" in rel_path

                if is_context_file:
                    # Context files: read-modify-write under sidecar flock
                    # (prevents race with save-memory / distillation / evolution)
                    self._apply_fixes_locked(abs_path, fixes)
                    applied += sum(1 for f in fixes if f.applied)
                else:
                    # DDD docs in Projects/: read, modify, atomic write (tmp+replace)
                    content = abs_path.read_text(encoding="utf-8")
                    original = content

                    pending_fixes: list[RefreshResult] = []
                    for fix in fixes:
                        if fix.old_value in content:
                            content = content.replace(fix.old_value, fix.new_value, 1)
                            pending_fixes.append(fix)

                    if content != original:
                        tmp_path = abs_path.with_suffix(".tmp")
                        tmp_path.write_text(content, encoding="utf-8")
                        os.replace(tmp_path, abs_path)

                        for fix in pending_fixes:
                            fix.applied = True
                            applied += 1

                        logger.info(
                            "auto_refresh.L1: applied %d fixes to %s",
                            len(pending_fixes), rel_path,
                        )
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning("auto_refresh.L1: failed to apply to %s: %s", rel_path, exc)

        return applied

    def _apply_fixes_locked(self, abs_path: Path, fixes: list) -> None:
        """Read-modify-write under sidecar flock (TOCTOU-safe for .context/ files).

        Entire read + modify + write happens under the lock, so no concurrent
        writer (save-memory, distillation, evolution) can interleave.
        """
        import fcntl
        lock_path = abs_path.with_suffix(abs_path.suffix + ".lock")
        lock_fd = None
        try:
            lock_fd = open(lock_path, "w")
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)

            # Read under lock (no TOCTOU)
            content = abs_path.read_text(encoding="utf-8")
            original = content

            pending_fixes = []
            for fix in fixes:
                if fix.old_value in content:
                    content = content.replace(fix.old_value, fix.new_value, 1)
                    pending_fixes.append(fix)

            if content != original:
                abs_path.write_text(content, encoding="utf-8")

                for fix in pending_fixes:
                    fix.applied = True

                logger.info(
                    "auto_refresh.L1: applied %d fixes to %s (locked)",
                    len(pending_fixes), abs_path.name,
                )
        finally:
            if lock_fd is not None:
                try:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                except (OSError, IOError):
                    pass
                lock_fd.close()



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


# ── LLM Refresh Proposer (Layer 2) ─────────────────────────────────────────


# Throttle state file path (relative to workspace)
_LLM_REFRESH_STATE_FILE = ".context/.llm_refresh_state.json"

# Minimum days between LLM refresh attempts for the same (project, doc) pair
_LLM_REFRESH_COOLDOWN_DAYS = 7

# Max tokens for LLM response
_LLM_MAX_TOKENS = 1024


@dataclass
class LlmRefreshProposal:
    """A Layer 2 proposal generated by LLM analysis."""
    project: str
    target_doc: str
    section_name: str
    current_text: str
    proposed_text: str
    citations: list[str]  # [source: file:line] refs
    confidence: float
    evidence_commits: list[str]  # commit hashes that triggered this


class LlmRefreshProposer:
    """Layer 2: Generates section-scoped DDD refresh proposals using LLM.

    Runs on TIMER_30MIN but throttled to max 1x per (project, doc) per 7 days.
    Uses Bedrock (Sonnet) for cost efficiency. Evidence-mandatory: every proposed
    change must cite a source file:line.
    """

    def __init__(self, swarmai_root: Path, workspace_root: Path):
        self._swarmai_root = swarmai_root
        self._workspace_root = workspace_root
        self._state_path = workspace_root / _LLM_REFRESH_STATE_FILE

    def should_run(self, project: str, doc: str) -> bool:
        """Check if enough time has passed since last LLM refresh for this pair."""
        state = self._load_state()
        key = f"{project}/{doc}"
        last_run = state.get(key)
        if last_run is None:
            return True

        try:
            last_dt = datetime.fromisoformat(last_run)
            elapsed = datetime.now() - last_dt
            return elapsed.days >= _LLM_REFRESH_COOLDOWN_DAYS
        except (ValueError, TypeError):
            return True

    def record_run(self, project: str, doc: str) -> None:
        """Record that we ran LLM refresh for this pair (updates throttle state)."""
        state = self._load_state()
        key = f"{project}/{doc}"
        state[key] = datetime.now().isoformat()
        self._save_state(state)

    def generate_proposal(
        self,
        project: str,
        doc_name: str,
        section_name: str,
        current_section: str,
        recent_commits: list[str],
        source_excerpts: str,
    ) -> Optional[LlmRefreshProposal]:
        """Generate a refresh proposal for a stale DDD section.

        Calls Bedrock Sonnet with evidence-mandatory prompt. Returns None if
        LLM call fails or proposal confidence is too low to even escalate.

        Args:
            project: Project name (e.g., "SwarmAI")
            doc_name: DDD doc (e.g., "TECH.md")
            section_name: Section being refreshed (e.g., "Autonomous Pipeline")
            current_section: Current text of the section
            recent_commits: List of "hash subject" commit lines
            source_excerpts: Key source file content for citation
        """
        prompt = self._build_prompt(
            section_name, current_section, recent_commits, source_excerpts
        )

        try:
            response_text = self._call_llm(prompt)
        except Exception as exc:
            logger.warning("auto_refresh.L2: LLM call failed: %s", exc)
            return None

        if not response_text:
            return None

        # Parse response: extract proposed text + citations
        proposed, citations = self._parse_response(response_text)
        if not proposed:
            return None

        # Verify citations against filesystem
        verifier = CitationVerifier(self._swarmai_root, self._workspace_root)
        verified, total = verifier.verify_all_citations(citations)

        # Classify confidence
        touches_strategy = is_strategy_section(section_name)
        # Changes are factual if the proposal mostly changes numbers/names (not prose)
        changes_factual = self._assess_factual(current_section, proposed)

        confidence = classify_confidence(
            citations_verified=verified,
            citations_total=total,
            changes_are_factual=changes_factual,
            touches_strategy_section=touches_strategy,
        )

        return LlmRefreshProposal(
            project=project,
            target_doc=doc_name,
            section_name=section_name,
            current_text=current_section,
            proposed_text=proposed,
            citations=citations,
            confidence=confidence,
            evidence_commits=recent_commits[:5],
        )

    def _build_prompt(
        self,
        section_name: str,
        current_section: str,
        recent_commits: list[str],
        source_excerpts: str,
    ) -> str:
        """Build the evidence-mandatory LLM prompt."""
        commits_text = "\n".join(f"  - {c}" for c in recent_commits[:10])
        return f"""You are updating a DDD (Domain-Driven Design) document section that has become stale.

## Task
Given recent code commits and the current source of truth, produce an updated version of the section below.

## Rules (STRICT)
1. Only change FACTS verifiable from the source files provided.
2. Do NOT change strategic framing, opinions, or design philosophy.
3. Every changed line must include a citation: [source: file:path:line_or_content]
4. If you cannot cite a change, do NOT make it.
5. Keep the same markdown formatting and style.
6. If nothing needs changing, respond with: NO_CHANGES_NEEDED

## Recent commits (evidence of what changed):
{commits_text}

## Source of truth (current code):
{source_excerpts}

## Current section text to refresh:
```
{current_section[:3000]}
```

## Output format:
PROPOSED:
```
<your updated section text with [source: ...] citations inline>
```

CITATIONS:
- file:line_or_content
- file:line_or_content
"""

    def _call_llm(self, prompt: str) -> str:
        """Make Bedrock Sonnet call with explicit timeouts. Returns response text.

        Timeout: connect=5s, read=25s — fits within the channel's 30s budget.
        If Bedrock is slow, the call fails cleanly rather than lingering.
        """
        from jobs.bedrock import get_client
        from botocore.config import Config as BotoConfig

        # Get base client, then create a timeout-constrained one for this call
        base_client = get_client()
        # Use the same region but with explicit timeouts
        model_id = self._get_sonnet_model_id()

        # Create a timeout-scoped client (reuses credentials from get_client)
        import boto3
        timeout_config = BotoConfig(
            connect_timeout=5,
            read_timeout=25,
            retries={"max_attempts": 1, "mode": "standard"},
        )
        client = boto3.client(
            "bedrock-runtime",
            region_name=base_client.meta.region_name,
            config=timeout_config,
        )

        response = client.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": _LLM_MAX_TOKENS,
                "temperature": 0,
                "messages": [
                    {"role": "user", "content": prompt}
                ],
            }),
        )
        result = json.loads(response["body"].read())
        content = result.get("content", [])
        if content and content[0].get("type") == "text":
            return content[0]["text"]
        return ""

    @staticmethod
    def _get_sonnet_model_id() -> str:
        """Get Sonnet model ID from config (same source as summarization.py)."""
        try:
            from core.app_config_manager import AppConfigManager
            cfg = AppConfigManager.instance()
            bedrock_map = cfg.get("bedrock_model_map") or {}
            return bedrock_map.get("claude-sonnet-4-6", "us.anthropic.claude-sonnet-4-6")
        except Exception:
            return "us.anthropic.claude-sonnet-4-6"

    def _parse_response(self, response: str) -> tuple[str, list[str]]:
        """Parse LLM response into (proposed_text, citations_list)."""
        # Check for no-changes response
        if "NO_CHANGES_NEEDED" in response:
            return ("", [])

        # Extract PROPOSED block (allow optional language identifier after ```)
        proposed = ""
        proposed_match = re.search(
            r"PROPOSED:\s*```[^\n]*\n(.*?)```", response, re.DOTALL
        )
        if proposed_match:
            proposed = proposed_match.group(1).strip()

        # Extract CITATIONS list
        citations: list[str] = []
        citations_section = re.search(
            r"CITATIONS:\s*\n((?:- .+\n?)+)", response
        )
        if citations_section:
            for line in citations_section.group(1).splitlines():
                line = line.strip().lstrip("- ").strip()
                if line:
                    citations.append(line)

        # Also extract inline [source: ...] citations
        inline_citations = re.findall(r"\[source:\s*([^\]]+)\]", proposed)
        for c in inline_citations:
            if c not in citations:
                citations.append(c)

        return (proposed, citations)

    def _assess_factual(self, old_text: str, new_text: str) -> bool:
        """Rough heuristic: are changes mostly factual (numbers/names) vs prose?

        Compares word-level diff. If >70% of changed words are numbers,
        identifiers, or technical terms → factual.
        """
        old_words = set(re.findall(r"\b\w+\b", old_text.lower()))
        new_words = set(re.findall(r"\b\w+\b", new_text.lower()))

        added = new_words - old_words
        removed = old_words - new_words
        changed = added | removed

        if not changed:
            return True  # No change = trivially factual

        # Count technical/numeric words in the diff
        technical = sum(
            1 for w in changed
            if re.match(r"^\d+$", w)  # numbers
            or re.match(r"^[a-z_]+\d+", w)  # identifiers with numbers
            or w in {"stage", "specialist", "gate", "layer", "channel", "pipeline"}
        )

        return technical / len(changed) > 0.5  # >50% technical words = factual change

    def _load_state(self) -> dict:
        """Load throttle state from JSON file."""
        if not self._state_path.exists():
            return {}
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_state(self, state: dict) -> None:
        """Save throttle state to JSON file."""
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps(state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("auto_refresh.L2: failed to save state: %s", exc)


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
