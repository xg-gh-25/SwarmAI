"""Global session infrastructure registry.

Provides module-level access to the SessionRouter, PromptBuilder,
LifecycleManager, and hook infrastructure singletons. Initialized once
at startup by ``initialize()`` (called from ``main.py`` lifespan).

This replaces the old ``agent_manager`` module-level singleton pattern
with explicit initialization and clear ownership.

Public symbols:

- ``initialize()``            — Create and wire all components (call once)
- ``configure_hooks()``       — Wire lifecycle hooks after initialize()
- ``kill_all_claude_processes()`` — Startup cleanup of leftover processes
- ``disconnect_all()``        — Graceful shutdown of all sessions
- ``session_router``          — The SessionRouter singleton
- ``prompt_builder``          — The PromptBuilder singleton
- ``lifecycle_manager``       — The LifecycleManager singleton
- ``hook_executor``           — The BackgroundHookExecutor singleton
- ``system_prompt_metadata``  — Per-session prompt metadata for TSCC viewer
"""
from __future__ import annotations

import logging
import os
import platform
import signal
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

# Module-level singletons — set by initialize()
session_router: Optional["SessionRouter"] = None
prompt_builder: Optional["PromptBuilder"] = None
lifecycle_manager: Optional["LifecycleManager"] = None
hook_executor: Optional["BackgroundHookExecutor"] = None
hook_manager: Optional["SessionLifecycleHookManager"] = None

# Per-session system prompt metadata, keyed by session_id.
# Populated by PromptBuilder and read by the TSCC API endpoint.
system_prompt_metadata: dict[str, dict] = {}

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


def configure_hooks(
    executor: "BackgroundHookExecutor",
    manager: "SessionLifecycleHookManager",
) -> None:
    """Wire lifecycle hook infrastructure after initialize().

    Called from ``main.py`` lifespan after hooks are registered.
    """
    global hook_executor, hook_manager

    hook_executor = executor
    hook_manager = manager

    # Wire executor into LifecycleManager so TTL kills fire hooks
    if lifecycle_manager is not None:
        lifecycle_manager._hook_executor = executor

    logger.info("Session hooks configured in registry")


def kill_all_claude_processes() -> int:
    """Kill ALL claude CLI processes unconditionally.

    Called at **startup** — no claude processes should be running before
    the backend starts. This is more aggressive than orphan reaping
    which only kills orphans/our-children.

    COE 2026-03-15: At startup, leftover processes from a crashed previous
    instance are guaranteed stale. Kill them all.

    Returns the number of processes killed.
    """
    if platform.system() not in ("Darwin", "Linux"):
        return 0

    killed = 0
    try:
        result = subprocess.run(
            ["pgrep", "-x", "claude"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return 0

        pids = [int(p) for p in result.stdout.strip().split("\n") if p.strip()]
        for pid in pids:
            try:
                # Kill children first
                try:
                    subprocess.run(
                        ["pkill", "-9", "-P", str(pid)],
                        capture_output=True, timeout=3,
                    )
                except Exception:
                    pass
                os.kill(pid, signal.SIGKILL)
                killed += 1
                logger.info("Startup: killed leftover claude process pid=%d", pid)
            except (ProcessLookupError, PermissionError):
                pass
            except Exception as exc:
                logger.debug("Could not kill claude pid %d: %s", pid, exc)
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("Startup claude cleanup failed: %s", exc)

    if killed:
        logger.warning(
            "Startup: killed %d leftover claude process(es) from previous instance",
            killed,
        )
    return killed


async def disconnect_all() -> None:
    """Graceful shutdown: kill all alive SessionUnits.

    Called from ``main.py`` lifespan shutdown and ``/shutdown`` endpoint.
    """
    if session_router is not None:
        await session_router.disconnect_all()
    logger.info("All sessions disconnected via session_registry")
