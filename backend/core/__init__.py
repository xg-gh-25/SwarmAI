"""Core business logic modules.

The multi-session architecture (SessionUnit, SessionRouter, PromptBuilder,
LifecycleManager) is the active code path for all chat endpoints.
``session_registry`` provides module-level access to the singletons.
"""
from .session_manager import SessionManager, session_manager
from .system_prompt import SystemPromptBuilder

# New multi-session architecture modules
from .session_unit import SessionState, SessionUnit
from .session_router import SessionRouter
from .prompt_builder import PromptBuilder
from .lifecycle_manager import LifecycleManager
from .session_utils import _is_retriable_error, _sanitize_sdk_error, _build_error_event

__all__ = [
    "SessionManager",
    "session_manager",
    "SystemPromptBuilder",
    # New architecture
    "SessionState",
    "SessionUnit",
    "SessionRouter",
    "PromptBuilder",
    "LifecycleManager",
]
