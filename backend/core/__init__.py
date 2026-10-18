"""Core business logic modules.

The multi-session architecture (SessionUnit, SessionRouter, PromptBuilder,
LifecycleManager) is the active code path for all chat endpoints.
``session_registry`` provides module-level access to the singletons.

The package-level names below load LAZILY (PEP 562 ``__getattr__``). Importing any
``core.<submodule>`` runs this file first, and the old eager imports pulled
session_manager → database → config → pydantic_settings (~150 ms) into every
process that touched one submodule — e.g. each ``scripts/artifact_cli.py`` call, which
the pipeline runs hundreds of times per run and its tests spawn thousands of times per
test run. ``from core import SessionUnit`` / ``core.SessionRouter`` still work; the
module is imported on first access. ``from core import <submodule>`` is unaffected
(an unknown name raises AttributeError, so the import system loads the submodule).

``session_manager`` is no longer re-exported: the name is both a submodule and the
instance inside it, and the eager re-export made ``core.session_manager`` mean the
instance or the module depending on import order. No caller used the package-level
name; import the instance as ``from core.session_manager import session_manager``.

Key symbols: ``SessionManager``, ``SystemPromptBuilder``,
``SessionState``, ``SessionUnit``, ``SessionRouter``, ``PromptBuilder``,
``LifecycleManager``, ``_is_retriable_error``, ``_sanitize_sdk_error``,
``_build_error_event``.
"""
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # static names for type checkers and PyInstaller's import scan
    from .lifecycle_manager import LifecycleManager
    from .prompt_builder import PromptBuilder
    from .session_manager import SessionManager
    from .session_router import SessionRouter
    from .session_unit import SessionState, SessionUnit
    from .session_utils import _build_error_event, _is_retriable_error, _sanitize_sdk_error
    from .system_prompt import SystemPromptBuilder

# exported name → submodule that defines it
_LAZY = {
    "SessionManager": "session_manager",
    "SystemPromptBuilder": "system_prompt",
    "SessionState": "session_unit",
    "SessionUnit": "session_unit",
    "SessionRouter": "session_router",
    "PromptBuilder": "prompt_builder",
    "LifecycleManager": "lifecycle_manager",
    "_is_retriable_error": "session_utils",
    "_sanitize_sdk_error": "session_utils",
    "_build_error_event": "session_utils",
}

__all__ = [
    "SessionManager",
    "SystemPromptBuilder",
    # New architecture
    "SessionState",
    "SessionUnit",
    "SessionRouter",
    "PromptBuilder",
    "LifecycleManager",
]


def __getattr__(name: str):
    sub = _LAZY.get(name)
    if sub is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{sub}", __name__), name)
    globals()[name] = value  # later lookups skip __getattr__
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY))
