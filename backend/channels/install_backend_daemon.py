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
WRAPPER_SOURCE = Path(__file__).parent / "swarmai_backend.sh"
# Installed wrapper lives in ~/.swarm-ai/ — a non-TCC-protected directory.
# macOS TCC blocks launchd daemons from reading files under ~/Desktop,
# ~/Documents, ~/Downloads without Full Disk Access.  Copying the wrapper
# to ~/.swarm-ai/ avoids the "Operation not permitted" error entirely.
WRAPPER_DEST = Path.home() / ".swarm-ai" / "swarmai_backend.sh"


def _uid() -> int:
    return os.getuid()


def _resolve_wrapper() -> str:
    """Copy wrapper script to ~/.swarm-ai/ and return its path.

    The source script lives in the repo (channels/swarmai_backend.sh).
    We copy it to ~/.swarm-ai/ so launchd can read it without TCC
    restrictions that apply to ~/Desktop, ~/Documents, etc.
    """
    WRAPPER_DEST.parent.mkdir(parents=True, exist_ok=True)
    # Always overwrite — pick up code changes on reinstall
    import shutil
    shutil.copy2(str(WRAPPER_SOURCE), str(WRAPPER_DEST))
    WRAPPER_DEST.chmod(0o755)
    # Clear quarantine just in case
    subprocess.run(
        ["xattr", "-d", "com.apple.quarantine", str(WRAPPER_DEST)],
        capture_output=True,
    )
    return str(WRAPPER_DEST)


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

    if not WRAPPER_SOURCE.exists():
        print(f"Wrapper script not found: {WRAPPER_SOURCE}", file=sys.stderr)
        sys.exit(1)

    # Make source wrapper executable (idempotent)
    WRAPPER_SOURCE.chmod(0o755)

    # Clear macOS quarantine attribute — launchd won't run scripts
    # that are quarantined (shows "Operation not permitted" in stderr).
    subprocess.run(
        ["xattr", "-d", "com.apple.quarantine", str(WRAPPER_SOURCE)],
        capture_output=True,
    )

    # Generate plist from template
    content = TEMPLATE.read_text()
    wrapper_path = _resolve_wrapper()
    content = content.replace("__WRAPPER_PATH__", wrapper_path)
    content = content.replace("__LOG_DIR__", _resolve_log_dir())
    content = content.replace("__HOME__", str(Path.home()))
    content = content.replace("__BACKEND_DIR__", str(WRAPPER_SOURCE.parent.parent.resolve()))

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
