"""Unit and property-based tests for ContextDirectoryLoader.

Tests the BOOTSTRAP.md detection and creation logic, dynamic token budget
computation, truncation direction support, and L1 cache budget-tier
awareness, including:

- ``_is_empty_template()`` — structural detection of unfilled USER.md
- ``_maybe_create_bootstrap()`` — conditional BOOTSTRAP.md creation
- ``compute_token_budget()`` — dynamic budget tiers based on model window
- ``_enforce_token_budget()`` — truncate_from="head" vs "tail" behavior
- ``_write_l1_cache()`` — budget header prepended to L1 cache
- ``_load_l1_if_fresh()`` — budget-tier validation on cache load
- ``load_all()`` — dynamic budget integration

Testing methodology: unit tests for specific scenarios, property-based
tests (Hypothesis) for universal correctness properties.

Key properties verified:
- Property 3: BOOTSTRAP.md created iff USER.md is empty template AND
  BOOTSTRAP.md does not already exist.
- Property 4: Dynamic token budget tiers match model context window
  (1M/500K+, 200K+, 64K+, <64K).
- Property 5: Truncation direction matches truncate_from field.
- Property 11: L1 cache budget-tier consistency — cache returns None
  when budget mismatch, content when budget matches.
"""

from pathlib import Path

import pytest

from core.context_directory_loader import (
    BUDGET_LARGE_MODEL,
    ContextDirectoryLoader,
    DEFAULT_TOKEN_BUDGET,
    _atomic_write_bytes,
)


# ── Atomic write helper (run_6a7e5a2f P3) ──────────────────────────────


class TestAtomicWriteBytes:
    """_atomic_write_bytes: mkstemp(same dir) → fsync → os.replace → chmod perm.

    Provenance: run_6a7e5a2f. ensure_directory writes were bare write_bytes while
    a reader could touch the same .context file on another thread — same torn-read
    class the L1 cache got an atomic write for in run_cc397b0d. This helper gives
    ensure_directory parity, PRESERVING each site's perm (0644 public / 0444 readonly)
    — NOT L1's 0600 (those are public constitution files).
    """

    def test_writes_content(self, tmp_path: Path):
        dest = tmp_path / "f.md"
        _atomic_write_bytes(dest, b"hello", 0o644)
        assert dest.read_bytes() == b"hello"

    def test_preserves_0644_perm(self, tmp_path: Path):
        dest = tmp_path / "pub.md"
        _atomic_write_bytes(dest, b"x", 0o644)
        assert (dest.stat().st_mode & 0o777) == 0o644, "public template must end 0644, NOT mkstemp 0600"

    def test_preserves_0444_readonly_perm(self, tmp_path: Path):
        """System-default files (SWARMAI/SOUL/AGENT) are 0444 read-only."""
        dest = tmp_path / "ro.md"
        _atomic_write_bytes(dest, b"x", 0o444)
        assert (dest.stat().st_mode & 0o777) == 0o444

    def test_replace_over_readonly_dest(self, tmp_path: Path):
        """os.replace onto an existing 0444 dest must succeed (POSIX: rename needs
        write on the DIR, not the file). This is the load-bearing correctness case
        for the system-default refresh path."""
        dest = tmp_path / "ro.md"
        dest.write_bytes(b"old")
        import os as _os
        _os.chmod(dest, 0o444)
        _atomic_write_bytes(dest, b"new", 0o444)
        assert dest.read_bytes() == b"new"
        assert (dest.stat().st_mode & 0o777) == 0o444

    def test_no_temp_residue_on_success(self, tmp_path: Path):
        dest = tmp_path / "f.md"
        _atomic_write_bytes(dest, b"data", 0o644)
        leftovers = [p for p in tmp_path.iterdir() if p.name != "f.md"]
        assert not leftovers, f"no temp file must remain, found {leftovers}"

    def test_no_temp_residue_on_error(self, tmp_path: Path, monkeypatch):
        """If os.replace fails, the temp file is cleaned up (no .tmp litter)."""
        import os as _os
        dest = tmp_path / "f.md"

        def _boom(*a, **k):
            raise OSError("replace failed")

        monkeypatch.setattr(_os, "replace", _boom)
        with pytest.raises(OSError):
            _atomic_write_bytes(dest, b"data", 0o644)
        leftovers = list(tmp_path.iterdir())
        assert not leftovers, f"temp must be cleaned on failure, found {leftovers}"


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def tmp_dirs(tmp_path: Path):
    """Create temporary context_dir and templates_dir for testing."""
    context_dir = tmp_path / "context"
    templates_dir = tmp_path / "templates"
    context_dir.mkdir()
    templates_dir.mkdir()
    return context_dir, templates_dir


@pytest.fixture
def bootstrap_template(tmp_dirs):
    """Write a BOOTSTRAP.md template into templates_dir."""
    _, templates_dir = tmp_dirs
    bootstrap = templates_dir / "BOOTSTRAP.md"
    bootstrap.write_text("# Welcome to SwarmAI\nOnboarding content here.\n")
    return bootstrap


# The default USER.md template content (matches backend/context/USER.md)
EMPTY_USER_TEMPLATE = """\
<!-- 👤 USER-CUSTOMIZED — This file is YOURS. SwarmAI will never overwrite your edits.
     Fill in your details so the agent can personalize responses to your preferences. -->

# User — About You

_I'll learn about you as we work together. Update this anytime, or let me fill it in as I learn._

- **Name:**
- **What to call you:**
- **Timezone:**
- **Primary language:**
- **Role:**

## Work Context

_(What do you do? What are you building?)_

## Preferences

_(How do you like to work?)_
"""

FILLED_USER_TEMPLATE = """\
<!-- 👤 USER-CUSTOMIZED -->

# User — About You

- **Name:** Alice
- **What to call you:** Alice
- **Timezone:** UTC+8
- **Primary language:** English
- **Role:** Backend Engineer
"""


# ── _is_empty_template() unit tests ───────────────────────────────────


