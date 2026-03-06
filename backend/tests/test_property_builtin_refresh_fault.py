"""Bug condition exploration test for ContextDirectoryLoader.ensure_directory().

This module verifies the fault condition described in bugfix requirement 1.1:
when a developer updates a built-in context file in ``backend/context/`` and
restarts the app, the system skips copying the updated file because
``ensure_directory()`` unconditionally skips any destination file that already
exists (``if dest.exists(): continue``).

Testing methodology:
    Property-based testing with Hypothesis.  Random file content pairs (source
    vs. stale destination) are generated to demonstrate that ``ensure_directory()``
    fails to overwrite existing built-in files with updated source content.

Key property verified:
    **Property 1 — Built-in context files always match source on startup.**
    For every file in ``templates_dir``, after calling ``ensure_directory()``,
    the corresponding file in ``context_dir`` SHALL have content identical to
    the source.  On unfixed code this property FAILS because the method skips
    existing files.

Validates: Requirements 1.1, 2.1
"""

import os
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from core.context_directory_loader import ContextDirectoryLoader


# ── Hypothesis strategies ──────────────────────────────────────────────

# Generate safe filenames: 1-20 lowercase alphanumeric chars + .md extension
_safe_filename = st.from_regex(r"[a-z][a-z0-9]{0,19}\.md", fullmatch=True)

# Generate non-empty file content (1-200 bytes of printable ASCII)
_file_content = st.binary(min_size=1, max_size=200)

# A single file entry: (filename, source_content, stale_dest_content)
# where source and stale content are guaranteed to differ.
_file_entry = st.tuples(
    _safe_filename,
    _file_content,
    _file_content,
).filter(lambda t: t[1] != t[2])

# A list of 1-5 file entries with unique filenames
_file_entries = st.lists(
    _file_entry,
    min_size=1,
    max_size=5,
    unique_by=lambda t: t[0],
)


# ── Property test ──────────────────────────────────────────────────────


class TestBuiltinRefreshFaultCondition:
    """Property 1: Built-in context files always match source on startup.

    Validates: Requirements 1.1, 2.1

    This test MUST FAIL on unfixed code.  The failure confirms the bug:
    ``ensure_directory()`` has ``if dest.exists(): continue`` which skips
    overwriting existing (stale) built-in files.
    """

    @given(file_entries=_file_entries)
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    def test_builtin_files_match_source_after_ensure_directory(
        self, file_entries
    ):
        """After ensure_directory(), every built-in file in context_dir
        must have content identical to the corresponding source file in
        templates_dir.

        **Validates: Requirements 1.1, 2.1**
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir) / "templates"
            context_dir = Path(tmpdir) / "context"
            templates_dir.mkdir()
            context_dir.mkdir()

            # Populate templates_dir (source of truth) and context_dir
            # (stale destination) with DIFFERENT content for each file.
            for filename, src_content, stale_content in file_entries:
                (templates_dir / filename).write_bytes(src_content)
                (context_dir / filename).write_bytes(stale_content)

            # Act: run ensure_directory() on the pre-populated dirs
            loader = ContextDirectoryLoader(
                context_dir=context_dir,
                templates_dir=templates_dir,
            )
            loader.ensure_directory()

            # Assert: every file in templates_dir must match in context_dir
            for filename, src_content, _stale in file_entries:
                dest = context_dir / filename
                assert dest.exists(), (
                    f"Built-in file {filename!r} missing from context_dir"
                )
                actual = dest.read_bytes()
                assert actual == src_content, (
                    f"Built-in file {filename!r} has stale content.\n"
                    f"  Expected (source): {src_content!r}\n"
                    f"  Actual (dest):     {actual!r}\n"
                    f"  Bug: ensure_directory() skipped overwrite "
                    f"because dest already existed."
                )
