"""Global session infrastructure registry.

Provides module-level access to the SessionRouter, PromptBuilder,
and LifecycleManager singletons. Initialized once at startup by
``initialize()`` (called from ``main.py`` lifespan).

This replaces the old ``agent_manager`` module-level singleton pattern
with explicit initialization and clear ownership.

Public symbols:

- ``initialize()``       — Create and wire all components (call once)
- ``session_router``     — The SessionRouter singleton
- ``prompt_builder``     — The PromptBuilder singleton
- ``lifecycle_manager``  — The LifecycleManager singleton
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Module-level singletons — set by initialize()
session_router: Optional["SessionRouter"] = None
prompt_builder: Optional["PromptBuilder"] = None
lifecycle_manager: Optional["LifecycleManager"] = None

_initialized = False


def initialize(config: "AppConfigManager") -> None:
    """Create and wire all session infrastructure components.

    Called once from ``main.py`` lifespan after config is loaded.
    Safe to call multiple times — subsequent calls are no-ops.
    """
    global session_router, prompt_builder, lifecycle_manager, _initialized

    if _initialized:
        return

    from .prompt_builder import PromptBuilder
    from .session_router import SessionRouter
    from .lifecycle_manager import LifecycleManager

    prompt_builder = PromptBuilder(config=config)
    session_router = SessionRouter(prompt_builder=prompt_builder, config=config)
    lifecycle_manager = LifecycleManager(router=session_router)

    _initialized = True
    logger.info("Session infrastructure initialized (SessionRouter + PromptBuilder + LifecycleManager)")
