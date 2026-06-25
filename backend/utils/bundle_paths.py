"""Utilities for finding resources in both dev and Tauri bundle environments.

This module provides centralized path detection logic for locating bundled
resources in both development mode and production Tauri app bundles.

Tauri macOS Bundle Structure:
    SwarmAI.app/
    ├── Contents/
    │   ├── MacOS/
    │   │   ├── swarmai (main Tauri app)
    │   │   └── python-backend (PyInstaller binary, deployed to daemon)
    │   └── Resources/
    │       └── _up_/
    │           └── resources/
    │               ├── seed.db
    │               ├── default-agent.json
    │               └── mcp-catalog.json
"""
import shutil
from pathlib import Path
import sys
import logging

logger = logging.getLogger(__name__)


def get_python_executable() -> str:
    """Return the Python interpreter path, safe for PyInstaller bundles.

    In development, ``sys.executable`` is the Python interpreter.
    In a frozen (PyInstaller) bundle, ``sys.executable`` is the bundled
    binary (e.g. ``python-backend``), **not** a Python interpreter.
    Spawning ``python-backend some_script.py`` fails because the binary's
    argparser rejects the unknown arguments.

    Resolution order:
    1. If not frozen → ``sys.executable`` (standard Python interpreter).
    2. ``sys._base_executable`` — set by venv/virtualenv to the real Python.
    3. ``shutil.which("python3")`` → system Python.
    4. ``shutil.which("python")`` → fallback.
    5. ``"python3"`` → last resort (will fail loudly if missing).
    """
    if not getattr(sys, "frozen", False):
        return sys.executable

    # In frozen bundle, try known alternatives.
    # NOTE: PyInstaller onedir sets sys._base_executable = sys.executable (the frozen
    # binary), so we must verify it's actually a Python interpreter, not just that
    # the file exists.
    base = getattr(sys, "_base_executable", None)
    if base and Path(base).exists() and base != sys.executable:
        # Only use _base_executable if it differs from the frozen binary
        return base

    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return found

    logger.warning(
        "Cannot find Python interpreter in frozen bundle — "
        "subprocess calls to .py scripts will likely fail"
    )
    return "python3"


def _get_deployed_daemon_resources() -> Path:
    """Canonical deployed-daemon resources dir: ``<app_data>/daemon/resources``.

    This is the single authoritative location the daemon is deployed to (see
    ``scripts/daemon-lib.sh`` DAEMON_DIR). Used as a last-resort candidate so a
    frozen binary still finds resources even when ``sys.executable`` /
    ``__file__`` resolve to a stale build-output path (PyInstaller bakes the
    build-time source path into ``__file__``, and the build-output binary under
    ``desktop/src-tauri/binaries/...`` has no ``resources/`` sibling).

    The app-data root comes from ``config.get_app_data_dir()`` (the SSOT for
    ``~/.swarm-ai``) rather than re-deriving ``Path.home()`` here. Imported
    lazily to keep this low-level util import-cycle-free.
    """
    try:
        from config import get_app_data_dir
        app_data = get_app_data_dir()
    except Exception:
        # Last-ditch fallback if config is unavailable (e.g. very early import).
        app_data = Path.home() / ".swarm-ai"
    return app_data / "daemon" / "resources"


def _get_tauri_bundle_resource_candidates(exe_dir: Path) -> list[Path]:
    """Get candidate paths for resources in Tauri bundle.
    
    Args:
        exe_dir: Directory containing the executable (Contents/MacOS/)
        
    Returns:
        List of candidate paths to check, in priority order
    """
    return [
        # Daemon mode: resources/ next to the binary (~/.swarm-ai/daemon/resources/)
        exe_dir / "resources",
        # macOS .app bundle: Contents/MacOS/../Resources/_up_/resources/
        exe_dir.parent / "Resources" / "_up_" / "resources",
        # Alternative macOS path (using string navigation)
        (exe_dir / ".." / "Resources" / "_up_" / "resources").resolve(),
        # Last resort: the canonical deployed-daemon location. Covers the case
        # where exe_dir points at a build-output binary (smoke-test during
        # `prod.sh build`) that has no resources/ sibling.
        _get_deployed_daemon_resources(),
    ]


def get_resources_dir(dev_path: Path) -> Path:
    """Get the resources directory path.
    
    Handles both development and production (Tauri bundle) environments.
    
    Args:
        dev_path: Path to resources in development mode (e.g., desktop/resources/)
        
    Returns:
        Path to the resources directory
        
    Note:
        In development, returns dev_path if it exists.
        In production (PyInstaller bundle), searches Tauri bundle locations.
        Falls back to dev_path if nothing found (will fail with clear error).
    """
    # Development path takes priority
    if dev_path.exists():
        logger.debug(f"Using development resources path: {dev_path}")
        return dev_path
    
    # Production path: Check relative to the executable
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).parent
        logger.debug(f"Running as frozen executable, exe_dir: {exe_dir}")
        
        for candidate in _get_tauri_bundle_resource_candidates(exe_dir):
            resolved = candidate.resolve() if not candidate.is_absolute() else candidate
            logger.debug(f"Checking resources path: {resolved}")
            if resolved.exists():
                logger.debug(f"Found resources directory at: {resolved}")
                return resolved
        
        logger.warning("Resources directory not found in any Tauri bundle location")
    
    # Fallback to dev path (will likely fail but provides clear error)
    return dev_path


def get_resource_file(filename: str, dev_path: Path) -> Path | None:
    """Get path to a specific resource file.
    
    Handles both development and production (Tauri bundle) environments.
    
    Args:
        filename: Name of the resource file (e.g., "seed.db")
        dev_path: Path to the file in development mode
        
    Returns:
        Path to the resource file, or None if not found
        
    Note:
        In development, returns dev_path if it exists.
        In production (PyInstaller bundle), searches Tauri bundle locations.
    """
    # Development path takes priority
    if dev_path.exists():
        logger.debug(f"Found {filename} at development path: {dev_path}")
        return dev_path
    
    # Production path: Check relative to the executable
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).parent
        logger.debug(f"Running as frozen executable, searching for {filename}")
        
        for candidate in _get_tauri_bundle_resource_candidates(exe_dir):
            file_path = candidate / filename
            resolved = file_path.resolve()
            logger.debug(f"Checking {filename} path: {resolved}")
            if resolved.exists():
                logger.info(f"Found {filename} at: {resolved}")
                return resolved
        
        # Log all checked paths for debugging
        checked_paths = [
            str((candidate / filename).resolve())
            for candidate in _get_tauri_bundle_resource_candidates(exe_dir)
        ]
        logger.warning(f"{filename} not found. Checked: {checked_paths}")
    
    return None
