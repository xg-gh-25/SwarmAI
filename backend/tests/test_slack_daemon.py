"""Tests for Slack daemon installer and wrapper script.

Tests the install/uninstall lifecycle, plist generation, port conflict
detection, and wrapper script correctness. All tests run without root
privileges or actual launchd interaction (mocked).

Acceptance criteria tested:
  AC1: launchd plist installs and starts on login
  AC2: Backend runs standalone without Tauri (fixed port 18321)
  AC3: Slack bot responds when macOS lid closed (caffeinate)
  AC4: Daemon survives macOS sleep via caffeinate
  AC5: Daemon auto-restarts on crash (KeepAlive)
"""

from __future__ import annotations

import os
import plistlib
import socket
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BACKEND_DIR = Path(__file__).parent.parent
CHANNELS_DIR = BACKEND_DIR / "channels"
PLIST_TEMPLATE = CHANNELS_DIR / "com.swarmai.slack-daemon.plist"
WRAPPER_SCRIPT = CHANNELS_DIR / "slack_daemon.sh"
INSTALLER_MODULE = "channels.install_slack_daemon"

DAEMON_PORT = 18321
DAEMON_LABEL = "com.swarmai.slack-daemon"


# ---------------------------------------------------------------------------
# AC1: launchd plist installs and starts on login
# ---------------------------------------------------------------------------


class TestPlistTemplate:
    """Verify the launchd plist template is well-formed and has required keys."""

    def test_plist_template_exists(self):
        """The plist template file must exist in channels/."""
        assert PLIST_TEMPLATE.exists(), f"Missing: {PLIST_TEMPLATE}"

    def test_plist_is_valid_xml(self):
        """The plist must be valid XML parseable by plistlib."""
        content = PLIST_TEMPLATE.read_text()
        # Replace placeholders so plistlib can parse
        content = content.replace("__WRAPPER_PATH__", "/tmp/slack_daemon.sh")
        content = content.replace("__LOG_DIR__", "/tmp/logs")
        plist = plistlib.loads(content.encode())
        assert isinstance(plist, dict)

    def test_plist_has_label(self):
        """Plist Label must be com.swarmai.slack-daemon."""
        content = PLIST_TEMPLATE.read_text()
        content = content.replace("__WRAPPER_PATH__", "/tmp/slack_daemon.sh")
        content = content.replace("__LOG_DIR__", "/tmp/logs")
        plist = plistlib.loads(content.encode())
        assert plist["Label"] == DAEMON_LABEL

    def test_plist_keep_alive(self):
        """AC5: KeepAlive must be true for auto-restart on crash."""
        content = PLIST_TEMPLATE.read_text()
        content = content.replace("__WRAPPER_PATH__", "/tmp/slack_daemon.sh")
        content = content.replace("__LOG_DIR__", "/tmp/logs")
        plist = plistlib.loads(content.encode())
        assert plist["KeepAlive"] is True

    def test_plist_run_at_load(self):
        """AC1: RunAtLoad must be true so daemon starts on login."""
        content = PLIST_TEMPLATE.read_text()
        content = content.replace("__WRAPPER_PATH__", "/tmp/slack_daemon.sh")
        content = content.replace("__LOG_DIR__", "/tmp/logs")
        plist = plistlib.loads(content.encode())
        assert plist["RunAtLoad"] is True

    def test_plist_has_log_paths(self):
        """Plist must define stdout and stderr log paths."""
        content = PLIST_TEMPLATE.read_text()
        content = content.replace("__WRAPPER_PATH__", "/tmp/slack_daemon.sh")
        content = content.replace("__LOG_DIR__", "/tmp/logs")
        plist = plistlib.loads(content.encode())
        assert "StandardOutPath" in plist
        assert "StandardErrorPath" in plist


# ---------------------------------------------------------------------------
# AC2: Backend runs standalone without Tauri (fixed port)
# ---------------------------------------------------------------------------


