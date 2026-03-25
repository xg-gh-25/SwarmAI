"""Tests for core.service_manager — sidecar service lifecycle management."""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Ensure backend is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.service_manager import ServiceManager, ManagedService, PORT_FILE


# ---------------------------------------------------------------------------
# ManagedService unit tests
# ---------------------------------------------------------------------------

class TestManagedService:
    def test_initial_state(self):
        svc = ManagedService(
            name="test", command=["echo", "hi"], cwd="/tmp",
        )
        assert svc.name == "test"
        assert not svc.is_running
        assert svc.pid is None
        assert svc.crash_count == 0
        assert svc.enabled is True

    def test_to_status_not_running(self):
        svc = ManagedService(
            name="test", command=["echo"], cwd="/tmp",
        )
        status = svc.to_status()
        assert status["name"] == "test"
        assert status["running"] is False
        assert status["pid"] is None
        assert status["uptime_seconds"] == 0


# ---------------------------------------------------------------------------
# ServiceManager unit tests
# ---------------------------------------------------------------------------

class TestServiceManager:
    def test_initial_state(self):
        mgr = ServiceManager()
        assert mgr.get_status() == []

    @pytest.mark.asyncio
    async def test_start_all_no_services_dir(self, tmp_path):
        """When Services/ doesn't exist, start_all is a no-op."""
        mgr = ServiceManager()
        await mgr.start_all(str(tmp_path), 8000)
        assert mgr.get_status() == []
        await mgr.stop_all()

    @pytest.mark.asyncio
    async def test_start_all_empty_services_dir(self, tmp_path):
        """When Services/ exists but has no service.json, no services started."""
        (tmp_path / "Services").mkdir()
        mgr = ServiceManager()
        await mgr.start_all(str(tmp_path), 8000)
        assert mgr.get_status() == []
        await mgr.stop_all()

    @pytest.mark.asyncio
    async def test_discovers_and_starts_service(self, tmp_path):
        """A valid service.json is discovered, started, and stopped."""
        svc_dir = tmp_path / "Services" / "test-svc"
        svc_dir.mkdir(parents=True)
        (svc_dir / "logs").mkdir()

        # Command that runs for a bit then exits
        config = {
            "name": "test-svc",
            "command": [sys.executable, "-c", "import time; time.sleep(60)"],
            "enabled": True,
            "restart_policy": "never",
        }
        (svc_dir / "service.json").write_text(json.dumps(config))

        mgr = ServiceManager()
        await mgr.start_all(str(tmp_path), 9999)

        statuses = mgr.get_status()
        assert len(statuses) == 1
        assert statuses[0]["name"] == "test-svc"
        assert statuses[0]["running"] is True
        assert statuses[0]["pid"] is not None

        await mgr.stop_all()

        # After stop, services list is cleared
        assert mgr.get_status() == []

    @pytest.mark.asyncio
    async def test_disabled_service_not_started(self, tmp_path):
        """A service with enabled=false is discovered but not started."""
        svc_dir = tmp_path / "Services" / "disabled-svc"
        svc_dir.mkdir(parents=True)

        config = {
            "name": "disabled-svc",
            "command": [sys.executable, "-c", "pass"],
            "enabled": False,
        }
        (svc_dir / "service.json").write_text(json.dumps(config))

        mgr = ServiceManager()
        await mgr.start_all(str(tmp_path), 8000)

        statuses = mgr.get_status()
        assert len(statuses) == 1
        assert statuses[0]["running"] is False
        assert statuses[0]["enabled"] is False

        await mgr.stop_all()

    @pytest.mark.asyncio
    async def test_port_file_lifecycle(self, tmp_path):
        """Port file is written on start and removed on stop."""
        mgr = ServiceManager()
        (tmp_path / "Services").mkdir()

        await mgr.start_all(str(tmp_path), 12345)
        assert PORT_FILE.exists()
        assert PORT_FILE.read_text() == "12345"

        await mgr.stop_all()
        assert not PORT_FILE.exists()

    @pytest.mark.asyncio
    async def test_env_vars_injected(self, tmp_path):
        """Services receive SWARM_BACKEND_PORT and SWARM_BACKEND_URL."""
        svc_dir = tmp_path / "Services" / "env-test"
        svc_dir.mkdir(parents=True)

        # Script that writes env vars to a file
        marker = tmp_path / "env_output.txt"
        script = (
            "import os; "
            f"open('{marker}', 'w').write("
            "os.environ.get('SWARM_BACKEND_PORT', '') + '|' + "
            "os.environ.get('SWARM_BACKEND_URL', ''))"
        )
        config = {
            "name": "env-test",
            "command": [sys.executable, "-c", script],
            "enabled": True,
            "restart_policy": "never",
        }
        (svc_dir / "service.json").write_text(json.dumps(config))

        mgr = ServiceManager()
        await mgr.start_all(str(tmp_path), 7777)

        # Wait for short-lived process to complete
        await asyncio.sleep(1)

        assert marker.exists()
        content = marker.read_text()
        assert "7777" in content
        assert "http://127.0.0.1:7777" in content

        await mgr.stop_all()

    def test_detect_backend_port_default(self):
        """Port detection falls back to settings.port in dev mode."""
        # This tests the module-level function in main.py
        from main import _detect_backend_port
        port = _detect_backend_port()
        assert isinstance(port, int)
        assert port > 0
