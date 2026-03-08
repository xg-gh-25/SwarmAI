"""Centralized context directory loader.

Reads markdown files from ``~/.swarm-ai/.context/`` and assembles them into
the system prompt with priority-based ordering, token budget enforcement,
and L0/L1 compaction support.

This module replaces the scattered context injection system with a single
centralized directory.  It is responsible for:

- ``ContextFileSpec``           — Frozen dataclass defining a source file's
                                  metadata (filename, priority, section_name,
                                  truncatable, user_customized, truncate_from)
- ``CONTEXT_FILES``             — Ordered list of all 10 ContextFileSpec entries
- ``DEFAULT_TOKEN_BUDGET``      — Default token budget constant (25,000)
- ``BUDGET_LARGE_MODEL``        — Token budget for >= 200K models (40,000)
- ``L1_CACHE_FILENAME``         — Filename for the full L1 cache
- ``L0_CACHE_FILENAME``         — Filename for the compact L0 cache
- ``THRESHOLD_USE_L1``          — Context window threshold for L1 usage (64K)
- ``THRESHOLD_SKIP_LOW_PRIORITY`` — Context window threshold below which
                                    KNOWLEDGE and PROJECTS are excluded (32K)
- ``ContextDirectoryLoader``    — Main loader class with load_all(),
                                  ensure_directory(), and estimate_tokens()

The ``user_customized`` field drives two-mode copy behavior in
``ensure_directory()`` (always-overwrite vs copy-only-if-missing) and
readonly enforcement (0o444 vs 0o644).  The ``truncate_from`` field
controls truncation direction in ``_enforce_token_budget()`` — ``"tail"``
keeps the beginning (default), ``"head"`` keeps the end (newest content).

The existing ``SystemPromptBuilder`` continues to handle non-file sections
(safety principles, datetime, runtime metadata).  This module provides
global identity/personality/memory context from the SwarmWS ``.context/``
directory.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional
import logging
import os
import re
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

BUDGET_LARGE_MODEL = 40_000
"""Token budget for models with >= 200K context window (20% of 200K)."""

GROUP_CHANNEL_EXCLUDE: frozenset[str] = frozenset({"MEMORY.md", "USER.md"})
"""Files excluded from group channel prompts to prevent personal data leakage."""


# ── Data Models ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ContextFileSpec:
    """Metadata for a single context source file.

    Attributes:
        filename:        Filename in the context directory (e.g. ``"SWARMAI.md"``).
        priority:        Assembly order and truncation priority
                         (0 = highest priority, 9 = lowest).
        section_name:    Header used in assembled output (e.g. ``"SwarmAI"``).
        truncatable:     Whether this file can be truncated during budget
                         enforcement.  Priorities 0–2 are non-truncatable.
        user_customized: ``True`` for user-owned files (copy-only-if-missing,
                         ``0o644``); ``False`` for system defaults
                         (always-overwrite, ``0o444``).
        truncate_from:   Truncation direction — ``"tail"`` keeps the beginning
                         (default), ``"head"`` keeps the end (newest content).
    """

    filename: str
    priority: int
    section_name: str
    truncatable: bool
    user_customized: bool = False
    truncate_from: Literal["head", "tail"] = "tail"


CONTEXT_FILES: list[ContextFileSpec] = [
    ContextFileSpec("SWARMAI.md",           0,  "SwarmAI",            False, False, "tail"),
    ContextFileSpec("IDENTITY.md",          1,  "Identity",           False, False, "tail"),
    ContextFileSpec("SOUL.md",              2,  "Soul",               False, False, "tail"),
    ContextFileSpec("AGENT.md",             3,  "Agent Directives",   True,  False, "tail"),
    ContextFileSpec("USER.md",              4,  "User",               True,  True,  "tail"),
    ContextFileSpec("STEERING.md",          5,  "Steering",           True,  True,  "tail"),
    ContextFileSpec("TOOLS.md",             6,  "Tools",              True,  True,  "tail"),
    ContextFileSpec("MEMORY.md",            7,  "Memory",             True,  True,  "head"),
    ContextFileSpec("KNOWLEDGE.md",         8,  "Knowledge",          True,  True,  "tail"),
    ContextFileSpec("PROJECTS.md",          9,  "Projects",           True,  True,  "tail"),
    # GROWTH_PRINCIPLES.md removed — content folded into SOUL.md and skills.
    # EVOLUTION.md removed — agent reads it on-demand via Read tool per AGENT.md
    #   "Every Session" directive, not loaded into system prompt.
]
"""All 10 context source files in ascending priority order (P0-P9)."""


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


    # ── Class-level compiled regexes ─────────────────────────────────

    # HTML comments (<!-- ... -->) — stripped during assembly to save tokens.
    # Uses re.DOTALL so multi-line comments are matched.
    _HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

    # Regex matching CJK Unified Ideographs, CJK Extension A, Hangul,
    # Hiragana, Katakana, and fullwidth forms — characters that are NOT
    # space-separated and need per-character token estimation.
    _CJK_RE = re.compile(
        r"[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\u3400-\u4dbf"
        r"\u4e00-\u9fff\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef"
        r"\U00020000-\U0002a6df\U0002a700-\U0002b73f]"
    )

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estimate token count with CJK awareness.

        Uses a two-pass heuristic:

        1. Count CJK characters (Chinese/Japanese/Korean ideographs) and
           estimate at **1.5 characters per token** (~0.67 tokens/char).
        2. Remove CJK characters, then count remaining space-separated
           words at **4/3 tokens per word** (~0.75 words/token).
        3. Sum both estimates.

        This avoids the major underestimation that occurs with pure
        word-split on CJK text (a Chinese paragraph may be 1 "word"
        but 100+ tokens).

        Args:
            text: Input text to estimate.

        Returns:
            Positive integer token estimate.  Returns ``0`` for empty
            or whitespace-only input.
        """
        if not text or not text.strip():
            return 0

        # Count CJK characters
        cjk_chars = ContextDirectoryLoader._CJK_RE.findall(text)
        cjk_count = len(cjk_chars)

        if cjk_count == 0:
            # Fast path: pure Latin/ASCII text — original heuristic
            word_count = len(text.split())
            return max(1, int(word_count * 4 / 3))

        # CJK tokens: ~1.5 chars per token (empirical average for
        # Chinese/Japanese with cl100k_base / Claude tokenizers)
        cjk_tokens = int(cjk_count / 1.5)

        # Remove CJK chars, estimate remaining words
        latin_text = ContextDirectoryLoader._CJK_RE.sub("", text)
        latin_words = len(latin_text.split())
        latin_tokens = int(latin_words * 4 / 3)

        return max(1, cjk_tokens + latin_tokens)

    def ensure_directory(self) -> None:
        """Create context directory and refresh context files with two-mode copy.

        Creates ``~/.swarm-ai/.context/`` if it does not exist, then iterates
        ``CONTEXT_FILES`` entries and copies templates using two-mode logic:

        - **System defaults** (``user_customized=False``): always overwrite from
          template, then set ``chmod 0o444`` (readonly).  A byte-comparison
          skips the write when content is already identical.  Readonly permission
          is removed before overwriting and re-applied after.
        - **User-customized** (``user_customized=True``): copy only if the
          destination file does not exist, then set ``chmod 0o644`` (read-write).
          Existing user files are never overwritten.

        All ``chmod`` calls are wrapped in ``try/except OSError`` for Windows
        compatibility (best-effort).

        Only ``CONTEXT_FILES`` entries are iterated — non-CONTEXT_FILES templates
        (like BOOTSTRAP.md) are handled by ``_maybe_create_bootstrap()``.

        Individual copy failures are logged as warnings and do not prevent
        the remaining files from being copied.

        If ``templates_dir`` was not provided at init time, this method
        is a no-op beyond directory creation.

        Validates: Requirements 9.1, 9.6, 9.7, 10.3, 10.4, 10.5, 10.7, 14.3, 14.4
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

        refreshed: list[str] = []
        created: list[str] = []
        for spec in CONTEXT_FILES:
            src = self.templates_dir / spec.filename
            if not src.is_file():
                continue
            dest = self.context_dir / spec.filename

            try:
                if spec.user_customized:
                    # Copy-only-if-missing: never overwrite user edits
                    if dest.exists():
                        continue
                    dest.write_bytes(src.read_bytes())
                    try:
                        os.chmod(dest, 0o644)
                    except OSError:
                        pass  # Best-effort on non-Unix (Windows)
                    created.append(spec.filename)
                else:
                    # Always-overwrite: system defaults refreshed every startup
                    # Single read of source, compare against dest to skip no-ops
                    src_bytes = src.read_bytes()
                    needs_write = True
                    if dest.exists():
                        try:
                            if dest.read_bytes() == src_bytes:
                                needs_write = False
                        except OSError:
                            pass  # Can't read dest — overwrite it
                    if needs_write:
                        # Remove readonly before overwriting
                        if dest.exists():
                            try:
                                os.chmod(dest, 0o644)
                            except OSError:
                                pass
                        dest.write_bytes(src_bytes)
                        refreshed.append(spec.filename)
                    # Always ensure readonly permission (whether written or not)
                    try:
                        os.chmod(dest, 0o444)
                    except OSError:
                        pass  # Best-effort on non-Unix
            except OSError as exc:
                logger.warning("Failed to copy %s → %s: %s", src, dest, exc)

        # Startup health report
        if refreshed or created:
            logger.info(
                "Context sync: refreshed=%s, created=%s",
                refreshed or "none",
                created or "none",
            )
        else:
            logger.debug("Context sync: all %d files current", len(CONTEXT_FILES))

        # BOOTSTRAP.md detection: create if USER.md is empty template
        self._maybe_create_bootstrap()

    def compute_token_budget(self, model_context_window: int | None) -> int:
        """Compute dynamic token budget based on model context window size.

        Scales the token budget to the model's capacity:

        - >= 200K tokens → 40,000 (``BUDGET_LARGE_MODEL``)
        - >= 64K and < 200K → 25,000 (``DEFAULT_TOKEN_BUDGET``)
        - < 64K → ``self.token_budget`` (instance default, L0 path)

        When *model_context_window* is ``None`` or ``0``, falls back to
        ``DEFAULT_TOKEN_BUDGET`` (25,000).

        This is a public method — also used by ``_build_system_prompt()``
        for metadata reporting.

        Args:
            model_context_window: The model's context window size in tokens.
                ``None`` or ``0`` triggers the default fallback.

        Returns:
            Computed token budget as a positive integer.

        Validates: Requirements 11.1, 11.2, 11.3, 11.4, 11.5, 14.6
        """
        if not model_context_window:
            return DEFAULT_TOKEN_BUDGET
        if model_context_window >= 200_000:
            return BUDGET_LARGE_MODEL
        elif model_context_window >= THRESHOLD_USE_L1:
            return DEFAULT_TOKEN_BUDGET
        else:
            return self.token_budget

    def _maybe_create_bootstrap(self) -> None:
        """Create BOOTSTRAP.md if USER.md is an empty template.

        Checks if USER.md contains only the unfilled default template
        placeholders.  If so, copies BOOTSTRAP.md from ``templates_dir``
        to ``context_dir`` for first-run onboarding.

        BOOTSTRAP.md is NOT in ``CONTEXT_FILES`` — it is detected separately
        by ``_build_system_prompt()`` at session start.

        Guard clauses:
        - If ``templates_dir`` is ``None``, return immediately.
        - If BOOTSTRAP.md already exists, skip (don't recreate).
        - If USER.md doesn't exist, skip.
        - If USER.md has user-provided content, skip.

        All file I/O is wrapped in try/except for resilience — a failure
        here should never prevent the agent from starting.

        Validates: Requirements 4.1, 4.4, 4.6, 14.5
        """
        if self.templates_dir is None:
            return

        try:
            bootstrap_md = self.context_dir / "BOOTSTRAP.md"
            if bootstrap_md.exists():
                return  # Already exists, don't recreate

            user_md = self.context_dir / "USER.md"
            if not user_md.exists():
                return

            content = user_md.read_text(encoding="utf-8").strip()
            if not self._is_empty_template(content):
                return  # User has filled in content

            bootstrap_src = self.templates_dir / "BOOTSTRAP.md"
            if bootstrap_src.is_file():
                bootstrap_md.write_bytes(bootstrap_src.read_bytes())
        except OSError as exc:
            logger.warning("BOOTSTRAP.md creation failed: %s", exc)

    def _is_empty_template(self, content: str) -> bool:
        """Check if USER.md is still the unfilled default template.

        Uses structural detection: looks for empty placeholder fields
        rather than exact hash comparison (fragile to whitespace changes).

        Scans for key user-fillable fields (``**Name:**``, ``**Timezone:**``,
        ``**Role:**``) and checks whether any have been filled in with
        real content.  If all fields are still empty (or contain only
        template marker text like ``_`` or ``_(``), returns ``True``.

        Args:
            content: The full text content of USER.md.

        Returns:
            ``True`` if the content appears to be an unfilled template,
            ``False`` if the user has provided real content.
        """
        indicators = ["**Name:**", "**Timezone:**", "**Role:**"]
        for indicator in indicators:
            idx = content.find(indicator)
            if idx == -1:
                continue
            # Check if there's content after the field on the same line
            line_end = content.find("\n", idx)
            if line_end != -1:
                field_value = content[idx + len(indicator):line_end].strip()
            else:
                field_value = content[idx + len(indicator):].strip()
            if field_value and field_value not in ("", "_", "_("):
                return False  # User has filled in at least one field
        return True  # All fields still empty

    # ── Private Methods ────────────────────────────────────────────────

    @classmethod
    def _clean_content(cls, raw: str, section_name: str) -> str:
        """Strip boilerplate from file content before assembly.

        Removes:

        1. **HTML comments** (``<!-- ... -->``) — template markers like
           ``<!-- ⚙️ SYSTEM DEFAULT -->`` are useful for human editors
           but waste ~200 tokens in the LLM system prompt.
        2. **Redundant first H1** — each file already gets a
           ``## {section_name}`` wrapper during assembly, so a leading
           ``# Title`` that repeats the section name is redundant.
           Only stripped when the H1 text is a close match to
           ``section_name`` (case-insensitive prefix match after
           stripping markdown formatting).

        Args:
            raw: Raw file content (before stripping).
            section_name: The section header name for this file.

        Returns:
            Cleaned, stripped content.  May be empty string.
        """
        # 1. Strip HTML comments
        content = cls._HTML_COMMENT_RE.sub("", raw).strip()
        if not content:
            return ""

        # 2. Strip redundant leading H1 if it matches section_name
        #    e.g. "# SwarmAI — Your AI Command Center" matches section "SwarmAI"
        lines = content.split("\n", 1)
        first_line = lines[0].strip()
        if first_line.startswith("# ") and not first_line.startswith("## "):
            h1_text = first_line[2:].strip()
            # Normalize: take text before em-dash, en-dash, double-dash, or colon
            h1_prefix = h1_text.split("—")[0].split("–")[0].split(" -- ")[0].split(":")[0].strip()
            if h1_prefix.lower() == section_name.lower():
                # Remove the H1, keep the rest
                content = lines[1].strip() if len(lines) > 1 else ""

        return content

    def _assemble_from_sources(
        self,
        model_context_window: int = 200_000,
        token_budget: int | None = None,
        exclude_filenames: set[str] | None = None,
    ) -> str:
        """Read all source files, enforce token budget, and assemble.

        Reads the 10 source files from the context directory in ascending
        priority order (0 first, 9 last).  Each non-empty file gets a
        ``## {section_name}`` header.  Empty or missing files are skipped
        entirely — no empty section headers appear in the output.

        For models with a context window below ``THRESHOLD_SKIP_LOW_PRIORITY``
        (32K), KNOWLEDGE.md and PROJECTS.md are excluded entirely.

        Files listed in ``exclude_filenames`` are also skipped (used by
        group channels to suppress MEMORY.md and USER.md).

        After reading, the sections are passed through
        ``_enforce_token_budget()`` to ensure the assembled output fits
        within the computed budget.

        Args:
            model_context_window: The model's context window size in tokens.
                Used to decide whether to exclude low-priority files.
            token_budget: Dynamic token budget computed by
                ``compute_token_budget()``.  Falls back to
                ``self.token_budget`` when ``None``.
            exclude_filenames: Set of filenames to skip entirely (e.g.
                ``{"MEMORY.md", "USER.md"}`` for group channels).

        Returns:
            Assembled context string with section headers separated by
            double newlines.  Returns ``""`` if all files are empty/missing.

        Validates: Requirements 2.1, 2.2, 2.3, 2.4, 6.4, 11.6, 11.7
        """
        effective_budget = token_budget if token_budget is not None else self.token_budget
        skip_filenames: set[str] = set(exclude_filenames or ())
        if model_context_window < THRESHOLD_SKIP_LOW_PRIORITY:
            skip_filenames |= {"KNOWLEDGE.md", "PROJECTS.md"}

        # Build section tuples: (priority, section_name, content, truncatable, truncate_from)
        section_tuples: list[tuple[int, str, str, bool, str]] = []
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

            content = self._clean_content(content, spec.section_name)
            if not content:
                continue

            section_tuples.append(
                (spec.priority, spec.section_name, content, spec.truncatable, spec.truncate_from)
            )

        if not section_tuples:
            return ""

        # Enforce token budget (truncates from lowest priority upward)
        section_tuples = self._enforce_token_budget(section_tuples, effective_budget)

        # Join into final assembled string
        parts: list[str] = []
        for _, section_name, content, _, _ in section_tuples:
            parts.append(f"## {section_name}\n{content}")

        return "\n\n".join(parts)

    def _enforce_token_budget(
        self,
        sections: list[tuple[int, str, str, bool, str]],
        budget: int | None = None,
    ) -> list[tuple[int, str, str, bool, str]]:
        """Truncate sections from lowest priority to fit token budget.

        Processes a list of
        ``(priority, section_name, content, truncatable, truncate_from)``
        tuples.  When the total token count exceeds the budget, truncatable
        sections are progressively shortened starting from the lowest
        priority (highest number) upward.

        Delegates to ``_truncate_section_tail()`` or
        ``_truncate_section_head()`` based on the ``truncate_from`` field.

        Args:
            sections: List of section tuples in ascending priority order.
            budget: Token budget to enforce.  Falls back to
                ``self.token_budget`` when ``None``.

        Returns:
            The same list structure with truncated content where needed.

        Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 10.2, 16.1, 16.3, 16.4, 16.5
        """
        if not sections:
            return sections

        effective_budget = budget if budget is not None else self.token_budget

        def _section_tokens(section_name: str, content: str) -> int:
            """Estimate tokens for a full section including header."""
            full_text = f"## {section_name}\n{content}"
            return self.estimate_tokens(full_text)

        # Separator is "\n\n" between sections — estimate its token cost
        separator_tokens = self.estimate_tokens("\n\n") if len(sections) > 1 else 0

        # Calculate total tokens
        total = sum(
            _section_tokens(name, content)
            for _, name, content, _, _ in sections
        )
        total += separator_tokens * max(0, len(sections) - 1)

        if total <= effective_budget:
            return sections

        # Sort truncatable sections by priority descending (lowest priority first)
        truncatable_indices = [
            i for i, (_, _, _, trunc, _) in enumerate(sections) if trunc
        ]
        truncatable_indices.sort(
            key=lambda i: sections[i][0], reverse=True
        )

        result = list(sections)

        for idx in truncatable_indices:
            if total <= effective_budget:
                break

            priority, section_name, content, truncatable, truncate_from = result[idx]
            original_tokens = _section_tokens(section_name, content)
            overshoot = total - effective_budget

            if overshoot >= original_tokens:
                # Remove the entire section content
                indicator = f"\n\n[Truncated: {original_tokens:,} → 0 tokens]"
                new_content = indicator.strip()
                new_tokens = _section_tokens(section_name, new_content)
                total -= (original_tokens - new_tokens)
                result[idx] = (priority, section_name, new_content, truncatable, truncate_from)
            else:
                # Partially truncate
                new_content = self._truncate_section(
                    content, section_name, original_tokens,
                    overshoot, truncate_from, _section_tokens,
                )
                new_tokens = _section_tokens(section_name, new_content)
                total -= (original_tokens - new_tokens)
                result[idx] = (priority, section_name, new_content, truncatable, truncate_from)

        return result

    def _truncate_section(
        self,
        content: str,
        section_name: str,
        original_tokens: int,
        overshoot: int,
        truncate_from: str,
        section_tokens_fn,
    ) -> str:
        """Partially truncate a section's content to save *overshoot* tokens.

        Args:
            content: Original section content.
            section_name: Section header name (for token estimation).
            original_tokens: Token count of the full section.
            overshoot: How many tokens to save.
            truncate_from: ``"head"`` keeps end, ``"tail"`` keeps beginning.
            section_tokens_fn: Callable(section_name, content) → int.

        Returns:
            Truncated content with ``[Truncated]`` indicator.
        """
        target_section_tokens = original_tokens - overshoot
        header_tokens = self.estimate_tokens(f"## {section_name}\n")
        target_content_tokens = max(0, target_section_tokens - header_tokens)

        words = content.split()
        words_to_keep = max(0, int(target_content_tokens * 3 / 4))

        if truncate_from == "head":
            truncated = " ".join(words[-words_to_keep:]) if words_to_keep else ""
            indicator = (
                f"[Truncated: {original_tokens:,} → "
                f"{section_tokens_fn(section_name, truncated):,} tokens]\n\n"
            )
            return indicator + truncated
        else:
            truncated = " ".join(words[:words_to_keep])
            indicator = (
                f"\n\n[Truncated: {original_tokens:,} → "
                f"{section_tokens_fn(section_name, truncated):,} tokens]"
            )
            return truncated + indicator

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

    def _write_l1_cache(self, content: str, budget: int = DEFAULT_TOKEN_BUDGET) -> None:
        """Write assembled content to L1 cache file with budget header.

        Prepends a ``<!-- budget:NNNNN -->`` header as the first line so
        that ``_load_l1_if_fresh()`` can verify the cache was assembled
        with the same token budget tier as the current session.  This
        prevents serving a 40K-budget cache to a 25K-budget session (or
        vice versa) when the user switches models.

        Logs a warning and continues if the write fails.

        Args:
            content: Assembled context string to cache.
            budget:  Token budget tier used during assembly.

        Validates: Requirements 11.6, 11.7, 14.12
        """
        l1_path = self.context_dir / L1_CACHE_FILENAME
        try:
            header = f"<!-- budget:{budget} -->\n"
            l1_path.write_text(header + content, encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to write L1 cache %s: %s", l1_path, exc)

    # Regex for parsing the budget header from L1 cache files.
    _BUDGET_HEADER_RE = re.compile(r"^<!--\s*budget:(\d+)\s*-->")

    def _load_l1_if_fresh(self, expected_budget: int = DEFAULT_TOKEN_BUDGET) -> Optional[str]:
        """Load L1 cache if fresh and budget-tier matches.

        Checks git-based freshness first, then verifies the cache's
        ``<!-- budget:NNNNN -->`` header matches ``expected_budget``.
        Returns ``None`` (stale) if the budget differs or the header is
        missing (old cache format), forcing reassembly with the correct
        budget tier.

        Args:
            expected_budget: The token budget tier required for this session.

        Returns:
            Cached content (without the budget header line) if fresh and
            budget matches, otherwise ``None``.

        Validates: Requirements 11.6, 11.7, 14.12
        """
        if not self._is_l1_fresh():
            return None
        l1_path = self.context_dir / L1_CACHE_FILENAME
        try:
            raw = l1_path.read_text(encoding="utf-8")
        except OSError:
            return None

        # Parse budget header from the first line
        first_newline = raw.find("\n")
        if first_newline == -1:
            return None  # No newline → malformed cache

        first_line = raw[:first_newline]
        match = self._BUDGET_HEADER_RE.match(first_line)
        if not match:
            return None  # Missing header (old format) → treat as stale

        cached_budget = int(match.group(1))
        if cached_budget != expected_budget:
            return None  # Budget mismatch → stale

        # Return content after the header line
        return raw[first_newline + 1:]

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

    def load_all(
        self,
        model_context_window: int = 200_000,
        exclude_filenames: set[str] | None = None,
    ) -> str:
        """Load and assemble context based on model context window.

        Main entry point.  Selects the loading strategy based on the
        model's context window size:

        - >= 64K: use L1 cache (if fresh) or assemble from sources
        - < 64K: use L0 compact cache

        Computes a dynamic token budget via ``compute_token_budget()``
        before assembly, and passes it to ``_assemble_from_sources()``
        and ``_enforce_token_budget()`` so the budget scales with the
        model's capacity.

        When ``exclude_filenames`` is provided, the L1 cache is bypassed
        (exclusions are session-specific and the cache is shared).

        The entire method is wrapped in try/except so context loading
        failures never block agent startup.

        Args:
            model_context_window: Model's context window size in tokens.
            exclude_filenames: Set of filenames to skip (e.g.
                ``{"MEMORY.md", "USER.md"}`` for group channels).
                When non-empty, L1 cache is bypassed.

        Returns:
            Assembled context string.  Returns ``""`` on any failure.

        Validates: Requirements 2.1, 3.1, 4.1-4.4, 5.1, 6.1-6.4, 11.1, 11.6, 11.7, 14.6
        """
        try:
            # Compute dynamic budget based on model window
            dynamic_budget = self.compute_token_budget(model_context_window)

            if model_context_window < THRESHOLD_USE_L1:
                # Small model: use L0 compact cache
                return self._load_l0(model_context_window)

            # When files are excluded (group channels), skip L1 cache —
            # exclusions are session-specific and the cache is shared.
            if not exclude_filenames:
                cached = self._load_l1_if_fresh(expected_budget=dynamic_budget)
                if cached:
                    return cached

            # Assemble from sources (with exclusions if any)
            assembled = self._assemble_from_sources(
                model_context_window=model_context_window,
                token_budget=dynamic_budget,
                exclude_filenames=exclude_filenames,
            )

            # Only write L1 cache when no exclusions (cache is the full set)
            if assembled and not exclude_filenames:
                self._write_l1_cache(assembled, budget=dynamic_budget)

            return assembled
        except Exception as exc:
            logger.error("ContextDirectoryLoader.load_all failed: %s", exc)
            return ""
