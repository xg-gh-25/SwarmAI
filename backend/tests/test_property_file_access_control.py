"""Property-based tests for file access control determined by workspace mode.

**Feature: unified-swarm-workspace-cwd, Property 7: File access control determined by workspace mode**

Uses Hypothesis to verify that:
- ``global_user_mode=True`` produces ``can_use_tool=None`` (no file access control)
- ``global_user_mode=False`` produces ``can_use_tool`` as a callable handler

**Validates: Requirements 7.1, 7.2, 7.3**
"""
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import given, strategies as st, settings, HealthCheck

from core.security_hooks import create_file_access_permission_handler
from tests.helpers import PROPERTY_SETTINGS





# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_safe_chars = st.sampled_from(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789_-"
)

safe_segment = st.text(alphabet=_safe_chars, min_size=1, max_size=12)

random_workspace_path = st.lists(
    safe_segment, min_size=1, max_size=3,
).map(lambda parts: "/" + "/".join(parts) + "/SwarmWS")

agent_config_strategy = st.fixed_dictionaries({
    "id": st.text(alphabet=_safe_chars, min_size=1, max_size=10),
    "global_user_mode": st.booleans(),
    "allowed_directories": st.lists(
        st.text(alphabet=_safe_chars, min_size=1, max_size=20).map(lambda s: f"/extra/{s}"),
        min_size=0,
        max_size=3,
    ),
})


# ---------------------------------------------------------------------------
# Helper: replicate the inlined logic from _build_options step 4
# ---------------------------------------------------------------------------

def build_file_access_handler(agent_config: dict, working_directory: str):
    """Replicate the file access handler logic from _build_options."""
    global_user_mode = agent_config.get("global_user_mode", True)

    if global_user_mode:
        return None
    else:
        allowed_directories = [working_directory]
        extra_dirs = agent_config.get("allowed_directories", [])
        if extra_dirs:
            allowed_directories.extend(extra_dirs)
        return create_file_access_permission_handler(allowed_directories)


# ---------------------------------------------------------------------------
# Property 7: File access control determined by workspace mode
# ---------------------------------------------------------------------------


class TestFileAccessControlDeterminedByWorkspaceMode:
    """Property 7: File access control determined by workspace mode.

    For global_user_mode=True, can_use_tool is None.
    For global_user_mode=False, can_use_tool is a callable handler.

    **Validates: Requirements 7.1, 7.2, 7.3**
    """

    @given(
        agent_config=agent_config_strategy,
        workspace_path=random_workspace_path,
    )
    @PROPERTY_SETTINGS
    def test_global_mode_has_no_file_access_control(
        self, agent_config: dict, workspace_path: str,
    ):
        """global_user_mode=True produces can_use_tool=None.

        **Validates: Requirement 7.2**
        """
        config = {**agent_config, "global_user_mode": True}
        handler = build_file_access_handler(config, workspace_path)

        assert handler is None, (
            f"global_user_mode=True should produce None handler, got {type(handler)}"
        )

    @given(
        agent_config=agent_config_strategy,
        workspace_path=random_workspace_path,
    )
    @PROPERTY_SETTINGS
    def test_isolated_mode_has_callable_handler(
        self, agent_config: dict, workspace_path: str,
    ):
        """global_user_mode=False produces a callable can_use_tool handler.

        **Validates: Requirement 7.1**
        """
        config = {**agent_config, "global_user_mode": False}
        handler = build_file_access_handler(config, workspace_path)

        assert handler is not None, (
            "global_user_mode=False should produce a non-None handler"
        )
        assert callable(handler), (
            f"Handler should be callable, got {type(handler)}"
        )

    @given(
        agent_config=agent_config_strategy,
        workspace_path=random_workspace_path,
    )
    @PROPERTY_SETTINGS
    def test_handler_type_determined_solely_by_global_user_mode(
        self, agent_config: dict, workspace_path: str,
    ):
        """The handler type (None vs callable) depends only on global_user_mode.

        **Validates: Requirements 7.1, 7.2, 7.3**
        """
        global_mode = agent_config.get("global_user_mode", True)
        handler = build_file_access_handler(agent_config, workspace_path)

        if global_mode:
            assert handler is None, (
                "global_user_mode=True must yield None handler"
            )
        else:
            assert handler is not None and callable(handler), (
                "global_user_mode=False must yield a callable handler"
            )
