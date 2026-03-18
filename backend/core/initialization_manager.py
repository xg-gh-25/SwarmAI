"""Initialization Manager for fast startup optimization.

This module manages application initialization state and flow, implementing
a two-phase initialization strategy:
- First run: Full initialization (scan skills, MCPs, create default resources)
- Subsequent runs: Quick validation (verify resources exist)

Validates: Requirements 1.1, 1.2, 1.3, 2.1, 3.1
"""
import asyncio
import logging
from typing import Callable, TypeVar, Optional

from database import db

logger = logging.getLogger(__name__)

# Constants
DEFAULT_SETTINGS_ID = "default"
DEFAULT_AGENT_ID = "default"

# Retry configuration
MAX_RETRIES = 3
INITIAL_DELAY_MS = 100  # 100ms, 200ms, 400ms

T = TypeVar('T')


async def retry_with_backoff(
    operation: Callable[[], T],
    operation_name: str,
    max_retries: int = MAX_RETRIES,
    initial_delay_ms: int = INITIAL_DELAY_MS,
) -> T:
    """Execute an operation with exponential backoff retry.
    
    Args:
        operation: Async callable to execute
        operation_name: Name for logging purposes
        max_retries: Maximum number of retry attempts
        initial_delay_ms: Initial delay in milliseconds (doubles each retry)
    
    Returns:
        Result of the operation
    
    Raises:
        Exception: If all retries are exhausted
    
    Validates: Requirements 6.1
    """
    last_exception = None
    delay_ms = initial_delay_ms
    
    for attempt in range(max_retries + 1):
        try:
            return await operation()
        except Exception as e:
            last_exception = e
            if attempt < max_retries:
                logger.warning(
                    "%s failed (attempt %d/%d), "
                    "retrying in %dms: %s",
                    operation_name, attempt + 1, max_retries + 1, delay_ms, e
                )
                await asyncio.sleep(delay_ms / 1000.0)
                delay_ms *= 2  # Exponential backoff
            else:
                logger.error(
                    "%s failed after %d attempts: %s",
                    operation_name, max_retries + 1, e
                )
    
    raise last_exception


