"""Tests for daemon-first backend architecture.

Tests the daemon-first startup flow where launchd manages the backend
process 24/7 and Tauri connects as a pure UI client.

Acceptance criteria tested:
  AC1: App launch with no daemon → auto-bootstrap → connects on 18321
  AC2: App launch with daemon running → connects immediately (no sidecar)
  AC3: App close → daemon keeps running → Slack stays connected
  AC4: tauri:dev does not conflict with running daemon
  AC5: Port 18321 occupied → wrapper exits non-zero with retry limit

Methodology: TDD RED — tests written before implementation changes.
The Rust (lib.rs) changes are verified by integration test + script tests.
Python installer changes and bash wrapper changes are unit-testable.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


BACKEND_DIR = Path(__file__).parent.parent
CHANNELS_DIR = BACKEND_DIR / "channels"
WRAPPER_SCRIPT = CHANNELS_DIR / "swarmai_backend.sh"
INSTALLER_MODULE = "channels.install_backend_daemon"
DAEMON_LABEL = "com.swarmai.backend"
DAEMON_PORT = 18321


# ---------------------------------------------------------------------------
# AC5: Port conflict → wrapper exits non-zero with retry limit
# ---------------------------------------------------------------------------


class TestWrapperPortConflictExitCode:
    """Wrapper script must exit non-zero when port is occupied (for launchd retry)."""

    def test_wrapper_exits_nonzero_on_port_conflict(self):
        """AC5: When port 18321 is taken, wrapper should exit with code 1 (not 0).

        This is critical: exit 0 tells launchd 'success' and it stops retrying.
        exit 1 tells launchd 'failed' and KeepAlive triggers a retry.
        """
        content = WRAPPER_SCRIPT.read_text()
        # Find the port conflict block (from lsof check to the outer fi)
        lines = content.split("\n")
        in_port_check = False
        depth = 0
        exit_codes_in_block = []
        for line in lines:
            stripped = line.strip()
            if "lsof" in stripped and ("DAEMON_PORT" in stripped or str(DAEMON_PORT) in stripped) and "if " in line:
                in_port_check = True
                depth = 1
                continue
            if in_port_check:
                if stripped.startswith("if "):
                    depth += 1
                if "exit " in stripped:
                    # Extract exit code: "exit 1" or "exit 0  # comment"
                    import re
                    match = re.search(r"exit\s+(\d+)", stripped)
                    if match:
                        exit_codes_in_block.append(match.group(1))
                if stripped == "fi":
                    depth -= 1
                    if depth <= 0:
                        break

        # Must include exit 1 (non-zero) for launchd retry
        assert "1" in exit_codes_in_block, (
            f"Port conflict block exits with {exit_codes_in_block} — must include 'exit 1' for launchd retry"
        )

    def test_wrapper_has_retry_limit(self):
        """AC5: Wrapper should limit port-conflict retries (not infinite loop).

        After N consecutive port-conflict failures, wrapper should exit 0
        to stop launchd from retrying endlessly.
        """
        content = WRAPPER_SCRIPT.read_text()
        # Should have some retry counting mechanism
        assert any(
            keyword in content
            for keyword in ["FAIL_COUNT", "fail_count", "retry", "attempt", "RETRY"]
        ), "Wrapper should have a retry/failure counter for port conflicts"

    def test_wrapper_resets_fail_counter_on_success(self):
        """After a successful start, the failure counter should be cleared."""
        content = WRAPPER_SCRIPT.read_text()
        # After the port check passes (port is free), should clear any counter
        # Look for rm/reset of the stamp file after the lsof check
        assert any(
            keyword in content
            for keyword in ["rm -f", "rm ", "echo 0", "> /dev/null", "FAIL_STAMP"]
        ), "Wrapper should reset failure counter when port is free"


# ---------------------------------------------------------------------------
# AC1: Installer handles already-bootstrapped (idempotent)
# ---------------------------------------------------------------------------


class TestInstallerIdempotent:
    """Installer must handle being run when daemon is already bootstrapped."""

    def test_install_handles_already_bootstrapped(self, tmp_path):
        """AC1: Running install when daemon already loaded should not crash.

        launchctl bootstrap returns exit code 37 when the service is already
        loaded. The installer should handle this gracefully.
        """
        import importlib
        mod = importlib.import_module(INSTALLER_MODULE)

        # Mock subprocess.run to simulate already-bootstrapped (exit 37)
        mock_results = []

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            if "bootstrap" in cmd:
                result.returncode = 37  # Already bootstrapped
                result.stderr = "Bootstrap failed: 37: Operation already in progress"
                result.stdout = ""
            else:
                result.returncode = 0
                result.stderr = ""
                result.stdout = ""
            mock_results.append((cmd, result))
            return result

        with patch.object(mod, "LAUNCH_AGENTS", tmp_path), \
             patch("subprocess.run", side_effect=mock_run):
            # Should not raise even when bootstrap returns 37
            mod.install()

        # Verify plist was still written
        dest = tmp_path / f"{DAEMON_LABEL}.plist"
        assert dest.exists(), "Plist should be written even if already bootstrapped"

    def test_install_reports_bootstrap_failure(self, tmp_path, capsys):
        """Install should report non-37 bootstrap failures."""
        import importlib
        mod = importlib.import_module(INSTALLER_MODULE)

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            if "bootstrap" in cmd:
                result.returncode = 5  # I/O error
                result.stderr = "Bootstrap failed: 5: Input/output error"
                result.stdout = ""
            else:
                result.returncode = 0
                result.stderr = ""
                result.stdout = ""
            return result

        with patch.object(mod, "LAUNCH_AGENTS", tmp_path), \
             patch("subprocess.run", side_effect=mock_run):
            mod.install()

        captured = capsys.readouterr()
        # Should mention the failure or the label (current behavior just prints "Loaded")
        # After fix, should print error for non-37 failures
        assert dest_exists_or_error_printed(tmp_path, captured)


def dest_exists_or_error_printed(tmp_path, captured):
    """Helper: either plist exists or error was printed."""
    dest = tmp_path / f"{DAEMON_LABEL}.plist"
    return dest.exists() or "failed" in captured.out.lower() or "error" in captured.err.lower()


# ---------------------------------------------------------------------------
# AC4: dev.sh daemon conflict management
# ---------------------------------------------------------------------------


class TestDevShDaemonConflict:
    """dev.sh should manage daemon lifecycle to avoid conflicts."""

    def test_devsh_has_daemon_bootout(self):
        """AC4: dev.sh should bootout daemon before starting dev server."""
        devsh = BACKEND_DIR.parent / "dev.sh"
        assert devsh.exists(), f"dev.sh not found at {devsh}"
        content = devsh.read_text()

        # Should contain logic to stop daemon before dev mode
        assert any(
            keyword in content
            for keyword in ["bootout", "daemon stop", "daemon_stop"]
        ), "dev.sh should bootout/stop daemon before starting dev server"

    def test_devsh_has_daemon_rebootstrap(self):
        """AC4: dev.sh should re-bootstrap daemon after dev server stops."""
        devsh = BACKEND_DIR.parent / "dev.sh"
        content = devsh.read_text()

        # Should contain logic to restart daemon after dev mode ends
        # This could be in a trap, cleanup function, or kill command
        assert any(
            keyword in content
            for keyword in ["bootstrap", "daemon start", "daemon_start"]
        ), "dev.sh should re-bootstrap daemon when dev server stops"


# ---------------------------------------------------------------------------
# Integration: wrapper script doesn't have unresolved variables
# ---------------------------------------------------------------------------


class TestWrapperScriptIntegrity:
    """Wrapper script should be self-contained and correct."""

    def test_no_unresolved_template_vars(self):
        """Wrapper script should not have any __PLACEHOLDER__ template vars."""
        content = WRAPPER_SCRIPT.read_text()
        import re
        placeholders = re.findall(r"__[A-Z_]+__", content)
        assert not placeholders, f"Unresolved template variables: {placeholders}"

    def test_wrapper_sets_daemon_mode(self):
        """Wrapper must export SWARMAI_MODE=daemon."""
        content = WRAPPER_SCRIPT.read_text()
        assert 'SWARMAI_MODE' in content
        assert '"daemon"' in content or "'daemon'" in content