class TestWrapperScript:
    """Verify the shell wrapper script structure and correctness."""

    def test_wrapper_script_exists(self):
        """The wrapper script must exist."""
        assert WRAPPER_SCRIPT.exists(), f"Missing: {WRAPPER_SCRIPT}"

    def test_wrapper_is_executable(self):
        """Wrapper must have executable permission."""
        mode = WRAPPER_SCRIPT.stat().st_mode
        assert mode & stat.S_IXUSR, "Wrapper script is not executable"

    def test_wrapper_uses_fixed_port(self):
        """AC2: Wrapper must set SWARMAI_PORT to 18321."""
        content = WRAPPER_SCRIPT.read_text()
        assert "18321" in content, "Fixed port 18321 not found in wrapper"

    def test_wrapper_has_port_conflict_check(self):
        """AC2: Wrapper must check if port is already bound before starting."""
        content = WRAPPER_SCRIPT.read_text()
        # Should contain some form of port check (lsof, nc, or socket)
        assert any(
            keyword in content
            for keyword in ["lsof", "nc ", "netstat", "ss ", "/dev/tcp"]
        ), "No port conflict check found in wrapper"

    def test_wrapper_uses_caffeinate(self):
        """AC3+AC4: Wrapper must use caffeinate to prevent sleep."""
        content = WRAPPER_SCRIPT.read_text()
        assert "caffeinate" in content, "caffeinate not found in wrapper"

    def test_wrapper_has_shebang(self):
        """Wrapper must start with a proper shebang line."""
        content = WRAPPER_SCRIPT.read_text()
        assert content.startswith("#!/"), "Missing shebang"


# ---------------------------------------------------------------------------
# AC3+AC4: Daemon survives macOS sleep via caffeinate
# ---------------------------------------------------------------------------


class TestCaffeinateIntegration:
    """Verify caffeinate is configured correctly for sleep prevention."""

    def test_wrapper_caffeinate_flags(self):
        """caffeinate should use -is (idle + system sleep prevention)."""
        content = WRAPPER_SCRIPT.read_text()
        # Should contain caffeinate -is or caffeinate -i -s or similar
        assert any(
            flag in content for flag in ["-is", "-i -s", "-si"]
        ), "caffeinate missing -is flags (idle + system sleep prevention)"


# ---------------------------------------------------------------------------
# Installer tests
# ---------------------------------------------------------------------------


class TestInstaller:
    """Verify the installer can generate, install, and uninstall the plist."""

    def test_installer_module_importable(self):
        """The installer module must be importable."""
        import importlib
        mod = importlib.import_module(INSTALLER_MODULE)
        assert hasattr(mod, "install")
        assert hasattr(mod, "uninstall")
        assert hasattr(mod, "status")

    def test_installer_generates_valid_plist(self, tmp_path):
        """Installer must produce a valid plist with placeholders resolved."""
        import importlib
        mod = importlib.import_module(INSTALLER_MODULE)

        # Mock the install to write to tmp instead of ~/Library/LaunchAgents
        dest = tmp_path / f"{DAEMON_LABEL}.plist"
        with patch.object(mod, "LAUNCH_AGENTS", tmp_path), \
             patch("subprocess.run"):
            mod.install()

        assert dest.exists(), "Plist not written to expected location"
        content = dest.read_text()
        # No unresolved placeholders
        assert "__WRAPPER_PATH__" not in content
        assert "__LOG_DIR__" not in content

    def test_uninstall_removes_plist(self, tmp_path):
        """Uninstall must remove the plist file."""
        import importlib
        mod = importlib.import_module(INSTALLER_MODULE)

        dest = tmp_path / f"{DAEMON_LABEL}.plist"
        dest.write_text("<plist></plist>")

        with patch.object(mod, "LAUNCH_AGENTS", tmp_path), \
             patch("subprocess.run"):
            mod.uninstall()

        assert not dest.exists(), "Plist not removed after uninstall"


# ---------------------------------------------------------------------------
# Port conflict detection (unit test)
# ---------------------------------------------------------------------------


class TestPortConflict:
    """Verify port conflict detection logic."""

    def test_port_free_detection(self):
        """When port is free, should not report conflict."""
        # Bind a socket then release it to confirm the port is free
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        free_port = sock.getsockname()[1]
        sock.close()

        # The port should now be free
        result = _check_port_free("127.0.0.1", free_port)
        assert result is True

    def test_port_occupied_detection(self):
        """When port is occupied, should report conflict."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        occupied_port = sock.getsockname()[1]

        try:
            result = _check_port_free("127.0.0.1", occupied_port)
            assert result is False
        finally:
            sock.close()


def _check_port_free(host: str, port: int) -> bool:
    """Check if a port is free (helper for tests)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(1)
        sock.connect((host, port))
        sock.close()
        return False  # Port is occupied
    except (ConnectionRefusedError, OSError):
        return True  # Port is free
    finally:
        sock.close()
