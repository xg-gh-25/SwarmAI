#!/usr/bin/env python3
"""Install the SwarmAI Slack daemon launchd plist.

Installs a launchd user agent that keeps the SwarmAI backend (and thus
the Slack channel adapter) running even when the Tauri desktop app is
closed or macOS is locked/sleeping.

Usage:
    python -m channels.install_slack_daemon           # Install
    python -m channels.install_slack_daemon --uninstall  # Remove
    python -m channels.install_slack_daemon --status     # Check status
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

DAEMON_LABEL = "com.swarmai.slack-daemon"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
TEMPLATE = Path(__file__).parent / "com.swarmai.slack-daemon.plist"
WRAPPER = Path(__file__).parent / "slack_daemon.sh"


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
    """Install the Slack daemon plist."""
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
    print(f"\nSlack daemon installed. It will start on login and restart on crash.")
    print(f"  Logs: {_resolve_log_dir()}/slack-daemon-{{stdout,stderr}}.log")
    print(f"  Check: launchctl list | grep slack-daemon")


def uninstall():
    """Remove the Slack daemon plist."""
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
    """Show Slack daemon status."""
    result = subprocess.run(
        ["launchctl", "list"],
        capture_output=True, text=True,
    )
    found = False
    for line in result.stdout.splitlines():
        if "slack-daemon" in line:
            print(line)
            found = True
    if not found:
        print("Slack daemon is not running.")

    dest = LAUNCH_AGENTS / f"{DAEMON_LABEL}.plist"
    print(f"\nPlist exists: {dest.exists()} ({dest})")


def main():
    parser = argparse.ArgumentParser(description="SwarmAI Slack Daemon Installer")
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
