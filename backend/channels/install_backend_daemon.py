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


def _deploy_daemon_binary() -> bool:
    """Deploy the backend binary to ~/.swarm-ai/daemon/.

    Searches for the PyInstaller binary in these locations (priority order):
    1. Tauri app bundle: /Applications/SwarmAI.app/Contents/MacOS/python-backend
    2. Dev build output: <repo>/desktop/src-tauri/binaries/python-backend-*

    Returns True if binary was deployed, False if not found.
    The daemon wrapper script falls back to venv Python if no binary exists.
    """
    import platform as _platform
    import shutil

    daemon_dir = Path.home() / ".swarm-ai" / "daemon"
    daemon_binary = daemon_dir / "python-backend"

    # Already deployed and recent (< 1 hour) — skip
    if daemon_binary.exists():
        import time
        age_hours = (time.time() - daemon_binary.stat().st_mtime) / 3600
        if age_hours < 1:
            print(f"  Daemon binary already deployed ({age_hours:.0f}h old)")
            return True

    # Source 1: Installed Tauri app bundle
    app_binary = Path("/Applications/SwarmAI.app/Contents/MacOS/python-backend")
    if not app_binary.exists():
        # Also check user Applications
        app_binary = Path.home() / "Applications/SwarmAI.app/Contents/MacOS/python-backend"

    # Source 2: Dev build output (for developers running ./dev.sh build)
    if _platform.machine() == "arm64":
        target = "aarch64-apple-darwin"
    else:
        target = "x86_64-apple-darwin"
    dev_binary = Path(__file__).parent.parent.parent / "desktop" / "src-tauri" / "binaries" / f"python-backend-{target}"

    # Pick the first available source
    source = None
    for candidate in [app_binary, dev_binary]:
        if candidate.exists():
            source = candidate
            break

    if source is None:
        print("  No backend binary found — daemon will use venv fallback")
        return False

    # Atomic copy: write to .tmp then rename
    daemon_dir.mkdir(parents=True, exist_ok=True)
    tmp = daemon_binary.with_suffix(".tmp")
    shutil.copy2(str(source), str(tmp))
    tmp.rename(daemon_binary)
    daemon_binary.chmod(0o755)
    print(f"  Daemon binary deployed from {source}")
    return True


def install():
    """Install the backend daemon plist and deploy binary."""
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

    # Deploy backend binary to ~/.swarm-ai/daemon/
    _deploy_daemon_binary()

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

    # Load the plist (idempotent — handles already-bootstrapped)
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{_uid()}", str(dest)],
        capture_output=True, text=True,
    )
    if result.returncode in (5, 37):
        # 5 = I/O error (service already loaded, common on macOS Ventura+)
        # 37 = Operation already in progress (already bootstrapped)
        print(f"  Already loaded: {DAEMON_LABEL} (re-installed plist, kickstarting...)")
        subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{_uid()}/{DAEMON_LABEL}"],
            capture_output=True,
        )
    elif result.returncode != 0:
        print(f"  Bootstrap warning (code {result.returncode}): {result.stderr.strip()}", file=sys.stderr)
    else:
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