class InitializationManager:
    """Manages application initialization state and flow.
    
    This class coordinates the initialization process, deciding whether to run
    full initialization or quick validation based on the initialization_complete
    flag stored in the database.
    """
    
    def __init__(self):
        """Initialize the InitializationManager."""
        self._mode: str = "unknown"
        self._cached_workspace_path: Optional[str] = None
    
    async def get_initialization_status(self) -> dict:
        """Get current initialization state.
        
        Returns:
            dict with keys:
                - initialization_complete: bool - Whether first-time init is done
                - mode: str - 'first_run', 'quick_validation', or 'reset'
        
        Validates: Requirements 1.1, 5.1, 5.2
        """
        is_complete = await self.is_initialization_complete()
        return {
            "initialization_complete": is_complete,
            "mode": self._mode if self._mode != "unknown" else ("quick_validation" if is_complete else "first_run"),
        }
    
    async def is_initialization_complete(self) -> bool:
        """Check if first-time initialization has been completed.
        
        Returns:
            True if initialization_complete flag is set, False otherwise.
        
        Validates: Requirements 1.1, 1.4, 6.1
        """
        async def _check():
            settings = await db.app_settings.get(DEFAULT_SETTINGS_ID)
            if settings is None:
                return False
            return bool(settings.get("initialization_complete", 0))
        
        try:
            return await retry_with_backoff(
                _check,
                "Check initialization status"
            )
        except Exception as e:
            logger.error("Failed to check initialization status after retries: %s", e)
            return False
    
    async def set_initialization_complete(self, complete: bool) -> None:
        """Set the initialization complete flag.
        
        Args:
            complete: True to mark initialization as complete, False otherwise.
        
        Validates: Requirements 1.3, 1.4, 6.1
        """
        async def _set():
            settings = await db.app_settings.get(DEFAULT_SETTINGS_ID)
            if settings is None:
                # Create default settings if they don't exist
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc).isoformat()
                settings = {
                    "id": DEFAULT_SETTINGS_ID,
                    "initialization_complete": 1 if complete else 0,
                    "created_at": now,
                    "updated_at": now,
                }
                await db.app_settings.put(settings)
            else:
                await db.app_settings.update(DEFAULT_SETTINGS_ID, {
                    "initialization_complete": 1 if complete else 0
                })
            return None
        
        try:
            await retry_with_backoff(
                _set,
                "Set initialization status"
            )
            logger.info("Set initialization_complete to %s", complete)
        except Exception as e:
            logger.error("Failed to set initialization status after retries: %s", e)
            raise

    async def run_quick_validation(self) -> bool:
        """Run quick validation to check if required resources exist.
        
        This method performs fast checks to verify that the default agent
        and workspace exist in the database, without performing full
        directory scanning or registration.
        
        Returns:
            True if all required resources exist, False otherwise.
        
        Validates: Requirements 3.2, 3.3, 3.4, 6.1
        """
        self._mode = "quick_validation"
        logger.info("Running quick validation...")
        
        async def _validate():
            # Check if default agent exists
            agent = await db.agents.get(DEFAULT_AGENT_ID)
            if agent is None:
                logger.warning("Quick validation failed: default agent not found")
                return False
            
            # Check if default workspace config exists
            workspace = await db.workspace_config.get_config()
            if workspace is None:
                logger.warning("Quick validation failed: workspace config not found")
                return False
            
            return True
        
        try:
            result = await retry_with_backoff(
                _validate,
                "Quick validation"
            )
            if result:
                logger.info("Quick validation passed: all required resources exist")
            return result
            
        except Exception as e:
            logger.error("Quick validation failed with error after retries: %s", e)
            return False

    def get_cached_workspace_path(self) -> str:
        """Return the cached expanded SwarmWorkspace path.

        This path is set once during app init and read per-session.
        No DB lookup occurs.

        Falls back to computing from DEFAULT_WORKSPACE_CONFIG if not set.

        Returns:
            The expanded filesystem path to the SwarmWorkspace directory.

        Validates: Requirements 1.1, 1.2
        """
        if not self._cached_workspace_path:
            # Fallback: compute from DEFAULT_WORKSPACE_CONFIG
            from core.swarm_workspace_manager import swarm_workspace_manager
            self._cached_workspace_path = swarm_workspace_manager.expand_path(
                swarm_workspace_manager.DEFAULT_WORKSPACE_CONFIG["file_path"]
            )
        return self._cached_workspace_path



    async def run_full_initialization(self) -> bool:
        """Run full initialization (skills, MCPs, agent, workspace).
        
        This method performs complete initialization including:
        - Scanning and registering default skills
        - Scanning and registering default MCP servers
        - Creating/updating the default agent (SwarmAgent)
        - Creating the default workspace (SwarmWorkspace)
        - Setting up skill symlinks in the workspace
        - Copying templates into the workspace
        - Caching the expanded workspace path for per-session use
        
        The initialization_complete flag is only set to True if ALL
        critical steps succeed (agent and workspace creation).
        
        Returns:
            True if all critical steps succeeded, False otherwise.
        
        Validates: Requirements 1.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.2, 4.1, 8.1
        """
        from pathlib import Path
        from core.agent_defaults import ensure_default_agent
        from core.swarm_workspace_manager import swarm_workspace_manager
        
        self._mode = "first_run"
        logger.info("Running full initialization...")
        
        try:
            # Run skill_ids → allowed_skills migration BEFORE SkillManager is used.
            # This is idempotent and safe to call on every startup.
            try:
                from core.skill_migration import migrate_skill_ids_to_allowed_skills
                await migrate_skill_ids_to_allowed_skills(db)
                logger.info("Skill migration check completed")
            except Exception as e:
                logger.error("Skill migration failed (non-fatal): %s", e)
                # Non-critical — continue initialization; the migration will
                # retry on next startup since it is idempotent.

            # Create/update default agent (includes skill and MCP registration)
            # This is a critical step - failure means we don't set initialization_complete
            try:
                await ensure_default_agent()
                logger.info("Default agent ensured during full initialization")
            except Exception as e:
                logger.error("Failed to ensure default agent: %s", e)
                # Do NOT set initialization_complete - this is a critical failure
                return False
            
            # Create/migrate default workspace (includes folder structure)
            # This is a critical step - failure means we don't set initialization_complete
            try:
                workspace = await swarm_workspace_manager.ensure_default_workspace(db)
                logger.info("Default workspace ensured during full initialization")
            except Exception as e:
                logger.error("Failed to ensure default workspace: %s", e)
                # Do NOT set initialization_complete - this is a critical failure
                return False
            
            # Expand workspace path and run workspace setup
            workspace_path = swarm_workspace_manager.expand_path(workspace["file_path"])
            
            # Cache the expanded path BEFORE refresh (refresh_builtin_defaults
            # calls get_cached_workspace_path())
            self._cached_workspace_path = workspace_path
            
            # Refresh built-in skills and context files
            await self.refresh_builtin_defaults()

            # --- MCP file-based config: migration + catalog merge ---
            try:
                from pathlib import Path
                ws_path = Path(workspace_path)

                # One-time migration from DB + legacy JSON → mcp-dev.json
                from core.mcp_migration import migrate_if_needed
                await migrate_if_needed(ws_path)

                # Merge bundled catalog template → mcp-catalog.json
                from core.mcp_config_loader import merge_catalog_template
                from utils.bundle_paths import get_resources_dir
                _backend_dir = Path(__file__).resolve().parent.parent
                _dev_resources = _backend_dir.parent / "desktop" / "resources"
                resources_dir = get_resources_dir(_dev_resources)
                template_path = resources_dir / "mcp-catalog.json"
                merge_catalog_template(ws_path, template_path)

                # Ensure .claude/mcps/ directory exists
                (ws_path / ".claude" / "mcps").mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning("MCP file config setup failed (non-critical): %s", e)
            
            # All critical steps succeeded - set the flag
            await self.set_initialization_complete(True)
            logger.info("Full initialization completed successfully")
            return True
            
        except Exception as e:
            logger.error("Full initialization failed: %s", e)
            # Do NOT set initialization_complete
            return False

    async def refresh_builtin_defaults(self) -> None:
        """Refresh built-in skills and context files.

        Called on every startup (both full init and quick validation).
        Each step is independent — failure in one does not block others.

        Steps:
            0. Clean stale git index.lock (safe at startup — no git ops in flight)
            1. Re-scan skills (populates cache with latest built-in skills)
            2. Re-project skill symlinks (creates/removes symlinks based on cache)
            3. Refresh built-in context files (overwrites with latest source)

        Validates: Requirements 2.1, 2.3
        """
        from pathlib import Path

        workspace_path = self.get_cached_workspace_path()

        # Step 0: Clean stale git index.lock from previous crash/restart.
        # Safe at startup because no git operations from our app can be
        # in-flight when the backend is just booting.
        try:
            lock_file = Path(workspace_path) / ".git" / "index.lock"
            if lock_file.exists():
                lock_file.unlink()
                logger.warning(
                    "Removed stale .git/index.lock from %s (left by previous crash)",
                    workspace_path,
                )
        except Exception as e:
            logger.warning("Failed to clean stale git index.lock: %s", e)

        # Step 1: Re-scan skills (MUST run before projection)
        _sm = None
        try:
            from core.skill_manager import skill_manager as _sm
            await _sm.scan_all()
            logger.info("SkillManager scan completed during refresh")
        except Exception as e:
            logger.error("Skill scan failed during refresh (non-fatal): %s", e)

        # Step 2: Re-project skill symlinks (uses cache from step 1)
        try:
            if _sm is None:
                from core.skill_manager import skill_manager as _sm
            from core.projection_layer import ProjectionLayer
            _projection = ProjectionLayer(_sm)
            await _projection.project_skills(Path(workspace_path), allow_all=True)
            logger.info("Skill symlinks projected during refresh")
        except Exception as e:
            logger.error("Skill projection failed during refresh (non-fatal): %s", e)

        # Step 3: Refresh built-in context files
        # Note: templates_dir uses __file__-relative path here, which is safe
        # because initialization_manager.py always passes templates_dir
        # explicitly. The sys._MEIPASS fallback is only needed in
        # SkillManager where builtin_path defaults from __file__.
        try:
            from core.context_directory_loader import ContextDirectoryLoader
            loader = ContextDirectoryLoader(
                context_dir=Path(workspace_path) / ".context",
                templates_dir=Path(__file__).resolve().parent.parent / "context",
            )
            loader.ensure_directory()
            logger.info("Context directory refreshed during refresh")
        except Exception as e:
            logger.error("Context refresh failed during refresh (non-fatal): %s", e)

        # Step 4 (removed): config.json symlink no longer needed — config.json
        # now lives directly in SwarmWS/ via AppConfigManager.

        # Step 5: Check CLI tool availability (non-fatal, report-only)
        # Actual installation is deferred to user consent or explicit request.
        try:
            from core.cli_tool_provisioner import check_all_tools
            status = check_all_tools()
            if status["missing"]:
                missing_ids = [t["id"] for t in status["missing"]]
                logger.warning(
                    "CLI tools missing (%d/%d): %s — "
                    "install via 'brew install %s' or ask SwarmAI to provision",
                    len(status["missing"]), status["total"],
                    missing_ids,
                    " ".join(
                        t.get("install", {}).get("brew", t["id"])
                        for t in status["missing"]
                    ),
                )
            else:
                logger.info(
                    "All %d required CLI tools are available",
                    status["total"],
                )
        except Exception as e:
            logger.error("CLI tool check failed (non-fatal): %s", e)


    async def reset_to_defaults(self) -> dict:
        """Reset initialization state and re-run full initialization.
        
        This method clears the initialization_complete flag and triggers
        full initialization, useful for recovering from configuration issues.
        
        Returns:
            dict with keys:
                - success: bool - Whether reset completed successfully
                - error: Optional[str] - Error message if failed
        
        Validates: Requirements 4.2, 4.3, 4.4, 4.5
        """
        self._mode = "reset"
        logger.info("Resetting to defaults...")
        
        try:
            # Clear the initialization flag
            await self.set_initialization_complete(False)
            logger.info("Cleared initialization_complete flag")
            
            # Trigger full initialization
            success = await self.run_full_initialization()
            
            if success:
                logger.info("Reset to defaults completed successfully")
                return {"success": True, "error": None}
            else:
                error_msg = "Full initialization failed during reset"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
                
        except Exception as e:
            error_msg = f"Reset to defaults failed: {e}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}


# Global instance
initialization_manager = InitializationManager()
