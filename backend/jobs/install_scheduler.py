#!/usr/bin/env python3
"""
DEPRECATED: External scheduler plist is no longer used (since v1.13).

The scheduler now runs in-process inside the daemon (main.py asyncio loop).
This module is retained ONLY for:
  - uninstall() — app uninstall cleanup (routers/system.py)
  - Constants (LAUNCH_AGENTS, NEW_LABEL) — used by legacy plist removal

The install() function is a no-op for backwards compatibility.
The plist template (com.swarmai.scheduler.plist) can be deleted in a future release.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from .paths import JOBS_DATA_DIR, LOG_DIR

# Old plists to remove
OLD_PLISTS = [
    "com.swarm.signal-pipeline",
    "com.swarm.slack-bot",
    "com.swarm.channel-monitor",
    "com.swarmai.github-monitor",
    "com.swarmai.gh-update-topics",
    "com.swarmai.trash-prune",
    "com.swarmai.jobs",  # Old name from workspace-level install_launchd()
    "com.swarmai.slack-daemon",  # Replaced by com.swarmai.backend
]

NEW_LABEL = "com.swarmai.scheduler"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
TEMPLATE = Path(__file__).parent / "com.swarmai.scheduler.plist"


def _resolve_python() -> str:
    """Find the backend venv Python."""
    # Try common locations
    candidates = [
        Path(__file__).parent.parent / ".venv" / "bin" / "python",  # backend/.venv/
        JOBS_DATA_DIR / "venv" / "bin" / "python",
    ]
    for p in candidates:
        if p.exists():
            return str(p)

    # Fallback to system
    return shutil.which("python3") or "python3"


def _resolve_backend_dir() -> str:
    """Find the backend directory."""
    return str(Path(__file__).parent.parent)


def _resolve_log_dir() -> str:
    """Log directory for scheduler output."""
    log_dir = LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    return str(log_dir)


def install():
    """DEPRECATED: No-op. Scheduler now runs in-process inside the daemon.

    Retained for CLI backwards compatibility (python -m jobs.install_scheduler).
    Removes any legacy plists if they still exist but does NOT install a new one.
    """
    # Still clean up legacy plists if present
    for label in OLD_PLISTS:
        plist_path = LAUNCH_AGENTS / f"{label}.plist"
        if plist_path.exists():
            subprocess.run(
                ["launchctl", "bootout", f"gui/{_uid()}/{label}"],
                capture_output=True,
            )
            plist_path.unlink()
            print(f"  Removed legacy: {label}")

    # Remove the consolidated plist too (no longer needed)
    dest = LAUNCH_AGENTS / f"{NEW_LABEL}.plist"
    if dest.exists():
        subprocess.run(
            ["launchctl", "bootout", f"gui/{_uid()}/{NEW_LABEL}"],
            capture_output=True,
        )
        dest.unlink()
        print(f"  Removed: {NEW_LABEL}")

    print("\nScheduler now runs in-process (daemon asyncio loop).")
    print("No external plist needed.")


def uninstall():
    """Remove the consolidated scheduler plist."""
    subprocess.run(
        ["launchctl", "bootout", f"gui/{_uid()}/{NEW_LABEL}"],
        capture_output=True,
    )
    dest = LAUNCH_AGENTS / f"{NEW_LABEL}.plist"
    if dest.exists():
        dest.unlink()
        print(f"Removed: {NEW_LABEL}")
    else:
        print(f"Not installed: {dest}")


def status():
    """Show scheduler status."""
    result = subprocess.run(
        ["launchctl", "list"],
        capture_output=True, text=True,
    )
    found = False
    for line in result.stdout.splitlines():
        if "swarmai" in line or "swarm" in line.lower():
            print(line)
            found = True
    if not found:
        print("No SwarmAI scheduler plists found.")

    # Check if plist file exists
    dest = LAUNCH_AGENTS / f"{NEW_LABEL}.plist"
    print(f"\nPlist exists: {dest.exists()} ({dest})")


def _uid() -> int:
    import os
    return os.getuid()


def main():
    parser = argparse.ArgumentParser(description="SwarmAI Scheduler Installer")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if args.status:
        status()
    elif args.uninstall:
        uninstall()
    else:
        install()


if __name__ == "__main__":
    main()
