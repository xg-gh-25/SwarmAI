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


def _read_owner_pid_sync(pid: int) -> int | None:
    """Read SWARMAI_OWNER_PID from a process's environment (sync).

    Delegates to ``session_utils.read_owner_pid()`` — single source of truth.
    """
    from .session_utils import read_owner_pid
    return read_owner_pid(pid)


def kill_all_claude_processes() -> int:
    """Kill SwarmAI's own leftover claude CLI processes at startup.

    Uses ``pgrep -f "claude_agent_sdk/_bundled/claude"`` to match ONLY
    the bundled Claude CLI from SwarmAI's PyInstaller package.  This
    avoids killing the user's own ``claude`` processes (e.g., Claude
    Code CLI in terminal, Kiro's claude subprocess, or any other app
    that spawns a process named ``claude``).

    Called at **startup** — leftover processes from a crashed previous
    SwarmAI instance are guaranteed stale.

    COE 2026-03-15: At startup, leftover processes from a crashed previous
    instance are guaranteed stale.  Kill them all.

    Returns the number of processes killed.
    """
    if platform.system() not in ("Darwin", "Linux"):
        return 0

    killed = 0
    my_pid = os.getpid()
    try:
        # Match ONLY SwarmAI's bundled claude binary — NOT user's claude CLI.
        # At startup, kill processes owned by a PREVIOUS backend instance
        # (SWARMAI_OWNER_PID set but owner PID is dead).  Processes owned
        # by the CURRENT instance (shouldn't exist at startup) are skipped.
        # Processes without the tag are skipped (not SwarmAI-managed).
        result = subprocess.run(
            ["pgrep", "-f", "claude_agent_sdk/_bundled/claude"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return 0

        pids = [int(p) for p in result.stdout.strip().split("\n") if p.strip()]
        for pid in pids:
            if pid == my_pid:
                continue
            try:
                # Ownership check: only kill processes from a previous
                # backend instance.  Read SWARMAI_OWNER_PID from the
                # process's environment.
                owner_pid = _read_owner_pid_sync(pid)
                if owner_pid is not None and owner_pid == my_pid:
                    continue  # Our own child (shouldn't exist at startup, but safe)
                if owner_pid is not None:
                    # Has ownership tag — check if owner is alive
                    try:
                        os.kill(owner_pid, 0)
                        continue  # Owner is alive — not an orphan
                    except ProcessLookupError:
                        pass  # Owner dead — proceed to kill
                    except PermissionError:
                        continue  # Can't check — assume alive
                # owner_pid is None (no tag) at startup = legacy process
                # from before ownership model.  Kill it (startup guarantee).

                from core.session_unit import _snapshot_descendant_tree, _kill_pids
                tree = _snapshot_descendant_tree(pid)
                tree_killed = _kill_pids(tree)
                os.kill(pid, signal.SIGKILL)
                killed += 1
                logger.info(
                    "Startup: killed leftover SwarmAI claude pid=%d "
                    "(+%d descendants, owner=%s)", pid, tree_killed,
                    owner_pid,
                )
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
            "Startup: killed %d leftover SwarmAI claude process(es)",
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


async def start_lifecycle() -> None:
    """Start the LifecycleManager background loop.

    Called from ``main.py`` lifespan after hooks are configured.
    Safe to call multiple times (idempotent).
    """
    if lifecycle_manager is not None:
        await lifecycle_manager.start()
    logger.info("Lifecycle manager started via session_registry")


async def stop_lifecycle() -> None:
    """Stop the LifecycleManager background loop and drain hooks.

    Called from ``main.py`` lifespan shutdown before ``disconnect_all()``.
    """
    if lifecycle_manager is not None:
        await lifecycle_manager.stop()
    logger.info("Lifecycle manager stopped via session_registry")


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