class TestIsEmptyTemplate:
    """Unit tests for _is_empty_template() structural detection."""

    def _make_loader(self, tmp_dirs):
        context_dir, templates_dir = tmp_dirs
        return ContextDirectoryLoader(
            context_dir=context_dir,
            templates_dir=templates_dir,
        )

    def test_empty_template_returns_true(self, tmp_dirs):
        loader = self._make_loader(tmp_dirs)
        assert loader._is_empty_template(EMPTY_USER_TEMPLATE) is True

    def test_filled_template_returns_false(self, tmp_dirs):
        loader = self._make_loader(tmp_dirs)
        assert loader._is_empty_template(FILLED_USER_TEMPLATE) is False

    def test_partially_filled_name_returns_false(self, tmp_dirs):
        loader = self._make_loader(tmp_dirs)
        content = "- **Name:** Bob\n- **Timezone:**\n- **Role:**\n"
        assert loader._is_empty_template(content) is False

    def test_partially_filled_role_returns_false(self, tmp_dirs):
        loader = self._make_loader(tmp_dirs)
        content = "- **Name:**\n- **Timezone:**\n- **Role:** Engineer\n"
        assert loader._is_empty_template(content) is False

    def test_underscore_placeholder_treated_as_empty(self, tmp_dirs):
        loader = self._make_loader(tmp_dirs)
        content = "- **Name:** _\n- **Timezone:**\n- **Role:**\n"
        assert loader._is_empty_template(content) is True

    def test_no_indicators_at_all_returns_true(self, tmp_dirs):
        """Content without any indicator fields is treated as empty."""
        loader = self._make_loader(tmp_dirs)
        assert loader._is_empty_template("# Just a heading\nSome text.") is True

    def test_empty_string_returns_true(self, tmp_dirs):
        loader = self._make_loader(tmp_dirs)
        assert loader._is_empty_template("") is True

    def test_field_at_end_of_file_no_newline(self, tmp_dirs):
        """Field on the last line with no trailing newline."""
        loader = self._make_loader(tmp_dirs)
        content = "- **Name:** Alice"
        assert loader._is_empty_template(content) is False

    def test_field_at_end_of_file_empty_no_newline(self, tmp_dirs):
        """Empty field on the last line with no trailing newline."""
        loader = self._make_loader(tmp_dirs)
        content = "- **Name:**"
        assert loader._is_empty_template(content) is True


# ── _maybe_create_bootstrap() unit tests ──────────────────────────────


class TestMaybeCreateBootstrap:
    """Unit tests for _maybe_create_bootstrap() conditional creation."""

    def test_creates_bootstrap_when_user_md_is_empty_template(
        self, tmp_dirs, bootstrap_template
    ):
        context_dir, templates_dir = tmp_dirs
        user_md = context_dir / "USER.md"
        user_md.write_text(EMPTY_USER_TEMPLATE, encoding="utf-8")

        loader = ContextDirectoryLoader(
            context_dir=context_dir, templates_dir=templates_dir
        )
        loader._maybe_create_bootstrap()

        bootstrap_md = context_dir / "BOOTSTRAP.md"
        assert bootstrap_md.exists()
        assert bootstrap_md.read_text() == bootstrap_template.read_text()

    def test_skips_when_user_md_is_filled(self, tmp_dirs, bootstrap_template):
        context_dir, templates_dir = tmp_dirs
        user_md = context_dir / "USER.md"
        user_md.write_text(FILLED_USER_TEMPLATE, encoding="utf-8")

        loader = ContextDirectoryLoader(
            context_dir=context_dir, templates_dir=templates_dir
        )
        loader._maybe_create_bootstrap()

        assert not (context_dir / "BOOTSTRAP.md").exists()

    def test_skips_when_bootstrap_already_exists(
        self, tmp_dirs, bootstrap_template
    ):
        context_dir, templates_dir = tmp_dirs
        user_md = context_dir / "USER.md"
        user_md.write_text(EMPTY_USER_TEMPLATE, encoding="utf-8")

        # Pre-create BOOTSTRAP.md with different content
        existing = context_dir / "BOOTSTRAP.md"
        existing.write_text("# Old bootstrap content\n")

        loader = ContextDirectoryLoader(
            context_dir=context_dir, templates_dir=templates_dir
        )
        loader._maybe_create_bootstrap()

        # Should NOT overwrite existing BOOTSTRAP.md
        assert existing.read_text() == "# Old bootstrap content\n"

    def test_skips_when_user_md_does_not_exist(
        self, tmp_dirs, bootstrap_template
    ):
        context_dir, templates_dir = tmp_dirs
        # No USER.md created

        loader = ContextDirectoryLoader(
            context_dir=context_dir, templates_dir=templates_dir
        )
        loader._maybe_create_bootstrap()

        assert not (context_dir / "BOOTSTRAP.md").exists()

    def test_skips_when_templates_dir_is_none(self, tmp_dirs):
        context_dir, _ = tmp_dirs
        user_md = context_dir / "USER.md"
        user_md.write_text(EMPTY_USER_TEMPLATE, encoding="utf-8")

        loader = ContextDirectoryLoader(
            context_dir=context_dir, templates_dir=None
        )
        loader._maybe_create_bootstrap()

        assert not (context_dir / "BOOTSTRAP.md").exists()

    def test_skips_when_bootstrap_template_missing(self, tmp_dirs):
        context_dir, templates_dir = tmp_dirs
        user_md = context_dir / "USER.md"
        user_md.write_text(EMPTY_USER_TEMPLATE, encoding="utf-8")
        # No BOOTSTRAP.md in templates_dir

        loader = ContextDirectoryLoader(
            context_dir=context_dir, templates_dir=templates_dir
        )
        loader._maybe_create_bootstrap()

        assert not (context_dir / "BOOTSTRAP.md").exists()

    def test_called_by_ensure_directory(self, tmp_dirs, bootstrap_template):
        """Verify ensure_directory() calls _maybe_create_bootstrap()."""
        context_dir, templates_dir = tmp_dirs
        # Write USER.md template into templates_dir so ensure_directory
        # copies it to context_dir
        (templates_dir / "USER.md").write_text(
            EMPTY_USER_TEMPLATE, encoding="utf-8"
        )

        loader = ContextDirectoryLoader(
            context_dir=context_dir, templates_dir=templates_dir
        )
        loader.ensure_directory()

        # After ensure_directory, USER.md should be copied (empty template)
        # and BOOTSTRAP.md should be created
        assert (context_dir / "USER.md").exists()
        assert (context_dir / "BOOTSTRAP.md").exists()


