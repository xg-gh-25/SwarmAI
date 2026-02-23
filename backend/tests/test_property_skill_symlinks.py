"""Property-based tests for skill symlink management.

**Feature: unified-swarm-workspace-cwd, Property 4: Skill symlink set equals all available skills**

Uses Hypothesis to verify that ``AgentSandboxManager.setup_workspace_skills()``
creates symlinks that exactly match the set of all available skill names.

**Validates: Requirements 3.2, 3.4**
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from hypothesis import given, strategies as st, settings, HealthCheck, assume

from core.agent_sandbox_manager import AgentSandboxManager


PROPERTY_SETTINGS = settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Safe skill name characters (lowercase alphanumeric, hyphens, underscores)
_skill_name_chars = st.sampled_from(
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789_-"
)

skill_name = st.text(
    alphabet=_skill_name_chars,
    min_size=1,
    max_size=20,
).filter(lambda n: n not in (".", "..") and not n.startswith(".") and not n.startswith("-"))

# Strategy for a set of unique skill names
skill_name_set = st.frozensets(skill_name, min_size=0, max_size=15)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_fake_skill_sources(root: Path, names: frozenset[str]) -> dict[str, Path]:
    """Create fake skill source directories with SKILL.md files.

    Returns a mapping of skill_name -> source_path.
    """
    sources = {}
    for name in names:
        skill_dir = root / "skill_sources" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(f"# {name}\nFake skill for testing.")
        sources[name] = skill_dir
    return sources


def get_symlink_names(workspace_path: Path) -> set[str]:
    """Return the set of symlink names in .claude/skills/."""
    skills_dir = workspace_path / ".claude" / "skills"
    if not skills_dir.exists():
        return set()
    return {p.name for p in skills_dir.iterdir() if p.is_symlink()}


def snapshot_symlink_state(workspace_path: Path) -> dict[str, str]:
    """Return a mapping of symlink_name -> resolved_target for .claude/skills/.

    Used to capture the full filesystem state for idempotence comparison.
    """
    skills_dir = workspace_path / ".claude" / "skills"
    if not skills_dir.exists():
        return {}
    return {
        p.name: str(p.resolve())
        for p in skills_dir.iterdir()
        if p.is_symlink()
    }


def make_manager_with_mocks(
    skill_names: frozenset[str],
    skill_sources: dict[str, Path],
) -> AgentSandboxManager:
    """Create an AgentSandboxManager with mocked skill lookup methods.

    Mocks:
    - get_all_skill_names() -> returns the given skill names
    - _get_skill_by_name(name) -> returns a fake record with local_path
    - _get_skill_source_path(name, record) -> returns the source path
    """
    manager = AgentSandboxManager.__new__(AgentSandboxManager)

    async def mock_get_all_skill_names():
        return list(skill_names)

    async def mock_get_skill_by_name(name):
        if name in skill_sources:
            return {"name": name, "local_path": str(skill_sources[name])}
        return None

    def mock_get_skill_source_path(name, record=None):
        return skill_sources.get(name)

    manager.get_all_skill_names = mock_get_all_skill_names
    manager._get_skill_by_name = mock_get_skill_by_name
    manager._get_skill_source_path = mock_get_skill_source_path

    return manager


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestSkillSymlinkSetEqualsAllAvailableSkills:
    """Property 4: Skill symlink set equals all available skills.

    **Validates: Requirements 3.2, 3.4**
    """

    @given(names=skill_name_set)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_symlinks_match_available_skills(
        self, tmp_path: Path, names: frozenset[str],
    ):
        """After setup_workspace_skills(), symlink names equal all available skill names.

        **Validates: Requirements 3.2, 3.4**

        Generate random sets of skill names, create fake source files,
        call setup_workspace_skills(), verify symlink names match exactly.
        """
        example_dir = tmp_path / str(uuid4())
        workspace_path = example_dir / "SwarmWS"
        workspace_path.mkdir(parents=True, exist_ok=True)

        # Create fake skill source files
        skill_sources = create_fake_skill_sources(example_dir, names)

        manager = make_manager_with_mocks(names, skill_sources)
        await manager.setup_workspace_skills(workspace_path)

        actual_symlinks = get_symlink_names(workspace_path)
        assert actual_symlinks == set(names), (
            f"Symlink set mismatch. Expected: {set(names)}, Got: {actual_symlinks}"
        )

    @given(names=skill_name_set)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_symlinks_point_to_correct_sources(
        self, tmp_path: Path, names: frozenset[str],
    ):
        """Each symlink points to the resolved source path of its skill.

        **Validates: Requirements 3.2, 3.4**
        """
        assume(len(names) > 0)

        example_dir = tmp_path / str(uuid4())
        workspace_path = example_dir / "SwarmWS"
        workspace_path.mkdir(parents=True, exist_ok=True)

        skill_sources = create_fake_skill_sources(example_dir, names)

        manager = make_manager_with_mocks(names, skill_sources)
        await manager.setup_workspace_skills(workspace_path)

        skills_dir = workspace_path / ".claude" / "skills"
        for name in names:
            link = skills_dir / name
            assert link.is_symlink(), f"{name} should be a symlink"
            assert link.resolve() == skill_sources[name].resolve(), (
                f"Symlink {name} points to {link.resolve()}, "
                f"expected {skill_sources[name].resolve()}"
            )

    @given(
        initial_names=skill_name_set,
        final_names=skill_name_set,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_stale_symlinks_removed(
        self,
        tmp_path: Path,
        initial_names: frozenset[str],
        final_names: frozenset[str],
    ):
        """Stale symlinks from a previous skill set are removed after re-sync.

        **Validates: Requirements 3.2, 3.4**

        Setup with initial skill set, then re-sync with a different set.
        Verify only the final set's symlinks remain.
        """
        example_dir = tmp_path / str(uuid4())
        workspace_path = example_dir / "SwarmWS"
        workspace_path.mkdir(parents=True, exist_ok=True)

        # First pass: setup with initial skills
        all_names = initial_names | final_names
        all_sources = create_fake_skill_sources(example_dir, all_names)

        initial_sources = {n: all_sources[n] for n in initial_names}
        manager1 = make_manager_with_mocks(initial_names, initial_sources)
        await manager1.setup_workspace_skills(workspace_path)

        # Second pass: re-sync with final skills
        final_sources = {n: all_sources[n] for n in final_names}
        manager2 = make_manager_with_mocks(final_names, final_sources)
        await manager2.setup_workspace_skills(workspace_path)

        actual_symlinks = get_symlink_names(workspace_path)
        assert actual_symlinks == set(final_names), (
            f"After re-sync, symlinks should match final set. "
            f"Expected: {set(final_names)}, Got: {actual_symlinks}"
        )


class TestSkillSymlinkIdempotence:
    """Property 5: Skill symlink idempotence.

    **Feature: unified-swarm-workspace-cwd, Property 5: Skill symlink idempotence**

    Calling ``setup_workspace_skills()`` twice in succession with the same
    skill set should produce the same filesystem state as calling it once.
    The second call is a no-op.

    **Validates: Requirements 3.2, 3.4**
    """

    @given(names=skill_name_set)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_second_call_is_noop(
        self, tmp_path: Path, names: frozenset[str],
    ):
        """Filesystem state is identical after calling setup_workspace_skills() twice.

        **Validates: Requirements 3.2, 3.4**

        Generate random skill sets, call setup_workspace_skills() once,
        snapshot the filesystem state (symlink names + targets), call it
        again with the same skill set, verify the snapshot is identical.
        """
        example_dir = tmp_path / str(uuid4())
        workspace_path = example_dir / "SwarmWS"
        workspace_path.mkdir(parents=True, exist_ok=True)

        # Create fake skill source files
        skill_sources = create_fake_skill_sources(example_dir, names)

        manager = make_manager_with_mocks(names, skill_sources)

        # First call
        await manager.setup_workspace_skills(workspace_path)
        state_after_first = snapshot_symlink_state(workspace_path)

        # Second call (should be a no-op)
        await manager.setup_workspace_skills(workspace_path)
        state_after_second = snapshot_symlink_state(workspace_path)

        assert state_after_first == state_after_second, (
            f"Filesystem state changed after second call. "
            f"First: {state_after_first}, Second: {state_after_second}"
        )

    @given(names=skill_name_set)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_symlink_count_stable_after_repeated_calls(
        self, tmp_path: Path, names: frozenset[str],
    ):
        """The number of symlinks does not change across repeated calls.

        **Validates: Requirements 3.2, 3.4**

        Ensures no duplicate or phantom symlinks are created by repeated
        invocations.
        """
        example_dir = tmp_path / str(uuid4())
        workspace_path = example_dir / "SwarmWS"
        workspace_path.mkdir(parents=True, exist_ok=True)

        skill_sources = create_fake_skill_sources(example_dir, names)
        manager = make_manager_with_mocks(names, skill_sources)

        # Call setup three times
        await manager.setup_workspace_skills(workspace_path)
        count_first = len(get_symlink_names(workspace_path))

        await manager.setup_workspace_skills(workspace_path)
        count_second = len(get_symlink_names(workspace_path))

        assert count_first == count_second == len(names), (
            f"Symlink count should remain {len(names)} across calls. "
            f"First: {count_first}, Second: {count_second}"
        )



class TestSkillEdgeCases:
    """Unit tests for skill symlink edge cases.

    **Validates: Requirements 3.4, 3.5**
    """

    @pytest.mark.asyncio
    async def test_empty_skill_set_removes_all_symlinks(self, tmp_path: Path):
        """An empty skill set removes all existing symlinks.

        **Validates: Requirements 3.4**

        Create some symlinks manually, then call setup_workspace_skills()
        with an empty skill set. Verify all symlinks are removed.
        """
        workspace_path = tmp_path / "SwarmWS"
        skills_dir = workspace_path / ".claude" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        # Create fake source files and symlinks manually
        pre_existing_names = frozenset({"alpha", "beta", "gamma"})
        skill_sources = create_fake_skill_sources(tmp_path, pre_existing_names)
        for name in pre_existing_names:
            (skills_dir / name).symlink_to(skill_sources[name].resolve())

        # Verify symlinks exist before the call
        assert get_symlink_names(workspace_path) == set(pre_existing_names)

        # Call with empty skill set
        manager = make_manager_with_mocks(frozenset(), {})
        await manager.setup_workspace_skills(workspace_path)

        # All symlinks should be removed
        assert get_symlink_names(workspace_path) == set(), (
            "All symlinks should be removed when skill set is empty"
        )

    @pytest.mark.asyncio
    async def test_missing_source_logs_warning_others_still_linked(
        self, tmp_path: Path, caplog,
    ):
        """A missing skill source file logs a warning; other skills are still linked.

        **Validates: Requirements 3.4, 3.5**

        Set up skills where one has a valid source and another doesn't.
        Call setup_workspace_skills(). Verify the valid one is linked and
        a warning is logged for the missing one.
        """
        import logging

        workspace_path = tmp_path / "SwarmWS"
        workspace_path.mkdir(parents=True, exist_ok=True)

        # Create a real source for "good_skill" only
        good_sources = create_fake_skill_sources(tmp_path, frozenset({"good_skill"}))

        # "bad_skill" has no source on disk
        all_names = frozenset({"good_skill", "bad_skill"})

        # The manager returns both names but only good_skill has a source path
        manager = make_manager_with_mocks(all_names, good_sources)

        with caplog.at_level(logging.WARNING):
            await manager.setup_workspace_skills(workspace_path)

        # good_skill should be symlinked
        actual = get_symlink_names(workspace_path)
        assert "good_skill" in actual, "good_skill should be symlinked"

        # bad_skill should NOT be symlinked
        assert "bad_skill" not in actual, "bad_skill should not be symlinked"

        # A warning should have been logged for the missing source
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("bad_skill" in msg for msg in warning_messages), (
            f"Expected a warning about bad_skill. Warnings logged: {warning_messages}"
        )

