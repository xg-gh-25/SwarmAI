"""Tests for Daemon/Sidecar robustness fixes (run_90fc63d3).

Acceptance criteria:
  G4: remove_backend_json() only removes if current mode matches startup mode
  G6: POST /api/system/install-daemon installs launchd plist and returns status
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# G4: remove_backend_json mode guard
# ---------------------------------------------------------------------------


class TestRemoveBackendJsonModeGuard:
    """remove_backend_json must only delete if mode matches startup mode."""

    def test_removes_when_mode_matches(self, tmp_path):
        """If startup mode == current mode, backend.json should be removed."""
        from main import write_backend_json, remove_backend_json

        path = str(tmp_path / "backend.json")
        write_backend_json(port=18321, mode="daemon", path=path)
        assert Path(path).exists()

        # Remove with matching mode
        remove_backend_json(path=path, startup_mode="daemon")
        assert not Path(path).exists()

    def test_skips_when_mode_mismatch(self, tmp_path):
        """If startup mode != file mode, backend.json must NOT be removed."""
        from main import write_backend_json, remove_backend_json

        path = str(tmp_path / "backend.json")
        write_backend_json(port=18321, mode="daemon", path=path)
        assert Path(path).exists()

        # Try to remove with mismatched mode (sidecar trying to delete daemon's file)
        remove_backend_json(path=path, startup_mode="sidecar")
        assert Path(path).exists(), "Sidecar must not delete daemon's backend.json"

    def test_removes_when_no_mode_in_file(self, tmp_path):
        """If backend.json has no mode field, removal should proceed (legacy compat)."""
        from main import remove_backend_json

        path = str(tmp_path / "backend.json")
        Path(path).write_text(json.dumps({"pid": os.getpid(), "port": 8000}))
        assert Path(path).exists()

        remove_backend_json(path=path, startup_mode="sidecar")
        assert not Path(path).exists()

    def test_removes_when_file_corrupt(self, tmp_path):
        """If backend.json is corrupt, removal should proceed."""
        from main import remove_backend_json

        path = str(tmp_path / "backend.json")
        Path(path).write_text("not json")

        remove_backend_json(path=path, startup_mode="sidecar")
        assert not Path(path).exists()

    def test_removes_when_file_missing(self, tmp_path):
        """If backend.json doesn't exist, no error."""
        from main import remove_backend_json

        path = str(tmp_path / "backend.json")
        remove_backend_json(path=path, startup_mode="daemon")
        # Should not raise


# ---------------------------------------------------------------------------
# G6: POST /api/system/install-daemon endpoint
# ---------------------------------------------------------------------------


class TestInstallDaemonEndpoint:
    """POST /api/system/install-daemon must install the launchd plist."""

    def test_endpoint_exists(self, client):
        """Endpoint must exist and accept POST."""
        with patch("routers.system.sys") as mock_sys, \
             patch("routers.system._run_install_daemon") as mock_install:
            mock_sys.platform = "darwin"
            mock_install.return_value = {"status": "installed", "port": 18321}
            resp = client.post("/api/system/install-daemon")
            assert resp.status_code == 200

    def test_returns_installed_status(self, client):
        """Successful install returns status and port."""
        with patch("routers.system.sys") as mock_sys, \
             patch("routers.system._run_install_daemon") as mock_install:
            mock_sys.platform = "darwin"
            mock_install.return_value = {"status": "installed", "port": 18321}
            resp = client.post("/api/system/install-daemon")
            data = resp.json()
            assert data["status"] == "installed"
            assert data["port"] == 18321

    def test_returns_error_on_failure(self, client):
        """Failed install returns error status."""
        with patch("routers.system.sys") as mock_sys, \
             patch("routers.system._run_install_daemon") as mock_install:
            mock_sys.platform = "darwin"
            mock_install.side_effect = RuntimeError("Plist template not found")
            resp = client.post("/api/system/install-daemon")
            assert resp.status_code == 500

    def test_macos_only(self, client):
        """On non-macOS, endpoint returns 400."""
        with patch("routers.system.sys") as mock_sys:
            mock_sys.platform = "linux"
            resp = client.post("/api/system/install-daemon")
            assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """FastAPI test client with startup complete."""
    from fastapi.testclient import TestClient
    import main

    original = main._startup_complete
    main._startup_complete = True
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        main._startup_complete = original