# ── compute_token_budget() unit tests ─────────────────────────────────


class TestComputeTokenBudget:
    """Unit tests for compute_token_budget() dynamic budget tiers.

    Validates: Requirements 11.1, 11.2, 11.3, 11.4, 11.5, 14.6
    """

    def _make_loader(self, tmp_dirs, token_budget=DEFAULT_TOKEN_BUDGET):
        context_dir, templates_dir = tmp_dirs
        return ContextDirectoryLoader(
            context_dir=context_dir,
            token_budget=token_budget,
            templates_dir=templates_dir,
        )

    def test_large_model_200k(self, tmp_dirs):
        """>=200K context window → BUDGET_LARGE_MODEL (50,000)."""
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(200_000) == BUDGET_LARGE_MODEL

    def test_large_model_499k(self, tmp_dirs):
        """499K context window → BUDGET_LARGE_MODEL (50,000)."""
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(499_999) == BUDGET_LARGE_MODEL

    def test_1m_model_500k(self, tmp_dirs):
        """>=500K context window → BUDGET_1M_MODEL (100,000)."""
        from core.context_directory_loader import BUDGET_1M_MODEL
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(500_000) == BUDGET_1M_MODEL

    def test_1m_model_1m(self, tmp_dirs):
        """1M context window → BUDGET_1M_MODEL (100,000)."""
        from core.context_directory_loader import BUDGET_1M_MODEL
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(1_000_000) == BUDGET_1M_MODEL

    def test_medium_model_64k(self, tmp_dirs):
        """Exactly 64K → DEFAULT_TOKEN_BUDGET (30,000)."""
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(64_000) == DEFAULT_TOKEN_BUDGET

    def test_medium_model_128k(self, tmp_dirs):
        """128K context window → DEFAULT_TOKEN_BUDGET (30,000)."""
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(128_000) == DEFAULT_TOKEN_BUDGET

    def test_medium_model_199999(self, tmp_dirs):
        """Just below 200K → DEFAULT_TOKEN_BUDGET (30,000)."""
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(199_999) == DEFAULT_TOKEN_BUDGET

    def test_small_model_below_64k(self, tmp_dirs):
        """<64K → instance token_budget (self.token_budget)."""
        loader = self._make_loader(tmp_dirs, token_budget=15_000)
        assert loader.compute_token_budget(32_000) == 15_000

    def test_small_model_63999(self, tmp_dirs):
        """Just below 64K boundary → instance token_budget."""
        loader = self._make_loader(tmp_dirs, token_budget=20_000)
        assert loader.compute_token_budget(63_999) == 20_000

    def test_none_falls_back_to_default(self, tmp_dirs):
        """None model_context_window → DEFAULT_TOKEN_BUDGET."""
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(None) == DEFAULT_TOKEN_BUDGET

    def test_zero_falls_back_to_default(self, tmp_dirs):
        """Zero model_context_window → DEFAULT_TOKEN_BUDGET."""
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(0) == DEFAULT_TOKEN_BUDGET


# ── _enforce_token_budget() truncate_from unit tests ──────────────────


class TestEnforceTokenBudgetNoTruncate:
    """Unit tests for the NO-TRUNCATE read-line policy (XG directive 2026-06-28,
    pure-filesystem recall design §3.5).

    The assembly line does NOT arbitrate content by size. On budget overshoot it
    emits a WARNING and returns the FULL content untruncated — size governance is
    the separate write-side management line's job. These tests REPLACE the old
    truncate_from tests (truncation behavior was removed by design).

    Validates: pure-filesystem recall design §3.5 (read-line does not truncate).
    """

    def _make_loader(self, tmp_dirs, token_budget=DEFAULT_TOKEN_BUDGET):
        context_dir, templates_dir = tmp_dirs
        return ContextDirectoryLoader(
            context_dir=context_dir,
            token_budget=token_budget,
            templates_dir=templates_dir,
        )

    def test_overshoot_returns_full_content_untruncated(self, tmp_dirs):
        """When total exceeds budget, content is returned FULL — no truncation."""
        loader = self._make_loader(tmp_dirs, token_budget=50)
        long_content = " ".join(f"word{i}" for i in range(200))
        sections = [
            (0, "Fixed", "small", False, "tail"),
            (5, "Big", long_content, True, "tail"),
        ]
        result = loader._enforce_token_budget(sections, budget=50)
        # Identical to input — every word preserved, no truncation indicator.
        assert result == sections
        _, _, content, _, _ = result[1]
        assert content.startswith("word0")
        assert content.rstrip().endswith("word199")
        assert "[Truncated:" not in content

    def test_overshoot_emits_warning_log(self, tmp_dirs, caplog):
        """Overshoot emits a WARNING (the signal for the write-side line)."""
        import logging
        loader = self._make_loader(tmp_dirs, token_budget=10)
        long_content = " ".join(f"word{i}" for i in range(500))
        sections = [
            (0, "Fixed", "small", False, "tail"),
            (5, "Big", long_content, True, "head"),
        ]
        with caplog.at_level(logging.WARNING):
            loader._enforce_token_budget(sections, budget=10)
        assert any(
            "exceeds token budget" in r.message for r in caplog.records
        ), "expected a budget-overshoot WARNING"

    def test_no_truncation_when_under_budget(self, tmp_dirs):
        """Sections under budget are returned unchanged (unchanged behavior)."""
        loader = self._make_loader(tmp_dirs, token_budget=100_000)
        sections = [
            (0, "A", "hello world", False, "tail"),
            (5, "B", "foo bar", True, "head"),
        ]
        result = loader._enforce_token_budget(sections, budget=100_000)
        assert result == sections

    def test_under_budget_emits_no_warning(self, tmp_dirs, caplog):
        """No warning when content fits — warning is overshoot-only."""
        import logging
        loader = self._make_loader(tmp_dirs, token_budget=100_000)
        sections = [(0, "A", "hello world", False, "tail")]
        with caplog.at_level(logging.WARNING):
            loader._enforce_token_budget(sections, budget=100_000)
        assert not any(
            "exceeds token budget" in r.message for r in caplog.records
        )

    def test_truncatable_flag_no_longer_causes_truncation(self, tmp_dirs):
        """Even truncatable=True sections survive intact on overshoot.

        Mutation guard: if someone re-introduces truncation, the truncatable
        section would shrink and this assertion would catch it.
        """
        loader = self._make_loader(tmp_dirs, token_budget=30)
        long_content = " ".join(f"w{i}" for i in range(500))
        sections = [
            (0, "Fixed", "small", False, "tail"),
            (5, "Big", long_content, True, "tail"),
        ]
        result = loader._enforce_token_budget(sections, budget=30)
        _, _, content, truncatable, _ = result[1]
        assert truncatable is True
        assert content == long_content  # byte-identical, fully preserved


