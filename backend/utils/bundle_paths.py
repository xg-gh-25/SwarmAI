"""Utilities for finding resources in both dev and Tauri bundle environments.

This module provides centralized path detection logic for locating bundled
resources in both development mode and production Tauri app bundles.

Tauri macOS Bundle Structure:
    SwarmAI.app/
    ├── Contents/
    │   ├── MacOS/
    │   │   ├── swarmai (main Tauri app)
    │   │   └── python-backend (PyInstaller sidecar)
    │   └── Resources/
    │       └── _up_/
    │           └── resources/
    │               ├── seed.db
    │               ├── default-agent.json
    │               └── default-mcp-servers.json
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

    # In frozen bundle, try known alternatives
    base = getattr(sys, "_base_executable", None)
    if base and Path(base).exists():
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


def _get_tauri_bundle_resource_candidates(exe_dir: Path) -> list[Path]:
    """Get candidate paths for resources in Tauri bundle.
    
    Args:
        exe_dir: Directory containing the executable (Contents/MacOS/)
        
    Returns:
        List of candidate paths to check, in priority order
    """
    return [
        # macOS .app bundle: Contents/MacOS/../Resources/_up_/resources/
        exe_dir.parent / "Resources" / "_up_" / "resources",
        # Alternative macOS path (using string navigation)
        (exe_dir / ".." / "Resources" / "_up_" / "resources").resolve(),
        # Windows/Linux: resources folder next to executable
        exe_dir / "resources",
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
