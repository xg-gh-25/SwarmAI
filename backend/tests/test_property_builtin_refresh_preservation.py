"""Preservation property tests for built-in defaults refresh.

This module captures baseline behavior that MUST be preserved after the
bugfix for ``ContextDirectoryLoader.ensure_directory()`` and
``ProjectionLayer.project_skills()``.  All three properties are expected
to PASS on the current unfixed code — they document invariants that the
fix must not break.

Testing methodology:
    Property-based testing with Hypothesis (Properties 2a, 2b) and a
    focused unit-style test using mocked ``SkillManager`` (Property 2c).

Key properties verified:

- **Property 2a — User files untouched**: Files in ``context_dir`` whose
  names do NOT appear in ``templates_dir`` are never modified or deleted
  by ``ensure_directory()``.
- **Property 2b — Idempotent when content matches**: Files that already
  exist in ``context_dir`` with identical content to ``templates_dir``
  remain unchanged after ``ensure_directory()``.
- **Property 2c — Stale symlink cleanup preserved**:
  ``ProjectionLayer.project_skills()`` removes symlinks pointing to
  skills no longer in the target set.

Validates: Requirements 3.1, 3.2, 3.3, 3.4
"""

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from core.context_directory_loader import ContextDirectoryLoader
from core.projection_layer import ProjectionLayer
from core.skill_manager import SkillInfo


# ── Hypothesis strategies ──────────────────────────────────────────────

# Safe filenames: lowercase alpha start + alphanumeric body + .md
_safe_filename = st.from_regex(r"[a-z][a-z0-9]{0,14}\.md", fullmatch=True)

# Non-empty file content (1-200 bytes)
_file_content = st.binary(min_size=1, max_size=200)


def _disjoint_filename_sets():
    """Strategy producing two disjoint sets of filenames.

    Returns (builtin_files, user_files) where each is a list of
    (filename, content) tuples and no filename appears in both sets.
    """
    return (
        st.lists(
            st.tuples(_safe_filename, _file_content),
            min_size=1,
            max_size=5,
            unique_by=lambda t: t[0],
        )
        .flatmap(
            lambda builtin: st.tuples(
                st.just(builtin),
                st.lists(
                    st.tuples(
                        _safe_filename.filter(
                            lambda fn, _b=builtin: fn
                            not in {b[0] for b in _b}
                        ),
                        _file_content,
                    ),
                    min_size=1,
                    max_size=5,
                    unique_by=lambda t: t[0],
                ),
            )
        )
    )


# ── Property 2a — User files untouched ─────────────────────────────────


class TestUserFilesUntouched:
    """Property 2a: User-created files are never modified or deleted.

    Validates: Requirements 3.1

    Files in ``context_dir`` whose names do NOT appear in
    ``templates_dir`` must survive ``ensure_directory()`` with their
    content unchanged.
    """

    @given(data=_disjoint_filename_sets())
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        
    )
    def test_user_files_preserved_after_ensure_directory(self, data):
        """After ensure_directory(), every user-created file in
        context_dir that has no corresponding file in templates_dir
        must retain its original content.

        **Validates: Requirements 3.1**
        """
        builtin_files, user_files = data

        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir) / "templates"
            context_dir = Path(tmpdir) / "context"
            templates_dir.mkdir()
            context_dir.mkdir()

            # Populate templates_dir with built-in files
            for filename, content in builtin_files:
                (templates_dir / filename).write_bytes(content)

            # Populate context_dir with user-created files (disjoint names)
            original_user_content = {}
            for filename, content in user_files:
                (context_dir / filename).write_bytes(content)
                original_user_content[filename] = content

            # Act
            loader = ContextDirectoryLoader(
                context_dir=context_dir,
                templates_dir=templates_dir,
            )
            loader.ensure_directory()

            # Assert: every user file still exists with original content
            for filename, expected in original_user_content.items():
                dest = context_dir / filename
                assert dest.exists(), (
                    f"User file {filename!r} was deleted by "
                    f"ensure_directory()"
                )
                actual = dest.read_bytes()
                assert actual == expected, (
                    f"User file {filename!r} was modified.\n"
                    f"  Expected: {expected!r}\n"
                    f"  Actual:   {actual!r}"
                )


# ── Property 2b — Idempotent when content matches ─────────────────────


