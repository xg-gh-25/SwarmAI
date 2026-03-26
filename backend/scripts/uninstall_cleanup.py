#!/usr/bin/env python3
"""SwarmAI Uninstall Cleanup — standalone script.

Removes the launchd scheduler plist and cleans up background processes.
Run this AFTER deleting the app if the scheduler is still firing hourly.

Works with system Python — no venv or SwarmAI backend required.

Usage:
    python3 uninstall_cleanup.py              # Remove scheduler + port file
    python3 uninstall_cleanup.py --all        # Also remove ~/.swarm-ai/ data dir
    python3 uninstall_cleanup.py --dry-run    # Show what would be removed
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "com.swarmai.scheduler"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = LAUNCH_AGENTS / f"{LABEL}.plist"
DATA_DIR = Path.home() / ".swarm-ai"
PORT_FILE = DATA_DIR / "backend.port"

# Legacy labels from older SwarmAI versions
LEGACY_LABELS = [
    "com.swarm.signal-pipeline",
    "com.swarm.slack-bot",
    "com.swarm.channel-monitor",
    "com.swarmai.github-monitor",
    "com.swarmai.gh-update-topics",
    "com.swarmai.trash-prune",
    "com.swarmai.jobs",
]


def _uid() -> int:
    return os.getuid()


def remove_plist(label: str, dry_run: bool = False) -> bool:
    """Unload and remove a single launchd plist. Returns True if found."""
    plist = LAUNCH_AGENTS / f"{label}.plist"
    if not plist.exists():
        return False

    if dry_run:
        print(f"  [dry-run] Would remove: {plist}")
        return True

    subprocess.run(
        ["launchctl", "bootout", f"gui/{_uid()}/{label}"],
        capture_output=True,
    )
    plist.unlink(missing_ok=True)
    print(f"  Removed: {label}")
    return True


def main():
    parser = argparse.ArgumentParser(description="SwarmAI Uninstall Cleanup")
    parser.add_argument("--all", action="store_true",
                        help="Also remove ~/.swarm-ai/ data directory")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be removed without doing it")
    args = parser.parse_args()

    print("SwarmAI Uninstall Cleanup")
    print("=" * 40)

    # 1. Remove scheduler plist
    found = remove_plist(LABEL, args.dry_run)
    if not found:
        print(f"  Scheduler plist not found ({PLIST_PATH})")

    # 2. Remove legacy plists
    for label in LEGACY_LABELS:
        remove_plist(label, args.dry_run)

    # 3. Remove port file
    if PORT_FILE.exists():
        if args.dry_run:
            print(f"  [dry-run] Would remove: {PORT_FILE}")
        else:
            PORT_FILE.unlink()
            print(f"  Removed: {PORT_FILE}")

    # 4. Optionally remove data directory
    if args.all:
        if DATA_DIR.exists():
            if args.dry_run:
                print(f"  [dry-run] Would remove: {DATA_DIR}")
            else:
                shutil.rmtree(DATA_DIR)
                print(f"  Removed: {DATA_DIR}")
        else:
            print(f"  Data directory not found ({DATA_DIR})")

    print()
    if args.dry_run:
        print("Dry run complete — no changes made.")
    else:
        print("Cleanup complete.")
        if not args.all:
            print(f"Note: Data directory kept at {DATA_DIR}")
            print("  Run with --all to remove it too.")


if __name__ == "__main__":
    main()
