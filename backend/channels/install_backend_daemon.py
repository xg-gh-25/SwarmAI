#!/usr/bin/env python3
"""Install the SwarmAI backend daemon launchd plist.

DEPRECATED: Daemon installation is now handled automatically by the Tauri
desktop app (auto_install_daemon in lib.rs) on first launch. This script
is kept for: (1) verify_build.py import check, (2) system.py /api/system/daemon
endpoint, (3) manual diagnostics. It will be removed in a future version.

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

from jobs.paths import APP_DATA_DIR, DAEMON_DIR

DAEMON_LABEL = "com.swarmai.backend"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
TEMPLATE = Path(__file__).parent / "com.swarmai.backend.plist"
WRAPPER_SOURCE = Path(__file__).parent / "swarmai_backend.sh"
# Installed wrapper lives in ~/.swarm-ai/ — a non-TCC-protected directory.
# macOS TCC blocks launchd daemons from reading files under ~/Desktop,
# ~/Documents, ~/Downloads without Full Disk Access.  Copying the wrapper
# to ~/.swarm-ai/ avoids the "Operation not permitted" error entirely.
WRAPPER_DEST = APP_DATA_DIR / "swarmai_backend.sh"

# ── Guardian watchdog (C034 recovery) ──────────────────────────────────────
GUARDIAN_LABEL = "com.swarmai.guardian"
GUARDIAN_TEMPLATE = Path(__file__).parent / "com.swarmai.guardian.plist"
GUARDIAN_SCRIPT_SOURCE = Path(__file__).parent / "swarmai_guardian.sh"
GUARDIAN_SCRIPT_DEST = APP_DATA_DIR / "swarmai_guardian.sh"
# Standalone copy of daemon_guard.py (pure stdlib) so the installed guardian can
# run it directly — no repo checkout, no PYTHONPATH, no frozen-bundle dependency.
GUARDIAN_GUARD_PY_SOURCE = Path(__file__).parent.parent / "core" / "daemon_guard.py"
GUARDIAN_GUARD_PY_DEST = APP_DATA_DIR / "guardian" / "daemon_guard.py"


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
    log_dir = APP_DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return str(log_dir)


class DaemonDeployError(RuntimeError):
    """A backend source was found but deploying it into the daemon dir failed."""


def _rsync(args: list[str]) -> None:
    """Run rsync; DaemonDeployError on a missing binary or non-zero exit."""
    try:
        r = subprocess.run(["rsync", *args], capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise DaemonDeployError(f"rsync not available: {exc}") from exc
    if r.returncode != 0:
        raise DaemonDeployError(
            f"rsync rc={r.returncode}: {(r.stderr or '').strip()[-300:]}"
        )


def _sync_bundle_staged(source: Path, daemon_dir: Path) -> None:
    """Stage-then-swap a onedir bundle into ``daemon_dir`` (as daemon-lib.sh does).

    Never syncs into the live dir in place, where a failure halfway would leave
    the running daemon (and its next respawn) on a mix of old and new files:
    1. ``<dir>.next`` = hard-link clone of the live dir (keeps resources/ and
       .version; rsync replaces changed files via temp+rename, so no live inode
       is written);
    2. the bundle sync (``--delete``, anchored excludes) into the clone;
    3. two renames: live → ``<dir>.prev`` (kept as a rollback copy, removed by
       the next deploy), clone → live. A failure before step 3 leaves the live
       dir untouched; a failed swap restores it.
    """
    import shutil

    stage = daemon_dir.with_name(daemon_dir.name + ".next")
    prev = daemon_dir.with_name(daemon_dir.name + ".prev")
    for leftover in (stage, prev):
        try:
            if leftover.is_symlink() or leftover.is_file():
                leftover.unlink()  # rmtree refuses a symlink (ignore_errors hides it)
            else:
                shutil.rmtree(leftover, ignore_errors=True)
        except OSError as exc:
            raise DaemonDeployError(f"cannot clear leftover {leftover}: {exc}") from exc
    try:
        if daemon_dir.is_dir():
            _rsync(["-a", f"--link-dest={daemon_dir}/", f"{daemon_dir}/", f"{stage}/"])
        else:
            stage.mkdir(parents=True)
        # Anchored excludes: only the top-level resources/ + .version (not in
        # the bundle; --delete would wipe them).
        _rsync(["-a", "--delete", "--exclude", "/resources", "--exclude", "/.version",
                f"{source}/", f"{stage}/"])
    except DaemonDeployError as exc:
        shutil.rmtree(stage, ignore_errors=True)
        raise DaemonDeployError(
            f"daemon bundle sync failed ({exc}) — staged copy discarded, "
            f"{daemon_dir} untouched"
        ) from exc
    if daemon_dir.exists():
        try:
            daemon_dir.rename(prev)
        except OSError as exc:
            shutil.rmtree(stage, ignore_errors=True)
            raise DaemonDeployError(
                f"moving the live bundle aside failed ({exc}) — {daemon_dir} untouched"
            ) from exc
    try:
        stage.rename(daemon_dir)
    except OSError as exc:
        if prev.exists() and not daemon_dir.exists():
            prev.rename(daemon_dir)
        raise DaemonDeployError(f"swapping in the staged bundle failed: {exc}") from exc


def _deploy_daemon_binary(
    sources: "list[Path] | None" = None,
    daemon_dir: "Path | None" = None,
) -> bool:
    """Deploy the backend to ~/.swarm-ai/daemon/.

    Candidate sources (the one whose executable is NEWEST wins):
    1. Installed app: ``SwarmAI.app/Contents/Resources/python-backend/`` — the
       onedir bundle ``prod.sh release`` injects (``/Applications`` and
       ``~/Applications``); a legacy one-file ``Contents/MacOS/python-backend``.
    2. Dev build output: ``<repo>/desktop/src-tauri/binaries/python-backend-<target>/``
       — a onedir bundle DIRECTORY (executable + ``_internal/``).

    A onedir bundle is staged and swapped in (``_sync_bundle_staged``, same
    contract as scripts/daemon-lib.sh: keeps the daemon's own ``resources/`` and
    ``.version``, never leaves a half-synced live dir); a plain executable is
    copied. (The old code ``copy2``'d the bundle directory as if it were a file
    → IsADirectoryError on every install.)

    Returns True if the backend was deployed or already current, False if no
    source was found (the wrapper then falls back to venv Python). Raises
    DaemonDeployError if a found source could not be deployed — the caller must
    stop before bootstrapping a possibly half-synced bundle.
    """
    import platform as _platform
    import shutil

    if daemon_dir is None:
        from utils.runtime_paths import current_backend_dir
        daemon_dir = current_backend_dir()   # the dir the launchd wrapper runs
    daemon_binary = daemon_dir / "python-backend"

    if sources is not None:
        candidates = list(sources)  # tests inject sources
    else:
        target = "aarch64-apple-darwin" if _platform.machine() == "arm64" else "x86_64-apple-darwin"
        candidates = []
        for apps in (Path("/Applications"), Path.home() / "Applications"):
            app = apps / "SwarmAI.app" / "Contents"
            candidates += [app / "Resources" / "python-backend", app / "MacOS" / "python-backend"]
        candidates.append(
            Path(__file__).parent.parent.parent / "desktop" / "src-tauri" / "binaries"
            / f"python-backend-{target}"
        )

    def _executable_of(path: Path) -> Path | None:
        if path.is_dir():
            exe = path / "python-backend"
            return exe if exe.is_file() else None
        return path if path.is_file() else None

    # The NEWEST available build wins (by executable mtime), not the first path
    # in the list: a stale legacy one-file binary in /Applications must not beat
    # a fresher ~/Applications bundle or dev build.
    found = [c for c in candidates if _executable_of(c) is not None]
    if not found:
        print("  No backend binary found — daemon will use venv fallback")
        return False
    source = max(found, key=lambda c: _executable_of(c).stat().st_mtime)
    source_exe = _executable_of(source)

    # Skip if deployed binary is newer than (or same age as) source.
    # Compares mtime directly — catches app updates immediately.
    if daemon_binary.exists() and daemon_binary.stat().st_mtime >= source_exe.stat().st_mtime:
        print("  Daemon binary already up-to-date (deployed >= source)")
        return True

    if source.is_dir():
        _sync_bundle_staged(source, daemon_dir)
    else:
        daemon_dir.mkdir(parents=True, exist_ok=True)
        # Legacy one-file binary — atomic copy: write to .tmp then rename
        tmp = daemon_binary.with_suffix(".tmp")
        shutil.copy2(str(source), str(tmp))
        tmp.rename(daemon_binary)
    if not daemon_binary.is_file():
        raise DaemonDeployError(f"daemon binary missing after deploy: {daemon_binary}")
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

    # Deploy backend binary to ~/.swarm-ai/daemon/. A DaemonDeployError
    # propagates on purpose: never go on to bootstrap a half-synced bundle.
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
        # Re-register instead of `kickstart -k`: kickstart respawns from the
        # LOADED job definition, so the plist just written (e.g. a ProcessType
        # change) would never take effect. launchd re-reads it only on bootstrap.
        print(f"  Already loaded: {DAEMON_LABEL} (re-installed plist, reloading job...)")
        subprocess.run(
            ["launchctl", "bootout", f"gui/{_uid()}/{DAEMON_LABEL}"],
            capture_output=True,
        )
        subprocess.run(
            ["launchctl", "bootstrap", f"gui/{_uid()}", str(dest)],
            capture_output=True,
        )
    elif result.returncode != 0:
        print(f"  Bootstrap warning (code {result.returncode}): {result.stderr.strip()}", file=sys.stderr)
    else:
        print(f"  Loaded: {DAEMON_LABEL}")
    print(f"\nBackend daemon installed. It will start on login and restart on crash.")
    print(f"  Logs: {_resolve_log_dir()}/backend-{{stdout,stderr}}.log")
    print(f"  Check: launchctl list | grep swarmai.backend")

    # Install the guardian watchdog alongside the backend.
    install_guardian()


def install_guardian():
    """Install the guardian watchdog plist (C034 recovery).

    The guardian polls every 30s and re-bootstraps the backend ONLY when it is
    deregistered AND no intent sentinel is present AND the port is dead for N
    probes. Idempotent — safe to call on every backend install.
    """
    if not GUARDIAN_TEMPLATE.exists() or not GUARDIAN_SCRIPT_SOURCE.exists() \
            or not GUARDIAN_GUARD_PY_SOURCE.exists():
        print(f"  Guardian assets missing — skipping watchdog install", file=sys.stderr)
        return

    # Copy guardian loop script to ~/.swarm-ai/ (non-TCC-protected).
    import shutil
    GUARDIAN_SCRIPT_DEST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(GUARDIAN_SCRIPT_SOURCE), str(GUARDIAN_SCRIPT_DEST))
    GUARDIAN_SCRIPT_DEST.chmod(0o755)
    subprocess.run(
        ["xattr", "-d", "com.apple.quarantine", str(GUARDIAN_SCRIPT_DEST)],
        capture_output=True,
    )

    # Copy the standalone (stdlib-only) guard script so the guardian can run it
    # directly — works for end-user .app installs with no repo / no PYTHONPATH.
    GUARDIAN_GUARD_PY_DEST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(GUARDIAN_GUARD_PY_SOURCE), str(GUARDIAN_GUARD_PY_DEST))

    # Generate guardian plist from template.
    content = GUARDIAN_TEMPLATE.read_text()
    content = content.replace("__GUARDIAN_SCRIPT__", str(GUARDIAN_SCRIPT_DEST))
    content = content.replace("__LOG_DIR__", _resolve_log_dir())
    content = content.replace("__HOME__", str(Path.home()))

    dest = LAUNCH_AGENTS / f"{GUARDIAN_LABEL}.plist"
    dest.write_text(content)

    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{_uid()}", str(dest)],
        capture_output=True, text=True,
    )
    if result.returncode in (5, 37):
        print(f"  Guardian already loaded (re-installed plist)")
    elif result.returncode != 0:
        print(f"  Guardian bootstrap warning (code {result.returncode}): {result.stderr.strip()}", file=sys.stderr)
    else:
        print(f"  Loaded guardian watchdog: {GUARDIAN_LABEL}")


def uninstall():
    """Remove the backend daemon plist (and the guardian watchdog)."""
    # Remove guardian FIRST so it can't resurrect the backend we're about to
    # stop. Verify deregistration — if bootout fails transiently, a surviving
    # guardian would re-bootstrap the backend within 30s of us removing it.
    g_target = f"gui/{_uid()}/{GUARDIAN_LABEL}"
    r = subprocess.run(["launchctl", "bootout", g_target], capture_output=True)
    if r.returncode not in (0, 3):  # 3 = no such process (already gone)
        # Retry once, then SIGKILL as a last resort, then bootout again.
        subprocess.run(["launchctl", "kill", "SIGKILL", g_target], capture_output=True)
        subprocess.run(["launchctl", "bootout", g_target], capture_output=True)
    # Unlink the guardian plist BEFORE booting out the backend: even if a
    # guardian invocation is mid-flight, its missing-plist guard (line ~100 in
    # swarmai_guardian.sh) makes it abort rather than re-bootstrap.
    guardian_plist = LAUNCH_AGENTS / f"{GUARDIAN_LABEL}.plist"
    if guardian_plist.exists():
        guardian_plist.unlink()
        print(f"Removed: {GUARDIAN_LABEL}")

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
        try:
            install()
        except DaemonDeployError as exc:
            print(f"Install aborted: {exc}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