# ── L1 Cache Budget-Tier Tests ─────────────────────────────────────────


class TestWriteL1Cache:
    """Tests for _write_l1_cache() budget header writing."""

    def _make_loader(self, tmp_dirs, token_budget=DEFAULT_TOKEN_BUDGET):
        context_dir, templates_dir = tmp_dirs
        return ContextDirectoryLoader(
            context_dir=context_dir,
            templates_dir=templates_dir,
            token_budget=token_budget,
        )

    def test_writes_budget_header_as_first_line(self, tmp_dirs):
        """Cache file starts with <!-- budget:NNNNN --> header."""
        loader = self._make_loader(tmp_dirs)
        loader._write_l1_cache("hello world", budget=40000)
        l1_path = tmp_dirs[0] / "L1_SYSTEM_PROMPTS.md"
        raw = l1_path.read_text(encoding="utf-8")
        assert raw.startswith("<!-- budget:40000 -->\n")

    def test_content_follows_header(self, tmp_dirs):
        """Actual content appears after the budget header line."""
        loader = self._make_loader(tmp_dirs)
        loader._write_l1_cache("my context content", budget=25000)
        l1_path = tmp_dirs[0] / "L1_SYSTEM_PROMPTS.md"
        raw = l1_path.read_text(encoding="utf-8")
        lines = raw.split("\n", 1)
        assert lines[0] == "<!-- budget:25000 -->"
        assert lines[1] == "my context content"

    def test_default_budget_is_default_token_budget(self, tmp_dirs):
        """When budget is not specified, DEFAULT_TOKEN_BUDGET is used."""
        loader = self._make_loader(tmp_dirs)
        loader._write_l1_cache("content")
        l1_path = tmp_dirs[0] / "L1_SYSTEM_PROMPTS.md"
        raw = l1_path.read_text(encoding="utf-8")
        assert raw.startswith(f"<!-- budget:{DEFAULT_TOKEN_BUDGET} -->\n")

    def test_no_temp_file_left_behind(self, tmp_dirs):
        """Atomic write cleans up: only the L1 file exists, no stray temp file.

        The atomic temp-file + os.replace pattern must not litter the .context
        dir with an unrenamed temp on success.
        """
        loader = self._make_loader(tmp_dirs)
        loader._write_l1_cache("some content", budget=40000)
        context_dir = tmp_dirs[0]
        files = sorted(p.name for p in context_dir.iterdir())
        assert files == ["L1_SYSTEM_PROMPTS.md"], (
            f"expected only the L1 cache file, found {files}"
        )

    def test_atomic_write_no_torn_read_under_concurrency(self, tmp_dirs):
        """AC3/AC5: a concurrent reader NEVER sees a torn (header-present,
        body-truncated) L1 file while writers overwrite it in parallel.

        This is the regression that the atomic os.replace prevents once
        load_all() runs in worker threads (build_system_prompt to_thread).
        Mutation-proof: revert _write_l1_cache to a raw
        ``l1_path.write_text(header + content)`` and this test goes RED
        (a reader catches a partial body).
        """
        import threading

        loader = self._make_loader(tmp_dirs)
        l1_path = tmp_dirs[0] / "L1_SYSTEM_PROMPTS.md"

        # A large body widens the torn-write window; a sentinel terminator lets
        # a reader detect truncation deterministically.
        BODY = "X" * 200_000 + "\n<<<END>>>"
        loader._write_l1_cache(BODY, budget=40000)  # seed a valid file

        stop = threading.Event()
        torn = []

        def writer():
            n = 0
            while not stop.is_set():
                # alternate content so the file is genuinely rewritten each pass
                loader._write_l1_cache(BODY + str(n % 10), budget=40000)
                n += 1

        def reader():
            while not stop.is_set():
                try:
                    raw = l1_path.read_text(encoding="utf-8")
                except (OSError, FileNotFoundError):
                    # os.replace guarantees the path always resolves to a
                    # complete file; a missing file would itself be a tear.
                    torn.append("missing")
                    continue
                if not raw:
                    continue
                # A well-formed file has the budget header AND the sentinel.
                # Header present but sentinel absent == a torn body.
                if raw.startswith("<!-- budget:") and "<<<END>>>" not in raw:
                    torn.append(f"len={len(raw)}")

        writers = [threading.Thread(target=writer) for _ in range(3)]
        readers = [threading.Thread(target=reader) for _ in range(3)]
        for t in writers + readers:
            t.start()
        # Let them race briefly.
        time_slept = 0.0
        import time as _time
        while time_slept < 0.8:
            _time.sleep(0.05)
            time_slept += 0.05
        stop.set()
        for t in writers + readers:
            t.join(timeout=2)

        assert not torn, (
            f"reader observed {len(torn)} torn L1 reads (first few: {torn[:3]}) "
            "— _write_l1_cache is not atomic"
        )


    def test_l1_cache_file_is_owner_only_0600(self, tmp_dirs):
        """AC4: the L1 cache (holds assembled MEMORY/USER/EVOLUTION content) ends
        up owner-only 0600 — delegating to _atomic_write_bytes(..., 0o600) must
        preserve the perm the hand-rolled mkstemp-inheritance produced.

        Mutation-proof: pass 0o644 to the delegate and this goes RED.
        """
        import stat as _stat

        loader = self._make_loader(tmp_dirs)
        loader._write_l1_cache("owner-only content", budget=40000)
        l1_path = tmp_dirs[0] / "L1_SYSTEM_PROMPTS.md"
        mode = _stat.S_IMODE(l1_path.stat().st_mode)
        assert mode == 0o600, f"expected 0o600 owner-only, got {oct(mode)}"

    def test_oserror_is_swallowed_not_raised(self, tmp_dirs, monkeypatch, caplog):
        """AC5: _write_l1_cache is non-fatal (docstring contract): an OSError from
        the atomic write is logged as a warning and SWALLOWED, never propagated
        into build_system_prompt.

        Mutation-proof: narrow the `except (OSError, UnicodeEncodeError)` wrapper
        so it no longer catches OSError → this goes RED (the OSError propagates).
        """
        import logging
        import core.context_directory_loader as cdl

        loader = self._make_loader(tmp_dirs)

        def _boom(dest, data, perm):
            raise OSError("disk full")

        monkeypatch.setattr(cdl, "_atomic_write_bytes", _boom)
        with caplog.at_level(logging.WARNING):
            # Must NOT raise — the contract is best-effort logging + continue.
            loader._write_l1_cache("content that fails to write", budget=40000)
        assert "Failed to write L1 cache" in caplog.text

    def test_encode_error_is_swallowed_via_real_path(self, tmp_dirs, caplog):
        """AC5 (hardened, Gate-2): a UnicodeEncodeError from the REAL .encode('utf-8')
        path (unpaired surrogate in assembled content, e.g. a corrupted MEMORY.md)
        must ALSO be swallowed — it is NOT an OSError. No monkeypatch: this drives
        the actual encode call so the test proves the widened guard.

        Mutation-proof: revert the guard to bare `except OSError` and this goes RED
        (UnicodeEncodeError escapes into build_system_prompt).
        """
        import logging

        loader = self._make_loader(tmp_dirs)
        with caplog.at_level(logging.WARNING):
            # \ud800 is an unpaired high surrogate — .encode('utf-8') raises
            # UnicodeEncodeError, which the OLD text-mode fh.write also raised.
            loader._write_l1_cache("memory\ud800corrupt", budget=40000)
        assert "Failed to write L1 cache" in caplog.text


