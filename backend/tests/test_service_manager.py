"""Tests for core.service_manager — sidecar service lifecycle management."""
import asyncio
import json
import sys
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
        await mgr.start_all(str(tmp_path), 8000, owns_data_dir=True)
        assert mgr.get_status() == []
        await mgr.stop_all()

    @pytest.mark.asyncio
    async def test_start_all_empty_services_dir(self, tmp_path):
        """When Services/ exists but has no service.json, no services started."""
        (tmp_path / "Services").mkdir()
        mgr = ServiceManager()
        await mgr.start_all(str(tmp_path), 8000, owns_data_dir=True)
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
        await mgr.start_all(str(tmp_path), 9999, owns_data_dir=True)

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
        await mgr.start_all(str(tmp_path), 8000, owns_data_dir=True)

        statuses = mgr.get_status()
        assert len(statuses) == 1
        assert statuses[0]["running"] is False
        assert statuses[0]["enabled"] is False

        await mgr.stop_all()

    @pytest.mark.asyncio
    async def test_port_file_lifecycle(self, tmp_path):
        """Port file is written on start and removed on stop."""
        # Read PORT_FILE through the LIVE module attribute, not this test's frozen
        # `from ... import PORT_FILE` copy. The autouse _isolate_app_data_dir fixture
        # re-roots core.service_manager.PORT_FILE onto a per-test sandbox (so tests
        # never write the production ~/.swarm-ai/backend.port), but it cannot rewrite
        # the separate binding this test module froze at import time. _write_port_file
        # resolves PORT_FILE from the service_manager module namespace, so the reader
        # must too — otherwise writer(sandbox) != reader(production) and the assert fails.
        import core.service_manager as _sm
        mgr = ServiceManager()
        (tmp_path / "Services").mkdir()

        await mgr.start_all(str(tmp_path), 12345, owns_data_dir=True)
        assert _sm.PORT_FILE.exists()
        assert _sm.PORT_FILE.read_text() == "12345"

        await mgr.stop_all()
        assert not _sm.PORT_FILE.exists()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kwargs", [{"owns_data_dir": False}, {}])
    async def test_non_owner_neither_writes_nor_deletes_port_file(self, tmp_path, kwargs):
        """A backend that does not own backend.json (a probe, an accidental second
        launch) leaves the owner's backend.port as it is, on start and on stop, and
        starts none of the managed services; not saying is the same as not owning."""
        import core.service_manager as _sm
        _sm.PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _sm.PORT_FILE.write_text("18321")      # the owner's
        svc_dir = tmp_path / "Services" / "bot"
        svc_dir.mkdir(parents=True)
        (svc_dir / "service.json").write_text(json.dumps({
            "name": "bot", "command": [sys.executable, "-c", "import time; time.sleep(30)"],
            "enabled": True}))
        mgr = ServiceManager()

        await mgr.start_all(str(tmp_path), 8000, **kwargs)
        assert _sm.PORT_FILE.read_text() == "18321"
        assert mgr.get_status() == []          # no second copy of the owner's services

        await mgr.stop_all()
        assert _sm.PORT_FILE.read_text() == "18321"

    @pytest.mark.asyncio
    async def test_old_owner_keeps_the_new_owners_port_file(self, tmp_path):
        """Restart overlap: the old owner wrote backend.port, a new owner has taken
        backend.json over; the old one's stop (still_owner=False) leaves the file."""
        import core.service_manager as _sm
        mgr = ServiceManager()
        (tmp_path / "Services").mkdir()
        await mgr.start_all(str(tmp_path), 18321, owns_data_dir=True)
        assert _sm.PORT_FILE.read_text() == "18321"

        await mgr.stop_all(still_owner=False)
        assert _sm.PORT_FILE.read_text() == "18321"

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
        await mgr.start_all(str(tmp_path), 7777, owns_data_dir=True)

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
