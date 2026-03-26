"""Property-based tests for cached workspace path.

**Feature: unified-swarm-workspace-cwd, Property 1: Cached path equals expanded default**

Uses Hypothesis to verify that ``InitializationManager.get_cached_workspace_path()``
returns ``expand_path(DEFAULT_WORKSPACE_CONFIG["file_path"])`` — the expanded form
of ``{app_data_dir}/SwarmWS``.

**Validates: Requirements 1.1, 1.2, 2.1**
"""
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import given, strategies as st, settings, HealthCheck

from core.initialization_manager import InitializationManager
from core.swarm_workspace_manager import SwarmWorkspaceManager
from tests.helpers import PROPERTY_SETTINGS





# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Safe path segment characters
_safe_path_chars = st.sampled_from(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789_-"
)

safe_path_segment = st.text(
    alphabet=_safe_path_chars,
    min_size=1,
    max_size=20,
)

# Strategy for random app_data_dir paths: /tmp/<seg1>/<seg2>/...
random_app_data_dir = st.lists(
    safe_path_segment,
    min_size=1,
    max_size=4,
).map(lambda parts: "/" + "/".join(parts))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_fake_expand(app_data_dir: str):
    """Create a fake expand_path that substitutes {app_data_dir} with the given path."""
    def fake_expand(file_path: str) -> str:
        return file_path.replace("{app_data_dir}", app_data_dir)
    return fake_expand


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestCachedPathEqualsExpandedDefault:
    """Property 1: Cached path equals expanded default workspace path.

    **Validates: Requirements 1.1, 1.2, 2.1**
    """

    @given(app_data_dir=random_app_data_dir)
    @PROPERTY_SETTINGS
    def test_cached_path_equals_expanded_default_after_init(
        self, app_data_dir: str
    ):
        """After init sets _cached_workspace_path, get_cached_workspace_path()
        returns the same value as expand_path(DEFAULT_WORKSPACE_CONFIG["file_path"]).

        **Validates: Requirements 1.1, 1.2**
        """
        manager = InitializationManager()
        swm = SwarmWorkspaceManager()
        fake_expand = make_fake_expand(app_data_dir)

        expected = fake_expand(swm.DEFAULT_WORKSPACE_CONFIG["file_path"])

        # Simulate what run_full_initialization does: cache the expanded path
        manager._cached_workspace_path = expected

        result = manager.get_cached_workspace_path()

        assert result == expected, (
            f"Cached path should equal expanded default. "
            f"Expected: {expected}, Got: {result}"
        )

    @given(app_data_dir=random_app_data_dir)
    @PROPERTY_SETTINGS
    def test_fallback_computes_from_default_config(
        self, app_data_dir: str
    ):
        """When _cached_workspace_path is None, get_cached_workspace_path()
        computes the path from DEFAULT_WORKSPACE_CONFIG and returns the
        same value as expand_path(DEFAULT_WORKSPACE_CONFIG["file_path"]).

        **Validates: Requirements 1.1, 1.2, 2.1**
        """
        manager = InitializationManager()
        swm = SwarmWorkspaceManager()
        fake_expand = make_fake_expand(app_data_dir)

        # Ensure cached path is not set (fallback scenario)
        manager._cached_workspace_path = None

        expected = fake_expand(swm.DEFAULT_WORKSPACE_CONFIG["file_path"])

        with patch("config.get_app_data_dir", return_value=Path(app_data_dir)):
            result = manager.get_cached_workspace_path()

        assert result == expected, (
            f"Fallback path should equal expanded default. "
            f"Expected: {expected}, Got: {result}"
        )

    @given(app_data_dir=random_app_data_dir)
    @PROPERTY_SETTINGS
    def test_path_ends_with_swarmws(self, app_data_dir: str):
        """The cached workspace path always ends with '/SwarmWS'.

        **Validates: Requirements 2.1**
        """
        manager = InitializationManager()
        manager._cached_workspace_path = None

        with patch("config.get_app_data_dir", return_value=Path(app_data_dir)):
            result = manager.get_cached_workspace_path()

        assert result.endswith("/SwarmWS"), (
            f"Cached path should end with '/SwarmWS', got: {result}"
        )

    @given(app_data_dir=random_app_data_dir)
    @PROPERTY_SETTINGS
    def test_path_contains_app_data_dir(self, app_data_dir: str):
        """The cached workspace path contains the app_data_dir prefix.

        **Validates: Requirements 1.1, 2.1**
        """
        manager = InitializationManager()
        manager._cached_workspace_path = None

        with patch("config.get_app_data_dir", return_value=Path(app_data_dir)):
            result = manager.get_cached_workspace_path()

        # Normalize: Path resolves trailing slashes etc.
        normalized_app_dir = str(Path(app_data_dir))
        assert result.startswith(normalized_app_dir), (
            f"Cached path should start with app_data_dir '{normalized_app_dir}', "
            f"got: {result}"
        )
