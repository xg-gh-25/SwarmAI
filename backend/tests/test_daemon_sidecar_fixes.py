"""Tests for Daemon/Sidecar architecture fixes (run_1436b796).

Acceptance criteria:
  AC-A: backend.json not overwritten when existing PID is alive on expected port
  AC-D: /health endpoint includes boot_id for silent restart detection
  AC-C: write_backend_json skips write when conflict detected

Tests are written RED-first: they define expected behavior before implementation.
"""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# AC-D: boot_id in /health response
# ---------------------------------------------------------------------------


class TestBootId:
    """Verify boot_id is generated at startup and included in /health."""

    def test_boot_id_module_attribute_exists(self):
        """main.py must expose _boot_id at module level."""
        from main import _boot_id
        assert isinstance(_boot_id, str)
        assert len(_boot_id) >= 8, "boot_id must be at least 8 chars"

    def test_boot_id_in_health_response(self, client):
        """GET /health must include boot_id field."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "boot_id" in data, "/health response missing boot_id"
        assert isinstance(data["boot_id"], str)
        assert len(data["boot_id"]) >= 8

    def test_boot_id_stable_across_requests(self, client):
        """/health boot_id must not change within same process."""
        r1 = client.get("/health")
        r2 = client.get("/health")
        assert r1.json()["boot_id"] == r2.json()["boot_id"]

    def test_boot_id_in_backend_json(self, tmp_path):
        """write_backend_json must include boot_id."""
        from main import write_backend_json, _boot_id
        path = str(tmp_path / "backend.json")
        write_backend_json(port=18321, mode="daemon", path=path)
        data = json.loads(Path(path).read_text())
        assert "boot_id" in data
        assert data["boot_id"] == _boot_id


# ---------------------------------------------------------------------------
# AC-C: backend.json conflict check
# ---------------------------------------------------------------------------


class TestBackendJsonConflictCheck:
    """write_backend_json must NOT overwrite when existing PID is alive."""

    def test_skips_write_if_existing_pid_alive_and_port_listening(self, tmp_path):
        """If backend.json has a live PID on a listening port, don't overwrite."""
        from main import write_backend_json

        path = str(tmp_path / "backend.json")

        # Simulate an existing backend: bind a socket, write backend.json
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        occupied_port = sock.getsockname()[1]

        try:
            # Use PID 1 (launchd/init — always alive, definitely not us)
            # to simulate a different backend process owning the file
            initial_data = {
                "pid": 1,
                "port": occupied_port,
                "mode": "daemon",
                "boot_id": "existing_boot",
                "started_at": "2026-01-01T00:00:00",
            }
            Path(path).write_text(json.dumps(initial_data))

            # Now try to write with a DIFFERENT port — should be blocked
            write_backend_json(port=9999, mode="sidecar", path=path)

            # Verify original data preserved
            data = json.loads(Path(path).read_text())
            assert data["port"] == occupied_port, "backend.json was overwritten despite live process"
            assert data["mode"] == "daemon"
        finally:
            sock.close()

    def test_overwrites_if_existing_pid_dead(self, tmp_path):
        """If backend.json has a dead PID, overwrite is allowed."""
        from main import write_backend_json

        path = str(tmp_path / "backend.json")

        # Write with a definitely-dead PID
        initial_data = {
            "pid": 99999999,  # almost certainly dead
            "port": 18321,
            "mode": "daemon",
            "boot_id": "dead_boot",
            "started_at": "2026-01-01T00:00:00",
        }
        Path(path).write_text(json.dumps(initial_data))

        # New write should succeed
        write_backend_json(port=9999, mode="sidecar", path=path)
        data = json.loads(Path(path).read_text())
        assert data["port"] == 9999, "Should overwrite when existing PID is dead"

    def test_overwrites_if_no_existing_file(self, tmp_path):
        """If no backend.json exists, write normally."""
        from main import write_backend_json

        path = str(tmp_path / "backend.json")
        write_backend_json(port=18321, mode="daemon", path=path)
        assert Path(path).exists()
        data = json.loads(Path(path).read_text())
        assert data["port"] == 18321

    def test_overwrites_if_port_not_listening(self, tmp_path):
        """If backend.json PID is alive but port not listening, overwrite."""
        from main import write_backend_json

        path = str(tmp_path / "backend.json")

        # Write with current PID but a port that's definitely not listening
        initial_data = {
            "pid": os.getpid(),
            "port": 1,  # port 1 is never listening
            "mode": "daemon",
            "boot_id": "stale_boot",
            "started_at": "2026-01-01T00:00:00",
        }
        Path(path).write_text(json.dumps(initial_data))

        write_backend_json(port=9999, mode="sidecar", path=path)
        data = json.loads(Path(path).read_text())
        assert data["port"] == 9999, "Should overwrite when port not listening"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """FastAPI test client with startup complete."""
    from fastapi.testclient import TestClient
    import main

    # Force startup_complete so /health returns healthy
    original = main._startup_complete
    main._startup_complete = True
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        main._startup_complete = original
