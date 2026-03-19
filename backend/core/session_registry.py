"""Global session infrastructure registry.

Provides module-level access to the SessionRouter, PromptBuilder,
LifecycleManager, and hook infrastructure singletons.  Initialized once
at startup by ``initialize()`` (called from ``main.py`` lifespan).

This replaces the old ``agent_manager`` module-level singleton pattern
with explicit initialization and clear ownership.

Public symbols:

- ``initialize()``                — Create and wire all components (call once)
- ``configure_hooks()``           — Wire lifecycle hooks after initialize()
- ``kill_all_claude_processes()`` — Startup cleanup of leftover processes
- ``disconnect_all()``            — Graceful shutdown of all sessions
- ``run_skill_creator()``         — AI-powered skill generation via SessionRouter
- ``session_router``              — The SessionRouter singleton
- ``prompt_builder``              — The PromptBuilder singleton
- ``lifecycle_manager``           — The LifecycleManager singleton
- ``hook_executor``               — The BackgroundHookExecutor singleton
- ``system_prompt_metadata``      — Per-session prompt metadata for TSCC viewer
"""
from __future__ import annotations

import logging
import os
import platform
import signal
import subprocess
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .session_router import SessionRouter
    from .prompt_builder import PromptBuilder
    from .lifecycle_manager import LifecycleManager
    from .session_hooks import BackgroundHookExecutor, SessionLifecycleHookManager
    from .app_config_manager import AppConfigManager

logger = logging.getLogger(__name__)

# ── Module-level singletons (set by initialize / configure_hooks) ──
session_router: Optional[SessionRouter] = None
prompt_builder: Optional[PromptBuilder] = None
lifecycle_manager: Optional[LifecycleManager] = None
hook_executor: Optional[BackgroundHookExecutor] = None
hook_manager: Optional[SessionLifecycleHookManager] = None

# Per-session system prompt metadata, keyed by session_id.
# Populated by SessionRouter after PromptBuilder runs, read by TSCC API.
# Cleaned up by delete_session and LifecycleManager TTL kill.
system_prompt_metadata: dict[str, dict] = {}

_initialized = False


# ── Initialization ────────────────────────────────────────────────

def initialize(config: AppConfigManager) -> None:
    """Create and wire all session infrastructure components.

    Called once from ``main.py`` lifespan after config is loaded.
    Safe to call multiple times — subsequent calls are no-ops.
    """
    global session_router, prompt_builder, lifecycle_manager, _initialized

    if _initialized:
        return

    from .prompt_builder import PromptBuilder as _PB
    from .session_router import SessionRouter as _SR
    from .lifecycle_manager import LifecycleManager as _LM

    prompt_builder = _PB(config=config)
    session_router = _SR(prompt_builder=prompt_builder, config=config)
    lifecycle_manager = _LM(router=session_router)

    # Wire lifecycle_manager back into router for eviction/shutdown hooks
    session_router._lifecycle_manager = lifecycle_manager

    _initialized = True
    logger.info(
        "Session infrastructure initialized "
        "(SessionRouter + PromptBuilder + LifecycleManager)"
    )


def configure_hooks(
    executor: BackgroundHookExecutor,
    manager: SessionLifecycleHookManager,
) -> None:
    """Wire lifecycle hook infrastructure after initialize().

    Called from ``main.py`` lifespan after hooks are registered.
    Must be called AFTER ``initialize()`` so ``lifecycle_manager`` exists.
    """
    global hook_executor, hook_manager

    hook_executor = executor
    hook_manager = manager

    # Wire executor into LifecycleManager so TTL kills fire hooks
    if lifecycle_manager is not None:
        lifecycle_manager._hook_executor = executor

    logger.info("Session hooks configured in registry")


# ── Startup / Shutdown ────────────────────────────────────────────

def kill_all_claude_processes() -> int:
    """Kill ALL claude CLI processes unconditionally.

    Called at **startup** — no claude processes should be running before
    the backend starts.  More aggressive than orphan reaping which only
    kills orphans/our-children.

    COE 2026-03-15: At startup, leftover processes from a crashed previous
    instance are guaranteed stale.  Kill them all.

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


# ── Skill Creator (delegates to skill_creator module) ─────────────

async def run_skill_creator(
    skill_name: str,
    skill_description: str,
    user_message: Optional[str] = None,
    session_id: Optional[str] = None,
    model: Optional[str] = None,
):
    """Run a skill creation conversation via SessionRouter.

    Thin wrapper that delegates to ``skill_creator.run_skill_creator()``.
    Yields SSE event dicts.
    """
    from .session_utils import _build_error_event

    if session_router is None:
        yield _build_error_event(
            code="NOT_INITIALIZED",
            message="Session infrastructure not initialized yet.",
            suggested_action="Please wait for the backend to finish starting up.",
        )
        return

    from .skill_creator import run_skill_creator as _run

    async for event in _run(
        router=session_router,
        skill_name=skill_name,
        skill_description=skill_description,
        user_message=user_message,
        session_id=session_id,
        model=model,
    ):
        yield event
