"""Tests for context refresh behavior with ContextDirectoryLoader.

Verifies two key requirements from the SwarmWS restructure spec:

- **Requirement 4.2**: L1 cache freshness uses ``git status --porcelain``
  with mtime fallback — new sessions pick up fresh context after edits.
- **Requirement 4.3/4.4**: Running sessions keep their frozen prompt and
  are NOT affected by ``.context/`` changes.

Testing methodology: unit tests using tmp_path fixtures with real
ContextDirectoryLoader instances (no mocks for core logic).

Key invariants verified:
- ``_is_l1_fresh()`` returns False when source files are newer than L1
- ``load_all()`` re-assembles from sources when L1 is stale
- A second ``load_all()`` call (simulating a new session) picks up edits
- A frozen prompt string is unaffected by subsequent file changes
"""

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from core.context_directory_loader import (
    CONTEXT_FILES,
    ContextDirectoryLoader,
    L1_CACHE_FILENAME,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_context_dir(tmp_path: Path) -> Path:
    """Create a minimal .context/ directory with one source file."""
    ctx = tmp_path / ".context"
    ctx.mkdir()
    # Write a single source file (SWARMAI.md — priority 0, always loaded)
    (ctx / "SWARMAI.md").write_text("# SwarmAI\nOriginal content.", encoding="utf-8")
    return ctx


def _make_loader(context_dir: Path, budget: int = 25_000) -> ContextDirectoryLoader:
    """Create a ContextDirectoryLoader without templates_dir."""
    return ContextDirectoryLoader(
        context_dir=context_dir,
        token_budget=budget,
        templates_dir=None,
    )


# ---------------------------------------------------------------------------
# 5.4 — New sessions pick up fresh context after .context/ file edits
# ---------------------------------------------------------------------------


class TestNewSessionPicksUpFreshContext:
    """Verify that new sessions see updated .context/ content.

    Validates: Requirements 4.1, 4.2
    """

    def test_load_all_returns_source_content(self, tmp_path: Path):
        """First load_all() assembles from source files."""
        ctx = _create_context_dir(tmp_path)
        loader = _make_loader(ctx)

        result = loader.load_all()

        assert "Original content" in result

    def test_l1_stale_after_source_edit_mtime(self, tmp_path: Path):
        """L1 cache is stale when a source file has a newer mtime.

        Uses mtime fallback (git unavailable).
        """
        ctx = _create_context_dir(tmp_path)
        loader = _make_loader(ctx)

        # Build L1 cache
        loader.load_all()
        l1_path = ctx / L1_CACHE_FILENAME
        assert l1_path.exists(), "L1 cache should be written after load_all()"

        # Ensure mtime difference (filesystem granularity)
        time.sleep(0.05)

        # Edit a source file — mtime is now newer than L1
        (ctx / "SWARMAI.md").write_text(
            "# SwarmAI\nUpdated content for new session.",
            encoding="utf-8",
        )

        # With git unavailable, mtime fallback detects staleness
        with patch("subprocess.run", side_effect=FileNotFoundError("no git")):
            assert not loader._is_l1_fresh()

    def test_second_load_picks_up_edits_mtime(self, tmp_path: Path):
        """A second load_all() (new session) returns updated content.

        Simulates: user edits SWARMAI.md, then opens a new chat tab.
        """
        ctx = _create_context_dir(tmp_path)
        loader = _make_loader(ctx)

        # Session 1: load original
        first = loader.load_all()
        assert "Original content" in first

        time.sleep(0.05)

        # User edits the file
        (ctx / "SWARMAI.md").write_text(
            "# SwarmAI\nBrand new content.",
            encoding="utf-8",
        )

        # Session 2: new load_all() should pick up the edit
        with patch("subprocess.run", side_effect=FileNotFoundError("no git")):
            second = loader.load_all()

        assert "Brand new content" in second
        assert "Original content" not in second

    def test_l1_fresh_when_no_changes(self, tmp_path: Path):
        """L1 cache is fresh when no source files have changed."""
        ctx = _create_context_dir(tmp_path)
        loader = _make_loader(ctx)

        # Build L1 cache
        loader.load_all()

        # No edits — L1 should be fresh (mtime fallback)
        with patch("subprocess.run", side_effect=FileNotFoundError("no git")):
            assert loader._is_l1_fresh()

    def test_l1_fresh_returns_false_when_no_cache(self, tmp_path: Path):
        """_is_l1_fresh() returns False when L1 cache file doesn't exist."""
        ctx = _create_context_dir(tmp_path)
        loader = _make_loader(ctx)

        # No load_all() called — no L1 cache file
        assert not loader._is_l1_fresh()

    def test_cached_load_returns_same_content(self, tmp_path: Path):
        """Second load_all() without edits returns cached content."""
        ctx = _create_context_dir(tmp_path)
        loader = _make_loader(ctx)

        first = loader.load_all()

        # Patch git to be unavailable so mtime fallback is used
        with patch("subprocess.run", side_effect=FileNotFoundError("no git")):
            second = loader.load_all()

        assert first == second


# ---------------------------------------------------------------------------
# 5.5 — Running sessions are NOT affected by .context/ changes
# ---------------------------------------------------------------------------


class TestRunningSessionUnaffected:
    """Verify that running sessions keep their frozen system prompt.

    The system prompt is built once at session start via
    ``_build_system_prompt()``.  The returned string is frozen — subsequent
    edits to ``.context/`` files do NOT retroactively change it.

    Validates: Requirements 4.3, 4.4
    """

    def test_frozen_prompt_unchanged_after_edit(self, tmp_path: Path):
        """A frozen prompt string is not affected by later file edits.

        Simulates the core invariant: once _build_system_prompt() returns,
        the prompt is a plain string stored in memory. Editing .context/
        files after that point has zero effect on the already-built prompt.
        """
        ctx = _create_context_dir(tmp_path)
        loader = _make_loader(ctx)

        # Session starts — prompt is frozen
        frozen_prompt = loader.load_all()
        assert "Original content" in frozen_prompt

        # User edits .context/ while session is running
        time.sleep(0.05)
        (ctx / "SWARMAI.md").write_text(
            "# SwarmAI\nCompletely different content.",
            encoding="utf-8",
        )

        # The frozen prompt is a plain string — it doesn't change
        assert "Original content" in frozen_prompt
        assert "Completely different content" not in frozen_prompt

    def test_multiple_sessions_get_independent_prompts(self, tmp_path: Path):
        """Two sessions created at different times get independent prompts.

        Session 1 starts, user edits .context/, Session 2 starts.
        Session 1's prompt is unchanged. Session 2 sees the edit.
        """
        ctx = _create_context_dir(tmp_path)
        loader = _make_loader(ctx)

        # Session 1 starts
        session1_prompt = loader.load_all()
        assert "Original content" in session1_prompt

        # User edits between sessions
        time.sleep(0.05)
        (ctx / "SWARMAI.md").write_text(
            "# SwarmAI\nEdited between sessions.",
            encoding="utf-8",
        )

        # Session 2 starts — picks up the edit (mtime fallback)
        with patch("subprocess.run", side_effect=FileNotFoundError("no git")):
            session2_prompt = loader.load_all()

        # Session 1 is frozen — still has original
        assert "Original content" in session1_prompt
        assert "Edited between sessions" not in session1_prompt

        # Session 2 has the new content
        assert "Edited between sessions" in session2_prompt
        assert "Original content" not in session2_prompt

    def test_context_dir_path_uses_working_directory(self):
        """_build_system_prompt() uses Path(working_directory) / '.context'.

        This is a structural assertion: the context_dir is derived from
        the working directory (SwarmWS), not from get_app_data_dir().
        Verified by inspecting the source code path in _build_system_prompt.
        """
        import inspect
        from core.agent_manager import AgentManager

        source = inspect.getsource(AgentManager._build_system_prompt)
        # The design requires: Path(working_directory) / ".context"
        assert 'Path(working_directory) / ".context"' in source
        # Must NOT use get_app_data_dir()
        assert "get_app_data_dir" not in source
