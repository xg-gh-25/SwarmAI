"""Centralized context directory loader.

Reads markdown files from ``~/.swarm-ai/.context/`` and assembles them into
the system prompt with priority-based ordering, token budget enforcement,
and L0/L1 compaction support.

This module replaces the scattered context injection system with a single
centralized directory.  It is responsible for:

- ``ContextFileSpec``           — Namedtuple defining a source file's metadata
                                  (filename, priority, section_name, truncatable)
- ``CONTEXT_FILES``             — Ordered list of all 9 ContextFileSpec entries
- ``DEFAULT_TOKEN_BUDGET``      — Default token budget constant (25,000)
- ``L1_CACHE_FILENAME``         — Filename for the full L1 cache
- ``L0_CACHE_FILENAME``         — Filename for the compact L0 cache
- ``THRESHOLD_USE_L1``          — Context window threshold for L1 usage (64K)
- ``THRESHOLD_SKIP_LOW_PRIORITY`` — Context window threshold below which
                                    KNOWLEDGE and PROJECTS are excluded (32K)
- ``ContextDirectoryLoader``    — Main loader class with load_all(),
                                  ensure_directory(), and estimate_tokens()

The existing ``SystemPromptBuilder`` continues to handle non-file sections
(safety principles, datetime, runtime metadata).  This module provides
global identity/personality/memory context from the SwarmWS ``.context/``
directory.
"""

from pathlib import Path
from typing import NamedTuple, Optional
import logging
import subprocess

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

DEFAULT_TOKEN_BUDGET = 25_000
"""Maximum tokens for assembled context output (default)."""

L1_CACHE_FILENAME = "L1_SYSTEM_PROMPTS.md"
"""Filename for the full concatenation cache (models >= 64K context)."""

L0_CACHE_FILENAME = "L0_SYSTEM_PROMPTS.md"
"""Filename for the compact/compressed cache (models < 64K context)."""

THRESHOLD_USE_L1 = 64_000
"""Model context window >= this value uses L1 cache or source files."""

THRESHOLD_SKIP_LOW_PRIORITY = 32_000
"""Model context window < this value excludes KNOWLEDGE + PROJECTS."""


# ── Data Models ────────────────────────────────────────────────────────


class ContextFileSpec(NamedTuple):
    """Metadata for a single context source file.

    Attributes:
        filename:     Filename in the context directory (e.g. ``"SWARMAI.md"``).
        priority:     Assembly order and truncation priority
                      (0 = highest priority, 8 = lowest).
        section_name: Header used in assembled output (e.g. ``"SwarmAI"``).
        truncatable:  Whether this file can be truncated during budget
                      enforcement.  Priorities 0–2 are non-truncatable.
    """

    filename: str
    priority: int
    section_name: str
    truncatable: bool


CONTEXT_FILES: list[ContextFileSpec] = [
    ContextFileSpec("SWARMAI.md",    0, "SwarmAI",          False),
    ContextFileSpec("IDENTITY.md",   1, "Identity",         False),
    ContextFileSpec("SOUL.md",       2, "Soul",             False),
    ContextFileSpec("AGENT.md",      3, "Agent Directives", True),
    ContextFileSpec("USER.md",       4, "User",             True),
    ContextFileSpec("STEERING.md",   5, "Steering",         True),
    ContextFileSpec("MEMORY.md",     6, "Memory",           True),
    ContextFileSpec("KNOWLEDGE.md",  7, "Knowledge",        True),
    ContextFileSpec("PROJECTS.md",   8, "Projects",         True),
]
"""All 9 context source files in ascending priority order."""