class TestLoadL1IfFresh:
    """Tests for _load_l1_if_fresh() budget-tier validation."""

    def _make_loader(self, tmp_dirs, token_budget=DEFAULT_TOKEN_BUDGET):
        context_dir, templates_dir = tmp_dirs
        return ContextDirectoryLoader(
            context_dir=context_dir,
            templates_dir=templates_dir,
            token_budget=token_budget,
        )

    def _write_cache_with_budget(self, context_dir, budget, content="cached content"):
        """Helper: write an L1 cache file with a budget header."""
        l1_path = context_dir / "L1_SYSTEM_PROMPTS.md"
        l1_path.write_text(
            f"<!-- budget:{budget} -->\n{content}", encoding="utf-8"
        )

    def test_returns_content_when_budget_matches(self, tmp_dirs):
        """Returns cached content (sans header) when budget matches."""
        loader = self._make_loader(tmp_dirs)
        self._write_cache_with_budget(tmp_dirs[0], 40000, "my content")
        # Bypass _is_l1_fresh by monkeypatching
        loader._is_l1_fresh = lambda: True
        result = loader._load_l1_if_fresh(expected_budget=40000)
        assert result == "my content"

    def test_returns_none_when_budget_mismatch(self, tmp_dirs):
        """Returns None when cached budget differs from expected."""
        loader = self._make_loader(tmp_dirs)
        self._write_cache_with_budget(tmp_dirs[0], 40000, "my content")
        loader._is_l1_fresh = lambda: True
        result = loader._load_l1_if_fresh(expected_budget=25000)
        assert result is None

    def test_returns_none_when_header_missing(self, tmp_dirs):
        """Returns None for old-format cache without budget header."""
        loader = self._make_loader(tmp_dirs)
        l1_path = tmp_dirs[0] / "L1_SYSTEM_PROMPTS.md"
        l1_path.write_text("old cache without header\n", encoding="utf-8")
        loader._is_l1_fresh = lambda: True
        result = loader._load_l1_if_fresh(expected_budget=25000)
        assert result is None

    def test_returns_none_when_no_newline(self, tmp_dirs):
        """Returns None for malformed cache with no newline."""
        loader = self._make_loader(tmp_dirs)
        l1_path = tmp_dirs[0] / "L1_SYSTEM_PROMPTS.md"
        l1_path.write_text("<!-- budget:40000 -->", encoding="utf-8")
        loader._is_l1_fresh = lambda: True
        result = loader._load_l1_if_fresh(expected_budget=40000)
        assert result is None

    def test_returns_none_when_l1_not_fresh(self, tmp_dirs):
        """Returns None when _is_l1_fresh() returns False."""
        loader = self._make_loader(tmp_dirs)
        self._write_cache_with_budget(tmp_dirs[0], 40000)
        loader._is_l1_fresh = lambda: False
        result = loader._load_l1_if_fresh(expected_budget=40000)
        assert result is None

    def test_returns_none_when_file_missing(self, tmp_dirs):
        """Returns None when L1 cache file does not exist."""
        loader = self._make_loader(tmp_dirs)
        loader._is_l1_fresh = lambda: True
        result = loader._load_l1_if_fresh(expected_budget=25000)
        assert result is None

    def test_multiline_content_preserved(self, tmp_dirs):
        """Multi-line content after header is returned intact."""
        loader = self._make_loader(tmp_dirs)
        content = "line one\nline two\nline three"
        self._write_cache_with_budget(tmp_dirs[0], 25000, content)
        loader._is_l1_fresh = lambda: True
        result = loader._load_l1_if_fresh(expected_budget=25000)
        assert result == content

    def test_roundtrip_write_then_load(self, tmp_dirs):
        """Content survives a write→load roundtrip with matching budget."""
        loader = self._make_loader(tmp_dirs)
        original = "## SwarmAI\nHello world\n\n## Memory\nStuff here"
        loader._write_l1_cache(original, budget=40000)
        loader._is_l1_fresh = lambda: True
        result = loader._load_l1_if_fresh(expected_budget=40000)
        assert result == original

    def test_roundtrip_budget_mismatch_returns_none(self, tmp_dirs):
        """Write with budget A, load with budget B → None."""
        loader = self._make_loader(tmp_dirs)
        loader._write_l1_cache("content", budget=40000)
        loader._is_l1_fresh = lambda: True
        result = loader._load_l1_if_fresh(expected_budget=25000)
        assert result is None


