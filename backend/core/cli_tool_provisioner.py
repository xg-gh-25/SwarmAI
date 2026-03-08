"""CLI Tool Provisioner — ensures required CLI tools are available.

Reads required-cli-tools.json from the resources directory and checks/installs
missing tools using the appropriate package manager (Homebrew on macOS,
apt on Linux).

Called during initialization as a non-fatal step. Missing tools are logged
but do not block startup. Installation requires user consent or auto_approve
configuration.

Architecture:
  required-cli-tools.json (product-level registry)
      ↓
  cli_tool_provisioner.py (this module — check/install logic)
      ↓
  initialization_manager.py (calls provision_cli_tools() during refresh)
"""
import json
import logging
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Path to the registry file (relative to this module)
_RESOURCES_DIR = Path(__file__).resolve().parent.parent.parent / "desktop" / "resources"
REGISTRY_FILE = _RESOURCES_DIR / "required-cli-tools.json"


def _detect_package_manager() -> Optional[str]:
    """Detect the system package manager.

    Returns:
        'brew' on macOS with Homebrew, 'apt' on Debian/Ubuntu, or None.
    """
    system = platform.system()
    if system == "Darwin":
        if shutil.which("brew"):
            return "brew"
        return None
    elif system == "Linux":
        if shutil.which("apt-get"):
            return "apt"
        return None
    return None


def load_registry() -> list[dict]:
    """Load the CLI tool registry from JSON.

    Returns:
        List of tool definitions, or empty list on error.
    """
    try:
        if not REGISTRY_FILE.exists():
            logger.warning("CLI tool registry not found at %s", REGISTRY_FILE)
            return []
        with open(REGISTRY_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to load CLI tool registry: %s", e)
        return []


def check_tool(tool: dict) -> bool:
    """Check if a CLI tool is available on PATH.

    Args:
        tool: Tool definition from registry.

    Returns:
        True if the tool's check_command is found on PATH.
    """
    cmd = tool.get("check_command", tool["id"])
    return shutil.which(cmd) is not None


def check_all_tools() -> dict:
    """Check availability of all registered CLI tools.

    Returns:
        Dict with keys:
            - available: list of tool IDs that are installed
            - missing: list of tool dicts that are not installed
            - total: total number of registered tools
    """
    registry = load_registry()
    available = []
    missing = []

    for tool in registry:
        if check_tool(tool):
            available.append(tool["id"])
        else:
            missing.append(tool)

    return {
        "available": available,
        "missing": missing,
        "total": len(registry),
    }


def install_tool(tool: dict, pkg_manager: str) -> bool:
    """Install a single CLI tool using the detected package manager.

    Args:
        tool: Tool definition from registry.
        pkg_manager: 'brew' or 'apt'.

    Returns:
        True if installation succeeded, False otherwise.
    """
    install_config = tool.get("install", {})
    package_name = install_config.get(pkg_manager)

    if not package_name:
        logger.warning(
            "No %s package defined for tool '%s', skipping",
            pkg_manager, tool["id"]
        )
        return False

    try:
        if pkg_manager == "brew":
            cmd = ["brew", "install", package_name]
        elif pkg_manager == "apt":
            cmd = ["sudo", "apt-get", "install", "-y", package_name]
        else:
            logger.error("Unsupported package manager: %s", pkg_manager)
            return False

        logger.info("Installing %s via %s: %s", tool["id"], pkg_manager, " ".join(cmd))
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout per tool
        )

        if result.returncode == 0:
            logger.info("Successfully installed %s", tool["id"])
            return True
        else:
            logger.error(
                "Failed to install %s (exit %d): %s",
                tool["id"], result.returncode, result.stderr[:500]
            )
            return False

    except subprocess.TimeoutExpired:
        logger.error("Installation of %s timed out after 300s", tool["id"])
        return False
    except Exception as e:
        logger.error("Error installing %s: %s", tool["id"], e)
        return False


def provision_missing_tools(
    priority_filter: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Check and install missing CLI tools.

    Args:
        priority_filter: If set, only install tools with this priority (e.g. 'P0').
        dry_run: If True, only report what would be installed without installing.

    Returns:
        Dict with keys:
            - checked: int — total tools checked
            - already_installed: list of tool IDs already present
            - installed: list of tool IDs successfully installed
            - failed: list of tool IDs that failed to install
            - skipped: list of tool IDs skipped (no package manager, filtered, etc.)
            - pkg_manager: detected package manager or None
    """
    status = check_all_tools()
    pkg_manager = _detect_package_manager()

    result = {
        "checked": status["total"],
        "already_installed": status["available"],
        "installed": [],
        "failed": [],
        "skipped": [],
        "pkg_manager": pkg_manager,
    }

    if not status["missing"]:
        logger.info("All %d CLI tools are already installed", status["total"])
        return result

    if not pkg_manager:
        logger.warning(
            "No supported package manager found. %d tools missing: %s",
            len(status["missing"]),
            [t["id"] for t in status["missing"]]
        )
        result["skipped"] = [t["id"] for t in status["missing"]]
        return result

    for tool in status["missing"]:
        # Apply priority filter if set
        if priority_filter and tool.get("priority") != priority_filter:
            result["skipped"].append(tool["id"])
            continue

        if dry_run:
            logger.info("[DRY RUN] Would install: %s (%s)", tool["id"], tool["name"])
            result["skipped"].append(tool["id"])
            continue

        if install_tool(tool, pkg_manager):
            result["installed"].append(tool["id"])
        else:
            result["failed"].append(tool["id"])

    logger.info(
        "CLI tool provisioning complete: %d installed, %d failed, %d skipped",
        len(result["installed"]),
        len(result["failed"]),
        len(result["skipped"]),
    )
    return result
