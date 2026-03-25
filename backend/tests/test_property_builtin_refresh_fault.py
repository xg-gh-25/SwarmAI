"""Bug condition exploration test for ContextDirectoryLoader.ensure_directory().

This module verifies that system-default context files (``user_customized=False``)
are always refreshed from templates on startup.  Originally written for bugfix
requirement 1.1, now updated for the two-mode copy architecture where
``ensure_directory()`` only iterates ``CONTEXT_FILES`` entries.

Testing methodology:
    Property-based testing with Hypothesis.  Random file content pairs (source
    vs. stale destination) are generated for system-default filenames from
    ``CONTEXT_FILES`` to verify that ``ensure_directory()`` always overwrites
    stale system files with the template content.

Key property verified:
    **Property 1 — System-default context files always match source on startup.**
    For every ``ContextFileSpec`` in ``CONTEXT_FILES`` with
    ``user_customized=False``, after calling ``ensure_directory()``, the
    corresponding file in ``context_dir`` SHALL have content identical to
    the template source.

Validates: Requirements 1.1, 2.1, 10.5, 14.3
"""

import os
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from core.context_directory_loader import (
    CONTEXT_FILES,
    ContextDirectoryLoader,
)


# ── Hypothesis strategies ──────────────────────────────────────────────

# System-default filenames from CONTEXT_FILES (user_customized=False)
_system_filenames = [
    spec.filename for spec in CONTEXT_FILES if not spec.user_customized
]

# Generate non-empty file content (1-200 bytes)
_file_content = st.binary(min_size=1, max_size=200)

# A single file entry: (filename, source_content, stale_dest_content)
# where source and stale content are guaranteed to differ.
# Uses actual system-default filenames from CONTEXT_FILES.
_file_entry = st.tuples(
    st.sampled_from(_system_filenames),
    _file_content,
    _file_content,
).filter(lambda t: t[1] != t[2])

# A list of 1-4 file entries with unique filenames (max = len of system files)
_file_entries = st.lists(
    _file_entry,
    min_size=1,
    max_size=min(4, len(_system_filenames)),
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
