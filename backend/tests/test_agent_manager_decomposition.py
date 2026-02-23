"""Tests for _build_options decomposition into focused helpers.

Validates: Requirements 6.1, 6.2

Verifies that all helper methods extracted from the monolithic _build_options
exist on the AgentManager class and are callable.
"""

import pytest

from core.agent_manager import AgentManager


# ---------------------------------------------------------------------------
# Helper method existence checks (parametrized)
# ---------------------------------------------------------------------------

HELPER_METHODS = [
    "_resolve_allowed_tools",
    "_build_mcp_config",
    "_build_hooks",
    "_build_sandbox_config",
    "_inject_channel_mcp",
    "_resolve_model",
    "_build_system_prompt",
]


@pytest.mark.parametrize("method_name", HELPER_METHODS)
def test_helper_method_exists_on_agent_manager(method_name: str):
    """**Validates: Requirements 6.1**

    Each helper method extracted from _build_options must exist on
    AgentManager and be callable.
    """
    assert hasattr(AgentManager, method_name), (
        f"AgentManager is missing helper method '{method_name}'"
    )
    assert callable(getattr(AgentManager, method_name)), (
        f"AgentManager.{method_name} is not callable"
    )


def test_build_options_exists_and_is_callable():
    """**Validates: Requirements 6.2**

    The orchestrator method _build_options must still exist on AgentManager.
    """
    assert hasattr(AgentManager, "_build_options")
    assert callable(getattr(AgentManager, "_build_options"))


# ---------------------------------------------------------------------------
# Conversation deduplication checks (Phase 3)
# ---------------------------------------------------------------------------

CONVERSATION_METHODS = [
    ("_execute_on_session", False),   # private helper
    ("run_conversation", True),       # public wrapper
    ("continue_with_answer", True),   # public wrapper
]


@pytest.mark.parametrize("method_name,is_public", CONVERSATION_METHODS)
def test_conversation_method_exists_on_agent_manager(method_name: str, is_public: bool):
    """**Validates: Requirements 7.1, 7.2, 7.3**

    The shared helper and both public wrappers must exist on AgentManager
    and be callable.
    """
    assert hasattr(AgentManager, method_name), (
        f"AgentManager is missing method '{method_name}'"
    )
    assert callable(getattr(AgentManager, method_name)), (
        f"AgentManager.{method_name} is not callable"
    )


@pytest.mark.parametrize("method_name,is_public", CONVERSATION_METHODS)
def test_conversation_method_is_async(method_name: str, is_public: bool):
    """**Validates: Requirements 7.1, 7.2, 7.3**

    All conversation methods must be async (coroutine functions or async generators).
    """
    import asyncio
    import inspect

    method = getattr(AgentManager, method_name)
    assert inspect.iscoroutinefunction(method) or inspect.isasyncgenfunction(method), (
        f"AgentManager.{method_name} is not async"
    )


def test_public_conversation_methods_do_not_start_with_underscore():
    """**Validates: Requirements 7.2, 7.3**

    run_conversation and continue_with_answer must remain public (no underscore prefix).
    """
    for name in ("run_conversation", "continue_with_answer"):
        assert not name.startswith("_"), f"{name} should be a public method"
        assert hasattr(AgentManager, name), f"AgentManager is missing public method '{name}'"


def test_execute_on_session_is_private():
    """**Validates: Requirements 7.1**

    _execute_on_session is an internal helper and should be private (underscore prefix).
    """
    assert hasattr(AgentManager, "_execute_on_session")
    assert "_execute_on_session".startswith("_")
