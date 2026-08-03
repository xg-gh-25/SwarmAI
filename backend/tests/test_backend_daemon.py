"""Tests for SwarmAI backend daemon installer and wrapper script.

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

import plistlib
import socket
import stat
from pathlib import Path
from unittest.mock import patch



# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BACKEND_DIR = Path(__file__).parent.parent
CHANNELS_DIR = BACKEND_DIR / "channels"
PLIST_TEMPLATE = CHANNELS_DIR / "com.swarmai.backend.plist"
WRAPPER_SCRIPT = CHANNELS_DIR / "swarmai_backend.sh"
INSTALLER_MODULE = "channels.install_backend_daemon"

DAEMON_PORT = 18321
DAEMON_LABEL = "com.swarmai.backend"


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
        content = content.replace("__WRAPPER_PATH__", "/tmp/swarmai_backend.sh")
        content = content.replace("__LOG_DIR__", "/tmp/logs")
        plist = plistlib.loads(content.encode())
        assert isinstance(plist, dict)

    def test_plist_has_label(self):
        """Plist Label must be com.swarmai.backend."""
        content = PLIST_TEMPLATE.read_text()
        content = content.replace("__WRAPPER_PATH__", "/tmp/swarmai_backend.sh")
        content = content.replace("__LOG_DIR__", "/tmp/logs")
        plist = plistlib.loads(content.encode())
        assert plist["Label"] == DAEMON_LABEL

    def test_plist_keep_alive(self):
        """AC5: KeepAlive must be true for auto-restart on crash."""
        content = PLIST_TEMPLATE.read_text()
        content = content.replace("__WRAPPER_PATH__", "/tmp/swarmai_backend.sh")
        content = content.replace("__LOG_DIR__", "/tmp/logs")
        plist = plistlib.loads(content.encode())
        assert plist["KeepAlive"] is True

    def test_plist_run_at_load(self):
        """AC1: RunAtLoad must be true so daemon starts on login."""
        content = PLIST_TEMPLATE.read_text()
        content = content.replace("__WRAPPER_PATH__", "/tmp/swarmai_backend.sh")
        content = content.replace("__LOG_DIR__", "/tmp/logs")
        plist = plistlib.loads(content.encode())
        assert plist["RunAtLoad"] is True

    def test_plist_has_log_paths(self):
        """Plist must define stdout and stderr log paths."""
        content = PLIST_TEMPLATE.read_text()
        content = content.replace("__WRAPPER_PATH__", "/tmp/swarmai_backend.sh")
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
        """caffeinate should use -i (idle sleep prevention only, not -s system sleep)."""
        content = WRAPPER_SCRIPT.read_text()
        # Should contain caffeinate -i (idle only) — NOT -s (system sleep blocks lid-close)
        assert "caffeinate -i" in content, "caffeinate missing -i flag (idle sleep prevention)"
        # Ensure we're NOT preventing system sleep (battery drain on laptops)
        assert "caffeinate -is" not in content, (
            "caffeinate should use -i only, not -is — "
            "-s prevents lid-close sleep and drains laptop battery"
        )


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


# ---------------------------------------------------------------------------
# Guardian bundling (run_8a9de435) — guardian assets must ship in the .app so
# the Rust auto_install_daemon can install the C034 recovery watchdog for
# end-user installs (not just the deprecated Python installer).
# ---------------------------------------------------------------------------

RESOURCES_DAEMON_DIR = BACKEND_DIR.parent / "desktop" / "resources" / "daemon"
GUARDIAN_PLIST_TEMPLATE = RESOURCES_DAEMON_DIR / "com.swarmai.guardian.plist.template"
GUARDIAN_SCRIPT = RESOURCES_DAEMON_DIR / "swarmai_guardian.sh"
GUARDIAN_GUARD_PY = RESOURCES_DAEMON_DIR / "daemon_guard.py"
GUARD_PY_SOURCE = BACKEND_DIR / "core" / "daemon_guard.py"


class TestGuardianBundling:
    """The guardian watchdog assets must be staged in desktop/resources/daemon/
    so Tauri bundles them into the .app (tauri.conf ../resources/daemon/* glob)
    and Rust auto_install_daemon can install them for end users."""

    def test_guardian_assets_staged_for_bundling(self):
        # AC1: all 3 guardian assets present in the bundled resources dir.
        assert GUARDIAN_PLIST_TEMPLATE.exists(), f"Missing: {GUARDIAN_PLIST_TEMPLATE}"
        assert GUARDIAN_SCRIPT.exists(), f"Missing: {GUARDIAN_SCRIPT}"
        assert GUARDIAN_GUARD_PY.exists(), f"Missing: {GUARDIAN_GUARD_PY}"

    def test_guardian_plist_template_valid_and_substitutable(self):
        # AC4: rendered plist parses and leaves no unsubstituted __ placeholders.
        content = GUARDIAN_PLIST_TEMPLATE.read_text()
        content = content.replace("__GUARDIAN_SCRIPT__", "/tmp/swarmai_guardian.sh")
        content = content.replace("__LOG_DIR__", "/tmp/logs")
        assert "__" not in content, "guardian plist has unsubstituted __ placeholders"
        plist = plistlib.loads(content.encode())
        assert plist["Label"] == "com.swarmai.guardian"
        assert plist["StartInterval"] <= 60
        assert plist.get("RunAtLoad") is True

    def test_staged_guard_py_identical_to_source(self):
        # AC5: drift guard — the bundled daemon_guard.py must match the source
        # of truth (backend/core/daemon_guard.py). build-backend.sh refreshes it,
        # but this test catches drift if someone edits one without the other.
        import filecmp
        assert filecmp.cmp(str(GUARDIAN_GUARD_PY), str(GUARD_PY_SOURCE), shallow=False), \
            "Staged daemon_guard.py drifted from backend/core/daemon_guard.py — " \
            "re-run build-backend.sh or copy the source"

    def test_guardian_script_is_executable_and_self_contained(self):
        # The bundled script must run the STANDALONE guard (not `-m core...`),
        # since the .app install has no repo/PYTHONPATH.
        body = GUARDIAN_SCRIPT.read_text()
        assert "-m core.daemon_guard" not in body
        assert "guardian/daemon_guard.py" in body

    def test_all_bundled_guardian_assets_match_sources(self):
        # Drift guard for ALL 3 bundled assets (not just guard.py): the bundled
        # script + plist template must track their sources of truth too. The
        # sync script (run via build-backend.sh AND tauri beforeBuildCommand)
        # keeps them current; this test is the backstop.
        import filecmp
        pairs = [
            (GUARDIAN_GUARD_PY, GUARD_PY_SOURCE),
            (GUARDIAN_SCRIPT, CHANNELS_DIR / "swarmai_guardian.sh"),
            (GUARDIAN_PLIST_TEMPLATE, CHANNELS_DIR / "com.swarmai.guardian.plist"),
        ]
        for staged, source in pairs:
            assert filecmp.cmp(str(staged), str(source), shallow=False), \
                f"Bundled {staged.name} drifted from {source} — run sync-guardian-assets.sh"

    def test_sync_script_exists(self):
        # The single-source-of-truth sync script must exist (wired into both
        # build-backend.sh and tauri.conf beforeBuildCommand).
        sync = BACKEND_DIR.parent / "desktop" / "scripts" / "sync-guardian-assets.sh"
        assert sync.exists(), "sync-guardian-assets.sh missing"
