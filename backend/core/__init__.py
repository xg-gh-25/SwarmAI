"""Core business logic modules."""
from .agent_manager import AgentManager, agent_manager
from .session_manager import SessionManager, session_manager
from .system_prompt import SystemPromptBuilder

# New multi-session architecture modules (Phase 1 extraction)
from .session_unit import SessionState, SessionUnit
from .session_router import SessionRouter
from .prompt_builder import PromptBuilder
from .lifecycle_manager import LifecycleManager
from .session_utils import _is_retriable_error, _sanitize_sdk_error, _build_error_event

__all__ = [
    # Legacy (AgentManager still active during Phase 1)
    "AgentManager",
    "agent_manager",
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