# ── CJK Token Estimation ─────────────────────────────────────────────


class TestEstimateTokensCJK:
    """Tests for CJK-aware token estimation."""

    def test_pure_ascii_unchanged(self):
        """Pure ASCII text uses the calibrated word-based coefficient."""
        text = "Hello world this is a test"
        result = ContextDirectoryLoader.estimate_tokens(text)
        # 6 words * 2.2 (LATIN_TOKENS_PER_WORD) = 13
        assert result == 13

    def test_pure_chinese_text(self):
        """Pure Chinese text should count characters, not words."""
        # 13 Chinese characters * 1.1 (CJK_TOKENS_PER_CHAR) = 14
        text = "你好世界这是一个测试用例吧"
        result = ContextDirectoryLoader.estimate_tokens(text)
        assert result == 14  # int(13 * 1.1) = 14

    def test_mixed_cjk_and_latin(self):
        """Mixed CJK + Latin text sums both estimates."""
        # 4 Chinese chars * 1.1 ≈ 4 CJK tokens
        # "hello world" → 2 words * 2.2 ≈ 4 Latin tokens
        text = "你好世界 hello world"
        result = ContextDirectoryLoader.estimate_tokens(text)
        assert result >= 8  # ~4 CJK + ~4 Latin

    def test_chinese_much_higher_than_naive(self):
        """A Chinese paragraph should estimate far more than 1 token."""
        # This is a single "word" by split() but should be many tokens
        text = "这是一段中文文本用于测试令牌估算的准确性确保中日韩文字不会被低估"
        naive_word_count = len(text.split())
        assert naive_word_count == 1  # Naive split sees 1 word
        result = ContextDirectoryLoader.estimate_tokens(text)
        assert result >= 15  # Should be much more than 1

    def test_japanese_hiragana(self):
        """Japanese hiragana characters should be CJK-counted."""
        text = "おはようございます"  # 9 hiragana chars
        result = ContextDirectoryLoader.estimate_tokens(text)
        assert result == 9  # int(9 * 1.1) = 9

    def test_empty_returns_zero(self):
        """Empty/whitespace returns 0 (unchanged behavior)."""
        assert ContextDirectoryLoader.estimate_tokens("") == 0
        assert ContextDirectoryLoader.estimate_tokens("   ") == 0

    def test_single_cjk_char(self):
        """Single CJK character returns at least 1."""
        result = ContextDirectoryLoader.estimate_tokens("你")
        assert result >= 1


# ── Group Channel Exclusion ──────────────────────────────────────────


class TestExcludeFilenames:
    """Tests for the exclude_filenames parameter in assembly."""

    def _write_context_files(self, context_dir: Path):
        """Create minimal context files for testing exclusion."""
        (context_dir / "SWARMAI.md").write_text("# Core\nYou are SwarmAI.")
        (context_dir / "MEMORY.md").write_text("# Memory\nSecret personal memory content.")
        (context_dir / "USER.md").write_text("# User\n**Name:** TestUser\n**Timezone:** UTC\n**Role:** Dev")
        (context_dir / "PROJECTS.md").write_text("# Projects\nActive project list.")

    def test_no_exclusion_includes_all(self, tmp_path):
        """Without exclusions, all files appear in output."""
        context_dir = tmp_path / "ctx"
        context_dir.mkdir()
        self._write_context_files(context_dir)
        loader = ContextDirectoryLoader(context_dir=context_dir)
        result = loader._assemble_from_sources(exclude_filenames=None)
        assert "Secret personal memory" in result
        assert "TestUser" in result

    def test_exclude_memory_removes_it(self, tmp_path):
        """Excluding MEMORY.md removes personal memory from output."""
        context_dir = tmp_path / "ctx"
        context_dir.mkdir()
        self._write_context_files(context_dir)
        loader = ContextDirectoryLoader(context_dir=context_dir)
        result = loader._assemble_from_sources(exclude_filenames={"MEMORY.md"})
        assert "Secret personal memory" not in result
        assert "SwarmAI" in result  # Non-excluded files still present

    def test_exclude_memory_and_user(self, tmp_path):
        """Group channel exclusion removes both MEMORY.md and USER.md."""
        context_dir = tmp_path / "ctx"
        context_dir.mkdir()
        self._write_context_files(context_dir)
        loader = ContextDirectoryLoader(context_dir=context_dir)
        from core.context_directory_loader import GROUP_CHANNEL_EXCLUDE
        result = loader._assemble_from_sources(exclude_filenames=set(GROUP_CHANNEL_EXCLUDE))
        assert "Secret personal memory" not in result
        assert "TestUser" not in result
        assert "SwarmAI" in result

    def test_load_all_skips_cache_when_excluding(self, tmp_path):
        """load_all bypasses L1 cache when exclude_filenames is set."""
        context_dir = tmp_path / "ctx"
        context_dir.mkdir()
        self._write_context_files(context_dir)
        loader = ContextDirectoryLoader(context_dir=context_dir)

        # Pre-populate L1 cache with full content (includes MEMORY)
        full = loader._assemble_from_sources()
        loader._write_l1_cache(full, budget=50000)
        loader._is_l1_fresh = lambda: True

        # Load with exclusion — should NOT use the cache
        result = loader.load_all(
            model_context_window=200_000,
            exclude_filenames={"MEMORY.md"},
        )
        assert "Secret personal memory" not in result

    def test_load_all_no_exclusion_uses_cache(self, tmp_path):
        """load_all uses L1 cache when no exclusions (normal path)."""
        context_dir = tmp_path / "ctx"
        context_dir.mkdir()
        self._write_context_files(context_dir)
        loader = ContextDirectoryLoader(context_dir=context_dir)

        # Write cache with known content
        loader._write_l1_cache("cached content only", budget=50000)
        loader._is_l1_fresh = lambda: True

        result = loader.load_all(model_context_window=200_000)
        assert result == "cached content only"


