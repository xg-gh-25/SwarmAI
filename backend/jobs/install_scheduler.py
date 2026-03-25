#!/usr/bin/env python3
"""
Install the consolidated SwarmAI scheduler launchd plist.

Replaces 6 scattered plists with a single com.swarmai.scheduler.plist.
Run this once after upgrading to the product-level job system.

Usage:
    python -m jobs.install_scheduler           # Install
    python -m jobs.install_scheduler --uninstall  # Remove
    python -m jobs.install_scheduler --status     # Check status
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Old plists to remove
OLD_PLISTS = [
    "com.swarm.signal-pipeline",
    "com.swarm.slack-bot",
    "com.swarm.channel-monitor",
    "com.swarmai.github-monitor",
    "com.swarmai.gh-update-topics",
    "com.swarmai.trash-prune",
    "com.swarmai.jobs",  # Old name from workspace-level install_launchd()
]

NEW_LABEL = "com.swarmai.scheduler"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
TEMPLATE = Path(__file__).parent / "com.swarmai.scheduler.plist"


def _resolve_python() -> str:
    """Find the backend venv Python."""
    # Try common locations
    candidates = [
        Path(__file__).parent.parent / ".venv" / "bin" / "python",  # backend/.venv/
        Path.home() / ".swarm-ai" / "SwarmWS" / "Services" / "swarm-jobs" / "venv" / "bin" / "python",
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
    log_dir = Path.home() / ".swarm-ai" / "SwarmWS" / "Services" / "swarm-jobs" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return str(log_dir)


def install():
    """Install the consolidated scheduler plist."""
    # 1. Unload and remove old plists
    for label in OLD_PLISTS:
        plist_path = LAUNCH_AGENTS / f"{label}.plist"
        if plist_path.exists():
            subprocess.run(
                ["launchctl", "bootout", f"gui/{_uid()}/{label}"],
                capture_output=True,
            )
            plist_path.unlink()
            print(f"  Removed: {label}")

    # 2. Generate new plist from template
    if not TEMPLATE.exists():
        print(f"Template not found: {TEMPLATE}", file=sys.stderr)
        sys.exit(1)

    content = TEMPLATE.read_text()
    content = content.replace("__PYTHON_PATH__", _resolve_python())
    content = content.replace("__BACKEND_DIR__", _resolve_backend_dir())
    content = content.replace("__LOG_DIR__", _resolve_log_dir())

    dest = LAUNCH_AGENTS / f"{NEW_LABEL}.plist"
    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    dest.write_text(content)
    print(f"  Installed: {dest}")

    # 3. Load the new plist
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{_uid()}", str(dest)],
        capture_output=True,
    )
    print(f"  Loaded: {NEW_LABEL}")
    print(f"\nDone. One scheduler replaces {len(OLD_PLISTS)} old plists.")
    print(f"Check: launchctl list | grep swarmai")


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
