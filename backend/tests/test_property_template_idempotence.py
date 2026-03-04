"""Property-based tests for template idempotence — DEPRECATED.

**Feature: unified-swarm-workspace-cwd, Property 6: Template idempotence**

These tests validated ``AgentSandboxManager.ensure_templates_in_directory()``
which was removed during the SwarmWS restructure.  ``AgentSandboxManager``
no longer exists as a module.

All tests in this module are skipped.

**Validates: Requirements 4.1, 4.2**
"""
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from hypothesis import given, strategies as st, settings, HealthCheck, assume

pytestmark = pytest.mark.skip(
    reason="AgentSandboxManager removed during SwarmWS restructure; "
    "ensure_templates_in_directory() no longer exists"
)


PROPERTY_SETTINGS = settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# Stub — AgentSandboxManager was removed during SwarmWS restructure
TEMPLATE_FILES: list[str] = []

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Strategy: a random subset of template filenames to pre-populate
template_subset = st.frozensets(
    st.sampled_from(TEMPLATE_FILES),
    min_size=0,
    max_size=len(TEMPLATE_FILES),
)

# Strategy: random content for modified template files
modified_content = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=200,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_fake_templates_source(source_dir: Path) -> None:
    """Create a fake templates source directory with all TEMPLATE_FILES."""
    source_dir.mkdir(parents=True, exist_ok=True)
    for filename in TEMPLATE_FILES:
        (source_dir / filename).write_text(f"# Original {filename}\nDefault content.")


def make_manager_with_templates_dir(templates_dir: Path):
    """Create an AgentSandboxManager with _templates_dir pointing to our fake source."""
    manager = AgentSandboxManager.__new__(AgentSandboxManager)
    manager._templates_dir = templates_dir
    return manager


def snapshot_swarmai_dir(workspace_path: Path) -> dict[str, str]:
    """Return a mapping of filename -> content for all files in .swarmai/."""
    swarmai_dir = workspace_path / ".swarmai"
    if not swarmai_dir.exists():
        return {}
    return {
        p.name: p.read_text()
        for p in swarmai_dir.iterdir()
        if p.is_file()
    }


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestTemplateIdempotence:
    """Property 6: Template idempotence.

    **Feature: unified-swarm-workspace-cwd, Property 6: Template idempotence**

    After calling ``ensure_templates_in_directory()``, the ``.swarmai/`` directory
    should contain all expected template files. If a template file already exists
    with modified content, the existing content is preserved (not overwritten).
    Calling the method again does not change the filesystem.

    **Validates: Requirements 4.1, 4.2**
    """

    @given(
        pre_existing=template_subset,
        content_map=st.fixed_dictionaries(
            {f: modified_content for f in TEMPLATE_FILES}
        ),
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_modified_files_preserved_missing_files_created(
        self,
        tmp_path: Path,
        pre_existing: frozenset[str],
        content_map: dict[str, str],
    ):
        """Existing modified templates are preserved; missing templates are created.

        **Validates: Requirements 4.1, 4.2**

        1. Create a workspace with a random subset of templates pre-populated
           with random modified content.
        2. Call ensure_templates_in_directory().
        3. Verify: pre-existing files retain their modified content,
           missing files are created with source content.
        """
        example_dir = tmp_path / str(uuid4())
        workspace_path = example_dir / "SwarmWS"
        workspace_path.mkdir(parents=True, exist_ok=True)

        # Create fake template sources
        templates_source = example_dir / "templates_source"
        create_fake_templates_source(templates_source)

        # Pre-populate some templates with modified content
        swarmai_dir = workspace_path / ".swarmai"
        swarmai_dir.mkdir(parents=True, exist_ok=True)
        for filename in pre_existing:
            (swarmai_dir / filename).write_text(content_map[filename])

        manager = make_manager_with_templates_dir(templates_source)
        manager.ensure_templates_in_directory(workspace_path)

        # Verify: pre-existing files retain modified content
        for filename in pre_existing:
            actual = (swarmai_dir / filename).read_text()
            assert actual == content_map[filename], (
                f"Pre-existing file {filename} was overwritten. "
                f"Expected modified content, got: {actual[:80]}..."
            )

        # Verify: missing files were created
        missing = set(TEMPLATE_FILES) - set(pre_existing)
        for filename in missing:
            dst = swarmai_dir / filename
            assert dst.exists(), f"Missing template {filename} was not created"
            # Content should match the source
            expected = (templates_source / filename).read_text()
            assert dst.read_text() == expected, (
                f"Created template {filename} has unexpected content"
            )

    @given(
        pre_existing=template_subset,
        content_map=st.fixed_dictionaries(
            {f: modified_content for f in TEMPLATE_FILES}
        ),
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_second_call_does_not_change_filesystem(
        self,
        tmp_path: Path,
        pre_existing: frozenset[str],
        content_map: dict[str, str],
    ):
        """Calling ensure_templates_in_directory() twice produces identical state.

        **Validates: Requirements 4.1, 4.2**

        1. Pre-populate random subset with modified content.
        2. Call ensure_templates_in_directory() once, snapshot state.
        3. Call again, snapshot state.
        4. Verify snapshots are identical.
        """
        example_dir = tmp_path / str(uuid4())
        workspace_path = example_dir / "SwarmWS"
        workspace_path.mkdir(parents=True, exist_ok=True)

        templates_source = example_dir / "templates_source"
        create_fake_templates_source(templates_source)

        swarmai_dir = workspace_path / ".swarmai"
        swarmai_dir.mkdir(parents=True, exist_ok=True)
        for filename in pre_existing:
            (swarmai_dir / filename).write_text(content_map[filename])

        manager = make_manager_with_templates_dir(templates_source)

        # First call
        manager.ensure_templates_in_directory(workspace_path)
        state_after_first = snapshot_swarmai_dir(workspace_path)

        # Second call
        manager.ensure_templates_in_directory(workspace_path)
        state_after_second = snapshot_swarmai_dir(workspace_path)

        assert state_after_first == state_after_second, (
            f"Filesystem state changed after second call. "
            f"Diff keys: {set(state_after_first.keys()) ^ set(state_after_second.keys())}"
        )

    @given(pre_existing=template_subset)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_all_templates_present_after_call(
        self,
        tmp_path: Path,
        pre_existing: frozenset[str],
    ):
        """After ensure_templates_in_directory(), all template files exist in .swarmai/.

        **Validates: Requirements 4.1**
        """
        example_dir = tmp_path / str(uuid4())
        workspace_path = example_dir / "SwarmWS"
        workspace_path.mkdir(parents=True, exist_ok=True)

        templates_source = example_dir / "templates_source"
        create_fake_templates_source(templates_source)

        # Pre-populate subset
        swarmai_dir = workspace_path / ".swarmai"
        swarmai_dir.mkdir(parents=True, exist_ok=True)
        for filename in pre_existing:
            (swarmai_dir / filename).write_text(f"Custom content for {filename}")

        manager = make_manager_with_templates_dir(templates_source)
        manager.ensure_templates_in_directory(workspace_path)

        # All template files should exist
        for filename in TEMPLATE_FILES:
            assert (swarmai_dir / filename).exists(), (
                f"Template {filename} should exist after ensure_templates_in_directory()"
            )