# ── Content Cleaning ─────────────────────────────────────────────────


class TestCleanContent:
    """Tests for _clean_content — HTML comment stripping and H1 dedup."""

    def test_strips_html_comments(self):
        """HTML comments are removed from assembled content."""
        raw = '<!-- ⚙️ SYSTEM DEFAULT -->\n# Soul\nYou are warm.'
        result = ContextDirectoryLoader._clean_content(raw, "Soul")
        assert "SYSTEM DEFAULT" not in result
        assert "warm" in result

    def test_strips_multiline_html_comment(self):
        """Multi-line HTML comments are fully removed."""
        raw = (
            '<!-- ⚙️ SYSTEM DEFAULT — Managed by SwarmAI.\n'
            '     Edits here will be OVERWRITTEN. -->\n'
            '# Identity\nI am SwarmAI.'
        )
        result = ContextDirectoryLoader._clean_content(raw, "Identity")
        assert "OVERWRITTEN" not in result
        assert "SwarmAI" in result

    def test_strips_redundant_h1_matching_section_name(self):
        """H1 that matches section_name is removed (avoids ## + # duplication)."""
        raw = "# SwarmAI — Your AI Command Center\n\nYou are the central intelligence."
        result = ContextDirectoryLoader._clean_content(raw, "SwarmAI")
        assert not result.startswith("# SwarmAI")
        assert "central intelligence" in result

    def test_keeps_h1_not_matching_section_name(self):
        """H1 that doesn't match section_name is preserved."""
        raw = "# Completely Different Title\n\nSome content here."
        result = ContextDirectoryLoader._clean_content(raw, "SwarmAI")
        assert "# Completely Different Title" in result

    def test_keeps_h2_headers(self):
        """H2 headers are never stripped (only H1 is checked)."""
        raw = "## Sub Section\nContent here."
        result = ContextDirectoryLoader._clean_content(raw, "Sub Section")
        assert "## Sub Section" in result

    def test_empty_after_comment_strip_returns_empty(self):
        """If only HTML comments exist, returns empty string."""
        raw = "<!-- just a comment -->"
        result = ContextDirectoryLoader._clean_content(raw, "Test")
        assert result == ""

    def test_h1_with_colon_separator(self):
        """H1 with colon separator: 'Soul: Who You Are' matches 'Soul'."""
        raw = "# Soul: Who You Are\n\nPersonality content."
        result = ContextDirectoryLoader._clean_content(raw, "Soul")
        assert not result.startswith("# Soul")
        assert "Personality content" in result

    def test_h1_with_en_dash_separator(self):
        """H1 with en-dash: 'Agent – Directives' matches 'Agent Directives'."""
        raw = "# Agent Directives – How to Act\n\nBe resourceful."
        result = ContextDirectoryLoader._clean_content(raw, "Agent Directives")
        assert not result.startswith("# Agent Directives")
        assert "Be resourceful" in result

    def test_case_insensitive_h1_match(self):
        """H1 matching is case-insensitive."""
        raw = "# SWARMAI\n\nContent."
        result = ContextDirectoryLoader._clean_content(raw, "SwarmAI")
        assert not result.startswith("# SWARMAI")

    def test_preserves_content_without_h1(self):
        """Content without an H1 is returned unchanged (minus comments)."""
        raw = "Just plain content\nwith multiple lines."
        result = ContextDirectoryLoader._clean_content(raw, "Test")
        assert result == raw