class TestIdempotentWhenContentMatches:
    """Property 2b: No-op when dest already matches source.

    Validates: Requirements 3.3

    When files exist in both ``templates_dir`` and ``context_dir`` with
    identical content, ``ensure_directory()`` must leave them unchanged.
    """

    @given(
        file_entries=st.lists(
            st.tuples(_safe_filename, _file_content),
            min_size=1,
            max_size=5,
            unique_by=lambda t: t[0],
        )
    )
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        
    )
    def test_identical_files_unchanged_after_ensure_directory(
        self, file_entries
    ):
        """After ensure_directory(), files that already match the source
        must retain identical content (byte-for-byte comparison).

        **Validates: Requirements 3.3**
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir) / "templates"
            context_dir = Path(tmpdir) / "context"
            templates_dir.mkdir()
            context_dir.mkdir()

            # Populate BOTH dirs with identical content
            for filename, content in file_entries:
                (templates_dir / filename).write_bytes(content)
                (context_dir / filename).write_bytes(content)

            # Act
            loader = ContextDirectoryLoader(
                context_dir=context_dir,
                templates_dir=templates_dir,
            )
            loader.ensure_directory()

            # Assert: content is still identical
            for filename, content in file_entries:
                dest = context_dir / filename
                assert dest.exists(), (
                    f"File {filename!r} was deleted"
                )
                actual = dest.read_bytes()
                assert actual == content, (
                    f"File {filename!r} content changed despite "
                    f"being identical to source.\n"
                    f"  Expected: {content!r}\n"
                    f"  Actual:   {actual!r}"
                )


# ── Property 2c — Stale symlink cleanup preserved ─────────────────────


def _make_mock_skill_manager(
    skills: dict[str, Path],
    builtin_path: Path,
    user_skills_path: Path,
    plugin_skills_path: Path,
) -> MagicMock:
    """Create a mock SkillManager with a known skill cache.

    Args:
        skills: Mapping of folder_name → skill directory path.
        builtin_path: Path to built-in skills root.
        user_skills_path: Path to user skills root.
        plugin_skills_path: Path to plugin skills root.

    Returns:
        A MagicMock that behaves like SkillManager for projection.
    """
    cache = {}
    for folder_name, path in skills.items():
        cache[folder_name] = SkillInfo(
            folder_name=folder_name,
            name=folder_name,
            description=f"Test skill {folder_name}",
            version="1.0.0",
            source_tier="built-in",
            path=path,
            content=None,
        )

    mock_sm = MagicMock()
    mock_sm.get_cache = AsyncMock(return_value=cache)
    mock_sm.builtin_path = builtin_path
    mock_sm.user_skills_path = user_skills_path
    mock_sm.plugin_skills_path = plugin_skills_path
    return mock_sm


class TestStaleSymlinkCleanup:
    """Property 2c: Stale symlinks are removed by project_skills().

    Validates: Requirements 3.4

    When a symlink in the projection directory points to a skill that
    is no longer in the target set, ``project_skills()`` must remove it.
    """

    def test_stale_symlink_removed_after_project_skills(self):
        """project_skills() removes symlinks for skills no longer in
        the cache.

        **Validates: Requirements 3.4**
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "SwarmWS"
            skills_dir = workspace / ".claude" / "skills"
            skills_dir.mkdir(parents=True)

            # Create a real skill directory for the "active" skill
            builtin_root = Path(tmpdir) / "builtin_skills"
            active_skill_dir = builtin_root / "s_active"
            active_skill_dir.mkdir(parents=True)
            (active_skill_dir / "SKILL.md").write_text("# Active")

            # Create a stale symlink pointing to a non-existent skill
            stale_target = Path(tmpdir) / "deleted_skill"
            stale_link = skills_dir / "s_stale"
            stale_link.symlink_to(stale_target)

            assert stale_link.is_symlink(), "Stale symlink should exist"

            # Create the active skill symlink too
            active_link = skills_dir / "s_active"
            active_link.symlink_to(active_skill_dir)

            # Mock SkillManager with only the active skill
            mock_sm = _make_mock_skill_manager(
                skills={"s_active": active_skill_dir},
                builtin_path=builtin_root,
                user_skills_path=Path(tmpdir) / "user_skills",
                plugin_skills_path=Path(tmpdir) / "plugin_skills",
            )

            # Act
            projection = ProjectionLayer(mock_sm)
            asyncio.get_event_loop().run_until_complete(
                projection.project_skills(workspace, allow_all=True)
            )

            # Assert: stale symlink removed, active skill preserved (as copy, not symlink)
            assert not stale_link.exists() and not stale_link.is_symlink(), (
                "Stale symlink 's_stale' should have been removed"
            )
            assert active_link.exists() and active_link.is_dir(), (
                "Active skill 's_active' should exist as a directory copy "
                "(project_skills uses copytree, not symlinks)"
            )
