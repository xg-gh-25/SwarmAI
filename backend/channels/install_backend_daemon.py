#!/usr/bin/env python3
"""Install the SwarmAI backend daemon launchd plist.

Installs a launchd user agent that keeps the SwarmAI backend running
even when the Tauri desktop app is closed or macOS is locked/sleeping.
This keeps channels (Slack, etc.) and background jobs alive 24/7.

Usage:
    python -m channels.install_backend_daemon           # Install
    python -m channels.install_backend_daemon --uninstall  # Remove
    python -m channels.install_backend_daemon --status     # Check status
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

DAEMON_LABEL = "com.swarmai.backend"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
TEMPLATE = Path(__file__).parent / "com.swarmai.backend.plist"
WRAPPER = Path(__file__).parent / "swarmai_backend.sh"


def _uid() -> int:
    return os.getuid()


def _resolve_wrapper() -> str:
    """Return the absolute path to the wrapper script."""
    return str(WRAPPER.resolve())


def _resolve_log_dir() -> str:
    """Log directory for daemon output."""
    log_dir = Path.home() / ".swarm-ai" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return str(log_dir)


def install():
    """Install the backend daemon plist."""
    if not TEMPLATE.exists():
        print(f"Template not found: {TEMPLATE}", file=sys.stderr)
        sys.exit(1)

    if not WRAPPER.exists():
        print(f"Wrapper script not found: {WRAPPER}", file=sys.stderr)
        sys.exit(1)

    # Make wrapper executable (idempotent)
    WRAPPER.chmod(0o755)

    # Generate plist from template
    content = TEMPLATE.read_text()
    wrapper_path = _resolve_wrapper()
    content = content.replace("__WRAPPER_PATH__", wrapper_path)
    content = content.replace("__LOG_DIR__", _resolve_log_dir())

    # Uninstall old slack-daemon plist if it exists
    old_label = "com.swarmai.slack-daemon"
    old_plist = LAUNCH_AGENTS / f"{old_label}.plist"
    if old_plist.exists():
        subprocess.run(
            ["launchctl", "bootout", f"gui/{_uid()}/{old_label}"],
            capture_output=True,
        )
        old_plist.unlink()
        print(f"  Removed old plist: {old_label}")

    # Write plist
    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    dest = LAUNCH_AGENTS / f"{DAEMON_LABEL}.plist"
    dest.write_text(content)
    print(f"  Installed plist: {dest}")

    # Load the plist
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{_uid()}", str(dest)],
        capture_output=True,
    )
    print(f"  Loaded: {DAEMON_LABEL}")
    print(f"\nBackend daemon installed. It will start on login and restart on crash.")
    print(f"  Logs: {_resolve_log_dir()}/backend-{{stdout,stderr}}.log")
    print(f"  Check: launchctl list | grep swarmai.backend")


def uninstall():
    """Remove the backend daemon plist."""
    subprocess.run(
        ["launchctl", "bootout", f"gui/{_uid()}/{DAEMON_LABEL}"],
        capture_output=True,
    )
    dest = LAUNCH_AGENTS / f"{DAEMON_LABEL}.plist"
    if dest.exists():
        dest.unlink()
        print(f"Removed: {DAEMON_LABEL}")
    else:
        print(f"Not installed: {dest}")


def status():
    """Show backend daemon status."""
    result = subprocess.run(
        ["launchctl", "list"],
        capture_output=True, text=True,
    )
    found = False
    for line in result.stdout.splitlines():
        if "swarmai.backend" in line:
            print(line)
            found = True
    if not found:
        print("Backend daemon is not running.")

    dest = LAUNCH_AGENTS / f"{DAEMON_LABEL}.plist"
    print(f"\nPlist exists: {dest.exists()} ({dest})")


def main():
    parser = argparse.ArgumentParser(description="SwarmAI Backend Daemon Installer")
    parser.add_argument("--uninstall", action="store_true", help="Remove the daemon")
    parser.add_argument("--status", action="store_true", help="Check daemon status")
    args = parser.parse_args()

    if args.status:
        status()
    elif args.uninstall:
        uninstall()
    else:
        install()


if __name__ == "__main__":
    main()