class TestTokenCalibration:
    """Calibrated token estimation (run_3f25a73a).

    estimate_tokens must reflect the REAL opus-4-8 tokenizer, measured via
    invoke_model usage.input_tokens (baseline-subtracted) on 2026-06-28:
      - CJK ~1.07-1.11 tok/char  → CJK_TOKENS_PER_CHAR = 1.1
      - Latin ~2.0-2.5 tok/word (markdown/technical) → LATIN_TOKENS_PER_WORD = 2.2

    The OLD coefficients (CJK 0.667, Latin 1.333) under-counted real content
    by ~40-65% — these tests lock the calibration so it can't silently drift.
    """

    def test_constants_exist_and_calibrated(self):
        from core.context_directory_loader import (
            CJK_TOKENS_PER_CHAR,
            LATIN_TOKENS_PER_WORD,
        )
        # Calibrated to real tokenizer (NOT old 0.667 / 1.333)
        assert 1.0 <= CJK_TOKENS_PER_CHAR <= 1.2
        assert 2.0 <= LATIN_TOKENS_PER_WORD <= 2.6

    def test_cjk_within_15pct_of_real(self):
        """Recorded real: 63 CJK chars = 70 tokens (opus-4-8)."""
        from core.context_directory_loader import ContextDirectoryLoader
        # 63-char pure-CJK sample (no spaces)
        sample = "认知是操作系统知识硬盘数据充足但系统有问题输出仍错误真实分词器校准系数凭经验猜测数字测量中文每字符真实令牌数量统计学习记忆进化深度"[:63]
        real = 70
        est = ContextDirectoryLoader.estimate_tokens(sample)
        assert abs(est - real) / real <= 0.15, f"est={est} real={real}"

    def test_latin_within_20pct_of_real(self):
        """Recorded real: a 27-word latin sentence ~ 49-55 tokens (markdown/technical)."""
        from core.context_directory_loader import ContextDirectoryLoader
        sample = ("the quick brown fox jumps over lazy dog runs across field "
                  "calibrate latin coefficient against real tokenizer output "
                  "precisely without any punctuation here today now")
        nwords = len(sample.split())
        est = ContextDirectoryLoader.estimate_tokens(sample)
        # real ~ 2.0 tok/word; allow 20% band
        assert 1.6 * nwords <= est <= 2.6 * nwords, f"est={est} words={nwords}"

    def test_calibrated_higher_than_old_undercounting(self):
        """The whole point: new estimate must be HIGHER than the old under-count."""
        from core.context_directory_loader import ContextDirectoryLoader
        text = "SwarmAI 是一个自进化的 Agent OS。The READ path is THE differentiator."
        old_cjk = text  # simulate old: cjk/1.5 + words*4/3
        cjk_re = ContextDirectoryLoader._CJK_RE
        cjk = len(cjk_re.findall(text))
        words = len(cjk_re.sub("", text).split())
        old_est = int(cjk / 1.5) + int(words * 4 / 3)
        new_est = ContextDirectoryLoader.estimate_tokens(text)
        assert new_est > old_est, f"new={new_est} not > old={old_est}"


class TestCJKRangeUnification:
    """The two CJK detectors (loader _CJK_RE, health _is_cjk_like) covered
    DIFFERENT ranges (Gate-1 finding C): loader had Kana but not Hangul;
    health had Hangul but not Kana. After unification, BOTH Hangul AND Kana
    must classify as CJK through the canonical path."""

    def test_hangul_counted_as_cjk(self):
        from core.context_directory_loader import ContextDirectoryLoader
        # Pure Hangul, no spaces — must be per-char counted, not 1 "word"
        hangul = "안녕하세요반갑습니다오늘날씨가좋네요"  # 18 Hangul syllables
        est = ContextDirectoryLoader.estimate_tokens(hangul)
        # If Hangul were treated as Latin: 1 word -> ~2 tokens. As CJK: ~18*1.1
        assert est >= 12, f"Hangul not counted as CJK: est={est}"

    def test_kana_counted_as_cjk(self):
        from core.context_directory_loader import ContextDirectoryLoader
        kana = "こんにちはみなさんおげんきですかきょうはいいてんきですね"  # Hiragana, no spaces
        est = ContextDirectoryLoader.estimate_tokens(kana)
        assert est >= 12, f"Kana not counted as CJK: est={est}"


class TestInverseTruncationCoherence:
    """Gate-1 finding A: the inverse-truncation must derive words_to_keep from
    LATIN_TOKENS_PER_WORD, so a truncated section actually fits its token cap.
    A hardcoded inverse (old `* 3 / 4`) over-shot by ~65% after recalibration."""

    def test_daily_truncation_fits_cap(self):
        from core.prompt_builder import _truncate_daily_content, TOKEN_CAP_PER_DAILY_FILE
        from core.context_directory_loader import ContextDirectoryLoader
        # Build content well over the cap (pure latin, ~5000 words)
        big = " ".join(f"word{i}" for i in range(5000))
        assert ContextDirectoryLoader.estimate_tokens(big) > TOKEN_CAP_PER_DAILY_FILE
        out = _truncate_daily_content(big)
        # Strip the marker line before re-estimating the kept content
        kept = out.split("\n\n", 1)[1]
        est = ContextDirectoryLoader.estimate_tokens(kept)
        # Must fit under cap (with small headroom for the inverse rounding)
        assert est <= TOKEN_CAP_PER_DAILY_FILE, f"truncated content {est} > cap {TOKEN_CAP_PER_DAILY_FILE}"

    # test_section_truncation_fits_target DELETED 2026-08-14: it exercised the
    # dead read-line `_truncate_section` (removed — read-line no longer truncates).
    # The forward/inverse coefficient coherence is still covered by the DailyActivity
    # truncation test above (prompt_builder._truncate_section_by_tokens, the remaining
    # inverse path).


class TestCoreSectionNames:
    """core_section_names(): the SSOT-derived set for the completeness gate.

    Root-fix run_e47c1cfb: the completeness gate must key off CONTEXT_FILES
    programmatically, NOT a hand-typed literal (Gate-1 CHECK2 — a literal already
    drifted and dropped SELF). Core = system-owned (user_customized=False) OR
    non-truncatable P0-2 (truncatable=False). This is exactly {SWARMAI, IDENTITY,
    SOUL, SELF, AGENT} — SELF is user_customized=True but truncatable=False, so a
    single-predicate key misses it.
    """

    def test_core_set_is_the_five_system_critical_sections(self):
        from core.context_directory_loader import core_section_names
        assert set(core_section_names()) == {
            "SwarmAI", "Identity", "Soul", "Self-Portrait", "Agent Directives"
        }

    def test_core_set_derived_from_context_files_not_literal(self):
        from core.context_directory_loader import CONTEXT_FILES, core_section_names
        expected = {
            s.section_name for s in CONTEXT_FILES
            if (s.user_customized is False) or (s.truncatable is False)
        }
        assert set(core_section_names()) == expected

    def test_self_portrait_included_despite_user_customized(self):
        from core.context_directory_loader import core_section_names
        assert "Self-Portrait" in core_section_names()