class ContextDirectoryLoader:
    """Loads and assembles context files from ``~/.swarm-ai/.context/``.

    Guarantees:

    - **Deterministic**: same files + same budget → identical output
    - **Priority-ordered**: files assembled in ascending priority (0 first)
    - **Non-truncatable**: priorities 0–2 are never removed or shortened
    - **Cache-aware**: L1 cache used when fresh; regenerated on change
    - **L0 support**: compact cache for models with < 64K context window
    - **Resilient**: all filesystem errors caught and logged; never blocks
      agent startup
    """

    def __init__(
        self,
        context_dir: Path,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        templates_dir: Optional[Path] = None,
    ) -> None:
        """Initialize the loader.

        Args:
            context_dir:   Path to ``~/.swarm-ai/.context/``.
            token_budget:  Max tokens for assembled output (default 25,000).
            templates_dir: Path to ``backend/context/`` for initialization.
                           If *None*, template copying is skipped.
        """
        self.context_dir = context_dir
        self.token_budget = token_budget
        self.templates_dir = templates_dir


    # ── Public API ─────────────────────────────────────────────────────

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estimate token count using word-based heuristic.

        Formula::

            tokens = max(1, int(word_count * 4 / 3))

        This approximation (1 token ≈ 0.75 words) provides a fast,
        dependency-free estimate suitable for budget enforcement.

        Args:
            text: Input text to estimate.

        Returns:
            Positive integer token estimate.  Returns ``0`` for empty
            or whitespace-only input.
        """
        if not text or not text.strip():
            return 0
        word_count = len(text.split())
        return max(1, int(word_count * 4 / 3))

    def ensure_directory(self) -> None:
        """Create context directory and copy missing template files.

        Creates ``~/.swarm-ai/.context/`` if it does not exist, then copies
        default templates from ``templates_dir`` (``backend/context/``) for
        any files that are missing.  Existing files are never overwritten.

        The method copies all 9 source file defaults plus the L0 and L1
        cache template files.  Individual copy failures are logged as
        warnings and do not prevent the remaining files from being copied.

        If ``templates_dir`` was not provided at init time, this method
        is a no-op beyond directory creation.

        Validates: Requirements 1.1, 1.2, 1.3, 1.4
        """
        # Create the context directory (and parents) if needed
        try:
            self.context_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error(
                "Cannot create context directory %s: %s",
                self.context_dir,
                exc,
            )
            return

        if self.templates_dir is None:
            return

        # Build the list of files to copy: 9 source files + L0 + L1
        files_to_copy: list[str] = [
            spec.filename for spec in CONTEXT_FILES
        ]
        files_to_copy.append(L0_CACHE_FILENAME)
        files_to_copy.append(L1_CACHE_FILENAME)

        for filename in files_to_copy:
            dest = self.context_dir / filename
            if dest.exists():
                # Preserve existing files — never overwrite
                continue

            src = self.templates_dir / filename
            try:
                if not src.exists():
                    logger.warning(
                        "Template file not found, skipping: %s", src
                    )
                    continue
                dest.write_bytes(src.read_bytes())
            except OSError as exc:
                logger.warning(
                    "Failed to copy template %s → %s: %s",
                    src,
                    dest,
                    exc,
                )

    # ── Private Methods ────────────────────────────────────────────────

    def _assemble_from_sources(
        self,
        model_context_window: int = 200_000,
    ) -> str:
        """Read all source files, enforce token budget, and assemble.

        Reads the 9 source files from the context directory in ascending
        priority order (0 first, 8 last).  Each non-empty file gets a
        ``## {section_name}`` header.  Empty or missing files are skipped
        entirely — no empty section headers appear in the output.

        For models with a context window below ``THRESHOLD_SKIP_LOW_PRIORITY``
        (32K), KNOWLEDGE.md and PROJECTS.md are excluded entirely.

        After reading, the sections are passed through
        ``_enforce_token_budget()`` to ensure the assembled output fits
        within ``self.token_budget``.

        Args:
            model_context_window: The model's context window size in tokens.
                Used to decide whether to exclude low-priority files.

        Returns:
            Assembled context string with section headers separated by
            double newlines.  Returns ``""`` if all files are empty/missing.

        Validates: Requirements 2.1, 2.2, 2.3, 2.4, 6.4
        """
        skip_filenames: set[str] = set()
        if model_context_window < THRESHOLD_SKIP_LOW_PRIORITY:
            skip_filenames = {"KNOWLEDGE.md", "PROJECTS.md"}

        # Build section tuples: (priority, section_name, content, truncatable)
        section_tuples: list[tuple[int, str, str, bool]] = []
        for spec in CONTEXT_FILES:
            if spec.filename in skip_filenames:
                continue

            filepath = self.context_dir / spec.filename
            try:
                if not filepath.exists():
                    continue
                content = filepath.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                logger.warning(
                    "Skipping file with invalid UTF-8 encoding: %s",
                    filepath,
                )
                continue
            except OSError as exc:
                logger.warning(
                    "Cannot read source file %s: %s", filepath, exc
                )
                continue

            content = content.strip()
            if not content:
                continue

            section_tuples.append(
                (spec.priority, spec.section_name, content, spec.truncatable)
            )

        if not section_tuples:
            return ""

        # Enforce token budget (truncates from lowest priority upward)
        section_tuples = self._enforce_token_budget(section_tuples)

        # Join into final assembled string
        parts: list[str] = []
        for _, section_name, content, _ in section_tuples:
            parts.append(f"## {section_name}\n{content}")

        return "\n\n".join(parts)

    def _enforce_token_budget(
        self,
        sections: list[tuple[int, str, str, bool]],
    ) -> list[tuple[int, str, str, bool]]:
        """Truncate sections from lowest priority to fit token budget.

        Processes a list of ``(priority, section_name, content, truncatable)``
        tuples.  When the total token count (including ``## {section_name}``
        headers and ``\\n\\n`` separators) exceeds ``self.token_budget``,
        truncatable sections are progressively shortened starting from the
        lowest priority (highest number) upward.

        Non-truncatable sections (priorities 0–2: SWARMAI, IDENTITY, SOUL)
        are never modified.  If non-truncatable sections alone exceed the
        budget, the output will exceed the budget — this is by design
        (Requirement 3.5).

        A truncation indicator ``[Truncated: X → Y tokens]`` is appended
        to each section that was shortened.

        Args:
            sections: List of ``(priority, section_name, content, truncatable)``
                tuples in ascending priority order.

        Returns:
            The same list structure with truncated content where needed.

        Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 10.2
        """
        if not sections:
            return sections

        def _section_tokens(section_name: str, content: str) -> int:
            """Estimate tokens for a full section including header."""
            full_text = f"## {section_name}\n{content}"
            return self.estimate_tokens(full_text)

        # Separator is "\n\n" between sections — estimate its token cost
        separator_tokens = self.estimate_tokens("\n\n") if len(sections) > 1 else 0

        # Calculate total tokens
        total = sum(
            _section_tokens(name, content)
            for _, name, content, _ in sections
        )
        # Add separator tokens between sections
        total += separator_tokens * max(0, len(sections) - 1)

        if total <= self.token_budget:
            return sections

        # Sort truncatable sections by priority descending (lowest priority first)
        # so we truncate from priority 8 upward
        truncatable_indices = [
            i for i, (_, _, _, trunc) in enumerate(sections) if trunc
        ]
        truncatable_indices.sort(
            key=lambda i: sections[i][0], reverse=True
        )

        result = list(sections)

        for idx in truncatable_indices:
            if total <= self.token_budget:
                break

            priority, section_name, content, truncatable = result[idx]
            original_tokens = _section_tokens(section_name, content)

            # How much do we need to save?
            overshoot = total - self.token_budget

            if overshoot >= original_tokens:
                # Remove the entire section content, replace with indicator
                indicator = f"\n\n[Truncated: {original_tokens:,} → 0 tokens]"
                new_content = indicator.strip()
                new_tokens = _section_tokens(section_name, new_content)
                total -= (original_tokens - new_tokens)
                result[idx] = (priority, section_name, new_content, truncatable)
            else:
                # Partially truncate: keep enough words to fit budget
                # Target tokens for this section = original - overshoot
                target_section_tokens = original_tokens - overshoot
                # The header "## {section_name}\n" is fixed overhead
                header_text = f"## {section_name}\n"
                header_tokens = self.estimate_tokens(header_text)

                # Target tokens for content alone (minus header)
                target_content_tokens = max(0, target_section_tokens - header_tokens)

                # Truncate content by words to approximate target
                words = content.split()
                # Estimate words to keep: target_content_tokens * 3/4
                # (inverse of the 4/3 estimation formula)
                words_to_keep = max(0, int(target_content_tokens * 3 / 4))
                truncated_content = " ".join(words[:words_to_keep])

                indicator = (
                    f"\n\n[Truncated: {original_tokens:,} → "
                    f"{_section_tokens(section_name, truncated_content):,} tokens]"
                )
                new_content = truncated_content + indicator
                new_tokens = _section_tokens(section_name, new_content)
                total -= (original_tokens - new_tokens)
                result[idx] = (priority, section_name, new_content, truncatable)

        return result

    # ── L1 Cache ───────────────────────────────────────────────────────

    def _is_l1_fresh(self) -> bool:
        """Check if L1 cache is fresh. Git-first with mtime fallback.

        Primary: ``git status --porcelain`` (atomic, catches tracked + untracked).
        Fallback: mtime comparison (only when git unavailable).
        """
        l1_path = self.context_dir / L1_CACHE_FILENAME
        if not l1_path.exists():
            return False

        # Try git first (preferred — atomic, no TOCTOU)
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain", "--", str(self.context_dir)],
                cwd=self.context_dir.parent,
                capture_output=True, text=True, timeout=5,
            )
            return not result.stdout.strip()
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass

        # Mtime fallback (git unavailable)
        try:
            l1_mtime = l1_path.stat().st_mtime
            for spec in CONTEXT_FILES:
                src = self.context_dir / spec.filename
                if src.exists() and src.stat().st_mtime > l1_mtime:
                    return False
            return True
        except OSError:
            return False

    def _write_l1_cache(self, content: str) -> None:
        """Write assembled content to L1 cache file.

        Logs a warning and continues if the write fails.

        Validates: Requirements 4.1, 4.5
        """
        l1_path = self.context_dir / L1_CACHE_FILENAME
        try:
            l1_path.write_text(content, encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to write L1 cache %s: %s", l1_path, exc)

    def _load_l1_if_fresh(self) -> Optional[str]:
        """Load L1 cache if fresh. Git-based check is atomic, no TOCTOU."""
        if not self._is_l1_fresh():
            return None
        l1_path = self.context_dir / L1_CACHE_FILENAME
        try:
            return l1_path.read_text(encoding="utf-8")
        except OSError:
            return None

    # ── L0 Cache ───────────────────────────────────────────────────────

    def _load_l0(self, model_context_window: int) -> str:
        """Load L0 compact cache or fall back to aggressive truncation.

        Reads ``L0_SYSTEM_PROMPTS.md`` if it exists.  If missing, falls
        back to ``_assemble_from_sources()`` which will apply token budget
        truncation and (for models < 32K) exclude KNOWLEDGE + PROJECTS.

        Validates: Requirements 5.1, 5.2, 5.3, 6.3, 6.4
        """
        l0_path = self.context_dir / L0_CACHE_FILENAME
        try:
            if l0_path.is_file():
                content = l0_path.read_text(encoding="utf-8").strip()
                if content:
                    return content
        except OSError as exc:
            logger.warning("Failed to read L0 cache %s: %s", l0_path, exc)

        # Fall back to assembly with the model's context window
        # (will exclude low-priority files for small models)
        return self._assemble_from_sources(
            model_context_window=model_context_window,
        )

    # ── Main Entry Point ───────────────────────────────────────────────

    def load_all(self, model_context_window: int = 200_000) -> str:
        """Load and assemble context based on model context window.

        Main entry point.  Selects the loading strategy based on the
        model's context window size:

        - >= 64K: use L1 cache (if fresh) or assemble from sources
        - < 64K: use L0 compact cache

        The entire method is wrapped in try/except so context loading
        failures never block agent startup.

        Args:
            model_context_window: Model's context window size in tokens.

        Returns:
            Assembled context string.  Returns ``""`` on any failure.

        Validates: Requirements 2.1, 3.1, 4.1-4.4, 5.1, 6.1-6.4, 11.1
        """
        try:
            if model_context_window < THRESHOLD_USE_L1:
                # Small model: use L0 compact cache
                return self._load_l0(model_context_window)

            # Large model: try L1 cache first
            cached = self._load_l1_if_fresh()
            if cached:
                return cached

            # L1 stale or missing: assemble from sources
            assembled = self._assemble_from_sources(
                model_context_window=model_context_window,
            )

            # Write L1 cache for next time
            if assembled:
                self._write_l1_cache(assembled)

            return assembled
        except Exception as exc:
            logger.error("ContextDirectoryLoader.load_all failed: %s", exc)
            return ""
